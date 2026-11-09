"""Phase 6G asynchronous fixture worker. No scientific execution."""

from __future__ import annotations

import threading

import pytest

from cellertz.application.approved_run import FixtureApprovedRunRunner
from cellertz.application.approved_run_config import (
    ApprovedRunConfigurationError,
    resolve_approved_run_worker_poll_seconds,
)
from cellertz.application.approved_run_worker import ApprovedRunWorker
from cellertz.application.request_draft import DesignDraftCommand
from tests.test_approved_run import SESSION, _demonstrated


def _queue(service, recorded, key: str = "run-1"):
    approval = recorded.approval
    assert approval is not None
    queued = service.create(
        session_id=SESSION,
        approval_id=approval.approval_id,
        idempotency_key=key,
    )
    assert queued.status == "run_queued"
    assert queued.run is not None and queued.run.status == "queued"
    return queued


def test_create_and_retry_enqueue_without_calling_the_runner():
    service, runner, _gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    queued = _queue(service, recorded)
    assert runner.calls == 0

    worker = ApprovedRunWorker(service)
    assert worker.run_once() is True
    assert worker.run_once() is False
    assert runner.calls == 1
    completed = service.read(session_id=SESSION, run_id=queued.run.run_id)
    assert completed.run is not None and completed.run.status == "completed"

    service._runner = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN"))
    # A completed attempt cannot be retried and cannot invoke the replacement runner.
    blocked = service.retry(
        session_id=SESSION, run_id=queued.run.run_id, idempotency_key="retry-completed"
    )
    assert blocked.status == "retry_not_allowed"
    assert service._runner.calls == 0


def test_failed_retry_is_a_new_queued_attempt_processed_later():
    service, _runner, _gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    failing = FixtureApprovedRunRunner(RuntimeError("Traceback TOKEN"))
    service._runner = failing
    queued = _queue(service, recorded)
    worker = ApprovedRunWorker(service)
    assert worker.run_once() is True
    failed = service.read(session_id=SESSION, run_id=queued.run.run_id)
    assert failed.run is not None and failed.run.status == "failed"
    assert failing.calls == 1

    successful = FixtureApprovedRunRunner()
    service._runner = successful
    retried = service.retry(
        session_id=SESSION,
        run_id=queued.run.run_id,
        idempotency_key="retry-1",
    )
    assert retried.status == "run_queued"
    assert retried.run is not None and retried.run.attempt == 2
    assert successful.calls == 0
    assert worker.run_once() is True
    observed = service.read(session_id=SESSION, run_id=queued.run.run_id)
    assert observed.run is not None and observed.run.status == "completed"
    assert observed.run.attempt == 2
    assert successful.calls == 1


class _BlockingRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.fixture = FixtureApprovedRunRunner()

    def run(self, *, run_id: str, attempt: int, request_digest: str):
        self.calls += 1
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("fixture timeout")
        return self.fixture.run(run_id=run_id, attempt=attempt, request_digest=request_digest)


def test_background_worker_persists_running_before_adapter_and_stops_cooperatively():
    service, _runner, _gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    blocking = _BlockingRunner()
    service._runner = blocking
    queued = _queue(service, recorded)
    worker = ApprovedRunWorker(service, poll_seconds=0.01)
    worker.start()
    worker.start()
    assert blocking.started.wait(2)
    running = service.read(session_id=SESSION, run_id=queued.run.run_id)
    assert running.run is not None and running.run.status == "running"
    assert [event.status for event in running.run.events] == ["queued", "running"]
    assert worker.stop(timeout=0.01) is False
    blocking.release.set()
    assert worker.stop(timeout=2) is True
    completed = service.read(session_id=SESSION, run_id=queued.run.run_id)
    assert completed.run is not None and completed.run.status == "completed"
    assert blocking.calls == 1


def test_concurrent_workers_claim_one_attempt_once():
    service, runner, _gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    queued = _queue(service, recorded)
    workers = [ApprovedRunWorker(service), ApprovedRunWorker(service)]
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def work(item: ApprovedRunWorker) -> None:
        barrier.wait()
        results.append(item.run_once())

    threads = [threading.Thread(target=work, args=(item,)) for item in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True]
    assert runner.calls == 1
    observed = service.read(session_id=SESSION, run_id=queued.run.run_id)
    assert observed.run is not None and observed.run.status == "completed"


def test_worker_revalidates_after_queue_and_fails_closed_without_runner():
    service, runner, _gate, drafter, _store, _dispatcher, recorded = _demonstrated()
    queued = _queue(service, recorded)
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
    assert ApprovedRunWorker(service).run_once() is True
    observed = service.read(session_id=SESSION, run_id=queued.run.run_id)
    assert observed.run is not None and observed.run.status == "failed"
    assert observed.text == "The fixture run failed. No scientific action was taken."
    assert runner.calls == 0


def test_worker_thread_stays_non_daemon_when_started_from_a_daemon_thread():
    service, _runner, _gate, _drafter, _store, _dispatcher, _recorded = _demonstrated()
    worker = ApprovedRunWorker(service, poll_seconds=60)
    started = threading.Event()

    def launch() -> None:
        worker.start()
        started.set()

    parent = threading.Thread(target=launch, daemon=True)
    parent.start()
    parent.join(timeout=2)
    assert not parent.is_alive()
    assert started.wait(2)
    assert worker._thread is not None
    assert worker._thread.daemon is False
    assert worker.stop(timeout=2) is True


@pytest.mark.parametrize("value", ["", "0", "-1", "61", "nan", "fixture"])
def test_worker_poll_configuration_fails_closed(value: str):
    with pytest.raises(ApprovedRunConfigurationError) as caught:
        resolve_approved_run_worker_poll_seconds(
            {"CELLERTZ_APPROVED_RUN_WORKER_POLL_SECONDS": value}
        )
    assert caught.value.code == "invalid_approved_run_worker_poll_seconds"


def test_unexpected_claim_error_fails_closed_without_leaking_or_running_twice():
    service, runner, _gate, _drafter, _store, _dispatcher, recorded = _demonstrated()
    queued = _queue(service, recorded)

    def explode(_run: object) -> bool:
        raise RuntimeError("Traceback /tmp/secret TOKEN")

    service._authorized_for_run = explode
    assert ApprovedRunWorker(service).run_once() is True
    observed = service.read(session_id=SESSION, run_id=queued.run.run_id)
    assert observed.run is not None and observed.run.status == "failed"
    encoded = observed.text + "".join(event.text for event in observed.run.events)
    for leak in ("Traceback", "/tmp/secret", "TOKEN", "RuntimeError"):
        assert leak not in encoded
    assert runner.calls == 0
    assert ApprovedRunWorker(service).run_once() is False


def test_worker_poll_configuration_has_a_bounded_default():
    assert resolve_approved_run_worker_poll_seconds({}) == 0.25
    assert (
        resolve_approved_run_worker_poll_seconds(
            {"CELLERTZ_APPROVED_RUN_WORKER_POLL_SECONDS": "1.5"}
        )
        == 1.5
    )
