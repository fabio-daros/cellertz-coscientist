"""Read-only scientific-runner readiness edge.

GET /api/scientific-runner-readiness returns the startup snapshot.
It does not probe, enqueue, retry, recover, or execute.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from cellertz.application.scientific_runner_readiness import (
    ReadinessReply,
    adapter_failure_reply,
    readiness_reply_is_public,
)

READINESS_PATH = "/api/scientific-runner-readiness"
_REJECTED = "The readiness query was rejected. No scientific action was taken."


def handle_read_scientific_runner_readiness(
    service: Any, path: str, session_id: str
) -> tuple[int, dict[str, Any]]:
    split = urlsplit(path)
    if split.query or split.fragment or split.path != READINESS_PATH:
        return 400, _rejected(session_id)
    try:
        reply = service.read()
        if (
            type(reply) is not ReadinessReply
            or reply.session_id != session_id
            or not readiness_reply_is_public(reply)
        ):
            payload = _adapter_failure(session_id)
        else:
            payload = ReadinessReply.to_dict(reply)
            if not _public_ok(payload, session_id):
                payload = _adapter_failure(session_id)
    except Exception:  # noqa: BLE001 — safe body, no traceback
        payload = _adapter_failure(session_id)
    return 200, payload


def _adapter_failure(session_id: str) -> dict[str, Any]:
    return ReadinessReply.to_dict(adapter_failure_reply(session_id, _stamp()))


def _rejected(session_id: str) -> dict[str, Any]:
    return {
        "status": "invalid_payload",
        "text": _REJECTED,
        "session_id": session_id,
        "execution_authorized": False,
        "scientific_execution": False,
        "scientific_validation": False,
    }


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _public_ok(payload: dict[str, Any], session_id: str) -> bool:
    if set(payload) != set(ReadinessReply.public_fields()):
        return False
    if payload["session_id"] != session_id or type(payload["session_id"]) is not str:
        return False
    if payload["status"] not in {
        "disabled",
        "blocked",
        "ready",
        "configuration_failure",
        "adapter_failure",
    }:
        return False
    for name in ("execution_authorized", "scientific_execution", "scientific_validation"):
        if payload[name] is not False:
            return False
    if type(payload["text"]) is not str or type(payload["observed_at"]) is not str:
        return False
    checks = payload["checks"]
    if type(checks) is not list:
        return False
    for check in checks:
        if type(check) is not dict or set(check) != {"code", "status", "text"}:
            return False
        if type(check["code"]) is not str or type(check["text"]) is not str:
            return False
        if check["status"] not in {"passed", "blocked"}:
            return False
    return True
