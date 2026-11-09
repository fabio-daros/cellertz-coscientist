"""Phase 6D: human decision, server authorization, fixture-only dispatch."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cellertz.application.approval import (
    FIXTURE_PROFILE,
    DemonstrationReceipt,
    FixtureOnlyDispatcher,
    HumanApprovalGate,
    InMemoryApprovalStore,
    canonical_digest,
    revision_document,
)
from cellertz.application.contracts import ApplicationContractError, ExecutionBudget
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DesignDrafting,
    DeterministicDraftExtractor,
)

WHEN = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
SESSION = "session_pdl1_prototype"
ROOT = Path(__file__).resolve().parents[1]


class CountingDispatcher(FixtureOnlyDispatcher):
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls = 0
        self.error = error

    def dispatch(self, receipt: DemonstrationReceipt) -> DemonstrationReceipt:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return receipt


def _stack(
    message: str = "design a binder for pdl1 using fixture with 1 candidates",
    store: InMemoryApprovalStore | None = None,
    session_id: str = SESSION,
    id_prefix: str = "",
):
    session = pdl1_prototype_session()
    if session_id != SESSION:
        session = replace(session, session_id=session_id)
    counter = {"request": 0, "approval": 0, "dispatch": 0}

    def request_ids() -> str:
        counter["request"] += 1
        return f"request_{counter['request']}"

    def approval_ids() -> str:
        counter["approval"] += 1
        return f"approval_{id_prefix}{counter['approval']}"

    def dispatch_ids() -> str:
        counter["dispatch"] += 1
        return f"dispatch_{id_prefix}{counter['dispatch']}"

    drafter = DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        clock=lambda: WHEN,
        interaction_ids=lambda: "interaction_1",
        request_ids=request_ids,
    )
    store = store if store is not None else InMemoryApprovalStore()
    dispatcher = CountingDispatcher()
    gate = HumanApprovalGate(
        {session.session_id: session},
        drafter,
        store,
        dispatcher,
        clock=lambda: WHEN,
        approval_ids=approval_ids,
        dispatch_ids=dispatch_ids,
    )
    reply = drafter.revise(DesignDraftCommand(session.session_id, message))
    return gate, drafter, store, dispatcher, reply


def _decide(gate: HumanApprovalGate, reply, decision: str = "approved", **overrides):
    draft = reply.draft
    payload = {
        "session_id": draft.session_id,
        "request_id": draft.request_id,
        "revision": draft.revision,
        "decision": decision,
        "reviewer_id": "operator_declared",
        "reason": "Fixture demonstration only.",
        "idempotency_key": f"decision-{decision}",
    }
    payload.update(overrides)
    return gate.decide(**payload)


def test_approved_decision_requires_reviewer_and_reason():
    gate, _drafter, _store, dispatcher, reply = _stack()
    missing = _decide(gate, reply, reviewer_id="  ", reason="because")
    assert missing.status == "invalid_payload"
    assert dispatcher.calls == 0
    recorded = _decide(gate, reply)
    assert recorded.approval is not None
    assert recorded.approval.reviewer_id == "operator_declared"
    assert recorded.approval.reason == "Fixture demonstration only."
    assert recorded.authorization_status == "not_run"
    assert recorded.to_dict()["execution_authorized"] is False


@pytest.mark.parametrize("decision", ["rejected", "changes_requested", "held"])
def test_non_approval_does_not_authorize(decision: str):
    gate, _drafter, store, dispatcher, reply = _stack()
    recorded = _decide(gate, reply, decision)
    assert recorded.approval is not None
    assert recorded.approval.decision == decision
    assert recorded.authorization_status == "authorization_denied"
    dispatched = gate.dispatch(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-1",
    )
    assert dispatched.status == "authorization_denied"
    assert dispatcher.calls == 0
    assert store.dispatch_count() == 0


def test_approval_binds_exact_request_revision_and_server_digest():
    gate, _drafter, _store, _dispatcher, reply = _stack()
    recorded = _decide(gate, reply)
    approval = recorded.approval
    assert approval is not None
    assert approval.request_id == reply.draft.request_id
    assert approval.request_revision == reply.draft.revision
    document = revision_document(reply.draft, reply.request, pdl1_prototype_session().budget)
    assert approval.revision_digest == canonical_digest(document)
    assert canonical_digest(document) == canonical_digest(document)


def test_old_revision_conflicts_and_a_changed_draft_invalidates_approval():
    gate, drafter, _store, dispatcher, reply = _stack()
    recorded = _decide(gate, reply)
    updated = drafter.revise(
        DesignDraftCommand(
            SESSION,
            "design a binder for bcma using fixture with 2 candidates",
            request_id=reply.draft.request_id,
            revision=reply.draft.revision,
        )
    )
    assert updated.draft.revision == 2
    stale = _decide(gate, reply, idempotency_key="decision-old")
    assert stale.status == "revision_conflict"
    assert recorded.approval is not None
    blocked = gate.dispatch(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-stale",
    )
    assert blocked.status == "digest_mismatch"
    assert dispatcher.calls == 0


def test_same_idempotency_key_replays_and_different_content_conflicts():
    gate, _drafter, store, dispatcher, reply = _stack()
    first = _decide(gate, reply)
    second = _decide(gate, reply)
    assert first.approval is not None and second.approval is not None
    assert second.approval.approval_id == first.approval.approval_id
    conflict = _decide(gate, reply, reason="A different reason.")
    assert conflict.status == "idempotency_conflict"
    done = gate.dispatch(
        session_id=SESSION,
        approval_id=first.approval.approval_id,
        idempotency_key="dispatch-1",
    )
    again = gate.dispatch(
        session_id=SESSION,
        approval_id=first.approval.approval_id,
        idempotency_key="dispatch-1",
    )
    other_key = gate.dispatch(
        session_id=SESSION,
        approval_id=first.approval.approval_id,
        idempotency_key="dispatch-2",
    )
    assert done.demonstration is not None and again.demonstration is not None
    assert again.demonstration.dispatch_id == done.demonstration.dispatch_id
    assert other_key.demonstration is not None
    assert other_key.demonstration.dispatch_id == done.demonstration.dispatch_id
    assert store.dispatch_count() == 1
    assert dispatcher.calls == 1
    second_reply = _drafter.revise(
        DesignDraftCommand(SESSION, "design a binder for pdl1 using fixture with 1 candidates")
    )
    second = _decide(gate, second_reply, idempotency_key="decision-second")
    assert second.approval is not None
    mismatched = gate.dispatch(
        session_id=SESSION,
        approval_id=second.approval.approval_id,
        idempotency_key="dispatch-1",
    )
    assert mismatched.status == "idempotency_conflict"
    assert store.dispatch_count() == 1


@pytest.mark.parametrize("decision", ["rejected", "changes_requested", "held"])
def test_negative_decision_is_recorded_on_an_incomplete_revision(decision: str):
    gate, _drafter, store, dispatcher, reply = _stack("design a binder")
    assert reply.draft.completeness == "incomplete"
    recorded = _decide(gate, reply, decision)
    assert recorded.status == "decision_recorded"
    assert recorded.approval is not None
    assert recorded.approval.decision == decision
    assert recorded.authorization_status == "authorization_denied"
    blocked = gate.dispatch(
        session_id=reply.draft.session_id,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-incomplete",
    )
    assert blocked.status == "authorization_denied"
    assert blocked.demonstration is None
    assert dispatcher.calls == 0
    assert store.dispatch_count() == 0


def test_approved_decision_still_requires_a_complete_revision():
    gate, _drafter, store, dispatcher, reply = _stack("design a binder")
    recorded = _decide(gate, reply)
    assert recorded.status == "request_incomplete"
    assert recorded.approval is None
    assert dispatcher.calls == 0
    assert store.dispatch_count() == 0


def test_idempotency_scope_includes_the_session():
    store = InMemoryApprovalStore()
    gate_a, _drafter_a, _store_a, dispatcher_a, reply_a = _stack(store=store, id_prefix="a")
    other = "session_phase6d_other"
    gate_b, _drafter_b, _store_b, dispatcher_b, reply_b = _stack(
        store=store,
        session_id=other,
        id_prefix="b",
    )
    first = _decide(gate_a, reply_a, idempotency_key="shared-decision")
    second = _decide(gate_b, reply_b, idempotency_key="shared-decision")
    assert first.status == "decision_recorded"
    assert second.status == "decision_recorded"
    assert first.approval is not None and second.approval is not None
    assert first.approval.approval_id != second.approval.approval_id
    dispatched_a = gate_a.dispatch(
        session_id=SESSION,
        approval_id=first.approval.approval_id,
        idempotency_key="shared-dispatch",
    )
    dispatched_b = gate_b.dispatch(
        session_id=other,
        approval_id=second.approval.approval_id,
        idempotency_key="shared-dispatch",
    )
    assert dispatched_a.status == "demonstration_completed"
    assert dispatched_b.status == "demonstration_completed"
    assert dispatched_a.demonstration is not None and dispatched_b.demonstration is not None
    assert dispatched_a.demonstration.dispatch_id != dispatched_b.demonstration.dispatch_id
    assert dispatcher_a.calls == 1
    assert dispatcher_b.calls == 1
    assert store.dispatch_count() == 2


def test_budget_does_not_authorize_and_invalid_budget_fails_closed():
    session = pdl1_prototype_session()
    assert session.budget.authorizes_execution is False
    assert session.budget.model_cost_ceiling is None
    assert session.budget.to_dict()["model_cost_ceiling"] != 0
    with pytest.raises(ApplicationContractError):
        ExecutionBudget(
            token_ceiling=1,
            model_cost_ceiling=None,
            scientific_tool_cost_ceiling=None,
            warning_threshold=None,
            action_on_limit="stop",
            human_override_required=False,
            authorizes_execution=True,
        )
    gate, _drafter, _store, dispatcher, reply = _stack()
    bare = HumanApprovalGate({SESSION: object()}, _drafter, clock=lambda: WHEN)
    blocked = bare.decide(
        session_id=SESSION,
        request_id=reply.draft.request_id,
        revision=reply.draft.revision,
        decision="approved",
        reviewer_id="operator_declared",
        reason="Fixture demonstration only.",
        idempotency_key="decision-budget",
    )
    assert blocked.status == "budget_invalid"
    assert dispatcher.calls == 0
    recorded = _decide(gate, reply)
    assert recorded.authorization_status == "not_run"


def test_fixture_profile_is_accepted_and_real_profiles_are_blocked():
    gate, _drafter, store, dispatcher, reply = _stack()
    recorded = _decide(gate, reply)
    assert recorded.approval is not None
    ready = gate.preflight(session_id=SESSION, approval_id=recorded.approval.approval_id)
    assert ready.status == "preflight_ready"
    assert ready.dispatch_status == "not_dispatched"
    assert store.dispatch_count() == 0
    for profile in ("mber", "bindcraft", "bindcraft2", "boltz", "rfdiffusion", "aws_batch", "unknown"):
        blocked = gate.dispatch(
            session_id=SESSION,
            approval_id=recorded.approval.approval_id,
            idempotency_key=f"dispatch-{profile}",
            execution_profile=profile,
        )
        assert blocked.status == "profile_not_allowlisted"
    assert dispatcher.calls == 0
    done = gate.dispatch(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-ok",
        execution_profile=FIXTURE_PROFILE,
    )
    assert done.status == "demonstration_completed"
    assert done.demonstration is not None
    assert done.demonstration.to_dict()["candidates"] == []
    assert done.demonstration.to_dict()["scientific_evidence"] == []
    assert done.demonstration.to_dict()["scientific_memory_written"] is False
    assert done.demonstration.to_dict()["scientific_execution"] is False
    assert "not a scientific run" in done.text.casefold()
    assert done.demonstration.budget["model_cost_ceiling"] is None
    assert done.demonstration.to_dict()["observed_model_calls"] == 0


def test_real_generator_requests_never_reach_the_adapter():
    gate, _drafter, _store, dispatcher, reply = _stack(
        "design a binder for pdl1 using mber with 1 candidates"
    )
    assert reply.request.generator == "mber"
    recorded = _decide(gate, reply, idempotency_key="decision-mber")
    assert recorded.approval is not None
    blocked = gate.dispatch(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-mber",
    )
    assert blocked.status == "profile_not_allowlisted"
    assert dispatcher.calls == 0
    bindcraft, _drafter, _store, bindcraft_dispatcher, bindcraft_reply = _stack(
        "design a binder for pdl1 using bindcraft with 1 candidates"
    )
    approved = _decide(bindcraft, bindcraft_reply, idempotency_key="decision-bindcraft")
    assert approved.approval is not None
    denied = bindcraft.dispatch(
        session_id=SESSION,
        approval_id=approved.approval.approval_id,
        idempotency_key="dispatch-bindcraft",
    )
    assert denied.status == "profile_not_allowlisted"
    assert bindcraft_dispatcher.calls == 0


def test_receipt_serialization_is_deterministic_and_errors_are_safe():
    gate, _drafter, _store, _dispatcher, reply = _stack()
    recorded = _decide(gate, reply)
    assert recorded.approval is not None
    crashing = CountingDispatcher(RuntimeError("Traceback /Users/secret TOKEN=abc"))
    gate._dispatcher = crashing
    failed = gate.dispatch(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-boom",
    )
    encoded = json.dumps(failed.to_dict(), sort_keys=True)
    assert failed.status == "adapter_failure"
    assert "Traceback" not in encoded
    assert "/Users/secret" not in encoded
    assert "TOKEN" not in encoded
    done_gate, _drafter, _store, _dispatcher, done_reply = _stack()
    approved = _decide(done_gate, done_reply)
    assert approved.approval is not None
    first = done_gate.dispatch(
        session_id=SESSION,
        approval_id=approved.approval.approval_id,
        idempotency_key="dispatch-stable",
    )
    second = done_gate.dispatch(
        session_id=SESSION,
        approval_id=approved.approval.approval_id,
        idempotency_key="dispatch-stable",
    )
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(second.to_dict(), sort_keys=True)


def test_application_gate_does_not_import_scientific_execution_boundaries():
    source = (ROOT / "src/cellertz/application/approval.py").read_text(encoding="utf-8")
    adapter = (ROOT / "src/cellertz/application/approval.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert modules
    banned = ("databricks", "second_brain", "knowledge", "cellertz.execution", "cellertz.runs", "cellertz.domains")
    for module in modules:
        if module.startswith("cellertz."):
            assert module.startswith("cellertz.application")
        for name in banned:
            assert name not in module
    assert "subprocess" not in adapter
    assert "Popen" not in adapter
    assert "urlopen" not in adapter
    assert "ScientificRunMemory" not in source
    assert "reviewer_id is operator-asserted in Phase 6D." in source
    assert "process-local and non-durable" in source
