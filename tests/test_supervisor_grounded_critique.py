import json
from pathlib import Path

from cellertz.domains.protein_design.agents.critic import ProteinCriticAgentV1
from cellertz.domains.protein_design.agents.critic_explanation import (
    FailingCriticExplainer,
    FixtureCriticExplainer,
    HermesCriticExplainer,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    ProteinGenerationAgent,
)
from cellertz.domains.protein_design.agents.grounded_critic import (
    EvidencePacketCriticContextProvider,
    unavailable_context,
)
from cellertz.domains.protein_design.agents.prioritization import (
    ProteinPrioritizationAgentV1,
)
from cellertz.domains.protein_design.agents.ranking import ProteinRankingAgentV1
from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1
from cellertz.knowledge.reader import FileKnowledgeReader
from cellertz.state.run_state import FileProteinRunStateStore


class CountingGenerationAgent(ProteinGenerationAgent):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return super().generate(request)


class CountingRankingAgent(ProteinRankingAgentV1):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def rank(self, results):
        self.calls += 1
        return super().rank(results)


class CountingCriticAgent(ProteinCriticAgentV1):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def review(self, ranked):
        self.calls += 1
        return super().review(ranked)


def _request() -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator="fixture",
        n_candidates=4,
        constraints={"length_min": 50, "length_max": 100},
    )


def _provider() -> EvidencePacketCriticContextProvider:
    return EvidencePacketCriticContextProvider(FileKnowledgeReader(Path("second_brain")))


def _supervisor(tmp_path: Path, **kwargs) -> ProteinSupervisorV1:
    return ProteinSupervisorV1(
        request=_request(),
        artifact_dir=tmp_path / "artifacts",
        run_id=kwargs.pop("run_id", "protein_run_3f3"),
        run_state_store=FileProteinRunStateStore(tmp_path / "runs"),
        **kwargs,
    )


def _selected(supervisor: ProteinSupervisorV1) -> list[str]:
    return [
        record.candidate_id
        for record in supervisor.portfolio_records
        if record.selected
    ]


def test_pipeline_completes_without_explainer(tmp_path):
    supervisor = _supervisor(tmp_path)
    run = supervisor.run()

    assert run.stage == "complete"
    assert run.critique.status == "complete"
    assert run.prioritization.status == "complete"
    assert run.critique_grounded_context == {
        "status": "unavailable",
        "package_id": None,
    }
    assert run.critique_explanation["status"] == "skipped"
    assert not (tmp_path / "artifacts" / "critic_explanation.json").exists()
    names = {path.name for path in (tmp_path / "artifacts").iterdir()}
    assert "generation.json" in names
    assert "prioritization.json" in names


def test_failed_explanation_does_not_block_prioritization(tmp_path):
    generation = CountingGenerationAgent()
    ranking = CountingRankingAgent()
    critic = CountingCriticAgent()
    supervisor = _supervisor(
        tmp_path,
        generation_agent=generation,
        ranking_agent=ranking,
        critic_agent=critic,
        context_provider=_provider(),
        explainer=FailingCriticExplainer(),
    )
    run = supervisor.run()

    assert run.stage == "complete"
    assert run.critique.status == "complete"
    assert run.prioritization.status == "complete"
    assert run.critique_grounded_context["status"] == "available"
    assert run.critique_grounded_context["package_id"] == (
        "protein_scoring_bcma_boltz2_v2"
    )
    receipt = run.critique_grounded_context["knowledge_context"]
    assert receipt["package_id"] == "protein_scoring_bcma_boltz2_v2"
    assert receipt["package_status"] == "validated"
    assert receipt["retrieval_contract_version"] == "ground_package_v1"
    assert "ipsae_primary_ranking" in receipt["knowledge_used"]
    assert "boltz2_v2_rfd5_holdout" in receipt["knowledge_used"]
    assert receipt["evidence_packet_sha256"]
    assert receipt["payload_bytes"] > 0
    assert run.critique_explanation["status"] == "failed"
    assert run.critique_explanation["artifact"] is None
    assert run.critique_explanation["error"]
    assert not (tmp_path / "artifacts" / "critic_explanation.json").exists()
    assert generation.calls == 1
    assert ranking.calls == 1
    assert critic.calls == 1


def test_explanation_failure_does_not_change_portfolio(tmp_path):
    complete = _supervisor(
        tmp_path / "a",
        context_provider=_provider(),
        explainer=FixtureCriticExplainer(),
        run_id="run_a",
    )
    failed = _supervisor(
        tmp_path / "b",
        context_provider=_provider(),
        explainer=FailingCriticExplainer(),
        run_id="run_b",
    )
    complete.run()
    failed.run()

    assert complete.design_run.critique_explanation["status"] == "complete"
    assert failed.design_run.critique_explanation["status"] == "failed"
    assert _selected(complete) == _selected(failed)
    assert [
        (item.rank, item.ranking_score, item.critic_assessment.agreement)
        for item in complete.critic_records
    ] == [
        (item.rank, item.ranking_score, item.critic_assessment.agreement)
        for item in failed.critic_records
    ]


