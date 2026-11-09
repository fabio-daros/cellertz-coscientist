"""Phase 6D human-approval gate. Fixture demonstration only.

reviewer_id is operator-asserted in Phase 6D.
It is not production authentication.

The Phase 6D default approval store is process-local and non-durable.
It is a prototype application store, not scientific memory.

A recorded decision is not authorization. Authorization is not preflight.
Preflight ready is not dispatched. A demonstration receipt is not a
scientific run, not scientific evidence, and not biological validation.

ProteinDesignRequest.execution_authorized stays false. This gate does
not mutate that contract. It authorizes only the phase6d_fixture_only
demonstration, on a separate receipt.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from cellertz.application.contracts import ApplicationContractError, ExecutionBudget, _text
from cellertz.application.pdl1_selection import selection_document
from cellertz.application.request_draft import (
    DesignDrafting,
    ProteinDesignRequest,
    ProteinDesignRequestDraft,
    _request_from,
)

FIXTURE_PROFILE = "phase6d_fixture_only"
BLOCKED_PROFILES = frozenset(
    {
        "mber",
        "bindcraft",
        "bindcraft2",
        "boltz",
        "rfdiffusion",
        "aws_batch",
    }
)
DECISIONS = frozenset({"approved", "rejected", "changes_requested", "held"})
PROFILE_FLAGS = {
    "scientific_execution": False,
    "external_tool_call": False,
    "gpu_execution": False,
    "memory_write": False,
    "semantic_promotion": False,
}
_DECISION_TEXT = "A human decision was recorded. It does not execute a campaign."
_DENIED_TEXT = "The decision is not an approval. Nothing was dispatched."
_INCOMPLETE_TEXT = "The request is incomplete. No approval was recorded."
_ABSENT_TEXT = "The draft revision does not exist."
_CONFLICT_TEXT = "The draft has a newer revision. The decision was not recorded."
_DIGEST_TEXT = "The approved revision no longer matches the server draft."
_BUDGET_TEXT = "The budget is invalid. A budget does not authorize execution."
_PROFILE_TEXT = "The execution profile is not allowlisted. Nothing was dispatched."
_PREFLIGHT_TEXT = "Preflight failed. Nothing was dispatched."
_READY_TEXT = "Preflight is ready. Nothing has been dispatched."
_DEMO_TEXT = (
    "Demonstration dispatch. Not a scientific run. Not scientific evidence. "
    "Not biological validation."
)
_ADAPTER_TEXT = "The demonstration adapter failed. No scientific action was taken."
_KEY_TEXT = "The idempotency key was reused with different content."
_APPROVAL_ABSENT = "No approval receipt matches this request."


def canonical_digest(document: Mapping[str, Any]) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def revision_document(
    draft: ProteinDesignRequestDraft,
    request: ProteinDesignRequest | None,
    budget: ExecutionBudget,
) -> dict[str, Any]:
    generator = None if request is None else request.generator
    document = {
        "session_id": draft.session_id,
        "request_id": draft.request_id,
        "revision": draft.revision,
        "completeness": draft.completeness,
        "issues": [issue.to_dict() for issue in draft.issues],
        "fields": [field.to_dict() for field in draft.fields],
        "request": None if request is None else request.to_dict(),
        "tool_plan": {
            "execution_profile": FIXTURE_PROFILE,
            "generator": generator,
            **PROFILE_FLAGS,
        },
        "budget": budget.to_dict(),
        "execution_profile": FIXTURE_PROFILE,
    }
    selected = selection_document(getattr(draft, "input_selection", None))
    if selected is not None:
        document["pdl1_input_selection"] = selected
    return document


@dataclass(frozen=True)
class ApprovalReceipt:
    approval_id: str
    session_id: str
    request_id: str
    request_revision: int
    revision_digest: str
    decision: str
    reviewer_id: str
    reason: str
    recorded_at: str
    mode: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "request_revision": self.request_revision,
            "revision_digest": self.revision_digest,
            "decision": self.decision,
            "reviewer_id": self.reviewer_id,
            "reason": self.reason,
            "recorded_at": self.recorded_at,
            "mode": self.mode,
            "status": self.status,
            "reviewer_identity": "operator_asserted",
            "production_authentication": False,
            "execution_authorized": False,
            "executable": False,
        }


@dataclass(frozen=True)
class DemonstrationReceipt:
    dispatch_id: str
    approval_id: str
    idempotency_key: str
    execution_profile: str
    authorization_status: str
    preflight_status: str
    dispatch_status: str
    request_digest: str
    recorded_at: str
    budget: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dispatch_id": self.dispatch_id,
            "approval_id": self.approval_id,
            "idempotency_key": self.idempotency_key,
            "execution_profile": self.execution_profile,
            "authorization_status": self.authorization_status,
            "preflight_status": self.preflight_status,
            "dispatch_status": self.dispatch_status,
            "request_digest": self.request_digest,
            "recorded_at": self.recorded_at,
            "budget": self.budget,
            "observed_model_calls": 0,
            "observed_scientific_tool_calls": 0,
            "observed_model_cost": 0,
            "observed_scientific_tool_cost": 0,
            "observed_costs_known": True,
            "candidates": [],
            "scientific_evidence": [],
            "scientific_memory_written": False,
            "semantic_promotion": False,
            **PROFILE_FLAGS,
            "label": "demonstration dispatch",
            "text": _DEMO_TEXT,
        }


@dataclass(frozen=True)
class GateReply:
    status: str
    text: str
    session_id: str
    approval: ApprovalReceipt | None = None
    demonstration: DemonstrationReceipt | None = None
    authorization_status: str = "not_run"
    preflight_status: str = "not_run"
    dispatch_status: str = "not_dispatched"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "execution_authorized": False,
            "executable": False,
            "authorization_status": self.authorization_status,
            "preflight_status": self.preflight_status,
            "dispatch_status": self.dispatch_status,
            "approval": None if self.approval is None else self.approval.to_dict(),
            "demonstration": None if self.demonstration is None else self.demonstration.to_dict(),
            "candidates": [],
            "scientific_evidence": [],
            "scientific_memory_written": False,
        }


class ApprovalStore(Protocol):
    def remember_decision(
        self,
        *,
        idempotency_scope: tuple[str, str],
        content_hash: str,
        revision_key: tuple[str, str, int],
        create: Callable[[], ApprovalReceipt],
    ) -> tuple[str, ApprovalReceipt]: ...

    def remember_dispatch(
        self,
        *,
        idempotency_scope: tuple[str, str],
        content_hash: str,
        approval_id: str,
        create: Callable[[], DemonstrationReceipt],
    ) -> tuple[str, DemonstrationReceipt]: ...

    def demonstration_for(self, approval_id: str) -> DemonstrationReceipt | None: ...

    def dispatch_count(self) -> int: ...


class InMemoryApprovalStore:
    """Process-local and non-durable. Not scientific memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._decision_by_key: dict[tuple[str, str], tuple[str, ApprovalReceipt]] = {}
        self._decision_by_revision: dict[tuple[str, str, int], tuple[str, str]] = {}
        self._dispatch_by_key: dict[tuple[str, str], tuple[str, DemonstrationReceipt]] = {}
        self._dispatch_by_approval: dict[str, tuple[str, str]] = {}
        self._dispatches = 0

    def remember_decision(
        self,
        *,
        idempotency_scope: tuple[str, str],
        content_hash: str,
        revision_key: tuple[str, str, int],
        create: Callable[[], ApprovalReceipt],
    ) -> tuple[str, ApprovalReceipt]:
        with self._lock:
            found = self._decision_by_key.get(idempotency_scope)
            if found is not None:
                return ("replay" if found[0] == content_hash else "conflict"), found[1]
            prior_scope = self._decision_by_revision.get(revision_key)
            if prior_scope is not None:
                prior_hash, prior = self._decision_by_key[prior_scope]
                return ("replay" if prior_hash == content_hash else "conflict"), prior
            receipt = create()
            self._decision_by_key[idempotency_scope] = (content_hash, receipt)
            self._decision_by_revision[revision_key] = idempotency_scope
            return "created", receipt

    def remember_dispatch(
        self,
        *,
        idempotency_scope: tuple[str, str],
        content_hash: str,
        approval_id: str,
        create: Callable[[], DemonstrationReceipt],
    ) -> tuple[str, DemonstrationReceipt]:
        with self._lock:
            found = self._dispatch_by_key.get(idempotency_scope)
            if found is not None:
                return ("replay" if found[0] == content_hash else "conflict"), found[1]
            prior_scope = self._dispatch_by_approval.get(approval_id)
            if prior_scope is not None:
                prior_hash, prior = self._dispatch_by_key[prior_scope]
                if prior_hash != content_hash:
                    return "conflict", prior
                return "replay", prior
            receipt = create()
            self._dispatch_by_key[idempotency_scope] = (content_hash, receipt)
            self._dispatch_by_approval[approval_id] = idempotency_scope
            self._dispatches += 1
            return "created", receipt

    def demonstration_for(self, approval_id: str) -> DemonstrationReceipt | None:
        with self._lock:
            scope = self._dispatch_by_approval.get(approval_id)
            if scope is None:
                return None
            found = self._dispatch_by_key.get(scope)
            if found is None:
                return None
            return found[1]

    def dispatch_count(self) -> int:
        with self._lock:
            return self._dispatches


