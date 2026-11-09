"""Single-process background worker for scientific runs.

This is not ApprovedRunWorker. It claims only scientific-run records and
calls ScientificRunService, which keeps the executor outside the database
transaction. Production execution stays disabled unless the service was
constructed with server-owned configuration.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

from cellertz.application.scientific_run import ScientificRunService


class ScientificRunWorker:
    """Poll one scientific-run store from one background thread."""

    def __init__(
        self,
        service: ScientificRunService,
        *,
        poll_seconds: float = 0.25,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)):
            raise TypeError("invalid_scientific_run_worker_poll_seconds")
        if not math.isfinite(float(poll_seconds)) or poll_seconds <= 0 or poll_seconds > 60:
            raise ValueError("invalid_scientific_run_worker_poll_seconds")
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
                name="cellertz-scientific-run-worker",
                daemon=False,
            )
            self._thread = thread
            thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, *, timeout: float = 5.0) -> bool:
        self._stop.set()
        self._wake.set()
        shutdown = getattr(self._service, "request_shutdown", None)
        if callable(shutdown):
            shutdown()
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