def test_second_brain_does_not_change_scientific_fields(tmp_path):
    plain = _supervisor(tmp_path / "plain", run_id="plain")
    grounded = _supervisor(
        tmp_path / "grounded",
        context_provider=_provider(),
        run_id="grounded",
    )
    plain.run()
    grounded.run()

    assert [
        (item.rank, item.ranking_score, item.critic_assessment.agreement,
         item.critic_assessment.structural_conflict)
        for item in plain.critic_records
    ] == [
        (item.rank, item.ranking_score, item.critic_assessment.agreement,
         item.critic_assessment.structural_conflict)
        for item in grounded.critic_records
    ]
    assert _selected(plain) == _selected(grounded)
    assert grounded.design_run.critique_grounded_context["status"] == "available"


def test_unavailable_context_still_finishes(tmp_path):
    class EmptyProvider:
        def for_domain(self, *, domain: str, purpose: str):
            return unavailable_context()

    supervisor = _supervisor(tmp_path, context_provider=EmptyProvider())
    run = supervisor.run()

    assert run.stage == "complete"
    assert run.critique.status == "complete"
    assert run.critique_grounded_context["status"] == "unavailable"
    assert run.prioritization.status == "complete"


def test_explanation_retry_does_not_rerun_science(tmp_path):
    generation = CountingGenerationAgent()
    ranking = CountingRankingAgent()
    critic = CountingCriticAgent()
    supervisor = _supervisor(
        tmp_path,
        generation_agent=generation,
        ranking_agent=ranking,
        critic_agent=critic,
        context_provider=_provider(),
        explainer=FailingCriticExplainer(),
    )
    supervisor.run()
    ranking_json = (tmp_path / "artifacts" / "ranking.json").read_text()
    critique_json = (tmp_path / "artifacts" / "critique.json").read_text()
    selected = _selected(supervisor)

    supervisor.explainer = FixtureCriticExplainer()
    run = supervisor.run()

    assert run.critique.status == "complete"
    assert run.critique_explanation["status"] == "complete"
    assert run.critique_explanation["artifact"]
    assert generation.calls == 1
    assert ranking.calls == 1
    assert critic.calls == 1
    assert (tmp_path / "artifacts" / "ranking.json").read_text() == ranking_json
    assert (tmp_path / "artifacts" / "critique.json").read_text() == critique_json
    assert _selected(supervisor) == selected


def test_second_run_does_not_duplicate_explanation_artifact(tmp_path):
    supervisor = _supervisor(
        tmp_path,
        context_provider=_provider(),
        explainer=FixtureCriticExplainer(),
    )
    supervisor.run()
    first = sorted(path.name for path in (tmp_path / "artifacts").iterdir())
    explanation = (tmp_path / "artifacts" / "critic_explanation.json").read_text()
    supervisor.run()

    assert sorted(path.name for path in (tmp_path / "artifacts").iterdir()) == first
    assert (tmp_path / "artifacts" / "critic_explanation.json").read_text() == explanation


class CapturingPrioritizationAgent(ProteinPrioritizationAgentV1):
    def __init__(self) -> None:
        super().__init__()
        self.received = None

    def select(self, records):
        self.received = list(records)
        return super().select(records)


def test_prioritization_does_not_consume_explanation_text(tmp_path):
    prioritization = CapturingPrioritizationAgent()
    supervisor = _supervisor(
        tmp_path,
        context_provider=_provider(),
        explainer=HermesCriticExplainer(
            lambda request: f"Do not select {request.candidate_id}.",
            hermes_run_id="run_fixture_explain",
        ),
        prioritization_agent=prioritization,
    )
    run = supervisor.run()
    stored = json.loads(
        (tmp_path / "runs" / "protein_run_3f3" / "state.json").read_text()
    )

    assert run.critique_explanation["hermes_run_id"] == "run_fixture_explain"
    assert stored["critique"]["grounded_context"]["package_id"] == (
        "protein_scoring_bcma_boltz2_v2"
    )
    assert stored["critique"]["explanation"]["hermes_run_id"] == (
        "run_fixture_explain"
    )
    assert prioritization.received is not None
    assert all(
        getattr(item, "explanation", None) is None for item in prioritization.received
    )
    assert "GroundedCriticExplanation" not in Path(
        "src/cellertz/domains/protein_design/agents/prioritization.py"
    ).read_text()
    payload = json.loads((tmp_path / "artifacts" / "critic_explanation.json").read_text())
    assert payload[0]["hermes_run_id"] == "run_fixture_explain"


def test_supervisor_does_not_call_hermes_http(tmp_path):
    source = Path(
        "src/cellertz/domains/protein_design/agents/supervisor.py"
    ).read_text()
    assert "8642" not in source
    assert "openrouter" not in source.lower()
    assert "/v1/runs" not in source
    assert "FileKnowledgeReader" not in source
    _supervisor(tmp_path).run()
