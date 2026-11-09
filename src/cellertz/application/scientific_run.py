"""Scientific-run execution lifecycle.

A run exists only after one current scientific authorization is
consumed. Readiness, approval, and fixture completion do not execute.
This module does not launch a shell, invent a BindCraft command, or
write scientific memory.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from cellertz.application.contracts import ApplicationContractError, _text
from cellertz.application.scientific_authorization import (
    ScientificAuthorizationService,
    ScientificAuthorizationStoreError,
    ScientificRunAuthorization,
    binding_document,
    binding_hash_for,
    budget_document,
    same_document,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^run_[0-9a-f]{32}$")
_AUTHORIZATION_ID = re.compile(r"^authorization_[0-9a-f]{32}$")
_CANDIDATE_ID = re.compile(r"^candidate_[0-9a-f]{32}$")
_OBSERVATION_ID = re.compile(r"^observation_[0-9a-f]{64}$")
_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(?!000000)\d{6})?\+00:00$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_AMINO = re.compile(r"[ACDEFGHIKLMNPQRSTVWY]+")
_UNSAFE = re.compile(r"traceback|/users/|/tmp/|token=|sqlite|select\s", re.IGNORECASE)
_STATUSES = frozenset({"queued", "running", "completed", "failed"})
_KINDS = frozenset({"execution_record", "execution_note", "candidate_record", "workflow_trace"})
_MEDIA = frozenset({"application/json", "text/plain", "text/markdown"})
_HISTORY_LIMIT = 50
_PUBLIC_KEYS = frozenset(
    {
        "accepted",
        "approval_id",
        "artifacts",
        "attempt",
        "authorization_id",
        "budget",
        "created_at",
        "events",
        "image_digest",
        "manifest_digest",
        "profile_id",
        "ranked",
        "readiness_observation_id",
        "readiness_observed_at",
        "request_digest",
        "request_id",
        "request_revision",
        "run_id",
        "runtime_name",
        "runtime_version",
        "scientific_candidate_generated",
        "scientific_execution",
        "scientific_validation",
        "session_id",
        "status",
        "target_scope",
        "text",
        "tool_name",
        "tool_version",
        "updated_at",
    }
)
_MAX_ARTIFACT_BYTES = 65536
_MAX_NOTE_CHARS = 4000
_PROFILE = "pdl1-bindcraft-historical-v1"
_PROFILES = frozenset({_PROFILE, "pdl1-bindcraft-docker-v1"})
_EXECUTION_SCHEMA = "cellertz.scientific_execution_record.v1"
_CANDIDATE_SCHEMA = "cellertz.scientific_candidate.v1"
_CANDIDATE_KEYS = frozenset({"candidate_id", "schema", "sequence", "target_scope"})
_RECORD_KEYS = frozenset(
    {
        "attempt",
        "authorization_id",
        "run_id",
        "schema",
        "scientific_validation",
        "session_id",
        "status",
    }
)

QUEUED_TEXT = "Scientific run queued."
RETRY_QUEUED_TEXT = "Scientific retry queued."
RUNNING_TEXT = "Scientific run started."
COMPLETED_TEXT = "Scientific run completed. Not scientific validation."
FAILED_TEXT = "The scientific run failed. No scientific action was taken."
INTERRUPTED_TEXT = (
    "The scientific run was interrupted before completion. No scientific action was taken."
)
EXTERNAL_FAILED_TEXT = (
    "The scientific run failed after an external launch. The outcome is not scientific validation."
)
EXTERNAL_UNCERTAIN_TEXT = (
    "The scientific run was interrupted after an external launch. "
    "The external outcome is unknown. No new launch was started."
)
DISABLED_TEXT = "Scientific execution is disabled. No scientific action was taken."
_FAILURE_TEXTS = frozenset(
    {FAILED_TEXT, INTERRUPTED_TEXT, EXTERNAL_FAILED_TEXT, EXTERNAL_UNCERTAIN_TEXT, DISABLED_TEXT}
)


def interruption_text(external_state: str | None) -> str:
    if external_state is None or external_state == "reserved":
        return INTERRUPTED_TEXT
    if external_state == "exited":
        return EXTERNAL_FAILED_TEXT
    return EXTERNAL_UNCERTAIN_TEXT


def execution_failure_text(external_state: str | None) -> str:
    if external_state is None or external_state == "reserved":
        return FAILED_TEXT
    if external_state == "exited":
        return EXTERNAL_FAILED_TEXT
    return EXTERNAL_UNCERTAIN_TEXT


_ABSENT_TEXT = "No scientific run matches this request."
_KEY_TEXT = "The idempotency key does not match this scientific run."
_UNAVAILABLE_TEXT = (
    "The scientific authorization is not available for execution. No scientific action was taken."
)
_RETRY_TEXT = "Retry is not available for this scientific run."
_REJECTED_TEXT = "The scientific run payload was rejected. No scientific action was taken."
_UNUSABLE_TEXT = "The scientific run record could not be used. No scientific action was taken."
_ADAPTER_TEXT = "The scientific run adapter failed. No scientific action was taken."
_HISTORY_TEXT = "Scientific runs. Not scientific validation."
_ARTIFACT_ABSENT = "The scientific run artifact is not available."


class ScientificRunStoreError(Exception):
    """Stable operational-store code. It does not carry driver text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _digest(document: Mapping[str, Any]) -> str:
    return _sha256(_canonical(document).encode("utf-8"))


def _stamp_ok(value: object) -> bool:
    if type(value) is not str or _STAMP.fullmatch(value) is None:
        return False
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return False
    return moment.utcoffset() == timedelta(0) and moment.isoformat() == value


