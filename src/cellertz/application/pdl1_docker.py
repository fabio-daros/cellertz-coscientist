"""Governed Docker boundary for one PDL1 attempt.

This module does not download an image, start a daemon, or run protein
design on import. ``CliDockerControl`` uses ``ProductionCommandRunner``.
Enabled startup calls that runner only while observing Docker and
installing the systemd deadline. Disabled startup does not construct
it. Tests inject a process factory. A scripted result is not evidence
that a GPU host enforced the same limits.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from cellertz.application.contracts import ApplicationContractError, ExecutionBudget
from cellertz.application.pdl1_attempt import ExternalAttempt
from cellertz.application.pdl1_container_entrypoint import (
    DESIGN_COUNT_MAX,
    DESIGN_COUNT_MIN,
    LENGTH_MAX,
    LENGTH_MIN,
    SEED_MAX,
    SEED_MIN,
)
from cellertz.application.pdl1_input_verification import Pdl1VerifiedInputs
from cellertz.application.pdl1_selection import Pdl1InputSelection
from cellertz.application.request_draft import (
    FILTER_POLICIES,
    ProteinDesignRequest,
    ProteinDesignRequestDraft,
)
from cellertz.application.scientific_run import (
    _EXECUTION_SCHEMA,
    ExecutorArtifact,
    ScientificExecutionResult,
    ScientificRun,
    _canonical,
)
from cellertz.application.scientific_runner_readiness import (
    _ARGV_TOKEN,
    _IMAGE_DIGEST,
    DOCKER_PROFILE_ID,
)
from cellertz.domains.protein_design.agents.generation import AMINO_ACIDS
from cellertz.domains.protein_design.execution.bindcraft_apptainer import CONTAINER_NO_FILTERS

_FAILED = ScientificExecutionResult("failed", ())
_INPUT_FILES = (
    ("target_structure", "target.pdb"),
    ("settings", "settings.json"),
    ("advanced_profile_canonical", "advanced.json"),
)
_CONTAINER_INPUT = "/cellertz/input"
_CONTAINER_OUTPUT = "/cellertz/output/design_records.csv"
_SUPERVISOR = (
    "import subprocess,sys,time\n"
    "container,deadline,daemon,image,binary=sys.argv[1:6]\n"
    "sys.stdout.write('ready\\n'); sys.stdout.flush()\n"
    "time.sleep(max(0.0, float(deadline)-time.time()))\n"
    "shown=subprocess.run([binary,'inspect',container],capture_output=True,check=False)\n"
    "if shown.returncode!=0:\n"
    "    raise SystemExit(1)\n"
    "subprocess.run([binary,'kill',container],check=False)\n"
)
CONFIG_ENV = "CELLERTZ_PDL1_DOCKER_CONFIG"
WORKER_LOSS_DEADLINE = "pdl1_worker_loss_deadline_unresolved"


def _parse_inspect(text: str) -> object:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return "error"
    if type(parsed) is list:
        parsed = parsed[0] if parsed else "error"
    return parsed if type(parsed) is dict else "error"


_COMMAND_TIMEOUT_SECONDS = 30.0
_READ_CHUNK = 65536
_CONFIG_LIMIT = 65536
_REGULAR_TYPES = frozenset({tarfile.REGTYPE, tarfile.AREGTYPE})


class ProductionCommandRunner:
    """Bounded Docker CLI runner. It does not use a shell.

    stdout and stderr are read in chunks and must be nonblocking. Stream
    EOF does not finish the command: the process is waited until it exits
    or the same deadline expires. Cleanup kills only after timeout,
    overflow, or a failed read.
    """

    def __init__(
        self,
        process_factory: Callable[..., object] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        set_blocking: Callable[[int, bool], object] | None = None,
        *,
        docker_host: str | None = None,
        docker_root: str | None = None,
    ) -> None:
        self._popen = process_factory if process_factory is not None else subprocess.Popen
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep
        self._set_blocking = set_blocking if set_blocking is not None else os.set_blocking
        if docker_host is None and docker_root is None:
            self.docker_host: str | None = None
            self.docker_root: str | None = None
        else:
            self.docker_host = require_docker_endpoint(docker_host, docker_root)
            self.docker_root = docker_root if type(docker_root) is str else None

    def __call__(
        self, argv: Sequence[str], *, max_bytes: int, timeout: float | None = None
    ) -> bytes:
        if (
            type(argv) is not list
            or not argv
            or any(type(item) is not str for item in argv)
            or type(max_bytes) is not int
            or isinstance(max_bytes, bool)
            or max_bytes < 1
        ):
            raise ApplicationContractError("pdl1_container_policy_rejected")
        limit = _COMMAND_TIMEOUT_SECONDS if timeout is None else timeout
        if type(limit) not in {int, float} or isinstance(limit, bool) or limit <= 0:
            raise ApplicationContractError("pdl1_container_policy_rejected")
        command = list(argv)
        if command and command[0] == "docker":
            if self.docker_host is None or self.docker_root is None:
                raise ApplicationContractError("pdl1_docker_endpoint_required")
            command = explicit_docker_argv(self.docker_host, command)
            require_docker_socket(self.docker_host, self.docker_root)
        try:
            process = self._popen(
                command,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                env={"PATH": "/usr/bin:/bin"},
            )
        except OSError:
            raise ApplicationContractError("pdl1_docker_command_failed") from None
        failed = False
        try:
            stdout = _read_command(process, max_bytes, self._monotonic() + float(limit), self)
        except ApplicationContractError:
            failed = True
            raise
        except Exception:  # noqa: BLE001 — driver text must not escape
            failed = True
            raise ApplicationContractError("pdl1_docker_command_failed") from None
        finally:
            _reap_command(process, terminate=failed)
        if getattr(process, "returncode", None) != 0:
            raise ApplicationContractError("pdl1_docker_command_failed")
        return stdout


def _read_command(
    process: object, max_bytes: int, deadline: float, runner: ProductionCommandRunner
) -> bytes:
    stdout = bytearray()
    stderr = bytearray()
    out = getattr(process, "stdout", None)
    err = getattr(process, "stderr", None)
    pending = [stream for stream in (out, err) if stream is not None]
    poll = getattr(process, "poll", None)
    if not callable(poll):
        raise ApplicationContractError("pdl1_docker_command_failed")
    while True:
        if runner._monotonic() >= deadline:
            raise ApplicationContractError("pdl1_docker_command_timeout")
        for stream in tuple(pending):
            block = _read_some(stream, min(_READ_CHUNK, max_bytes + 1), runner)
            if block is None:
                continue
            if block == b"":
                pending.remove(stream)
                continue
            target = stdout if stream is out else stderr
            if len(target) + len(block) > max_bytes:
                raise ApplicationContractError("pdl1_output_too_large")
            target.extend(block)
        if poll() is not None and not pending:
            return bytes(stdout)
        runner._sleep(0.01)


def _read_some(stream: object, count: int, runner: ProductionCommandRunner) -> bytes | None:
    fileno = getattr(stream, "fileno", None)
    if not callable(fileno):
        raise ApplicationContractError("pdl1_docker_command_failed")
    try:
        descriptor = fileno()
    except OSError:
        raise ApplicationContractError("pdl1_docker_command_failed") from None
    if type(descriptor) is not int or isinstance(descriptor, bool) or descriptor < 0:
        raise ApplicationContractError("pdl1_docker_command_failed")
    try:
        runner._set_blocking(descriptor, False)
    except (OSError, AttributeError, ValueError):
        raise ApplicationContractError("pdl1_docker_command_failed") from None
    try:
        block = stream.read(count)  # type: ignore[attr-defined]
    except BlockingIOError:
        return None
    if block is None:
        return None
    if type(block) is not bytes:
        raise ApplicationContractError("pdl1_docker_command_failed")
    return block


def _reap_command(process: object, *, terminate: bool) -> None:
    poll = getattr(process, "poll", None)
    kill = getattr(process, "kill", None)
    wait = getattr(process, "wait", None)
    if terminate:
        try:
            if callable(poll) and poll() is None and callable(kill):
                kill()
        except OSError:
            pass
    try:
        if callable(wait) and (terminate or (callable(poll) and poll() is not None)):
            wait(timeout=1)
    except Exception:  # noqa: BLE001 — reap must not hide the command result
        reaped = False
    else:
        reaped = True
    del reaped
    for name in ("stdout", "stderr", "stdin"):
        stream = getattr(process, name, None)
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except OSError:
                pass


@dataclass(frozen=True)
class DeadlineAck:
    ready: bool
    expired: bool


class DetachedDeadlineSupervisor:
    """Acknowledges before start and kills by container id.

    ``start_new_session`` only reparents a sleeper. It is not a host timer
    outside the worker process tree, so registration stays blocked.
    """

    def __init__(self, starter: Callable[..., object] | None = None) -> None:
        self._start = starter if starter is not None else subprocess.Popen

    def acknowledge(
        self,
        *,
        container_id: str,
        deadline_epoch: float,
        daemon_id: str,
        image: str,
        docker_bin: str = "docker",
    ) -> DeadlineAck:
        if (
            docker_bin != "docker"
            or type(container_id) is not str
            or not container_id
            or "/" in container_id
        ):
            return DeadlineAck(ready=False, expired=False)
        read_fd, write_fd = os.pipe()
        argv = [
            sys.executable,
            "-c",
            _SUPERVISOR,
            container_id,
            str(deadline_epoch),
            daemon_id,
            image,
            docker_bin,
        ]
        try:
            self._start(
                argv,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=write_fd,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env={"PATH": "/usr/bin:/bin"},
            )
        except Exception:  # noqa: BLE001 — supervisor startup failure is not a start
            os.close(read_fd)
            os.close(write_fd)
            return DeadlineAck(ready=False, expired=False)
        os.close(write_fd)
        try:
            ready = os.read(read_fd, 16)
        except OSError:
            return DeadlineAck(ready=False, expired=False)
        finally:
            os.close(read_fd)
        if ready != b"ready\n":
            return DeadlineAck(ready=False, expired=False)
        return DeadlineAck(ready=True, expired=time.time() >= deadline_epoch)


class ScriptedDeadline:
    """Test double. It does not create a process."""

    def __init__(self, *, ready: bool = True, expired: bool = False) -> None:
        self.ready = ready
        self.expired = expired
        self.acks: list[dict[str, object]] = []

    def acknowledge(
        self,
        *,
        container_id: str,
        deadline_epoch: float,
        daemon_id: str,
        image: str,
        docker_bin: str = "docker",
    ) -> DeadlineAck:
        del docker_bin
        self.acks.append(
            {
                "container_id": container_id,
                "deadline_epoch": deadline_epoch,
                "daemon_id": daemon_id,
                "image": image,
            }
        )
        return DeadlineAck(ready=self.ready, expired=self.expired)


@dataclass(frozen=True)
class DockerExecutionConfig:
    """Server-owned execution settings. Not a browser payload."""

    repository: str
    image_digest: str
    image_id: str
    entrypoint: str
    argv: tuple[str, ...]
    gpu_device: str
    deployment_id: str
    staging_root: str
    storage_bytes: int
    log_bytes: int
    docker_host: str
    docker_root: str
    vram_mib: int | None = None
    manifest_path: str | None = None
    input_root: str | None = None
    deadline_manager: str | None = None
    deadline_state_dir: str | None = None
    deadline_interpreter: str | None = None

    def __post_init__(self) -> None:
        argv = self.argv
        require_docker_endpoint(self.docker_host, self.docker_root)
        repository_ok = (
            type(self.repository) is str
            and "/" in self.repository
            and " " not in self.repository
            and "@" not in self.repository
            and ".." not in self.repository.split("/")
        )
        if (
            not repository_ok
            or _IMAGE_DIGEST.fullmatch(self.image_digest or "") is None
            or _IMAGE_DIGEST.fullmatch(self.image_id or "") is None
            or self.image_id == self.image_digest
            or type(self.entrypoint) is not str
            or not self.entrypoint
            or type(argv) is not tuple
            or not 1 <= len(argv) <= 16
            or any(type(item) is not str or _ARGV_TOKEN.fullmatch(item) is None for item in argv)
            or type(self.gpu_device) is not str
            or not self.gpu_device.isdigit()
            or type(self.deployment_id) is not str
            or not self.deployment_id
            or "/" in self.deployment_id
            or type(self.staging_root) is not str
            or not os.path.isabs(self.staging_root)
            or "\0" in self.staging_root
            or type(self.storage_bytes) is not int
            or isinstance(self.storage_bytes, bool)
            or self.storage_bytes < 1
            or type(self.log_bytes) is not int
            or isinstance(self.log_bytes, bool)
            or self.log_bytes < 1
            or (self.vram_mib is not None and type(self.vram_mib) is not int)
            or not _optional_absolute(self.manifest_path)
            or not _optional_absolute(self.input_root)
            or not _optional_absolute(self.deadline_state_dir)
            or not _optional_absolute(self.deadline_interpreter)
            or (
                self.deadline_manager is not None
                and self.deadline_manager not in {"system", "user"}
            )
        ):
            raise ApplicationContractError("pdl1_execution_configuration_invalid")


def require_docker_endpoint(value: object, root: object) -> str:
    """Accept one ``unix://`` socket inside ``root``. No default daemon."""

    if type(value) is not str or value == "":
        raise ApplicationContractError("pdl1_docker_endpoint_required")
    root_parts = root.split("/") if type(root) is str else []
    if (
        type(root) is not str
        or not os.path.isabs(root)
        or root.endswith("/")
        or any(char in root for char in "\0\n\r")
        or any(part in {"", ".", ".."} for part in root_parts[1:])
    ):
        raise ApplicationContractError("pdl1_docker_endpoint_rejected")
    lowered = value.lower()
    if lowered.startswith(("tcp://", "ssh://", "http://", "https://", "fd://", "npipe://")):
        raise ApplicationContractError("pdl1_docker_endpoint_rejected")
    if not value.startswith("unix://") or any(char in value for char in "@?#%\0\n\r \t\\"):
        raise ApplicationContractError("pdl1_docker_endpoint_rejected")
    path = value[len("unix://") :]
    parts = path.split("/")
    if (
        not path.startswith("/")
        or path.endswith("/")
        or any(part in {"", ".", ".."} for part in parts[1:])
    ):
        raise ApplicationContractError("pdl1_docker_endpoint_rejected")
    if not path.startswith(root + "/"):
        raise ApplicationContractError("pdl1_docker_endpoint_rejected")
    return value


