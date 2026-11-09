"""Phase 6D HTTP edges. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cellertz.application.approval import HumanApprovalGate
from cellertz.application.approval_transport import (
    handle_approved_execution,
    handle_design_approval,
)
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DesignDrafting,
    DeterministicDraftExtractor,
)

WHEN = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
SESSION = "session_pdl1_prototype"


class RecordingDispatcher:
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls = 0
        self.error = error

    def dispatch(self, receipt):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return receipt


def _gate(message: str = "design a binder for pdl1 using fixture with 1 candidates", dispatcher=None):
    session = pdl1_prototype_session()
    ids = {"request": 0}

    def request_ids() -> str:
        ids["request"] += 1
        return f"request_{ids['request']}"

    drafter = DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        clock=lambda: WHEN,
        request_ids=request_ids,
        interaction_ids=lambda: "interaction_1",
    )
    adapter = dispatcher if dispatcher is not None else RecordingDispatcher()
    gate = HumanApprovalGate(
        {session.session_id: session},
        drafter,
        dispatcher=adapter,
        clock=lambda: WHEN,
        approval_ids=lambda: f"approval_{ids['request']}",
        dispatch_ids=lambda: "dispatch_1",
    )
    draft = drafter.revise(DesignDraftCommand(SESSION, message))
    return gate, adapter, draft, drafter


def _body(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _decision(draft, **overrides) -> dict:
    payload = {
        "session_id": SESSION,
        "request_id": draft.draft.request_id,
        "revision": draft.draft.revision,
        "decision": "approved",
        "reviewer_id": "operator_declared",
        "reason": "Fixture demonstration only.",
        "idempotency_key": "decision-1",
    }
    payload.update(overrides)
    return payload


def test_valid_approval_does_not_dispatch():
    gate, adapter, draft, _drafter = _gate()
    status, body = handle_design_approval(gate, _body(_decision(draft)))
    assert status == 200
    assert body["status"] == "decision_recorded"
    assert body["approval"]["decision"] == "approved"
    assert body["execution_authorized"] is False
    assert body["dispatch_status"] == "not_dispatched"
    assert adapter.calls == 0


_BAD_IDENTITY = [None, 123, True, ["operator"], {"id": "operator"}]
_TEXT_FIELDS = (
    "session_id",
    "request_id",
    "decision",
    "reviewer_id",
    "reason",
    "idempotency_key",
)
_EXECUTION_TEXT_FIELDS = ("session_id", "approval_id", "idempotency_key")


@pytest.mark.parametrize("field", _TEXT_FIELDS)
@pytest.mark.parametrize("value", _BAD_IDENTITY)
def test_decision_rejects_non_text_identity(field: str, value: object):
    gate, adapter, draft, _drafter = _gate()
    status, body = handle_design_approval(gate, _body(_decision(draft, **{field: value})))
    assert status == 400
    assert body["status"] == "invalid_payload"
    assert body["approval"] is None
    assert adapter.calls == 0
    encoded = json.dumps(body)
    assert "None" not in encoded
    assert "True" not in encoded


@pytest.mark.parametrize("value", [True, False])
def test_decision_rejects_bool_revision(value: bool):
    gate, adapter, draft, _drafter = _gate()
    status, body = handle_design_approval(gate, _body(_decision(draft, revision=value)))
    assert status == 400
    assert body["status"] == "invalid_payload"
    assert body["approval"] is None
    assert adapter.calls == 0


@pytest.mark.parametrize("field", _EXECUTION_TEXT_FIELDS)
@pytest.mark.parametrize("value", _BAD_IDENTITY)
def test_execution_rejects_non_text_identity(field: str, value: object):
    gate, adapter, draft, _drafter = _gate()
    _status, approved = handle_design_approval(gate, _body(_decision(draft)))
    payload = {
        "session_id": SESSION,
        "approval_id": approved["approval"]["approval_id"],
        "idempotency_key": "dispatch-types",
        field: value,
    }
    status, body = handle_approved_execution(gate, _body(payload))
    assert status == 400
    assert body["status"] == "invalid_payload"
    assert body["demonstration"] is None
    assert adapter.calls == 0


def test_invalid_utf8_is_a_safe_invalid_payload():
    gate, adapter, _draft, _drafter = _gate()
    for handler in (handle_design_approval, handle_approved_execution):
        status, body = handler(gate, b"\xff")
        encoded = json.dumps(body)
        assert status == 400
        assert body["status"] == "invalid_payload"
        assert body["approval"] is None
        assert body["demonstration"] is None
        assert "Traceback" not in encoded
        assert "UnicodeDecodeError" not in encoded
    assert adapter.calls == 0


def test_invalid_payload_and_forbidden_command_are_rejected():
    gate, adapter, draft, _drafter = _gate()
    status, body = handle_design_approval(gate, b"{")
    assert status == 400
    assert body["status"] == "invalid_payload"
    assert "Traceback" not in json.dumps(body)
    forbidden, _blocked = handle_design_approval(
        gate,
        _body({**_decision(draft), "command": "bash -lc echo"}),
    )
    assert forbidden == 400
    assert adapter.calls == 0


def test_missing_request_revision_conflict_and_incomplete_request():
    gate, _adapter, draft, _drafter = _gate()
    missing, body = handle_design_approval(
        gate,
        _body(_decision(draft, request_id="request_missing")),
    )
    assert missing == 200
    assert body["status"] == "request_absent"
    conflict, conflict_body = handle_design_approval(
        gate,
        _body(_decision(draft, revision=9, idempotency_key="decision-old")),
    )
    assert conflict == 409
    assert conflict_body["status"] == "revision_conflict"
    incomplete_gate, _adapter, incomplete, _drafter = _gate("design a binder")
    status, incomplete_body = handle_design_approval(
        incomplete_gate,
        _body(_decision(incomplete, idempotency_key="decision-incomplete")),
    )
    assert status == 200
    assert incomplete_body["status"] == "request_incomplete"
    assert incomplete_body["approval"] is None
    for decision in ("rejected", "changes_requested", "held"):
        negative_gate, negative_adapter, negative, _negative_drafter = _gate("design a binder")
        recorded_status, recorded = handle_design_approval(
            negative_gate,
            _body(_decision(negative, decision=decision, idempotency_key=f"decision-{decision}")),
        )
        assert recorded_status == 200
        assert recorded["status"] == "decision_recorded"
        assert recorded["approval"]["decision"] == decision
        assert recorded["authorization_status"] == "authorization_denied"
        denied, denied_body = handle_approved_execution(
            negative_gate,
            _body(
                {
                    "session_id": SESSION,
                    "approval_id": recorded["approval"]["approval_id"],
                    "idempotency_key": f"dispatch-{decision}",
                }
            ),
        )
        assert denied == 200
        assert denied_body["status"] == "authorization_denied"
        assert denied_body["demonstration"] is None
        assert negative_adapter.calls == 0


def test_rejected_decision_and_execution_without_approval():
    gate, adapter, draft, _drafter = _gate()
    status, body = handle_design_approval(gate, _body(_decision(draft, decision="rejected")))
    assert status == 200
    assert body["authorization_status"] == "authorization_denied"
    absent, absent_body = handle_approved_execution(
        gate,
        _body(
            {
                "session_id": SESSION,
                "approval_id": "approval_missing",
                "idempotency_key": "dispatch-missing",
            }
        ),
    )
    assert absent == 200
    assert absent_body["status"] == "approval_absent"
    assert adapter.calls == 0


def test_blocked_profile_invalid_budget_idempotency_and_adapter_failure():
    gate, adapter, draft, drafter = _gate()
    _status, approved = handle_design_approval(gate, _body(_decision(draft)))
    blocked, blocked_body = handle_approved_execution(
        gate,
        _body(
            {
                "session_id": SESSION,
                "approval_id": approved["approval"]["approval_id"],
                "idempotency_key": "dispatch-mber",
                "execution_profile": "mber",
            }
        ),
    )
    assert blocked == 200
    assert blocked_body["status"] == "profile_not_allowlisted"
    assert adapter.calls == 0
    bare_session_gate, _bare_adapter, bare_draft, _bare_drafter = _gate()
    bare_session_gate._sessions = {SESSION: object()}
    budget_status, budget_body = handle_design_approval(
        bare_session_gate,
        _body(_decision(bare_draft, idempotency_key="decision-budget")),
    )
    assert budget_status == 200
    assert budget_body["status"] == "budget_invalid"
    first, first_body = handle_approved_execution(
        gate,
        _body(
            {
                "session_id": SESSION,
                "approval_id": approved["approval"]["approval_id"],
                "idempotency_key": "dispatch-1",
            }
        ),
    )
    assert first == 200
    assert first_body["status"] == "demonstration_completed"
    assert adapter.calls == 1
    assert first_body["demonstration"]["scientific_execution"] is False
    second = drafter.revise(
        DesignDraftCommand(SESSION, "design a binder for pdl1 using fixture with 1 candidates")
    )
    _second_status, second_body = handle_design_approval(
        gate,
        _body(_decision(second, idempotency_key="decision-2")),
    )
    clash, clash_body = handle_approved_execution(
        gate,
        _body(
            {
                "session_id": SESSION,
                "approval_id": second_body["approval"]["approval_id"],
                "idempotency_key": "dispatch-1",
            }
        ),
    )
    assert clash == 409
    assert clash_body["status"] == "idempotency_conflict"
    assert "Traceback" not in json.dumps(clash_body)
    crashing = RecordingDispatcher(RuntimeError("Traceback /tmp/secret"))
    broken, _broken_adapter, broken_draft, _broken_drafter = _gate(dispatcher=crashing)
    _ok, broken_approval = handle_design_approval(
        broken,
        _body(_decision(broken_draft, idempotency_key="decision-broken")),
    )
    failed, failed_body = handle_approved_execution(
        broken,
        _body(
            {
                "session_id": SESSION,
                "approval_id": broken_approval["approval"]["approval_id"],
                "idempotency_key": "dispatch-broken",
            }
        ),
    )
    encoded = json.dumps(failed_body)
    assert failed == 200
    assert failed_body["status"] == "adapter_failure"
    assert "Traceback" not in encoded
    assert "/tmp/secret" not in encoded
    assert crashing.calls == 1
