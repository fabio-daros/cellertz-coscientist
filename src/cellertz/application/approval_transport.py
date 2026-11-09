"""JSON edges for a human decision and a fixture-only demonstration.

POST /api/design-approval records a decision. It never dispatches.
POST /api/approved-execution authorizes, preflights, and dispatches
only the phase6d_fixture_only adapter.

The client cannot send authorization flags, a trusted digest, a
preflight result, an execution status, a command, argv, a shell
string, or a filesystem path.
"""

from __future__ import annotations

import json
from typing import Any

from cellertz.application.approval import FIXTURE_PROFILE, GateReply, HumanApprovalGate

APPROVAL_PATH = "/api/design-approval"
EXECUTION_PATH = "/api/approved-execution"
_DECISION_KEYS = frozenset(
    {
        "session_id",
        "request_id",
        "revision",
        "decision",
        "reviewer_id",
        "reason",
        "idempotency_key",
    }
)
_EXECUTION_KEYS = frozenset(
    {
        "session_id",
        "approval_id",
        "idempotency_key",
        "execution_profile",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "execution_authorized",
        "executable",
        "digest",
        "revision_digest",
        "preflight",
        "preflight_status",
        "status",
        "command",
        "argv",
        "shell",
        "path",
        "tool",
        "tool_name",
        "candidates",
        "human_override",
    }
)
_CONFLICTS = frozenset({"revision_conflict", "idempotency_conflict", "digest_mismatch"})
_SAFE_TEXT = "The payload was rejected. No scientific action was taken."
_ADAPTER_TEXT = "The demonstration adapter failed. No scientific action was taken."


def handle_design_approval(gate: HumanApprovalGate, body: bytes) -> tuple[int, dict[str, Any]]:
    payload, session_id = _object(body, _DECISION_KEYS)
    if payload is None:
        return 400, _invalid(session_id)
    fields = _exact_strings(
        payload,
        ("session_id", "request_id", "decision", "reviewer_id", "reason", "idempotency_key"),
    )
    revision = payload.get("revision")
    if fields is None or type(revision) is not int:
        return 400, _invalid(session_id)
    try:
        reply = gate.decide(
            session_id=fields["session_id"],
            request_id=fields["request_id"],
            revision=revision,
            decision=fields["decision"],
            reviewer_id=fields["reviewer_id"],
            reason=fields["reason"],
            idempotency_key=fields["idempotency_key"],
        )
    except Exception:  # noqa: BLE001 — safe body, no traceback
        reply = GateReply("adapter_failure", _ADAPTER_TEXT, session_id or "unknown")
    return _status(reply.status), reply.to_dict()


def handle_approved_execution(gate: HumanApprovalGate, body: bytes) -> tuple[int, dict[str, Any]]:
    payload, session_id = _object(body, _EXECUTION_KEYS)
    if payload is None:
        return 400, _invalid(session_id)
    fields = _exact_strings(payload, ("session_id", "approval_id", "idempotency_key"))
    if "execution_profile" in payload:
        profile = payload.get("execution_profile")
    else:
        profile = FIXTURE_PROFILE
    if fields is None or type(profile) is not str:
        return 400, _invalid(session_id)
    try:
        reply = gate.dispatch(
            session_id=fields["session_id"],
            approval_id=fields["approval_id"],
            idempotency_key=fields["idempotency_key"],
            execution_profile=profile,
        )
    except Exception:  # noqa: BLE001 — safe body, no traceback
        reply = GateReply("adapter_failure", _ADAPTER_TEXT, session_id or "unknown")
    return _status(reply.status), reply.to_dict()


def _exact_strings(payload: dict[str, Any], names: tuple[str, ...]) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for name in names:
        value = payload.get(name)
        if type(value) is not str:
            return None
        values[name] = value
    return values


def _object(body: bytes, allowed: frozenset[str]) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, ""
    if not isinstance(payload, dict):
        return None, ""
    session_id = payload.get("session_id")
    safe_session = session_id if isinstance(session_id, str) else ""
    if set(payload) - allowed:
        return None, safe_session
    return payload, safe_session


def _status(status: str) -> int:
    if status == "invalid_payload":
        return 400
    if status in _CONFLICTS:
        return 409
    return 200


def _invalid(session_id: str) -> dict[str, Any]:
    return GateReply("invalid_payload", _SAFE_TEXT, session_id or "unknown").to_dict()