def require_docker_socket(value: object, root: object) -> str:
    """Real use. The socket must exist and resolve inside ``root``.

    Planning uses :func:`require_docker_endpoint` and does not stat the
    socket. A symlink on the final file or on an ancestor that leaves
    ``root`` is rejected here, before a process is started.
    """

    endpoint = require_docker_endpoint(value, root)
    if type(root) is not str:
        raise ApplicationContractError("pdl1_docker_endpoint_rejected")
    root_real = os.path.realpath(root)
    if not os.path.isdir(root_real):
        raise ApplicationContractError("pdl1_docker_endpoint_rejected")
    path = endpoint[len("unix://") :]
    current = root
    for part in path[len(root) :].split("/")[1:]:
        current = current + "/" + part
        if not os.path.lexists(current):
            raise ApplicationContractError("pdl1_docker_endpoint_rejected")
        resolved = os.path.realpath(current)
        if resolved != root_real and not resolved.startswith(root_real + os.sep):
            raise ApplicationContractError("pdl1_docker_endpoint_rejected")
    if os.path.isdir(os.path.realpath(path)):
        raise ApplicationContractError("pdl1_docker_endpoint_rejected")
    return endpoint


_BOOLEAN_GLOBALS = frozenset(
    {"--debug", "-D", "--help", "-h", "--tls", "--tlsverify", "--version", "-v"}
)
_VALUE_GLOBALS = frozenset({"--config", "--log-level", "--tlscacert", "--tlscert", "--tlskey"})


def _global_flag(token: str) -> tuple[str, str | None]:
    """Classify one client-global token. Unknown flags are not given an arity.

    The second item is an attached value. ``None`` means the next argv
    token is that value. Boolean flags use an empty string.
    """

    if token in _BOOLEAN_GLOBALS:
        return ("bool", "")
    if token in {"--host", "-H"}:
        return ("host", None)
    if token in {"--context", "-c"}:
        return ("context", None)
    if token == "-l" or token in _VALUE_GLOBALS:
        return ("value", None)
    if token.startswith("--host="):
        return ("host", token[len("--host=") :])
    if token.startswith("--context="):
        return ("context", token[len("--context=") :])
    for name in _VALUE_GLOBALS:
        prefix = name + "="
        if token.startswith(prefix):
            return ("value", token[len(prefix) :])
    if token.startswith("--") or "=" in token:
        return ("unknown", None)
    if token.startswith("-H") and len(token) > 2:
        return ("host", token[2:])
    if token.startswith("-c") and len(token) > 2:
        return ("context", token[2:])
    if token.startswith("-l") and len(token) > 2:
        return ("value", token[2:])
    return ("unknown", None)


def explicit_docker_argv(endpoint: str, argv: Sequence[str]) -> list[str]:
    """Insert one canonical ``--host`` or reject another destination.

    Known client globals are read only before the subcommand. A value
    option consumes its following token, so that token is not treated as
    the subcommand. Unknown globals are rejected instead of assigned an
    arity. Later arguments, including ``-c`` and ``--context``, stay in
    place. Calling this twice does not emit a second ``--host``.
    """

    if type(endpoint) is not str or not endpoint.startswith("unix:///"):
        raise ApplicationContractError("pdl1_docker_endpoint_required")
    if type(argv) not in {list, tuple} or not argv or type(argv[0]) is not str:
        raise ApplicationContractError("pdl1_container_policy_rejected")
    command = list(argv)
    if command[0] != "docker":
        raise ApplicationContractError("pdl1_container_policy_rejected")
    prefix: list[str] = []
    seen_host = False
    index = 1
    while index < len(command):
        token = command[index]
        if token == "--":
            prefix.append(token)
            index += 1
            break
        if not token.startswith("-"):
            break
        kind, attached = _global_flag(token)
        if kind == "unknown":
            raise ApplicationContractError("pdl1_docker_endpoint_conflict")
        if kind == "bool":
            prefix.append(token)
            index += 1
            continue
        if attached is None:
            if index + 1 >= len(command):
                raise ApplicationContractError("pdl1_docker_endpoint_conflict")
            attached = command[index + 1]
            kept = [token, attached]
            index += 2
        else:
            if attached == "":
                raise ApplicationContractError("pdl1_docker_endpoint_conflict")
            kept = [token]
            index += 1
        if kind == "context" or (kind == "host" and (seen_host or attached != endpoint)):
            raise ApplicationContractError("pdl1_docker_endpoint_conflict")
        if kind == "host":
            seen_host = True
            continue
        prefix.extend(kept)
    rest = command[index:]
    if rest[:1] == ["context"]:
        raise ApplicationContractError("pdl1_docker_endpoint_conflict")
    return ["docker", "--host", endpoint, *prefix, *rest]


def _optional_absolute(value: str | None) -> bool:
    if value is None:
        return True
    return type(value) is str and os.path.isabs(value) and "\0" not in value and "\n" not in value


def docker_gaps(
    info: Mapping[str, Any] | None,
    image: Mapping[str, Any] | None,
    config: DockerExecutionConfig | None,
) -> tuple[str, ...]:
    """Names of missing host facts. This does not run a container."""

    if config is None or type(info) is not dict or type(image) is not dict:
        return ("docker_facts",)
    missing: list[str] = []
    if info.get("OSType") != "linux":
        missing.append("architecture")
    if info.get("Driver") != "overlay2":
        missing.append("storage_driver")
    pairs = info.get("DriverStatus")
    quota = ["Supports project quotas", "true"]
    if type(pairs) is not list or quota not in pairs:
        missing.append("storage_quota")
    if info.get("LoggingDriver") != "json-file":
        missing.append("log_driver")
    if type(info.get("ID")) is not str or not info.get("ID"):
        missing.append("daemon_id")
    runtimes = info.get("Runtimes")
    if type(runtimes) is not dict or "nvidia" not in runtimes:
        missing.append("gpu_runtime")
    digests = image.get("RepoDigests")
    reference = f"{config.repository}@{config.image_digest}"
    if type(digests) is not list or reference not in digests:
        missing.append("image_repo_digest")
    if image.get("Id") != config.image_id:
        missing.append("image_id")
    if config.vram_mib is not None:
        missing.append("vram_quota")
    return tuple(missing)


