"""Phase 6E fixture-only run lifecycle. Not scientific execution.

LocalProteinRunStarter.start_fixture_run is not used. It constructs
ProteinSupervisorV1, writes a protein-run idempotency record, writes
run state, and materializes candidate artifacts. Those effects are
scientific fixture execution.

This module records the fixture lifecycle for the
phase6d_fixture_only demonstration. Creation and retry only enqueue an
attempt. A server-owned worker claims and executes queued attempts:

    queued -> running -> completed
    queued -> running -> failed
    failed -> queued

The default store is process-local and non-durable. It is a prototype
application store, not scientific memory. A durable operational store
can replace it. That store is still not scientific memory. A completed
run means the fixture adapter finished. It is not scientific validation.

reviewer_id remains operator-asserted. This module does not add
production authentication.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from cellertz.application.approval import FIXTURE_PROFILE, HumanApprovalGate, canonical_digest
from cellertz.application.contracts import ApplicationContractError, UsageBudgetSnapshot, _text

_ALLOWED = frozenset(
    {
        ("queued", "running"),
        ("running", "completed"),
        ("running", "failed"),
        ("failed", "queued"),
    }
)
_SAFE_FAILURE = "The fixture run failed. No scientific action was taken."
_INTERRUPTED = "The fixture run was interrupted before completion. No scientific action was taken."
_COMPLETED_TEXT = "Fixture-only run completed. Not scientific validation."
_OBSERVED_TEXT = "Fixture-only run. Not scientific validation."
_QUEUED_TEXT = "Fixture run queued."
_RUNNING_TEXT = "Fixture runner started."
_RETRY_QUEUED_TEXT = "Fixture retry queued."
_EVENT_TEXTS = frozenset(
    {_QUEUED_TEXT, _RUNNING_TEXT, _RETRY_QUEUED_TEXT, _COMPLETED_TEXT, _SAFE_FAILURE, _INTERRUPTED}
)
_RETRY_TEXT = "Retry is not available for this fixture run."
_ABSENT_TEXT = "No fixture run matches this request."
_DEMO_TEXT = "A fixture demonstration receipt is required before a run is created."
_KEY_TEXT = "The idempotency key was reused with different content."
_LABEL = "fixture operational artifact"
_HISTORY_LIMIT = 50
_MAX_ARTIFACT_BYTES = 65536
_MEDIA_TYPES = frozenset({"application/json", "text/plain", "text/markdown"})
_ARTIFACT_KINDS = (
    "fixture_receipt",
    "fixture_manifest",
    "fixture_summary",
    "input_digest",
    "output_digest",
    "operational_log",
)
_ARTIFACT_NOTICES = (
    "fixture operational artifact",
    "not scientific validation",
    "no scientific action was taken",
    "no candidate protein was generated",
)
_MEDIA_SUFFIX = {
    "application/json": "json",
    "text/plain": "txt",
    "text/markdown": "md",
}


def _canonical(document: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return encoded.encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _history_limit(limit: int) -> int:
    if type(limit) is not int or limit < 1:
        raise ApprovedRunStoreError("run_record_invalid")
    return min(limit, _HISTORY_LIMIT)


def artifact_identity(run_id: str, artifact_id: str) -> tuple[int, str] | None:
    prefix = f"artifact_{run_id}_"
    if not artifact_id.startswith(prefix):
        return None
    attempt_text, separator, kind = artifact_id.removeprefix(prefix).partition("_")
    if (
        not separator
        or not attempt_text.isdigit()
        or attempt_text.startswith("0")
        or kind not in _ARTIFACT_KINDS
    ):
        return None
    return int(attempt_text), kind


_TEXT_CLASSIFICATION = (
    "fixture: true",
    "scientific_validation: false",
    "scientific_candidate: false",
)
_FILENAME = re.compile(r"fixture-[a-z_]+\.(?:json|txt|md)")


def _strict_json_object(text: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in items:
            if type(key) is not str or key in parsed:
                raise ValueError
            parsed[key] = value
        return parsed

    try:
        parsed = json.loads(text, object_pairs_hook=pairs)
    except (json.JSONDecodeError, ValueError, TypeError):
        raise ApprovedRunStoreError("artifact_invalid") from None
    if type(parsed) is not dict:
        raise ApprovedRunStoreError("artifact_invalid")
    encoded = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if encoded != text:
        raise ApprovedRunStoreError("artifact_invalid")
    return parsed


def verify_artifact_bytes(artifact: OperationalArtifact, content: bytes) -> dict[str, Any] | None:
    """Check bytes, digest, size, and media type. This does not read prose."""
    if type(content) is not bytes:
        raise ApprovedRunStoreError("artifact_invalid")
    if artifact.media_type not in _MEDIA_TYPES:
        raise ApprovedRunStoreError("artifact_media_type_rejected")
    if artifact.size_bytes > _MAX_ARTIFACT_BYTES or len(content) > _MAX_ARTIFACT_BYTES:
        raise ApprovedRunStoreError("artifact_too_large")
    if len(content) != artifact.size_bytes:
        raise ApprovedRunStoreError("artifact_size_mismatch")
    if _sha256(content) != artifact.content_sha256:
        raise ApprovedRunStoreError("artifact_digest_mismatch")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ApprovedRunStoreError("artifact_invalid") from None
    if artifact.media_type == "application/json":
        return _strict_json_object(text)
    return None


def require_fixture_classification(
    artifact: OperationalArtifact, content: bytes, parsed: dict[str, Any] | None
) -> None:
    if artifact.media_type == "application/json":
        if (
            not isinstance(parsed, dict)
            or parsed.get("fixture") is not True
            or parsed.get("scientific_validation") is not False
            or parsed.get("scientific_candidate") is not False
        ):
            raise ApprovedRunStoreError("artifact_invalid")
        return
    if artifact.media_type in {"text/plain", "text/markdown"}:
        lines = content.decode("utf-8").splitlines()
        if tuple(lines[:3]) != _TEXT_CLASSIFICATION:
            raise ApprovedRunStoreError("artifact_invalid")
        return
    raise ApprovedRunStoreError("artifact_media_type_rejected")


def artifact_filename(artifact: OperationalArtifact) -> str:
    suffix = _MEDIA_SUFFIX.get(artifact.media_type)
    if suffix is None or artifact.kind not in _ARTIFACT_KINDS:
        raise ApprovedRunStoreError("artifact_media_type_rejected")
    filename = f"fixture-{artifact.kind}.{suffix}"
    if _FILENAME.fullmatch(filename) is None:
        raise ApprovedRunStoreError("artifact_media_type_rejected")
    return filename


def _visible_artifact(run: ApprovedRun, artifact_id: str) -> OperationalArtifact:
    identity = artifact_identity(run.run_id, artifact_id)
    matches = [item for item in run.artifacts if item.artifact_id == artifact_id]
    if identity is None or len(matches) != 1:
        raise ApprovedRunStoreError("artifact_absent")
    artifact = matches[0]
    attempt, kind = identity
    if kind != artifact.kind:
        raise ApprovedRunStoreError("artifact_absent")
    if attempt != run.attempt:
        raise ApprovedRunStoreError("artifact_attempt_unavailable")
    return artifact


def _valid_fixture_package(run: ApprovedRun, artifacts: tuple[object, ...]) -> bool:
    if len(artifacts) != len(_ARTIFACT_KINDS):
        return False
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, OperationalArtifact) or item.kind in seen:
            return False
        identity = artifact_identity(run.run_id, item.artifact_id)
        if identity != (run.attempt, item.kind) or not item.content:
            return False
        try:
            parsed = verify_artifact_bytes(item, item.content)
            require_fixture_classification(item, item.content, parsed)
        except ApprovedRunStoreError:
            return False
        seen.add(item.kind)
    return seen == set(_ARTIFACT_KINDS)


def _prepare_artifact_bytes(
    run: ApprovedRun,
    contents: Mapping[str, bytes],
    existing: Mapping[str, bytes],
) -> dict[str, bytes]:
    prepared: dict[str, bytes] = {}
    current: list[OperationalArtifact] = []
    for artifact in run.artifacts:
        identity = artifact_identity(run.run_id, artifact.artifact_id)
        if identity is None or identity[1] != artifact.kind:
            raise ApprovedRunStoreError("artifact_invalid")
        if identity[0] != run.attempt:
            continue
        current.append(artifact)
    if run.status != "completed":
        if current or contents:
            raise ApprovedRunStoreError("artifact_invalid")
        return {}
    for artifact in current:
        content = contents.get(artifact.artifact_id)
        if content is None:
            raise ApprovedRunStoreError("artifact_absent")
        if artifact.artifact_id in existing or artifact.artifact_id in prepared:
            raise ApprovedRunStoreError("artifact_immutable")
        parsed = verify_artifact_bytes(artifact, content)
        require_fixture_classification(artifact, content, parsed)
        prepared[artifact.artifact_id] = content
    if len(prepared) != len(_ARTIFACT_KINDS):
        raise ApprovedRunStoreError("artifact_invalid")
    return prepared


def _key(value: object) -> str:
    if not isinstance(value, str):
        raise ApplicationContractError("invalid_idempotency_key")
    cleaned = value.strip()
    if not cleaned or any(char.isspace() for char in cleaned) or len(cleaned) > 200:
        raise ApplicationContractError("invalid_idempotency_key")
    return cleaned


@dataclass(frozen=True)
class RunEvent:
    sequence: int
    status: str
    recorded_at: str
    attempt: int
    text: str
    origin: str = "server"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "status": self.status,
            "recorded_at": self.recorded_at,
            "attempt": self.attempt,
            "text": self.text,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class OperationalArtifact:
    artifact_id: str
    kind: str
    content_sha256: str
    size_bytes: int
    media_type: str
    label: str = _LABEL
    content: bytes = b""

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "label": self.label,
            "fixture": True,
            "scientific_evidence": False,
            "scientific_candidate": False,
        }


@dataclass(frozen=True)
class ApprovedRun:
    run_id: str
    session_id: str
    approval_id: str
    request_id: str
    request_revision: int
    request_digest: str
    execution_profile: str
    status: str
    attempt: int
    created_at: str
    updated_at: str
    events: tuple[RunEvent, ...]
    artifacts: tuple[OperationalArtifact, ...]
    budget: dict[str, Any]
    usage: dict[str, Any]

    def transition(
        self,
        status: str,
        *,
        recorded_at: str,
        text: str,
        attempt: int | None = None,
        artifacts: tuple[OperationalArtifact, ...] = (),
    ) -> ApprovedRun:
        if (self.status, status) not in _ALLOWED:
            raise ApplicationContractError("invalid_run_transition")
        next_attempt = self.attempt if attempt is None else attempt
        if next_attempt < self.attempt:
            raise ApplicationContractError("attempt_decreased")
        event = RunEvent(
            sequence=len(self.events) + 1,
            status=status,
            recorded_at=recorded_at,
            attempt=next_attempt,
            text=text,
            origin="server",
        )
        return replace(
            self,
            status=status,
            attempt=next_attempt,
            updated_at=recorded_at,
            events=self.events + (event,),
            artifacts=self.artifacts + artifacts,
        )

    def mark_interrupted(self, *, recorded_at: str) -> ApprovedRun:
        """Server recovery only. queued or running becomes failed without a runner."""
        if (
            self.status not in {"queued", "running"}
            or type(recorded_at) is not str
            or not recorded_at
        ):
            raise ApplicationContractError("invalid_run_transition")
        event = RunEvent(
            sequence=len(self.events) + 1,
            status="failed",
            recorded_at=recorded_at,
            attempt=self.attempt,
            text=_INTERRUPTED,
            origin="server",
        )
        return replace(
            self,
            status="failed",
            updated_at=recorded_at,
            events=self.events + (event,),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "approval_id": self.approval_id,
            "request_id": self.request_id,
            "request_revision": self.request_revision,
            "request_digest": self.request_digest,
            "execution_profile": self.execution_profile,
            "status": self.status,
            "attempt": self.attempt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "events": [event.to_dict() for event in self.events],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "budget": self.budget,
            "usage": self.usage,
            "fixture": True,
            "scientific_validation": False,
            "scientific_execution": False,
            "label": "fixture-only run",
            "text": _status_text(self),
        }


@dataclass(frozen=True)
class ApprovedRunReply:
    status: str
    text: str
    session_id: str
    run: ApprovedRun | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "execution_authorized": False,
            "executable": False,
            "scientific_validation": False,
            "scientific_candidate": False,
            "candidates": [],
            "scientific_evidence": [],
            "scientific_memory_written": False,
            "semantic_promotion": False,
            "run": None if self.run is None else self.run.to_dict(),
        }


@dataclass(frozen=True)
class ApprovedRunSummary:
    run: ApprovedRun

    def to_dict(self) -> dict[str, Any]:
        visible = [
            artifact
            for artifact in self.run.artifacts
            if artifact_identity(self.run.run_id, artifact.artifact_id)
            == (self.run.attempt, artifact.kind)
        ]
        return {
            "run_id": self.run.run_id,
            "request_id": self.run.request_id,
            "approval_id": self.run.approval_id,
            "revision": self.run.request_revision,
            "status": self.run.status,
            "attempt": self.run.attempt,
            "created_at": self.run.created_at,
            "updated_at": self.run.updated_at,
            "text": _status_text(self.run),
            "artifact_count": len(visible),
            "fixture": True,
            "scientific_validation": False,
            "label": "fixture-only run",
        }


@dataclass(frozen=True)
class ApprovedRunListReply:
    status: str
    text: str
    session_id: str
    runs: tuple[ApprovedRun, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "execution_authorized": False,
            "executable": False,
            "scientific_validation": False,
            "scientific_candidate": False,
            "candidates": [],
            "scientific_evidence": [],
            "scientific_memory_written": False,
            "semantic_promotion": False,
            "label": "fixture-only run",
            "runs": [ApprovedRunSummary(run).to_dict() for run in self.runs],
        }


@dataclass(frozen=True)
class ApprovedArtifactRecord:
    media_type: str
    filename: str
    content: bytes


class ApprovedRunStoreError(Exception):
    """Stable operational-store code. It does not carry driver text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ApprovedRunStore(Protocol):
    def claim_create(
        self,
        *,
        scope: tuple[str, str],
        content_hash: str,
        approval_key: tuple[str, str],
        run: ApprovedRun,
    ) -> tuple[str, ApprovedRun]: ...

    def claim_retry(
        self,
        *,
        run_id: str,
        session_id: str,
        scope: tuple[str, str],
        content_hash: str,
        expected_attempt: int,
        recorded_at: str,
    ) -> tuple[str, ApprovedRun | None]: ...

    def claim_next_queued(self, *, recorded_at: str) -> ApprovedRun | None: ...

    def commit_transition(
        self,
        run: ApprovedRun,
        *,
        expected_status: str,
        expected_attempt: int,
    ) -> str: ...

    def read_run(self, session_id: str, run_id: str) -> ApprovedRun | None: ...

    def list_runs(self, session_id: str, *, limit: int) -> tuple[ApprovedRun, ...]: ...

    def read_artifact(
        self, session_id: str, run_id: str, artifact_id: str
    ) -> tuple[OperationalArtifact, bytes]: ...

    def recover_interrupted(self, *, recorded_at: str) -> tuple[ApprovedRun, ...]: ...

    def close(self) -> None: ...