class FixtureOnlyDispatcher:
    """Deterministic demonstration adapter. No process, network, or tool call."""

    def dispatch(self, receipt: DemonstrationReceipt) -> DemonstrationReceipt:
        return receipt


def _key(value: object) -> str:
    if not isinstance(value, str):
        raise ApplicationContractError("invalid_idempotency_key")
    cleaned = value.strip()
    if not cleaned or any(char.isspace() for char in cleaned) or len(cleaned) > 200:
        raise ApplicationContractError("invalid_idempotency_key")
    return cleaned


class HumanApprovalGate:
    """Records a human decision, then authorizes a fixture demonstration."""

    def __init__(
        self,
        sessions: Mapping[str, Any],
        drafter: DesignDrafting,
        store: ApprovalStore | None = None,
        dispatcher: FixtureOnlyDispatcher | None = None,
        clock: Callable[[], datetime] | None = None,
        approval_ids: Callable[[], str] | None = None,
        dispatch_ids: Callable[[], str] | None = None,
    ) -> None:
        self._sessions = sessions
        self._drafter = drafter
        self._store = store if store is not None else InMemoryApprovalStore()
        self._dispatcher = dispatcher if dispatcher is not None else FixtureOnlyDispatcher()
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._approval_ids = approval_ids or (lambda: f"approval_{uuid4().hex}")
        self._dispatch_ids = dispatch_ids or (lambda: f"dispatch_{uuid4().hex}")
        self._approvals: dict[str, ApprovalReceipt] = {}
        self._approval_lock = threading.Lock()

    def decide(
        self,
        *,
        session_id: str,
        request_id: str,
        revision: int,
        decision: str,
        reviewer_id: str,
        reason: str,
        idempotency_key: str,
    ) -> GateReply:
        try:
            _text(session_id, "invalid_session_id")
            _text(request_id, "invalid_request_id")
            _text(reviewer_id, "invalid_reviewer_id")
            _text(reason, "invalid_reason")
            key = _key(idempotency_key)
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise ApplicationContractError("invalid_revision")
            if decision not in DECISIONS:
                raise ApplicationContractError("invalid_decision")
        except ApplicationContractError:
            return GateReply(
                "invalid_payload", "The decision payload was rejected.", session_id or "unknown"
            )
        if session_id not in self._sessions:
            return GateReply("request_absent", _ABSENT_TEXT, session_id)
        located, draft = self._locate(session_id, request_id, revision)
        if located == "request_absent" or draft is None:
            return GateReply("request_absent", _ABSENT_TEXT, session_id)
        if located == "revision_conflict":
            return GateReply("revision_conflict", _CONFLICT_TEXT, session_id)
        request = _request_from(draft)
        if decision == "approved" and (draft.completeness != "complete" or request is None):
            return GateReply("request_incomplete", _INCOMPLETE_TEXT, session_id)
        budget = self._budget(session_id)
        if budget is None:
            return GateReply("budget_invalid", _BUDGET_TEXT, session_id)
        digest = canonical_digest(revision_document(draft, request, budget))
        content = {
            "session_id": session_id,
            "request_id": request_id,
            "revision": revision,
            "decision": decision,
            "reviewer_id": reviewer_id,
            "reason": reason,
            "digest": digest,
        }
        content_hash = canonical_digest(content)

        def create() -> ApprovalReceipt:
            return ApprovalReceipt(
                approval_id=self._approval_ids(),
                session_id=session_id,
                request_id=request_id,
                request_revision=revision,
                revision_digest=digest,
                decision=decision,
                reviewer_id=reviewer_id,
                reason=reason,
                recorded_at=self._stamp(),
                mode=FIXTURE_PROFILE,
                status="recorded",
            )

        outcome, receipt = self._store.remember_decision(
            idempotency_scope=(session_id, key),
            content_hash=content_hash,
            revision_key=(session_id, request_id, revision),
            create=create,
        )
        if outcome == "conflict":
            return GateReply("idempotency_conflict", _KEY_TEXT, session_id, approval=receipt)
        self._remember_approval(receipt)
        approved = receipt.decision == "approved"
        return GateReply(
            "decision_recorded",
            _DECISION_TEXT if approved else _DENIED_TEXT,
            session_id,
            approval=receipt,
            authorization_status="not_run" if approved else "authorization_denied",
        )

    def preflight(
        self,
        *,
        session_id: str,
        approval_id: str,
        execution_profile: str = FIXTURE_PROFILE,
    ) -> GateReply:
        """Authorize and preflight. Does not dispatch."""
        return self._prepare(session_id, approval_id, execution_profile)

    def recorded_demonstration(
        self,
        *,
        session_id: str,
        approval_id: str,
    ) -> DemonstrationReceipt | None:
        """Return the fixture demonstration already recorded for this approval.

        This does not dispatch, does not authorize a scientific run, and does
        not expose the approval store.
        """
        approval = self._approval_by_id(approval_id, session_id)
        if approval is None or approval.decision != "approved":
            return None
        receipt = self._store.demonstration_for(approval_id)
        if receipt is None or receipt.approval_id != approval.approval_id:
            return None
        if receipt.execution_profile != FIXTURE_PROFILE:
            return None
        return receipt

    def dispatch(
        self,
        *,
        session_id: str,
        approval_id: str,
        idempotency_key: str,
        execution_profile: str = FIXTURE_PROFILE,
    ) -> GateReply:
        try:
            key = _key(idempotency_key)
            _text(session_id, "invalid_session_id")
            _text(approval_id, "invalid_approval_id")
        except ApplicationContractError:
            return GateReply(
                "invalid_payload", "The dispatch payload was rejected.", session_id or "unknown"
            )
        if execution_profile != FIXTURE_PROFILE:
            return GateReply(
                "profile_not_allowlisted",
                _PROFILE_TEXT,
                session_id,
                authorization_status="authorization_denied",
                preflight_status="preflight_failed",
            )
        prepared = self._prepare(session_id, approval_id, execution_profile)
        if prepared.status != "preflight_ready" or prepared.approval is None:
            return prepared
        approval = prepared.approval
        content_hash = canonical_digest(
            {
                "approval_id": approval.approval_id,
                "digest": approval.revision_digest,
                "execution_profile": FIXTURE_PROFILE,
                "session_id": session_id,
            }
        )

        def create() -> DemonstrationReceipt:
            receipt = DemonstrationReceipt(
                dispatch_id=self._dispatch_ids(),
                approval_id=approval.approval_id,
                idempotency_key=key,
                execution_profile=FIXTURE_PROFILE,
                authorization_status="authorized",
                preflight_status="preflight_ready",
                dispatch_status="completed",
                request_digest=approval.revision_digest,
                recorded_at=self._stamp(),
                budget=prepared_budget(self._budget(session_id)),
            )
            demonstrated = self._dispatcher.dispatch(receipt)
            if not isinstance(demonstrated, DemonstrationReceipt):
                raise TypeError("adapter")
            return demonstrated

        try:
            outcome, receipt = self._store.remember_dispatch(
                idempotency_scope=(session_id, key),
                content_hash=content_hash,
                approval_id=approval.approval_id,
                create=create,
            )
        except Exception:  # noqa: BLE001 — the edge must stay free of internals
            return GateReply("adapter_failure", _ADAPTER_TEXT, session_id, approval=approval)
        if outcome == "conflict":
            return GateReply("idempotency_conflict", _KEY_TEXT, session_id, approval=approval)
        return GateReply(
            "demonstration_completed",
            _DEMO_TEXT,
            session_id,
            approval=approval,
            demonstration=receipt,
            authorization_status="authorized",
            preflight_status="preflight_ready",
            dispatch_status="completed",
        )

    def record_bindcraft_demonstration(
        self,
        *,
        session_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> GateReply:
        """Record a fixture demonstration for a BindCraft revision.

        The scientific generator stays bindcraft. This does not run a tool.
        """

        try:
            key = _key(idempotency_key)
            _text(session_id, "invalid_session_id")
            _text(approval_id, "invalid_approval_id")
        except ApplicationContractError:
            return GateReply(
                "invalid_payload", "The dispatch payload was rejected.", session_id or "unknown"
            )
        approval = self._approval_by_id(approval_id, session_id)
        if approval is None or approval.decision != "approved":
            return GateReply("approval_absent", _APPROVAL_ABSENT, session_id, approval=approval)
        located, draft = self._locate(session_id, approval.request_id, approval.request_revision)
        if located != "ok" or draft is None or draft.input_selection is None:
            return GateReply(
                "profile_not_allowlisted", _PROFILE_TEXT, session_id, approval=approval
            )
        request = _request_from(draft)
        budget = self._budget(session_id)
        if request is None or budget is None or request.generator != "bindcraft":
            return GateReply(
                "profile_not_allowlisted", _PROFILE_TEXT, session_id, approval=approval
            )
        if request.target_id != "PDL1":
            return GateReply(
                "profile_not_allowlisted", _PROFILE_TEXT, session_id, approval=approval
            )
        digest = canonical_digest(revision_document(draft, request, budget))
        if digest != approval.revision_digest:
            return GateReply("digest_mismatch", _DIGEST_TEXT, session_id, approval=approval)
        content_hash = canonical_digest(
            {
                "approval_id": approval.approval_id,
                "digest": digest,
                "execution_profile": FIXTURE_PROFILE,
                "session_id": session_id,
            }
        )

        def create() -> DemonstrationReceipt:
            receipt = DemonstrationReceipt(
                dispatch_id=self._dispatch_ids(),
                approval_id=approval.approval_id,
                idempotency_key=key,
                execution_profile=FIXTURE_PROFILE,
                authorization_status="authorized",
                preflight_status="preflight_ready",
                dispatch_status="completed",
                request_digest=digest,
                recorded_at=self._stamp(),
                budget=prepared_budget(budget),
            )
            demonstrated = self._dispatcher.dispatch(receipt)
            if not isinstance(demonstrated, DemonstrationReceipt):
                raise TypeError("adapter")
            return demonstrated

        try:
            outcome, receipt = self._store.remember_dispatch(
                idempotency_scope=(session_id, key),
                content_hash=content_hash,
                approval_id=approval.approval_id,
                create=create,
            )
        except Exception:  # noqa: BLE001 — the edge must stay free of internals
            return GateReply("adapter_failure", _ADAPTER_TEXT, session_id, approval=approval)
        if outcome == "conflict":
            return GateReply("idempotency_conflict", _KEY_TEXT, session_id, approval=approval)
        return GateReply(
            "demonstration_completed",
            _DEMO_TEXT,
            session_id,
            approval=approval,
            demonstration=receipt,
            authorization_status="authorized",
            preflight_status="preflight_ready",
            dispatch_status="completed",
        )

    def _prepare(
        self,
        session_id: str,
        approval_id: str,
        execution_profile: str,
    ) -> GateReply:
        if execution_profile != FIXTURE_PROFILE:
            return GateReply(
                "profile_not_allowlisted",
                _PROFILE_TEXT,
                session_id,
                preflight_status="preflight_failed",
                authorization_status="authorization_denied",
            )
        approval = self._approval_by_id(approval_id, session_id)
        if approval is None:
            return GateReply("approval_absent", _APPROVAL_ABSENT, session_id)
        if approval.decision != "approved":
            return GateReply(
                "authorization_denied",
                _DENIED_TEXT,
                session_id,
                approval=approval,
                authorization_status="authorization_denied",
            )
        located, draft = self._locate(session_id, approval.request_id, approval.request_revision)
        if located != "ok" or draft is None:
            return GateReply(
                "digest_mismatch",
                _DIGEST_TEXT,
                session_id,
                approval=approval,
                authorization_status="authorization_denied",
            )
        request = _request_from(draft)
        budget = self._budget(session_id)
        if budget is None or request is None:
            return GateReply("budget_invalid", _BUDGET_TEXT, session_id, approval=approval)
        digest = canonical_digest(revision_document(draft, request, budget))
        if digest != approval.revision_digest:
            return GateReply(
                "digest_mismatch",
                _DIGEST_TEXT,
                session_id,
                approval=approval,
                authorization_status="authorization_denied",
            )
        blocked = _preflight_block(request, budget, execution_profile)
        if blocked == "budget_invalid":
            return GateReply(
                "budget_invalid",
                _BUDGET_TEXT,
                session_id,
                approval=approval,
                authorization_status="authorized",
                preflight_status="preflight_failed",
            )
        if blocked == "profile_not_allowlisted":
            return GateReply(
                "profile_not_allowlisted",
                _PROFILE_TEXT,
                session_id,
                approval=approval,
                authorization_status="authorized",
                preflight_status="preflight_failed",
            )
        if blocked is not None:
            return GateReply(
                "preflight_failed",
                _PREFLIGHT_TEXT,
                session_id,
                approval=approval,
                authorization_status="authorized",
                preflight_status="preflight_failed",
            )
        return GateReply(
            "preflight_ready",
            _READY_TEXT,
            session_id,
            approval=approval,
            authorization_status="authorized",
            preflight_status="preflight_ready",
            dispatch_status="not_dispatched",
        )

    def _remember_approval(self, receipt: ApprovalReceipt) -> None:
        with self._approval_lock:
            self._approvals[receipt.approval_id] = receipt

    def _approval_by_id(self, approval_id: str, session_id: str) -> ApprovalReceipt | None:
        with self._approval_lock:
            receipt = self._approvals.get(approval_id)
        if receipt is None or receipt.session_id != session_id:
            return None
        return receipt

    def _locate(
        self,
        session_id: str,
        request_id: str,
        revision: int,
    ) -> tuple[str, ProteinDesignRequestDraft | None]:
        history = self._drafter.revisions(request_id)
        if not history or history[-1].session_id != session_id:
            return "request_absent", None
        if history[-1].revision != revision:
            return "revision_conflict", history[-1]
        return "ok", history[-1]

    def _budget(self, session_id: str) -> ExecutionBudget | None:
        session = self._sessions.get(session_id)
        budget = getattr(session, "budget", None)
        if not isinstance(budget, ExecutionBudget):
            return None
        if budget.authorizes_execution:
            return None
        return budget

    def _stamp(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None:
            raise ApplicationContractError("naive_timestamp")
        return moment.astimezone(UTC).isoformat()


def prepared_budget(budget: ExecutionBudget | None) -> dict[str, Any]:
    if budget is None:
        return {}
    return budget.to_dict()


def _preflight_block(
    request: ProteinDesignRequest,
    budget: ExecutionBudget,
    execution_profile: str,
) -> str | None:
    if budget.authorizes_execution:
        return "budget_invalid"
    if execution_profile != FIXTURE_PROFILE:
        return "profile_not_allowlisted"
    if execution_profile in BLOCKED_PROFILES or request.generator != "fixture":
        return "profile_not_allowlisted"
    if request.generator in BLOCKED_PROFILES:
        return "profile_not_allowlisted"
    return None