def _safe_text(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and _UNSAFE.search(value) is None


def _identifier(value: object, code: str) -> str:
    if type(value) is not str or _KEY.fullmatch(value) is None:
        raise ApplicationContractError(code)
    return value


@dataclass(frozen=True)
class ScientificRunEvent:
    sequence: int
    status: str
    recorded_at: str
    text: str
    attempt: int
    origin: str = "server"

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or isinstance(self.sequence, bool) or self.sequence < 1:
            raise ApplicationContractError("invalid_scientific_run_event")
        if type(self.attempt) is not int or isinstance(self.attempt, bool) or self.attempt < 1:
            raise ApplicationContractError("invalid_scientific_run_event")
        if self.status not in _STATUSES or self.origin != "server":
            raise ApplicationContractError("invalid_scientific_run_event")
        if not _stamp_ok(self.recorded_at) or self.text not in _event_texts(self):
            raise ApplicationContractError("invalid_scientific_run_event")


def _event_texts(event: ScientificRunEvent) -> frozenset[str]:
    if event.status == "queued":
        return frozenset({QUEUED_TEXT if event.attempt == 1 else RETRY_QUEUED_TEXT})
    if event.status == "running":
        return frozenset({RUNNING_TEXT})
    if event.status == "completed":
        return frozenset({COMPLETED_TEXT})
    return _FAILURE_TEXTS


@dataclass(frozen=True)
class ScientificArtifact:
    artifact_id: str
    run_id: str
    session_id: str
    attempt: int
    kind: str
    media_type: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        if (
            type(self.artifact_id) is not str
            or type(self.run_id) is not str
            or type(self.session_id) is not str
            or type(self.attempt) is not int
            or isinstance(self.attempt, bool)
            or self.attempt < 1
            or self.kind not in _KINDS
            or self.media_type not in _MEDIA
            or _DIGEST.fullmatch(self.sha256) is None
            or type(self.size) is not int
            or isinstance(self.size, bool)
            or self.size < 1
            or self.size > _MAX_ARTIFACT_BYTES
        ):
            raise ApplicationContractError("invalid_scientific_artifact")


@dataclass(frozen=True)
class ScientificRun:
    run_id: str
    session_id: str
    authorization_id: str
    request_id: str
    request_revision: int
    approval_id: str
    request_digest: str
    readiness_observation_id: str
    readiness_observed_at: str
    profile_id: str
    tool_name: str
    tool_version: str
    runtime_name: str
    runtime_version: str
    image_digest: str
    manifest_digest: str
    target_scope: str
    budget: dict[str, Any]
    binding_hash: str
    status: str
    attempt: int
    created_at: str
    updated_at: str
    events: tuple[ScientificRunEvent, ...]
    artifacts: tuple[ScientificArtifact, ...] = ()
    confirmed: bool = False

    def __post_init__(self) -> None:
        if not _run_shape(self) or not _history_ok(self):
            raise ApplicationContractError("invalid_scientific_run")

    @property
    def text(self) -> str:
        return _status_text(self)

    @property
    def scientific_execution(self) -> bool:
        return self.status == "completed"

    @property
    def scientific_validation(self) -> bool:
        return False

    @property
    def scientific_candidate_generated(self) -> bool:
        return self.status == "completed" and any(
            item.kind == "candidate_record" for item in self.artifacts
        )

    @property
    def ranked(self) -> bool:
        return False

    @property
    def accepted(self) -> bool:
        return False

    def transition(
        self,
        status: str,
        *,
        recorded_at: str,
        text: str,
        attempt: int | None = None,
        artifacts: tuple[ScientificArtifact, ...] = (),
    ) -> ScientificRun:
        next_attempt = self.attempt if attempt is None else attempt
        event = ScientificRunEvent(
            sequence=self.events[-1].sequence + 1,
            status=status,
            recorded_at=recorded_at,
            text=text,
            attempt=next_attempt,
        )
        return replace(
            self,
            status=status,
            attempt=next_attempt,
            updated_at=recorded_at,
            events=(*self.events, event),
            artifacts=artifacts,
        )


def _run_shape(run: ScientificRun) -> bool:
    if (
        _RUN_ID.fullmatch(run.run_id) is None
        or _TOKEN.fullmatch(run.session_id) is None
        or _AUTHORIZATION_ID.fullmatch(run.authorization_id) is None
        or _TOKEN.fullmatch(run.request_id) is None
        or _TOKEN.fullmatch(run.approval_id) is None
        or type(run.request_revision) is not int
        or isinstance(run.request_revision, bool)
        or run.request_revision < 1
        or _DIGEST.fullmatch(run.request_digest) is None
        or _OBSERVATION_ID.fullmatch(run.readiness_observation_id) is None
        or not _stamp_ok(run.readiness_observed_at)
        or run.profile_id not in _PROFILES
        or run.tool_name != "bindcraft"
        or run.target_scope != "PDL1"
        or _TOKEN.fullmatch(run.tool_version) is None
        or _TOKEN.fullmatch(run.runtime_name) is None
        or _TOKEN.fullmatch(run.runtime_version) is None
        or run.tool_version.lower() in {"latest", "current", "unknown"}
        or run.runtime_name.lower() in {"latest", "current", "unknown"}
        or run.runtime_version.lower() in {"latest", "current", "unknown"}
        or _IMAGE.fullmatch(run.image_digest) is None
        or _DIGEST.fullmatch(run.manifest_digest) is None
        or run.binding_hash != binding_hash_for(run)
        or run.status not in _STATUSES
        or type(run.attempt) is not int
        or isinstance(run.attempt, bool)
        or run.attempt < 1
        or not _stamp_ok(run.created_at)
        or not _stamp_ok(run.updated_at)
        or type(run.confirmed) is not bool
        or not budget_document(run.budget)
        or run.budget.get("authorizes_execution") is not False
    ):
        return False
    if run.status == "completed":
        kinds = [item.kind for item in run.artifacts]
        if kinds.count("execution_record") != 1 or len(kinds) != len(set(kinds)):
            return False
    elif run.artifacts:
        return False
    return all(_artifact_matches(run, item) for item in run.artifacts)


def _artifact_matches(run: ScientificRun, item: ScientificArtifact) -> bool:
    if (
        item.run_id != run.run_id
        or item.session_id != run.session_id
        or item.attempt != run.attempt
    ):
        return False
    expected = f"artifact_{run.run_id}_{run.attempt}_{item.kind}"
    if item.artifact_id != expected:
        return False
    if item.kind == "execution_note":
        return item.media_type in {"text/plain", "text/markdown"}
    return item.media_type == "application/json"


def _history_ok(run: ScientificRun) -> bool:
    events = run.events
    if not events or events[-1].status != run.status or events[-1].attempt != run.attempt:
        return False
    if events[-1].text != _status_text(run):
        return False
    if events[0].sequence != 1 or events[0].status != "queued" or events[0].attempt != 1:
        return False
    if events[0].text != QUEUED_TEXT or not _stamp_ok(events[0].recorded_at):
        return False
    if run.created_at != events[0].recorded_at or run.updated_at != events[-1].recorded_at:
        return False
    if datetime.fromisoformat(run.created_at) > datetime.fromisoformat(run.updated_at):
        return False
    previous = events[0]
    for event in events[1:]:
        if event.sequence != previous.sequence + 1:
            return False
        if datetime.fromisoformat(event.recorded_at) < datetime.fromisoformat(previous.recorded_at):
            return False
        if event.attempt == previous.attempt + 1:
            if (
                event.status != "queued"
                or event.text != RETRY_QUEUED_TEXT
                or previous.status != "failed"
            ):
                return False
        elif (
            event.attempt != previous.attempt
            or event.status == "queued"
            or event.status == "running"
            and previous.status != "queued"
            or event.status in {"completed", "failed"}
            and previous.status != "running"
        ):
            return False
        previous = event
    return True


def _status_text(run: ScientificRun) -> str:
    if run.status == "completed":
        return COMPLETED_TEXT
    if run.status == "running":
        return RUNNING_TEXT
    if run.status == "failed":
        return run.events[-1].text if run.events[-1].text in _FAILURE_TEXTS else FAILED_TEXT
    return run.events[-1].text


def stored_run(run: ScientificRun) -> dict[str, Any]:
    return {
        "accepted": False,
        "approval_id": run.approval_id,
        "artifacts": [_artifact_dict(item) for item in run.artifacts],
        "attempt": run.attempt,
        "authorization_id": run.authorization_id,
        "binding_hash": run.binding_hash,
        "budget": dict(run.budget),
        "created_at": run.created_at,
        "events": [_event_dict(event) for event in run.events],
        "image_digest": run.image_digest,
        "manifest_digest": run.manifest_digest,
        "profile_id": run.profile_id,
        "ranked": False,
        "readiness_observation_id": run.readiness_observation_id,
        "readiness_observed_at": run.readiness_observed_at,
        "request_digest": run.request_digest,
        "request_id": run.request_id,
        "request_revision": run.request_revision,
        "run_id": run.run_id,
        "runtime_name": run.runtime_name,
        "runtime_version": run.runtime_version,
        "scientific_candidate_generated": run.scientific_candidate_generated,
        "scientific_execution": run.scientific_execution,
        "scientific_validation": False,
        "session_id": run.session_id,
        "status": run.status,
        "target_scope": run.target_scope,
        "text": _status_text(run),
        "tool_name": run.tool_name,
        "tool_version": run.tool_version,
        "updated_at": run.updated_at,
    }


def public_run(run: ScientificRun) -> dict[str, Any]:
    if (
        type(run) is not ScientificRun
        or not run.confirmed
        or run.binding_hash != binding_hash_for(run)
    ):
        raise ApplicationContractError("invalid_scientific_run")
    payload = stored_run(run)
    del payload["binding_hash"]
    return payload


def public_run_ok(payload: object, session_id: str) -> bool:
    """Reconstruct the public document. A mutated field cannot pass through."""
    if (
        type(payload) is not dict
        or set(payload) != _PUBLIC_KEYS
        or payload.get("session_id") != session_id
    ):
        return False
    document = dict(payload)
    document["binding_hash"] = binding_hash_for(payload)
    try:
        run = _run_from_public(document, confirmed=True)
    except ApplicationContractError:
        return False
    return public_run(run) == payload


def artifact_bytes_match(run: ScientificRun, artifact: ScientificArtifact, content: bytes) -> bool:
    if (
        type(run) is not ScientificRun
        or type(artifact) is not ScientificArtifact
        or type(content) is not bytes
        or artifact.run_id != run.run_id
        or artifact.session_id != run.session_id
        or artifact.attempt != run.attempt
    ):
        return False
    if not 1 <= len(content) <= _MAX_ARTIFACT_BYTES or len(content) != artifact.size:
        return False
    if _sha256(content) != artifact.sha256:
        return False
    if artifact.kind == "execution_record":
        return artifact.media_type == "application/json" and _record_bytes(content, run)
    if artifact.kind == "candidate_record":
        return artifact.media_type == "application/json" and _candidate_bytes(content, run)
    if artifact.kind == "workflow_trace":
        from cellertz.application.governed_agent_runtime import workflow_bytes_match

        return artifact.media_type == "application/json" and workflow_bytes_match(content, run)
    return artifact.media_type in {"text/plain", "text/markdown"} and _note_bytes(content)


def run_document(run: ScientificRun) -> str:
    return _canonical(stored_run(run))


def run_from_document(payload: object, *, confirmed: bool = False) -> ScientificRun:
    if type(payload) is not str or type(confirmed) is not bool:
        raise ScientificRunStoreError("scientific_run_record_invalid")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        raise ScientificRunStoreError("scientific_run_record_invalid") from None
    if type(parsed) is not dict or _canonical(parsed) != payload:
        raise ScientificRunStoreError("scientific_run_record_invalid")
    try:
        run = _run_from_public(parsed, confirmed=confirmed)
    except ApplicationContractError:
        raise ScientificRunStoreError("scientific_run_record_invalid") from None
    if run_document(run) != payload:
        raise ScientificRunStoreError("scientific_run_record_invalid")
    return run


def _run_from_public(parsed: dict[str, Any], *, confirmed: bool) -> ScientificRun:
    events = parsed.get("events")
    artifacts = parsed.get("artifacts")
    budget = parsed.get("budget")
    if type(events) is not list or type(artifacts) is not list or type(budget) is not dict:
        raise ApplicationContractError("invalid_scientific_run")
    run = ScientificRun(
        run_id=parsed.get("run_id", ""),
        session_id=parsed.get("session_id", ""),
        authorization_id=parsed.get("authorization_id", ""),
        request_id=parsed.get("request_id", ""),
        request_revision=parsed.get("request_revision", 0),
        approval_id=parsed.get("approval_id", ""),
        request_digest=parsed.get("request_digest", ""),
        readiness_observation_id=parsed.get("readiness_observation_id", ""),
        readiness_observed_at=parsed.get("readiness_observed_at", ""),
        profile_id=parsed.get("profile_id", ""),
        tool_name=parsed.get("tool_name", ""),
        tool_version=parsed.get("tool_version", ""),
        runtime_name=parsed.get("runtime_name", ""),
        runtime_version=parsed.get("runtime_version", ""),
        image_digest=parsed.get("image_digest", ""),
        manifest_digest=parsed.get("manifest_digest", ""),
        target_scope=parsed.get("target_scope", ""),
        budget=budget,
        binding_hash=parsed.get("binding_hash", ""),
        status=parsed.get("status", ""),
        attempt=parsed.get("attempt", 0),
        created_at=parsed.get("created_at", ""),
        updated_at=parsed.get("updated_at", ""),
        events=tuple(_event_from_public(item) for item in events),
        artifacts=tuple(_artifact_from_public(item) for item in artifacts),
        confirmed=confirmed,
    )
    if (
        parsed.get("scientific_validation") is not False
        or parsed.get("ranked") is not False
        or parsed.get("accepted") is not False
        or parsed.get("scientific_execution") is not run.scientific_execution
        or parsed.get("scientific_candidate_generated") is not run.scientific_candidate_generated
        or parsed.get("text") != _status_text(run)
    ):
        raise ApplicationContractError("invalid_scientific_run")
    return run


def _event_from_public(value: object) -> ScientificRunEvent:
    if type(value) is not dict:
        raise ApplicationContractError("invalid_scientific_run_event")
    return ScientificRunEvent(
        sequence=value.get("sequence", 0),
        status=value.get("status", ""),
        recorded_at=value.get("recorded_at", ""),
        text=value.get("text", ""),
        attempt=value.get("attempt", 0),
        origin=value.get("origin", ""),
    )


def _artifact_from_public(value: object) -> ScientificArtifact:
    if type(value) is not dict:
        raise ApplicationContractError("invalid_scientific_artifact")
    return ScientificArtifact(
        artifact_id=value.get("artifact_id", ""),
        run_id=value.get("run_id", ""),
        session_id=value.get("session_id", ""),
        attempt=value.get("attempt", 0),
        kind=value.get("kind", ""),
        media_type=value.get("media_type", ""),
        sha256=value.get("sha256", ""),
        size=value.get("size", 0),
    )


def _event_dict(event: ScientificRunEvent) -> dict[str, Any]:
    return {
        "attempt": event.attempt,
        "origin": event.origin,
        "recorded_at": event.recorded_at,
        "sequence": event.sequence,
        "status": event.status,
        "text": event.text,
    }


def _artifact_dict(item: ScientificArtifact) -> dict[str, Any]:
    return {
        "artifact_id": item.artifact_id,
        "attempt": item.attempt,
        "kind": item.kind,
        "media_type": item.media_type,
        "run_id": item.run_id,
        "session_id": item.session_id,
        "sha256": item.sha256,
        "size": item.size,
    }


def freeze_run(run: ScientificRun) -> ScientificRun:
    return run_from_document(run_document(run), confirmed=run.confirmed)


@dataclass(frozen=True)
class ExecutorArtifact:
    kind: str
    media_type: str
    body: bytes


@dataclass(frozen=True)
class ScientificExecutionResult:
    status: str
    artifacts: tuple[ExecutorArtifact, ...] = ()


class ScientificRunExecutor(Protocol):
    """Server-owned execution boundary. It cannot receive a client command."""

    def execute(self, run: ScientificRun) -> ScientificExecutionResult: ...


def accept_outputs(run: ScientificRun, result: object) -> tuple[ScientificArtifact, ...] | None:
    """Return checked artifacts, or None when the output must fail closed."""
    if type(result) is not ScientificExecutionResult or result.status not in {
        "completed",
        "failed",
    }:
        return None
    if result.status == "failed":
        return () if result.artifacts == () else None
    if type(result.artifacts) is not tuple or not result.artifacts:
        return None
    kinds = [item.kind for item in result.artifacts if type(item) is ExecutorArtifact]
    if len(kinds) != len(result.artifacts) or kinds.count("execution_record") != 1:
        return None
    if len(kinds) != len(set(kinds)) or any(kind not in _KINDS for kind in kinds):
        return None
    built: list[ScientificArtifact] = []
    for item in result.artifacts:
        artifact = _one_output(run, item)
        if artifact is None:
            return None
        built.append(artifact)
    return tuple(built)


def _one_output(run: ScientificRun, item: ExecutorArtifact) -> ScientificArtifact | None:
    if type(item.body) is not bytes or not 1 <= len(item.body) <= _MAX_ARTIFACT_BYTES:
        return None
    if item.kind not in _KINDS or item.media_type not in _MEDIA:
        return None
    if item.kind == "execution_record":
        if item.media_type != "application/json" or not _record_bytes(item.body, run):
            return None
    elif item.kind == "candidate_record":
        if item.media_type != "application/json" or not _candidate_bytes(item.body, run):
            return None
    elif item.kind == "workflow_trace":
        from cellertz.application.governed_agent_runtime import workflow_bytes_match

        if item.media_type != "application/json" or not workflow_bytes_match(item.body, run):
            return None
    elif item.media_type not in {"text/plain", "text/markdown"} or not _note_bytes(item.body):
        return None
    return ScientificArtifact(
        artifact_id=f"artifact_{run.run_id}_{run.attempt}_{item.kind}",
        run_id=run.run_id,
        session_id=run.session_id,
        attempt=run.attempt,
        kind=item.kind,
        media_type=item.media_type,
        sha256=_sha256(item.body),
        size=len(item.body),
    )


def _record_bytes(body: bytes, run: ScientificRun) -> bool:
    expected = {
        "attempt": run.attempt,
        "authorization_id": run.authorization_id,
        "run_id": run.run_id,
        "schema": _EXECUTION_SCHEMA,
        "scientific_validation": False,
        "session_id": run.session_id,
        "status": "completed",
    }
    return _exact_json(body, expected, _RECORD_KEYS)


def _candidate_bytes(body: bytes, run: ScientificRun) -> bool:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if type(parsed) is not dict or set(parsed) != _CANDIDATE_KEYS:
        return False
    sequence = parsed.get("sequence")
    if (
        parsed.get("schema") != _CANDIDATE_SCHEMA
        or _CANDIDATE_ID.fullmatch(str(parsed.get("candidate_id", ""))) is None
        or type(sequence) is not str
        or not 1 <= len(sequence) <= 512
        or _AMINO.fullmatch(sequence) is None
        or parsed.get("target_scope") != run.target_scope
    ):
        return False
    return body.decode("utf-8") == _canonical(parsed)


def _exact_json(body: bytes, expected: Mapping[str, Any], keys: frozenset[str]) -> bool:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        type(parsed) is dict
        and set(parsed) == keys
        and parsed == dict(expected)
        and (body.decode("utf-8") == _canonical(expected))
    )


