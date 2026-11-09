"""Bounded Apptainer demonstration. This is not the Docker production path.

The language model can draft a plan. It cannot approve or launch.
A human approval record remains a separate action. Run design is the
explicit click for the revision on screen and does not invent that record.
``no_filters`` is accepted only as a technical demonstration.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cellertz.application.trajectory_budget import ADAPTER_VERSION, classify_budget_evidence
from cellertz.domains.protein_design.adapters.bindcraft import STATS_FILENAME
from cellertz.domains.protein_design.agents.generation import GenerationRequest
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    ATTEMPTED_TRAJECTORY_BUDGET_UPPER_BOUND,
    bounded_sequence_limits,
    build_apptainer_plan,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    BindCraftRealExecutionBridge,
    developer_bindcraft_authorization,
)
from cellertz.runtime.base import ExecutionRequest, ExecutionResult

PROFILE_ID = "pdl1-bindcraft-apptainer-demonstration-v1"
SIF_PATH = "/home/fabio/containers/cellertz/bindcraft/bindcraft-preflight.sif"
SIF_SHA256 = "bec155fd18c4a0f6150b44328673fa18e10431b4567598eebf8e4ccb9930f0b6"
RESERVED_ATTEMPTS = frozenset({"attempt.2LUTUOX4", "attempt.uUVN3umi"})
DEMONSTRATION_SEED = 1
BOOTSTRAP_SEED_MINIMUM = 1
BOOTSTRAP_SEED_MAXIMUM = 2**32 - 1
DEMONSTRATION_LENGTH = 65
DEMONSTRATION_CPUS = 4
DEMONSTRATION_MEMORY_MIB = 32768
DEMONSTRATION_DEADLINE_SECONDS = 7200
FILTER_POLICY = "no_filters"
SMOKE_TOKEN = "technical-smoke-test"
RUNTIME_NAME = "apptainer-group-supervisor"
RUNTIME_VERSION = "apptainer_group_supervisor_v1"
_FORBIDDEN_ROOTS = ("/home/fabio/projects",)
_ROLES = (
    ("professor_request", "The professor's words. They do not approve or launch."),
    ("plan_draft", "A language model may propose fields. It cannot approve."),
    ("human_approval", "A person records approval. Plan approval does not launch."),
    ("explicit_launch", "A separate human action starts the process."),
    ("bindcraft_gpu", "Apptainer runs BindCraft on one visible GPU."),
    ("output_check", "Deterministic hash, sequence, model, and metric checks."),
    ("geometric_contacts", "Deterministic heavy-atom distances from PDB bytes."),
    ("metric_read", "Deterministic read of recorded cells. It does not score."),
    ("narrative", "A language model may interpret tool evidence. It creates no measurement."),
    ("ipsae_ranking_score", "Not computed. Absent values stay absent."),
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def demonstration_contract() -> dict[str, Any]:
    """Reviewable limits. This does not approve or start a process."""

    return {
        "profile_id": PROFILE_ID,
        "impersonates_docker_profile": False,
        "clears_docker_readiness_gates": False,
        "scientific_validation": False,
        "filter_policy": FILTER_POLICY,
        "filter_choice": SMOKE_TOKEN,
        "filter_is_scientific_validation": False,
        "seed": DEMONSTRATION_SEED,
        "seed_role": "numpy_bootstrap_seed",
        "trajectory_seed_selection": "bindcraft_np_random_randint_0_999999",
        "attempted_trajectory_budget_upper_bound": ATTEMPTED_TRAJECTORY_BUDGET_UPPER_BOUND,
        "binder_length": DEMONSTRATION_LENGTH,
        "n_candidates": 1,
        "advanced_limits": {
            "max_trajectories": 1,
            "num_seqs": 1,
            "max_mpnn_sequences": 1,
        },
        "cpu_count": DEMONSTRATION_CPUS,
        "memory_mib": DEMONSTRATION_MEMORY_MIB,
        "gpu_count": 1,
        "timeout_seconds": DEMONSTRATION_DEADLINE_SECONDS,
        "sif_path": SIF_PATH,
        "sif_sha256": SIF_SHA256,
        "library_path_prepend": "/opt/conda/envs/BindCraft/lib:/opt/conda/lib",
        "roles": [{"name": name, "label": label} for name, label in _ROLES],
    }


def explicit_gpu_index(value: object) -> int | None:
    """The host GPU index is a canonical decimal string, not a count or a default.

    ``"0"`` is index 0. A blank, ``not_assigned``, a padded spelling, or a
    value outside 0–7 is not an assignment. This does not choose a GPU.
    """

    if isinstance(value, bool) or not isinstance(value, str) or not value.isdecimal():
        return None
    index = int(value)
    if value != str(index) or not 0 <= index <= 7:
        return None
    return index


def plan_matches_demonstration(fields: dict[str, Any], request_text: str) -> list[str]:
    """Names of scientific demonstration terms the approved plan does not carry.

    The host GPU index is not one of those terms. It is checked separately
    and is never substituted.
    """

    expected = {
        "filter_policy": FILTER_POLICY,
        "binder_length": DEMONSTRATION_LENGTH,
        "cpu_count": DEMONSTRATION_CPUS,
        "memory_mib": DEMONSTRATION_MEMORY_MIB,
        "execution_deadline_seconds": DEMONSTRATION_DEADLINE_SECONDS,
    }
    missing = [name for name, value in expected.items() if fields.get(name) != value]
    if not _bootstrap_seed(fields.get("seed")):
        missing.append("seed")
    if SMOKE_TOKEN not in request_text or fields.get("filter_policy_explicit") is not True:
        missing.append(SMOKE_TOKEN)
    return missing


def _bootstrap_seed(value: object) -> int | None:
    """NumPy bootstrap seed. This is not BindCraft's drawn trajectory seed."""

    if type(value) is not int or isinstance(value, bool):
        return None
    if not BOOTSTRAP_SEED_MINIMUM <= value <= BOOTSTRAP_SEED_MAXIMUM:
        return None
    return value


