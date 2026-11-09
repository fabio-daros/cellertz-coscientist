import json
from pathlib import Path

from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    ProteinGenerationAgent,
)
from cellertz.domains.protein_design.agents.ranking import ProteinRankingAgentV1
from cellertz.domains.protein_design.agents.supervisor import (
    ProteinSupervisorV1,
)
from cellertz.tools.protein_scoring.scoring import RANKING_METHOD
from cellertz.tools.protein_scoring.scoring_v2 import (
    ProteinScoreResultV2,
)

STAGE_FILES = (
    "generation.json",
    "scoring.json",
    "ranking.json",
    "critique.json",
    "prioritization.json",
    "run.json",
)


class CountingGenerationAgent(ProteinGenerationAgent):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate(self, request: GenerationRequest):
        self.calls += 1
        return super().generate(request)


class CountingRankingAgent(ProteinRankingAgentV1):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def rank(self, results):
        self.calls += 1
        return super().rank(results)


def _request(n_candidates: int = 10) -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator="fixture",
        n_candidates=n_candidates,
        constraints={
            "length_min": 50,
            "length_max": 100,
        },
    )


def _supervisor(
    tmp_path: Path,
    *,
    request: GenerationRequest | None = None,
    generation_agent: ProteinGenerationAgent | None = None,
    ranking_agent: ProteinRankingAgentV1 | None = None,
    score_fn=None,
    run_id: str = "protein_run_001",
) -> ProteinSupervisorV1:
    return ProteinSupervisorV1(
        request=request or _request(),
        artifact_dir=tmp_path,
        run_id=run_id,
        generation_agent=generation_agent,
        ranking_agent=ranking_agent,
        score_fn=score_fn,
    )


def _assert_round_complete(run) -> None:
    assert run.generation.status == "complete"
    assert run.scoring.status == "complete"
    assert run.ranking.status == "complete"
    assert run.critique.status == "complete"
    assert run.prioritization.status == "complete"
    assert run.stage == "complete"
    assert run.round == 1


def test_single_round_completes_every_stage(tmp_path):
    supervisor = _supervisor(tmp_path)
    run = supervisor.run()

    _assert_round_complete(run)
    assert run.run_id == "protein_run_001"
    assert run.target == "BCMA"
    assert len(supervisor.generation_result.candidates) == 10
    assert {
        path.name for path in tmp_path.iterdir()
    } == set(STAGE_FILES)


def test_second_run_does_not_duplicate_artifacts(tmp_path):
    generation = CountingGenerationAgent()
    ranking = CountingRankingAgent()
    supervisor = _supervisor(
        tmp_path,
        generation_agent=generation,
        ranking_agent=ranking,
    )

    supervisor.run()
    generation_json = (tmp_path / "generation.json").read_text()
    ranking_json = (tmp_path / "ranking.json").read_text()
    names = sorted(path.name for path in tmp_path.iterdir())

    supervisor.run()

    _assert_round_complete(supervisor.design_run)
    assert generation.calls == 1
    assert ranking.calls == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == names
    assert (tmp_path / "generation.json").read_text() == generation_json
    assert (tmp_path / "ranking.json").read_text() == ranking_json


