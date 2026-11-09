"""Phase 6E fixture-run lifecycle. No scientific execution."""

from __future__ import annotations

import ast
import inspect
import json
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cellertz.application.approval import HumanApprovalGate
from cellertz.application.approved_run import (
    ApprovedRun,
    ApprovedRunService,
    FixtureApprovedRunRunner,
    InMemoryApprovedRunStore,
)
from cellertz.application.contracts import ApplicationContractError, ExecutionBudget
from cellertz.application.request_draft import DesignDraftCommand

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_human_approval import _decide, _stack

WHEN = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
SESSION = "session_pdl1_prototype"
_LEAKS = ("Traceback", "/tmp/secret", "TOKEN", "subprocess", "databricks")


def _service(
    gate: HumanApprovalGate,
    runner: FixtureApprovedRunRunner | None = None,
    store=None,
    start: int = 0,
):
    ids = {"n": start}

    def run_ids() -> str:
        ids["n"] += 1
        return f"run_{ids['n']:032x}"

    adapter = runner if runner is not None else FixtureApprovedRunRunner()
    service = ApprovedRunService(
        gate,
        store if store is not None else InMemoryApprovedRunStore(),
        adapter,
        clock=lambda: WHEN,
        run_ids=run_ids,
    )
    return service, adapter


def _demonstrated(message: str = "design a binder for pdl1 using fixture with 1 candidates"):
    gate, drafter, store, dispatcher, reply = _stack(message)
    recorded = _decide(gate, reply)
    assert recorded.approval is not None
    dispatched = gate.dispatch(
        session_id=reply.draft.session_id,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-1",
    )
    assert dispatched.status == "demonstration_completed"
    service, runner = _service(gate)
    return service, runner, gate, drafter, store, dispatcher, recorded


def _created():
    service, runner, gate, drafter, _store, _dispatcher, recorded = _demonstrated()
    approval = recorded.approval
    assert approval is not None
    created = service.create(
        session_id=SESSION,
        approval_id=approval.approval_id,
        idempotency_key="run-1",
    )
    return _finish(service, created), service, runner, gate, drafter, approval


def _finish(service: ApprovedRunService, queued):
    assert queued.status == "run_queued"
    assert queued.run is not None and queued.run.status == "queued"
    finished = service.process_next()
    assert finished is not None and finished.run_id == queued.run.run_id
    return service.read(session_id=queued.run.session_id, run_id=queued.run.run_id)


def test_run_requires_a_valid_approval_and_a_recorded_demonstration():
    gate, _drafter, _store, dispatcher, reply = _stack()
    service, runner = _service(gate)
    missing = service.create(
        session_id=SESSION, approval_id="approval_missing", idempotency_key="run-1"
    )
    assert missing.status == "approval_absent"
    assert missing.run is None
    recorded = _decide(gate, reply)
    assert recorded.approval is not None
    before_dispatch = service.create(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="run-1",
    )
    assert before_dispatch.status == "demonstration_absent"
    assert runner.calls == 0
    assert dispatcher.calls == 0
    gate.dispatch(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-1",
    )
    created = service.create(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="run-1",
    )
    assert created.status == "run_queued"
    assert runner.calls == 0
    created = _finish(service, created)
    assert created.status == "run_observed"
    assert created.run is not None
    assert runner.calls == 1


@pytest.mark.parametrize("decision", ["rejected", "changes_requested", "held"])
def test_negative_decisions_do_not_create_a_run(decision: str):
    gate, _drafter, _store, dispatcher, reply = _stack()
    recorded = _decide(gate, reply, decision)
    assert recorded.approval is not None
    service, runner = _service(gate)
    blocked = service.create(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="run-negative",
    )
    assert blocked.status == "authorization_denied"
    assert blocked.run is None
    assert runner.calls == 0
    assert dispatcher.calls == 0


def test_incomplete_revision_does_not_create_a_run():
    gate, _drafter, _store, dispatcher, reply = _stack("design a binder")
    recorded = _decide(gate, reply)
    assert recorded.status == "request_incomplete"
    service, runner = _service(gate)
    blocked = service.create(
        session_id=SESSION, approval_id="approval_1", idempotency_key="run-incomplete"
    )
    assert blocked.status == "approval_absent"
    assert runner.calls == 0
    assert dispatcher.calls == 0


