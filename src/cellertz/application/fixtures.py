"""Deterministic display fixture for the recorded PDL1 smoke identity.

The numbers and files from that run are not copied here. This fixture
names the recorded run and candidate so the workspace can show the
boundary. It is not a new observation and not a validated result.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cellertz.application.contracts import (
    AgentSnapshot,
    ArtifactSummary,
    CandidateSummary,
    ConversationMessage,
    EvidenceSummary,
    ExecutionBudget,
    PipelineStageSnapshot,
    ProjectContext,
    ProteinDesignDraft,
    RunSummary,
    UsageBudgetSnapshot,
    WorkspaceSession,
)

_WHEN = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)
_SCORE_LABEL = "raw ipSAE (not a probability; not a hard lab filter)"
_RECORDED_CANDIDATE = "bindcraft_PDL1_l65_s585903_mpnn1"
_RECORDED_RUN = "protein_run_pdl1_smoke"


def pdl1_prototype_session() -> WorkspaceSession:
    return WorkspaceSession(
        session_id="session_pdl1_prototype",
        project=ProjectContext(
            project_id="protein_design_prototype",
            target_id="PDL1",
            design_goal="Show a recorded BindCraft smoke identity without executing it.",
            prototype_label="Phase 6A prototype. Fixture only. Read only.",
        ),
        messages=(
            ConversationMessage(
                message_id="msg_scientist_1",
                role="scientist",
                created_at=_WHEN,
                content="Show the recorded PDL1 smoke identity in this workspace.",
            ),
            ConversationMessage(
                message_id="msg_system_1",
                role="system",
                created_at=_WHEN,
                content=(
                    "This panel is a local fixture. It does not call Hermes, "
                    "MCP, the Supervisor, or BindCraft."
                ),
            ),
        ),
        draft=ProteinDesignDraft(
            draft_id="draft_pdl1_prototype",
            target_id="PDL1",
            design_goal="Binder display for the recorded smoke identity.",
            status="incomplete",
        ),
        pipeline=(
            PipelineStageSnapshot(
                stage_id="conversation",
                label="Scientist conversation",
                display_status="shown",
                detail="Local messages only. Not scientific memory.",
            ),
            PipelineStageSnapshot(
                stage_id="draft",
                label="ProteinDesignDraft",
                display_status="shown",
                detail="Incomplete draft. Not an approved request.",
            ),
            PipelineStageSnapshot(
                stage_id="approval",
                label="Review and approval",
                display_status="future",
                detail="Phase 6D. Not available in this prototype.",
            ),
            PipelineStageSnapshot(
                stage_id="supervisor",
                label="Supervisor execution",
                display_status="not_executed",
                detail="Phase 6D. This prototype does not start a run.",
            ),
        ),
        agents=(
            AgentSnapshot(agent_id="supervisor", role="Stage display", display_status="idle"),
            AgentSnapshot(agent_id="ranking", role="raw ipSAE label", display_status="shown"),
        ),
        candidates=(
            CandidateSummary(
                candidate_id=_RECORDED_CANDIDATE,
                candidate_kind="observed_identity",
                epistemic_status="observed",
                rank=None,
                ranking_signal="raw_ipsae",
                ranking_score=None,
                score_label=_SCORE_LABEL,
                provenance=(
                    "Identity from the recorded episodic smoke run. "
                    "This prototype does not republish a score."
                ),
            ),
            CandidateSummary(
                candidate_id="fixture_display_slot",
                candidate_kind="placeholder",
                epistemic_status="unknown",
                rank=None,
                ranking_signal="raw_ipsae",
                ranking_score=None,
                score_label=_SCORE_LABEL,
                provenance="Display slot only. Not an observed or validated candidate.",
            ),
        ),
        evidence=(
            EvidenceSummary(
                evidence_id="evidence_pdl1_smoke_record",
                candidate_id=_RECORDED_CANDIDATE,
                kind="execution_record",
                summary=(
                    f"{_RECORDED_RUN} is an observed computational execution. "
                    "It is not a validated biological conclusion."
                ),
                epistemic_status="observed",
            ),
            EvidenceSummary(
                evidence_id="evidence_pdl1_provenance",
                candidate_id=_RECORDED_CANDIDATE,
                kind="provenance",
                summary="Provenance is separate from the raw ipSAE label.",
                epistemic_status="unknown",
            ),
        ),
        artifacts=(
            ArtifactSummary(
                artifact_id="artifact_pdl1_smoke_metadata",
                candidate_id=_RECORDED_CANDIDATE,
                kind="run_metadata",
                uri=f"artifact://{_RECORDED_RUN}/{_RECORDED_CANDIDATE}",
                media_type="application/vnd.cellertz.artifact-metadata",
            ),
        ),
        runs=(
            RunSummary(
                run_id=_RECORDED_RUN,
                target_id="PDL1",
                memory_house="episodic",
                epistemic_status="observed",
                display_status="Recorded execution. Not opened by this prototype.",
            ),
        ),
        usage=(),
        budget=ExecutionBudget(
            token_ceiling=None,
            model_cost_ceiling=None,
            scientific_tool_cost_ceiling=None,
            warning_threshold=None,
            action_on_limit="stop",
            human_override_required=True,
        ),
        usage_snapshot=UsageBudgetSnapshot(
            session_id="session_pdl1_prototype",
            estimated_model_cost=None,
            scientific_infrastructure_cost=None,
            currency=None,
        ),
    )
