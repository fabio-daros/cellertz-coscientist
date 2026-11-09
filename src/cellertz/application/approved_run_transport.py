"""JSON edges for a fixture-only approved run.

POST /api/approved-runs creates or replays one logical run.
GET /api/approved-runs lists the server session and does not execute.
GET /api/approved-runs/{run_id} reads one run and does not execute.
GET /api/approved-runs/{run_id}/artifacts/{artifact_id} reads one artifact.
POST /api/approved-runs/{run_id}/retry retries a failed run.

The client cannot send a digest, revision, budget, authorization flag,
status, transition, attempt, command, path, artifact, evidence,
candidate, usage, cost, or executor. History and artifact routes do not
accept a session selector.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from cellertz.application.approved_run import (
    ApprovedArtifactRecord,
    ApprovedRunListReply,
    ApprovedRunReply,
    ApprovedRunService,
)

RUNS_PATH = "/api/approved-runs"
_RUN_ID = re.compile(r"run_[0-9a-f]{32}")
_ARTIFACT_ID = re.compile(
    r"artifact_run_[0-9a-f]{32}_[1-9][0-9]{0,8}_"
    r"(?:fixture_receipt|fixture_manifest|fixture_summary|input_digest|output_digest|operational_log)"
)
_FILENAME = re.compile(r"fixture-[a-z_]+\.(?:json|txt|md)")
_CREATE_KEYS = frozenset({"session_id", "approval_id", "idempotency_key"})
_RETRY_KEYS = frozenset({"session_id", "idempotency_key"})
_FORBIDDEN_KEYS = frozenset(
    {
        "digest",
        "revision",
        "request_revision",
        "budget",
        "execution_authorized",
        "executable",
        "authorization",
        "preflight",
        "preflight_status",
        "status",
        "transition",
        "attempt",
        "command",
        "argv",
        "shell",
        "path",
        "artifact",
        "artifacts",
        "evidence",
        "candidate",
        "candidates",
        "usage",
        "cost",
        "executor",
        "tool",
        "tool_name",
        "generator",
    }
)
_CONFLICTS = frozenset({"revision_conflict", "idempotency_conflict", "digest_mismatch"})
_ARTIFACT_CONFLICTS = frozenset(
    {
        "artifact_invalid",
        "artifact_digest_mismatch",
        "artifact_size_mismatch",
        "artifact_media_type_rejected",
        "artifact_too_large",
        "artifact_immutable",
    }
)
_MISSING = frozenset({"run_absent", "artifact_absent", "artifact_attempt_unavailable"})
_SAFE_TEXT = "The payload was rejected. No scientific action was taken."
_ADAPTER_TEXT = "The fixture run adapter failed. No scientific action was taken."


def handle_create_approved_run(
    service: ApprovedRunService, body: bytes
) -> tuple[int, dict[str, Any]]:
    payload, session_id = _object(body, _CREATE_KEYS)
    if payload is None:
        return 400, _invalid(session_id)
    fields = _exact_strings(payload, ("session_id", "approval_id", "idempotency_key"))
    if fields is None:
        return 400, _invalid(session_id)
    return _call(
        session_id,
        lambda: service.create(
            session_id=fields["session_id"],
            approval_id=fields["approval_id"],
            idempotency_key=fields["idempotency_key"],
        ),
    )


def handle_retry_approved_run(
    service: ApprovedRunService,
    run_id: str,
    body: bytes,
) -> tuple[int, dict[str, Any]]:
    if not _run_id(run_id):
        return 400, _invalid("")
    payload, session_id = _object(body, _RETRY_KEYS)
    if payload is None:
        return 400, _invalid(session_id)
    fields = _exact_strings(payload, ("session_id", "idempotency_key"))
    if fields is None:
        return 400, _invalid(session_id)
    return _call(
        session_id,
        lambda: service.retry(
            session_id=fields["session_id"],
            run_id=run_id,
            idempotency_key=fields["idempotency_key"],
        ),
    )


@dataclass(frozen=True)
class ArtifactResponse:
    status: int
    body: bytes
    media_type: str
    filename: str


def handle_list_approved_runs(
    service: ApprovedRunService, session_id: str, path: str
) -> tuple[int, dict[str, Any]]:
    split = urlsplit(path)
    if split.path != RUNS_PATH or split.query:
        return 400, _invalid(session_id)
    return _list(session_id, lambda: service.list_runs(session_id=session_id))


def handle_read_artifact(
    service: ApprovedRunService, session_id: str, run_id: str, artifact_id: str
) -> ArtifactResponse | tuple[int, dict[str, Any]]:
    if not _run_id(run_id) or not _artifact_id(artifact_id):
        return 400, _invalid(session_id)
    try:
        record = service.read_artifact(
            session_id=session_id, run_id=run_id, artifact_id=artifact_id
        )
    except Exception:  # noqa: BLE001 — safe body, no traceback
        record = ApprovedRunReply("adapter_failure", _ADAPTER_TEXT, session_id or "unknown")
    if isinstance(record, ApprovedArtifactRecord):
        if record.media_type not in {"application/json", "text/plain", "text/markdown"}:
            return 409, _invalid(session_id)
        if _FILENAME.fullmatch(record.filename) is None:
            return 409, _invalid(session_id)
        return ArtifactResponse(200, record.content, record.media_type, record.filename)
    if not isinstance(record, ApprovedRunReply):
        record = ApprovedRunReply("adapter_failure", _ADAPTER_TEXT, session_id or "unknown")
    code = 404 if record.status in _MISSING else _status(record.status)
    return code, record.to_dict()


def handle_read_approved_run(service: ApprovedRunService, path: str) -> tuple[int, dict[str, Any]]:
    split = urlsplit(path)
    run_id = split.path.removeprefix(RUNS_PATH).strip("/")
    if not _run_id(run_id):
        return 400, _invalid("")
    query = parse_qs(split.query, keep_blank_values=True)
    if set(query) != {"session_id"}:
        return 400, _invalid("")
    values = query.get("session_id", [])
    if len(values) != 1 or type(values[0]) is not str:
        return 400, _invalid("")
    session_id = values[0]
    return _call(session_id, lambda: service.read(session_id=session_id, run_id=run_id))


def _artifact_id(value: str) -> bool:
    return _ARTIFACT_ID.fullmatch(value) is not None


def _list(session_id: str, action: Any) -> tuple[int, dict[str, Any]]:
    try:
        reply = action()
    except Exception:  # noqa: BLE001 — safe body, no traceback
        reply = ApprovedRunListReply("adapter_failure", _ADAPTER_TEXT, session_id or "unknown")
    if not isinstance(reply, ApprovedRunListReply):
        reply = ApprovedRunListReply("adapter_failure", _ADAPTER_TEXT, session_id or "unknown")
    return _status(reply.status), reply.to_dict()


def _call(session_id: str, action: Any) -> tuple[int, dict[str, Any]]:
    try:
        reply = action()
    except Exception:  # noqa: BLE001 — safe body, no traceback
        reply = ApprovedRunReply("adapter_failure", _ADAPTER_TEXT, session_id or "unknown")
    if not isinstance(reply, ApprovedRunReply):
        reply = ApprovedRunReply("adapter_failure", _ADAPTER_TEXT, session_id or "unknown")
    return _status(reply.status), reply.to_dict()


def _run_id(value: str) -> bool:
    return _RUN_ID.fullmatch(value) is not None


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
    safe_session = session_id if type(session_id) is str else ""
    if set(payload) - allowed or set(payload) & _FORBIDDEN_KEYS:
        return None, safe_session
    return payload, safe_session


def _status(status: str) -> int:
    if status == "invalid_payload":
        return 400
    if status in _CONFLICTS or status in _ARTIFACT_CONFLICTS:
        return 409
    if status == "run_queued":
        return 202
    return 200


def _invalid(session_id: str) -> dict[str, Any]:
    return ApprovedRunReply("invalid_payload", _SAFE_TEXT, session_id or "unknown").to_dict()
