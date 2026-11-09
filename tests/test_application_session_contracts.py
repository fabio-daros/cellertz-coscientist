from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cellertz.application.contracts import (
    ApplicationContractError,
    ArtifactSummary,
    CandidateSummary,
    EvidenceSummary,
    ExecutionBudget,
    ModelUsageEvent,
    ProteinDesignDraft,
    WorkspaceSession,
)
from cellertz.application.fixtures import pdl1_prototype_session

WHEN = datetime(2026, 9, 26, 12, tzinfo=UTC)


def _usage(**overrides: object) -> ModelUsageEvent:
    payload = {
        "session_id": "session_a",
        "agent_id": "ranking",
        "stage": "display",
        "provider": "example",
        "model": "example-display",
        "started_at": WHEN,
        "completed_at": WHEN,
    }
    payload.update(overrides)
    return ModelUsageEvent(**payload)  # type: ignore[arg-type]


def test_session_json_is_deterministic_and_marks_the_prototype() -> None:
    first = pdl1_prototype_session().to_json()
    second = pdl1_prototype_session().to_json()
    assert first == second
    payload = json.loads(first)
    assert payload["prototype_only"] is True
    assert payload["execution_authorized"] is False
    assert payload["draft"]["executable"] is False
    assert payload["budget"]["authorizes_execution"] is False


def test_incomplete_draft_cannot_become_executable() -> None:
    draft = ProteinDesignDraft(
        draft_id="draft_a",
        target_id="PDL1",
        design_goal="display",
        status="incomplete",
    )
    assert draft.executable is False
    assert draft.to_dict()["executable"] is False
    with pytest.raises(ApplicationContractError) as caught:
        ProteinDesignDraft(
            draft_id="draft_a",
            target_id="PDL1",
            design_goal="display",
            status="incomplete",
            executable=True,
        )
    assert caught.value.code == "draft_is_not_executable"


def test_unknown_usage_cost_stays_unknown() -> None:
    event = _usage()
    encoded = event.to_dict()
    assert encoded["estimated_cost"] is None
    assert encoded["currency"] is None
    assert encoded["input_tokens"] is None
    assert "0" not in json.dumps({"estimated_cost": encoded["estimated_cost"]})
    snapshot = pdl1_prototype_session().usage_snapshot.to_dict()
    assert snapshot["estimated_model_cost"] is None
    assert snapshot["scientific_infrastructure_cost"] is None
    assert snapshot["model_cost_known"] is False


def test_negative_token_and_cost_values_fail_closed() -> None:
    with pytest.raises(ApplicationContractError) as tokens:
        _usage(input_tokens=-1)
    assert tokens.value.code == "negative_quantity"
    with pytest.raises(ApplicationContractError) as cost:
        _usage(estimated_cost=-0.01, currency="USD")
    assert cost.value.code == "negative_quantity"
    with pytest.raises(ApplicationContractError):
        ExecutionBudget(
            token_ceiling=-5,
            model_cost_ceiling=None,
            scientific_tool_cost_ceiling=None,
            warning_threshold=None,
            action_on_limit="stop",
            human_override_required=True,
        )


def test_budget_cannot_authorize_execution() -> None:
    budget = ExecutionBudget(
        token_ceiling=100,
        model_cost_ceiling=None,
        scientific_tool_cost_ceiling=None,
        warning_threshold=0.8,
        action_on_limit="warn",
        human_override_required=True,
    )
    assert budget.authorizes_execution is False
    assert budget.to_dict()["authorizes_execution"] is False
    with pytest.raises(ApplicationContractError) as caught:
        ExecutionBudget(
            token_ceiling=None,
            model_cost_ceiling=None,
            scientific_tool_cost_ceiling=None,
            warning_threshold=None,
            action_on_limit="stop",
            human_override_required=True,
            authorizes_execution=True,
        )
    assert caught.value.code == "budget_does_not_authorize_execution"


def test_fixture_candidate_labels_raw_ipsae() -> None:
    session = pdl1_prototype_session()
    recorded = session.candidates[0]
    assert recorded.candidate_id == "bindcraft_PDL1_l65_s585903_mpnn1"
    assert recorded.ranking_signal == "raw_ipsae"
    assert recorded.ranking_score is None
    assert recorded.is_probability is False
    assert recorded.recommended_as_hard_filter is False
    assert "raw ipSAE" in recorded.score_label
    assert "not a probability" in recorded.score_label
    assert session.runs[0].epistemic_status == "observed"
    assert session.runs[0].memory_house == "episodic"
    assert session.evidence[0].epistemic_status == "observed"
    assert session.artifacts[0].uri.startswith("artifact://")
    assert session.artifacts[0].candidate_id == recorded.candidate_id
    assert recorded.candidate_kind == "observed_identity"
    assert recorded.epistemic_status == "observed"
    assert session.candidates[1].candidate_kind == "placeholder"
    assert session.candidates[1].epistemic_status == "unknown"
    assert session.usage == ()
    assert session.usage_snapshot.estimated_model_cost is None
    assert session.usage_snapshot.scientific_infrastructure_cost is None


def test_serialized_session_matches_the_typescript_fixture() -> None:
    fixture_path = Path("apps/protein-design-workspace/src/session-fixture.json")
    expected = pdl1_prototype_session().to_dict()
    actual = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert actual == expected
    assert actual["usage"] == []
    assert "none" not in json.dumps(actual["usage"])