class InMemoryApprovedRunStore:
    """Process-local and non-durable. Not scientific memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, ApprovedRun] = {}
        self._by_scope: dict[tuple[str, str], tuple[str, str]] = {}
        self._by_approval: dict[tuple[str, str], str] = {}
        self._retry_scope: dict[tuple[str, str], tuple[str, ApprovedRun | None, int, str]] = {}
        self._artifact_bytes: dict[str, bytes] = {}

    def claim_create(
        self,
        *,
        scope: tuple[str, str],
        content_hash: str,
        approval_key: tuple[str, str],
        run: ApprovedRun,
    ) -> tuple[str, ApprovedRun]:
        with self._lock:
            found = self._by_scope.get(scope)
            if found is not None:
                prior_hash, run_id = found
                return ("replay" if prior_hash == content_hash else "conflict"), self._runs[run_id]
            existing = self._by_approval.get(approval_key)
            if existing is not None:
                self._by_scope[scope] = (content_hash, existing)
                return "replay", self._runs[existing]
            _require_new_claim(run, scope, approval_key)
            self._runs[run.run_id] = run
            self._by_scope[scope] = (content_hash, run.run_id)
            self._by_approval[approval_key] = run.run_id
            return "created", run

    def claim_retry(
        self,
        *,
        run_id: str,
        session_id: str,
        scope: tuple[str, str],
        content_hash: str,
        expected_attempt: int,
        recorded_at: str,
    ) -> tuple[str, ApprovedRun | None]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.session_id != session_id:
                return "absent", None
            prior = self._retry_scope.get(scope)
            if prior is not None:
                return _retry_replay(prior, content_hash, run)
            if run.status != "failed" or run.attempt != expected_attempt:
                return "retry_not_allowed", run
            queued = run.transition(
                "queued",
                recorded_at=recorded_at,
                text=_RETRY_QUEUED_TEXT,
                attempt=run.attempt + 1,
            )
            self._runs[run_id] = queued
            self._retry_scope[scope] = (content_hash, None, queued.attempt, run_id)
            return "retried", queued

    def claim_next_queued(self, *, recorded_at: str) -> ApprovedRun | None:
        with self._lock:
            queued = next((run for run in self._runs.values() if run.status == "queued"), None)
            if queued is None:
                return None
            running = freeze_run(
                queued.transition("running", recorded_at=recorded_at, text=_RUNNING_TEXT)
            )
            self._runs[queued.run_id] = running
            return running

    def commit_transition(
        self,
        run: ApprovedRun,
        *,
        expected_status: str,
        expected_attempt: int,
    ) -> str:
        with self._lock:
            current = self._runs.get(run.run_id)
            if (
                current is None
                or current.session_id != run.session_id
                or current.status != expected_status
                or current.attempt != expected_attempt
            ):
                return "lost"
            contents = {item.artifact_id: item.content for item in run.artifacts if item.content}
            frozen = freeze_run(run)
            prepared = _prepare_artifact_bytes(frozen, contents, self._artifact_bytes)
            self._runs[run.run_id] = frozen
            self._artifact_bytes.update(prepared)
            if frozen.status in {"completed", "failed"}:
                self._seal_open_retries(frozen)
            return "committed"

    def read_run(self, session_id: str, run_id: str) -> ApprovedRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.session_id != session_id:
                return None
            return run

    def list_runs(self, session_id: str, *, limit: int) -> tuple[ApprovedRun, ...]:
        bounded = _history_limit(limit)
        with self._lock:
            owned = [run for run in self._runs.values() if run.session_id == session_id]
            owned.sort(key=lambda run: (run.updated_at, run.run_id), reverse=True)
            return tuple(owned[:bounded])

    def read_artifact(
        self, session_id: str, run_id: str, artifact_id: str
    ) -> tuple[OperationalArtifact, bytes]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.session_id != session_id:
                raise ApprovedRunStoreError("run_absent")
            artifact = _visible_artifact(run, artifact_id)
            content = self._artifact_bytes.get(artifact_id)
            if content is None:
                raise ApprovedRunStoreError("artifact_absent")
            parsed = verify_artifact_bytes(artifact, content)
            require_fixture_classification(artifact, content, parsed)
            return artifact, content

    def recover_interrupted(self, *, recorded_at: str) -> tuple[ApprovedRun, ...]:
        with self._lock:
            recovered: list[ApprovedRun] = []
            for run_id, run in list(self._runs.items()):
                if run.status != "running":
                    continue
                updated = freeze_run(run.mark_interrupted(recorded_at=recorded_at))
                self._runs[run_id] = updated
                self._seal_open_retries(updated)
                recovered.append(updated)
            return tuple(recovered)

    def close(self) -> None:
        with self._lock:
            pass

    def _seal_open_retries(self, run: ApprovedRun) -> None:
        for scope, prior in list(self._retry_scope.items()):
            content_hash, snapshot, attempt, run_id = prior
            if snapshot is None and attempt == run.attempt and run_id == run.run_id:
                self._retry_scope[scope] = (content_hash, run, attempt, run_id)


class ApprovedRunRunner(Protocol):
    def run(
        self,
        *,
        run_id: str,
        attempt: int,
        request_digest: str,
    ) -> tuple[OperationalArtifact, ...]: ...


class FixtureApprovedRunRunner:
    """Deterministic fixture adapter. No process, network, tool, or memory write."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.calls = 0
        self.error = error

    def run(
        self,
        *,
        run_id: str,
        attempt: int,
        request_digest: str,
    ) -> tuple[OperationalArtifact, ...]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _fixture_artifacts(run_id, attempt, request_digest)


