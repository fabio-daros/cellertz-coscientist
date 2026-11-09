"""Scientific-run execution edge.

POST /api/scientific-runs consumes one authorization and queues one run.
GET /api/scientific-runs lists the server session and does not execute.
GET /api/scientific-runs/{run_id} reads one run and does not execute.
POST /api/scientific-runs/{run_id}/retry retries a failed run.
GET /api/scientific-runs/{run_id}/artifacts/{artifact_id} reads one artifact.

The browser sends only an authorization id and an idempotency key on
create. It cannot choose a command, runtime, image, target, or path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from cellertz.application.scientific_run import (
    _ABSENT_TEXT,
    _ADAPTER_TEXT,
    _ARTIFACT_ABSENT,
    _HISTORY_TEXT,
    _KEY_TEXT,
    _REJECTED_TEXT,
    _RETRY_TEXT,
    _UNAVAILABLE_TEXT,
    _UNUSABLE_TEXT,
    COMPLETED_TEXT,
    DISABLED_TEXT,
    EXTERNAL_FAILED_TEXT,
    EXTERNAL_UNCERTAIN_TEXT,
    FAILED_TEXT,
    INTERRUPTED_TEXT,
    QUEUED_TEXT,
    RETRY_QUEUED_TEXT,
    RUNNING_TEXT,
    ScientificArtifactRecord,
    ScientificRunListReply,
    ScientificRunReply,
    ScientificRunService,
    _safe_text,
    public_run_ok,
)

RUNS_PATH = "/api/scientific-runs"
_RUN_ID = re.compile(r"run_[0-9a-f]{32}")
_AUTHORIZATION_ID = re.compile(r"^authorization_[0-9a-f]{32}$")
_ARTIFACT_ID = re.compile(
    r"artifact_run_[0-9a-f]{32}_[1-9][0-9]*_(?:execution_record|execution_note|candidate_record|workflow_trace)"
)
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_FILENAME = re.compile(
    r"scientific-(?:execution-record\.json|candidate-record\.json|agent-workflow\.json|execution-note\.(?:txt|md))"
)
_FILENAME_MEDIA = {
    "scientific-execution-record.json": "application/json",
    "scientific-candidate-record.json": "application/json",
    "scientific-agent-workflow.json": "application/json",
    "scientific-execution-note.txt": "text/plain",
    "scientific-execution-note.md": "text/markdown",
}
_CREATE_KEYS = frozenset({"authorization_id", "idempotency_key"})
_RETRY_KEYS = frozenset({"idempotency_key"})
_FORBIDDEN = frozenset(
    {
        "accepted",
        "argv",
        "attempt",
        "backend",
        "budget",
        "command",
        "environment",
        "executable",
        "executor",
        "image",
        "image_digest",
        "manifest_digest",
        "path",
        "profile_id",
        "ranked",
        "request_revision",
        "resource_envelope",
        "runtime",
        "runtime_name",
        "session_id",
        "shell",
        "status",
        "target",
        "target_scope",
        "tool_name",
        "working_directory",
    }
)
_CONFLICTS = frozenset(
    {
        "authorization_unavailable",
        "idempotency_conflict",
        "retry_not_allowed",
        "run_record_invalid",
        "artifact_digest_mismatch",
        "artifact_invalid",
        "artifact_media_type_rejected",
        "artifact_size_mismatch",
        "artifact_too_large",
    }
)
_MISSING = frozenset({"run_absent", "artifact_absent", "artifact_attempt_unavailable"})
_REPLY_FIELDS = frozenset(
    {
        "accepted",
        "ranked",
        "run",
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
        "accepted",
        "ranked",
        "runs",
        "scientific_candidate_generated",
        "scientific_execution",
        "scientific_validation",
        "session_id",
        "status",
        "text",
    }
)
_OBSERVED_TEXTS = frozenset(
    {
        QUEUED_TEXT,
        RETRY_QUEUED_TEXT,
        RUNNING_TEXT,
        COMPLETED_TEXT,
        EXTERNAL_FAILED_TEXT,
        EXTERNAL_UNCERTAIN_TEXT,
        FAILED_TEXT,
        INTERRUPTED_TEXT,
        DISABLED_TEXT,
    }
)
_REPLY_TEXT = {
    "run_queued": frozenset({QUEUED_TEXT, RETRY_QUEUED_TEXT}),
    "run_observed": _OBSERVED_TEXTS,
    "idempotency_conflict": frozenset({_KEY_TEXT}),
    "retry_not_allowed": frozenset({_RETRY_TEXT}),
    "authorization_unavailable": frozenset({_UNAVAILABLE_TEXT}),
    "invalid_payload": frozenset({_REJECTED_TEXT}),
    "run_absent": frozenset({_ABSENT_TEXT, _ARTIFACT_ABSENT}),
    "artifact_absent": frozenset({_ARTIFACT_ABSENT}),
    "artifact_attempt_unavailable": frozenset({_ARTIFACT_ABSENT}),
    "run_record_invalid": frozenset({_UNUSABLE_TEXT}),
    "adapter_failure": frozenset({_ADAPTER_TEXT}),
    "artifact_digest_mismatch": frozenset({_ARTIFACT_ABSENT}),
    "artifact_invalid": frozenset({_ARTIFACT_ABSENT}),
    "artifact_media_type_rejected": frozenset({_ARTIFACT_ABSENT}),
    "artifact_size_mismatch": frozenset({_ARTIFACT_ABSENT}),
    "artifact_too_large": frozenset({_ARTIFACT_ABSENT}),
}
_WITH_RUN = frozenset({"run_queued", "run_observed", "idempotency_conflict", "retry_not_allowed"})
_RUNLESS = frozenset(
    {
        "authorization_unavailable",
        "invalid_payload",
        "run_absent",
        "artifact_absent",
        "artifact_attempt_unavailable",
        "run_record_invalid",
        "adapter_failure",
        "artifact_digest_mismatch",
        "artifact_invalid",
        "artifact_media_type_rejected",
        "artifact_size_mismatch",
        "artifact_too_large",
    }
)


@dataclass(frozen=True)
class ScientificArtifactResponse:
    status: int
    body: bytes
    media_type: str
    filename: str


def handle_scientific_run(
    service: ScientificRunService,
    method: str,
    path: str,
    body: bytes,
    session_id: str,
) -> tuple[int, dict[str, Any]] | ScientificArtifactResponse:
    split = urlsplit(path)
    if split.query or split.fragment or method not in {"GET", "POST"}:
        return 404 if method not in {"GET", "POST"} else 400, _invalid(session_id)
    route = split.path
    if method == "GET" and route == RUNS_PATH:
        return _list(session_id, lambda: service.list_runs(session_id=session_id))
    if method == "POST" and route == RUNS_PATH:
        payload = _object(body, _CREATE_KEYS)
        if payload is None:
            return 400, _invalid(session_id)
        fields = _exact_strings(payload, ("authorization_id", "idempotency_key"))
        if fields is None or _AUTHORIZATION_ID.fullmatch(fields["authorization_id"]) is None:
            return 400, _invalid(session_id)
        return _one(
            session_id,
            lambda: service.create(
                session_id=session_id,
                authorization_id=fields["authorization_id"],
                idempotency_key=fields["idempotency_key"],
            ),
        )
    artifact = _artifact_route(route)
    if method == "GET" and artifact is not None:
        return _artifact(service, session_id, artifact[0], artifact[1])
    if method == "GET" and route.startswith(f"{RUNS_PATH}/"):
        run_id = route.removeprefix(f"{RUNS_PATH}/")
        if _RUN_ID.fullmatch(run_id) is None or "/" in run_id:
            return 404, _invalid(session_id)
        return _one(session_id, lambda: service.read(session_id=session_id, run_id=run_id))
    if method == "POST" and route.endswith("/retry"):
        run_id = route.removeprefix(f"{RUNS_PATH}/").removesuffix("/retry")
        if _RUN_ID.fullmatch(run_id) is None or "/" in run_id:
            return 404, _invalid(session_id)
        payload = _object(body, _RETRY_KEYS)
        if payload is None:
            return 400, _invalid(session_id)
        fields = _exact_strings(payload, ("idempotency_key",))
        if fields is None:
            return 400, _invalid(session_id)
        return _one(
            session_id,
            lambda: service.retry(
                session_id=session_id,
                run_id=run_id,
                idempotency_key=fields["idempotency_key"],
            ),
        )
    return 404, _invalid(session_id)


def _artifact(
    service: ScientificRunService, session_id: str, run_id: str, artifact_id: str
) -> tuple[int, dict[str, Any]] | ScientificArtifactResponse:
    if _RUN_ID.fullmatch(run_id) is None or _ARTIFACT_ID.fullmatch(artifact_id) is None:
        return 404, _invalid(session_id)
    try:
        record = service.read_artifact(
            session_id=session_id, run_id=run_id, artifact_id=artifact_id
        )
    except Exception:  # noqa: BLE001 — safe body, no traceback
        record = _adapter(session_id)
    if type(record) is not ScientificArtifactRecord:
        return _one(session_id, lambda: record)
    if (
        type(record.content) is not bytes
        or not 1 <= len(record.content) <= 65536
        or type(record.filename) is not str
        or type(record.media_type) is not str
        or _FILENAME.fullmatch(record.filename) is None
        or _FILENAME_MEDIA.get(record.filename) != record.media_type
    ):
        return 409, _invalid(session_id)
    return ScientificArtifactResponse(200, record.content, record.media_type, record.filename)


def _one(session_id: str, action: Any) -> tuple[int, dict[str, Any]]:
    try:
        reply = action()
    except Exception:  # noqa: BLE001 — safe body, no traceback
        reply = _adapter(session_id)
    if type(reply) is not ScientificRunReply or reply.session_id != session_id:
        reply = _adapter(session_id)
    try:
        payload = ScientificRunReply.to_dict(reply)
    except Exception:  # noqa: BLE001
        payload = ScientificRunReply.to_dict(_adapter(session_id))
    if not _reply_ok(payload, session_id):
        payload = ScientificRunReply.to_dict(_adapter(session_id))
    return _status(payload["status"]), payload


def _list(session_id: str, action: Any) -> tuple[int, dict[str, Any]]:
    try:
        reply = action()
    except Exception:  # noqa: BLE001
        reply = ScientificRunListReply("adapter_failure", _adapter(session_id).text, session_id)
    if type(reply) is not ScientificRunListReply or reply.session_id != session_id:
        reply = ScientificRunListReply("adapter_failure", _adapter(session_id).text, session_id)
    try:
        payload = ScientificRunListReply.to_dict(reply)
    except Exception:  # noqa: BLE001
        payload = ScientificRunListReply.to_dict(
            ScientificRunListReply("adapter_failure", _adapter(session_id).text, session_id)
        )
    if not _list_ok(payload, session_id):
        payload = ScientificRunListReply.to_dict(
            ScientificRunListReply("adapter_failure", _adapter(session_id).text, session_id)
        )
    return _status(payload["status"]), payload


def _adapter(session_id: str) -> ScientificRunReply:
    safe = session_id if type(session_id) is str and _TOKEN.fullmatch(session_id) else "unknown"
    return ScientificRunReply(
        "adapter_failure",
        "The scientific run adapter failed. No scientific action was taken.",
        safe,
    )


def _status(status: str) -> int:
    if status == "invalid_payload":
        return 400
    if status in _MISSING:
        return 404
    if status in _CONFLICTS:
        return 409
    if status == "run_queued":
        return 202
    if status == "adapter_failure":
        return 200
    return 200


def _invalid(session_id: str) -> dict[str, Any]:
    safe = session_id if type(session_id) is str and _TOKEN.fullmatch(session_id) else "unknown"
    return ScientificRunReply.to_dict(
        ScientificRunReply(
            "invalid_payload",
            "The scientific run payload was rejected. No scientific action was taken.",
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


def _artifact_route(route: str) -> tuple[str, str] | None:
    marker = "/artifacts/"
    prefix = f"{RUNS_PATH}/"
    if not route.startswith(prefix) or marker not in route:
        return None
    run_id, separator, artifact_id = route.removeprefix(prefix).partition(marker)
    if not separator or not run_id or not artifact_id or "/" in artifact_id or "/" in run_id:
        return None
    return run_id, artifact_id


def _reply_ok(payload: dict[str, Any], session_id: str) -> bool:
    if set(payload) != _REPLY_FIELDS or payload["session_id"] != session_id:
        return False
    if type(payload["status"]) is not str or type(payload["text"]) is not str:
        return False
    if payload["scientific_validation"] is not False or payload["ranked"] is not False:
        return False
    if payload["accepted"] is not False or not _safe_text(payload["text"]):
        return False
    status = payload["status"]
    text = payload["text"]
    run = payload["run"]
    if status not in _REPLY_TEXT or text not in _REPLY_TEXT[status]:
        return False
    if status in _RUNLESS:
        return (
            run is None
            and payload["scientific_execution"] is False
            and payload["scientific_candidate_generated"] is False
        )
    if status not in _WITH_RUN or run is None or not public_run_ok(run, session_id):
        return False
    if (
        payload["scientific_execution"] is not run["scientific_execution"]
        or payload["scientific_candidate_generated"] is not run["scientific_candidate_generated"]
    ):
        return False
    if status == "run_queued":
        return run["status"] == "queued" and text == run["text"]
    if status == "run_observed":
        return text == run["text"]
    if status == "idempotency_conflict":
        return text == _KEY_TEXT
    if status == "retry_not_allowed":
        return text == _RETRY_TEXT
    return False


def _list_ok(payload: dict[str, Any], session_id: str) -> bool:
    if set(payload) != _LIST_FIELDS or payload["session_id"] != session_id:
        return False
    if (
        payload["scientific_execution"] is not False
        or payload["scientific_validation"] is not False
    ):
        return False
    if payload["scientific_candidate_generated"] is not False or payload["ranked"] is not False:
        return False
    if payload["accepted"] is not False or payload["text"] != _HISTORY_TEXT:
        return False
    if payload["status"] != "runs_observed":
        return False
    rows = payload["runs"]
    if type(rows) is not list or len(rows) > 50:
        return False
    return all(public_run_ok(row, session_id) for row in rows)
