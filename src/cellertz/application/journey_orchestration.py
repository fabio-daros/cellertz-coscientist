"""Background continuation for a newly launched professor journey.

The HTTP launch commits a queued dispatch and returns the attempt identity.
This worker claims that record in the journey store, then calls the existing
launcher once. It does not use ApprovedRunWorker or ScientificRunWorker.

An in-process lock does not make execution exactly once. The claim is a
compare-and-set inside the journey store transaction. A crash after that
commit and before the launcher returns is not retried as a new GPU launch.
Historical rounds have no automatic dispatch and are left alone. A scoring
audit stays a separate operator action.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_TERMINAL_DISPATCH = frozenset({"completed", "blocked", "failed", "unresolved", "refused"})
_CLEAN_NO_CANDIDATE = frozenset({"budget_exhausted"})


class JourneyOrchestrationWorker:
    """Poll one journey service from one background thread."""

    def __init__(
        self,
        service: Any,
        *,
        poll_seconds: float = 0.25,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)):
            raise TypeError("invalid_journey_orchestration_poll_seconds")
        if not math.isfinite(float(poll_seconds)) or poll_seconds <= 0 or poll_seconds > 60:
            raise ValueError("invalid_journey_orchestration_poll_seconds")
        self._service = service
        self._poll_seconds = float(poll_seconds)
        self._thread_factory = thread_factory
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self._recovered = False

    def start(self) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.clear()
            if not self._recovered:
                self._service.recover_automatic_dispatches()
                self._recovered = True
            thread = self._thread_factory(
                target=self._loop,
                name="cellertz-journey-orchestration",
                daemon=False,
            )
            self._thread = thread
            thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, *, timeout: float = 5.0) -> bool:
        """Ask the loop to stop. An in-flight launcher call is not killed here."""

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
            return self._service.continue_post_generation() is True
        except Exception:  # noqa: BLE001 — the worker does not leak launcher text
            return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.run_once():
                continue
            self._wake.wait(self._poll_seconds)
            self._wake.clear()


def new_dispatch(
    parsed: Mapping[str, Any],
    *,
    attempt_id: str,
    attempt_directory: str,
) -> dict[str, Any]:
    now = _now()
    dispatch = {
        "dispatch_id": f"dispatch_{uuid4().hex}",
        "automatic": True,
        "status": "queued",
        "phase": "generation",
        "owner": None,
        "generation_invoked": False,
        "attempt_id": attempt_id,
        "created_at": now,
        "event_sequence": 0,
        "events": [],
        "launch_request": {
            "plan_id": parsed["plan_id"],
            "revision": parsed["revision"],
            "digest": parsed["digest"],
            "reviewer_id": parsed["reviewer_id"],
            "reason": parsed["reason"],
            "idempotency_key": parsed["idempotency_key"],
            "gpu_index": parsed["gpu_index"],
            "attempt_id": attempt_id,
            "attempt_directory": attempt_directory,
        },
        "scientific_validation_established": False,
        "experimental_validation_established": False,
        "replaces_historical_round": False,
    }
    append_dispatch_event(
        dispatch,
        kind="dispatch_queued",
        status="queued",
        phase="generation",
        detail="The launch identity was saved. Generation has not started.",
    )
    return dispatch


def dispatch_is_queued(document: Mapping[str, Any]) -> bool:
    dispatch = document.get("post_generation")
    if not isinstance(dispatch, dict) or dispatch.get("automatic") is not True:
        return False
    if dispatch.get("status") != "queued" or dispatch.get("generation_invoked") is True:
        return False
    return dispatch.get("owner") in {None, ""}


def claim_dispatch(document: dict[str, Any], owner: str) -> None:
    dispatch = document.get("post_generation")
    if not isinstance(dispatch, dict):
        return
    dispatch["status"] = "running"
    dispatch["phase"] = "generation"
    dispatch["owner"] = owner
    dispatch["generation_invoked"] = True
    dispatch["claimed_at"] = _now()
    append_dispatch_event(
        dispatch,
        kind="generation_claimed",
        status="running",
        phase="generation",
        detail="This worker owns the saved launch. The GPU process has not been confirmed.",
    )
    _copy_onto_round(document)


def append_dispatch_event(
    dispatch: dict[str, Any],
    *,
    kind: str,
    status: str,
    phase: str,
    detail: str,
) -> None:
    sequence = int(dispatch.get("event_sequence") or 0) + 1
    dispatch["event_sequence"] = sequence
    events = [item for item in dispatch.get("events") or [] if isinstance(item, dict)]
    events.append(
        {
            "event_id": f"{dispatch.get('dispatch_id')}:{sequence}",
            "sequence": sequence,
            "at": _now(),
            "kind": kind,
            "status": status,
            "phase": phase,
            "implementation": "JourneyOrchestrationWorker",
            "implementation_kind": "journey_post_generation",
            "agent_executed": False,
            "detail": detail,
        }
    )
    dispatch["events"] = events


def recover_document(document: dict[str, Any]) -> dict[str, Any] | None:
    """Reconcile one saved dispatch. This never calls the launcher."""

    dispatch = document.get("post_generation")
    if not isinstance(dispatch, dict) or dispatch.get("automatic") is not True:
        return None
    if dispatch.get("status") in _TERMINAL_DISPATCH:
        return None
    if _specialist_terminal(document):
        _finish_dispatch(document, _status_from_specialists(document), "specialist")
        return document
    if _running_specialist(document):
        _mark_running_specialist_unresolved(document)
        _finish_dispatch(
            document,
            "unresolved",
            "specialist",
            "A specialist stage was running when the process stopped. It was not replayed.",
        )
        return document
    if dispatch.get("status") == "queued" and dispatch.get("generation_invoked") is not True:
        return None
    if dispatch.get("generation_invoked") is True and _generation_process_alive(document):
        return None
    execution = document.get("execution") if isinstance(document.get("execution"), dict) else {}
    if execution.get("terminal") is True and execution.get("status") in _CLEAN_NO_CANDIDATE | {"verified"}:
        return None
    _finish_dispatch(
        document,
        "unresolved",
        "generation",
        "Generation ownership was saved, but the GPU launch was not started again after restart.",
    )
    if isinstance(execution, dict) and execution.get("terminal") is not True:
        execution = dict(execution)
        execution["status"] = "execution_unresolved"
        execution["terminal"] = False
        execution["launched"] = execution.get("launched") is True
        document["execution"] = execution
        _copy_onto_round(document)
    return document


def needs_specialist_follow_up(document: Mapping[str, Any]) -> bool:
    """True when generation was saved and post-processing has not finished.

    This does not mean the GPU process should be started.
    """

    dispatch = document.get("post_generation")
    if not isinstance(dispatch, dict) or dispatch.get("automatic") is not True:
        return False
    if dispatch.get("status") in _TERMINAL_DISPATCH or dispatch.get("generation_invoked") is not True:
        return False
    if _generation_process_alive(document) or _running_specialist(document) or _specialist_terminal(document):
        return False
    execution = document.get("execution")
    if not isinstance(execution, dict) or execution.get("terminal") is not True:
        return False
    return follow_up_decision(execution) in {"specialists", "unresolved", "failed"}


def follow_up_decision(execution: Mapping[str, Any]) -> str:
    """Choose the post-generation step from the saved launcher outcome."""

    status = execution.get("status")
    if status in _CLEAN_NO_CANDIDATE:
        return "specialists"
    if execution.get("terminal") is not True:
        if status == "execution_unresolved":
            return "unresolved"
        return "running"
    if status == "verified" and execution.get("accepted_candidate") is True:
        return "specialists"
    if status == "verified":
        return "unresolved"
    if status in {"launch_failed", "failed", "outputs_rejected", "launcher_exception", "vram_insufficient"}:
        return "failed"
    if status == "execution_unresolved":
        return "unresolved"
    return "failed"


def specialist_dispatch_status(document: Mapping[str, Any]) -> tuple[str, str]:
    publication = _publication(document)
    if isinstance(publication, dict) and publication.get("status") not in {None, "verified"}:
        return "failed", "publication"
    progress = _specialist_progress_status(document)
    if progress in {"failed", "interrupted"}:
        return "failed", "specialist"
    if _any_stage(document, "blocked") and not _scoring_completed(document):
        return "blocked", "specialist"
    if _scoring_completed(document) or progress == "recorded":
        return "completed", "specialist"
    return "blocked", "specialist"


def finish_dispatch_record(
    document: dict[str, Any],
    status: str,
    phase: str,
    detail: str,
) -> None:
    _finish_dispatch(document, status, phase, detail)


def _finish_dispatch(document: dict[str, Any], status: str, phase: str, detail: str | None = None) -> None:
    dispatch = document.get("post_generation")
    if not isinstance(dispatch, dict):
        return
    dispatch["status"] = status
    dispatch["phase"] = phase
    dispatch["finished_at"] = _now()
    append_dispatch_event(
        dispatch,
        kind="post_generation_finished",
        status=status,
        phase=phase,
        detail=detail or f"Post-generation status is {status}.",
    )
    _copy_onto_round(document)


def _copy_onto_round(document: dict[str, Any]) -> None:
    dispatch = document.get("post_generation")
    rounds = document.get("rounds")
    active = document.get("active_round_id")
    if not isinstance(rounds, list):
        return
    for item in rounds:
        if isinstance(item, dict) and item.get("round_id") == active:
            item["post_generation"] = dispatch
            if isinstance(document.get("execution"), dict):
                item["execution"] = document["execution"]


def _specialist_terminal(document: Mapping[str, Any]) -> bool:
    progress = _specialist_progress_status(document)
    return progress in {"recorded", "failed", "interrupted"}


def _specialist_progress_status(document: Mapping[str, Any]) -> str | None:
    for owner in _round_then_document(document):
        progress = owner.get("specialist_progress")
        if isinstance(progress, dict) and isinstance(progress.get("status"), str):
            return progress["status"]
    return None


def _running_specialist(document: Mapping[str, Any]) -> bool:
    for owner in _round_then_document(document):
        stages = owner.get("specialist_stages")
        if isinstance(stages, list) and any(
            isinstance(item, dict) and item.get("status") == "running" for item in stages
        ):
            return True
    return False


def _mark_running_specialist_unresolved(document: dict[str, Any]) -> None:
    now = _now()
    for owner in _round_then_document(document):
        stages = owner.get("specialist_stages")
        if not isinstance(stages, list):
            continue
        for stage in stages:
            if isinstance(stage, dict) and stage.get("status") == "running":
                stage["status"] = "unresolved"
                stage["agent_executed"] = False
                stage["completed_at"] = now
                stage["failure_reason"] = (
                    "The specialist stage was running when the process stopped. It was not replayed."
                )


def _publication(document: Mapping[str, Any]) -> dict[str, Any] | None:
    for owner in _round_then_document(document):
        publication = owner.get("results_publication")
        if isinstance(publication, dict):
            return publication
    return None


def _any_stage(document: Mapping[str, Any], status: str) -> bool:
    for owner in _round_then_document(document):
        stages = owner.get("specialist_stages")
        if isinstance(stages, list) and any(
            isinstance(item, dict) and item.get("status") == status for item in stages
        ):
            return True
    return False


def _scoring_completed(document: Mapping[str, Any]) -> bool:
    for owner in _round_then_document(document):
        stages = owner.get("specialist_stages")
        if not isinstance(stages, list):
            continue
        for stage in stages:
            if (
                isinstance(stage, dict)
                and stage.get("stage_id") == "scoring"
                and stage.get("status") == "completed"
                and stage.get("agent_executed") is True
            ):
                output = stage.get("output")
                if isinstance(output, dict) and isinstance(output.get("ranking_score"), (int, float)):
                    return True
    return False


def _status_from_specialists(document: Mapping[str, Any]) -> str:
    status, _phase = specialist_dispatch_status(document)
    return status


def _round_then_document(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    owners: list[Mapping[str, Any]] = []
    rounds = document.get("rounds")
    active = document.get("active_round_id")
    if isinstance(rounds, list):
        for item in rounds:
            if isinstance(item, dict) and item.get("round_id") == active:
                owners.append(item)
    owners.append(document)
    return owners


def _generation_process_alive(document: Mapping[str, Any]) -> bool:
    execution = document.get("execution")
    if not isinstance(execution, dict):
        return False
    directory = execution.get("attempt_directory")
    if not isinstance(directory, str):
        return False
    progress_path = Path(directory) / "progress.json"
    pid_path = Path(directory) / "supervisor.pid"
    if not progress_path.is_file() or not pid_path.is_file():
        return False
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        worker = int(progress.get("worker_pid"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if progress.get("status") != "started" or worker <= 1:
        return False
    try:
        os.kill(worker, 0)
    except OSError:
        return False
    return True


def _now() -> str:
    return datetime.now(UTC).isoformat()