def _artifact(
    run_id: str, attempt: int, kind: str, payload: bytes, media_type: str
) -> OperationalArtifact:
    return OperationalArtifact(
        artifact_id=f"artifact_{run_id}_{attempt}_{kind}",
        kind=kind,
        content_sha256=_sha256(payload),
        size_bytes=len(payload),
        media_type=media_type,
        content=payload,
    )


def _fixture_notice() -> dict[str, Any]:
    return {
        "fixture": True,
        "scientific_validation": False,
        "scientific_candidate": False,
        "classification": "fixture operational artifact",
        "validation": "not scientific validation",
        "action": "no scientific action was taken",
        "candidate": "no candidate protein was generated",
    }


def _fixture_text(heading: str, *lines: str) -> bytes:
    body = "\n".join((*_TEXT_CLASSIFICATION, heading, *_ARTIFACT_NOTICES, *lines, ""))
    return body.encode("utf-8")


def _fixture_artifacts(
    run_id: str, attempt: int, request_digest: str
) -> tuple[OperationalArtifact, ...]:
    notice = _fixture_notice()
    receipt = _canonical(
        {
            **notice,
            "run_id": run_id,
            "attempt": attempt,
            "execution_profile": FIXTURE_PROFILE,
            "scientific_execution": False,
            "scientific_tool_calls": 0,
        }
    )
    manifest = _canonical(
        {
            **notice,
            "run_id": run_id,
            "attempt": attempt,
            "request_digest": request_digest,
            "runner": "phase6e_fixture_only",
            "scientific_tool_calls": 0,
        }
    )
    summary = _fixture_text(
        "# Fixture-only run",
        "Operational artifacts.",
        "Not a scientific candidate.",
    )
    digest = _canonical({**notice, "request_digest": request_digest})
    output = _canonical({**notice, "output_sha256": _sha256(manifest), "scientific_tool_calls": 0})
    log = _fixture_text("Fixture runner finished.", "No scientific tool was called.")
    specs = (
        ("fixture_receipt", receipt, "application/json"),
        ("fixture_manifest", manifest, "application/json"),
        ("fixture_summary", summary, "text/markdown"),
        ("input_digest", digest, "application/json"),
        ("output_digest", output, "application/json"),
        ("operational_log", log, "text/plain"),
    )
    return tuple(_artifact(run_id, attempt, kind, payload, media) for kind, payload, media in specs)


