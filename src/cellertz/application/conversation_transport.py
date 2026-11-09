"""JSON edge in front of ReadOnlyConversation. Stdlib HTTP only."""

from __future__ import annotations

import json
from typing import Any

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.conversation import (
    ConversationReply,
    ConversationRequest,
    ReadOnlyConversation,
    _unused,
)
from cellertz.application.request_draft import DesignDrafting

CONVERSATION_PATH = "/api/conversation"
_UNSUPPORTED_TEXT = "This question is not supported by read-only conversational access."
_ADAPTER_TEXT = "The conversational adapter failed. No scientific action was taken."


def handle_conversation(service: ReadOnlyConversation, body: bytes) -> tuple[int, dict[str, Any]]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, _rejected("")
    if not isinstance(payload, dict):
        return 400, _rejected("")
    try:
        request = ConversationRequest(
            session_id=payload.get("session_id", ""),
            question=payload.get("question", ""),
            run_id=payload.get("run_id"),
            candidate_id=payload.get("candidate_id"),
        )
    except ApplicationContractError:
        session_id = payload.get("session_id")
        return 400, _rejected(session_id if isinstance(session_id, str) else "")
    try:
        reply = service.ask(request)
    except Exception:  # noqa: BLE001 — the edge returns a safe body, not a traceback
        reply = ConversationReply(
            status="adapter_failure",
            text=_ADAPTER_TEXT,
            session_id=request.session_id,
            run_id=request.run_id,
            evidence=(),
            instrumentation=_unused("unknown"),
        )
    return 200, reply.to_dict()


def _rejected(session_id: str) -> dict[str, Any]:
    return ConversationReply(
        status="unsupported_question",
        text=_UNSUPPORTED_TEXT,
        session_id=session_id,
        run_id=None,
        evidence=(),
        instrumentation=_unused("unknown"),
    ).to_dict()