def test_usage_completion_cannot_precede_start() -> None:
    with pytest.raises(ApplicationContractError) as caught:
        _usage(completed_at=WHEN.replace(hour=11))
    assert caught.value.code == "completed_before_started"
    assert _usage(completed_at=WHEN).completed_at == WHEN


def test_money_ceiling_requires_currency() -> None:
    with pytest.raises(ApplicationContractError) as model_cost:
        ExecutionBudget(
            token_ceiling=None,
            model_cost_ceiling=1.0,
            scientific_tool_cost_ceiling=None,
            warning_threshold=None,
            action_on_limit="stop",
            human_override_required=True,
        )
    assert model_cost.value.code == "cost_and_currency_must_agree"
    with pytest.raises(ApplicationContractError) as tool_cost:
        ExecutionBudget(
            token_ceiling=None,
            model_cost_ceiling=None,
            scientific_tool_cost_ceiling=2.0,
            warning_threshold=None,
            action_on_limit="stop",
            human_override_required=True,
        )
    assert tool_cost.value.code == "cost_and_currency_must_agree"
    budget = ExecutionBudget(
        token_ceiling=10,
        model_cost_ceiling=1.0,
        scientific_tool_cost_ceiling=2.0,
        warning_threshold=None,
        action_on_limit="stop",
        human_override_required=True,
        currency="USD",
    )
    assert budget.currency == "USD"
    assert budget.authorizes_execution is False


@pytest.mark.parametrize(
    "uri",
    [
        "/tmp/candidate.pdb",
        "relative/candidate.pdb",
        r"C:\candidate.pdb",
        "C:/candidate.pdb",
        r"\\server\share\candidate.pdb",
        "file:///tmp/candidate.pdb",
        "file://host/tmp/candidate.pdb",
        "artifact://../secret",
        "artifact://protein_run/../secret",
        "artifact:///tmp/candidate.pdb",
        "https://example.test/candidate.pdb",
    ],
)
def test_artifact_uri_rejects_paths_and_traversal(uri: str) -> None:
    with pytest.raises(ApplicationContractError) as caught:
        ArtifactSummary(
            artifact_id="artifact_a",
            candidate_id="bindcraft_PDL1_l65_s585903_mpnn1",
            kind="run_metadata",
            uri=uri,
            media_type="application/octet-stream",
        )
    assert caught.value.code == "artifact_uri_is_metadata_only"


def test_placeholder_cannot_carry_observed_evidence_or_artifacts() -> None:
    session = pdl1_prototype_session()
    placeholder = CandidateSummary(
        candidate_id="fixture_display_slot",
        candidate_kind="placeholder",
        epistemic_status="unknown",
        rank=None,
        ranking_signal="raw_ipsae",
        ranking_score=None,
        score_label="raw ipSAE (not a probability; not a hard lab filter)",
        provenance="Display slot only.",
    )
    observed = EvidenceSummary(
        evidence_id="evidence_bad",
        candidate_id=placeholder.candidate_id,
        kind="execution_record",
        summary="This must not attach to a placeholder.",
        epistemic_status="observed",
    )
    with pytest.raises(ApplicationContractError) as evidence:
        _replace_session(session, candidates=(placeholder,), evidence=(observed,), artifacts=())
    assert evidence.value.code == "placeholder_has_observed_evidence"
    artifact = ArtifactSummary(
        artifact_id="artifact_bad",
        candidate_id=placeholder.candidate_id,
        kind="run_metadata",
        uri="artifact://protein_run_pdl1_smoke/fixture_display_slot",
        media_type="application/vnd.cellertz.artifact-metadata",
    )
    with pytest.raises(ApplicationContractError) as artifacts:
        _replace_session(session, candidates=(placeholder,), evidence=(), artifacts=(artifact,))
    assert artifacts.value.code == "placeholder_has_artifact"
    with pytest.raises(ApplicationContractError) as kind:
        CandidateSummary(
            candidate_id="fixture_display_slot",
            candidate_kind="placeholder",
            epistemic_status="observed",
            rank=None,
            ranking_signal="raw_ipsae",
            ranking_score=None,
            score_label="raw ipSAE (not a probability; not a hard lab filter)",
            provenance="Display slot only.",
        )
    assert kind.value.code == "placeholder_is_not_observed"


def _replace_session(session: WorkspaceSession, **overrides: object) -> WorkspaceSession:
    payload = {
        "session_id": session.session_id,
        "project": session.project,
        "messages": session.messages,
        "draft": session.draft,
        "pipeline": session.pipeline,
        "agents": session.agents,
        "candidates": session.candidates,
        "evidence": session.evidence,
        "artifacts": session.artifacts,
        "runs": session.runs,
        "usage": session.usage,
        "budget": session.budget,
        "usage_snapshot": session.usage_snapshot,
    }
    payload.update(overrides)
    return WorkspaceSession(**payload)


def test_application_sources_do_not_import_scientific_backends() -> None:
    root = Path("src/cellertz/application")
    imports = [
        line.strip()
        for path in root.rglob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    joined = "\n".join(imports)
    for forbidden in (
        "databricks",
        "mcp",
        "hermes",
        "cellertz.execution",
        "cellertz.second_brain",
        "cellertz.mcp",
        "cellertz.runs",
    ):
        assert forbidden not in joined
