"""Explicit scientific-run authorization. Permission only.

A record here means the exact reviewed request may be consumed once
by a future scientific executor. It is not execution, not completion,
and not scientific validation. This module does not call a runner,
a container, a model, or scientific memory.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any, Protocol
from uuid import uuid4

from cellertz.application.approval import (
    FIXTURE_PROFILE,
    HumanApprovalGate,
    canonical_digest,
    revision_document,
)
from cellertz.application.contracts import ApplicationContractError, ExecutionBudget, _text
from cellertz.application.pdl1_selection import Pdl1InputSelection
from cellertz.application.request_draft import (
    DesignDrafting,
    ProteinDesignRequest,
    ProteinDesignRequestDraft,
    _request_from,
)
from cellertz.application.scientific_runner_readiness import (
    CD22_PROFILE_ID,
    DOCKER_PROFILE_ID,
    PDL1_PROFILE_ID,
    TACI_PROFILE_ID,
    ReadinessReply,
    readiness_reply_is_public,
)

_BLOCKED_TARGETS = frozenset({"CD22", "TACI"})
_BLOCKED_PROFILES = frozenset({CD22_PROFILE_ID, TACI_PROFILE_ID})
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_AUTHORIZATION_ID = re.compile(r"^authorization_[0-9a-f]{32}$")
_OBSERVATION_ID = re.compile(r"^observation_[0-9a-f]{64}$")
_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(?!000000)\d{6})?\+00:00$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_UNSAFE = re.compile(r"traceback|/users/|/tmp/|token=|sqlite|select\s", re.IGNORECASE)
_BUDGET_KEYS = frozenset(
    {
        "action_on_limit",
        "authorizes_execution",
        "currency",
        "human_override_required",
        "model_cost_ceiling",
        "scientific_tool_cost_ceiling",
        "token_ceiling",
        "warning_threshold",
    }
)
_HISTORY_LIMIT = 50
AUTHORIZED_TEXT = "Authorized — no scientific execution has started."
CONSUMED_TEXT = (
    "This authorization has been consumed. See the scientific run record for execution status."
)
REVOKED_TEXT = "Authorization revoked. No scientific execution has started."
SINGLE_USE_TEXT = "This authorization may be consumed once by a future scientific executor."
VALIDATION_TEXT = "This is not scientific validation."
_ABSENT_TEXT = "The scientific authorization is not available."
_CONFLICT_TEXT = "The idempotency key does not match this authorization."
_ADAPTER_TEXT = "The scientific authorization adapter failed. No scientific action was taken."
_UNUSABLE_TEXT = "The authorization record could not be used. No scientific action was taken."
_REJECTED_TEXT = "The authorization payload was rejected. No scientific action was taken."
_HISTORY_TEXT = "Scientific authorization history. Not scientific execution."
_STATUSES = frozenset({"authorized", "revoked"})
_PREREQUISITE_TEXT = {
    "session_absent": "The server session is not available. No scientific action was taken.",
    "request_incomplete": "The design request is not complete. No scientific action was taken.",
    "approval_absent": "The approval is not available. No scientific action was taken.",
    "approval_not_approved": "The approval is not approved. No scientific action was taken.",
    "revision_conflict": "The approval does not match the current revision. No scientific action was taken.",
    "digest_mismatch": "The approval does not match the current request. No scientific action was taken.",
    "budget_invalid": "The execution budget is not valid. No scientific action was taken.",
    "demonstration_absent": "The fixture demonstration is not recorded for this approval. No scientific action was taken.",
    "fixture_run_required": "A completed fixture run is not recorded for this approval. No scientific action was taken.",
    "profile_not_allowlisted": "This request is not the fixture-only profile. No scientific action was taken.",
    "readiness_not_ready": "Scientific runner readiness is not ready. No scientific action was taken.",
    "readiness_invalid": "The readiness snapshot was rejected. No scientific action was taken.",
    "target_blocked": "CD22 and TACI remain blocked. No scientific action was taken.",
    "authorization_rejected": "Scientific authorization was rejected. No scientific action was taken.",
}
_EMPTY_REPLIES = {
    **_PREREQUISITE_TEXT,
    "authorization_absent": _ABSENT_TEXT,
    "invalid_payload": _REJECTED_TEXT,
    "adapter_failure": _ADAPTER_TEXT,
    "authorization_record_invalid": _UNUSABLE_TEXT,
}


class ScientificAuthorizationStoreError(Exception):
    """Stable operational-store code. It does not carry driver text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()


def _stamp_ok(value: object) -> bool:
    if type(value) is not str or _STAMP.fullmatch(value) is None:
        return False
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return False
    return moment.utcoffset() == timedelta(0) and moment.isoformat() == value


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _ordered(earlier: str, later: str, *, strict: bool = False) -> bool:
    left = _moment(earlier)
    right = _moment(later)
    return left < right if strict else left <= right


def _safe_text(value: object) -> bool:
    if type(value) is not str or not value or len(value) > 500:
        return False
    if any(char in value for char in "\n\r\x00/\\`"):
        return False
    return _UNSAFE.search(value) is None


def _optional_int(value: object) -> bool:
    return value is None or (type(value) is int and value >= 0)


def _optional_number(value: object) -> bool:
    return value is None or (type(value) in {int, float} and value >= 0)


def budget_document(value: object) -> bool:
    if type(value) is not dict or set(value) != _BUDGET_KEYS:
        return False
    if (
        type(value["authorizes_execution"]) is not bool
        or value["authorizes_execution"] is not False
    ):
        return False
    if type(value["human_override_required"]) is not bool:
        return False
    if value["action_on_limit"] not in {"warn", "stop"}:
        return False
    if not _optional_int(value["token_ceiling"]):
        return False
    if not _optional_number(value["model_cost_ceiling"]):
        return False
    if not _optional_number(value["scientific_tool_cost_ceiling"]):
        return False
    threshold = value["warning_threshold"]
    if threshold is not None and (type(threshold) not in {int, float} or not 0 <= threshold <= 1):
        return False
    currency = value["currency"]
    return currency is None or (type(currency) is str and _safe_text(currency))