def test_old_revision_and_changed_digest_do_not_create_a_run():
    service, runner, _gate, drafter, _store, dispatcher, recorded = _demonstrated()
    approval = recorded.approval
    assert approval is not None
    drafter.revise(
        DesignDraftCommand(
            SESSION,
            "design a binder for bcma using fixture with 2 candidates",
            request_id=approval.request_id,
            revision=approval.request_revision,
        )
    )
    blocked = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-stale"
    )
    assert blocked.status == "digest_mismatch"
    assert blocked.run is None
    assert runner.calls == 0
    assert dispatcher.calls == 1


def test_invalid_budget_fails_closed_and_budget_does_not_authorize():
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
    service, runner, gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    approval = recorded.approval
    assert approval is not None
    gate._sessions[SESSION] = object()
    blocked = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-budget"
    )
    assert blocked.status == "budget_invalid"
    assert runner.calls == 0
    created, _service_ok, _runner, _gate, _drafter, _approval = _created()
    assert created.run is not None
    assert created.run.budget["authorizes_execution"] is False
    assert created.run.budget["model_cost_ceiling"] is None
    assert created.run.budget["scientific_tool_cost_ceiling"] is None
    assert created.run.usage["estimated_model_cost"] is None
    assert created.run.usage["observed_model_calls"] == 0
    assert created.run.usage["observed_scientific_tool_calls"] == 0
    assert created.run.usage["observed_costs_known"] is True
    assert created.run.usage["authorizes_execution"] is False


def test_fixture_generator_is_accepted_and_real_generators_are_blocked():
    created, _created_service, runner, _gate, _drafter, approval = _created()
    assert created.run is not None
    assert created.run.execution_profile == "phase6d_fixture_only"
    assert runner.calls == 1
    for message in (
        "design a binder for pdl1 using mber with 1 candidates",
        "design a binder for pdl1 using bindcraft with 1 candidates",
    ):
        gate, _drafter, _store, dispatcher, reply = _stack(message)
        recorded = _decide(gate, reply, idempotency_key=f"decision-{reply.request.generator}")
        assert recorded.approval is not None
        blocked_dispatch = gate.dispatch(
            session_id=SESSION,
            approval_id=recorded.approval.approval_id,
            idempotency_key="dispatch-blocked",
        )
        assert blocked_dispatch.status == "profile_not_allowlisted"
        blocked_service, blocked_runner = _service(gate)
        blocked = blocked_service.create(
            session_id=SESSION,
            approval_id=recorded.approval.approval_id,
            idempotency_key="run-blocked",
        )
        assert blocked.status == "profile_not_allowlisted"
        assert blocked_runner.calls == 0
        assert dispatcher.calls == 0
    assert "executor" not in inspect.signature(ApprovedRunService.create).parameters
    assert approval.decision == "approved"


def test_lifecycle_records_ordered_append_only_events_and_sanitized_failure():
    created, service, runner, _gate, _drafter, approval = _created()
    run = created.run
    assert run is not None
    assert [event.status for event in run.events] == ["queued", "running", "completed"]
    assert [event.sequence for event in run.events] == [1, 2, 3]
    assert [event.attempt for event in run.events] == [1, 1, 1]
    assert {event.origin for event in run.events} == {"server"}
    prior = run.events
    with pytest.raises(AttributeError):
        prior[0].status = "completed"  # type: ignore[misc]
    again = service.read(session_id=SESSION, run_id=run.run_id)
    assert again.run is not None
    assert again.run.events == prior
    assert runner.calls == 1
    failing = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN=abc"))
    failed_service, failed_runner, _gate, _drafter, _store, _dispatcher, failed_recorded = (
        _demonstrated()
    )
    failed_service._runner = failing
    assert failed_recorded.approval is not None
    failed = failed_service.create(
        session_id=SESSION,
        approval_id=failed_recorded.approval.approval_id,
        idempotency_key="run-fail",
    )
    failed = _finish(failed_service, failed)
    assert failed.status == "run_observed"
    assert failed.run is not None
    assert failed.run.status == "failed"
    assert [event.status for event in failed.run.events] == ["queued", "running", "failed"]
    assert failed.run.artifacts == ()
    encoded = json.dumps(failed.to_dict(), sort_keys=True)
    for leak in _LEAKS:
        assert leak not in encoded
    assert failed_runner.calls == 0
    observed = failed_service.read(session_id=SESSION, run_id=failed.run.run_id)
    assert observed.status == "run_observed"
    assert observed.run is not None
    assert observed.run.status == "failed"
    assert approval.approval_id == run.approval_id


