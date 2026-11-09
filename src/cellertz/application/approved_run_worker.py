"""Single-process background worker for fixture-only approved runs.

The worker owns no scientific authority. It consumes only server-created
queued records and invokes the fixture adapter through ApprovedRunService.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

from cellertz.application.approved_run import ApprovedRunService


class ApprovedRunWorker:
    """Poll one approved-run store from one background thread."""

    def __init__(
        self,
        service: ApprovedRunService,
        *,
        poll_seconds: float = 0.25,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)):
            raise TypeError("invalid_approved_run_worker_poll_seconds")
        if not math.isfinite(float(poll_seconds)) or poll_seconds <= 0 or poll_seconds > 60:
            raise ValueError("invalid_approved_run_worker_poll_seconds")
        self._service = service
        self._poll_seconds = float(poll_seconds)
        self._thread_factory = thread_factory
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.clear()
            thread = self._thread_factory(
                target=self._loop,
                name="cellertz-approved-run-worker",
                daemon=False,
            )
            self._thread = thread
            thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, *, timeout: float = 5.0) -> bool:
        self._stop.set()
        self._wake.set()
        with self._guard:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        stopped = not thread.is_alive()
        if stopped:
            with self._guard:
                if self._thread is thread:
                    self._thread = None
        return stopped

    def run_once(self) -> bool:
        try:
            return self._service.process_next() is not None
        except Exception:  # noqa: BLE001 — no traceback, path, or driver text leaves the worker
            return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.run_once():
                continue
            self._wake.wait(self._poll_seconds)
            self._wake.clear()