def reconcile_decision(
    *,
    state: str,
    daemon_ok: bool,
    inspection: str,
    output_ok: bool,
) -> str:
    """Pure recovery decision. ``inspection`` is absent, error, running, exited, or foreign."""

    if not daemon_ok or inspection == "error":
        return "uncertain"
    if state == "reserved":
        if inspection == "absent":
            return "abandoned_before_create"
        return "uncertain"
    if inspection in {"absent", "foreign", "running"}:
        return "uncertain"
    if inspection == "exited" and output_ok:
        return "persist_completed"
    if inspection == "exited":
        return "persist_failed"
    return "uncertain"


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_exclusive(directory: str, name: str, payload: bytes) -> None:
    path = os.path.join(directory, name)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o400)
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode) or os.fstat(descriptor).st_uid != os.getuid():
            raise ApplicationContractError("pdl1_staging_rejected")
        remaining = payload
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise ApplicationContractError("pdl1_staging_rejected")
            remaining = remaining[written:]
        os.fchmod(descriptor, 0o400)
    finally:
        os.close(descriptor)
    check = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        found = os.read(check, len(payload) + 1)
    finally:
        os.close(check)
    if found != payload:
        raise ApplicationContractError("pdl1_staging_rejected")


def stage_verified_inputs(
    verified: Pdl1VerifiedInputs,
    root: str,
    run_id: str,
    attempt: int,
) -> str:
    """Copy captured bytes into a new directory. Does not reopen source paths."""

    if type(verified) is not Pdl1VerifiedInputs or type(root) is not str:
        raise ApplicationContractError("pdl1_staging_rejected")
    if not os.path.isabs(root) or "\0" in root or "/" in run_id or run_id.startswith("."):
        raise ApplicationContractError("pdl1_staging_rejected")
    try:
        info = os.lstat(root)
    except OSError:
        raise ApplicationContractError("pdl1_staging_rejected") from None
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
        raise ApplicationContractError("pdl1_staging_rejected")
    attempt_dir = os.path.join(root, f"{run_id}-{attempt}")
    inputs = os.path.join(attempt_dir, "inputs")
    try:
        os.mkdir(attempt_dir, 0o700)
        os.mkdir(inputs, 0o700)
    except OSError:
        raise ApplicationContractError("pdl1_staging_rejected") from None
    payloads = {
        "target_structure": verified.target_structure,
        "settings": verified.settings,
        "advanced_profile_canonical": verified.advanced_profile_canonical,
    }
    expected = {
        "target_structure": verified.target_structure_sha256,
        "settings": verified.settings_sha256,
        "advanced_profile_canonical": verified.advanced_profile_sha256,
    }
    try:
        for field, filename in _INPUT_FILES:
            payload = payloads[field]
            if _digest(payload) != expected[field]:
                raise ApplicationContractError("pdl1_staging_rejected")
            _write_exclusive(inputs, filename, payload)
    except ApplicationContractError:
        raise
    except OSError:
        raise ApplicationContractError("pdl1_staging_rejected") from None
    return inputs


def output_bytes_ok(payload: object, limit: int) -> bool:
    """BindCraft ``final_design_stats.csv`` contract.

    Required columns are ``Design`` and ``Sequence``, matching
    ``BindCraftParser``. At least one nonempty record is required.
    ``Sequence`` must be amino-acid letters. The limit is the external
    scientific-output ceiling, which is separate from the 65536-byte
    public execution-record artifact.
    """

    if type(payload) is not bytes or type(limit) is not int or isinstance(limit, bool):
        return False
    if not 1 <= len(payload) <= limit or b"\0" in payload:
        return False
    try:
        text = payload.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text)))
    except (UnicodeDecodeError, csv.Error):
        return False
    if len(rows) < 2 or len(rows) > 65:
        return False
    header = rows[0]
    if (
        not header
        or any(type(name) is not str or not name or name.strip() != name for name in header)
        or len(header) != len(set(header))
        or header.count("Design") != 1
        or header.count("Sequence") != 1
    ):
        return False
    design_at = header.index("Design")
    sequence_at = header.index("Sequence")
    width = len(header)
    for row in rows[1:]:
        if len(row) != width:
            return False
        design = row[design_at].strip()
        sequence = "".join(row[sequence_at].split()).upper()
        if not design or any(char in design for char in ",\r\n"):
            return False
        if not sequence or any(residue not in AMINO_ACIDS for residue in sequence):
            return False
    return True


def csv_from_tar(blob: bytes, limit: int) -> bytes:
    """Uncompressed ``docker cp`` archive with one regular file.

    Members are visited one at a time. Nothing is extracted onto the host.
    Compression, extra members, links, traversal, and sparse files are
    rejected before their payload is accepted.
    """

    if type(blob) is not bytes or type(limit) is not int or isinstance(limit, bool) or limit < 1:
        raise ApplicationContractError("pdl1_output_unavailable")
    if len(blob) > limit + 20480:
        raise ApplicationContractError("pdl1_output_too_large")
    payload: bytes | None = None
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r|") as archive:
            seen = 0
            while True:
                try:
                    item = archive.next()
                except tarfile.TarError:
                    raise ApplicationContractError("pdl1_output_unavailable") from None
                if item is None:
                    break
                seen += 1
                if seen > 1 or not _tar_member_allowed(item, limit):
                    raise ApplicationContractError("pdl1_output_unavailable")
                extracted = archive.extractfile(item)
                if extracted is None:
                    raise ApplicationContractError("pdl1_output_unavailable")
                payload = extracted.read(limit + 1)
                if len(payload) > limit:
                    raise ApplicationContractError("pdl1_output_too_large")
            if seen != 1 or payload is None:
                raise ApplicationContractError("pdl1_output_unavailable")
    except tarfile.TarError:
        raise ApplicationContractError("pdl1_output_unavailable") from None
    return payload


def _tar_member_allowed(item: tarfile.TarInfo, limit: int) -> bool:
    name = item.name.replace("\\", "/")
    sparse = getattr(item, "sparse", None)
    headers = getattr(item, "pax_headers", None) or {}
    return not (
        item.type not in _REGULAR_TYPES
        or item.issym()
        or item.islnk()
        or item.isdev()
        or item.isdir()
        or item.isfifo()
        or not item.isfile()
        or sparse
        or len(name) > 128
        or name.startswith("/")
        or ".." in name.split("/")
        or name.split("/")[-1] != "design_records.csv"
        or type(item.size) is not int
        or item.size < 0
        or item.size > limit
        or len(headers) > 8
        or any(len(str(key)) > 64 or len(str(value)) > 256 for key, value in headers.items())
    )


@dataclass(frozen=True)
class ContainerPolicy:
    """The container contract used both to create and to reconcile."""

    image_reference: str
    image_id: str
    entrypoint: str
    argv: tuple[str, ...]
    inputs: str
    cpu_count: int
    memory_mib: int
    storage_bytes: int
    log_bytes: int
    gpu_device: str


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    image: str
    image_id: str
    entrypoint: str
    argv: tuple[str, ...]
    inputs: str
    cpu_count: int
    memory_mib: int
    storage_bytes: int
    log_bytes: int
    gpu_device: str
    labels: tuple[tuple[str, str], ...]

    def policy(self) -> ContainerPolicy:
        return ContainerPolicy(
            image_reference=self.image,
            image_id=self.image_id,
            entrypoint=self.entrypoint,
            argv=self.argv,
            inputs=self.inputs,
            cpu_count=self.cpu_count,
            memory_mib=self.memory_mib,
            storage_bytes=self.storage_bytes,
            log_bytes=self.log_bytes,
            gpu_device=self.gpu_device,
        )

    def command(self) -> list[str]:
        command = [
            "docker",
            "create",
            "--name",
            self.name,
            "--network",
            "none",
            "--restart",
            "no",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            str(self.cpu_count),
            "--memory",
            f"{self.memory_mib}m",
            "--storage-opt",
            f"size={self.storage_bytes}",
            "--log-driver",
            "json-file",
            "--log-opt",
            f"max-size={self.log_bytes}",
            "--log-opt",
            "max-file=1",
            "--gpus",
            f"device={self.gpu_device}",
            "--mount",
            f"type=bind,source={self.inputs},target={_CONTAINER_INPUT},readonly",
            "--entrypoint",
            self.entrypoint,
        ]
        for key, value in self.labels:
            command.extend(["--label", f"{key}={value}"])
        command.append(self.image)
        command.extend(self.argv)
        if "--privileged" in command or "docker.sock" in " ".join(command):
            raise ApplicationContractError("pdl1_container_policy_rejected")
        return command


class CliDockerControl:
    """Production control. The default runner does not call Docker.

    Command output is read only up to ``max_bytes``. A longer stream is
    rejected. It is not buffered and then truncated into a success.
    """

    def __init__(
        self,
        runner: Callable[..., bytes] | None = None,
        *,
        docker_host: str,
        docker_root: str,
    ) -> None:
        self.docker_host = require_docker_endpoint(docker_host, docker_root)
        self._run = (
            runner
            if runner is not None
            else ProductionCommandRunner(docker_host=docker_host, docker_root=docker_root)
        )
        self.commands: list[list[str]] = []

    def _call(self, argv: list[str], *, max_bytes: int) -> bytes:
        if argv[0] != "docker" or "--privileged" in argv or max_bytes < 1:
            raise ApplicationContractError("pdl1_container_policy_rejected")
        bound = explicit_docker_argv(self.docker_host, argv)
        self.commands.append(list(bound))
        payload = self._run(bound, max_bytes=max_bytes)
        if type(payload) is not bytes or len(payload) > max_bytes:
            raise ApplicationContractError("pdl1_output_too_large")
        return payload

    def create(self, spec: ContainerSpec) -> str:
        identifier = self._call(spec.command(), max_bytes=256).decode("utf-8").strip()
        if not identifier or "\n" in identifier or "/" in identifier:
            raise ApplicationContractError("pdl1_container_create_uncertain")
        return identifier

    def start(self, container_id: str) -> None:
        self._call(["docker", "start", container_id], max_bytes=256)

    def stop(self, container_id: str) -> None:
        self._call(["docker", "stop", "--time", "1", container_id], max_bytes=256)

    def kill(self, container_id: str) -> None:
        self._call(["docker", "kill", container_id], max_bytes=256)

    def inspect(self, container_id: str) -> object:
        return _parse_inspect(
            self._call(["docker", "inspect", container_id], max_bytes=1_048_576).decode("utf-8")
        )

    def inspect_name(self, name: str) -> object:
        return _parse_inspect(
            self._call(["docker", "inspect", name], max_bytes=1_048_576).decode("utf-8")
        )

    def logs(self, container_id: str, limit: int) -> bytes:
        return self._call(
            ["docker", "logs", "--tail", "1", container_id],
            max_bytes=limit,
        )

    def copy_out(self, container_id: str, limit: int) -> bytes:
        blob = self._call(
            ["docker", "cp", f"{container_id}:{_CONTAINER_OUTPUT}", "-"],
            max_bytes=limit + 20480,
        )
        return csv_from_tar(blob, limit)

    def daemon_identity(self) -> str | None:
        text = (
            self._call(
                ["docker", "info", "--format", "{{.ID}}"],
                max_bytes=128,
            )
            .decode("utf-8")
            .strip()
        )
        if not text or "\n" in text or "/" in text:
            return None
        return text

    def daemon_info(self) -> object:
        return json.loads(
            self._call(["docker", "info", "--format", "{{json .}}"], max_bytes=1_048_576)
        )

    def image_inspect(self, reference: str) -> object:
        parsed = _parse_inspect(
            self._call(
                ["docker", "image", "inspect", reference],
                max_bytes=1_048_576,
            ).decode("utf-8")
        )
        return parsed