def _status_text(run: ApprovedRun) -> str:
    if run.status == "completed":
        return _COMPLETED_TEXT
    if run.status == "failed":
        if run.events and run.events[-1].text == _INTERRUPTED:
            return _INTERRUPTED
        return _SAFE_FAILURE
    return _OBSERVED_TEXT


def _require_new_claim(
    run: ApprovedRun, scope: tuple[str, str], approval_key: tuple[str, str]
) -> None:
    if (
        run.status != "queued"
        or run.attempt != 1
        or len(run.events) != 1
        or run.events[0].text != _QUEUED_TEXT
        or run.session_id != scope[0]
        or run.session_id != approval_key[0]
        or run.approval_id != approval_key[1]
    ):
        raise ApplicationContractError("invalid_run_claim")


def _retry_replay(
    prior: tuple[str, ApprovedRun | None, int, str],
    content_hash: str,
    run: ApprovedRun,
) -> tuple[str, ApprovedRun | None]:
    prior_hash, snapshot, attempt, run_id = prior
    if prior_hash != content_hash:
        return "conflict", snapshot if snapshot is not None else run
    if snapshot is not None:
        return "replay", snapshot
    if run.run_id != run_id or run.attempt != attempt:
        raise ApprovedRunStoreError("run_record_invalid")
    return "replay", run