def same_document(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        if set(left) != set(right):
            return False
        return all(same_document(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(
            same_document(item, other) for item, other in zip(left, right, strict=True)
        )
    return left == right


@dataclass(frozen=True)
class ScientificAuthorizationEvent:
    sequence: int
    status: str
    recorded_at: str
    text: str
    origin: str = "server"

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or isinstance(self.sequence, bool) or self.sequence < 1:
            raise ApplicationContractError("invalid_authorization_event")
        if self.status not in _STATUSES or self.origin != "server":
            raise ApplicationContractError("invalid_authorization_event")
        if not _stamp_ok(self.recorded_at) or not _safe_text(self.text):
            raise ApplicationContractError("invalid_authorization_event")
        expected = AUTHORIZED_TEXT if self.status == "authorized" else REVOKED_TEXT
        if self.text != expected:
            raise ApplicationContractError("invalid_authorization_event")


@dataclass(frozen=True)
class ScientificRunAuthorization:
    authorization_id: str
    session_id: str
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
    status: str
    created_at: str
    updated_at: str
    events: tuple[ScientificAuthorizationEvent, ...]
    binding_hash: str
    consumed_at: str | None = None

    def __post_init__(self) -> None:
        if not _public_authorization_shape(self):
            raise ApplicationContractError("invalid_authorization_record")
        if not _history_ok(self):
            raise ApplicationContractError("invalid_authorization_history")

    @property
    def execution_authorized(self) -> bool:
        return self.status == "authorized" and self.consumed_at is None

    @property
    def scientific_execution(self) -> bool:
        return False

    @property
    def scientific_validation(self) -> bool:
        return False

    @property
    def scientific_candidate_generated(self) -> bool:
        return False


_BINDING_KEYS = (
    "approval_id",
    "budget",
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
    "session_id",
    "target_scope",
    "tool_name",
    "tool_version",
)


def scientific_binding(source: object) -> dict[str, Any]:
    """One canonical scientific binding. Authorization and run records share it."""
    try:
        if isinstance(source, Mapping):
            raw = {key: source[key] for key in _BINDING_KEYS}
        else:
            raw = {key: getattr(source, key) for key in _BINDING_KEYS}
    except (AttributeError, KeyError, TypeError):
        return {}
    budget = raw.get("budget")
    if type(budget) is not dict:
        return {}
    raw["budget"] = dict(budget)
    return raw


def binding_document(record: ScientificRunAuthorization) -> dict[str, Any]:
    return scientific_binding(record)


def binding_hash_for(source: object) -> str:
    document = scientific_binding(source)
    if not document:
        return ""
    return _digest(document)


def authorization_from_stored(
    payload: str, *, binding_hash: str, consumed_at: str | None
) -> ScientificRunAuthorization:
    if type(payload) is not str:
        raise ApplicationContractError("invalid_authorization_record")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        raise ApplicationContractError("invalid_authorization_record") from None
    if type(parsed) is not dict or _canonical(parsed) != payload:
        raise ApplicationContractError("invalid_authorization_record")
    record = _record_from_public(parsed, binding_hash=binding_hash, consumed_at=consumed_at)
    if stored_json(record) != payload or record.binding_hash != _digest(binding_document(record)):
        raise ApplicationContractError("invalid_authorization_record")
    return record


def _record_from_public(
    parsed: dict[str, Any], *, binding_hash: str, consumed_at: str | None
) -> ScientificRunAuthorization:
    events = parsed.get("events")
    if type(events) is not list:
        raise ApplicationContractError("invalid_authorization_record")
    built = tuple(_event_from_public(item) for item in events)
    budget = parsed.get("budget")
    if type(budget) is not dict:
        raise ApplicationContractError("invalid_authorization_record")
    return ScientificRunAuthorization(
        authorization_id=parsed.get("authorization_id", ""),
        session_id=parsed.get("session_id", ""),
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
        status=parsed.get("status", ""),
        created_at=parsed.get("created_at", ""),
        updated_at=parsed.get("updated_at", ""),
        events=built,
        binding_hash=binding_hash,
        consumed_at=consumed_at,
    )


def _event_from_public(value: object) -> ScientificAuthorizationEvent:
    if type(value) is not dict:
        raise ApplicationContractError("invalid_authorization_event")
    return ScientificAuthorizationEvent(
        sequence=value.get("sequence", 0),
        status=value.get("status", ""),
        recorded_at=value.get("recorded_at", ""),
        text=value.get("text", ""),
        origin=value.get("origin", ""),
    )


def public_authorization(record: ScientificRunAuthorization) -> dict[str, Any]:
    if (
        type(record) is not ScientificRunAuthorization
        or not _public_authorization_shape(record)
        or not _history_ok(record)
    ):
        raise ApplicationContractError("invalid_authorization_record")
    return {
        "authorization_id": record.authorization_id,
        "session_id": record.session_id,
        "request_id": record.request_id,
        "request_revision": record.request_revision,
        "approval_id": record.approval_id,
        "request_digest": record.request_digest,
        "readiness_observation_id": record.readiness_observation_id,
        "readiness_observed_at": record.readiness_observed_at,
        "profile_id": record.profile_id,
        "tool_name": record.tool_name,
        "tool_version": record.tool_version,
        "runtime_name": record.runtime_name,
        "runtime_version": record.runtime_version,
        "image_digest": record.image_digest,
        "manifest_digest": record.manifest_digest,
        "target_scope": record.target_scope,
        "budget": dict(record.budget),
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "events": [
            {
                "sequence": event.sequence,
                "status": event.status,
                "recorded_at": event.recorded_at,
                "text": event.text,
                "origin": event.origin,
            }
            for event in record.events
        ],
        "execution_authorized": record.status == "authorized" and record.consumed_at is None,
        "scientific_execution": False,
        "scientific_validation": False,
        "scientific_candidate_generated": False,
        "single_use": SINGLE_USE_TEXT,
        "text": record_text(record),
        "validation_text": VALIDATION_TEXT,
    }


def stored_authorization(record: ScientificRunAuthorization) -> dict[str, Any]:
    """Canonical record without the consumption marker."""
    payload = public_authorization(replace(record, consumed_at=None))
    payload["execution_authorized"] = record.status == "authorized"
    payload["text"] = AUTHORIZED_TEXT if record.status == "authorized" else REVOKED_TEXT
    return payload


def stored_json(record: ScientificRunAuthorization) -> str:
    return _canonical(stored_authorization(record))


@dataclass(frozen=True)
class ScientificAuthorizationReply:
    status: str
    text: str
    session_id: str
    authorization: ScientificRunAuthorization | None = None
    execution_authorized: bool = False
    scientific_execution: bool = False
    scientific_validation: bool = False
    scientific_candidate_generated: bool = False

    def __post_init__(self) -> None:
        derived = False if self.authorization is None else self.authorization.execution_authorized
        if type(derived) is not bool:
            raise ApplicationContractError("invalid_authorization_reply")
        object.__setattr__(self, "execution_authorized", derived)

    def to_dict(self) -> dict[str, Any]:
        if not _reply_shape(self):
            raise ApplicationContractError("invalid_authorization_reply")
        authorized = (
            self.authorization.execution_authorized if self.authorization is not None else False
        )
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "authorization": None
            if self.authorization is None
            else public_authorization(self.authorization),
            "execution_authorized": authorized if type(authorized) is bool else False,
            "scientific_execution": False,
            "scientific_validation": False,
            "scientific_candidate_generated": False,
        }


@dataclass(frozen=True)
class ScientificAuthorizationListReply:
    status: str
    text: str
    session_id: str
    authorizations: tuple[ScientificRunAuthorization, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        if not _list_shape(self):
            raise ApplicationContractError("invalid_authorization_reply")
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "authorizations": [public_authorization(item) for item in self.authorizations],
            "execution_authorized": False,
            "scientific_execution": False,
            "scientific_validation": False,
            "scientific_candidate_generated": False,
        }


def adapter_failure_reply(session_id: str) -> ScientificAuthorizationReply:
    return ScientificAuthorizationReply("adapter_failure", _ADAPTER_TEXT, session_id)


def _reply_shape(reply: ScientificAuthorizationReply) -> bool:
    if type(reply) is not ScientificAuthorizationReply:
        return False
    if (
        reply.scientific_execution is not False
        or reply.scientific_validation is not False
        or reply.scientific_candidate_generated is not False
        or type(reply.execution_authorized) is not bool
        or type(reply.status) is not str
        or type(reply.text) is not str
        or type(reply.session_id) is not str
    ):
        return False
    record = reply.authorization
    if record is None:
        if reply.execution_authorized is not False or reply.status not in _EMPTY_REPLIES:
            return False
        return reply.text == _EMPTY_REPLIES[reply.status]
    if type(record) is not ScientificRunAuthorization:
        return False
    if reply.execution_authorized is not record.execution_authorized:
        return False
    if reply.status == "authorization_created":
        return (
            record.status == "authorized"
            and record.consumed_at is None
            and reply.text == AUTHORIZED_TEXT
        )
    if reply.status == "authorization_replayed":
        return record.status == "authorized" and reply.text == record_text(record)
    if reply.status == "authorization_revoked":
        return (
            record.status == "revoked" and record.consumed_at is None and reply.text == REVOKED_TEXT
        )
    if reply.status == "authorization_observed":
        return reply.text == record_text(record)
    if reply.status == "idempotency_conflict":
        return reply.text == _CONFLICT_TEXT
    return False


def _list_shape(reply: ScientificAuthorizationListReply) -> bool:
    if type(reply) is not ScientificAuthorizationListReply:
        return False
    if (
        getattr(reply, "execution_authorized", False) is not False
        or getattr(reply, "scientific_execution", False) is not False
        or getattr(reply, "scientific_validation", False) is not False
        or getattr(reply, "scientific_candidate_generated", False) is not False
        or type(reply.authorizations) is not tuple
    ):
        return False
    if reply.status == "authorizations_observed":
        return reply.text == _HISTORY_TEXT
    if reply.status not in {"adapter_failure", "invalid_payload", "authorization_record_invalid"}:
        return False
    if reply.authorizations:
        return False
    expected = {
        "adapter_failure": _ADAPTER_TEXT,
        "invalid_payload": _REJECTED_TEXT,
        "authorization_record_invalid": _UNUSABLE_TEXT,
    }[reply.status]
    return reply.text == expected


class ScientificAuthorizationStore(Protocol):
    def claim(
        self,
        *,
        scope: tuple[str, str],
        content_hash: str,
        binding_hash: str,
        authorization: ScientificRunAuthorization,
    ) -> tuple[str, ScientificRunAuthorization]: ...

    def revoke(
        self,
        *,
        session_id: str,
        authorization_id: str,
        scope: tuple[str, str],
        content_hash: str,
        recorded_at: str,
        text: str,
    ) -> tuple[str, ScientificRunAuthorization | None]: ...

    def read(self, session_id: str, authorization_id: str) -> ScientificRunAuthorization | None: ...

    def list_authorizations(
        self, session_id: str, *, limit: int
    ) -> tuple[ScientificRunAuthorization, ...]: ...

    def claim_consumption(
        self,
        *,
        session_id: str,
        authorization_id: str,
        expected_binding_hash: str,
        consumed_at: str,
        recheck: Callable[[], str | None] | None = None,
    ) -> str: ...

    def close(self) -> None: ...


class _FixtureRuns(Protocol):
    def list_runs(self, *, session_id: str) -> Any: ...


class InMemoryScientificAuthorizationStore:
    """Process-local operational store. Not scientific memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, ScientificRunAuthorization] = {}
        self._bindings: dict[tuple[str, str], str] = {}
        self._keys: dict[tuple[str, str], tuple[str, str]] = {}

    def claim(
        self,
        *,
        scope: tuple[str, str],
        content_hash: str,
        binding_hash: str,
        authorization: ScientificRunAuthorization,
    ) -> tuple[str, ScientificRunAuthorization]:
        with self._lock:
            found = self._keys.get(scope)
            if found is not None:
                stored_hash, record_id = found
                record = self._records[record_id]
                if stored_hash != content_hash:
                    return "conflict", record
                return "replay", record
            existing_id = self._bindings.get((authorization.session_id, binding_hash))
            if existing_id is not None:
                record = self._records[existing_id]
                self._keys[scope] = (content_hash, existing_id)
                return "replay", record
            self._records[authorization.authorization_id] = authorization
            self._bindings[(authorization.session_id, binding_hash)] = (
                authorization.authorization_id
            )
            self._keys[scope] = (content_hash, authorization.authorization_id)
            return "created", authorization

    def revoke(
        self,
        *,
        session_id: str,
        authorization_id: str,
        scope: tuple[str, str],
        content_hash: str,
        recorded_at: str,
        text: str,
    ) -> tuple[str, ScientificRunAuthorization | None]:
        with self._lock:
            found = self._keys.get(scope)
            if found is not None:
                stored_hash, record_id = found
                record = self._records[record_id]
                if stored_hash != content_hash or record_id != authorization_id:
                    return "conflict", record
                return "replay", record
            record = self._records.get(authorization_id)
            if record is None or record.session_id != session_id:
                return "absent", None
            if record.consumed_at is not None:
                return "consumed", record
            if record.status == "revoked":
                self._keys[scope] = (content_hash, authorization_id)
                return "replay", record
            revoked = _revoke_record(record, recorded_at=recorded_at, text=text)
            self._records[authorization_id] = revoked
            self._keys[scope] = (content_hash, authorization_id)
            return "revoked", revoked

    def read(self, session_id: str, authorization_id: str) -> ScientificRunAuthorization | None:
        with self._lock:
            record = self._records.get(authorization_id)
            if record is None or record.session_id != session_id:
                return None
            return record

    def list_authorizations(
        self, session_id: str, *, limit: int
    ) -> tuple[ScientificRunAuthorization, ...]:
        bounded = min(limit, _HISTORY_LIMIT)
        with self._lock:
            owned = [item for item in self._records.values() if item.session_id == session_id]
            owned.sort(key=lambda item: (item.updated_at, item.authorization_id), reverse=True)
            return tuple(owned[:bounded])

    def claim_consumption(
        self,
        *,
        session_id: str,
        authorization_id: str,
        expected_binding_hash: str,
        consumed_at: str,
        recheck: Callable[[], str | None] | None = None,
    ) -> str:
        with self._lock:
            record = self._records.get(authorization_id)
            expected = expected_binding_hash if recheck is None else recheck()
            if (
                record is None
                or record.session_id != session_id
                or record.status != "authorized"
                or record.consumed_at is not None
                or type(expected) is not str
                or record.binding_hash != expected
                or expected != expected_binding_hash
            ):
                return "unavailable"
            self._records[authorization_id] = replace(record, consumed_at=consumed_at)
            return "consumed"

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class ConsumedRevision:
    """Live approved revision for one already consumed authorization."""

    draft: ProteinDesignRequestDraft
    request: ProteinDesignRequest
    budget: ExecutionBudget
    selection: Pdl1InputSelection
    digest: str
    record: ScientificRunAuthorization


class ScientificAuthorizationService:
    """Records one explicit permission. It does not execute it."""

    def __init__(
        self,
        sessions: Mapping[str, Any],
        drafter: DesignDrafting,
        gate: HumanApprovalGate,
        runs: _FixtureRuns,
        readiness: Any,
        store: ScientificAuthorizationStore | None = None,
        clock: Callable[[], datetime] | None = None,
        authorization_ids: Callable[[], str] | None = None,
    ) -> None:
        self._sessions = sessions
        self._drafter = drafter
        self._gate = gate
        self._runs = runs
        self._readiness = readiness
        self._store = store if store is not None else InMemoryScientificAuthorizationStore()
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._authorization_ids = authorization_ids or (lambda: f"authorization_{uuid4().hex}")
        self._claim_lock = threading.Lock()
        self._before_claim: Callable[[], None] = lambda: None

    def authorize(
        self, *, session_id: str, approval_id: str, idempotency_key: str
    ) -> ScientificAuthorizationReply:
        try:
            _text(session_id, "invalid_session_id")
            approval_id = _identifier(approval_id, "invalid_approval_id")
            key = _identifier(idempotency_key, "invalid_idempotency_key")
        except ApplicationContractError:
            return _rejected(session_id)
        prepared = self._prepare(session_id, approval_id)
        if isinstance(prepared, ScientificAuthorizationReply):
            return prepared
        binding_hash, record = prepared
        try:
            outcome, stored = self._store.claim(
                scope=(session_id, key),
                content_hash=binding_hash,
                binding_hash=binding_hash,
                authorization=record,
            )
        except ScientificAuthorizationStoreError:
            return _unusable(session_id)
        return _claimed(outcome, stored, session_id)

    def revoke(
        self, *, session_id: str, authorization_id: str, idempotency_key: str
    ) -> ScientificAuthorizationReply:
        try:
            _text(session_id, "invalid_session_id")
            authorization_id = _identifier(authorization_id, "invalid_authorization_id")
            key = _identifier(idempotency_key, "invalid_idempotency_key")
        except ApplicationContractError:
            return _rejected(session_id)
        content_hash = _digest(
            {
                "authorization_id": authorization_id,
                "purpose": "revoke",
                "session_id": session_id,
            }
        )
        try:
            outcome, stored = self._store.revoke(
                session_id=session_id,
                authorization_id=authorization_id,
                scope=(session_id, key),
                content_hash=content_hash,
                recorded_at=self._moment(),
                text=REVOKED_TEXT,
            )
        except ScientificAuthorizationStoreError:
            return _unusable(session_id)
        if outcome == "consumed":
            return ScientificAuthorizationReply(
                "authorization_rejected",
                _PREREQUISITE_TEXT["authorization_rejected"],
                session_id,
            )
        if outcome == "absent" or stored is None:
            return ScientificAuthorizationReply("authorization_absent", _ABSENT_TEXT, session_id)
        if outcome == "conflict":
            return ScientificAuthorizationReply(
                "idempotency_conflict", _CONFLICT_TEXT, session_id, stored
            )
        return ScientificAuthorizationReply(
            "authorization_revoked", REVOKED_TEXT, session_id, stored
        )

    def read(self, *, session_id: str, authorization_id: str) -> ScientificAuthorizationReply:
        try:
            _text(session_id, "invalid_session_id")
            authorization_id = _identifier(authorization_id, "invalid_authorization_id")
        except ApplicationContractError:
            return _rejected(session_id)
        try:
            record = self._store.read(session_id, authorization_id)
        except ScientificAuthorizationStoreError:
            return _unusable(session_id)
        if record is None:
            return ScientificAuthorizationReply("authorization_absent", _ABSENT_TEXT, session_id)
        return ScientificAuthorizationReply(
            "authorization_observed",
            record_text(record),
            session_id,
            record,
        )

    def list_authorizations(self, *, session_id: str) -> ScientificAuthorizationListReply:
        try:
            _text(session_id, "invalid_session_id")
        except ApplicationContractError:
            return ScientificAuthorizationListReply(
                "invalid_payload", _REJECTED_TEXT, session_id or "unknown"
            )
        try:
            records = self._store.list_authorizations(session_id, limit=_HISTORY_LIMIT)
        except ScientificAuthorizationStoreError:
            return ScientificAuthorizationListReply(
                "authorization_record_invalid", _UNUSABLE_TEXT, session_id
            )
        return ScientificAuthorizationListReply(
            "authorizations_observed",
            _HISTORY_TEXT,
            session_id,
            records,
        )

    def consumable(self, *, session_id: str, authorization_id: str) -> bool:
        """Read-only check for a future executor. Does not consume."""
        try:
            record = self._store.read(session_id, authorization_id)
        except ScientificAuthorizationStoreError:
            return False
        if record is None or record.status != "authorized" or record.consumed_at is not None:
            return False
        prepared = self._prepare(session_id, record.approval_id)
        if isinstance(prepared, ScientificAuthorizationReply):
            return False
        binding_hash, _current = prepared
        return binding_hash == record.binding_hash

    def claim_once(self, *, session_id: str, authorization_id: str) -> str:
        """Single consume for a future executor. Not an HTTP route.

        The live binding is checked again inside the store write. A
        prerequisite that changes before that write leaves the record
        unconsumed. This does not execute.
        """
        with self._claim_lock:
            try:
                record = self._store.read(session_id, authorization_id)
            except ScientificAuthorizationStoreError:
                return "unavailable"
            if not self._binding_current(record):
                return "unavailable"
            assert record is not None

            def recheck() -> str | None:
                self._before_claim()
                if not self._binding_current(record):
                    return None
                return record.binding_hash

            try:
                return self._store.claim_consumption(
                    session_id=session_id,
                    authorization_id=authorization_id,
                    expected_binding_hash=record.binding_hash,
                    consumed_at=self._moment(),
                    recheck=recheck,
                )
            except ScientificAuthorizationStoreError:
                return "unavailable"

    def consumed_revision(
        self, *, session_id: str, authorization_id: str
    ) -> ConsumedRevision | None:
        """Return the live revision of one consumed authorization.

        This does not create an authorization, consume one, or execute.
        """

        try:
            record = self._store.read(session_id, authorization_id)
        except (ApplicationContractError, ScientificAuthorizationStoreError):
            return None
        if (
            record is None
            or record.status != "authorized"
            or record.consumed_at is None
            or not self.binding_still_matches(
                session_id=session_id, authorization_id=authorization_id
            )
        ):
            return None
        history = self._drafter.revisions(record.request_id)
        if not history or history[-1].revision != record.request_revision:
            return None
        draft = history[-1]
        request = _request_from(draft)
        budget = _session_budget(self._sessions.get(session_id))
        selection = draft.input_selection
        if request is None or budget is None or type(selection) is not Pdl1InputSelection:
            return None
        digest = canonical_digest(revision_document(draft, request, budget))
        if (
            digest != record.request_digest
            or budget.to_dict().get("authorizes_execution") is not False
        ):
            return None
        return ConsumedRevision(
            draft=draft,
            request=request,
            budget=budget,
            selection=selection,
            digest=digest,
            record=record,
        )

    def binding_still_matches(self, *, session_id: str, authorization_id: str) -> bool:
        """Recheck the live binding. Does not consume and does not execute."""
        try:
            _text(session_id, "invalid_session_id")
            authorization_id = _identifier(authorization_id, "invalid_authorization_id")
            record = self._store.read(session_id, authorization_id)
        except (ApplicationContractError, ScientificAuthorizationStoreError):
            return False
        if record is None or record.status != "authorized":
            return False
        prepared = self._prepare(record.session_id, record.approval_id)
        if isinstance(prepared, ScientificAuthorizationReply):
            return False
        binding_hash, _current = prepared
        return binding_hash == record.binding_hash

    def _binding_current(self, record: ScientificRunAuthorization | None) -> bool:
        if record is None or record.status != "authorized" or record.consumed_at is not None:
            return False
        prepared = self._prepare(record.session_id, record.approval_id)
        if isinstance(prepared, ScientificAuthorizationReply):
            return False
        binding_hash, _current = prepared
        return binding_hash == record.binding_hash

    def close(self) -> None:
        self._store.close()

    def _prepare(
        self, session_id: str, approval_id: str
    ) -> ScientificAuthorizationReply | tuple[str, ScientificRunAuthorization]:
        if session_id not in self._sessions:
            return _prerequisite("session_absent", session_id)
        prepared = self._gate.preflight(session_id=session_id, approval_id=approval_id)
        approval = prepared.approval
        if prepared.status == "approval_absent" or approval is None:
            return _prerequisite("approval_absent", session_id)
        if approval.session_id != session_id or approval.approval_id != approval_id:
            return _prerequisite("approval_absent", session_id)
        history = self._drafter.revisions(approval.request_id)
        if not history or history[-1].session_id != session_id:
            return _prerequisite("approval_absent", session_id)
        draft = history[-1]
        if draft.revision != approval.request_revision:
            return _prerequisite("revision_conflict", session_id)
        if draft.completeness != "complete":
            return _prerequisite("request_incomplete", session_id)
        if approval.decision != "approved":
            return _prerequisite("approval_not_approved", session_id)
        if prepared.status == "budget_invalid":
            return _prerequisite("budget_invalid", session_id)
        if prepared.status == "digest_mismatch":
            return _prerequisite("digest_mismatch", session_id)
        request_preview = _request_from(draft)
        real_selection = (
            request_preview is not None
            and request_preview.generator == "bindcraft"
            and type(draft.input_selection) is Pdl1InputSelection
        )
        if prepared.status == "profile_not_allowlisted" and not real_selection:
            return _prerequisite("profile_not_allowlisted", session_id)
        if prepared.status != "preflight_ready" and not real_selection:
            return _prerequisite("authorization_rejected", session_id)
        request = _request_from(draft)
        budget = _session_budget(self._sessions.get(session_id))
        if request is None:
            return _prerequisite("request_incomplete", session_id)
        if budget is None or not budget_document(budget.to_dict()):
            return _prerequisite("budget_invalid", session_id)
        if request.target_id in _BLOCKED_TARGETS or request.target_id != "PDL1":
            return _prerequisite("target_blocked", session_id)
        selection = draft.input_selection
        if request.generator == "bindcraft" and type(selection) is Pdl1InputSelection:
            return self._prepare_bindcraft(session_id, approval, draft, request, budget, selection)
        if request.generator != "fixture" or selection is not None:
            return _prerequisite("profile_not_allowlisted", session_id)
        digest = canonical_digest(revision_document(draft, request, budget))
        if digest != approval.revision_digest or not _DIGEST.fullmatch(digest):
            return _prerequisite("digest_mismatch", session_id)
        demonstration = self._gate.recorded_demonstration(
            session_id=session_id, approval_id=approval.approval_id
        )
        if (
            demonstration is None
            or demonstration.approval_id != approval.approval_id
            or demonstration.request_digest != digest
            or demonstration.execution_profile != FIXTURE_PROFILE
            or not same_document(demonstration.budget, budget.to_dict())
        ):
            return _prerequisite("demonstration_absent", session_id)
        fixture = self._fixture_run(
            session_id, approval.approval_id, digest, draft.revision, budget
        )
        if isinstance(fixture, ScientificAuthorizationReply):
            return fixture
        snapshot = self._ready_snapshot(session_id, PDL1_PROFILE_ID)
        if isinstance(snapshot, ScientificAuthorizationReply):
            return snapshot
        observed_at = self._moment()
        event = ScientificAuthorizationEvent(1, "authorized", observed_at, AUTHORIZED_TEXT)
        record = ScientificRunAuthorization(
            authorization_id=self._authorization_ids(),
            session_id=session_id,
            request_id=approval.request_id,
            request_revision=draft.revision,
            approval_id=approval.approval_id,
            request_digest=digest,
            readiness_observation_id=snapshot[0],
            readiness_observed_at=snapshot[1].observed_at,
            profile_id=snapshot[1].profile_id or "",
            tool_name=snapshot[1].tool_name or "",
            tool_version=snapshot[1].tool_version or "",
            runtime_name=snapshot[1].runtime_name or "",
            runtime_version=snapshot[1].runtime_version or "",
            image_digest=snapshot[1].image_digest or "",
            manifest_digest=snapshot[1].manifest_digest or "",
            target_scope=snapshot[1].target_scope or "",
            budget=dict(budget.to_dict()),
            status="authorized",
            created_at=observed_at,
            updated_at=observed_at,
            events=(event,),
            binding_hash="",
        )
        binding_hash = _digest(binding_document(record))
        record = replace(record, binding_hash=binding_hash)
        return binding_hash, record

    def _fixture_run(
        self,
        session_id: str,
        approval_id: str,
        digest: str,
        revision: int,
        budget: ExecutionBudget,
    ) -> ScientificAuthorizationReply | None:
        # ponytail: list window is 50; an older fixture run is treated as absent
        try:
            listed = self._runs.list_runs(session_id=session_id)
        except Exception:  # noqa: BLE001 — fail closed before any authorization write
            return _unusable(session_id)
        if getattr(listed, "status", None) == "run_record_invalid":
            return _unusable(session_id)
        runs = getattr(listed, "runs", ())
        matches = [
            run
            for run in runs
            if getattr(run, "approval_id", None) == approval_id
            and getattr(run, "session_id", None) == session_id
        ]
        if len(matches) != 1:
            return _prerequisite("fixture_run_required", session_id)
        run = matches[0]
        events = getattr(run, "events", ())
        last = events[-1] if events else None
        if (
            run.status != "completed"
            or run.execution_profile != FIXTURE_PROFILE
            or run.request_id is None
            or run.request_revision != revision
            or run.request_digest != digest
            or type(run.attempt) is not int
            or run.attempt < 1
            or last is None
            or last.status != "completed"
            or last.attempt != run.attempt
            or any(event.attempt > run.attempt for event in events)
            or not same_document(run.budget, budget.to_dict())
        ):
            return _prerequisite("fixture_run_required", session_id)
        return None

    def _prepare_bindcraft(
        self,
        session_id: str,
        approval: Any,
        draft: Any,
        request: Any,
        budget: ExecutionBudget,
        selection: Pdl1InputSelection,
    ) -> ScientificAuthorizationReply | tuple[str, ScientificRunAuthorization]:
        digest = canonical_digest(revision_document(draft, request, budget))
        if digest != approval.revision_digest or not _DIGEST.fullmatch(digest):
            return _prerequisite("digest_mismatch", session_id)
        demonstration = self._gate.recorded_demonstration(
            session_id=session_id, approval_id=approval.approval_id
        )
        if (
            demonstration is None
            or demonstration.approval_id != approval.approval_id
            or demonstration.request_digest != digest
            or demonstration.execution_profile != FIXTURE_PROFILE
            or not same_document(demonstration.budget, budget.to_dict())
        ):
            return _prerequisite("demonstration_absent", session_id)
        snapshot = self._ready_snapshot(session_id, DOCKER_PROFILE_ID)
        if isinstance(snapshot, ScientificAuthorizationReply):
            return snapshot
        if selection.manifest_digest != (snapshot[1].manifest_digest or ""):
            return _prerequisite("digest_mismatch", session_id)
        observed_at = self._moment()
        event = ScientificAuthorizationEvent(1, "authorized", observed_at, AUTHORIZED_TEXT)
        record = ScientificRunAuthorization(
            authorization_id=self._authorization_ids(),
            session_id=session_id,
            request_id=approval.request_id,
            request_revision=draft.revision,
            approval_id=approval.approval_id,
            request_digest=digest,
            readiness_observation_id=snapshot[0],
            readiness_observed_at=snapshot[1].observed_at,
            profile_id=snapshot[1].profile_id or "",
            tool_name=snapshot[1].tool_name or "",
            tool_version=snapshot[1].tool_version or "",
            runtime_name=snapshot[1].runtime_name or "",
            runtime_version=snapshot[1].runtime_version or "",
            image_digest=snapshot[1].image_digest or "",
            manifest_digest=snapshot[1].manifest_digest or "",
            target_scope=snapshot[1].target_scope or "",
            budget=dict(budget.to_dict()),
            status="authorized",
            created_at=observed_at,
            updated_at=observed_at,
            events=(event,),
            binding_hash="",
        )
        binding_hash = _digest(binding_document(record))
        return binding_hash, replace(record, binding_hash=binding_hash)

    def _ready_snapshot(
        self, session_id: str, profile_id: str = PDL1_PROFILE_ID
    ) -> ScientificAuthorizationReply | tuple[str, ReadinessReply]:
        try:
            reply = self._readiness.read()
        except Exception:  # noqa: BLE001 — a bad snapshot is not authorization
            return _prerequisite("readiness_invalid", session_id)
        if type(reply) is not ReadinessReply or not readiness_reply_is_public(reply):
            return _prerequisite("readiness_invalid", session_id)
        if reply.session_id != session_id:
            return _prerequisite("readiness_invalid", session_id)
        identity = (reply.profile_id, reply.tool_name, reply.target_scope)
        if identity[0] in _BLOCKED_PROFILES or identity[2] in _BLOCKED_TARGETS:
            return _prerequisite("target_blocked", session_id)
        if (
            type(reply.execution_authorized) is not bool
            or type(reply.scientific_execution) is not bool
            or type(reply.scientific_validation) is not bool
            or reply.execution_authorized is not False
            or reply.scientific_execution is not False
            or reply.scientific_validation is not False
        ):
            return _prerequisite("readiness_invalid", session_id)
        if reply.status != "ready":
            return _prerequisite("readiness_not_ready", session_id)
        if identity != (profile_id, "bindcraft", "PDL1"):
            return _prerequisite("readiness_invalid", session_id)
        if (
            not _version(reply.tool_version)
            or not _version(reply.runtime_name)
            or not _version(reply.runtime_version)
            or _IMAGE.fullmatch(reply.image_digest or "") is None
            or _DIGEST.fullmatch(reply.manifest_digest or "") is None
        ):
            return _prerequisite("readiness_invalid", session_id)
        observation_id = "observation_" + _digest(
            {
                "image_digest": reply.image_digest,
                "manifest_digest": reply.manifest_digest,
                "observed_at": reply.observed_at,
                "profile_id": reply.profile_id,
                "runtime_name": reply.runtime_name,
                "runtime_version": reply.runtime_version,
                "target_scope": reply.target_scope,
                "tool_name": reply.tool_name,
                "tool_version": reply.tool_version,
            }
        )
        return observation_id, reply

    def _moment(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ApplicationContractError("naive_timestamp")
        return moment.astimezone(UTC).replace(tzinfo=UTC).isoformat()


def record_text(record: ScientificRunAuthorization) -> str:
    if record.consumed_at is not None:
        return CONSUMED_TEXT
    return AUTHORIZED_TEXT if record.status == "authorized" else REVOKED_TEXT


def _claimed(
    outcome: str, stored: ScientificRunAuthorization, session_id: str
) -> ScientificAuthorizationReply:
    if outcome == "conflict":
        return ScientificAuthorizationReply(
            "idempotency_conflict", _CONFLICT_TEXT, session_id, stored
        )
    if outcome == "created":
        return ScientificAuthorizationReply(
            "authorization_created", AUTHORIZED_TEXT, session_id, stored
        )
    if stored.status == "revoked":
        return ScientificAuthorizationReply(
            "authorization_revoked", REVOKED_TEXT, session_id, stored
        )
    return ScientificAuthorizationReply(
        "authorization_replayed", record_text(stored), session_id, stored
    )


def _revoke_record(
    record: ScientificRunAuthorization, *, recorded_at: str, text: str
) -> ScientificRunAuthorization:
    event = ScientificAuthorizationEvent(
        sequence=len(record.events) + 1,
        status="revoked",
        recorded_at=recorded_at,
        text=text,
    )
    return replace(
        record, status="revoked", updated_at=recorded_at, events=record.events + (event,)
    )


def _session_budget(session: object) -> ExecutionBudget | None:
    budget = getattr(session, "budget", None)
    if not isinstance(budget, ExecutionBudget) or budget.authorizes_execution:
        return None
    return budget


def _identifier(value: object, code: str) -> str:
    if type(value) is not str or _KEY.fullmatch(value) is None:
        raise ApplicationContractError(code)
    return value


def _version(value: object) -> bool:
    return (
        type(value) is str
        and _TOKEN.fullmatch(value) is not None
        and value.lower()
        not in {
            "latest",
            "current",
            "unknown",
        }
    )


def _rejected(session_id: str) -> ScientificAuthorizationReply:
    safe = session_id if type(session_id) is str and _TOKEN.fullmatch(session_id) else "unknown"
    return ScientificAuthorizationReply("invalid_payload", _REJECTED_TEXT, safe)


def _unusable(session_id: str) -> ScientificAuthorizationReply:
    return ScientificAuthorizationReply("authorization_record_invalid", _UNUSABLE_TEXT, session_id)


def _prerequisite(status: str, session_id: str) -> ScientificAuthorizationReply:
    return ScientificAuthorizationReply(status, _PREREQUISITE_TEXT[status], session_id)


def _event_fields(event: object) -> bool:
    if type(event) is not ScientificAuthorizationEvent:
        return False
    if type(event.sequence) is not int or isinstance(event.sequence, bool) or event.sequence < 1:
        return False
    if event.status not in _STATUSES or event.origin != "server":
        return False
    if not _stamp_ok(event.recorded_at) or not _safe_text(event.text):
        return False
    expected = AUTHORIZED_TEXT if event.status == "authorized" else REVOKED_TEXT
    return event.text == expected


def _history_ok(record: ScientificRunAuthorization) -> bool:
    events = record.events
    if type(events) is not tuple or not events or not all(_event_fields(event) for event in events):
        return False
    if [event.sequence for event in events] != list(range(1, len(events) + 1)):
        return False
    if not _stamp_ok(record.created_at) or not _stamp_ok(record.updated_at):
        return False
    if not _ordered(record.created_at, record.updated_at):
        return False
    if events[0].status != "authorized" or events[0].recorded_at != record.created_at:
        return False
    for previous, event in pairwise(events):
        if not _ordered(previous.recorded_at, event.recorded_at, strict=True):
            return False
    if events[-1].recorded_at != record.updated_at or events[-1].status != record.status:
        return False
    if record.consumed_at is not None and (
        type(record.consumed_at) is not str
        or not _stamp_ok(record.consumed_at)
        or record.status != "authorized"
        or not _ordered(record.created_at, record.consumed_at)
    ):
        return False
    if record.status == "authorized":
        return len(events) == 1 and record.updated_at == record.created_at
    return len(events) == 2 and events[1].status == "revoked"


def _public_authorization_shape(record: ScientificRunAuthorization) -> bool:
    if _AUTHORIZATION_ID.fullmatch(record.authorization_id) is None:
        return False
    if _TOKEN.fullmatch(record.session_id) is None:
        return False
    if _KEY.fullmatch(record.request_id) is None or _KEY.fullmatch(record.approval_id) is None:
        return False
    if type(record.request_revision) is not int or isinstance(record.request_revision, bool):
        return False
    if record.request_revision < 1:
        return False
    if _DIGEST.fullmatch(record.request_digest) is None:
        return False
    if record.binding_hash != "" and _DIGEST.fullmatch(record.binding_hash) is None:
        return False
    return _identity_ok(record)


def _identity_ok(record: ScientificRunAuthorization) -> bool:
    if (
        record.profile_id not in {PDL1_PROFILE_ID, DOCKER_PROFILE_ID}
        or record.tool_name != "bindcraft"
    ):
        return False
    if record.target_scope != "PDL1":
        return False
    if not _version(record.tool_version) or not _version(record.runtime_name):
        return False
    if not _version(record.runtime_version):
        return False
    if (
        _IMAGE.fullmatch(record.image_digest) is None
        or _DIGEST.fullmatch(record.manifest_digest) is None
    ):
        return False
    if _OBSERVATION_ID.fullmatch(record.readiness_observation_id) is None:
        return False
    if not _stamp_ok(record.readiness_observed_at) or not _stamp_ok(record.created_at):
        return False
    if not _stamp_ok(record.updated_at):
        return False
    if record.consumed_at is not None and not _stamp_ok(record.consumed_at):
        return False
    if record.status not in _STATUSES or not budget_document(record.budget):
        return False
    return record.binding_hash == "" or _DIGEST.fullmatch(record.binding_hash) is not None