def test_idempotency_replay_conflict_and_one_run_per_approval():
    created, service, runner, _gate, _drafter, approval = _created()
    assert created.run is not None
    replay = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert replay.run is not None
    assert replay.run.run_id == created.run.run_id
    assert runner.calls == 1
    other_key = service.create(
        session_id=SESSION,
        approval_id=approval.approval_id,
        idempotency_key="run-2",
    )
    assert other_key.run is not None
    assert other_key.run.run_id == created.run.run_id
    assert runner.calls == 1
    gate, drafter, _store, _dispatcher, reply = _stack()
    second_reply = drafter.revise(
        DesignDraftCommand(SESSION, "design a binder for pdl1 using fixture with 2 candidates")
    )
    del reply
    recorded = _decide(gate, second_reply, idempotency_key="decision-other")
    assert recorded.approval is not None
    gate.dispatch(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-other",
    )
    conflict_service, _runner = _service(gate)
    first = conflict_service.create(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="shared-run",
    )
    another = drafter.revise(
        DesignDraftCommand(SESSION, "design a binder for pdl1 using fixture with 3 candidates")
    )
    approved = _decide(gate, another, idempotency_key="decision-third")
    assert approved.approval is not None
    gate.dispatch(
        session_id=SESSION,
        approval_id=approved.approval.approval_id,
        idempotency_key="dispatch-third",
    )
    conflict = conflict_service.create(
        session_id=SESSION,
        approval_id=approved.approval.approval_id,
        idempotency_key="shared-run",
    )
    assert first.status == "run_queued"
    assert conflict.status == "idempotency_conflict"


def test_second_key_for_an_approval_conflicts_when_reused_for_another_approval():
    created, service, runner, gate, drafter, approval = _created()
    assert created.run is not None
    replay = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="key-2"
    )
    assert replay.run is not None
    assert replay.run.run_id == created.run.run_id
    assert runner.calls == 1
    other = drafter.revise(
        DesignDraftCommand(SESSION, "design a binder for pdl1 using fixture with 2 candidates")
    )
    recorded = _decide(gate, other, idempotency_key="decision-b")
    assert recorded.approval is not None
    gate.dispatch(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="dispatch-b",
    )
    conflict = service.create(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="key-2",
    )
    assert conflict.status == "idempotency_conflict"
    assert runner.calls == 1