def reviewed_runtime_binding(
    fields: Mapping[str, Any], advanced_profile_path: Path
) -> dict[str, Any] | None:
    """Approved bootstrap seed and budget. The registered profile is not rewritten."""

    seed = _bootstrap_seed(fields.get("seed"))
    if seed is None:
        return None
    try:
        document = json.loads(advanced_profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    limits = bounded_sequence_limits(
        {
            "max_trajectories": document.get("max_trajectories"),
            "num_seqs": document.get("num_seqs"),
            "max_mpnn_sequences": document.get("max_mpnn_sequences"),
        }
    )
    if limits is None:
        return None
    stated = fields.get("sequence_limits")
    if stated is None:
        if limits["max_trajectories"] != 1:
            return None
    elif stated != limits:
        return None
    return {"seed": seed, "sequence_limits": limits}


def _safe_attempt_directory(attempt_id: str, attempt_directory: Path) -> str | None:
    if attempt_id in RESERVED_ATTEMPTS or not attempt_id.startswith("attempt."):
        return "attempt_id_rejected"
    if len(attempt_id) != len("attempt.") + 8:
        return "attempt_id_rejected"
    if not attempt_directory.is_absolute() or attempt_directory.exists():
        return "attempt_directory_rejected"
    if attempt_directory.name != attempt_id or attempt_directory.parent.is_symlink():
        return "attempt_directory_rejected"
    if not attempt_directory.parent.is_dir():
        return "attempt_directory_rejected"
    resolved = str(attempt_directory.resolve())
    for root in _FORBIDDEN_ROOTS:
        if resolved == root or resolved.startswith(root + "/"):
            return "attempt_directory_rejected"
    return None


def _visible_gpu(argv: tuple[str, ...], gpu_index: int) -> tuple[str, ...]:
    parts = list(argv)
    try:
        index = parts.index("/usr/bin/env")
    except ValueError:
        return argv
    parts.insert(index + 1, f"CUDA_VISIBLE_DEVICES={gpu_index}")
    return tuple(parts)


def historical_resource_application(
    *,
    cpu_count: int | None,
    memory_mib: int | None,
    gpu_index: str | None,
) -> dict[str, Any]:
    """Classify the saved CPU, memory, and GPU values by what Apptainer does.

    ``--cpus`` and ``--memory`` become cgroup ``cpu.max`` and ``memory.max``.
    That is an imposed limit. ``CUDA_VISIBLE_DEVICES`` is read by the CUDA
    library and leaves the Nvidia device nodes in place, so it is not a
    device limit. A missing value is absent, not a recorded default.
    """

    resources: dict[str, Any] = {}
    flags: list[str] = []
    if type(cpu_count) is int and not isinstance(cpu_count, bool) and cpu_count > 0:
        flags.extend(["--cpus", str(cpu_count)])
        resources["cpu_count"] = {
            "kind": "imposed_limit",
            "mechanism": "apptainer_cgroup_cpu.max",
            "value": cpu_count,
        }
    else:
        resources["cpu_count"] = {"kind": "absent", "blocker": "cpu_count_not_saved"}
    if type(memory_mib) is int and not isinstance(memory_mib, bool) and memory_mib > 0:
        flags.extend(["--memory", f"{memory_mib}M"])
        resources["memory_mib"] = {
            "kind": "imposed_limit",
            "mechanism": "apptainer_cgroup_memory.max",
            "value": memory_mib,
        }
    else:
        resources["memory_mib"] = {"kind": "absent", "blocker": "host_memory_not_saved"}
    if type(gpu_index) is str and gpu_index.isdigit():
        resources["gpu"] = {
            "kind": "library_configuration",
            "mechanism": "CUDA_VISIBLE_DEVICES",
            "value": gpu_index,
            "device_nodes_remain_visible": True,
            "isolation_required": False,
            "vram_reservation_required": False,
        }
    else:
        resources["gpu"] = {"kind": "absent", "blocker": "gpu_not_selected"}
    resources["flags"] = tuple(flags)
    return resources


def _descendant_pids(root: int) -> tuple[int, ...]:
    """Children of ``root``, including a descendant that called setsid or setpgid."""

    children: dict[int, list[int]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        end = raw.rfind(b")")
        if end < 0 or end + 2 >= len(raw):
            continue
        fields = raw[end + 2 :].split()
        if len(fields) < 2:
            continue
        try:
            parent = int(fields[1])
        except ValueError:
            continue
        children.setdefault(parent, []).append(pid)
    found: list[int] = []
    seen: set[int] = set()
    stack = list(children.get(root, ()))
    while stack:
        current = stack.pop()
        if current == root or current in seen:
            continue
        seen.add(current)
        found.append(current)
        stack.extend(children.get(current, ()))
    return tuple(found)


def _signal_tree(pid: int, sig: int) -> None:
    """Signal every descendant before the process group.

    A new session is no longer reached by ``killpg``. The process list is
    collected first so those descendants still receive the signal.
    """

    descendants = _descendant_pids(pid)
    for child in descendants:
        try:
            os.kill(child, sig)
        except OSError:
            continue
    try:
        os.killpg(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            return


def _arm_parent_watch(watch_fd: int) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        _signal_tree(os.getpid(), signal.SIGKILL)
        os._exit(1)

    signal.signal(signal.SIGTERM, _stop)

    def _watch() -> None:
        while True:
            try:
                data = os.read(watch_fd, 1)
            except OSError:
                _stop(signal.SIGTERM, None)
                return
            if data == b"":
                _stop(signal.SIGTERM, None)
                return

    threading.Thread(target=_watch, name="cellertz-parent-watch", daemon=True).start()


def supervise(watch_fd: int, argv: list[str]) -> int:
    """Run argv under this supervisor. Parent loss kills every descendant."""

    if not argv:
        return 2
    _arm_parent_watch(watch_fd)
    child = subprocess.Popen(argv)
    code = child.wait()
    os._exit(code if isinstance(code, int) and code >= 0 else 1)


def _kill_group(process: subprocess.Popen[str], *, force: bool) -> None:
    if process.poll() is not None or process.pid is None:
        return
    _signal_tree(process.pid, signal.SIGKILL if force else signal.SIGTERM)


class GroupSupervisedRuntime:
    """Wall clock and parent watch. Both end every descendant process."""

    name = RUNTIME_NAME
    version = RUNTIME_VERSION

    def execute(
        self,
        request: ExecutionRequest,
        *,
        alive: Callable[[], bool] | None = None,
        on_start: Callable[[int], None] | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> ExecutionResult:
        started = _now()
        if not request.argv or request.timeout_seconds is None or request.timeout_seconds <= 0:
            return _failed(started, "invalid_execution_request")
        caller_alive = alive or (lambda: True)
        read_fd, write_fd = os.pipe()
        command = [
            sys.executable,
            "-m",
            "cellertz.application.apptainer_demonstration",
            "--supervise",
            str(read_fd),
            *request.argv,
        ]
        stdout_handle = None
        stderr_handle = None
        popen_stdout: object = subprocess.PIPE
        popen_stderr: object = subprocess.PIPE
        if stdout_path is not None and stderr_path is not None:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = stdout_path.open("w", encoding="utf-8", buffering=1)
            stderr_handle = stderr_path.open("w", encoding="utf-8", buffering=1)
            popen_stdout = stdout_handle
            popen_stderr = stderr_handle
        try:
            process = subprocess.Popen(
                command,
                stdout=popen_stdout,
                stderr=popen_stderr,
                text=True,
                start_new_session=True,
                pass_fds=(read_fd,),
                close_fds=True,
            )
        except OSError as error:
            os.close(read_fd)
            os.close(write_fd)
            message = f"Process failed to start: {error}"
            if stderr_handle is not None:
                stderr_handle.write(message + "\n")
            return _failed(started, message)
        finally:
            if stdout_handle is not None:
                stdout_handle.close()
            if stderr_handle is not None:
                stderr_handle.close()
        os.close(read_fd)
        if on_start is not None and process.pid is not None:
            on_start(process.pid)
        stop = threading.Event()
        guard = threading.Lock()

        def _close_write() -> None:
            with guard:
                try:
                    os.close(write_fd)
                except OSError:
                    return

        def _watch_caller() -> None:
            while not stop.is_set() and process.poll() is None:
                if not caller_alive():
                    _close_write()
                    return
                time.sleep(0.05)

        def _captured() -> tuple[str, str]:
            if stdout_path is None or stderr_path is None:
                return "", ""
            return _read_log_tail(stdout_path), _read_log_tail(stderr_path)

        threading.Thread(target=_watch_caller, name="cellertz-caller-watch", daemon=True).start()
        try:
            if stdout_path is None:
                stdout, stderr = process.communicate(timeout=request.timeout_seconds)
            else:
                process.wait(timeout=request.timeout_seconds)
                stdout, stderr = _captured()
        except subprocess.TimeoutExpired:
            _kill_group(process, force=False)
            try:
                if stdout_path is None:
                    stdout, stderr = process.communicate(timeout=2)
                else:
                    process.wait(timeout=2)
                    stdout, stderr = _captured()
            except subprocess.TimeoutExpired:
                _kill_group(process, force=True)
                if stdout_path is None:
                    stdout, stderr = process.communicate()
                else:
                    process.wait()
                    stdout, stderr = _captured()
            return _failed(started, "Process timed out.", stdout or "", stderr or "")
        finally:
            stop.set()
            _close_write()
        if process.returncode != 0:
            return _failed(
                started,
                f"Process failed with return code {process.returncode}.",
                stdout or "",
                stderr or "",
                process.returncode,
            )
        return ExecutionResult(
            status="complete",
            return_code=0,
            stdout=stdout or "",
            stderr=stderr or "",
            runtime_name=self.name,
            runtime_version=self.version,
            started_at=started,
            completed_at=_now(),
        )


def _read_log_tail(path: Path, limit: int = 65536) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > limit:
        data = data[-limit:]
    return data.decode("utf-8", errors="replace")


def _executor_diagnosis(result: ExecutionResult) -> str:
    for source in (result.stderr, result.stdout):
        for line in source.splitlines():
            text = line.strip()
            if text:
                return text[:400]
    return result.error or "Process failed."


def _executor_log(result: ExecutionResult) -> str:
    parts = [part.rstrip() for part in (result.stdout, result.stderr) if part and part.strip()]
    return "\n".join(parts)


def _failed(
    started: str,
    error: str,
    stdout: str = "",
    stderr: str = "",
    return_code: int | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        status="failed",
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        runtime_name=RUNTIME_NAME,
        runtime_version=RUNTIME_VERSION,
        started_at=started,
        completed_at=_now(),
        error=error,
    )


def _progress(directory: Path, status: str, **extra: Any) -> None:
    payload = {"status": status, "at": _now(), "profile_id": PROFILE_ID, **extra}
    path = directory / "progress.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def reap_abandoned(root: Path) -> list[str]:
    """Kill demonstration groups whose worker process is gone. Live workers are left alone."""

    killed: list[str] = []
    if not root.is_dir():
        return killed
    for progress_path in sorted(root.glob("attempt.*/progress.json")):
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            pid = int((progress_path.parent / "supervisor.pid").read_text(encoding="utf-8"))
            worker = int(progress["worker_pid"])
        except (OSError, ValueError, TypeError, KeyError):
            continue
        if progress.get("status") != "started":
            continue
        if worker <= 1:
            continue
        try:
            os.kill(worker, 0)
        except OSError:
            pass
        else:
            continue
        try:
            _signal_tree(pid, signal.SIGKILL)
            killed.append(progress_path.parent.name)
        except OSError:
            continue
    return killed


@dataclass(frozen=True)
class DemonstrationLaunch:
    fields: dict[str, Any]
    request_text: str
    approval_source: str | None
    approved: bool
    input_selection: object
    live_input_selection: object
    target_path: Path
    settings_path: Path
    advanced_profile_path: Path
    attempt_id: str
    attempt_directory: Path
    gpu_index: int
    apptainer_bin: Path
    sif_path: Path
    expected_sif_sha256: str
    n_designs: int | None = None


def explicit_run_delivery(request: DemonstrationLaunch) -> dict[str, Any] | None:
    """Values the displayed revision can deliver. This does not start a process."""

    from cellertz.application.bindcraft_input_preparation import _hotspot
    from cellertz.application.pdl1_container_entrypoint import (
        DESIGN_COUNT_MAX,
        DESIGN_COUNT_MIN,
        LENGTH_MAX,
        LENGTH_MIN,
    )

    fields = request.fields
    if fields.get("filter_policy") != FILTER_POLICY or fields.get("filter_policy_explicit") is not True:
        return None
    length = fields.get("binder_length")
    if type(length) is not int or isinstance(length, bool) or not LENGTH_MIN <= length <= LENGTH_MAX:
        return None
    count = request.n_designs
    if type(count) is not int or isinstance(count, bool) or not DESIGN_COUNT_MIN <= count <= DESIGN_COUNT_MAX:
        return None
    deadline = fields.get("execution_deadline_seconds")
    if type(deadline) is not int or isinstance(deadline, bool) or deadline < 1:
        return None
    seed = _bootstrap_seed(fields.get("seed"))
    if seed is None:
        return None
    hotspot, pending, blocker = _hotspot(fields)
    if pending or blocker is not None or not isinstance(hotspot, str):
        return None
    cpu_count = fields.get("cpu_count")
    memory_mib = fields.get("memory_mib")
    gpu = fields.get("gpu")
    resources = historical_resource_application(
        cpu_count=cpu_count if type(cpu_count) is int else None,
        memory_mib=memory_mib if type(memory_mib) is int else None,
        gpu_index=gpu if type(gpu) is str else None,
    )
    if (
        resources["cpu_count"]["kind"] != "imposed_limit"
        or resources["memory_mib"]["kind"] != "imposed_limit"
        or resources["gpu"]["kind"] != "library_configuration"
        or explicit_gpu_index(gpu) != request.gpu_index
    ):
        return None
    try:
        document = json.loads(request.advanced_profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    limits = bounded_sequence_limits(
        {
            "max_trajectories": document.get("max_trajectories"),
            "num_seqs": document.get("num_seqs"),
            "max_mpnn_sequences": document.get("max_mpnn_sequences"),
        }
    )
    if limits is None:
        return None
    return {
        "seed": seed,
        "length": length,
        "n_designs": count,
        "deadline": deadline,
        "hotspot": hotspot,
        "flags": tuple(resources["flags"]),
    }


def _write_displayed_hotspot(settings_path: Path, hotspot: str) -> None:
    document = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("displayed_settings_rejected")
    document["target_hotspot_residues"] = hotspot
    settings_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _insert_exec_flags(argv: tuple[str, ...], flags: tuple[str, ...]) -> tuple[str, ...]:
    parts = list(argv)
    try:
        index = parts.index("--nv")
    except ValueError:
        return argv
    return tuple(parts[: index + 1] + list(flags) + parts[index + 1 :])


class ApptainerDemonstrationLauncher:
    """Explicit launch. Construction does not start Apptainer."""

    def __init__(
        self,
        *,
        inert_outputs: bool = False,
        gpu_available: bool | None = None,
        gpu_readiness: Callable[[int], Mapping[str, Any]] | None = None,
        runtime: Any | None = None,
    ) -> None:
        self.inert_outputs = inert_outputs
        self.gpu_available = gpu_available
        self.gpu_readiness = gpu_readiness
        self.runtime = runtime

    def launch(self, request: DemonstrationLaunch) -> dict[str, Any]:
        if request.approved is not True or request.approval_source not in {"human_review", "explicit_run"}:
            return {"status": "approval_required", "launched": False, "profile_id": PROFILE_ID}
        mismatched = (
            plan_matches_demonstration(request.fields, request.request_text)
            if request.approval_source == "human_review"
            else []
        )
        if mismatched or request.input_selection != request.live_input_selection:
            return {
                "status": "launch_rejected",
                "launched": False,
                "profile_id": PROFILE_ID,
                "mismatched": mismatched or ["input_identity"],
            }
        directory_error = _safe_attempt_directory(request.attempt_id, request.attempt_directory)
        delivered = explicit_run_delivery(request) if request.approval_source == "explicit_run" else None
        if request.approval_source == "explicit_run":
            binding = None if delivered is None else {"seed": delivered["seed"]}
            length = None if delivered is None else delivered["length"]
            count = None if delivered is None else delivered["n_designs"]
            deadline = None if delivered is None else delivered["deadline"]
        else:
            binding = reviewed_runtime_binding(request.fields, request.advanced_profile_path)
            length = DEMONSTRATION_LENGTH
            count = 1
            deadline = DEMONSTRATION_DEADLINE_SECONDS
        if directory_error is not None or binding is None:
            refused = {"status": "launch_rejected", "launched": False, "profile_id": PROFILE_ID}
            if request.approval_source == "explicit_run":
                refused["diagnosis"] = "displayed_configuration_rejected"
                refused["automatic_retry"] = False
            return refused
        assigned = explicit_gpu_index(request.fields.get("gpu"))
        if (
            type(request.gpu_index) is not int
            or isinstance(request.gpu_index, bool)
            or assigned is None
            or assigned != request.gpu_index
        ):
            return {
                "status": "gpu_index_mismatch",
                "launched": False,
                "profile_id": PROFILE_ID,
                "gpu_index": request.gpu_index if type(request.gpu_index) is int else None,
            }
        if not request.sif_path.is_file() or _sha256(request.sif_path) != request.expected_sif_sha256:
            return {"status": "sif_hash_mismatch", "launched": False, "profile_id": PROFILE_ID}
        if not request.apptainer_bin.is_file():
            return {"status": "apptainer_unavailable", "launched": False, "profile_id": PROFILE_ID}
        report = None
        if not self.inert_outputs:
            readiness = self.gpu_readiness or gpu_launch_readiness
            report = readiness(request.gpu_index)
            blocker = gpu_launch_blocker(report if isinstance(report, Mapping) else None)
            if blocker is not None:
                return {
                    "status": blocker,
                    "launched": False,
                    "profile_id": PROFILE_ID,
                    "gpu_index": request.gpu_index,
                }
        request.attempt_directory.mkdir()
        _progress(
            request.attempt_directory,
            "planned",
            gpu_observation=_gpu_observation(report if isinstance(report, Mapping) else None),
        )
        generation = GenerationRequest(
            target_id="PDL1",
            objective="technical-smoke-test",
            generator="bindcraft",
            n_candidates=count,
            constraints={
                "length_min": length,
                "length_max": length,
                "seed": binding["seed"],
            },
        )
        execution = BindCraftExecutionRequest(
            generation=generation,
            run_id=request.attempt_id,
            execution_id=request.attempt_id,
            target_path=request.target_path,
            output_root=request.attempt_directory,
            apptainer_bin=request.apptainer_bin,
            settings_path=request.settings_path,
            container_image=str(request.sif_path),
            container_digest=request.expected_sif_sha256,
            tool_version="bindcraft",
            seed=binding["seed"],
            gpu_required=not self.inert_outputs,
        )
        prepared = BindCraftRealExecutionBridge().plan(
            execution,
            developer_bindcraft_authorization(),
            gpu_available=True if self.inert_outputs else self.gpu_available,
        )
        authorized = prepared.authorized_request
        if authorized is None or prepared.preflight is None or prepared.preflight.status != "ready":
            _progress(request.attempt_directory, "not_ready")
            return {"status": "not_ready", "launched": False, "profile_id": PROFILE_ID}
        plan = build_apptainer_plan(
            authorized,
            timeout_seconds=deadline,
            advanced_profile_path=request.advanced_profile_path,
        )
        argv = _visible_gpu(plan.argv, request.gpu_index)
        if delivered is not None:
            _write_displayed_hotspot(plan.runtime_settings_path, delivered["hotspot"])
            argv = _insert_exec_flags(argv, delivered["flags"])
        if self.inert_outputs:
            _write_inert_candidate(plan.bindcraft_output_dir)
            result = ExecutionResult(
                "complete",
                0,
                "",
                "",
                "inert-demonstration-runner",
                "inert_v1",
                _now(),
                _now(),
            )
        else:
            def _record_supervisor(pid: int) -> None:
                (request.attempt_directory / "supervisor.pid").write_text(f"{pid}\n", encoding="utf-8")
                _progress(
                    request.attempt_directory,
                    "started",
                    worker_pid=os.getpid(),
                    supervisor_pid=pid,
                    output_dir=str(plan.bindcraft_output_dir),
                )

            runtime = self.runtime or GroupSupervisedRuntime()
            result = runtime.execute(
                ExecutionRequest(argv=argv, timeout_seconds=deadline),
                on_start=_record_supervisor,
                stdout_path=request.attempt_directory / "executor.stdout",
                stderr_path=request.attempt_directory / "executor.stderr",
            )
        recorded = {"executor_log": _executor_log(result)} if _executor_log(result) else {}
        if result.status != "complete":
            diagnosis = _executor_diagnosis(result)
            vram = vram_failure_report(
                getattr(result, "stdout", "") or "",
                getattr(result, "stderr", "") or "",
            )
            if vram is not None:
                _progress(request.attempt_directory, "failed", error="vram_insufficient")
                return {**vram, "profile_id": PROFILE_ID, "gpu_index": request.gpu_index, "diagnosis": diagnosis, **recorded}
            _progress(request.attempt_directory, "failed", error=diagnosis)
            return {
                "status": "launch_failed",
                "launched": True,
                "profile_id": PROFILE_ID,
                "diagnosis": diagnosis,
                **recorded,
            }
        ingested = ingest_bindcraft_output(
            plan.bindcraft_output_dir,
            attempt_id=request.attempt_id,
            execution=result.runtime_name,
        )
        if ingested["status"] != "verified":
            _progress(request.attempt_directory, "rejected", reasons=ingested["reasons"])
            outcome = terminal_outcome_from_designs(plan.bindcraft_output_dir)
            if outcome is not None and outcome.get("outcome") == "budget_exhausted":
                return {
                    "status": "budget_exhausted",
                "launched": True,
                "profile_id": PROFILE_ID,
                "accepted_candidate": False,
                "successful_candidate_generation": False,
                "termination_reason": "attempted_trajectory_budget_exhausted",
                "budget_compliance_established": True,
                "scientific_validation_established": False,
                "reasons": ingested["reasons"],
                "failure_counters": outcome["failure_counters"],
                "designs_directory": outcome["designs_directory"],
                "raw_progress": _read_json_object(request.attempt_directory / "progress.json"),
                **recorded,
            }
            return {
                "status": "outputs_rejected",
                "launched": True,
                "profile_id": PROFILE_ID,
                "reasons": ingested["reasons"],
                "diagnostics": ingested["diagnostics"],
                **recorded,
            }
        published = publish_ingested_review(request.attempt_directory, ingested)
        if published["status"] != "verified":
            _progress(request.attempt_directory, "rejected", reasons=published["reasons"])
            return {
                "status": "outputs_rejected",
                "launched": True,
                "profile_id": PROFILE_ID,
                "reasons": published["reasons"],
                "diagnostics": published["diagnostics"],
                **recorded,
            }
        _progress(request.attempt_directory, "verified")
        return {
            "status": "verified",
            "launched": True,
            "profile_id": PROFILE_ID,
            "attempt_id": request.attempt_id,
            "scientific_validation": False,
            "review_json": published["review_json"],
            "catalog_parent": str(request.attempt_directory.parent),
            **recorded,
        }


_CANONICAL_PDB = re.compile(r"^MPNN/Relaxed/(?P<design>.+)_model(?P<model>[1-9][0-9]*)\.pdb$")
_ACCEPTED_PDB = re.compile(r"^Accepted/(?P<design>.+)_model(?P<model>[1-9][0-9]*)\.pdb$")
_RANKED_PDB = re.compile(
    r"^Accepted/Ranked/(?P<rank>[0-9]+)_(?P<design>.+)_model(?P<model>[1-9][0-9]*)\.pdb$"
)
_MODEL_COLUMN = re.compile(r"^([1-9])_(.+)$")
_AVERAGE_COLUMN = re.compile(r"^Average_(.+)$")
_AA3 = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
}


def _write_inert_candidate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [_ca(1, "A", 1, 0.0, "GLY")]
    for index in range(1, 7):
        lines.append(_ca(index + 1, "B", index, 3.0 if index == 1 else 40.0, "ALA"))
    lines.append("END")
    destination = output_dir / "MPNN" / "Relaxed" / "inert_candidate_model1.pdb"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = "Design,Sequence,InterfaceResidues,1_pLDDT,Average_pLDDT\n" "inert_candidate,AAAAAA,,0.50,0.50\n"
    (output_dir / STATS_FILENAME).write_text(rows, encoding="utf-8")


def _ca(serial: int, chain: str, resseq: int, x: float, resname: str) -> str:
    return (
        f"ATOM  {serial:5d}  CA  {resname:<3} {chain}{resseq:4d}    "
        f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
    )


def _rejected(reasons: list[str], diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {"status": "outputs_rejected", "reasons": reasons, "diagnostics": diagnostics}


def _chain_sequence(path: Path, chain: str) -> str:
    residues: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("ATOM") or len(line) < 26 or line[12:16].strip() != "CA":
            continue
        if line[21] != chain:
            continue
        residues[int(line[22:26])] = _AA3.get(line[17:20].strip(), "X")
    return "".join(residues[index] for index in sorted(residues))


def _metric_cell(present: bool, text: str | None) -> dict[str, str]:
    if not present:
        return {"kind": "absent"}
    raw = "" if text is None else text.strip()
    if raw == "":
        return {"kind": "empty_field"}
    try:
        number = float(raw)
    except ValueError:
        number = None
    if number == 0.0:
        return {"kind": "recorded_zero", "text": raw}
    return {"kind": "recorded", "text": raw}


def _read_final_stats(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return [], [f"final_design_stats_unreadable:{error}"]
    reader = csv.DictReader(text.splitlines())
    names = [name.strip() for name in reader.fieldnames or [] if name and name.strip()]
    if "Design" not in names or "Sequence" not in names:
        return [], ["final_design_stats_missing_design_or_sequence"]
    rows = []
    for row in reader:
        rows.append({(key or "").strip(): value or "" for key, value in row.items() if key and key.strip()})
    if not rows:
        return [], ["final_design_row_missing"]
    return rows, []


def _relative(output_dir: Path, path: Path) -> str:
    return path.relative_to(output_dir).as_posix()


def ingest_bindcraft_output(output_dir: Path, *, attempt_id: str, execution: str) -> dict[str, Any]:
    """Read one final BindCraft design. Rejection names the missing or conflicting artifact."""

    csv_path = output_dir / STATS_FILENAME
    if not csv_path.is_file():
        return _rejected(["final_design_stats_missing"], {"csv": str(csv_path)})
    rows, reasons = _read_final_stats(csv_path)
    if reasons:
        return _rejected(reasons, {"csv": str(csv_path)})
    if len(rows) != 1:
        return _rejected(
            [f"final_design_count:{len(rows)}"],
            {"designs": [(row.get("Design") or "").strip() for row in rows]},
        )
    row = rows[0]
    design = row.get("Design", "").strip()
    sequence = row.get("Sequence", "").strip()
    if not design:
        reasons.append("design_missing")
    if not sequence:
        reasons.append("sequence_missing")
    classified, classify_reasons = _classify_pdbs(output_dir, design)
    reasons.extend(classify_reasons)
    metrics, metric_reasons, blank_model2 = _metric_rows(row, set(classified["canonical"]))
    reasons.extend(metric_reasons)
    report = _trajectory_report(output_dir, classified["trajectory"])
    diagnostics = {
        "design": design,
        "canonical_pdbs": {f"model{number}": _relative(output_dir, path) for number, path in classified["canonical"].items()},
        "accepted_copies": classified["accepted_paths"],
        "ranked_copies": classified["ranked_paths"],
        "excluded_trajectories": classified["trajectory"],
        "execution_constraints": report,
    }
    if reasons:
        return _rejected(reasons, diagnostics)
    models, sources, representative, model_reasons = _assemble_models(
        output_dir, classified, sequence
    )
    if model_reasons:
        return _rejected(model_reasons, diagnostics)
    interface = _interface_residues(row)
    absent = [f"model{number}" for number in (1, 2) if number not in classified["canonical"]]
    review = {
        "attempt_id": attempt_id,
        "design": design,
        "execution": execution,
        "profile_id": PROFILE_ID,
        "filter_policy": (row.get("Filters") or "").strip() or FILTER_POLICY,
        "filter_choice": SMOKE_TOKEN,
        "scientific_evaluation": "pending",
        "platform_scientific_execution": "demonstration",
        "platform_ipsae_ranking": False,
        "experimental_validation": False,
        "ranking_score_computed": False,
        "representative": representative,
        "chains": {"target": "A", "binder": "B"},
        "models": models,
        "metrics": metrics,
        "interface_residues": interface,
        "blank_model2_metrics": blank_model2,
        "blank_model2_note": (
            "Empty model2 cells are empty fields. A recorded zero stays a recorded zero. "
            "A missing model2 column or structure is absent."
            if 2 in classified["canonical"]
            else "model2 was not among the final per-model structures."
        ),
        "absent_models": absent,
        "provenance": {
            "stats_csv_sha256": _sha256(csv_path),
            "sif_sha256": SIF_SHA256,
            "sif_sha256_checked_during_ingestion": False,
            "recorded_seed": row.get("Seed", "").strip(),
            "recorded_rank": row.get("Rank", "").strip(),
            "recorded_filters": row.get("Filters", "").strip(),
            "omitted": ["ipSAE", "ranking_score"],
            "excluded_trajectories": classified["trajectory"],
            "execution_constraints": report,
        },
    }
    return {
        "status": "verified",
        "reasons": [],
        "diagnostics": diagnostics,
        "review": review,
        "sources": sources,
    }


def _classify_pdbs(output_dir: Path, design: str) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    canonical: dict[int, Path] = {}
    accepted: dict[int, Path] = {}
    ranked: dict[int, tuple[str, Path]] = {}
    trajectories: list[str] = []
    accepted_paths: list[str] = []
    ranked_paths: list[str] = []
    for path in sorted(output_dir.rglob("*.pdb")):
        if not path.is_file():
            continue
        relative = _relative(output_dir, path)
        if "Trajectory" in relative.split("/"):
            trajectories.append(relative)
            continue
        matched = _CANONICAL_PDB.fullmatch(relative) or _ACCEPTED_PDB.fullmatch(relative)
        ranked_match = _RANKED_PDB.fullmatch(relative)
        if ranked_match is not None:
            matched = ranked_match
        if matched is None:
            reasons.append(f"unclassified_pdb:{relative}")
            continue
        if matched.group("design") != design:
            reasons.append(f"pdb_for_other_design:{relative}")
            continue
        number = int(matched.group("model"))
        if ranked_match is not None:
            if number in ranked:
                reasons.append(f"duplicate_ranked_copy:model{number}")
            ranked[number] = (ranked_match.group("rank"), path)
            ranked_paths.append(relative)
        elif relative.startswith("MPNN/Relaxed/"):
            if number in canonical:
                reasons.append(f"duplicate_model_pdb:model{number}")
            canonical[number] = path
        else:
            if number in accepted:
                reasons.append(f"duplicate_accepted_copy:model{number}")
            accepted[number] = path
            accepted_paths.append(relative)
    if design and not canonical:
        reasons.append(f"canonical_model_pdb_missing:{design}")
    return {
        "canonical": canonical,
        "accepted": accepted,
        "ranked": ranked,
        "trajectory": trajectories,
        "accepted_paths": accepted_paths,
        "ranked_paths": ranked_paths,
    }, reasons


def _metric_rows(
    row: dict[str, str], published: set[int]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    reasons: list[str] = []
    order: list[str] = []
    slots: dict[str, dict[int | str, str]] = {}
    for key, value in row.items():
        if "ipsae" in key.lower() or "ranking_score" in key.lower():
            reasons.append(f"forbidden_metric:{key}")
            continue
        model_match = _MODEL_COLUMN.fullmatch(key)
        average_match = _AVERAGE_COLUMN.fullmatch(key)
        if model_match is not None:
            number: int | str = int(model_match.group(1))
            name = model_match.group(2)
        elif average_match is not None:
            number = "average"
            name = average_match.group(1)
        else:
            continue
        if name not in slots:
            order.append(name)
            slots[name] = {}
        slots[name][number] = value
        if isinstance(number, int) and number not in published and value.strip():
            reasons.append(f"unpublished_model_metric:{key}")
    blank: list[str] = []
    metrics: list[dict[str, Any]] = []
    labels = [1, 2, *sorted(number for number in published if number > 2)]
    for name in order:
        item: dict[str, Any] = {"metric": name}
        for number in labels:
            present = number in published and number in slots[name]
            item[f"model{number}"] = _metric_cell(present, slots[name].get(number))
        if item["model2"]["kind"] == "empty_field":
            blank.append(name)
        item["average"] = _metric_cell("average" in slots[name], slots[name].get("average"))
        metrics.append(item)
    return metrics, reasons, blank


def _interface_residues(row: dict[str, str]) -> dict[str, str]:
    if "InterfaceResidues" not in row:
        return {"kind": "absent"}
    text = row["InterfaceResidues"].strip()
    if text == "":
        return {"kind": "empty_field"}
    return {"kind": "recorded", "text": text}


def _assemble_models(
    output_dir: Path,
    classified: dict[str, Any],
    sequence: str,
) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any], list[str]]:
    reasons: list[str] = []
    canonical: dict[int, Path] = classified["canonical"]
    accepted: dict[int, Path] = classified["accepted"]
    ranked: dict[int, tuple[str, Path]] = classified["ranked"]
    identified = set(accepted) | set(ranked)
    if len(identified) > 1:
        reasons.append("ambiguous_representative:" + ",".join(f"model{number}" for number in sorted(identified)))
    elif not identified and len(canonical) != 1:
        reasons.append("representative_not_identified")
    representative_number = next(iter(identified)) if len(identified) == 1 else next(iter(canonical), None)
    models: dict[str, Any] = {}
    sources: dict[str, Path] = {}
    for number, path in sorted(canonical.items()):
        model_id = f"model{number}"
        try:
            actual = _chain_sequence(path, "B")
        except ValueError:
            reasons.append(f"malformed_pdb:{model_id}")
            continue
        if actual != sequence:
            reasons.append(f"binder_sequence_mismatch:{model_id}")
            continue
        digest = _sha256(path)
        copy_notes: dict[str, str] = {}
        if number in accepted and _sha256(accepted[number]) != digest:
            reasons.append(f"accepted_copy_hash_mismatch:{model_id}")
        elif number in accepted:
            copy_notes["accepted_pdb"] = _relative(output_dir, accepted[number])
            copy_notes["accepted_sha256"] = digest
        if number in ranked and _sha256(ranked[number][1]) != digest:
            reasons.append(f"ranked_copy_hash_mismatch:{model_id}")
        elif number in ranked:
            copy_notes["ranked_pdb"] = _relative(output_dir, ranked[number][1])
            copy_notes["ranked_prefix"] = ranked[number][0]
            copy_notes["ranked_sha256"] = digest
        models[model_id] = {
            "id": model_id,
            "pdb_url": f"{model_id}.pdb",
            "sha256": digest,
            "representative": number == representative_number,
            "sequence": sequence,
            "binder_sequence_matches_csv": True,
            "source_pdb": _relative(output_dir, path),
            **copy_notes,
            "contacts": {
                "binder_residues_in_contact": None,
                "target_residues_in_contact": None,
                "residue_pairs": None,
                "minimum_heavy_atom_distance_angstrom": None,
            },
        }
        sources[model_id] = path
    if identified and representative_number not in canonical:
        reasons.append("representative_without_canonical_pdb")
    representative = {
        "model": None if representative_number is None else f"model{representative_number}",
        "rule": (
            "Accepted and Ranked copies match this model's SHA-256"
            if identified
            else "only canonical model; Accepted and Ranked copies were not written"
        ),
        "scientific_validation": False,
        "accepted_pdb": None
        if representative_number not in accepted
        else _relative(output_dir, accepted[representative_number]),
        "ranked_pdb": None
        if representative_number not in ranked
        else _relative(output_dir, ranked[representative_number][1]),
    }
    return models, sources, representative, reasons


def _trajectory_report(output_dir: Path, trajectories: list[str]) -> dict[str, Any]:
    advanced_path = output_dir.parent / ".cellertz_runtime" / "advanced.json"
    requested = None
    if advanced_path.is_file():
        try:
            document = json.loads(advanced_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            requested = "unreadable"
        else:
            requested = document.get("max_trajectories") if isinstance(document, dict) else "unreadable"
    relaxed = [path for path in trajectories if "/LowConfidence/" not in f"/{path}"]
    low = [path for path in trajectories if "/LowConfidence/" in f"/{path}"]
    counts: dict[str, str] = {}
    failure_path = output_dir / "failure_csv.csv"
    if failure_path.is_file():
        failure_rows, _ignored = _read_named_csv(failure_path)
        if failure_rows:
            counts = {key: value.strip() for key, value in failure_rows[0].items() if value.strip() not in {"", "0"}}
    unmet = requested == 1 and (len(relaxed) != 1 or len(low) > 0)
    if requested == 1 and unmet:
        detail = (
            "advanced.json requests max_trajectories 1. "
            f"{len(relaxed)} relaxed trajectory structure(s) and {len(low)} LowConfidence "
            "trajectory structure(s) are on disk. The native BindCraft counter counts "
            "Trajectory/Relaxed PDBs and does not count a rejected attempt. "
            "These files are not evidence that an attempted-trajectory budget was enforced. "
            "This constraint is separate from final-candidate ingestion."
        )
    elif not trajectories:
        detail = "No trajectory structures were in the output."
    else:
        detail = "Trajectory files are excluded from the final candidate models."
    return {
        "requested_max_trajectories": requested,
        "accepted_trajectory_files": relaxed,
        "low_confidence_trajectory_files": low,
        "nonzero_failure_counts": counts,
        "unmet": unmet,
        "detail": detail,
    }


def _read_named_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return [], [str(error)]
    reader = csv.DictReader(text.splitlines())
    return [
        {(key or "").strip(): value or "" for key, value in row.items() if key and key.strip()}
        for row in reader
    ], []


def publish_ingested_review(attempt_directory: Path, ingested: dict[str, Any]) -> dict[str, Any]:
    """Copy canonical models beside review.json. Raw design files stay in place."""

    review = ingested.get("review")
    sources = ingested.get("sources")
    if not isinstance(review, dict) or not isinstance(sources, dict):
        return _rejected(["review_not_verified"], {})
    written: list[Path] = []
    try:
        for model_id, source in sources.items():
            model = review["models"][model_id]
            if _sha256(source) != model["sha256"] or _chain_sequence(source, "B") != model["sequence"]:
                _remove(written)
                return _rejected(["source_hash_mismatch"], {"model": model_id})
            target = attempt_directory / f"{model_id}.pdb"
            if target.exists():
                _remove(written)
                return _rejected(["published_pdb_already_present"], {"path": str(target)})
            target.write_bytes(source.read_bytes())
            written.append(target)
            if _sha256(target) != model["sha256"]:
                _remove(written)
                return _rejected(["published_hash_mismatch"], {"model": model_id})
        review_path = attempt_directory / "review.json"
        if review_path.exists():
            _remove(written)
            return _rejected(["review_already_present"], {"review": str(review_path)})
        review_path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
        written.append(review_path)
        loaded = json.loads(review_path.read_text(encoding="utf-8"))
        for model_id, model in loaded["models"].items():
            if _sha256(attempt_directory / f"{model_id}.pdb") != model["sha256"]:
                _remove(written)
                return _rejected(["published_hash_mismatch"], {"model": model_id})
    except OSError as error:
        _remove(written)
        return _rejected(["publish_failed"], {"error": str(error)})
    return {
        "status": "verified",
        "reasons": [],
        "diagnostics": {},
        "review_json": str(review_path),
        "written": written,
    }


_RECOVERED_ATTEMPT_ID = "attempt.c5c1f13e"


def publish_verified_attempt_to_catalog(
    attempt_directory: Path,
    attempt_id: str,
    catalog_root: Path,
) -> dict[str, Any]:
    """Copy one verified attempt into the Results catalog.

    Model PDBs are written first. ``review.json`` is written only after
    those files match the review hashes, so a catalog scan cannot discover
    the attempt before its structures exist. An existing different file is
    left in place. This does not launch, score, or mark scientific validation.
    """

    if attempt_id == _RECOVERED_ATTEMPT_ID:
        return _rejected(["recovered_attempt_not_published"], {"attempt_id": attempt_id})
    if attempt_directory.name != attempt_id:
        return _rejected(["attempt_directory_mismatch"], {"attempt_id": attempt_id})
    designs = demonstration_designs_directory(attempt_directory, attempt_id)
    if designs is None:
        return _rejected(["designs_not_found"], {"attempt_id": attempt_id})
    before_raw = _tree_hashes(designs)
    review_path = attempt_directory / "review.json"
    if not review_path.is_file():
        ingested = ingest_bindcraft_output(
            designs,
            attempt_id=attempt_id,
            execution="apptainer_demonstration_launch",
        )
        if ingested["status"] != "verified":
            return ingested
        if _tree_hashes(designs) != before_raw:
            return _rejected(["raw_artifacts_changed"], {})
        placed = publish_ingested_review(attempt_directory, ingested)
        if placed["status"] != "verified":
            return placed
    if _tree_hashes(designs) != before_raw:
        return _rejected(["raw_artifacts_changed"], {})
    validated = _validate_review_directory(attempt_directory, designs)
    if validated.get("status") != "verified":
        return validated
    review = validated["review"]
    representative = review.get("representative")
    constraints = review.get("provenance", {}).get("execution_constraints")
    if (
        review.get("attempt_id") != attempt_id
        or review.get("experimental_validation") is True
        or not isinstance(representative, dict)
        or representative.get("scientific_validation") is True
        or not isinstance(constraints, dict)
        or "unmet" not in constraints
    ):
        return _rejected(["scientific_validation_not_publishable"], {"attempt_id": attempt_id})
    destination = _catalog_child(catalog_root, attempt_id)
    if destination is None:
        return _rejected(["catalog_path_escapes"], {"attempt_id": attempt_id})
    outcome = _copy_catalog_attempt(destination, validated)
    if outcome["status"] != "verified":
        return outcome
    if _tree_hashes(designs) != before_raw:
        _remove(outcome.get("written") or [])
        return _rejected(["raw_artifacts_changed"], {})
    from cellertz.application.attempt_inspection import structure_url

    models = {
        model_id: {
            "sha256": model["sha256"],
            "representative": model.get("representative") is True,
            "pdb_url": structure_url(attempt_id, model_id),
        }
        for model_id, model in review["models"].items()
    }
    return {
        "status": "verified",
        "attempt_id": attempt_id,
        "candidate": review.get("design"),
        "review_json": str(destination / "review.json"),
        "models": models,
        "representative": representative,
        "execution_constraints": constraints,
        "scientific_validation_established": False,
        "raw_artifacts_preserved": True,
        "rewritten": bool(outcome.get("written")),
        "reasons": [],
    }


def _copy_catalog_attempt(destination: Path, validated: dict[str, Any]) -> dict[str, Any]:
    review = validated["review"]
    review_bytes = validated["review_bytes"]
    sources: dict[str, Path] = validated["sources"]
    dest_review = destination / "review.json"
    if dest_review.is_file():
        if not _catalog_matches(destination, review, review_bytes):
            return _rejected(["catalog_review_conflicts"], {"review": str(dest_review)})
        return {"status": "verified", "written": []}
    for model_id, model in review["models"].items():
        target = destination / f"{model_id}.pdb"
        if target.exists() and (not target.is_file() or _sha256(target) != model["sha256"]):
            return _rejected(["catalog_pdb_conflicts"], {"model": model_id, "path": str(target)})
    written: list[Path] = []
    try:
        destination.mkdir(parents=True, exist_ok=True)
        for model_id, source in sources.items():
            target = destination / f"{model_id}.pdb"
            digest = review["models"][model_id]["sha256"]
            if _sha256(source) != digest:
                _remove(written)
                return _rejected(["source_hash_mismatch"], {"model": model_id})
            if target.is_file():
                continue
            target.write_bytes(source.read_bytes())
            written.append(target)
            if _sha256(target) != digest:
                _remove(written)
                return _rejected(["published_hash_mismatch"], {"model": model_id})
        dest_review.write_bytes(review_bytes)
        written.append(dest_review)
        if not _catalog_matches(destination, review, review_bytes):
            _remove(written)
            return _rejected(["published_hash_mismatch"], {"review": str(dest_review)})
    except OSError as error:
        _remove(written)
        return _rejected(["publish_failed"], {"error": str(error)})
    return {"status": "verified", "written": written}


def _catalog_matches(destination: Path, review: dict[str, Any], review_bytes: bytes) -> bool:
    review_path = destination / "review.json"
    if not review_path.is_file() or review_path.read_bytes() != review_bytes:
        return False
    for model_id, model in review["models"].items():
        pdb = destination / f"{model_id}.pdb"
        if not pdb.is_file() or _sha256(pdb) != model["sha256"]:
            return False
    return True


def _catalog_child(catalog_root: Path, attempt_id: str) -> Path | None:
    if not attempt_id or attempt_id != Path(attempt_id).name or ".." in Path(attempt_id).parts:
        return None
    root = catalog_root.resolve()
    destination = (catalog_root / attempt_id).resolve()
    if destination != root and root not in destination.parents:
        return None
    return destination


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


_HEADER_ONLY_CSVS = (
    STATS_FILENAME,
    "mpnn_design_stats.csv",
    "trajectory_stats.csv",
)
_FAILURE_COUNTERS = ("Trajectory_one-hot_pLDDT", "Trajectory_final_pLDDT")


def demonstration_designs_directory(attempt_directory: Path, attempt_id: str) -> Path | None:
    """Return the designs directory this launcher actually writes.

    ``output_root`` is the journey attempt directory and ``execution_id`` is
    the attempt id, so the directory is ``<attempt_directory>/<attempt_id>/designs``.
    The flat ``<attempt_directory>/designs`` layout is the only other accepted path.
    """

    if not attempt_directory.is_dir() or attempt_directory.name != attempt_id:
        return None
    nested = attempt_directory / attempt_id / "designs"
    if _has_design_evidence(nested):
        return nested
    direct = attempt_directory / "designs"
    if _has_design_evidence(direct):
        return direct
    return None


def read_terminal_attempt_evidence(attempt_directory: Path, attempt_id: str) -> dict[str, Any]:
    """Read one saved attempt. This does not launch, scan, or rewrite artifacts."""

    progress_path = attempt_directory / "progress.json"
    raw_progress = _read_json_object(progress_path)
    designs = demonstration_designs_directory(attempt_directory, attempt_id)
    checks = _terminal_checks(attempt_directory, designs, raw_progress)
    failed = [name for name, passed in checks.items() if not passed]
    base = {
        "evidence_checks": checks,
        "raw_progress": raw_progress,
        "designs_directory": None if designs is None else str(designs),
        "scientific_validation_established": False,
        "accepted_candidate": False,
        "successful_candidate_generation": False,
        "failure_counters": None if designs is None else _failure_counters(designs / "failure_csv.csv"),
    }
    if failed:
        return {
            **base,
            "outcome": "execution_unresolved",
            "budget_compliance_established": False,
            "budget_usage": None,
        }
    assert designs is not None
    return {
        **base,
        "outcome": "budget_exhausted",
        "budget_compliance_established": True,
        "termination_reason": "attempted_trajectory_budget_exhausted",
        "failure_counters": _failure_counters(designs / "failure_csv.csv"),
        "budget_usage": _budget_usage(designs / "cellertz_trajectory_budget.json"),
    }


_REPRESENTATIVE_RULES = frozenset(
    {
        "Accepted and Ranked copies match this model's SHA-256",
        "only canonical model; Accepted and Ranked copies were not written",
    }
)


def read_accepted_launch_evidence(attempt_directory: Path, attempt_id: str) -> dict[str, Any]:
    """Read one verified attempt. Status alone is not acceptance.

    This checks the saved review, the one final-design row, both model
    structures, their capture-time sidecars, and the trajectory ledger.
    It does not launch, score, or rewrite raw files.
    """

    if attempt_directory.name != attempt_id or not attempt_directory.is_dir():
        return _acceptance_refused(["attempt_directory_mismatch"])
    designs = demonstration_designs_directory(attempt_directory, attempt_id)
    if designs is None:
        return _acceptance_refused(["designs_not_found"])
    validated = _validate_review_directory(attempt_directory, designs)
    if validated.get("status") != "verified":
        return _acceptance_refused(list(validated.get("reasons") or ["review_not_verified"]))
    review = validated["review"]
    reasons: list[str] = []
    design = review.get("design")
    if not isinstance(design, str) or not design:
        reasons.append("design_missing")
        design = None
    models = review.get("models")
    sequence = _accepted_model_sequence(models, reasons)
    representative = review.get("representative")
    if (
        not isinstance(representative, dict)
        or representative.get("scientific_validation") is not False
        or representative.get("rule") not in _REPRESENTATIVE_RULES
    ):
        reasons.append("representative_not_preserved")
    rows, row_reasons = _read_final_stats(designs / STATS_FILENAME)
    reasons.extend(row_reasons)
    if not row_reasons:
        if len(rows) != 1:
            reasons.append(f"final_design_count:{len(rows)}")
        elif design is not None and rows[0].get("Design", "").strip() != design:
            reasons.append("stats_design_mismatch")
        elif sequence is not None and "".join(rows[0].get("Sequence", "").split()) != sequence:
            reasons.append("stats_sequence_mismatch")
    usage, termination, ledger_reasons = _early_completion_usage(designs / "cellertz_trajectory_budget.json")
    reasons.extend(ledger_reasons)
    if design is not None and isinstance(models, dict) and set(models) == {"model1", "model2"}:
        reasons.extend(_captured_models(designs, models, design))
    if reasons or usage is None or sequence is None or design is None:
        return _acceptance_refused(reasons or ["accepted_launch_not_validated"])
    from cellertz.domains.protein_design.adapters.bindcraft import _candidate_id

    candidate_id = _candidate_id(design)
    return {
        "accepted": True,
        "reasons": [],
        "candidate_id": candidate_id,
        "candidate_identity": {
            "candidate_id": candidate_id,
            "sequence": sequence,
            "target_id": "PDL1",
            "design": design,
            "execution_id": attempt_id,
        },
        "budget_usage": usage,
        "termination_reason": termination,
        "summary": {
            "accepted": True,
            "design": design,
            "candidate_id": candidate_id,
            "models": ["model1", "model2"],
            "representative_rule": representative["rule"],
            "termination_reason": termination,
            "requested_attempted_trajectories": usage["requested_attempted_trajectories"],
            "scientific_validation_established": False,
        },
    }


def _acceptance_refused(reasons: list[str]) -> dict[str, Any]:
    unique = list(dict.fromkeys(reasons))
    return {
        "accepted": False,
        "reasons": unique,
        "summary": {
            "accepted": False,
            "reasons": unique,
            "scientific_validation_established": False,
        },
    }


def _accepted_model_sequence(models: object, reasons: list[str]) -> str | None:
    if not isinstance(models, dict) or set(models) != {"model1", "model2"}:
        reasons.append("model_set_not_model1_and_model2")
        return None
    sequences: list[str] = []
    for model_id in ("model1", "model2"):
        model = models.get(model_id)
        if not isinstance(model, dict) or model.get("binder_sequence_matches_csv") is not True:
            reasons.append(f"binder_sequence_not_matched:{model_id}")
            continue
        sequence = model.get("sequence")
        if not isinstance(sequence, str) or not sequence:
            reasons.append(f"binder_sequence_missing:{model_id}")
            continue
        sequences.append(sequence)
    if len(sequences) != 2 or sequences[0] != sequences[1]:
        reasons.append("binder_sequences_disagree")
        return None
    return sequences[0]


def _early_completion_usage(path: Path) -> tuple[dict[str, int] | None, str | None, list[str]]:
    ledger = _read_json_object(path)
    if ledger is None:
        return None, None, ["ledger_missing"]
    classified = classify_budget_evidence(ledger)
    reservations = ledger.get("reservations")
    reservation = reservations[0] if isinstance(reservations, list) and len(reservations) == 1 else None
    requested = ledger.get("requested_attempted_trajectories")
    observed = ledger.get("observed_attempted_trajectories")
    low = ledger.get("observed_low_confidence_trajectories")
    relaxed = ledger.get("observed_relaxed_trajectories")
    counts_are_ints = all(
        type(value) is int and not isinstance(value, bool) for value in (requested, observed, low, relaxed)
    )
    compliant = (
        ledger.get("adapter_version") == ADAPTER_VERSION
        and classified.get("termination_reason") == "successful_early_completion"
        and classified.get("compliance_established") is True
        and ledger.get("termination_reason") == "successful_early_completion"
        and ledger.get("compliance_established") is True
        and isinstance(reservation, dict)
        and reservation.get("scheduler_reserved") is True
        and reservation.get("state") == "finished"
        and counts_are_ints
        and requested >= 1
        and observed == 1
    )
    if not compliant:
        return None, None, ["ledger_not_early_completion"]
    return (
        {
            "attempted_trajectories": observed,
            "low_confidence_trajectories": low,
            "relaxed_trajectories": relaxed,
            "requested_attempted_trajectories": requested,
        },
        "successful_early_completion",
        [],
    )


def _captured_models(designs: Path, models: Mapping[str, Any], design: str) -> list[str]:
    from cellertz.application.journey_specialist_progress import retained_capture_evidence

    reasons = []
    for number in (1, 2):
        model = models[f"model{number}"]
        source_name = model.get("source_pdb") if isinstance(model, dict) else None
        source = _contained(designs, source_name) if isinstance(source_name, str) else None
        pae = designs / "CellertzEvidence" / "PAE" / f"{design}_model{number}_pae.npz"
        if source is None or not pae.is_file():
            reasons.append(f"capture_missing:model{number}")
            continue
        reason, _summary = retained_capture_evidence(source, pae, design, number)
        if reason is not None:
            reasons.append(f"capture_rejected:model{number}")
    return reasons


def terminal_outcome_from_designs(designs: Path) -> dict[str, Any] | None:
    """The observed budget ledger at a known designs directory, or none."""

    if not _budget_exhausted_ledger(designs / "cellertz_trajectory_budget.json"):
        return None
    if not all(_header_only_csv(designs / name) for name in _HEADER_ONLY_CSVS):
        return None
    counters = _failure_counters(designs / "failure_csv.csv")
    if counters is None:
        return None
    return {
        "outcome": "budget_exhausted",
        "designs_directory": str(designs),
        "failure_counters": counters,
        "budget_compliance_established": True,
        "scientific_validation_established": False,
    }


def _has_design_evidence(designs: Path) -> bool:
    return (designs / "cellertz_trajectory_budget.json").is_file() or (designs / STATS_FILENAME).is_file()


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _terminal_checks(
    attempt_directory: Path,
    designs: Path | None,
    progress: dict[str, Any] | None,
) -> dict[str, bool]:
    reasons = [] if progress is None else progress.get("reasons")
    ledger = None if designs is None else designs / "cellertz_trajectory_budget.json"
    return {
        "progress_rejected_final_design_row_missing": (
            isinstance(progress, dict)
            and progress.get("status") == "rejected"
            and isinstance(reasons, list)
            and "final_design_row_missing" in reasons
            and (attempt_directory / "progress.json").is_file()
        ),
        "known_designs_directory": designs is not None,
        "budget_exhausted_ledger": ledger is not None and _budget_exhausted_ledger(ledger),
        "header_only_stat_csvs": designs is not None
        and all(_header_only_csv(designs / name) for name in _HEADER_ONLY_CSVS),
        "failure_counters_not_metric_scores": designs is not None
        and _failure_counters(designs / "failure_csv.csv") is not None,
    }


def _budget_exhausted_ledger(path: Path) -> bool:
    document = _read_json_object(path)
    if document is None:
        return False
    reservations = document.get("reservations")
    reservation = reservations[0] if isinstance(reservations, list) and len(reservations) == 1 else None
    return (
        document.get("adapter_version") == ADAPTER_VERSION
        and document.get("requested_attempted_trajectories") == 1
        and document.get("observed_attempted_trajectories") == 1
        and document.get("observed_low_confidence_trajectories") == 1
        and document.get("observed_relaxed_trajectories") == 0
        and document.get("compliance_established") is True
        and document.get("termination_reason") == "attempted_trajectory_budget_exhausted"
        and isinstance(reservation, dict)
        and reservation.get("scheduler_reserved") is True
        and reservation.get("state") == "finished"
    )


def _budget_usage(path: Path) -> dict[str, int] | None:
    document = _read_json_object(path)
    if document is None or not _budget_exhausted_ledger(path):
        return None
    usage = {
        "attempted_trajectories": document.get("observed_attempted_trajectories"),
        "low_confidence_trajectories": document.get("observed_low_confidence_trajectories"),
        "relaxed_trajectories": document.get("observed_relaxed_trajectories"),
    }
    if any(type(value) is not int or isinstance(value, bool) for value in usage.values()):
        return None
    return usage


def _header_only_csv(path: Path) -> bool:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError):
        return False
    return len(lines) == 1 and "," in lines[0]


def _failure_counters(path: Path) -> dict[str, str] | None:
    try:
        rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    except (OSError, UnicodeError):
        return None
    if len(rows) != 1:
        return None
    counters: dict[str, str] = {}
    for name in _FAILURE_COUNTERS:
        value = rows[0].get(name)
        if not isinstance(value, str) or not value.strip():
            return None
        counters[name] = value.strip()
    return counters


def recover_completed_attempt(path: Path) -> dict[str, Any]:
    """Reprocess a finished attempt. This does not call Apptainer or rewrite raw outputs."""

    located = _locate_attempt(path)
    if located.get("status") == "outputs_rejected":
        return located
    artifact_dir = located["attempt_directory"]
    publication_dir = located["publication_directory"]
    designs = located["designs"]
    progress_files = _progress_files(path, artifact_dir)
    before_progress = {str(item): item.read_bytes() for item in progress_files}
    before_raw = _artifact_hashes(artifact_dir)
    history = _preserved_history(artifact_dir)
    if artifact_dir != publication_dir and (artifact_dir / "review.json").is_file():
        return _publish_catalog_copy(
            artifact_dir,
            publication_dir,
            designs,
            before_progress,
            before_raw,
            history,
        )
    if (publication_dir / "review.json").is_file():
        return _accept_existing_publication(
            publication_dir,
            designs,
            before_progress,
            before_raw,
            history,
        )
    ingested = ingest_bindcraft_output(
        designs,
        attempt_id=artifact_dir.name,
        execution="recovered_without_apptainer",
    )
    if ingested["status"] != "verified":
        return ingested
    published = publish_ingested_review(publication_dir, ingested)
    if published["status"] != "verified":
        return published
    if not _bytes_unchanged(before_progress) or _artifact_hashes(artifact_dir) != before_raw:
        _remove(published["written"])
        return _rejected(["raw_artifacts_changed"], {})
    if _history_changed(history):
        _remove(published["written"])
        return _rejected(["recovery_history_changed"], {})
    record = _recovery_record(
        artifact_dir.name,
        published["review_json"],
        before_progress,
        ingested["review"]["provenance"]["execution_constraints"],
    )
    recovery_path = publication_dir / "recovery.json"
    recovery_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if not _bytes_unchanged(before_progress) or _artifact_hashes(artifact_dir) != before_raw:
        _remove([*published["written"], recovery_path])
        return _rejected(["raw_artifacts_changed"], {})
    if _history_changed(history):
        _remove([*published["written"], recovery_path])
        return _rejected(["recovery_history_changed"], {})
    return record


def _locate_attempt(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        return _rejected(["attempt_directory_not_found"], {"path": str(path)})
    if (path / "designs" / STATS_FILENAME).is_file():
        return {
            "attempt_directory": path,
            "publication_directory": path,
            "designs": path / "designs",
        }
    found = [
        child
        for child in sorted(path.iterdir())
        if child.is_dir() and (child / "designs" / STATS_FILENAME).is_file()
    ]
    if len(found) == 1:
        artifact_dir = found[0]
        # The catalog reads <catalog>/<attempt id>/review.json. The raw outputs
        # of this run live one level deeper, under the same attempt id.
        publication_dir = path if path.name == artifact_dir.name else artifact_dir
        return {
            "attempt_directory": artifact_dir,
            "publication_directory": publication_dir,
            "designs": artifact_dir / "designs",
        }
    return _rejected(
        ["attempt_directory_not_found"],
        {"path": str(path), "candidates": [child.name for child in found]},
    )


def _publish_catalog_copy(
    artifact_dir: Path,
    publication_dir: Path,
    designs: Path,
    before_progress: dict[str, bytes],
    before_raw: dict[str, str],
    history: dict[str, bytes],
) -> dict[str, Any]:
    """Validate the nested review, then publish it where the catalog can read it."""

    validated = _validate_review_directory(artifact_dir, designs)
    if validated.get("status") != "verified":
        return validated
    review_path = publication_dir / "review.json"
    if review_path.exists() and review_path.read_bytes() != validated["review_bytes"]:
        return _rejected(["catalog_review_conflicts"], {"review": str(review_path)})
    written: list[Path] = []
    try:
        for model_id, source in validated["sources"].items():
            target = publication_dir / f"{model_id}.pdb"
            digest = validated["review"]["models"][model_id]["sha256"]
            if target.exists():
                if _sha256(target) != digest:
                    _remove(written)
                    return _rejected(["catalog_pdb_conflicts"], {"model": model_id, "path": str(target)})
                continue
            if _inside(publication_dir, f"{model_id}.pdb") is None:
                _remove(written)
                return _rejected(["catalog_path_escapes"], {"model": model_id})
            target.write_bytes(source.read_bytes())
            written.append(target)
            if _sha256(target) != digest:
                _remove(written)
                return _rejected(["published_hash_mismatch"], {"model": model_id})
        if not review_path.exists():
            review_path.write_bytes(validated["review_bytes"])
            written.append(review_path)
    except OSError as error:
        _remove(written)
        return _rejected(["publish_failed"], {"error": str(error)})
    confirmed = _validate_review_directory(publication_dir, designs)
    if confirmed.get("status") != "verified":
        _remove(written)
        return confirmed
    if not _unchanged(before_progress, before_raw, artifact_dir, history):
        _remove(written)
        return _rejected(["raw_artifacts_changed"], {})
    record = _recovery_record(
        artifact_dir.name,
        str(review_path),
        before_progress,
        validated["review"]["provenance"]["execution_constraints"],
    )
    record["preserved_review"] = str(artifact_dir / "review.json")
    record["rewritten"] = bool(written)
    return record


def _accept_existing_publication(
    publication_dir: Path,
    designs: Path,
    before_progress: dict[str, bytes],
    before_raw: dict[str, str],
    history: dict[str, bytes],
) -> dict[str, Any]:
    validated = _validate_review_directory(publication_dir, designs)
    if validated.get("status") != "verified":
        return validated
    if not _unchanged(before_progress, before_raw, publication_dir, history):
        return _rejected(["raw_artifacts_changed"], {})
    record = _recovery_record(
        publication_dir.name,
        str(publication_dir / "review.json"),
        before_progress,
        validated["review"]["provenance"]["execution_constraints"],
    )
    record["rewritten"] = False
    return record


def _validate_review_directory(directory: Path, designs: Path) -> dict[str, Any]:
    review_path = directory / "review.json"
    try:
        review_bytes = review_path.read_bytes()
        review = json.loads(review_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return _rejected(["review_unreadable"], {"review": str(review_path), "error": str(error)})
    attempt_id = review.get("attempt_id") if isinstance(review, dict) else None
    same_attempt = directory.name == designs.parent.name == attempt_id
    if not isinstance(review, dict) or not same_attempt:
        return _rejected(["review_attempt_mismatch"], {"review": str(review_path)})
    models = review.get("models")
    provenance = review.get("provenance")
    if not isinstance(models, dict) or not models or not isinstance(provenance, dict):
        return _rejected(["review_references_missing"], {"review": str(review_path)})
    csv_path = designs / STATS_FILENAME
    if not csv_path.is_file() or provenance.get("stats_csv_sha256") != _sha256(csv_path):
        return _rejected(["stats_csv_hash_mismatch"], {"csv": str(csv_path)})
    constraints = provenance.get("execution_constraints")
    if not isinstance(constraints, dict) or "unmet" not in constraints:
        return _rejected(["execution_constraints_missing"], {})
    sources: dict[str, Path] = {}
    for model_id, model in models.items():
        if not isinstance(model_id, str) or not isinstance(model, dict):
            return _rejected(["model_identity_invalid"], {"model": str(model_id)})
        if not re.fullmatch(r"model[1-9][0-9]*", model_id):
            return _rejected(["model_identity_invalid"], {"model": model_id})
        source_name = model.get("source_pdb")
        if not isinstance(source_name, str):
            return _rejected(["source_pdb_missing"], {"model": model_id})
        source = _contained(designs, source_name)
        digest = model.get("sha256")
        if source is None or not isinstance(digest, str) or _sha256(source) != digest:
            return _rejected(["source_hash_mismatch"], {"model": model_id, "source_pdb": source_name})
        if _chain_sequence(source, "B") != model.get("sequence"):
            return _rejected([f"binder_sequence_mismatch:{model_id}"], {})
        published = directory / f"{model_id}.pdb"
        if published.is_file() and _sha256(published) != digest:
            return _rejected(["published_hash_mismatch"], {"model": model_id})
        for copy_name in ("accepted_pdb", "ranked_pdb"):
            copy = model.get(copy_name)
            if copy is None:
                continue
            if not isinstance(copy, str) or _contained(designs, copy) is None:
                return _rejected(["copy_path_escapes"], {"model": model_id, "path": str(copy)})
            if _sha256(_contained(designs, copy)) != digest:
                return _rejected(["copy_hash_mismatch"], {"model": model_id, "path": copy})
        sources[model_id] = source
    return {"status": "verified", "review": review, "review_bytes": review_bytes, "sources": sources}


def _contained(root: Path, relative: str) -> Path | None:
    candidate = _inside(root, relative)
    if candidate is None or not candidate.is_file():
        return None
    return candidate


def _inside(root: Path, relative: str) -> Path | None:
    if not relative or relative.startswith(("/", "\\")) or ".." in Path(relative).parts:
        return None
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        return None
    return candidate


def _preserved_history(artifact_dir: Path) -> dict[str, bytes]:
    history: dict[str, bytes] = {}
    for name in ("review.json", "recovery.json", "progress.json"):
        path = artifact_dir / name
        if path.is_file():
            history[str(path)] = path.read_bytes()
    return history


def _history_changed(history: dict[str, bytes]) -> bool:
    return not _bytes_unchanged(history)


def _unchanged(
    before_progress: dict[str, bytes],
    before_raw: dict[str, str],
    artifact_dir: Path,
    history: dict[str, bytes],
) -> bool:
    return (
        _bytes_unchanged(before_progress)
        and _artifact_hashes(artifact_dir) == before_raw
        and not _history_changed(history)
    )


def _recovery_record(
    attempt_id: str,
    review_json: str,
    before_progress: dict[str, bytes],
    constraints: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "verified",
        "attempt_id": attempt_id,
        "apptainer_invoked": False,
        "raw_artifacts_preserved": True,
        "review_json": review_json,
        "original_progress": [
            {"path": name, "sha256": hashlib.sha256(payload).hexdigest(), "text": payload.decode("utf-8")}
            for name, payload in before_progress.items()
        ],
        "execution_constraints": constraints,
        "reasons": [],
    }


def _progress_files(given: Path, attempt_directory: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in (attempt_directory / "progress.json", given / "progress.json"):
        if candidate.is_file() and candidate not in files:
            files.append(candidate)
    return files


def _artifact_hashes(attempt_directory: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for folder in ("designs", ".cellertz_runtime"):
        root = attempt_directory / folder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                hashes[path.relative_to(attempt_directory).as_posix()] = _sha256(path)
    return hashes


def _bytes_unchanged(before: dict[str, bytes]) -> bool:
    for name, payload in before.items():
        path = Path(name)
        if not path.is_file() or path.read_bytes() != payload:
            return False
    return True


def _remove(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            continue


def gpu_launch_blocker(report: Mapping[str, Any] | None) -> str | None:
    """Block only a missing or unreadable GPU. Occupancy does not block."""

    if not isinstance(report, Mapping):
        return "gpu_not_ready"
    status = report.get("status")
    if status in {"launch_ready", "selected_gpu_busy"}:
        return None
    if status in {"selected_gpu_missing", "nvidia_smi_unavailable", "gpu_not_ready"}:
        return str(status)
    if report.get("launch_ready") is True:
        return None
    return "gpu_not_ready"


def gpu_launch_readiness(
    gpu_index: int,
    *,
    query: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read occupancy and free memory. This does not signal any process.

    The selected index must be present. A compute process on that index is
    recorded and does not refuse the launch. Free memory is not a reservation.
    """

    try:
        snapshot = _nvidia_smi_occupancy() if query is None else query()
    except (OSError, subprocess.TimeoutExpired):
        snapshot = {"available": False}
    if not isinstance(snapshot, Mapping) or snapshot.get("available") is not True:
        return {"status": "nvidia_smi_unavailable", "launch_ready": False, "gpu_index": gpu_index}
    if not isinstance(snapshot.get("compute_apps"), list):
        return {"status": "nvidia_smi_unavailable", "launch_ready": False, "gpu_index": gpu_index}
    apps = _selected_gpu_apps(snapshot, gpu_index)
    if apps is None:
        return {"status": "selected_gpu_missing", "launch_ready": False, "gpu_index": gpu_index}
    return {
        "status": "launch_ready",
        "launch_ready": True,
        "gpu_index": gpu_index,
        "occupancy": "shared" if apps else "clear",
        "compute_apps": apps,
        "memory": _selected_gpu_memory(snapshot, gpu_index),
    }


def vram_failure_report(
    stdout: str,
    stderr: str,
    *,
    memory_query: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Record a GPU-memory failure. This does not signal other processes or retry."""

    text = f"{stdout}\n{stderr}"
    if not _vram_exhausted(text):
        return None
    excerpt = text.strip()
    if len(excerpt) > 2000:
        excerpt = excerpt[-2000:]
    diagnostics: dict[str, Any] = {"process_output": excerpt}
    query = memory_query if memory_query is not None else _informative_gpu_memory
    try:
        memory = query()
    except (OSError, subprocess.TimeoutExpired):
        memory = None
    if isinstance(memory, Mapping):
        diagnostics["gpu_memory"] = dict(memory)
    return {
        "status": "vram_insufficient",
        "launched": True,
        "automatic_retry": False,
        "other_services_signaled": False,
        "diagnostics": diagnostics,
    }


def _nvidia_smi_occupancy() -> dict[str, Any]:
    try:
        gpus = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,memory.used,memory.free",
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        apps = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name",
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {"available": False, "gpus": [], "compute_apps": []}
    if gpus.returncode != 0 or apps.returncode != 0:
        return {"available": False, "gpus": [], "compute_apps": []}
    return {
        "available": True,
        "gpus": _parse_gpu_index_rows(gpus.stdout),
        "compute_apps": _parse_compute_app_rows(apps.stdout),
    }


def _parse_gpu_index_rows(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        parts = [item.strip() for item in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        row: dict[str, Any] = {"index": index, "uuid": parts[1]}
        if len(parts) >= 4:
            row["memory_used"] = parts[2]
            row["memory_free"] = parts[3]
        rows.append(row)
    return rows


def _parse_compute_app_rows(raw: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        parts = [item.strip() for item in line.split(",")]
        if len(parts) < 3 or not parts[0]:
            continue
        rows.append({"gpu_uuid": parts[0], "pid": parts[1], "process_name": parts[2]})
    return rows


def _selected_gpu_apps(snapshot: Mapping[str, Any], gpu_index: int) -> list[Mapping[str, Any]] | None:
    gpus = snapshot.get("gpus")
    apps = snapshot.get("compute_apps")
    if not isinstance(gpus, list) or not isinstance(apps, list):
        return None
    uuid = next(
        (
            item.get("uuid")
            for item in gpus
            if isinstance(item, Mapping) and item.get("index") == gpu_index
        ),
        None,
    )
    if not isinstance(uuid, str) or not uuid:
        return None
    return [
        item
        for item in apps
        if isinstance(item, Mapping) and item.get("gpu_uuid") == uuid
    ]


def _selected_gpu_memory(snapshot: Mapping[str, Any], gpu_index: int) -> dict[str, Any] | None:
    gpus = snapshot.get("gpus")
    if not isinstance(gpus, list):
        return None
    for item in gpus:
        if not isinstance(item, Mapping) or item.get("index") != gpu_index:
            continue
        used = item.get("memory_used")
        free = item.get("memory_free")
        if used is None and free is None:
            return None
        return {"used": used, "free": free}
    return None


def _gpu_observation(report: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        return {}
    return {
        "occupancy": report.get("occupancy"),
        "compute_apps": report.get("compute_apps"),
        "memory": report.get("memory"),
    }


def _vram_exhausted(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "cuda out of memory",
        "cuda_error_out_of_memory",
        "resourceexhaustederror",
        "out of memory while trying to allocate",
    )
    if any(marker in lowered for marker in markers):
        return True
    return "xlaruntimeerror" in lowered and "out of memory" in lowered


def _informative_gpu_memory() -> dict[str, Any]:
    """Read GPU memory. This does not signal a process."""

    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,memory.used,memory.free",
            "--format=csv,noheader",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if completed.returncode != 0:
        return {"available": False}
    rows = []
    for line in completed.stdout.splitlines():
        parts = [item.strip() for item in line.split(",")]
        if len(parts) < 3:
            continue
        rows.append({"index": parts[0], "used": parts[1], "free": parts[2]})
    return {"available": True, "gpus": rows}


def host_commands() -> str:
    """Operator commands. Nothing here is executed by importing this module."""

    return f"""\
# Profile {PROFILE_ID}
# Docker reconstruction stays paused. Do not build an image or start a second dockerd.
export CELLERTZ_SCIENTIFIC_RUN_EXECUTION=apptainer-demonstration
export CELLERTZ_ATTEMPT_CATALOG=/mnt/hd10tb/cellertz-pdl1-demonstration
export CELLERTZ_APPTAINER_BIN=/usr/bin/apptainer
test ! -e /home/fabio/projects/cellertz-ai-coscientist/apps || echo checkout-unchanged
sha256sum {SIF_PATH}
# The SHA-256 must be {SIF_SHA256}
nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
# The server reads occupancy and free memory before a real launch.
# launch_ready means the selected index is present. A compute process on
# that index is informative and does not refuse the launch. The reading
# does not reserve VRAM and does not signal other GPU work.
# Exports below are read when a new server process starts. They do not
# change a server that is already running.
python3 -c 'import secrets; print("attempt." + secrets.token_hex(4))'
# Request text must include {SMOKE_TOKEN} and {FILTER_POLICY}.
# Approved scientific fields: seed {DEMONSTRATION_SEED}, length {DEMONSTRATION_LENGTH}, \
cpus {DEMONSTRATION_CPUS}, memory {DEMONSTRATION_MEMORY_MIB} MiB, \
deadline {DEMONSTRATION_DEADLINE_SECONDS}s.
# The host GPU index is not one of those fields. The approved plan records an
# explicit index. The launch request and CUDA_VISIBLE_DEVICES use that same
# index. The profile does not choose or substitute a GPU.
# Launch is POST /api/generation-plan/apptainer-demonstration
# after human approval. The coordinator cannot call that route.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) == 3 and args[1] == "--recover":
        outcome = recover_completed_attempt(Path(args[2]))
        printable = {key: value for key, value in outcome.items() if key != "sources"}
        print(json.dumps(printable, indent=2))
        return 0 if outcome.get("status") == "verified" else 1
    if len(args) < 4 or args[1] != "--supervise":
        return 2
    return supervise(int(args[2]), args[3:])


if __name__ == "__main__":
    raise SystemExit(main())