def configured_handler(
    service: ReadOnlyConversation,
    drafter: DesignDrafting | None = None,
    approvals: Any | None = None,
    runs: Any | None = None,
    session_id: str | None = None,
    readiness: Any | None = None,
    authorizations: Any | None = None,
    scientific_runs: Any | None = None,
    coordinator: Any | None = None,
    generation_plans: Any | None = None,
    journeys: Any | None = None,
    campaigns: Any | None = None,
    request_supervisor: Any | None = None,
) -> type:
    """One handler for the 6B through 6K paths. 6K executes only a queued scientific run."""
    from http.server import BaseHTTPRequestHandler

    from cellertz.application.approval_transport import (
        APPROVAL_PATH,
        EXECUTION_PATH,
        handle_approved_execution,
        handle_design_approval,
    )
    from cellertz.application.approved_run_transport import (
        RUNS_PATH,
        ArtifactResponse,
        handle_create_approved_run,
        handle_list_approved_runs,
        handle_read_approved_run,
        handle_read_artifact,
        handle_retry_approved_run,
    )
    from cellertz.application.request_transport import (
        DESIGN_DRAFT_PATH,
        handle_design_draft,
        handle_read_design_draft,
    )
    from cellertz.application.scientific_authorization_transport import (
        AUTHORIZATIONS_PATH,
        handle_scientific_authorization,
    )
    from cellertz.application.scientific_run_transport import (
        RUNS_PATH as SCIENTIFIC_RUNS_PATH,
    )
    from cellertz.application.scientific_run_transport import (
        ScientificArtifactResponse,
        handle_scientific_run,
    )
    from cellertz.application.scientific_runner_readiness_transport import (
        READINESS_PATH,
        handle_read_scientific_runner_readiness,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if campaigns is not None and path.startswith("/api/campaigns"):
                from cellertz.application.campaign import handle_campaign

                campaign_result = handle_campaign(campaigns, "GET", path, b"")
                if campaign_result is not None:
                    self._send(campaign_result[0], campaign_result[1])
                    return
            if path == AUTHORIZATIONS_PATH or path.startswith(f"{AUTHORIZATIONS_PATH}/"):
                if authorizations is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                status, payload = handle_scientific_authorization(
                    authorizations, "GET", self.path, b"", session_id or ""
                )
                self._send(status, payload)
                return
            if path == READINESS_PATH:
                if readiness is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                status, payload = handle_read_scientific_runner_readiness(
                    readiness, self.path, session_id or ""
                )
                self._send(status, payload)
                return
            if path == SCIENTIFIC_RUNS_PATH or path.startswith(f"{SCIENTIFIC_RUNS_PATH}/"):
                self._scientific("GET", session_id or "")
                return
            if journeys is not None and path.startswith("/api/design-journeys"):
                from cellertz.application.design_journey import handle_design_journey

                journey_result = handle_design_journey(journeys, "GET", path, b"")
                if journey_result is not None:
                    self._send(journey_result[0], journey_result[1])
                    return
            if path == DESIGN_DRAFT_PATH:
                if drafter is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                status, payload = handle_read_design_draft(drafter, self.path)
                self._send(status, payload)
                return
            if path == "/api/design-supervisor" and request_supervisor is not None:
                from cellertz.application.request_supervisor import handle_list_supervisor_analyses

                status, payload = handle_list_supervisor_analyses(request_supervisor, self.path)
                self._send(status, payload)
                return
            if path == "/api/generation-plan/suggestion" and generation_plans is not None and drafter is not None and request_supervisor is not None:
                from cellertz.application.plan_configuration_suggestion import handle_plan_suggestion

                status, payload = handle_plan_suggestion(
                    _plan_suggester(request_supervisor),
                    generation_plans,
                    drafter,
                    method="GET",
                    path=self.path,
                )
                self._send(status, payload)
                return
            if path == "/api/generation-plan" and generation_plans is not None and drafter is not None:
                from cellertz.application.generation_plan import handle_design_request_plan

                status, payload = handle_design_request_plan(
                    generation_plans,
                    drafter,
                    method="GET",
                    path=self.path,
                    configuration=readiness,
                )
                self._send(status, payload)
                return
            if path == "/api/attempt-coordinator" and coordinator is not None:
                from cellertz.application.attempt_coordinator import handle_attempt_coordinator_read

                status, payload = handle_attempt_coordinator_read(coordinator, self.path)
                self._send(status, payload)
                return
            from cellertz.application.attempt_inspection import (
                StructureArtifact,
                handle_attempt_get,
            )

            reader = getattr(coordinator, "traces_for", None) if coordinator is not None else None
            documents = None
            if journeys is not None:
                saved = getattr(journeys, "saved_documents", None)
                if callable(saved):
                    loaded = saved()
                    documents = loaded if isinstance(loaded, list) else []
            attempt_result = handle_attempt_get(
                self.path,
                traces_for=reader if callable(reader) else None,
                journey_documents=documents,
            )
            if attempt_result is not None:
                if isinstance(attempt_result, StructureArtifact):
                    self._send_structure(attempt_result)
                    return
                self._send(attempt_result[0], attempt_result[1])
                return
            if runs is None or not (path == RUNS_PATH or path.startswith(f"{RUNS_PATH}/")):
                self.send_response(404)
                self.end_headers()
                return
            if path == RUNS_PATH:
                status, payload = handle_list_approved_runs(runs, session_id or "", self.path)
                self._send(status, payload)
                return
            artifact = _artifact_route(path)
            if artifact is not None:
                if "?" in self.path:
                    status, payload = handle_read_artifact(runs, "", "not-a-run", "not-an-artifact")
                    self._send(status, payload)
                    return
                result = handle_read_artifact(runs, session_id or "", artifact[0], artifact[1])
                if isinstance(result, ArtifactResponse):
                    self._send_artifact(result)
                    return
                self._send(result[0], result[1])
                return
            status, payload = handle_read_approved_run(runs, self.path)
            self._send(status, payload)

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                length = 0
            body = self.rfile.read(length)
            if campaigns is not None and path.startswith("/api/campaigns"):
                from cellertz.application.campaign import handle_campaign

                campaign_result = handle_campaign(campaigns, "POST", path, body)
                if campaign_result is not None:
                    self._send(campaign_result[0], campaign_result[1])
                    return
            if path == AUTHORIZATIONS_PATH or path.startswith(f"{AUTHORIZATIONS_PATH}/"):
                if authorizations is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                status, payload = handle_scientific_authorization(
                    authorizations, "POST", self.path, body, session_id or ""
                )
                self._send(status, payload)
                return
            if path == SCIENTIFIC_RUNS_PATH or path.startswith(f"{SCIENTIFIC_RUNS_PATH}/"):
                self._scientific("POST", session_id or "", body)
                return
            if journeys is not None and path.startswith("/api/design-journeys"):
                from cellertz.application.design_journey import handle_design_journey

                journey_result = handle_design_journey(journeys, "POST", path, body)
                if journey_result is not None:
                    self._send(journey_result[0], journey_result[1])
                    return
            if path == "/api/generation-plan/suggestion" and generation_plans is not None and drafter is not None and request_supervisor is not None:
                from cellertz.application.plan_configuration_suggestion import handle_plan_suggestion

                status, payload = handle_plan_suggestion(
                    _plan_suggester(request_supervisor),
                    generation_plans,
                    drafter,
                    method="POST",
                    path=self.path,
                    body=body,
                )
                self._send(status, payload)
                return
            if path == "/api/generation-plan/revision" and generation_plans is not None and drafter is not None:
                from cellertz.application.generation_plan import handle_design_request_plan_revision

                status, payload = handle_design_request_plan_revision(
                    generation_plans,
                    drafter,
                    body=body,
                    configuration=readiness,
                )
            elif path == "/api/generation-plan" and generation_plans is not None and drafter is not None:
                from cellertz.application.generation_plan import handle_design_request_plan

                status, payload = handle_design_request_plan(
                    generation_plans,
                    drafter,
                    method="POST",
                    path=self.path,
                    body=body,
                    configuration=readiness,
                )
            elif path == "/api/generation-plan/decision" and generation_plans is not None:
                from cellertz.application.generation_plan import handle_generation_plan

                status, payload = handle_generation_plan(generation_plans, body, "decision")
            elif path == "/api/generation-plan/authorization" and generation_plans is not None:
                from cellertz.application.generation_plan import handle_generation_plan

                status, payload = handle_generation_plan(generation_plans, body, "authorization")
            elif path == "/api/generation-plan/apptainer-demonstration" and generation_plans is not None:
                from cellertz.application.generation_plan import handle_generation_plan

                status, payload = handle_generation_plan(
                    generation_plans, body, "apptainer-demonstration"
                )
            elif path == "/api/generation-plan/dispatch" and generation_plans is not None:
                from cellertz.application.generation_plan import handle_generation_plan

                status, payload = handle_generation_plan(generation_plans, body, "dispatch")
            elif path == "/api/attempt-coordinator" and coordinator is not None:
                from cellertz.application.attempt_coordinator import handle_attempt_coordinator

                status, payload = handle_attempt_coordinator(coordinator, body)
            elif path == CONVERSATION_PATH:
                status, payload = handle_conversation(service, body)
            elif path == DESIGN_DRAFT_PATH and drafter is not None:
                status, payload = handle_design_draft(drafter, body)
            elif path == "/api/design-supervisor" and request_supervisor is not None:
                from cellertz.application.request_supervisor import handle_design_supervisor

                status, payload = handle_design_supervisor(request_supervisor, body)
            elif path == APPROVAL_PATH and approvals is not None:
                status, payload = handle_design_approval(approvals, body)
            elif path == EXECUTION_PATH and approvals is not None:
                status, payload = handle_approved_execution(approvals, body)
            elif runs is not None and path == RUNS_PATH:
                status, payload = handle_create_approved_run(runs, body)
            elif runs is not None and path.startswith(f"{RUNS_PATH}/") and path.endswith("/retry"):
                run_id = path.removeprefix(f"{RUNS_PATH}/").removesuffix("/retry")
                status, payload = handle_retry_approved_run(runs, run_id, body)
            else:
                self.send_response(404)
                self.end_headers()
                return
            self._send(status, payload)

        def do_PUT(self) -> None:
            self._reject_scientific_method("PUT")

        def do_PATCH(self) -> None:
            self._reject_scientific_method("PATCH")

        def do_DELETE(self) -> None:
            self._reject_scientific_method("DELETE")

        def _reject_scientific_method(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            if path == SCIENTIFIC_RUNS_PATH or path.startswith(f"{SCIENTIFIC_RUNS_PATH}/"):
                self._scientific(method, session_id or "")
                return
            self.send_response(501)
            self.end_headers()

        def _scientific(self, method: str, owner: str, body: bytes = b"") -> None:
            if scientific_runs is None:
                self.send_response(404)
                self.end_headers()
                return
            result = handle_scientific_run(scientific_runs, method, self.path, body, owner)
            if type(result) is ScientificArtifactResponse:
                self._send_artifact(result)
                return
            if type(result) is not tuple:
                self.send_response(404)
                self.end_headers()
                return
            self._send(result[0], result[1])

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_structure(self, result: object) -> None:
            from cellertz.application.attempt_inspection import StructureArtifact

            if not isinstance(result, StructureArtifact):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(result.status)
            self.send_header("Content-Type", result.media_type)
            self.send_header("Content-Length", str(len(result.body)))
            self.send_header("Content-Disposition", f'attachment; filename="{result.filename}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "private, no-store")
            self.end_headers()
            self.wfile.write(result.body)

        def _send_artifact(self, result: ArtifactResponse) -> None:
            self.send_response(result.status)
            self.send_header("Content-Type", result.media_type)
            self.send_header("Content-Length", str(len(result.body)))
            self.send_header("Content-Disposition", f'attachment; filename="{result.filename}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "private, no-store")
            self.end_headers()
            self.wfile.write(result.body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _artifact_route(path: str) -> tuple[str, str] | None:
    from cellertz.application.approved_run_transport import RUNS_PATH

    marker = "/artifacts/"
    prefix = f"{RUNS_PATH}/"
    if not path.startswith(prefix) or marker not in path:
        return None
    run_id, separator, artifact_id = path.removeprefix(prefix).partition(marker)
    if not separator or not run_id or not artifact_id or "/" in artifact_id:
        return None
    return run_id, artifact_id


def _plan_suggester(supervisor: Any) -> Any:
    cached = getattr(supervisor, "_plan_suggester", None)
    if cached is not None:
        return cached
    from cellertz.application.plan_configuration_suggestion import (
        MemorySuggestionStore,
        PlanConfigurationSuggester,
        limits_for_suggestion,
        open_plan_suggestion_store,
    )

    store = open_plan_suggestion_store() or MemorySuggestionStore()
    from cellertz.application.literature_search import EuropePMCLiteratureSearch

    cached = PlanConfigurationSuggester(
        supervisor._model,
        limits_for_suggestion(supervisor._limits),
        store,
        knowledge=_knowledge_reader(),
        literature=EuropePMCLiteratureSearch(),
    )
    setattr(supervisor, "_plan_suggester", cached)
    return cached


def _knowledge_reader() -> Any:
    try:
        from cellertz.knowledge.reader import FileKnowledgeReader, default_second_brain_root

        return FileKnowledgeReader(default_second_brain_root())
    except Exception:  # noqa: BLE001 — a missing vault is reported as a limitation
        return None


def serve_conversation(
    service: ReadOnlyConversation,
    host: str = "127.0.0.1",
    port: int = 8765,
    drafter: DesignDrafting | None = None,
    approvals: Any | None = None,
    runs: Any | None = None,
    session_id: str | None = None,
    readiness: Any | None = None,
    authorizations: Any | None = None,
    scientific_runs: Any | None = None,
    coordinator: Any | None = None,
    generation_plans: Any | None = None,
    journeys: Any | None = None,
    campaigns: Any | None = None,
    request_supervisor: Any | None = None,
) -> None:
    from http.server import ThreadingHTTPServer

    ThreadingHTTPServer(
        (host, port),
        configured_handler(
            service,
            drafter,
            approvals,
            runs,
            session_id,
            readiness,
            authorizations,
            scientific_runs,
            coordinator,
            generation_plans,
            journeys,
            campaigns,
            request_supervisor,
        ),
    ).serve_forever()