def test_distinct_sessions_do_not_conflict_and_concurrency_creates_one_run():
    store = InMemoryApprovedRunStore()
    gate_a, _drafter_a, _store_a, _dispatcher_a, reply_a = _stack(id_prefix="a")
    other = "session_phase6d_other"
    gate_b, _drafter_b, _store_b, _dispatcher_b, reply_b = _stack(session_id=other, id_prefix="b")
    approved_a = _decide(gate_a, reply_a)
    approved_b = _decide(gate_b, reply_b)
    assert approved_a.approval is not None and approved_b.approval is not None
    gate_a.dispatch(
        session_id=SESSION,
        approval_id=approved_a.approval.approval_id,
        idempotency_key="dispatch-a",
    )
    gate_b.dispatch(
        session_id=other, approval_id=approved_b.approval.approval_id, idempotency_key="dispatch-b"
    )
    service_a, runner_a = _service(gate_a, store=store, start=10)
    service_b, runner_b = _service(gate_b, store=store, start=20)
    left = service_a.create(
        session_id=SESSION, approval_id=approved_a.approval.approval_id, idempotency_key="same"
    )
    right = service_b.create(
        session_id=other, approval_id=approved_b.approval.approval_id, idempotency_key="same"
    )
    assert left.run is not None and right.run is not None
    assert left.run.run_id != right.run.run_id
    assert runner_a.calls == 0 and runner_b.calls == 0
    assert service_a.process_next() is not None
    assert service_b.process_next() is not None
    assert runner_a.calls == 1 and runner_b.calls == 1
    concurrent_gate, _drafter, _store, _dispatcher, reply = _stack(id_prefix="c")
    approved = _decide(concurrent_gate, reply)
    assert approved.approval is not None
    concurrent_gate.dispatch(
        session_id=SESSION,
        approval_id=approved.approval.approval_id,
        idempotency_key="dispatch-c",
    )
    service, runner = _service(concurrent_gate)
    barrier = threading.Barrier(8)
    results: list = []

    def create(index: int) -> None:
        barrier.wait()
        results.append(
            service.create(
                session_id=SESSION,
                approval_id=approved.approval.approval_id,
                idempotency_key=f"parallel-{index}",
            )
        )

    threads = [threading.Thread(target=create, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {item.run.run_id for item in results} == {results[0].run.run_id}
    assert runner.calls == 0
    assert service.process_next() is not None
    assert runner.calls == 1


def test_retry_is_idempotent_and_blocked_unless_the_run_failed():
    failing = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN"))
    service, _runner, _gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    service._runner = failing
    approval = recorded.approval
    assert approval is not None
    failed = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert failed.run is not None
    failed = _finish(service, failed)
    assert failed.run.status == "failed"
    service._runner = FixtureApprovedRunRunner()
    retried = service.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-1")
    assert retried.run is not None
    assert retried.run.run_id == failed.run.run_id
    assert retried.run.attempt == 2
    assert retried.run.status == "queued"
    retried = _finish(service, retried)
    assert retried.run.status == "completed"
    duplicate = service.retry(
        session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-1"
    )
    assert duplicate.run is not None
    assert duplicate.run.attempt == 2
    assert duplicate.run.run_id == failed.run.run_id
    assert service._runner.calls == 1
    blocked = service.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-2")
    assert blocked.status == "retry_not_allowed"
    assert blocked.run is not None
    assert blocked.run.attempt == 2
    running = replace(retried.run, status="running")
    service._store._runs[running.run_id] = running
    denied = service.retry(
        session_id=SESSION, run_id=running.run_id, idempotency_key="retry-running"
    )
    assert denied.status == "retry_not_allowed"
    with pytest.raises(ApplicationContractError):
        retried.run.transition("running", recorded_at=WHEN.isoformat(), text="no")
    with pytest.raises(ApplicationContractError):
        retried.run.transition("queued", recorded_at=WHEN.isoformat(), text="no")
    with pytest.raises(ApplicationContractError):
        failed.run.transition("completed", recorded_at=WHEN.isoformat(), text="no")
    with pytest.raises(ApplicationContractError):
        failed.run.transition("queued", recorded_at=WHEN.isoformat(), text="no", attempt=0)


def test_concurrent_retries_do_not_open_two_attempts():
    failing = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN"))
    service, _runner, _gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    service._runner = failing
    approval = recorded.approval
    assert approval is not None
    failed = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert failed.run is not None
    failed = _finish(service, failed)
    service._runner = FixtureApprovedRunRunner()
    barrier = threading.Barrier(8)
    results: list = []

    def retry(index: int) -> None:
        barrier.wait()
        results.append(
            service.retry(
                session_id=SESSION,
                run_id=failed.run.run_id,
                idempotency_key=f"retry-{index}",
            )
        )

    threads = [threading.Thread(target=retry, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {item.run.run_id for item in results} == {failed.run.run_id}
    assert max(item.run.attempt for item in results) == 2
    assert service._runner.calls == 0
    assert service.process_next() is not None
    assert service._runner.calls == 1


def test_retry_key_replays_the_attempt_it_created_after_a_later_failure():
    failing = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN"))
    service, _runner, _gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    service._runner = failing
    approval = recorded.approval
    assert approval is not None
    failed = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert failed.run is not None
    failed = _finish(service, failed)
    assert failed.run.attempt == 1
    second = service.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-1")
    assert second.run is not None
    assert second.run.attempt == 2
    second = _finish(service, second)
    assert second.run.status == "failed"
    third = service.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-2")
    assert third.run is not None
    assert third.run.attempt == 3
    third = _finish(service, third)
    replay = service.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-1")
    assert replay.run is not None
    assert replay.run.run_id == failed.run.run_id
    assert replay.run.attempt == 2
    assert replay.run.status == "failed"
    assert [event.attempt for event in replay.run.events if event.status == "failed"] == [1, 2]
    observed = service.read(session_id=SESSION, run_id=failed.run.run_id)
    assert observed.run is not None
    assert observed.run.attempt == 3
    assert failing.calls == 3


def test_reading_a_failed_run_keeps_the_fixed_failure_sentence():
    failing = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN"))
    service, _runner, _gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    service._runner = failing
    approval = recorded.approval
    assert approval is not None
    failed = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert failed.run is not None
    failed = _finish(service, failed)
    sentence = "The fixture run failed. No scientific action was taken."
    assert failed.text == sentence
    assert failed.run.to_dict()["text"] == sentence
    observed = service.read(session_id=SESSION, run_id=failed.run.run_id)
    assert observed.status == "run_observed"
    assert observed.text == sentence
    assert observed.run is not None
    assert observed.run.to_dict()["text"] == sentence
    encoded = json.dumps(observed.to_dict())
    assert "Traceback" not in encoded
    assert "TOKEN" not in encoded


def test_read_does_not_execute_and_artifacts_are_deterministic_operational_metadata():
    created, service, runner, _gate, _drafter, _approval = _created()
    assert created.run is not None
    before = json.dumps(created.run.to_dict(), sort_keys=True)
    observed = service.read(session_id=SESSION, run_id=created.run.run_id)
    assert observed.status == "run_observed"
    assert json.dumps(observed.run.to_dict(), sort_keys=True) == before
    assert runner.calls == 1
    kinds = [artifact.kind for artifact in created.run.artifacts]
    assert kinds == [
        "fixture_receipt",
        "fixture_manifest",
        "fixture_summary",
        "input_digest",
        "output_digest",
        "operational_log",
    ]
    again = FixtureApprovedRunRunner().run(
        run_id=created.run.run_id,
        attempt=1,
        request_digest=created.run.request_digest,
    )
    assert [item.content_sha256 for item in again] == [
        item.content_sha256 for item in created.run.artifacts
    ]
    encoded = json.dumps(created.to_dict(), sort_keys=True)
    payload = created.to_dict()
    assert payload["scientific_validation"] is False
    assert payload["scientific_candidate"] is False
    assert payload["candidates"] == []
    assert payload["scientific_evidence"] == []
    assert payload["scientific_memory_written"] is False
    assert payload["semantic_promotion"] is False
    assert payload["execution_authorized"] is False
    assert "not scientific validation" in payload["text"].casefold()
    assert created.run.to_dict()["fixture"] is True
    assert created.run.to_dict()["scientific_validation"] is False
    assert "protein" not in encoded.casefold()
    for artifact in created.run.artifacts:
        body = artifact.to_dict()
        assert body["label"] == "fixture operational artifact"
        assert body["scientific_evidence"] is False
        assert body["scientific_candidate"] is False
        assert "path" not in body
    assert json.dumps(created.to_dict(), sort_keys=True) == json.dumps(
        created.to_dict(), sort_keys=True
    )


def test_boundaries_do_not_call_scientific_execution():
    names = (
        "approved_run.py",
        "approved_run_transport.py",
        "approved_run_store.py",
        "approved_run_config.py",
    )
    blobs = [
        (ROOT / "src/cellertz/application" / name).read_text(encoding="utf-8") for name in names
    ]
    source = blobs[0]
    modules: list[str] = []
    for blob in blobs:
        tree = ast.parse(blob)
        modules.extend(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        modules.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
    banned = (
        "databricks",
        "second_brain",
        "knowledge",
        "cellertz.execution",
        "cellertz.runs",
        "cellertz.domains",
    )
    for module in modules:
        if module.startswith("cellertz."):
            assert module.startswith("cellertz.application")
        for name in banned:
            assert name not in module
    for blob in blobs:
        assert "subprocess" not in blob
        assert "Popen" not in blob
        assert "urlopen" not in blob
        assert "socket" not in blob
        assert "ScientificRunMemory" not in blob
    assert "process-local and non-durable" in source
    assert "not scientific validation" in source.casefold()
    created, _service, _runner, _gate, _drafter, _approval = _created()
    assert isinstance(created.run, ApprovedRun)