def freeze_run(run: ApprovedRun) -> ApprovedRun:
    return run_from_document(run_document(run))


def run_document(run: ApprovedRun) -> str:
    return json.dumps(run.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def run_from_document(payload: object) -> ApprovedRun:
    if type(payload) is not str:
        raise ApprovedRunStoreError("run_record_invalid")
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        raise ApprovedRunStoreError("run_record_invalid") from None
    if type(document) is not dict:
        raise ApprovedRunStoreError("run_record_invalid")
    expected = {
        "run_id",
        "session_id",
        "approval_id",
        "request_id",
        "request_revision",
        "request_digest",
        "execution_profile",
        "status",
        "attempt",
        "created_at",
        "updated_at",
        "events",
        "artifacts",
        "budget",
        "usage",
        "fixture",
        "scientific_validation",
        "scientific_execution",
        "label",
        "text",
    }
    if set(document) != expected:
        raise ApprovedRunStoreError("run_record_invalid")
    run_id = _doc_text(document["run_id"])
    if not run_id.startswith("run_") or len(run_id) != 36:
        raise ApprovedRunStoreError("run_record_invalid")
    status = _doc_text(document["status"])
    if status not in {"queued", "running", "completed", "failed"}:
        raise ApprovedRunStoreError("run_record_invalid")
    if document["execution_profile"] != FIXTURE_PROFILE or document["label"] != "fixture-only run":
        raise ApprovedRunStoreError("run_record_invalid")
    if document["fixture"] is not True or document["scientific_validation"] is not False:
        raise ApprovedRunStoreError("run_record_invalid")
    if document["scientific_execution"] is not False:
        raise ApprovedRunStoreError("run_record_invalid")
    events = _events(document["events"])
    attempt = _doc_int(document["attempt"])
    if attempt < 1 or not events or events[-1].status != status or events[-1].attempt != attempt:
        raise ApprovedRunStoreError("run_record_invalid")
    _validate_history(events)
    run = ApprovedRun(
        run_id=run_id,
        session_id=_doc_text(document["session_id"]),
        approval_id=_doc_text(document["approval_id"]),
        request_id=_doc_text(document["request_id"]),
        request_revision=_doc_int(document["request_revision"]),
        request_digest=_digest(document["request_digest"]),
        execution_profile=FIXTURE_PROFILE,
        status=status,
        attempt=attempt,
        created_at=_doc_text(document["created_at"]),
        updated_at=_doc_text(document["updated_at"]),
        events=events,
        artifacts=_artifacts(document["artifacts"]),
        budget=_budget(document["budget"]),
        usage=_usage_document(document["usage"]),
    )
    if document["text"] != _status_text(run) or run.usage["session_id"] != run.session_id:
        raise ApprovedRunStoreError("run_record_invalid")
    if payload != run_document(run):
        raise ApprovedRunStoreError("run_record_invalid")
    return run


def _validate_history(events: tuple[RunEvent, ...]) -> None:
    if events[0].status != "queued" or events[0].attempt != 1 or events[0].text != _QUEUED_TEXT:
        raise ApprovedRunStoreError("run_record_invalid")
    for previous, event in itertools.pairwise(events):
        change = (previous.status, event.status)
        if change == ("queued", "running"):
            valid = event.attempt == previous.attempt and event.text == _RUNNING_TEXT
        elif change == ("running", "completed"):
            valid = event.attempt == previous.attempt and event.text == _COMPLETED_TEXT
        elif change == ("running", "failed"):
            valid = event.attempt == previous.attempt and event.text in {
                _SAFE_FAILURE,
                _INTERRUPTED,
            }
        elif change == ("queued", "failed"):
            valid = event.attempt == previous.attempt and event.text == _INTERRUPTED
        elif change == ("failed", "queued"):
            valid = event.attempt == previous.attempt + 1 and event.text == _RETRY_QUEUED_TEXT
        else:
            valid = False
        if not valid:
            raise ApprovedRunStoreError("run_record_invalid")


def _doc_text(value: object) -> str:
    if type(value) is not str or not value or any(char.isspace() for char in value):
        raise ApprovedRunStoreError("run_record_invalid")
    return value


def _doc_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ApprovedRunStoreError("run_record_invalid")
    return value


def _digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ApprovedRunStoreError("run_record_invalid")
    return value


def _events(value: object) -> tuple[RunEvent, ...]:
    if type(value) is not list or not value:
        raise ApprovedRunStoreError("run_record_invalid")
    events: list[RunEvent] = []
    for index, item in enumerate(value, start=1):
        if type(item) is not dict or set(item) != {
            "sequence",
            "status",
            "recorded_at",
            "attempt",
            "text",
            "origin",
        }:
            raise ApprovedRunStoreError("run_record_invalid")
        if (
            item["sequence"] != index
            or item["origin"] != "server"
            or item["text"] not in _EVENT_TEXTS
        ):
            raise ApprovedRunStoreError("run_record_invalid")
        if item["status"] not in {"queued", "running", "completed", "failed"}:
            raise ApprovedRunStoreError("run_record_invalid")
        events.append(
            RunEvent(
                sequence=index,
                status=item["status"],
                recorded_at=_doc_text(item["recorded_at"]),
                attempt=_doc_int(item["attempt"]),
                text=item["text"],
                origin="server",
            )
        )
    return tuple(events)


def _artifacts(value: object) -> tuple[OperationalArtifact, ...]:
    if type(value) is not list:
        raise ApprovedRunStoreError("run_record_invalid")
    artifacts: list[OperationalArtifact] = []
    for item in value:
        if type(item) is not dict or set(item) != {
            "artifact_id",
            "kind",
            "content_sha256",
            "size_bytes",
            "media_type",
            "label",
            "fixture",
            "scientific_evidence",
            "scientific_candidate",
        }:
            raise ApprovedRunStoreError("run_record_invalid")
        if item["fixture"] is not True or item["scientific_evidence"] is not False:
            raise ApprovedRunStoreError("run_record_invalid")
        if item["scientific_candidate"] is not False or item["label"] != _LABEL:
            raise ApprovedRunStoreError("run_record_invalid")
        kind = _doc_text(item["kind"])
        media_type = _doc_text(item["media_type"])
        if kind not in _ARTIFACT_KINDS or media_type not in _MEDIA_TYPES:
            raise ApprovedRunStoreError("run_record_invalid")
        size_bytes = _doc_int(item["size_bytes"])
        if size_bytes > _MAX_ARTIFACT_BYTES:
            raise ApprovedRunStoreError("artifact_too_large")
        artifacts.append(
            OperationalArtifact(
                artifact_id=_doc_text(item["artifact_id"]),
                kind=kind,
                content_sha256=_digest(item["content_sha256"]),
                size_bytes=size_bytes,
                media_type=media_type,
            )
        )
    return tuple(artifacts)


def _budget(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "token_ceiling",
        "model_cost_ceiling",
        "scientific_tool_cost_ceiling",
        "warning_threshold",
        "action_on_limit",
        "human_override_required",
        "currency",
        "authorizes_execution",
    }:
        raise ApprovedRunStoreError("run_record_invalid")
    if value["authorizes_execution"] is not False or value["action_on_limit"] not in {
        "warn",
        "stop",
    }:
        raise ApprovedRunStoreError("run_record_invalid")
    if type(value["human_override_required"]) is not bool:
        raise ApprovedRunStoreError("run_record_invalid")
    return {
        "token_ceiling": _optional_int(value["token_ceiling"]),
        "model_cost_ceiling": _optional_number(value["model_cost_ceiling"]),
        "scientific_tool_cost_ceiling": _optional_number(value["scientific_tool_cost_ceiling"]),
        "warning_threshold": _optional_number(value["warning_threshold"]),
        "action_on_limit": value["action_on_limit"],
        "human_override_required": value["human_override_required"],
        "currency": None if value["currency"] is None else _doc_text(value["currency"]),
        "authorizes_execution": False,
    }


def _usage_document(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "session_id",
        "estimated_model_cost",
        "scientific_infrastructure_cost",
        "currency",
        "model_cost_known",
        "infrastructure_cost_known",
        "observed_model_calls",
        "observed_scientific_tool_calls",
        "observed_model_cost",
        "observed_scientific_tool_cost",
        "observed_costs_known",
        "authorizes_execution",
    }:
        raise ApprovedRunStoreError("run_record_invalid")
    if value["authorizes_execution"] is not False or value["observed_costs_known"] is not True:
        raise ApprovedRunStoreError("run_record_invalid")
    if value["observed_model_calls"] != 0 or value["observed_scientific_tool_calls"] != 0:
        raise ApprovedRunStoreError("run_record_invalid")
    if value["observed_model_cost"] != 0 or value["observed_scientific_tool_cost"] != 0:
        raise ApprovedRunStoreError("run_record_invalid")
    if (
        type(value["model_cost_known"]) is not bool
        or type(value["infrastructure_cost_known"]) is not bool
    ):
        raise ApprovedRunStoreError("run_record_invalid")
    return {
        "session_id": _doc_text(value["session_id"]),
        "estimated_model_cost": _optional_number(value["estimated_model_cost"]),
        "scientific_infrastructure_cost": _optional_number(value["scientific_infrastructure_cost"]),
        "currency": None if value["currency"] is None else _doc_text(value["currency"]),
        "model_cost_known": value["model_cost_known"],
        "infrastructure_cost_known": value["infrastructure_cost_known"],
        "observed_model_calls": 0,
        "observed_scientific_tool_calls": 0,
        "observed_model_cost": 0,
        "observed_scientific_tool_cost": 0,
        "observed_costs_known": True,
        "authorizes_execution": False,
    }


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _doc_int(value)


def _optional_number(value: object) -> int | float | None:
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise ApprovedRunStoreError("run_record_invalid")
    return value


def _usage(session_id: str) -> dict[str, Any]:
    snapshot = UsageBudgetSnapshot(
        session_id=session_id,
        estimated_model_cost=None,
        scientific_infrastructure_cost=None,
        currency=None,
    )
    observed = snapshot.to_dict()
    observed.update(
        {
            "observed_model_calls": 0,
            "observed_scientific_tool_calls": 0,
            "observed_model_cost": 0,
            "observed_scientific_tool_cost": 0,
            "observed_costs_known": True,
            "authorizes_execution": False,
        }
    )
    return observed


class ApprovedRunService:
    """Queues, processes, and reads one fixture run per approval."""

    def __init__(
        self,
        gate: HumanApprovalGate,
        store: ApprovedRunStore | None = None,
        runner: ApprovedRunRunner | None = None,
        clock: Callable[[], datetime] | None = None,
        run_ids: Callable[[], str] | None = None,
    ) -> None:
        self._gate = gate
        self._store = store if store is not None else InMemoryApprovedRunStore()
        self._runner = runner if runner is not None else FixtureApprovedRunRunner()
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._run_ids = run_ids or (lambda: f"run_{uuid4().hex}")

    def create(
        self, *, session_id: str, approval_id: str, idempotency_key: str
    ) -> ApprovedRunReply:
        try:
            _text(session_id, "invalid_session_id")
            _text(approval_id, "invalid_approval_id")
            key = _key(idempotency_key)
        except ApplicationContractError:
            return ApprovedRunReply(
                "invalid_payload", "The run payload was rejected.", session_id or "unknown"
            )
        blocked, approval, demonstration = self._revalidate(session_id, approval_id)
        if blocked is not None or approval is None or demonstration is None:
            return blocked or ApprovedRunReply("approval_absent", _ABSENT_TEXT, session_id)
        content_hash = canonical_digest(
            {
                "session_id": session_id,
                "approval_id": approval.approval_id,
                "request_id": approval.request_id,
                "request_revision": approval.request_revision,
                "request_digest": approval.revision_digest,
                "execution_profile": FIXTURE_PROFILE,
            }
        )

        queued = self._queued_new(approval, session_id, demonstration.budget)
        try:
            outcome, run = self._store.claim_create(
                scope=(session_id, key),
                content_hash=content_hash,
                approval_key=(session_id, approval.approval_id),
                run=queued,
            )
        except ApprovedRunStoreError:
            return self._unusable(session_id)
        if outcome == "conflict":
            return ApprovedRunReply("idempotency_conflict", _KEY_TEXT, session_id, run)
        if outcome != "created":
            return self._describe(run, session_id)
        return ApprovedRunReply("run_queued", _QUEUED_TEXT, session_id, run)

    def retry(self, *, session_id: str, run_id: str, idempotency_key: str) -> ApprovedRunReply:
        try:
            _text(session_id, "invalid_session_id")
            _text(run_id, "invalid_run_id")
            key = _key(idempotency_key)
        except ApplicationContractError:
            return ApprovedRunReply(
                "invalid_payload", "The retry payload was rejected.", session_id or "unknown"
            )
        try:
            current = self._store.read_run(session_id, run_id)
        except ApprovedRunStoreError:
            return self._unusable(session_id)
        if current is None:
            return ApprovedRunReply("run_absent", _ABSENT_TEXT, session_id)
        blocked, _approval, _demonstration = self._revalidate(session_id, current.approval_id)
        if blocked is not None:
            return blocked
        content_hash = canonical_digest(
            {
                "session_id": session_id,
                "run_id": current.run_id,
                "approval_id": current.approval_id,
                "request_id": current.request_id,
                "request_revision": current.request_revision,
                "request_digest": current.request_digest,
                "execution_profile": FIXTURE_PROFILE,
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
        except ApprovedRunStoreError:
            return self._unusable(session_id)
        if outcome == "absent" or run is None:
            return ApprovedRunReply("run_absent", _ABSENT_TEXT, session_id)
        if outcome == "conflict":
            return ApprovedRunReply("idempotency_conflict", _KEY_TEXT, session_id, run)
        if outcome == "retry_not_allowed":
            return ApprovedRunReply("retry_not_allowed", _RETRY_TEXT, session_id, run)
        if outcome != "retried":
            return self._describe(run, session_id)
        return ApprovedRunReply("run_queued", _RETRY_QUEUED_TEXT, session_id, run)

    def read(self, *, session_id: str, run_id: str) -> ApprovedRunReply:
        try:
            _text(session_id, "invalid_session_id")
            _text(run_id, "invalid_run_id")
        except ApplicationContractError:
            return ApprovedRunReply(
                "invalid_payload", "The run query was rejected.", session_id or "unknown"
            )
        try:
            run = self._store.read_run(session_id, run_id)
        except ApprovedRunStoreError:
            return self._unusable(session_id)
        if run is None:
            return ApprovedRunReply("run_absent", _ABSENT_TEXT, session_id)
        return ApprovedRunReply("run_observed", _status_text(run), session_id, run)

    def list_runs(self, *, session_id: str) -> ApprovedRunListReply:
        try:
            _text(session_id, "invalid_session_id")
        except ApplicationContractError:
            return ApprovedRunListReply(
                "invalid_payload", "The run query was rejected.", session_id or "unknown"
            )
        try:
            runs = self._store.list_runs(session_id, limit=_HISTORY_LIMIT)
        except ApprovedRunStoreError:
            return ApprovedRunListReply(
                "run_record_invalid",
                "The fixture run record could not be used.",
                session_id,
            )
        return ApprovedRunListReply(
            "runs_observed",
            "Fixture-only runs. Not scientific validation.",
            session_id,
            runs,
        )

    def read_artifact(
        self, *, session_id: str, run_id: str, artifact_id: str
    ) -> ApprovedArtifactRecord | ApprovedRunReply:
        try:
            _text(session_id, "invalid_session_id")
            _text(run_id, "invalid_run_id")
            _text(artifact_id, "invalid_artifact_id")
        except ApplicationContractError:
            return ApprovedRunReply(
                "invalid_payload", "The artifact query was rejected.", session_id or "unknown"
            )
        try:
            artifact, content = self._store.read_artifact(session_id, run_id, artifact_id)
        except ApprovedRunStoreError as exc:
            return self._artifact_error(session_id, exc.code)
        try:
            filename = artifact_filename(artifact)
        except ApprovedRunStoreError as exc:
            return self._artifact_error(session_id, exc.code)
        return ApprovedArtifactRecord(artifact.media_type, filename, content)

    def recover_interrupted(self) -> tuple[ApprovedRun, ...]:
        """Mark only interrupted running attempts failed. Does not call the runner.

        The durable store accepts this only from the process that owns the
        database. A second process cannot open that database.
        """
        return self._store.recover_interrupted(recorded_at=self._stamp())

    def process_next(self) -> ApprovedRun | None:
        """Claim and finish at most one queued attempt.

        This method is for the server-owned worker. HTTP handlers never call it.
        The running transition is durable before the fixture adapter is invoked.
        """
        running = self._store.claim_next_queued(recorded_at=self._stamp())
        if running is None:
            return None
        try:
            terminal = (
                self._finish_attempt(running)
                if self._authorized_for_run(running)
                else running.transition("failed", recorded_at=self._stamp(), text=_SAFE_FAILURE)
            )
        except Exception:  # noqa: BLE001 — claimed work fails closed with the fixed sentence
            terminal = running.transition(
                "failed", recorded_at=running.updated_at, text=_SAFE_FAILURE
            )
        try:
            outcome = self._store.commit_transition(
                terminal,
                expected_status="running",
                expected_attempt=running.attempt,
            )
        except ApprovedRunStoreError:
            return self._commit_safe_failure(running)
        if outcome != "committed":
            return self._store.read_run(running.session_id, running.run_id) or terminal
        return terminal

    def _commit_safe_failure(self, running: ApprovedRun) -> ApprovedRun:
        """Record the fixed failure once the completed transaction has rolled back.

        At most two writes. This does not call itself.
        """
        failed = running.transition("failed", recorded_at=running.updated_at, text=_SAFE_FAILURE)
        for _ in range(2):
            try:
                if (
                    self._store.commit_transition(
                        failed,
                        expected_status="running",
                        expected_attempt=running.attempt,
                    )
                    == "committed"
                ):
                    return failed
            except ApprovedRunStoreError:
                continue
            break
        try:
            observed = self._store.read_run(running.session_id, running.run_id)
        except ApprovedRunStoreError:
            return failed
        if observed is not None and observed.status != "running":
            return observed
        return failed

    def close(self) -> None:
        self._store.close()

    def _revalidate(
        self, session_id: str, approval_id: str
    ) -> tuple[ApprovedRunReply | None, Any, Any]:
        prepared = self._gate.preflight(session_id=session_id, approval_id=approval_id)
        if prepared.status != "preflight_ready" or prepared.approval is None:
            return ApprovedRunReply(prepared.status, prepared.text, session_id), None, None
        demonstration = self._gate.recorded_demonstration(
            session_id=session_id,
            approval_id=approval_id,
        )
        if demonstration is None or demonstration.execution_profile != FIXTURE_PROFILE:
            return ApprovedRunReply("demonstration_absent", _DEMO_TEXT, session_id), None, None
        if demonstration.approval_id != prepared.approval.approval_id:
            return ApprovedRunReply("demonstration_absent", _DEMO_TEXT, session_id), None, None
        return None, prepared.approval, demonstration

    def _authorized_for_run(self, run: ApprovedRun) -> bool:
        try:
            blocked, approval, demonstration = self._revalidate(run.session_id, run.approval_id)
        except Exception:  # noqa: BLE001 — fail closed before invoking the runner
            return False
        return bool(
            blocked is None
            and approval is not None
            and demonstration is not None
            and approval.approval_id == run.approval_id
            and approval.request_id == run.request_id
            and approval.request_revision == run.request_revision
            and approval.revision_digest == run.request_digest
            and demonstration.approval_id == run.approval_id
            and demonstration.request_digest == run.request_digest
            and demonstration.execution_profile == run.execution_profile
            and dict(demonstration.budget) == run.budget
        )

    def _queued_new(self, approval: Any, session_id: str, budget: dict[str, Any]) -> ApprovedRun:
        stamp = self._stamp()
        queued = RunEvent(1, "queued", stamp, 1, _QUEUED_TEXT, "server")
        return ApprovedRun(
            run_id=self._run_ids(),
            session_id=session_id,
            approval_id=approval.approval_id,
            request_id=approval.request_id,
            request_revision=approval.request_revision,
            request_digest=approval.revision_digest,
            execution_profile=FIXTURE_PROFILE,
            status="queued",
            attempt=1,
            created_at=stamp,
            updated_at=stamp,
            events=(queued,),
            artifacts=(),
            budget=dict(budget),
            usage=_usage(session_id),
        )

    def _finish_attempt(self, run: ApprovedRun) -> ApprovedRun:
        try:
            artifacts = self._runner.run(
                run_id=run.run_id,
                attempt=run.attempt,
                request_digest=run.request_digest,
            )
        except Exception:  # noqa: BLE001 — the stored text stays fixed and safe
            return run.transition("failed", recorded_at=self._stamp(), text=_SAFE_FAILURE)
        if not isinstance(artifacts, tuple) or not _valid_fixture_package(run, artifacts):
            return run.transition("failed", recorded_at=self._stamp(), text=_SAFE_FAILURE)
        return run.transition(
            "completed",
            recorded_at=self._stamp(),
            text=_COMPLETED_TEXT,
            artifacts=artifacts,
        )

    def _stamp(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None:
            raise ApplicationContractError("naive_timestamp")
        return moment.astimezone(UTC).isoformat()

    def _describe(self, run: ApprovedRun, session_id: str) -> ApprovedRunReply:
        if run.status == "failed":
            return ApprovedRunReply("run_failed", _status_text(run), session_id, run)
        if run.status == "completed":
            return ApprovedRunReply("run_completed", _COMPLETED_TEXT, session_id, run)
        if run.status == "queued":
            return ApprovedRunReply("run_queued", run.events[-1].text, session_id, run)
        if run.status == "running":
            return ApprovedRunReply("run_running", _RUNNING_TEXT, session_id, run)
        return ApprovedRunReply("run_observed", _OBSERVED_TEXT, session_id, run)

    def _unusable(self, session_id: str) -> ApprovedRunReply:
        return ApprovedRunReply(
            "run_record_invalid",
            "The fixture run record could not be used.",
            session_id,
        )

    def _artifact_error(self, session_id: str, code: str) -> ApprovedRunReply:
        messages = {
            "run_absent": _ABSENT_TEXT,
            "artifact_absent": "The operational artifact is not available.",
            "artifact_attempt_unavailable": "The operational artifact is not available for this attempt.",
            "artifact_invalid": "The operational artifact could not be read.",
            "artifact_digest_mismatch": "The operational artifact could not be read.",
            "artifact_size_mismatch": "The operational artifact could not be read.",
            "artifact_media_type_rejected": "The operational artifact could not be read.",
            "artifact_too_large": "The operational artifact could not be read.",
            "artifact_immutable": "The operational artifact could not be read.",
            "unsupported_approved_run_schema": "The fixture run record could not be used.",
            "approved_run_store_unavailable": "The fixture run record could not be used.",
            "approved_run_database_unreadable": "The fixture run record could not be used.",
            "run_record_invalid": "The fixture run record could not be used.",
        }
        status = code if code in messages else "run_record_invalid"
        return ApprovedRunReply(
            status, messages.get(code, messages["run_record_invalid"]), session_id
        )