class ScriptedDockerControl:
    """Recording double. It does not talk to a daemon."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.created = "cid-scripted"
        self.fail_create = False
        self.inspect_result: object = {
            "State": {"Status": "exited", "ExitCode": 0, "Running": False},
            "Config": {"Labels": {}},
            "HostConfig": {"Privileged": False},
        }
        self.logs_payload = b""
        self.output_payload = b"Design,Sequence\nexample,ACDEFGHIK\n"
        self.daemon_id = "daemon-1"
        self.daemon_error = False
        self.name_result: object = None
        self._labels: dict[str, str] = {}
        self.image = ""
        self.memory_bytes: int | None = None
        self.nano_cpus: int | None = None
        self.info: object = {}
        self.image_record: object = {}
        self.policy: ContainerPolicy | None = None

    def create(self, spec: ContainerSpec) -> str:
        command = spec.command()
        self.calls.append(("create", tuple(command)))
        self._labels = dict(spec.labels)
        self.image = spec.image
        self.memory_bytes = spec.memory_mib * 1024 * 1024
        self.nano_cpus = spec.cpu_count * 1_000_000_000
        self.policy = spec.policy()
        if self.fail_create:
            raise ApplicationContractError("pdl1_container_create_uncertain")
        return self.created

    def start(self, container_id: str) -> None:
        self.calls.append(("start", container_id))

    def stop(self, container_id: str) -> None:
        self.calls.append(("stop", container_id))

    def kill(self, container_id: str) -> None:
        self.calls.append(("kill", container_id))

    def inspect(self, container_id: str) -> object:
        self.calls.append(("inspect", container_id))
        return self._echo(self.inspect_result, container_id)

    def inspect_name(self, name: str) -> object:
        self.calls.append(("inspect_name", name))
        return self.name_result

    def logs(self, container_id: str, limit: int) -> bytes:
        self.calls.append(("logs", container_id, limit))
        return self.logs_payload

    def copy_out(self, container_id: str, limit: int) -> bytes:
        self.calls.append(("copy_out", container_id, limit))
        if len(self.output_payload) > limit:
            raise ApplicationContractError("pdl1_output_too_large")
        return self.output_payload

    def daemon_info(self) -> object:
        self.calls.append(("daemon_info",))
        return self.info

    def image_inspect(self, reference: str) -> object:
        self.calls.append(("image_inspect", reference))
        return self.image_record

    def _echo(self, payload: object, container_id: str | None = None) -> object:
        if type(payload) is not dict:
            return payload
        config = dict(payload.get("Config") or {})
        labels = dict(config.get("Labels") or {})
        if not labels:
            labels = dict(self._labels)
        config["Labels"] = labels
        if "Image" not in config and self.image:
            config["Image"] = self.image
        if "Entrypoint" not in config and self.policy is not None:
            config["Entrypoint"] = [self.policy.entrypoint]
        if "Cmd" not in config and self.policy is not None:
            config["Cmd"] = list(self.policy.argv)
        host = dict(payload.get("HostConfig") or {})
        if self.policy is not None:
            host = {**_host_policy(self.policy), **host}
        elif "Privileged" not in host:
            host["Privileged"] = False
        echoed = {**_recorded_identity(self.policy, container_id), **payload}
        echoed["Config"] = config
        echoed["HostConfig"] = host
        if "Mounts" not in echoed and self.policy is not None:
            echoed["Mounts"] = _mount_policy(self.policy)
        return echoed

    def daemon_identity(self) -> str | None:
        self.calls.append(("daemon",))
        if self.daemon_error:
            return None
        return self.daemon_id


def _labels(attempt: ExternalAttempt) -> tuple[tuple[str, str], ...]:
    return (
        ("cellertz.deployment", attempt.deployment_id),
        ("cellertz.run", attempt.run_id),
        ("cellertz.attempt", str(attempt.attempt)),
        ("cellertz.authorization", attempt.authorization_id),
        ("cellertz.binding", attempt.binding_hash),
        ("cellertz.daemon", attempt.daemon_id),
    )


def _host_policy(policy: ContainerPolicy) -> dict[str, object]:
    return {
        "Privileged": False,
        "NetworkMode": "none",
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        "SecurityOpt": ["no-new-privileges"],
        "StorageOpt": {"size": str(policy.storage_bytes)},
        "LogConfig": {
            "Type": "json-file",
            "Config": {"max-file": "1", "max-size": str(policy.log_bytes)},
        },
        "Memory": policy.memory_mib * 1024 * 1024,
        "NanoCpus": policy.cpu_count * 1_000_000_000,
        "DeviceRequests": [{"Capabilities": [["gpu"]], "DeviceIDs": [policy.gpu_device]}],
    }


def _mount_policy(policy: ContainerPolicy) -> list[dict[str, object]]:
    return [
        {
            "Type": "bind",
            "Source": policy.inputs,
            "Destination": _CONTAINER_INPUT,
            "RW": False,
        }
    ]


def _recorded_identity(
    policy: ContainerPolicy | None,
    container_id: str | None,
) -> dict[str, object]:
    identity: dict[str, object] = {}
    if container_id:
        identity["Id"] = container_id
    if policy is not None:
        identity["Image"] = policy.image_id
    return identity


def _inspection_kind(
    payload: object,
    attempt: ExternalAttempt,
    *,
    policy: ContainerPolicy | None = None,
) -> str:
    if payload is None:
        return "absent"
    if type(payload) is not dict or policy is None:
        return "error"
    state = payload.get("State")
    config = payload.get("Config")
    labels = config.get("Labels") if type(config) is dict else None
    host = payload.get("HostConfig")
    if type(state) is not dict or type(labels) is not dict or type(host) is not dict:
        return "error"
    if not _container_matches(payload, config, host, policy):
        return "foreign"
    if attempt.container_id is not None and payload.get("Id") != attempt.container_id:
        return "foreign"
    expected = dict(_labels(attempt))
    if any(labels.get(key) != value for key, value in expected.items()):
        return "foreign"
    if state.get("Running") is True or state.get("Status") == "running":
        return "running"
    if state.get("Status") == "exited":
        return "exited"
    return "error"


def _container_matches(
    payload: Mapping[str, Any],
    config: object,
    host: Mapping[str, Any],
    policy: ContainerPolicy,
) -> bool:
    if type(config) is not dict:
        return False
    entry = config.get("Entrypoint")
    command = config.get("Cmd")
    restart = host.get("RestartPolicy")
    security = host.get("SecurityOpt")
    storage = host.get("StorageOpt")
    logging = host.get("LogConfig")
    devices = host.get("DeviceRequests")
    mounts = payload.get("Mounts")
    log_config = logging.get("Config") if type(logging) is dict else None
    return not (
        payload.get("Image") != policy.image_id
        or config.get("Image") != policy.image_reference
        or entry != [policy.entrypoint]
        or command != list(policy.argv)
        or host.get("Privileged") is not False
        or host.get("NetworkMode") != "none"
        or type(restart) is not dict
        or restart.get("Name") != "no"
        or security != ["no-new-privileges"]
        or type(storage) is not dict
        or storage.get("size") != str(policy.storage_bytes)
        or type(logging) is not dict
        or logging.get("Type") != "json-file"
        or type(log_config) is not dict
        or log_config.get("max-size") != str(policy.log_bytes)
        or log_config.get("max-file") != "1"
        or host.get("Memory") != policy.memory_mib * 1024 * 1024
        or host.get("NanoCpus") != policy.cpu_count * 1_000_000_000
        or not _gpu_matches(devices, policy.gpu_device)
        or not _mounts_match(mounts, policy)
    )


def _gpu_matches(devices: object, gpu_device: str) -> bool:
    if type(devices) is not list or len(devices) != 1 or type(devices[0]) is not dict:
        return False
    request = devices[0]
    return request.get("DeviceIDs") == [gpu_device] and request.get("Capabilities") == [["gpu"]]


def _mounts_match(mounts: object, policy: ContainerPolicy) -> bool:
    if type(mounts) is not list or len(mounts) != 1 or type(mounts[0]) is not dict:
        return False
    mount = mounts[0]
    return (
        mount.get("Type") == "bind"
        and mount.get("Source") == policy.inputs
        and mount.get("Destination") == _CONTAINER_INPUT
        and mount.get("RW") is False
    )


def _exit_code(payload: object) -> int | None:
    if type(payload) is not dict or type(payload.get("State")) is not dict:
        return None
    code = payload["State"].get("ExitCode")
    if type(code) is not int or isinstance(code, bool):
        return None
    return code


class _Clock:
    def __init__(self) -> None:
        self.now = time.monotonic
        self.sleep = time.sleep
        self.wall = time.time


_SCIENTIFIC_ARGV = (
    "--n-designs",
    "--length-min",
    "--length-max",
    "--cellertz-seed",
    "--filters",
)


def container_arguments_agree(request: ProteinDesignRequest, argv: object) -> bool:
    """Compare container argv with the approved revision. Manifest equality is not approval.

    The command is exactly the five adapter flags. Candidate count is 1
    through 64. Each length is 1 through 2147483647, and the minimum is
    at most the maximum. Seed is 0 through 4294967295. ``no_filters``
    maps only to ``CONTAINER_NO_FILTERS``. A missing or out-of-range
    value is not invented. The result of a scripted control is simulated.
    """

    flags = _adapter_flags(argv)
    if flags is None or set(flags) != set(_SCIENTIFIC_ARGV):
        return False
    constraints = dict(request.constraints)
    low = constraints.get("length_min")
    high = constraints.get("length_max")
    seed = constraints.get("seed")
    if (
        not _adapter_int(request.n_candidates, DESIGN_COUNT_MIN, DESIGN_COUNT_MAX)
        or not _adapter_int(low, LENGTH_MIN, LENGTH_MAX)
        or not _adapter_int(high, LENGTH_MIN, LENGTH_MAX)
        or not _adapter_int(seed, SEED_MIN, SEED_MAX)
        or low > high
        or request.filter_policy not in FILTER_POLICIES
    ):
        return False
    return (
        flags["--n-designs"] == str(request.n_candidates)
        and flags["--length-min"] == str(low)
        and flags["--length-max"] == str(high)
        and flags["--cellertz-seed"] == str(seed)
        and flags["--filters"] == CONTAINER_NO_FILTERS
    )


def _adapter_int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _adapter_flags(argv: object) -> dict[str, str] | None:
    if type(argv) not in {tuple, list} or any(type(item) is not str for item in argv):
        return None
    flags: dict[str, str] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token not in _SCIENTIFIC_ARGV or token in flags or index + 1 >= len(argv):
            return None
        value = argv[index + 1]
        if value.startswith("--") or not value:
            return None
        flags[token] = value
        index += 2
    return flags


class Pdl1DockerExecutor:
    """ScientificRunExecutor.

    Startup construction has no approved draft. Each run resolves the
    consumed authorization, the live revision, and the selected bytes.
    The protocol seam may still receive one already verified snapshot.
    """

    def __init__(
        self,
        *,
        config: DockerExecutionConfig,
        verified: Pdl1VerifiedInputs | None,
        selection: Pdl1InputSelection | None,
        request_digest: str | None,
        profile_id: str,
        control: CliDockerControl | ScriptedDockerControl,
        supervisor: object,
        store: object,
        cpu_count: int,
        memory_mib: int,
        timeout_seconds: int,
        max_output_bytes: int,
        clock: _Clock | None = None,
        authorizations: object | None = None,
        input_root: str | None = None,
        manifest_digests: tuple[str, str, str] | None = None,
        identity: ExecutionIdentity | None = None,
        approved_request: ProteinDesignRequest | None = None,
    ) -> None:
        self.config = config
        self.verified = verified
        self.selection = selection
        self.request_digest = request_digest
        self.profile_id = profile_id
        self.control = control
        self.supervisor = supervisor
        self.store = store
        self.cpu_count = cpu_count
        self.memory_mib = memory_mib
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self._clock = clock if clock is not None else _Clock()
        self.authorizations = authorizations
        self.input_root = input_root
        self.manifest_digests = manifest_digests
        self.identity = identity
        self.approved_request = approved_request
        self.prepared_launch: dict[str, object] | None = None
        self.prepared_outcome: dict[str, object] | None = None
        self._stop = threading.Event()
        self._active: str | None = None

    def shutdown(self) -> None:
        self._stop.set()
        active = self._active
        if active is None:
            return
        try:
            self.control.stop(active)
            self.control.kill(active)
            payload = self.control.inspect(active)
        except Exception:  # noqa: BLE001 — shutdown must not leak driver text
            self._mark_active_uncertain()
            return
        if self._kind(payload, self._active_attempt()) == "running":
            self._mark_active_uncertain()

    def execute(self, run: ScientificRun) -> ScientificExecutionResult:
        try:
            return self._execute(run)
        except Exception:  # noqa: BLE001 — execution must not leak driver text
            return _FAILED

    def reconcile(self, store: object, recorded_at: str) -> None:
        listed = getattr(store, "list_external", None)
        if not callable(listed):
            return
        for attempt in listed():
            self._reconcile_one(store, attempt, recorded_at)

    def _execute(self, run: ScientificRun) -> ScientificExecutionResult:
        prepared = self.prepared_launch
        if prepared is not None:
            from cellertz.application.bindcraft_input_preparation import consume_prepared_launch

            self.prepared_outcome = consume_prepared_launch(self.control, **prepared)
            return _FAILED
        resolved = self._resolve(run)
        if resolved is None:
            return _FAILED
        verified, selection, request = resolved
        if not self._eligible(run, selection, verified):
            return _FAILED
        if not container_arguments_agree(request, self.config.argv):
            return _FAILED
        name = f"cellertz-{self.config.deployment_id}-{run.run_id}-{run.attempt}"
        attempt = ExternalAttempt(
            run_id=run.run_id,
            attempt=run.attempt,
            authorization_id=run.authorization_id,
            binding_hash=run.binding_hash,
            deployment_id=self.config.deployment_id,
            daemon_id=self.control.daemon_identity() or "",
            container_name=name,
            state="reserved",
            retry_allowed=True,
        )
        if not attempt.daemon_id:
            uncertain = replace(attempt, state="uncertain", retry_allowed=False)
            self.store.reserve_external(uncertain)
            return _FAILED
        self.store.reserve_external(attempt)
        inputs = stage_verified_inputs(verified, self.config.staging_root, run.run_id, run.attempt)
        creating = replace(attempt, state="creating", retry_allowed=False)
        self.store.save_external(creating)
        spec = ContainerSpec(
            name=name,
            image=f"{self.config.repository}@{self.config.image_digest}",
            image_id=self.config.image_id,
            entrypoint=self.config.entrypoint,
            argv=self.config.argv,
            inputs=inputs,
            cpu_count=self.cpu_count,
            memory_mib=self.memory_mib,
            storage_bytes=self.config.storage_bytes,
            log_bytes=self.config.log_bytes,
            gpu_device=self.config.gpu_device,
            labels=_labels(creating),
        )
        try:
            container_id = self.control.create(spec)
        except Exception:  # noqa: BLE001 — create outcome is uncertain
            self.store.save_external(replace(creating, state="uncertain", retry_allowed=False))
            return _FAILED
        starting = replace(
            creating, state="starting", container_id=container_id, retry_allowed=False
        )
        self.store.save_external(starting)
        self._active = container_id
        self._attempt = starting
        deadline = self._clock.wall() + self.timeout_seconds
        try:
            ack = self.supervisor.acknowledge(
                container_id=container_id,
                deadline_epoch=deadline,
                daemon_id=starting.daemon_id,
                image=self.config.image_id,
            )
        except Exception:  # noqa: BLE001 — supervisor startup failure is not a start
            self._fence(starting)
            return _FAILED
        guarded = getattr(self.supervisor, "guarded_start", None)
        if callable(guarded):
            if not ack.ready or self.control.daemon_identity() != starting.daemon_id or ack.expired:
                self._confirm_not_started(container_id, starting)
                return _FAILED
            try:
                started = guarded(self.control, container_id, starting.daemon_id)
            except Exception:  # noqa: BLE001 — a late start stays inside the fence
                self._fence(starting)
                return _FAILED
            if started is not True:
                self._confirm_not_started(container_id, starting)
                return _FAILED
        elif (
            not ack.ready
            or self.control.daemon_identity() != starting.daemon_id
            or ack.expired
            or self._clock.wall() >= deadline
        ):
            self._confirm_not_started(container_id, starting)
            return _FAILED
        else:
            try:
                self.control.start(container_id)
            except Exception:  # noqa: BLE001 — start outcome is uncertain
                self.store.save_external(replace(starting, state="uncertain", retry_allowed=False))
                return _FAILED
        running = replace(starting, state="running")
        self.store.save_external(running)
        self._attempt = running
        payload = self._wait(container_id)
        logs = self.control.logs(container_id, self.config.log_bytes)
        if len(logs) > self.config.log_bytes:
            self.store.save_external(replace(running, state="uncertain", retry_allowed=False))
            return _FAILED
        kind = self._kind(payload, running)
        code = _exit_code(payload)
        copied = self.control.copy_out(container_id, self.max_output_bytes)
        accepted = kind == "exited" and code == 0 and output_bytes_ok(copied, self.max_output_bytes)
        output_hash = _digest(copied) if type(copied) is bytes else None
        finished = replace(
            running,
            state="exited" if kind == "exited" else "uncertain",
            retry_allowed=kind == "exited" and not accepted,
            output_sha256=output_hash,
        )
        if kind != "exited":
            finished = replace(finished, state="uncertain", retry_allowed=False)
        self.store.save_external(finished)
        if not accepted:
            return _FAILED
        self._retain(self.config.staging_root, run, copied)
        return ScientificExecutionResult(
            "completed",
            (ExecutorArtifact("execution_record", "application/json", _record(run)),),
        )

    def _wait(self, container_id: str) -> object:
        deadline = self._clock.now() + self.timeout_seconds
        payload: object = None
        while self._clock.now() < deadline:
            payload = self.control.inspect(container_id)
            if self._kind(payload, self._attempt) in {"exited", "error", "foreign"}:
                return payload
            if self._stop.is_set():
                break
            self._clock.sleep(0.05)
        self.control.stop(container_id)
        self.control.kill(container_id)
        return self.control.inspect(container_id)

    def _resolve(
        self, run: ScientificRun
    ) -> tuple[Pdl1VerifiedInputs, Pdl1InputSelection, ProteinDesignRequest] | None:
        if self.authorizations is None:
            if (
                self.verified is None
                or self.selection is None
                or self.request_digest is None
                or self.approved_request is None
                or run.request_digest != self.request_digest
            ):
                return None
            return self.verified, self.selection, self.approved_request
        found = self.authorizations.consumed_revision(  # type: ignore[attr-defined]
            session_id=run.session_id,
            authorization_id=run.authorization_id,
        )
        if found is None or found.selection.manifest_digest != run.manifest_digest:
            return None
        if run.request_digest != found.digest or run.request_revision != found.draft.revision:
            return None
        if (
            self.manifest_digests is not None
            and (
                found.selection.target_structure_sha256,
                found.selection.settings_sha256,
                found.selection.advanced_profile_sha256,
            )
            != self.manifest_digests
        ):
            return None
        if not self._identity_agrees(run, found.selection, found.record):
            return None
        try:
            verified = _verify_selected(found.selection, self.input_root)
        except ApplicationContractError:
            return None
        return verified, found.selection, found.request

    def _eligible(
        self,
        run: ScientificRun,
        selection: Pdl1InputSelection,
        verified: Pdl1VerifiedInputs,
    ) -> bool:
        return (
            type(run) is ScientificRun
            and run.confirmed
            and run.status == "running"
            and run.profile_id == self.profile_id
            and run.profile_id == DOCKER_PROFILE_ID
            and run.tool_name == "bindcraft"
            and run.target_scope == "PDL1"
            and run.manifest_digest == selection.manifest_digest
            and run.image_digest == self.config.image_digest
            and run.budget.get("authorizes_execution") is False
            and selection.matches_verified(verified)
        )

    def _identity_agrees(
        self,
        run: ScientificRun,
        selection: Pdl1InputSelection,
        record: object,
    ) -> bool:
        identity = self.identity
        if identity is None:
            return True
        from cellertz.application.scientific_authorization import ScientificRunAuthorization

        return (
            type(record) is ScientificRunAuthorization
            and record.manifest_digest == identity.manifest_digest
            and record.profile_id == identity.profile_id
            and record.tool_name == identity.tool_name
            and record.tool_version == identity.tool_version
            and record.runtime_name == identity.runtime_name
            and record.runtime_version == identity.runtime_version
            and record.image_digest == identity.image_digest
            and run.manifest_digest == identity.manifest_digest
            and run.profile_id == identity.profile_id
            and run.tool_name == identity.tool_name
            and run.tool_version == identity.tool_version
            and run.runtime_name == identity.runtime_name
            and run.runtime_version == identity.runtime_version
            and run.image_digest == identity.image_digest
            and run.target_scope == identity.target_scope
            and selection.manifest_digest == identity.manifest_digest
            and selection.target_structure_sha256 == identity.target_structure_sha256
            and selection.settings_sha256 == identity.settings_sha256
            and selection.advanced_profile_sha256 == identity.advanced_profile_sha256
            and self.cpu_count == identity.cpu_count
            and self.memory_mib == identity.memory_mib
            and self.timeout_seconds == identity.timeout_seconds
            and self.max_output_bytes == identity.max_output_bytes
            and self.config.image_id == identity.image_id
            and self.config.entrypoint == identity.entrypoint
            and self.config.argv == identity.argv
            and self.config.storage_bytes == identity.max_output_bytes
            and self.config.log_bytes == identity.max_output_bytes
        )

    def _completion_allowed(self, run: ScientificRun) -> bool:
        if self.identity is None or self.authorizations is None:
            return self.identity is None
        matches = getattr(self.authorizations, "binding_still_matches", None)
        revision = getattr(self.authorizations, "consumed_revision", None)
        if not callable(matches) or not callable(revision):
            return False
        if matches(session_id=run.session_id, authorization_id=run.authorization_id) is not True:
            return False
        found = revision(session_id=run.session_id, authorization_id=run.authorization_id)
        if found is None:
            return False
        return self._identity_agrees(run, found.selection, found.record)

    def _kind(self, payload: object, attempt: ExternalAttempt) -> str:
        return _inspection_kind(payload, attempt, policy=self._policy(attempt))

    def _policy(self, attempt: ExternalAttempt) -> ContainerPolicy:
        return ContainerPolicy(
            image_reference=f"{self.config.repository}@{self.config.image_digest}",
            image_id=self.config.image_id,
            entrypoint=self.config.entrypoint,
            argv=self.config.argv,
            inputs=os.path.join(
                self.config.staging_root,
                f"{attempt.run_id}-{attempt.attempt}",
                "inputs",
            ),
            cpu_count=self.cpu_count,
            memory_mib=self.memory_mib,
            storage_bytes=self.config.storage_bytes,
            log_bytes=self.config.log_bytes,
            gpu_device=self.config.gpu_device,
        )

    def _fence(self, attempt: ExternalAttempt) -> None:
        self.store.save_external(replace(attempt, state="uncertain", retry_allowed=False))

    def _confirm_not_started(self, container_id: str, attempt: ExternalAttempt) -> None:
        try:
            self.control.kill(container_id)
            payload = self.control.inspect(container_id)
        except Exception:  # noqa: BLE001 — an unconfirmed kill stays fenced
            self._fence(attempt)
            return
        if self._kind(payload, attempt) != "exited":
            self._fence(attempt)
            return
        self.store.save_external(replace(attempt, state="exited", retry_allowed=True))

    def _retain(self, root: str, run: ScientificRun, payload: bytes) -> None:
        directory = os.path.join(root, f"{run.run_id}-{run.attempt}", "retained")
        path = os.path.join(directory, "design_records.csv")
        if os.path.lexists(path):
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ApplicationContractError("pdl1_output_unavailable")
            if info.st_uid != os.getuid():
                raise ApplicationContractError("pdl1_output_unavailable")
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
            try:
                found = os.read(descriptor, len(payload) + 1)
                mode = os.fstat(descriptor).st_mode
                owner = os.fstat(descriptor).st_uid
            finally:
                os.close(descriptor)
            if not stat.S_ISREG(mode) or owner != os.getuid() or found != payload:
                raise ApplicationContractError("pdl1_output_unavailable")
            return
        os.makedirs(directory, mode=0o700, exist_ok=True)
        _write_exclusive(directory, "design_records.csv", payload)

    def _active_attempt(self) -> ExternalAttempt:
        return self._attempt

    def _mark_active_uncertain(self) -> None:
        current = getattr(self, "_attempt", None)
        if current is None:
            return
        self.store.save_external(replace(current, state="uncertain", retry_allowed=False))

    def _reconcile_one(self, store: object, attempt: ExternalAttempt, recorded_at: str) -> None:
        reader = getattr(store, "read_run_any", None)
        if not callable(reader) or type(attempt) is not ExternalAttempt:
            return
        run = reader(attempt.run_id)
        if run is None or run.status != "running" or run.attempt != attempt.attempt:
            return
        if (
            attempt.authorization_id != run.authorization_id
            or attempt.binding_hash != run.binding_hash
            or attempt.deployment_id != self.config.deployment_id
        ):
            self._fence(attempt)
            return
        recover = getattr(self.supervisor, "recover", None)
        if attempt.container_id is not None and callable(recover):
            try:
                owned = recover(attempt.container_id, attempt.daemon_id)
            except Exception:  # noqa: BLE001 — deadline recovery failure stays fenced
                self._fence(attempt)
                return
            if owned in {"absent", "uncertain", "failed"}:
                self._fence(attempt)
                return
        daemon = self.control.daemon_identity()
        daemon_ok = daemon is not None and daemon == attempt.daemon_id
        if attempt.container_id is None:
            payload = self.control.inspect_name(attempt.container_name)
        else:
            try:
                payload = self.control.inspect(attempt.container_id)
            except Exception:  # noqa: BLE001 — inspect failure is not absence
                payload = "error"
        if payload == "error":
            kind = "error"
        else:
            kind = self._kind(payload, attempt)
        output_ok = False
        copied = b""
        if kind == "exited" and _exit_code(payload) == 0 and attempt.container_id is not None:
            try:
                copied = self.control.copy_out(attempt.container_id, self.max_output_bytes)
            except Exception:  # noqa: BLE001 — unreadable output is not success
                copied = b""
            output_ok = output_bytes_ok(copied, self.max_output_bytes)
            if attempt.output_sha256 is not None and _digest(copied) != attempt.output_sha256:
                output_ok = False
        decision = reconcile_decision(
            state=attempt.state,
            daemon_ok=daemon_ok,
            inspection=kind,
            output_ok=output_ok,
        )
        if decision == "persist_failed":
            store.save_external(replace(attempt, state="exited", retry_allowed=True))
            return
        if decision == "persist_completed":
            if not self._completion_allowed(run):
                self._fence(attempt)
                return
            self._retain(self.config.staging_root, run, copied)
            completed = run.transition(
                "completed",
                recorded_at=recorded_at,
                text="Scientific run completed. Not scientific validation.",
                artifacts=_artifacts(run),
            )
            store.save_external(
                replace(attempt, state="exited", retry_allowed=False, output_sha256=_digest(copied))
            )
            store.commit_transition(
                completed,
                expected_status="running",
                expected_attempt=run.attempt,
                contents={completed.artifacts[0].artifact_id: _record(run)},
            )
            return
        if decision == "abandoned_before_create":
            return
        store.save_external(replace(attempt, state="uncertain", retry_allowed=False))


def _artifacts(run: ScientificRun) -> tuple:
    from cellertz.application.scientific_run import ScientificArtifact

    body = _record(run)
    return (
        ScientificArtifact(
            artifact_id=f"artifact_{run.run_id}_{run.attempt}_execution_record",
            run_id=run.run_id,
            session_id=run.session_id,
            attempt=run.attempt,
            kind="execution_record",
            media_type="application/json",
            sha256=hashlib.sha256(body).hexdigest(),
            size=len(body),
        ),
    )


def _record(run: ScientificRun) -> bytes:
    return _canonical(
        {
            "attempt": run.attempt,
            "authorization_id": run.authorization_id,
            "run_id": run.run_id,
            "schema": _EXECUTION_SCHEMA,
            "scientific_validation": False,
            "session_id": run.session_id,
            "status": "completed",
        }
    ).encode("utf-8")


@dataclass(frozen=True)
class DockerRegistrationBundle:
    config: DockerExecutionConfig
    info: Mapping[str, Any]
    image: Mapping[str, Any]
    verified: Pdl1VerifiedInputs
    draft: ProteinDesignRequestDraft
    request: ProteinDesignRequest
    budget: ExecutionBudget
    control: object
    supervisor: object
    store: object
    cpu_count: int
    memory_mib: int
    timeout_seconds: int
    max_output_bytes: int


def _observe(bundle: DockerRegistrationBundle) -> tuple[object, object] | None:
    info_fn = getattr(bundle.control, "daemon_info", None)
    image_fn = getattr(bundle.control, "image_inspect", None)
    if not callable(info_fn) or not callable(image_fn):
        return None
    reference = f"{bundle.config.repository}@{bundle.config.image_digest}"
    return info_fn(), image_fn(reference)


def _policy_matches(
    specification: object, bundle: DockerRegistrationBundle, selection: Pdl1InputSelection
) -> bool:
    verified = bundle.verified
    digest_names = (
        "target_structure_sha256",
        "settings_sha256",
        "advanced_profile_sha256",
    )
    verified_values = (
        verified.target_structure_sha256,
        verified.settings_sha256,
        verified.advanced_profile_sha256,
    )
    selected_values = (
        selection.target_structure_sha256,
        selection.settings_sha256,
        selection.advanced_profile_sha256,
    )
    for name, verified_digest, selected in zip(
        digest_names, verified_values, selected_values, strict=True
    ):
        if getattr(specification, name, None) != verified_digest or verified_digest != selected:
            return False
    return (
        getattr(specification, "profile_id", None) == DOCKER_PROFILE_ID
        and getattr(specification, "image_digest", None) == bundle.config.image_digest
        and getattr(specification, "manifest_digest", None) == selection.manifest_digest
        and getattr(specification, "entrypoint", None) == bundle.config.entrypoint
        and getattr(specification, "recorded_argv_template", None) == bundle.config.argv
        and getattr(specification, "max_output_bytes", None) == bundle.max_output_bytes
        and bundle.config.storage_bytes == bundle.max_output_bytes
        and bundle.config.log_bytes == bundle.max_output_bytes
        and getattr(specification, "gpu_count", None) == 1
        and getattr(specification, "cpu_count", None) == bundle.cpu_count
        and getattr(specification, "memory_mib", None) == bundle.memory_mib
        and getattr(specification, "timeout_seconds", None) == bundle.timeout_seconds
    )


def _assemble(
    specification: object,
    bundle: object,
) -> Pdl1DockerExecutor | tuple[str, None, tuple[str, ...]]:
    if type(bundle) is not DockerRegistrationBundle:
        return "pdl1_execution_configuration_missing", None, ("docker_facts",)
    observed = _observe(bundle)
    if observed is None:
        return "pdl1_execution_configuration_missing", None, ("docker_facts",)
    info, image = observed
    if (
        type(info) is not dict
        or type(image) is not dict
        or info != bundle.info
        or image != bundle.image
    ):
        return "pdl1_execution_policy_mismatch", None, ("observed_host",)
    gaps = docker_gaps(info, image, bundle.config)
    if gaps:
        return "pdl1_execution_configuration_missing", None, gaps
    from cellertz.application.pdl1_host_deadline import SystemdHostDeadline

    if type(bundle.supervisor) not in {
        DetachedDeadlineSupervisor,
        ScriptedDeadline,
        SystemdHostDeadline,
    }:
        return "pdl1_deadline_unavailable", None, ("deadline",)
    if type(bundle.control) not in {CliDockerControl, ScriptedDockerControl}:
        return "pdl1_runtime_invalid", None, ("runtime",)
    selection = bundle.draft.input_selection
    if (
        bundle.request.generator != "bindcraft"
        or bundle.request.target_id != "PDL1"
        or type(selection) is not Pdl1InputSelection
        or not selection.matches_verified(bundle.verified)
    ):
        return "pdl1_request_incompatible", None, ("request",)
    if not _policy_matches(specification, bundle, selection):
        return "pdl1_execution_policy_mismatch", None, ("execution_policy",)
    return Pdl1DockerExecutor(
        config=bundle.config,
        verified=bundle.verified,
        selection=selection,
        request_digest=approved_request_digest(bundle.draft, bundle.request, bundle.budget),
        profile_id=DOCKER_PROFILE_ID,
        control=bundle.control,
        supervisor=bundle.supervisor,
        store=bundle.store,
        cpu_count=bundle.cpu_count,
        memory_mib=bundle.memory_mib,
        timeout_seconds=bundle.timeout_seconds,
        max_output_bytes=bundle.max_output_bytes,
        approved_request=bundle.request,
    )


def build_docker_executor(
    specification: object,
    bundle: object,
) -> tuple[str, Pdl1DockerExecutor | None, tuple[str, ...]]:
    """Registration entry. A sleeper is not a host deadline.

    A ready ``SystemdHostDeadline`` is the worker-loss mechanism. Scripted
    and detached supervisors still return ``pdl1_worker_loss_deadline_unresolved``.
    """

    from cellertz.application.pdl1_host_deadline import MECHANISM, SystemdHostDeadline

    checked = _assemble(specification, bundle)
    if type(checked) is tuple:
        return checked
    supervisor = checked.supervisor
    if (
        type(supervisor) is not SystemdHostDeadline
        or getattr(supervisor, "mechanism", "") != MECHANISM
    ):
        return WORKER_LOSS_DEADLINE, None, ("host_deadline_service",)
    try:
        ready, missing = supervisor.readiness()
    except Exception:  # noqa: BLE001 — readiness text must not escape
        return "pdl1_deadline_unavailable", None, ("host_deadline_service",)
    if ready is not True or type(missing) is not tuple or missing:
        names = missing if type(missing) is tuple and missing else ("host_deadline_service",)
        return "pdl1_deadline_unavailable", None, tuple(str(item) for item in names)
    return "pdl1_executor_registered", checked, ()


def protocol_executor(specification: object, bundle: object) -> Pdl1DockerExecutor:
    """Test seam for the start/deadline protocol. Not production registration."""

    checked = _assemble(specification, bundle)
    if type(checked) is tuple:
        raise ApplicationContractError(checked[0])
    return checked


def _config_flags() -> int | None:
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")):
        return None
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC  # type: ignore[attr-defined]


def _config_pairs(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _value in pairs:
        if type(key) is not str or key in seen:
            raise ApplicationContractError("pdl1_execution_configuration_invalid")
        seen.add(key)
    return dict(pairs)


def _read_config_bytes(path: str) -> bytes:
    flags = _config_flags()
    if flags is None:
        raise ApplicationContractError("pdl1_execution_configuration_invalid")
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ApplicationContractError("pdl1_execution_configuration_invalid") from None
    rejected: ApplicationContractError | None = None
    completed = False
    try:
        try:
            mode = os.fstat(descriptor).st_mode
        except OSError:
            raise ApplicationContractError("pdl1_execution_configuration_invalid") from None
        if not stat.S_ISREG(mode):
            raise ApplicationContractError("pdl1_execution_configuration_invalid")
        chunks: list[bytes] = []
        remaining = _CONFIG_LIMIT + 1
        while remaining > 0:
            try:
                block = os.read(descriptor, min(_READ_CHUNK, remaining))
            except OSError:
                raise ApplicationContractError("pdl1_execution_configuration_invalid") from None
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        blob = b"".join(chunks)
        if not blob or len(blob) > _CONFIG_LIMIT:
            raise ApplicationContractError("pdl1_execution_configuration_invalid")
        completed = True
        return blob
    except ApplicationContractError as error:
        rejected = error
        raise
    except OSError:
        rejected = ApplicationContractError("pdl1_execution_configuration_invalid")
        raise rejected from None
    finally:
        close_failed = False
        try:
            os.close(descriptor)
        except OSError:
            close_failed = True
        if close_failed and rejected is None and completed:
            raise ApplicationContractError("pdl1_execution_configuration_invalid") from None


def load_docker_deployment(
    environ: Mapping[str, str] | None = None,
) -> DockerExecutionConfig | None:
    """Load a server-owned deployment file. Missing keys are not invented."""

    env = os.environ if environ is None else environ
    raw = env.get(CONFIG_ENV, "").strip()
    if raw == "":
        return None
    if not os.path.isabs(raw) or "\0" in raw or "\n" in raw or "\r" in raw:
        raise ApplicationContractError("pdl1_execution_configuration_invalid")
    blob = _read_config_bytes(raw)
    try:
        parsed = json.loads(blob.decode("utf-8"), object_pairs_hook=_config_pairs)
    except ApplicationContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ApplicationContractError("pdl1_execution_configuration_invalid") from None
    required = {
        "repository",
        "image_digest",
        "image_id",
        "entrypoint",
        "argv",
        "gpu_device",
        "deployment_id",
        "staging_root",
        "storage_bytes",
        "log_bytes",
        "manifest_path",
        "input_root",
        "deadline_manager",
        "deadline_state_dir",
        "deadline_interpreter",
        "docker_host",
        "docker_root",
    }
    argv = parsed.get("argv") if type(parsed) is dict else None
    numbers = (
        (parsed.get("storage_bytes") if type(parsed) is dict else None),
        (parsed.get("log_bytes") if type(parsed) is dict else None),
    )
    strings = (
        "repository",
        "image_digest",
        "image_id",
        "entrypoint",
        "gpu_device",
        "deployment_id",
        "staging_root",
        "manifest_path",
        "input_root",
        "deadline_manager",
        "deadline_state_dir",
        "deadline_interpreter",
        "docker_host",
        "docker_root",
    )
    if type(parsed) is not dict:
        raise ApplicationContractError("pdl1_execution_configuration_invalid")
    if "docker_host" not in parsed:
        raise ApplicationContractError("pdl1_docker_endpoint_required")
    if (
        set(parsed) != required
        or any(type(parsed.get(name)) is not str for name in strings)
        or type(argv) is not list
        or not argv
        or any(type(item) is not str for item in argv)
        or any(type(item) is not int or isinstance(item, bool) for item in numbers)
    ):
        raise ApplicationContractError("pdl1_execution_configuration_invalid")
    try:
        return DockerExecutionConfig(
            repository=parsed["repository"],
            image_digest=parsed["image_digest"],
            image_id=parsed["image_id"],
            entrypoint=parsed["entrypoint"],
            argv=tuple(argv),
            gpu_device=parsed["gpu_device"],
            deployment_id=parsed["deployment_id"],
            staging_root=parsed["staging_root"],
            storage_bytes=parsed["storage_bytes"],
            log_bytes=parsed["log_bytes"],
            docker_host=parsed["docker_host"],
            docker_root=parsed["docker_root"],
            manifest_path=parsed["manifest_path"],
            input_root=parsed["input_root"],
            deadline_manager=parsed["deadline_manager"],
            deadline_state_dir=parsed["deadline_state_dir"],
            deadline_interpreter=parsed["deadline_interpreter"],
        )
    except ApplicationContractError:
        raise
    except (TypeError, ValueError):
        raise ApplicationContractError("pdl1_execution_configuration_invalid") from None


def _verify_selected(selection: Pdl1InputSelection, root: str | None) -> Pdl1VerifiedInputs:
    from cellertz.application.pdl1_executor import (
        ARGV_POLICY_VERSION,
        DIRECTORY_POLICY,
        ENVIRONMENT_POLICY,
        Pdl1ValidatedInputIdentity,
    )
    from cellertz.application.pdl1_input_verification import (
        MAX_INPUT_BYTES,
        Pdl1InputLimits,
        Pdl1InputLocations,
        verify_pdl1_inputs,
    )

    if type(root) is not str or not os.path.isabs(root):
        raise ApplicationContractError("pdl1_input_location_invalid")
    identity = Pdl1ValidatedInputIdentity(
        manifest_digest=selection.manifest_digest,
        target_structure_sha256=selection.target_structure_sha256,
        settings_sha256=selection.settings_sha256,
        advanced_profile_sha256=selection.advanced_profile_sha256,
        argv_policy_version=ARGV_POLICY_VERSION,
        directory_policy=DIRECTORY_POLICY,
        environment_policy=ENVIRONMENT_POLICY,
    )
    locations = Pdl1InputLocations(
        os.path.join(root, selection.target_structure_sha256),
        os.path.join(root, selection.settings_sha256),
        os.path.join(root, selection.advanced_profile_sha256),
    )
    limit = Pdl1InputLimits(MAX_INPUT_BYTES, MAX_INPUT_BYTES, MAX_INPUT_BYTES, MAX_INPUT_BYTES)
    return verify_pdl1_inputs(identity, locations, limit)


@dataclass(frozen=True)
class ExecutionIdentity:
    """Immutable execution identity shared by readiness, authorization, and the run."""

    manifest_digest: str
    profile_id: str
    tool_name: str
    tool_version: str
    runtime_name: str
    runtime_version: str
    image_digest: str
    image_id: str
    entrypoint: str
    argv: tuple[str, ...]
    target_structure_sha256: str
    settings_sha256: str
    advanced_profile_sha256: str
    target_scope: str
    cpu_count: int
    memory_mib: int
    gpu_count: int
    timeout_seconds: int
    max_output_bytes: int


@dataclass(frozen=True)
class EnabledInfrastructure:
    """Server infrastructure. It does not contain an approval or a draft."""

    config: DockerExecutionConfig
    control: object
    deadline: object
    input_root: str
    manifest_digest: str
    manifest_digests: tuple[str, str, str]
    cpu_count: int
    memory_mib: int
    timeout_seconds: int
    max_output_bytes: int
    identity: ExecutionIdentity
    observation: InfrastructureReadinessProbe

    def bind(self, *, authorizations: object, store: object) -> Pdl1DockerExecutor:
        """Attach trusted services. This does not authorize a run."""

        return Pdl1DockerExecutor(
            config=self.config,
            verified=None,
            selection=None,
            request_digest=None,
            profile_id=DOCKER_PROFILE_ID,
            control=self.control,  # type: ignore[arg-type]
            supervisor=self.deadline,
            store=store,
            cpu_count=self.cpu_count,
            memory_mib=self.memory_mib,
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            authorizations=authorizations,
            input_root=self.input_root,
            manifest_digests=self.manifest_digests,
            identity=self.identity,
        )

    def readiness(self, session_id: str):
        """Observe the same parsed manifest and host facts used for execution."""

        from cellertz.application.scientific_run_config import ScientificRunConfigurationError
        from cellertz.application.scientific_runner_readiness import (
            load_scientific_runner_readiness,
        )
        from cellertz.application.scientific_runner_readiness_config import (
            BACKEND_ENV,
            MANIFEST_ENV,
        )

        if (
            os.environ.get(BACKEND_ENV) != "manifest"
            or os.environ.get(MANIFEST_ENV) != self.config.manifest_path
        ):
            raise ScientificRunConfigurationError(
                "scientific_run_executor_unconfigured",
                missing=("readiness_manifest",),
            )
        return load_scientific_runner_readiness(session_id=session_id, probe=self.observation)


class InfrastructureReadinessProbe:
    """Passing readiness requires the observed image, not the config file alone."""

    def __init__(
        self,
        identity: ExecutionIdentity,
        info: dict[str, Any],
        image: dict[str, Any],
        config: DockerExecutionConfig,
    ) -> None:
        self.identity = identity
        self.info = info
        self.image = image
        self.config = config

    def probe(self):
        from cellertz.application.scientific_runner_readiness import ProbeObservation

        reference = f"{self.config.repository}@{self.config.image_digest}"
        digests = self.image.get("RepoDigests")
        if (
            docker_gaps(self.info, self.image, self.config)
            or self.image.get("Id") != self.identity.image_id
            or type(digests) is not list
            or reference not in digests
        ):
            return ProbeObservation("failed", None)
        return ProbeObservation(
            "passed",
            True,
            manifest_digest=self.identity.manifest_digest,
            profile_id=self.identity.profile_id,
            tool_name=self.identity.tool_name,
            tool_version=self.identity.tool_version,
            runtime_name=self.identity.runtime_name,
            runtime_version=self.identity.runtime_version,
            image_digest=self.identity.image_digest,
            target_scope=self.identity.target_scope,
        )


def prepare_enabled_infrastructure(
    environ: Mapping[str, str] | None = None,
    *,
    control: object | None = None,
    deadline: object | None = None,
) -> EnabledInfrastructure:
    """Validate server-owned infrastructure and observe the configured host.

    No scientist draft, approval, or readiness snapshot is created here.
    Disabled startup must not call this function. Injected boundaries are
    for tests; production uses the command runner and systemd.
    """

    from cellertz.application.pdl1_host_deadline import SystemdHostDeadline
    from cellertz.application.scientific_run_config import ScientificRunConfigurationError
    from cellertz.application.scientific_runner_readiness import (
        _ManifestRejected,
        _parse_manifest,
        _read_manifest,
    )

    try:
        deployment = load_docker_deployment(environ)
    except ApplicationContractError as error:
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=(error.code,),
        ) from None
    if deployment is None:
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=("deployment",),
        )
    try:
        document, digest = _read_manifest(deployment.manifest_path or "")
        manifest = _parse_manifest(document, digest)
    except (_ManifestRejected, OSError, ValueError):
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=("manifest",),
        ) from None
    identity = manifest.input_identity
    envelope = manifest.envelope
    if (
        manifest.profile_id != DOCKER_PROFILE_ID
        or manifest.image_digest != deployment.image_digest
        or manifest.entrypoint != deployment.entrypoint
        or manifest.argv_template != deployment.argv
        or manifest.max_output_bytes != deployment.storage_bytes
        or manifest.max_output_bytes != deployment.log_bytes
        or envelope is None
        or envelope.gpu_count != 1
        or type(envelope.cpu_count) is not int
        or type(envelope.memory_mib) is not int
        or type(envelope.timeout_seconds) is not int
        or identity is None
        or type(deployment.input_root) is not str
        or type(manifest.tool_name) is not str
        or type(manifest.tool_version) is not str
        or type(manifest.runtime_name) is not str
        or type(manifest.runtime_version) is not str
        or type(manifest.target_scope) is not str
        or type(deployment.image_id) is not str
    ):
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=("execution_policy",),
        )
    if not _directory_path(deployment.input_root):
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=("input_root",),
        )
    host = (
        control
        if control is not None
        else CliDockerControl(
            docker_host=deployment.docker_host,
            docker_root=deployment.docker_root,
        )
    )
    if control is None and (
        type(host) is not CliDockerControl or type(host._run) is not ProductionCommandRunner
    ):
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=("runtime",),
        )
    try:
        info = host.daemon_info()
        image = host.image_inspect(f"{deployment.repository}@{deployment.image_digest}")
    except Exception:  # noqa: BLE001 — observation text must not escape
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=("docker_facts",),
        ) from None
    gaps = docker_gaps(
        info if type(info) is dict else None,
        image if type(image) is dict else None,
        deployment,
    )
    if gaps:
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=gaps,
        )
    service = deadline
    if service is None:
        service = SystemdHostDeadline(deployment, host._run)  # type: ignore[attr-defined]
    if type(service) is not SystemdHostDeadline:
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=("host_deadline_service",),
        )
    try:
        ready, missing = service.readiness()
    except Exception:  # noqa: BLE001 — deadline text must not escape
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=("host_deadline_service",),
        ) from None
    if ready is not True:
        raise ScientificRunConfigurationError(
            "scientific_run_executor_unconfigured",
            missing=missing if type(missing) is tuple and missing else ("host_deadline_service",),
        )
    execution_identity = ExecutionIdentity(
        manifest_digest=digest,
        profile_id=manifest.profile_id or "",
        tool_name=manifest.tool_name,
        tool_version=manifest.tool_version,
        runtime_name=manifest.runtime_name,
        runtime_version=manifest.runtime_version,
        image_digest=deployment.image_digest or "",
        image_id=deployment.image_id,
        entrypoint=deployment.entrypoint or "",
        argv=deployment.argv or (),
        target_structure_sha256=identity.target_structure_sha256,
        settings_sha256=identity.settings_sha256,
        advanced_profile_sha256=identity.advanced_profile_sha256,
        target_scope=manifest.target_scope,
        cpu_count=envelope.cpu_count,
        memory_mib=envelope.memory_mib,
        gpu_count=envelope.gpu_count,
        timeout_seconds=envelope.timeout_seconds,
        max_output_bytes=manifest.max_output_bytes or deployment.storage_bytes,
    )
    return EnabledInfrastructure(
        config=deployment,
        control=host,
        deadline=service,
        input_root=deployment.input_root,
        manifest_digest=digest,
        manifest_digests=(
            identity.target_structure_sha256,
            identity.settings_sha256,
            identity.advanced_profile_sha256,
        ),
        cpu_count=envelope.cpu_count,
        memory_mib=envelope.memory_mib,
        timeout_seconds=envelope.timeout_seconds,
        max_output_bytes=manifest.max_output_bytes or deployment.storage_bytes,
        identity=execution_identity,
        observation=InfrastructureReadinessProbe(
            execution_identity,
            info if type(info) is dict else {},
            image if type(image) is dict else {},
            deployment,
        ),
    )


def _directory_path(path: str) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def enabled_execution_blocked(environ: Mapping[str, str] | None = None) -> EnabledInfrastructure:
    """Production startup when execution is enabled. Disabled mode must not call it."""

    return prepare_enabled_infrastructure(environ)


def approved_request_digest(
    draft: ProteinDesignRequestDraft,
    request: ProteinDesignRequest,
    budget: ExecutionBudget,
) -> str:
    from cellertz.application.approval import canonical_digest, revision_document

    return canonical_digest(revision_document(draft, request, budget))
