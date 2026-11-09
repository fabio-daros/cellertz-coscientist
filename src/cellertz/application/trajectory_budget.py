"""Attempted-trajectory budget for pinned BindCraft efb5bfeb.

The native ``check_n_trajectories`` counts PDB files in
``Trajectory/Relaxed`` only. ``binder_hallucination`` can raise before
``save_pdb``, and one call can write several files. Neither outcome is
the attempt count.

This adapter reserves one attempt immediately before calling
``binder_hallucination``. The reservation is on disk before that call.
A later failure, including one that leaves no PDB, consumes it. Resume
is not supported: an existing ledger is refused and is not reset.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ADAPTER_VERSION = "bindcraft_attempted_trajectory_budget_v2"
BINDCRAFT_COMMIT = "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d"
BINDCRAFT_SCRIPT_SHA256 = "cc1103a96ac4b414d9e765c0ba58302914aacc3f47504a2b669a5a47f0deb33a"
ATTEMPT_DIRECTORIES = (
    "Trajectory",
    "Trajectory/Relaxed",
    "Trajectory/LowConfidence",
    "Trajectory/Clashing",
)
_TERMINAL = {"failed", "finished"}
_COMPLIANT_REASONS = {
    "attempted_trajectory_budget_exhausted",
    "successful_early_completion",
    "attempt_failed",
}


class TrajectoryBudgetError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def script_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def pdb_stems(directory: Path) -> set[str]:
    if not directory.is_dir():
        raise TrajectoryBudgetError("budget_directories_unreadable")
    names: set[str] = set()
    for entry in directory.iterdir():
        if entry.name.startswith(".") or entry.suffix != ".pdb" or not entry.is_file():
            continue
        names.add(entry.stem)
    return names


def classify_budget_evidence(
    document: dict[str, Any] | None,
    *,
    process_disposition: str = "observed",
) -> dict[str, Any]:
    """Read scheduler evidence. A deadline or a missing ledger is not compliance."""

    if process_disposition == "deadline":
        return {"termination_reason": "deadline_terminated", "compliance_established": False}
    reason, compliance = _compliance(document)
    return {"termination_reason": reason, "compliance_established": compliance}


def write_budget_report(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def bind_attempted_trajectory_budget(
    functions_module: Any,
    *,
    requested: int,
    report_path: Path,
) -> None:
    """Replace the native scheduler hooks. The original trajectory counter is not consulted."""

    if type(requested) is not int or isinstance(requested, bool) or requested < 1:
        raise TrajectoryBudgetError("trajectory_budget_invalid")
    if not hasattr(functions_module, "check_n_trajectories"):
        raise TrajectoryBudgetError("trajectory_budget_unavailable")
    if not hasattr(functions_module, "binder_hallucination"):
        raise TrajectoryBudgetError("trajectory_budget_unavailable")
    original_hallucination = functions_module.binder_hallucination
    original_accepted = getattr(functions_module, "check_accepted_designs", None)

    def check_n_trajectories(design_paths: dict[str, Any], advanced_settings: dict[str, Any]) -> bool:
        if advanced_settings.get("max_trajectories") != requested:
            _stop(report_path, requested, "approved_budget_mismatch")
            return True
        try:
            document = _read_ledger(report_path)
        except TrajectoryBudgetError:
            _stop(report_path, requested, "missing_evidence")
            return True
        _annotate_files(document, design_paths)
        reason, compliance = _compliance(document)
        if reason == "missing_evidence" or not _schedulable(document, requested):
            document["termination_reason"] = reason if reason == "missing_evidence" else _stop_reason(document)
            document["compliance_established"] = compliance and document["termination_reason"] in _COMPLIANT_REASONS
            _write(report_path, document)
            return True
        document["termination_reason"] = "budget_open"
        document["compliance_established"] = False
        _write(report_path, document)
        return False

    def binder_hallucination(*args: Any, **kwargs: Any) -> Any:
        _reserve(report_path, requested)
        try:
            result = original_hallucination(*args, **kwargs)
        except Exception:
            _complete(report_path, "failed")
            raise
        _complete(report_path, "finished")
        return result

    def check_accepted_designs(*args: Any, **kwargs: Any) -> Any:
        if original_accepted is None:
            raise TrajectoryBudgetError("trajectory_budget_unavailable")
        reached = original_accepted(*args, **kwargs)
        if reached is True:
            _record_early_completion(report_path, requested)
        return reached

    functions_module.check_n_trajectories = check_n_trajectories
    functions_module.binder_hallucination = binder_hallucination
    if original_accepted is not None:
        functions_module.check_accepted_designs = check_accepted_designs
    generic = getattr(functions_module, "generic_utils", None)
    if generic is not None and hasattr(generic, "check_n_trajectories"):
        generic.check_n_trajectories = check_n_trajectories


def install_attempted_trajectory_budget(
    functions_module: Any,
    *,
    script_path: Path,
    advanced_path: Path,
    report_path: Path,
) -> None:
    """Bind the budget to the pinned script and the approved advanced file."""

    if script_sha256(script_path) != BINDCRAFT_SCRIPT_SHA256:
        raise TrajectoryBudgetError("bindcraft_source_unverified")
    requested = _requested_budget(advanced_path)
    if report_path.exists():
        _refuse_existing_ledger(report_path)
    document = _fresh(requested)
    write_budget_report(report_path, document)
    bind_attempted_trajectory_budget(
        functions_module,
        requested=requested,
        report_path=report_path,
    )


def _requested_budget(advanced_path: Path) -> int:
    try:
        advanced = json.loads(advanced_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrajectoryBudgetError("trajectory_budget_invalid") from exc
    if not isinstance(advanced, dict):
        raise TrajectoryBudgetError("trajectory_budget_invalid")
    requested = advanced.get("max_trajectories")
    if type(requested) is not int or isinstance(requested, bool) or requested < 1:
        raise TrajectoryBudgetError("trajectory_budget_invalid")
    return requested


def _refuse_existing_ledger(path: Path) -> None:
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrajectoryBudgetError("trajectory_budget_evidence_unreadable") from exc
    if not isinstance(parsed, dict):
        raise TrajectoryBudgetError("trajectory_budget_evidence_unreadable")
    raise TrajectoryBudgetError("trajectory_budget_retry_refused")


def _fresh(requested: int) -> dict[str, Any]:
    return {
        "adapter_version": ADAPTER_VERSION,
        "bindcraft_commit": BINDCRAFT_COMMIT,
        "bindcraft_script_sha256": BINDCRAFT_SCRIPT_SHA256,
        "requested_attempted_trajectories": requested,
        "resume_supported": False,
        "reservations": [],
        "observed_attempted_trajectories": 0,
        "native_counter": "Trajectory/Relaxed PDB files",
        "enforced_counter": "one scheduler reservation written before each binder_hallucination call",
        "final_candidates_counted": False,
        "model_structures_counted": False,
        "termination_reason": "budget_open",
        "compliance_established": False,
    }


def _read_ledger(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrajectoryBudgetError("missing_evidence") from exc
    if not isinstance(document, dict) or document.get("adapter_version") != ADAPTER_VERSION:
        raise TrajectoryBudgetError("missing_evidence")
    reservations = document.get("reservations")
    if not isinstance(reservations, list):
        raise TrajectoryBudgetError("missing_evidence")
    return document


def _reserve(path: Path, requested: int) -> None:
    document = _read_ledger(path)
    reservations = document["reservations"]
    if len(reservations) >= requested or not _schedulable(document, requested):
        raise TrajectoryBudgetError("attempted_trajectory_budget_exhausted")
    reservations.append(
        {
            "ordinal": len(reservations) + 1,
            "state": "started",
            "scheduler_reserved": True,
        }
    )
    document["observed_attempted_trajectories"] = len(reservations)
    document["termination_reason"] = "budget_open"
    document["compliance_established"] = False
    write_budget_report(path, document)


def _complete(path: Path, state: str) -> None:
    try:
        document = _read_ledger(path)
    except TrajectoryBudgetError:
        return
    reservations = document["reservations"]
    if not reservations or reservations[-1].get("state") != "started":
        document["termination_reason"] = "missing_evidence"
        document["compliance_established"] = False
        _write(path, document)
        return
    reservations[-1]["state"] = state
    document["observed_attempted_trajectories"] = len(reservations)
    if state == "failed":
        document["termination_reason"] = "attempt_failed"
    elif len(reservations) == document["requested_attempted_trajectories"]:
        document["termination_reason"] = "attempted_trajectory_budget_exhausted"
    else:
        document["termination_reason"] = "budget_open"
    reason, compliance = _compliance(document)
    document["termination_reason"] = reason
    document["compliance_established"] = compliance
    _write(path, document)


def _record_early_completion(path: Path, requested: int) -> None:
    try:
        document = _read_ledger(path)
    except TrajectoryBudgetError:
        _stop(path, requested, "missing_evidence")
        return
    document["requested_attempted_trajectories"] = requested
    document["termination_reason"] = "successful_early_completion"
    reason, compliance = _compliance(document)
    document["termination_reason"] = reason
    document["compliance_established"] = compliance
    _write(path, document)


def _schedulable(document: dict[str, Any], requested: int) -> bool:
    reservations = document["reservations"]
    if len(reservations) >= requested:
        return False
    for item in reservations:
        if not isinstance(item, dict) or item.get("scheduler_reserved") is not True:
            return False
        if item.get("state") not in _TERMINAL:
            return False
    return True


def _stop_reason(document: dict[str, Any]) -> str:
    reason, _compliance_flag = _compliance(document)
    if reason in _COMPLIANT_REASONS:
        return reason
    return "missing_evidence"


def _compliance(document: dict[str, Any] | None) -> tuple[str, bool]:
    if not isinstance(document, dict) or document.get("adapter_version") != ADAPTER_VERSION:
        return "missing_evidence", False
    if document.get("resume_supported") is not False:
        return "missing_evidence", False
    reservations = document.get("reservations")
    requested = document.get("requested_attempted_trajectories")
    if not isinstance(reservations, list) or type(requested) is not int or isinstance(requested, bool):
        return "missing_evidence", False
    if requested < 1:
        return "missing_evidence", False
    states: list[str] = []
    for item in reservations:
        if not isinstance(item, dict) or item.get("scheduler_reserved") is not True:
            return "missing_evidence", False
        state = item.get("state")
        if not isinstance(state, str):
            return "missing_evidence", False
        states.append(state)
    if any(state == "started" for state in states) or any(state not in _TERMINAL for state in states):
        return "missing_evidence", False
    count = len(states)
    if count > requested:
        return "missing_evidence", False
    reason = document.get("termination_reason")
    if reason == "successful_early_completion":
        return reason, True
    if reason == "attempt_failed" and count >= 1 and states[-1] == "failed":
        return reason, True
    if reason == "attempted_trajectory_budget_exhausted" and count == requested:
        return reason, True
    if reason in {"budget_open", "approved_budget_mismatch", "deadline_terminated", "missing_evidence"}:
        return str(reason), False
    return "missing_evidence", False


def _annotate_files(document: dict[str, Any], design_paths: dict[str, Any]) -> None:
    try:
        stems = {key: pdb_stems(Path(design_paths[key])) for key in ATTEMPT_DIRECTORIES}
    except (KeyError, OSError, TrajectoryBudgetError, TypeError):
        document["observed_relaxed_trajectories"] = None
        document["observed_low_confidence_trajectories"] = None
        document["observed_clashing_trajectories"] = None
        return
    document["observed_relaxed_trajectories"] = len(stems["Trajectory/Relaxed"])
    document["observed_low_confidence_trajectories"] = len(stems["Trajectory/LowConfidence"])
    document["observed_clashing_trajectories"] = len(stems["Trajectory/Clashing"])


def _stop(path: Path, requested: int, reason: str) -> None:
    if path.exists():
        try:
            document = _read_ledger(path)
        except TrajectoryBudgetError:
            return
    else:
        document = _fresh(requested)
    document["termination_reason"] = reason
    document["compliance_established"] = False
    if reason != "approved_budget_mismatch":
        assessed, compliance = _compliance(document)
        document["termination_reason"] = assessed
        document["compliance_established"] = compliance
    _write(path, document)


def _write(path: Path, document: dict[str, Any]) -> None:
    try:
        write_budget_report(path, document)
    except OSError:
        return