def _note_bytes(body: bytes) -> bool:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return "\x00" not in text and 1 <= len(text) <= _MAX_NOTE_CHARS and _safe_text(text)


def artifact_filename(artifact: ScientificArtifact) -> str:
    if artifact.kind == "execution_record":
        return "scientific-execution-record.json"
    if artifact.kind == "candidate_record":
        return "scientific-candidate-record.json"
    if artifact.kind == "workflow_trace":
        return "scientific-agent-workflow.json"
    if artifact.media_type == "text/markdown":
        return "scientific-execution-note.md"
    return "scientific-execution-note.txt"


class ScientificRunStore(Protocol):
    def claim_create(
        self,
        *,
        scope: tuple[str, str],
        content_hash: str,
        run: ScientificRun,
    ) -> tuple[str, ScientificRun]: ...

    def confirm(self, run_id: str) -> ScientificRun | None: ...

    def delete_provisional(self, run_id: str) -> bool: ...

    def unconfirmed(self) -> tuple[ScientificRun, ...]: ...

    def claim_retry(
        self,
        *,
        run_id: str,
        session_id: str,
        scope: tuple[str, str],
        content_hash: str,
        expected_attempt: int,
        recorded_at: str,
    ) -> tuple[str, ScientificRun | None]: ...

    def claim_next_queued(self, *, recorded_at: str) -> ScientificRun | None: ...

    def commit_transition(
        self,
        run: ScientificRun,
        *,
        expected_status: str,
        expected_attempt: int,
        contents: Mapping[str, bytes],
    ) -> str: ...

    def read_run(self, session_id: str, run_id: str) -> ScientificRun | None: ...

    def read_by_authorization(
        self, session_id: str, authorization_id: str
    ) -> ScientificRun | None: ...

    def list_runs(self, session_id: str, *, limit: int) -> tuple[ScientificRun, ...]: ...

    def read_artifact(
        self, session_id: str, run_id: str, artifact_id: str
    ) -> tuple[ScientificArtifact, bytes]: ...

    def recover_interrupted(self, *, recorded_at: str) -> tuple[ScientificRun, ...]: ...

    def close(self) -> None: ...