def test_ranking_score_remains_raw_ipsae(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.run()

    assert supervisor.ranking_records
    for record in supervisor.ranking_records:
        assert record.ranking_signal == RANKING_METHOD
        assert record.ranking_score == record.structural_evidence["ipsae"]
        assert record.is_probability is False
        assert record.recommended_as_hard_filter is False

    scores = [
        record.ranking_score
        for record in supervisor.ranking_records
        if record.ranking_score is not None
    ]
    assert scores == sorted(scores, reverse=True)


def test_critic_does_not_change_rank(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.run()

    ranked = {
        record.candidate_id: (record.rank, record.ranking_score)
        for record in supervisor.ranking_records
    }
    criticized = [
        (record.candidate_id, record.rank, record.ranking_score)
        for record in supervisor.critic_records
    ]

    assert [
        (candidate_id, *ranked[candidate_id])
        for candidate_id, _, _ in criticized
    ] == criticized


def test_prioritization_does_not_change_rank(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.run()

    ranked = {
        record.candidate_id: (record.rank, record.ranking_score)
        for record in supervisor.ranking_records
    }

    for record in supervisor.portfolio_records:
        assert (
            record.rank,
            record.ranking_score,
        ) == ranked[record.candidate_id]


def test_failed_stage_preserves_earlier_stages(tmp_path):
    def boom(_candidates):
        raise RuntimeError("fixture scoring exploded")

    generation = CountingGenerationAgent()
    supervisor = _supervisor(
        tmp_path,
        generation_agent=generation,
        score_fn=boom,
    )
    run = supervisor.run()

    assert run.stage == "failed"
    assert run.generation.status == "complete"
    assert run.scoring.status == "failed"
    assert run.ranking.status == "pending"
    assert run.critique.status == "pending"
    assert run.prioritization.status == "pending"
    assert run.scoring.error == "fixture scoring exploded"
    assert generation.calls == 1
    assert (tmp_path / "generation.json").exists()
    assert not (tmp_path / "scoring.json").exists()
    assert not (tmp_path / "ranking.json").exists()


def test_retry_resumes_from_failed_stage(tmp_path):
    generation = CountingGenerationAgent()
    ranking = CountingRankingAgent()
    calls = {"n": 0}

    def boom(_candidates):
        calls["n"] += 1
        raise RuntimeError("fixture scoring exploded")

    supervisor = _supervisor(
        tmp_path,
        generation_agent=generation,
        ranking_agent=ranking,
        score_fn=boom,
    )
    supervisor.run()

    generation_json = (tmp_path / "generation.json").read_text()
    supervisor.score_fn = None
    run = supervisor.run()

    _assert_round_complete(run)
    assert generation.calls == 1
    assert ranking.calls == 1
    assert calls["n"] == 1
    assert (tmp_path / "generation.json").read_text() == generation_json
    assert supervisor.next_stage() is None


def test_completed_ranking_is_not_recomputed_when_critique_retries(
    tmp_path,
):
    ranking = CountingRankingAgent()
    supervisor = _supervisor(
        tmp_path,
        ranking_agent=ranking,
    )
    supervisor.run()
    ranking_json = (tmp_path / "ranking.json").read_text()

    supervisor.design_run.critique.status = "failed"
    run = supervisor.run()

    _assert_round_complete(run)
    assert ranking.calls == 1
    assert (tmp_path / "ranking.json").read_text() == ranking_json


def test_missing_ipsae_does_not_fail_the_run(tmp_path):
    def score_fn(candidates):
        results = []
        for index, candidate in enumerate(candidates):
            ipsae = None if index == 0 else 0.90 - (index * 0.05)
            results.append(
                ProteinScoreResultV2(
                    candidate_id=candidate.candidate_id,
                    baseline_score=ipsae,
                    structural_evidence={
                        "ipsae": ipsae,
                        "boltz2_iptm_mean": 0.50,
                    },
                    additional_evidence={
                        "boltz2_iptm_mean": 0.50,
                    },
                )
            )
        return results

    supervisor = _supervisor(tmp_path, score_fn=score_fn)
    run = supervisor.run()

    _assert_round_complete(run)
    assert run.scoring.missing_ipsae == 1
    assert supervisor.ranking_records[-1].ranking_score is None
    assert supervisor.ranking_records[-1].rank == 10


def test_no_scientific_generator_adapters_are_required(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.run()

    adapters = supervisor.generation_agent._adapters
    assert "fixture" in adapters
    assert supervisor.generation_result is not None
    assert supervisor.generation_result.adapter_name == "fixture"
    source = Path(
        "src/cellertz/domains/protein_design/agents/supervisor.py"
    ).read_text()
    lowered = source.lower()
    assert "import bindcraft" not in lowered
    assert "import rfdiffusion" not in lowered
    assert "import mber" not in lowered
    assert "BindCraftAdapter" not in source
    assert "RFdiffusionAdapter" not in source
    assert "MBERAdapter" not in source



def test_scoring_artifact_preserves_score_provenance(tmp_path):
    expected_provenance = {
        "ipsae_evidence": {
            "execution_id": "exec-test",
            "protocol_id": "TEST_PROTOCOL",
            "model_aggregation": "mean",
            "n_models": 2,
        },
    }

    def score_fn(candidates):
        return [
            ProteinScoreResultV2(
                candidate_id=candidate.candidate_id,
                baseline_score=0.42,
                structural_evidence={
                    "ipsae": 0.42,
                },
                provenance=expected_provenance,
            )
            for candidate in candidates
        ]

    supervisor = _supervisor(
        tmp_path,
        request=_request(n_candidates=1),
        score_fn=score_fn,
    )

    run = supervisor.run()

    _assert_round_complete(run)

    payload = json.loads(
        (tmp_path / "scoring.json").read_text()
    )

    assert len(payload) == 1
    assert payload[0]["provenance"] == expected_provenance
