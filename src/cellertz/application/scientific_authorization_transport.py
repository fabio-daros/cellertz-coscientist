"""Scientific-run authorization edge. Permission only.

POST /api/scientific-run-authorizations records or replays one permission.
GET /api/scientific-run-authorizations lists the server session.
GET /api/scientific-run-authorizations/{authorization_id} reads one record.
POST /api/scientific-run-authorizations/{authorization_id}/revoke revokes it.

There is no consume route and no execution route. The browser cannot
choose a session, a store, or a database path.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from cellertz.application.scientific_authorization import (
    AUTHORIZED_TEXT,
    CONSUMED_TEXT,
    DOCKER_PROFILE_ID,
    PDL1_PROFILE_ID,
    REVOKED_TEXT,
    SINGLE_USE_TEXT,
    VALIDATION_TEXT,
    ScientificAuthorizationListReply,
    ScientificAuthorizationReply,
    ScientificAuthorizationService,
    _safe_text,
    adapter_failure_reply,
    budget_document,
)

AUTHORIZATIONS_PATH = "/api/scientific-run-authorizations"
_AUTHORIZATION_ID = re.compile(r"authorization_[0-9a-f]{32}")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_CREATE_KEYS = frozenset({"approval_id", "idempotency_key"})
_REVOKE_KEYS = frozenset({"idempotency_key"})
_FORBIDDEN = frozenset(
    {
        "argv",
        "authorization_status",
        "budget",
        "command",
        "digest",
        "environment",
        "execution_authorized",
        "image_digest",
        "manifest_digest",
        "path",
        "profile_id",
        "readiness_status",
        "request_digest",
        "request_revision",
        "resource_limits",
        "revision",
        "runtime_name",
        "runtime_version",
        "scientific_execution",
        "scientific_validation",
        "session_id",
        "status",
        "store",
        "target_scope",
        "tool_name",
        "tool_version",
    }
)
_CONFLICTS = frozenset(
    {
        "approval_absent",
        "approval_not_approved",
        "authorization_rejected",
        "budget_invalid",
        "demonstration_absent",
        "digest_mismatch",
        "fixture_run_required",
        "idempotency_conflict",
        "profile_not_allowlisted",
        "readiness_invalid",
        "readiness_not_ready",
        "request_incomplete",
        "revision_conflict",
        "session_absent",
        "target_blocked",
    }
)
_REPLY_FIELDS = frozenset(
    {
        "authorization",
        "execution_authorized",
        "scientific_candidate_generated",
        "scientific_execution",
        "scientific_validation",
        "session_id",
        "status",
        "text",
    }
)
_LIST_FIELDS = frozenset(
    {
        "authorizations",
        "execution_authorized",
        "scientific_candidate_generated",
        "scientific_execution",
        "scientific_validation",
        "session_id",
        "status",
        "text",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "approval_id",
        "authorization_id",
        "budget",
        "created_at",
        "events",
        "execution_authorized",
        "image_digest",
        "manifest_digest",
        "profile_id",
        "readiness_observation_id",
        "readiness_observed_at",
        "request_digest",
        "request_id",
        "request_revision",
        "runtime_name",
        "runtime_version",
        "scientific_candidate_generated",
        "scientific_execution",
        "scientific_validation",
        "session_id",
        "single_use",
        "status",
        "target_scope",
        "text",
        "tool_name",
        "tool_version",
        "updated_at",
        "validation_text",
    }
)


def handle_scientific_authorization(
    service: ScientificAuthorizationService,
    method: str,
    path: str,
    body: bytes,
    session_id: str,
) -> tuple[int, dict[str, Any]]:
    split = urlsplit(path)
    if split.query or split.fragment:
        return 400, _invalid(session_id)
    route = split.path
    if method == "GET" and route == AUTHORIZATIONS_PATH:
        return _list(session_id, lambda: service.list_authorizations(session_id=session_id))
    if method == "GET" and route.startswith(f"{AUTHORIZATIONS_PATH}/"):
        authorization_id = route.removeprefix(f"{AUTHORIZATIONS_PATH}/")
        if _AUTHORIZATION_ID.fullmatch(authorization_id) is None:
            return 404, _invalid(session_id)
        return _one(
            session_id,
            lambda: service.read(session_id=session_id, authorization_id=authorization_id),
        )
    if method == "POST" and route == AUTHORIZATIONS_PATH:
        payload = _object(body, _CREATE_KEYS)
        if payload is None:
            return 400, _invalid(session_id)
        fields = _exact_strings(payload, ("approval_id", "idempotency_key"))
        if fields is None:
            return 400, _invalid(session_id)
        return _one(
            session_id,
            lambda: service.authorize(
                session_id=session_id,
                approval_id=fields["approval_id"],
                idempotency_key=fields["idempotency_key"],
            ),
            created=True,
        )
    if method == "POST" and route.endswith("/revoke"):
        authorization_id = route.removeprefix(f"{AUTHORIZATIONS_PATH}/").removesuffix("/revoke")
        if _AUTHORIZATION_ID.fullmatch(authorization_id) is None or "/" in authorization_id:
            return 404, _invalid(session_id)
        payload = _object(body, _REVOKE_KEYS)
        if payload is None:
            return 400, _invalid(session_id)
        fields = _exact_strings(payload, ("idempotency_key",))
        if fields is None:
            return 400, _invalid(session_id)
        return _one(
            session_id,
            lambda: service.revoke(
                session_id=session_id,
                authorization_id=authorization_id,
                idempotency_key=fields["idempotency_key"],
            ),
        )
    return 404, _invalid(session_id)


def _one(session_id: str, action: Any, *, created: bool = False) -> tuple[int, dict[str, Any]]:
    try:
        reply = action()
    except Exception:  # noqa: BLE001 — safe body, no traceback
        reply = adapter_failure_reply(session_id)
    if type(reply) is not ScientificAuthorizationReply or reply.session_id != session_id:
        reply = adapter_failure_reply(session_id)
    try:
        payload = ScientificAuthorizationReply.to_dict(reply)
    except Exception:  # noqa: BLE001 — do not serialize an untrusted override
        payload = ScientificAuthorizationReply.to_dict(adapter_failure_reply(session_id))
    if not _reply_ok(payload, session_id):
        payload = ScientificAuthorizationReply.to_dict(adapter_failure_reply(session_id))
    return _status(payload["status"], created=created), payload


def _list(session_id: str, action: Any) -> tuple[int, dict[str, Any]]:
    try:
        reply = action()
    except Exception:  # noqa: BLE001 — safe body, no traceback
        reply = ScientificAuthorizationListReply("adapter_failure", _adapter_text(), session_id)
    if type(reply) is not ScientificAuthorizationListReply or reply.session_id != session_id:
        reply = ScientificAuthorizationListReply("adapter_failure", _adapter_text(), session_id)
    try:
        payload = ScientificAuthorizationListReply.to_dict(reply)
    except Exception:  # noqa: BLE001
        payload = ScientificAuthorizationListReply.to_dict(
            ScientificAuthorizationListReply("adapter_failure", _adapter_text(), session_id)
        )
    if not _list_ok(payload, session_id):
        payload = ScientificAuthorizationListReply.to_dict(
            ScientificAuthorizationListReply("adapter_failure", _adapter_text(), session_id)
        )
    return _status(payload["status"]), payload


def _adapter_text() -> str:
    return adapter_failure_reply("unknown").text


def _status(status: str, *, created: bool = False) -> int:
    if status == "invalid_payload":
        return 400
    if status == "authorization_absent":
        return 404
    if status in _CONFLICTS or status == "authorization_record_invalid":
        return 409
    if created and status == "authorization_created":
        return 201
    return 200


def _invalid(session_id: str) -> dict[str, Any]:
    safe = session_id if type(session_id) is str and _TOKEN.fullmatch(session_id) else "unknown"
    return ScientificAuthorizationReply.to_dict(
        ScientificAuthorizationReply(
            "invalid_payload",
            "The authorization payload was rejected. No scientific action was taken.",
            safe,
        )
    )


def _object(body: bytes, allowed: frozenset[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if type(payload) is not dict or set(payload) != allowed or set(payload) & _FORBIDDEN:
        return None
    return payload


def _exact_strings(payload: dict[str, Any], names: tuple[str, ...]) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for name in names:
        value = payload.get(name)
        if type(value) is not str:
            return None
        values[name] = value
    return values


def _reply_ok(payload: dict[str, Any], session_id: str) -> bool:
    if set(payload) != _REPLY_FIELDS or payload["session_id"] != session_id:
        return False
    if type(payload["status"]) is not str or not _safe_text(payload["text"]):
        return False
    if (
        payload["scientific_execution"] is not False
        or payload["scientific_validation"] is not False
    ):
        return False
    if payload["scientific_candidate_generated"] is not False:
        return False
    if type(payload["execution_authorized"]) is not bool:
        return False
    authorization = payload["authorization"]
    if authorization is None:
        return payload["execution_authorized"] is False
    if not _record_ok(authorization, session_id):
        return False
    return payload["execution_authorized"] is authorization["execution_authorized"]


def _list_ok(payload: dict[str, Any], session_id: str) -> bool:
    if set(payload) != _LIST_FIELDS or payload["session_id"] != session_id:
        return False
    if not _safe_text(payload["text"]):
        return False
    if payload["execution_authorized"] is not False or payload["scientific_execution"] is not False:
        return False
    if (
        payload["scientific_validation"] is not False
        or payload["scientific_candidate_generated"] is not False
    ):
        return False
    rows = payload["authorizations"]
    if type(rows) is not list or len(rows) > 50:
        return False
    return all(_record_ok(row, session_id) for row in rows)


def _record_ok(payload: object, session_id: str) -> bool:
    if type(payload) is not dict or set(payload) != _RECORD_FIELDS:
        return False
    if payload["session_id"] != session_id or payload["status"] not in {"authorized", "revoked"}:
        return False
    if payload["single_use"] != SINGLE_USE_TEXT or payload["validation_text"] != VALIDATION_TEXT:
        return False
    if payload["status"] == "revoked":
        expected = REVOKED_TEXT
    elif payload["execution_authorized"] is True:
        expected = AUTHORIZED_TEXT
    else:
        expected = CONSUMED_TEXT
    if payload["text"] != expected:
        return False
    if (
        payload["scientific_execution"] is not False
        or payload["scientific_validation"] is not False
    ):
        return False
    if payload["scientific_candidate_generated"] is not False:
        return False
    if type(payload["execution_authorized"]) is not bool:
        return False
    if payload["execution_authorized"] is True and payload["status"] != "authorized":
        return False
    if payload["status"] == "revoked" and payload["execution_authorized"] is not False:
        return False
    if (
        payload["profile_id"] not in {PDL1_PROFILE_ID, DOCKER_PROFILE_ID}
        or payload["tool_name"] != "bindcraft"
    ):
        return False
    if payload["target_scope"] != "PDL1" or not budget_document(payload["budget"]):
        return False
    if not _safe_text(payload["text"]) or not _safe_text(payload["single_use"]):
        return False
    if not _safe_text(payload["validation_text"]):
        return False
    events = payload["events"]
    if type(events) is not list or not events:
        return False
    return all(
        type(event) is dict and event.get("origin") == "server" and _safe_text(event.get("text"))
        for event in events
    )
