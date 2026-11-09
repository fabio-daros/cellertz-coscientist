"""Serve the read-only conversation edge and the design-draft edge.

The handler is `serve_conversation(service, drafter=...)`. This module
only chooses the demo catalog. The draft extractor is deterministic and
is not an LLM. It does not start a run.

Usage: python -m cellertz.conversation
"""

from __future__ import annotations

from cellertz.application.approval import HumanApprovalGate
from cellertz.application.approved_run import ApprovedRunService
from cellertz.application.approved_run_config import (
    open_approved_run_store,
    resolve_approved_run_worker_poll_seconds,
)
from cellertz.application.approved_run_worker import ApprovedRunWorker
from cellertz.application.attempt_coordinator import coordinator_from_environment
from cellertz.application.conversation import ReadOnlyConversation
from cellertz.application.conversation_transport import serve_conversation
from cellertz.application.design_journey import DesignJourneyService, open_design_journey_store
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.generation_plan import (
    GenerationPlanService,
    open_registered_input_catalog,
)
from cellertz.application.generation_plan_store import open_generation_plan_store
from cellertz.application.governed_agent_runtime import GovernedProteinAgentRuntime
from cellertz.application.request_draft import DesignDrafting, DeterministicDraftExtractor
from cellertz.application.request_draft_store import open_design_draft_store
from cellertz.application.request_supervisor_store import open_supervisor_analysis_store
from cellertz.application.scientific_authorization import ScientificAuthorizationService
from cellertz.application.scientific_authorization_config import (
    open_scientific_authorization_store,
    resolve_scientific_authorization_backend,
)
from cellertz.application.scientific_run import ScientificRunService
from cellertz.application.scientific_run_config import (
    ScientificRunConfigurationError,
    open_scientific_run_store,
    resolve_scientific_run_backend,
    resolve_scientific_run_execution,
    resolve_scientific_run_worker_poll_seconds,
)
from cellertz.application.scientific_run_worker import ScientificRunWorker
from cellertz.application.scientific_runner_readiness import load_scientific_runner_readiness
from cellertz.conversation.config import resolve_conversation_model
from cellertz.conversation.context import GroundedConversationQuery
from cellertz.conversation.episodic import EpisodicReadOnlyQuery
from cellertz.knowledge.reader import FileKnowledgeReader, default_second_brain_root
from cellertz.mcp.episodic_memory import get_scientific_memory_reader


def conversation_port(environ: object | None = None) -> int:
    """Listen port for this process. Unset keeps the active installation on 8765."""

    import os

    env = os.environ if environ is None else environ
    raw = env.get("CELLERTZ_CONVERSATION_PORT", "").strip()
    if raw == "":
        return 8765
    if not raw.isdigit():
        raise ValueError("invalid_conversation_port")
    port = int(raw)
    if port < 1 or port > 65535 or str(port) != raw:
        raise ValueError("invalid_conversation_port")
    return port


