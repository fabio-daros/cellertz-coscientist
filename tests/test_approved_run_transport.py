"""Phase 6E HTTP edges. No network."""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cellertz.application.approved_run import ApprovedRunService, FixtureApprovedRunRunner
from cellertz.application.approved_run_transport import (
    RUNS_PATH,
    handle_create_approved_run,
    handle_read_approved_run,
    handle_retry_approved_run,
)
from cellertz.application.conversation_transport import configured_handler
from cellertz.application.request_draft import DesignDraftCommand
from tests.test_human_approval_transport import SESSION, _body, _decision, _gate

_BAD = [None, 123, True, ["run"], {"id": "run"}]
_CREATE_FIELDS = ("session_id", "approval_id", "idempotency_key")
_FORBIDDEN = (
    "digest",
    "revision",
    "budget",
    "execution_authorized",
    "status",
    "transition",
    "attempt",
    "command",
    "argv",
    "shell",
    "path",
    "artifact",
    "evidence",
    "candidate",
    "usage",
    "cost",
    "executor",
)


def _ready(message: str = "design a binder for pdl1 using fixture with 1 candidates", error=None):
    from cellertz.application.approval_transport import (
        handle_approved_execution,
        handle_design_approval,
    )

    gate, adapter, draft, drafter = _gate(message)

    _status, approved = handle_design_approval(gate, _body(_decision(draft)))
    assert approved["approval"] is not None
    approval_id = approved["approval"]["approval_id"]
    dispatched_status, dispatched = handle_approved_execution(
        gate,
        _body(
            {
                "session_id": SESSION,
                "approval_id": approval_id,
                "idempotency_key": "dispatch-1",
            }
        ),
    )
    runner = FixtureApprovedRunRunner(error)
    service = ApprovedRunService(gate, runner=runner)
    return (
        service,
        runner,
        gate,
        drafter,
        draft,
        adapter,
        approval_id,
        dispatched_status,
        dispatched,
    )


def _create_payload(approval_id: str, **overrides) -> dict:
    payload = {"session_id": SESSION, "approval_id": approval_id, "idempotency_key": "run-1"}
    payload.update(overrides)
    return payload


def test_valid_create_read_and_routes_reject_client_control():
    service, runner, gate, _drafter, draft, adapter, approval_id, dispatched_status, dispatched = (
        _ready()
    )
    assert dispatched_status == 200
    assert "not a scientific run" in dispatched["text"].casefold()
    status, body = handle_create_approved_run(service, _body(_create_payload(approval_id)))
    assert status == 202
    assert body["status"] == "run_queued"
    assert body["run"]["approval_id"] == approval_id
    assert body["run"]["request_id"] == draft.draft.request_id
    assert body["run"]["request_revision"] == draft.draft.revision
    assert body["scientific_validation"] is False
    assert body["candidates"] == []
    assert runner.calls == 0
    assert adapter.calls == 1
    run_id = body["run"]["run_id"]
    assert service.process_next() is not None
    read_status, observed = handle_read_approved_run(
        service, f"{RUNS_PATH}/{run_id}?session_id={SESSION}"
    )
    assert read_status == 200
    assert observed["status"] == "run_observed"
    assert observed["run"]["status"] == "completed"
    assert runner.calls == 1
    missing_status, missing = handle_read_approved_run(
        service,
        f"{RUNS_PATH}/run_{'ab' * 16}?session_id={SESSION}",
    )
    assert missing_status == 200
    assert missing["status"] == "run_absent"
    for field in _FORBIDDEN:
        rejected, payload = handle_create_approved_run(
            service,
            _body(_create_payload(approval_id, **{field: "client"})),
        )
        assert rejected == 400
        assert payload["status"] == "invalid_payload"
        assert payload["run"] is None
    assert runner.calls == 1
    encoded = json.dumps(body)
    assert "Traceback" not in encoded
    assert gate.preflight(session_id=SESSION, approval_id=approval_id).status == "preflight_ready"


def test_invalid_json_utf8_and_types_do_not_create_a_run():
    service, runner, _gate, _drafter, _draft, _adapter, approval_id, _status, _body_reply = _ready()
    del _body_reply
    for raw in (b"{", b"[]", b"null", b"\xff"):
        status, body = handle_create_approved_run(service, raw)
        encoded = json.dumps(body)
        assert status == 400
        assert body["status"] == "invalid_payload"
        assert "Traceback" not in encoded
        assert "UnicodeDecodeError" not in encoded
        assert "JSONDecodeError" not in encoded
    assert runner.calls == 0
    for field in _CREATE_FIELDS:
        for value in _BAD:
            payload = _create_payload(approval_id)
            payload[field] = value
            status, body = handle_create_approved_run(service, _body(payload))
            assert status == 400
            assert body["status"] == "invalid_payload"
            assert body["run"] is None
    assert runner.calls == 0