class InMemoryScientificRunStore:
    """Process-local operational store. Not scientific memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, ScientificRun] = {}
        self._by_authorization: dict[tuple[str, str], str] = {}
        self._create_keys: dict[tuple[str, str], tuple[str, str]] = {}
        self._retry_keys: dict[tuple[str, str], tuple[str, str, int, ScientificRun | None]] = {}
        self._bytes: dict[str, bytes] = {}
        self._fail_on: str | None = None
        self._external: dict[tuple[str, int], object] = {}

    def claim_create(
        self, *, scope: tuple[str, str], content_hash: str, run: ScientificRun
    ) -> tuple[str, ScientificRun]:
        with self._lock:
            self._failpoint("create_run")
            found = self._create_keys.get(scope)
            if found is not None:
                stored_hash, run_id = found
                current = self._runs[run_id]
                if stored_hash != content_hash:
                    return "conflict", current
                return "replay", current
            existing_id = self._by_authorization.get((run.session_id, run.authorization_id))
            if existing_id is not None:
                self._create_keys[scope] = (content_hash, existing_id)
                return "replay", self._runs[existing_id]
            if run.confirmed or run.status != "queued" or run.attempt != 1:
                raise ScientificRunStoreError("scientific_run_record_invalid")
            stored = freeze_run(run)
            self._runs[stored.run_id] = stored
            self._by_authorization[(stored.session_id, stored.authorization_id)] = stored.run_id
            self._failpoint("create_key")
            self._create_keys[scope] = (content_hash, stored.run_id)
            return "created", stored

    def confirm(self, run_id: str) -> ScientificRun | None:
        with self._lock:
            self._failpoint("confirm")
            current = self._runs.get(run_id)
            if current is None:
                return None
            if current.confirmed:
                return current
            updated = replace(current, confirmed=True)
            self._runs[run_id] = updated
            return updated

    def delete_provisional(self, run_id: str) -> bool:
        with self._lock:
            current = self._runs.get(run_id)
            if current is None or current.confirmed:
                return False
            del self._runs[run_id]
            self._by_authorization.pop((current.session_id, current.authorization_id), None)
            stale = [key for key, value in self._create_keys.items() if value[1] == run_id]
            for key in stale:
                del self._create_keys[key]
            return True

    def unconfirmed(self) -> tuple[ScientificRun, ...]:
        with self._lock:
            pending = [run for run in self._runs.values() if not run.confirmed]
            pending.sort(key=lambda item: item.run_id)
            return tuple(pending)

    def claim_retry(
        self,
        *,
        run_id: str,
        session_id: str,
        scope: tuple[str, str],
        content_hash: str,
        expected_attempt: int,
        recorded_at: str,
    ) -> tuple[str, ScientificRun | None]:
        with self._lock:
            found = self._retry_keys.get(scope)
            if found is not None:
                stored_hash, stored_run, attempt, snapshot = found
                current = self._runs.get(stored_run)
                if stored_hash != content_hash or stored_run != run_id:
                    return "conflict", snapshot if snapshot is not None else current
                if snapshot is not None:
                    return "replay", snapshot
                if current is None or current.attempt != attempt:
                    raise ScientificRunStoreError("scientific_run_record_invalid")
                return "replay", current
            current = self._runs.get(run_id)
            if current is None or current.session_id != session_id or not current.confirmed:
                return "absent", None
            if current.status != "failed" or current.attempt != expected_attempt:
                return "retry_not_allowed", current
            if self._retry_blocked(run_id):
                return "retry_not_allowed", current
            queued = freeze_run(
                current.transition(
                    "queued",
                    recorded_at=recorded_at,
                    text=RETRY_QUEUED_TEXT,
                    attempt=current.attempt + 1,
                )
            )
            self._failpoint("retry_key")
            self._runs[run_id] = queued
            self._retry_keys[scope] = (content_hash, run_id, queued.attempt, None)
            return "retried", queued

    def claim_next_queued(self, *, recorded_at: str) -> ScientificRun | None:
        with self._lock:
            queued = next(
                (run for run in self._runs.values() if run.confirmed and run.status == "queued"),
                None,
            )
            if queued is None:
                return None
            running = freeze_run(
                queued.transition("running", recorded_at=recorded_at, text=RUNNING_TEXT)
            )
            self._runs[running.run_id] = running
            return running

    def commit_transition(
        self,
        run: ScientificRun,
        *,
        expected_status: str,
        expected_attempt: int,
        contents: Mapping[str, bytes],
    ) -> str:
        with self._lock:
            current = self._runs.get(run.run_id)
            if (
                current is None
                or current.status != expected_status
                or current.attempt != expected_attempt
                or not current.confirmed
            ):
                return "lost"
            stored = freeze_run(replace(run, confirmed=True))
            if not _contents_match(stored, contents):
                raise ScientificRunStoreError("scientific_run_record_invalid")
            self._runs[stored.run_id] = stored
            if stored.status == "completed":
                for artifact in stored.artifacts:
                    self._bytes[artifact.artifact_id] = contents[artifact.artifact_id]
            self._seal(stored)
            return "committed"

    def read_run(self, session_id: str, run_id: str) -> ScientificRun | None:
        with self._lock:
            current = self._runs.get(run_id)
            if current is None or current.session_id != session_id or not current.confirmed:
                return None
            return current

    def read_by_authorization(self, session_id: str, authorization_id: str) -> ScientificRun | None:
        with self._lock:
            run_id = self._by_authorization.get((session_id, authorization_id))
            if run_id is None:
                return None
            return self._runs.get(run_id)

    def list_runs(self, session_id: str, *, limit: int) -> tuple[ScientificRun, ...]:
        with self._lock:
            owned = [
                run for run in self._runs.values() if run.session_id == session_id and run.confirmed
            ]
            owned.sort(key=lambda item: (item.updated_at, item.run_id), reverse=True)
            return tuple(owned[: min(limit, _HISTORY_LIMIT)])

    def read_artifact(
        self, session_id: str, run_id: str, artifact_id: str
    ) -> tuple[ScientificArtifact, bytes]:
        with self._lock:
            current = self._runs.get(run_id)
            if current is None or current.session_id != session_id or not current.confirmed:
                raise ScientificRunStoreError("run_absent")
            artifact = next(
                (item for item in current.artifacts if item.artifact_id == artifact_id), None
            )
            if artifact is None:
                older = artifact_id in self._bytes
                code = "artifact_attempt_unavailable" if older else "artifact_absent"
                raise ScientificRunStoreError(code)
            content = self._bytes.get(artifact_id)
            if content is None or not artifact_bytes_match(current, artifact, content):
                raise ScientificRunStoreError("artifact_digest_mismatch")
            return artifact, content

    def recover_interrupted(self, *, recorded_at: str) -> tuple[ScientificRun, ...]:
        with self._lock:
            recovered: list[ScientificRun] = []
            for current in list(self._runs.values()):
                if not current.confirmed or current.status != "running":
                    continue
                updated = freeze_run(
                    current.transition(
                        "failed",
                        recorded_at=recorded_at,
                        text=interruption_text(
                            self._external_state(current.run_id, current.attempt)
                        ),
                    )
                )
                self._runs[updated.run_id] = updated
                self._seal(updated)
                recovered.append(updated)
            return tuple(recovered)

    def close(self) -> None:
        return None

    def reserve_external(self, attempt: object) -> None:
        from cellertz.application.pdl1_attempt import ExternalAttempt

        if type(attempt) is not ExternalAttempt:
            raise ScientificRunStoreError("scientific_run_record_invalid")
        with self._lock:
            self._external[(attempt.run_id, attempt.attempt)] = attempt

    def save_external(self, attempt: object) -> None:
        from cellertz.application.pdl1_attempt import ExternalAttempt

        if type(attempt) is not ExternalAttempt:
            raise ScientificRunStoreError("scientific_run_record_invalid")
        from cellertz.application.pdl1_attempt import reservation_unchanged

        with self._lock:
            current = self._external.get((attempt.run_id, attempt.attempt))
            if type(current) is not ExternalAttempt or not reservation_unchanged(current, attempt):
                raise ScientificRunStoreError("scientific_run_record_invalid")
            self._external[(attempt.run_id, attempt.attempt)] = attempt

    def list_external(self) -> tuple[object, ...]:
        with self._lock:
            return tuple(self._external.values())

    def read_run_any(self, run_id: str) -> ScientificRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def external_state(self, run_id: str, attempt: int) -> str | None:
        with self._lock:
            return self._external_state(run_id, attempt)

    def _external_state(self, run_id: str, attempt: int) -> str | None:
        found = self._external.get((run_id, attempt))
        if found is None:
            return None
        return getattr(found, "state", None)

    def _retry_blocked(self, run_id: str) -> bool:
        return any(
            getattr(item, "run_id", None) == run_id
            and getattr(item, "retry_allowed", True) is False
            for item in self._external.values()
        )

    def _seal(self, run: ScientificRun) -> None:
        if run.status not in {"completed", "failed"}:
            return
        for key, value in list(self._retry_keys.items()):
            stored_hash, run_id, attempt, snapshot = value
            if run_id == run.run_id and attempt == run.attempt and snapshot is None:
                self._retry_keys[key] = (stored_hash, run_id, attempt, run)

    def _failpoint(self, name: str) -> None:
        if self._fail_on == name:
            raise ScientificRunStoreError("scientific_run_store_unavailable")


def _contents_match(run: ScientificRun, contents: Mapping[str, bytes]) -> bool:
    if run.status != "completed":
        return contents == {} and not run.artifacts
    if set(contents) != {item.artifact_id for item in run.artifacts}:
        return False
    return all(
        type(contents[item.artifact_id]) is bytes
        and len(contents[item.artifact_id]) == item.size
        and _sha256(contents[item.artifact_id]) == item.sha256
        for item in run.artifacts
    )


@dataclass(frozen=True)
class ScientificRunReply:
    status: str
    text: str
    session_id: str
    run: ScientificRun | None = None

    def to_dict(self) -> dict[str, Any]:
        run = self.run if self.run is not None and self.run.confirmed else None
        if self.run is not None and not self.run.confirmed:
            raise ApplicationContractError("invalid_scientific_run_reply")
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "run": None if run is None else public_run(run),
            "scientific_execution": False if run is None else run.scientific_execution,
            "scientific_validation": False,
            "scientific_candidate_generated": False
            if run is None
            else run.scientific_candidate_generated,
            "ranked": False,
            "accepted": False,
        }


@dataclass(frozen=True)
class ScientificRunListReply:
    status: str
    text: str
    session_id: str
    runs: tuple[ScientificRun, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "runs": [public_run(run) for run in self.runs],
            "scientific_execution": False,
            "scientific_validation": False,
            "scientific_candidate_generated": False,
            "ranked": False,
            "accepted": False,
        }


@dataclass(frozen=True)
class ScientificArtifactRecord:
    media_type: str
    filename: str
    content: bytes


class ScientificRunService:
    """Creates one scientific run by consuming one authorization."""

    def __init__(
        self,
        authorizations: ScientificAuthorizationService,
        store: ScientificRunStore | None = None,
        executor: ScientificRunExecutor | None = None,
        *,
        execution_enabled: bool = False,
        clock: Callable[[], datetime] | None = None,
        run_ids: Callable[[], str] | None = None,
    ) -> None:
        if type(execution_enabled) is not bool:
            raise ApplicationContractError("invalid_scientific_run_execution")
        self._authorizations = authorizations
        self._store = store if store is not None else InMemoryScientificRunStore()
        self._executor = executor
        self._execution_enabled = execution_enabled
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._run_ids = run_ids or (lambda: f"run_{uuid4().hex}")
        self._mutation = threading.Lock()
        self._pending_failure: ScientificRun | None = None

    def create(
        self, *, session_id: str, authorization_id: str, idempotency_key: str
    ) -> ScientificRunReply:
        try:
            _text(session_id, "invalid_session_id")
            authorization_id = _identifier(authorization_id, "invalid_authorization_id")
            key = _identifier(idempotency_key, "invalid_idempotency_key")
            if _AUTHORIZATION_ID.fullmatch(authorization_id) is None:
                raise ApplicationContractError("invalid_authorization_id")
        except ApplicationContractError:
            return _rejected(session_id)
        with self._mutation:
            try:
                return self._create_locked(session_id, authorization_id, key)
            except (ScientificRunStoreError, ScientificAuthorizationStoreError):
                return _unusable(session_id)

    def retry(self, *, session_id: str, run_id: str, idempotency_key: str) -> ScientificRunReply:
        try:
            _text(session_id, "invalid_session_id")
            run_id = _identifier(run_id, "invalid_run_id")
            key = _identifier(idempotency_key, "invalid_idempotency_key")
            if _RUN_ID.fullmatch(run_id) is None:
                raise ApplicationContractError("invalid_run_id")
        except ApplicationContractError:
            return _rejected(session_id)
        with self._mutation:
            try:
                current = self._store.read_run(session_id, run_id)
            except ScientificRunStoreError:
                return _unusable(session_id)
            if current is None:
                return ScientificRunReply("run_absent", _ABSENT_TEXT, session_id)
            content_hash = _digest(
                {
                    "authorization_id": current.authorization_id,
                    "run_id": current.run_id,
                    "session_id": session_id,
                }
            )
            try:
                outcome, run = self._store.claim_retry(
                    run_id=run_id,
                    session_id=session_id,
                    scope=(session_id, key),
                    content_hash=content_hash,
                    expected_attempt=current.attempt,
                    recorded_at=self._stamp(),
                )
            except ScientificRunStoreError:
                return _unusable(session_id)
            if outcome == "absent" or run is None:
                return ScientificRunReply("run_absent", _ABSENT_TEXT, session_id)
            if outcome == "conflict":
                return ScientificRunReply("idempotency_conflict", _KEY_TEXT, session_id, run)
            if outcome == "retry_not_allowed":
                return ScientificRunReply("retry_not_allowed", _RETRY_TEXT, session_id, run)
            if outcome != "retried":
                return self._describe(run, session_id)
            return ScientificRunReply("run_queued", RETRY_QUEUED_TEXT, session_id, run)

    def read(self, *, session_id: str, run_id: str) -> ScientificRunReply:
        try:
            _text(session_id, "invalid_session_id")
            if _RUN_ID.fullmatch(run_id) is None:
                raise ApplicationContractError("invalid_run_id")
        except ApplicationContractError:
            return _rejected(session_id)
        try:
            run = self._store.read_run(session_id, run_id)
        except ScientificRunStoreError:
            return _unusable(session_id)
        if run is None:
            return ScientificRunReply("run_absent", _ABSENT_TEXT, session_id)
        return ScientificRunReply("run_observed", _status_text(run), session_id, run)

    def list_runs(self, *, session_id: str) -> ScientificRunListReply:
        try:
            _text(session_id, "invalid_session_id")
        except ApplicationContractError:
            return ScientificRunListReply(
                "invalid_payload", _REJECTED_TEXT, session_id or "unknown"
            )
        try:
            runs = self._store.list_runs(session_id, limit=_HISTORY_LIMIT)
        except ScientificRunStoreError:
            return ScientificRunListReply("run_record_invalid", _UNUSABLE_TEXT, session_id)
        return ScientificRunListReply("runs_observed", _HISTORY_TEXT, session_id, runs)

    def read_artifact(
        self, *, session_id: str, run_id: str, artifact_id: str
    ) -> ScientificArtifactRecord | ScientificRunReply:
        try:
            _text(session_id, "invalid_session_id")
            if _RUN_ID.fullmatch(run_id) is None or type(artifact_id) is not str:
                raise ApplicationContractError("invalid_artifact_id")
        except ApplicationContractError:
            return _rejected(session_id)
        try:
            artifact, content = self._store.read_artifact(session_id, run_id, artifact_id)
            filename = artifact_filename(artifact)
        except ScientificRunStoreError as exc:
            return _artifact_error(session_id, exc.code)
        return ScientificArtifactRecord(artifact.media_type, filename, content)

    def startup(self) -> tuple[ScientificRun, ...]:
        """Recover abandoned attempts, then finish or drop provisional creates."""
        reconcile = getattr(self._executor, "reconcile", None)
        if callable(reconcile):
            reconcile(self._store, self._stamp())
        interrupted = self._store.recover_interrupted(recorded_at=self._stamp())
        with self._mutation:
            for pending in self._store.unconfirmed():
                self._finish(pending)
        return interrupted

    def process_next(self) -> ScientificRun | None:
        """Claim one queued attempt, then execute outside the store transaction."""
        if self._pending_failure is not None:
            stored = self._persist_safe_failure(self._pending_failure)
            if stored is None:
                return None
            self._pending_failure = None
            return stored
        running = self._store.claim_next_queued(recorded_at=self._stamp())
        if running is None:
            return None
        try:
            terminal, contents = self._execute(running)
        except Exception:  # noqa: BLE001 — fixed sentence, no driver or command text
            terminal, contents = (
                running.transition(
                    "failed",
                    recorded_at=running.updated_at,
                    text=self._failure_text(running),
                ),
                {},
            )
        try:
            outcome = self._store.commit_transition(
                terminal,
                expected_status="running",
                expected_attempt=running.attempt,
                contents=contents,
            )
        except ScientificRunStoreError:
            stored = self._persist_safe_failure(running)
            if stored is None:
                self._pending_failure = running
                return None
            return stored
        if outcome != "committed":
            return self._store.read_run(running.session_id, running.run_id) or terminal
        return terminal

    def close(self) -> None:
        self._store.close()

    def request_shutdown(self) -> None:
        shutdown = getattr(self._executor, "shutdown", None)
        if callable(shutdown):
            shutdown()

    def dispatch_gates(self) -> tuple[str, ...]:
        """Name unmet execution gates. This does not start a run."""

        blockers: list[str] = []
        if not self._execution_enabled or self._executor is None:
            blockers.append("scientific_execution_disabled")
        from cellertz.application.pdl1_host_deadline import MECHANISM, SystemdHostDeadline

        deadline = None if self._executor is None else getattr(self._executor, "supervisor", None)
        if type(deadline) is not SystemdHostDeadline or getattr(deadline, "mechanism", "") != MECHANISM:
            blockers.append("pdl1_worker_loss_deadline_unresolved")
        else:
            try:
                ready, missing = deadline.readiness()
            except Exception:  # noqa: BLE001 — readiness must not leak a host error
                ready, missing = False, ("host_deadline_service",)
            if ready is not True or missing:
                blockers.append("pdl1_deadline_unavailable")
        return tuple(dict.fromkeys(blockers))

    def _create_locked(
        self, session_id: str, authorization_id: str, key: str
    ) -> ScientificRunReply:
        content_hash = _digest({"authorization_id": authorization_id, "session_id": session_id})
        existing = self._store.read_by_authorization(session_id, authorization_id)
        if existing is not None:
            finished = existing if existing.confirmed else self._finish(existing)
            if finished is None:
                return ScientificRunReply(
                    "authorization_unavailable", _UNAVAILABLE_TEXT, session_id
                )
            outcome, run = self._store.claim_create(
                scope=(session_id, key),
                content_hash=content_hash,
                run=finished,
            )
            if outcome == "conflict":
                return ScientificRunReply("idempotency_conflict", _KEY_TEXT, session_id, run)
            return self._describe(run, session_id)
        record = self._current_record(session_id, authorization_id)
        if record is None or record.consumed_at is not None:
            return ScientificRunReply("authorization_unavailable", _UNAVAILABLE_TEXT, session_id)
        queued = self._queued_from(record)
        outcome, run = self._store.claim_create(
            scope=(session_id, key),
            content_hash=content_hash,
            run=queued,
        )
        if outcome == "conflict":
            return ScientificRunReply("idempotency_conflict", _KEY_TEXT, session_id, run)
        if outcome == "replay":
            finished = run if run.confirmed else self._finish(run)
            if finished is None:
                return ScientificRunReply(
                    "authorization_unavailable", _UNAVAILABLE_TEXT, session_id
                )
            return self._describe(finished, session_id)
        finished = self._finish(run)
        if finished is None:
            return ScientificRunReply("authorization_unavailable", _UNAVAILABLE_TEXT, session_id)
        return ScientificRunReply("run_queued", QUEUED_TEXT, session_id, finished)

    def _finish(self, run: ScientificRun) -> ScientificRun | None:
        if run.confirmed:
            return run
        record = self._authorizations.read(
            session_id=run.session_id, authorization_id=run.authorization_id
        ).authorization
        if (
            record is not None
            and record.consumed_at is not None
            and record.binding_hash == run.binding_hash
        ):
            confirmed = self._store.confirm(run.run_id)
            return confirmed
        if not self._authorizations.binding_still_matches(
            session_id=run.session_id, authorization_id=run.authorization_id
        ):
            self._store.delete_provisional(run.run_id)
            return None
        outcome = self._authorizations.claim_once(
            session_id=run.session_id, authorization_id=run.authorization_id
        )
        if outcome != "consumed":
            again = self._authorizations.read(
                session_id=run.session_id, authorization_id=run.authorization_id
            ).authorization
            if again is None or again.consumed_at is None or again.binding_hash != run.binding_hash:
                self._store.delete_provisional(run.run_id)
                return None
        confirmed = self._store.confirm(run.run_id)
        return confirmed

    def _current_record(
        self, session_id: str, authorization_id: str
    ) -> ScientificRunAuthorization | None:
        if not self._authorizations.binding_still_matches(
            session_id=session_id, authorization_id=authorization_id
        ):
            return None
        reply = self._authorizations.read(session_id=session_id, authorization_id=authorization_id)
        record = reply.authorization
        if record is None or record.consumed_at is not None or record.status != "authorized":
            return None
        if (
            record.profile_id not in _PROFILES
            or record.tool_name != "bindcraft"
            or record.target_scope != "PDL1"
        ):
            return None
        return record

    def _queued_from(self, record: ScientificRunAuthorization) -> ScientificRun:
        stamp = self._stamp()
        return ScientificRun(
            run_id=self._run_ids(),
            session_id=record.session_id,
            authorization_id=record.authorization_id,
            request_id=record.request_id,
            request_revision=record.request_revision,
            approval_id=record.approval_id,
            request_digest=record.request_digest,
            readiness_observation_id=record.readiness_observation_id,
            readiness_observed_at=record.readiness_observed_at,
            profile_id=record.profile_id,
            tool_name=record.tool_name,
            tool_version=record.tool_version,
            runtime_name=record.runtime_name,
            runtime_version=record.runtime_version,
            image_digest=record.image_digest,
            manifest_digest=record.manifest_digest,
            target_scope=record.target_scope,
            budget=dict(record.budget),
            binding_hash=record.binding_hash,
            status="queued",
            attempt=1,
            created_at=stamp,
            updated_at=stamp,
            events=(ScientificRunEvent(1, "queued", stamp, QUEUED_TEXT, 1),),
            confirmed=False,
        )

    def _execute(self, running: ScientificRun) -> tuple[ScientificRun, dict[str, bytes]]:
        if not self._bound(running):
            return (
                running.transition("failed", recorded_at=self._stamp(), text=FAILED_TEXT),
                {},
            )
        if not self._execution_enabled or self._executor is None:
            return (
                running.transition("failed", recorded_at=self._stamp(), text=DISABLED_TEXT),
                {},
            )
        result = self._executor.execute(running)
        if not self._bound(running):
            return (
                running.transition("failed", recorded_at=self._stamp(), text=FAILED_TEXT),
                {},
            )
        accepted = accept_outputs(running, result)
        if accepted is None or result.status != "completed":
            return (
                running.transition(
                    "failed", recorded_at=self._stamp(), text=self._failure_text(running)
                ),
                {},
            )
        contents = {
            artifact.artifact_id: item.body
            for item, artifact in zip(result.artifacts, accepted, strict=True)
        }
        completed = running.transition(
            "completed",
            recorded_at=self._stamp(),
            text=COMPLETED_TEXT,
            artifacts=accepted,
        )
        return completed, contents

    def _failure_text(self, run: ScientificRun) -> str:
        reader = getattr(self._store, "external_state", None)
        state = None if not callable(reader) else reader(run.run_id, run.attempt)
        return execution_failure_text(state)

    def _bound(self, run: ScientificRun) -> bool:
        if not self._authorizations.binding_still_matches(
            session_id=run.session_id, authorization_id=run.authorization_id
        ):
            return False
        reply = self._authorizations.read(
            session_id=run.session_id, authorization_id=run.authorization_id
        )
        record = reply.authorization
        if record is None or record.consumed_at is None:
            return False
        if run.binding_hash != binding_hash_for(run) or record.binding_hash != binding_hash_for(
            record
        ):
            return False
        live = binding_document(record)
        stored = binding_document(run)
        return (
            live == stored
            and live["request_id"] == run.request_id
            and live["approval_id"] == run.approval_id
            and live["readiness_observed_at"] == run.readiness_observed_at
            and same_document(live["budget"], run.budget)
        )

    def _persist_safe_failure(self, running: ScientificRun) -> ScientificRun | None:
        failed = running.transition("failed", recorded_at=running.updated_at, text=FAILED_TEXT)
        try:
            outcome = self._store.commit_transition(
                failed,
                expected_status="running",
                expected_attempt=running.attempt,
                contents={},
            )
        except ScientificRunStoreError:
            return None
        if outcome == "committed":
            return failed
        try:
            current = self._store.read_run(running.session_id, running.run_id)
        except ScientificRunStoreError:
            return None
        if current is not None and current.status != "running":
            return current
        return None

    def _describe(self, run: ScientificRun, session_id: str) -> ScientificRunReply:
        if run.status == "queued":
            return ScientificRunReply("run_queued", _status_text(run), session_id, run)
        return ScientificRunReply("run_observed", _status_text(run), session_id, run)

    def _stamp(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ApplicationContractError("naive_timestamp")
        return moment.astimezone(UTC).replace(tzinfo=UTC).isoformat()


def _rejected(session_id: str) -> ScientificRunReply:
    safe = session_id if type(session_id) is str and _TOKEN.fullmatch(session_id) else "unknown"
    return ScientificRunReply("invalid_payload", _REJECTED_TEXT, safe)


def _unusable(session_id: str) -> ScientificRunReply:
    return ScientificRunReply("run_record_invalid", _UNUSABLE_TEXT, session_id)


def _artifact_error(session_id: str, code: str) -> ScientificRunReply:
    absent = code in {"run_absent", "artifact_absent", "artifact_attempt_unavailable"}
    status = code if absent or code.startswith("artifact_") else "run_record_invalid"
    text = _ARTIFACT_ABSENT if absent or code.startswith("artifact_") else _UNUSABLE_TEXT
    return ScientificRunReply(status, text, session_id)