def main(*, control: object | None = None, deadline: object | None = None) -> None:
    port = conversation_port()
    poll_seconds = resolve_approved_run_worker_poll_seconds()
    authorization_backend, _authorization_path = resolve_scientific_authorization_backend()
    scientific_poll = resolve_scientific_run_worker_poll_seconds()
    run_backend, _run_path = resolve_scientific_run_backend()
    if authorization_backend != run_backend:
        raise ScientificRunConfigurationError("scientific_store_durability_mismatch")
    execution = resolve_scientific_run_execution()
    infrastructure = None
    if execution == "enabled":
        from cellertz.application.pdl1_docker import prepare_enabled_infrastructure

        infrastructure = prepare_enabled_infrastructure(control=control, deadline=deadline)
    demo = pdl1_prototype_session()
    if infrastructure is None:
        readiness = load_scientific_runner_readiness(session_id=demo.session_id)
    else:
        readiness = infrastructure.readiness(demo.session_id)
        if readiness.read().status != "ready":
            raise ScientificRunConfigurationError(
                "scientific_run_executor_unconfigured",
                missing=("readiness",),
            )
    sessions = {demo.session_id: demo}
    draft_store = open_design_draft_store()
    try:
        drafter = DesignDrafting(sessions, DeterministicDraftExtractor(), store=draft_store)
    except Exception:
        if draft_store is not None:
            draft_store.close()
        raise
    gate = HumanApprovalGate(sessions, drafter)
    runs = ApprovedRunService(gate, store=open_approved_run_store())
    auth_store = None
    run_store = None
    plan_store = None
    campaign_service = None
    try:
        auth_store = open_scientific_authorization_store()
        authorizations = ScientificAuthorizationService(
            sessions,
            drafter,
            gate,
            runs,
            readiness,
            store=auth_store,
        )
        run_store = open_scientific_run_store()
        if execution == "governed-fixture":
            executor = GovernedProteinAgentRuntime()
            execution_enabled = True
        elif infrastructure is not None:
            executor = infrastructure.bind(authorizations=authorizations, store=run_store)
            execution_enabled = True
        else:
            executor = None
            execution_enabled = False
        scientific_runs = ScientificRunService(
            authorizations,
            store=run_store,
            executor=executor,
            execution_enabled=execution_enabled,
        )
        plan_store = open_generation_plan_store()
        demonstration = None
        if execution == "apptainer-demonstration":
            import os
            from pathlib import Path

            from cellertz.application.apptainer_demonstration import (
                ApptainerDemonstrationLauncher,
                reap_abandoned,
            )

            root = os.environ.get("CELLERTZ_APPTAINER_DEMONSTRATION_ROOT", "").strip()
            if root:
                reap_abandoned(Path(root))
            demonstration = ApptainerDemonstrationLauncher()
        generation_plans = GenerationPlanService(
            scientific_runs,
            store=plan_store,
            inputs=open_registered_input_catalog(),
            authorizations=authorizations,
            session_id=demo.session_id,
            demonstration=demonstration,
        )
        from cellertz.application.campaign import CampaignService, open_campaign_store

        campaign_service = CampaignService(open_campaign_store())
    except Exception:
        if campaign_service is not None:
            campaign_service.close()
        if plan_store is not None:
            plan_store.close()
        if run_store is not None:
            run_store.close()
        if auth_store is not None:
            auth_store.close()
        runs.close()
        if draft_store is not None:
            draft_store.close()
        raise
    worker = ApprovedRunWorker(runs, poll_seconds=poll_seconds)
    scientific_worker = ScientificRunWorker(scientific_runs, poll_seconds=scientific_poll)
    journeys = None
    journey_worker = None
    analysis_store = None
    try:
        runs.recover_interrupted()
        scientific_runs.startup()
        worker.start()
        scientific_worker.start()
        journeys = DesignJourneyService(
            generation_plans,
            knowledge=FileKnowledgeReader(default_second_brain_root()),
            store=open_design_journey_store(),
            attempt_root=_journey_attempt_root(),
            results_catalog=_results_catalog(),
        )
        from cellertz.application.journey_orchestration import JourneyOrchestrationWorker

        journey_worker = JourneyOrchestrationWorker(journeys, poll_seconds=poll_seconds)
        journeys.attach_orchestrator(journey_worker)
        journey_worker.start()
        conversation_model = resolve_conversation_model()
        from cellertz.application.request_supervisor import DesignRequestSupervisor, resolve_supervisor_limits

        analysis_store = open_supervisor_analysis_store()
        serve_conversation(
            ReadOnlyConversation(
                GroundedConversationQuery(
                    EpisodicReadOnlyQuery(sessions, get_scientific_memory_reader()),
                    drafts=drafter,
                    knowledge=FileKnowledgeReader(default_second_brain_root()),
                    fixture_runs=runs,
                    scientific_runs=scientific_runs,
                ),
                model=conversation_model,
            ),
            drafter=drafter,
            approvals=gate,
            runs=runs,
            session_id=demo.session_id,
            readiness=readiness,
            authorizations=authorizations,
            scientific_runs=scientific_runs,
            coordinator=coordinator_from_environment(generation_plans),
            generation_plans=generation_plans,
            journeys=journeys,
            campaigns=campaign_service,
            request_supervisor=DesignRequestSupervisor(
                drafter,
                conversation_model,
                resolve_supervisor_limits(),
                readiness,
                analyses=analysis_store,
            ),
            port=port,
        )
    finally:
        if campaign_service is not None:
            campaign_service.close()
        journey_stopped = True if journey_worker is None else journey_worker.stop()
        scientific_stopped = scientific_worker.stop()
        approved_stopped = worker.stop()
        if scientific_stopped:
            authorizations.close()
            scientific_runs.close()
            generation_plans.close()
            if journeys is not None and journey_stopped:
                journeys.close()
        if approved_stopped and scientific_stopped:
            runs.close()
        if analysis_store is not None:
            analysis_store.close()
        if draft_store is not None:
            draft_store.close()


def _results_catalog():
    import os
    from pathlib import Path

    raw = os.environ.get("CELLERTZ_ATTEMPT_CATALOG", "").strip()
    if not raw or not os.path.isabs(raw) or any(char in raw for char in "\x00\n\r"):
        return None
    return Path(raw)


def _journey_attempt_root():
    import os
    from pathlib import Path

    raw = os.environ.get("CELLERTZ_JOURNEY_ATTEMPT_ROOT", "").strip()
    if not raw or not os.path.isabs(raw) or any(char in raw for char in "\x00\n\r"):
        return None
    return Path(raw)


if __name__ == "__main__":
    main()