def test_domain_conflicts_keep_their_http_status():
    from cellertz.application.approval_transport import (
        handle_approved_execution,
        handle_design_approval,
    )

    service, runner, _gate_ready, drafter, draft, _adapter, approval_id, _status, _dispatched = (
        _ready()
    )
    absent_status, absent = handle_create_approved_run(
        service,
        _body(_create_payload("approval_missing", idempotency_key="missing")),
    )
    assert absent_status == 200
    assert absent["status"] == "approval_absent"
    assert runner.calls == 0
    denied_gate, _adapter, denied_draft, _drafter = _gate()
    _decision_status, denied = handle_design_approval(
        denied_gate,
        _body(_decision(denied_draft, decision="rejected", idempotency_key="decision-rejected")),
    )
    denied_status, denied_body = handle_create_approved_run(
        ApprovedRunService(denied_gate),
        _body(_create_payload(denied["approval"]["approval_id"], idempotency_key="run-denied")),
    )
    assert denied_status == 200
    assert denied_body["status"] == "authorization_denied"
    drafter.revise(
        DesignDraftCommand(
            SESSION,
            "design a binder for bcma using fixture with 2 candidates",
            request_id=draft.draft.request_id,
            revision=draft.draft.revision,
        )
    )
    stale_status, stale = handle_create_approved_run(service, _body(_create_payload(approval_id)))
    assert stale_status == 409
    assert stale["status"] == "digest_mismatch"
    (
        budget_service,
        _runner,
        budget_gate,
        _drafter,
        _draft,
        _adapter,
        budget_approval,
        _status,
        _done,
    ) = _ready()
    budget_gate._sessions[SESSION] = object()
    budget_status, budget = handle_create_approved_run(
        budget_service,
        _body(_create_payload(budget_approval, idempotency_key="run-budget")),
    )
    assert budget_status == 200
    assert budget["status"] == "budget_invalid"
    blocked_gate, _adapter, blocked_draft, _drafter = _gate(
        "design a binder for pdl1 using mber with 1 candidates"
    )
    _recorded_status, recorded = handle_design_approval(
        blocked_gate,
        _body(_decision(blocked_draft, idempotency_key="decision-mber")),
    )
    blocked_status, blocked = handle_create_approved_run(
        ApprovedRunService(blocked_gate),
        _body(_create_payload(recorded["approval"]["approval_id"], idempotency_key="run-mber")),
    )
    assert blocked_status == 200
    assert blocked["status"] == "profile_not_allowlisted"
    second_gate, _adapter, _second_draft, second_drafter = _gate()
    first_draft = second_drafter.revise(
        DesignDraftCommand(SESSION, "design a binder for pdl1 using fixture with 1 candidates")
    )
    _second_status, second_approved = handle_design_approval(
        second_gate,
        _body(_decision(first_draft, idempotency_key="decision-second")),
    )
    handle_approved_execution(
        second_gate,
        _body(
            {
                "session_id": SESSION,
                "approval_id": second_approved["approval"]["approval_id"],
                "idempotency_key": "dispatch-second",
            }
        ),
    )
    second_service = ApprovedRunService(second_gate)
    handle_create_approved_run(
        second_service,
        _body(
            _create_payload(
                second_approved["approval"]["approval_id"], idempotency_key="run-unique"
            )
        ),
    )
    later = second_drafter.revise(
        DesignDraftCommand(SESSION, "design a binder for pdl1 using fixture with 4 candidates")
    )
    _third_status, third = handle_design_approval(
        second_gate,
        _body(_decision(later, idempotency_key="decision-third")),
    )
    handle_approved_execution(
        second_gate,
        _body(
            {
                "session_id": SESSION,
                "approval_id": third["approval"]["approval_id"],
                "idempotency_key": "dispatch-third",
            }
        ),
    )
    clash_status, clash = handle_create_approved_run(
        second_service,
        _body(_create_payload(third["approval"]["approval_id"], idempotency_key="run-unique")),
    )
    assert clash_status == 409
    assert clash["status"] == "idempotency_conflict"


def test_retry_read_and_adapter_failure_are_safe():
    service, runner, _gate, _drafter, _draft, _adapter, approval_id, _status, _done = _ready(
        error=RuntimeError("Traceback /tmp/secret TOKEN")
    )
    status, failed = handle_create_approved_run(service, _body(_create_payload(approval_id)))
    assert status == 202
    assert failed["status"] == "run_queued"
    assert runner.calls == 0
    assert service.process_next() is not None
    _read_status, failed = handle_read_approved_run(
        service, f"{RUNS_PATH}/{failed['run']['run_id']}?session_id={SESSION}"
    )
    encoded = json.dumps(failed)
    assert failed["status"] == "run_observed"
    assert failed["run"]["status"] == "failed"
    for leak in ("Traceback", "/tmp/secret", "TOKEN", "RuntimeError"):
        assert leak not in encoded
    run_id = failed["run"]["run_id"]
    sentence = "The fixture run failed. No scientific action was taken."
    read_status, observed = handle_read_approved_run(
        service, f"{RUNS_PATH}/{run_id}?session_id={SESSION}"
    )
    assert read_status == 200
    assert observed["text"] == sentence
    assert observed["run"]["text"] == sentence
    assert runner.calls == 1
    runner.error = None
    retry_status, retried = handle_retry_approved_run(
        service,
        run_id,
        _body({"session_id": SESSION, "idempotency_key": "retry-1"}),
    )
    assert retry_status == 202
    assert retried["status"] == "run_queued"
    assert retried["run"]["attempt"] == 2
    assert retried["run"]["run_id"] == run_id
    assert service.process_next() is not None
    _read_status, retried = handle_read_approved_run(
        service, f"{RUNS_PATH}/{run_id}?session_id={SESSION}"
    )
    assert retried["run"]["status"] == "completed"
    calls = runner.calls
    duplicate_status, duplicate = handle_retry_approved_run(
        service,
        run_id,
        _body({"session_id": SESSION, "idempotency_key": "retry-1"}),
    )
    assert duplicate_status == 200
    assert duplicate["run"]["attempt"] == 2
    assert runner.calls == calls
    blocked_status, blocked = handle_retry_approved_run(
        service,
        run_id,
        _body(
            {
                "session_id": SESSION,
                "idempotency_key": "retry-2",
                "attempt": 3,
                "status": "completed",
            }
        ),
    )
    assert blocked_status == 400
    assert blocked["status"] == "invalid_payload"
    finished_status, finished = handle_retry_approved_run(
        service,
        run_id,
        _body({"session_id": SESSION, "idempotency_key": "retry-done"}),
    )
    assert finished_status == 200
    assert finished["status"] == "retry_not_allowed"
    before = runner.calls
    handle_read_approved_run(service, f"{RUNS_PATH}/{run_id}?session_id={SESSION}")
    assert runner.calls == before


def test_handler_get_is_read_only_and_unwired_runs_stay_not_found():
    service, runner, gate, _drafter, _draft, _adapter, approval_id, _status, _done = _ready()
    _status, created = handle_create_approved_run(service, _body(_create_payload(approval_id)))
    run_id = created["run"]["run_id"]
    handler = configured_handler(object(), approvals=gate, runs=service)
    status, payload = _invoke(handler, "GET", f"{RUNS_PATH}/{run_id}?session_id={SESSION}", b"")
    assert status == 200
    assert payload["status"] == "run_observed"
    assert payload["run"]["status"] == "queued"
    assert runner.calls == 0
    post_status, post_payload = _invoke(
        handler,
        "POST",
        f"{RUNS_PATH}/{run_id}/retry",
        _body({"session_id": SESSION, "idempotency_key": "retry-handler", "command": "bash"}),
    )
    assert post_status == 400
    assert post_payload["run"] is None
    absent = configured_handler(object(), approvals=gate, runs=None)
    missing, _payload = _invoke(absent, "POST", RUNS_PATH, _body(_create_payload(approval_id)))
    assert missing == 404
    missing_get, _payload = _invoke(
        absent, "GET", f"{RUNS_PATH}/{run_id}?session_id={SESSION}", b""
    )
    assert missing_get == 404


def _invoke(handler: type, method: str, path: str, body: bytes) -> tuple[int, dict]:
    instance = handler.__new__(handler)
    instance.path = path
    instance.headers = {"Content-Length": str(len(body))}
    instance.rfile = BytesIO(body)
    instance.wfile = BytesIO()
    instance.status_code = None

    def send_response(code: int) -> None:
        instance.status_code = code

    instance.send_response = send_response
    instance.send_header = lambda *_args: None
    instance.end_headers = lambda: None
    if method == "GET":
        instance.do_GET()
    else:
        instance.do_POST()
    raw = instance.wfile.getvalue()
    payload = json.loads(raw) if raw else {}
    return instance.status_code, payload
