"""Linux host deadline owned by systemd, not by the application worker.

``systemd-run`` installs a transient timer in the system or user manager.
That timer remains after the worker process exits and starts
``python -m cellertz.application.pdl1_host_deadline enforce``. The enforcer
kills by container id only when ``docker info`` still reports the daemon id
recorded at installation. This module does not start systemd or Docker on
import. Tests inject the command boundary.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Self

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.pdl1_docker import explicit_docker_argv, require_docker_endpoint

MECHANISM = "systemd-transient-timer"
_ENDPOINT_ENV = "CELLERTZ_PDL1_DOCKER_HOST"
_MODULE = "cellertz.application.pdl1_host_deadline"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_READ_CHUNK = 65536
_STATE_LIMIT = 4096
_ENFORCER_CHECK = (
    "import importlib,sys; "
    "m=importlib.import_module('cellertz.application.pdl1_host_deadline'); "
    "sys.exit(0 if getattr(m,'MECHANISM',None)=='systemd-transient-timer' else 1)"
)


@dataclass(frozen=True)
class DeadlineClaim:
    container_id: str
    daemon_id: str
    image_id: str
    deadline_epoch: float


class SystemdHostDeadline:
    """Injectable systemd timer boundary. Readiness is a command result.

    A deployment file cannot set a boolean that skips this service.
    ``start_new_session`` is not this mechanism.
    """

    mechanism = MECHANISM

    def __init__(
        self,
        config: object,
        runner: Callable[..., bytes],
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._runner = runner
        self._clock = clock if clock is not None else time.time
        self._after_claim: Callable[[], None] | None = None

    def readiness(self) -> tuple[bool, tuple[str, ...]]:
        """Prove the enforcer can run. An empty file is not an interpreter."""

        missing: list[str] = []
        manager = getattr(self.config, "deadline_manager", None)
        interpreter = getattr(self.config, "deadline_interpreter", None)
        state_dir = getattr(self.config, "deadline_state_dir", None)
        if manager not in {"system", "user"}:
            missing.append("deadline_manager")
        endpoint = _configured_endpoint(self.config)
        if endpoint is None:
            missing.append("docker_host")
        if not _executable(interpreter):
            missing.append("deadline_interpreter")
        if not _writable_directory(state_dir):
            missing.append("deadline_state_dir")
        if missing:
            return False, tuple(dict.fromkeys(missing))
        if not self._command_ok(["systemctl", "--version"]):
            return False, ("systemd_manager",)
        if not self._command_ok(["systemd-run", "--version"]):
            return False, ("systemd_run",)
        if endpoint is None or not self._command_ok(
            explicit_docker_argv(endpoint, ["docker", "--version"])
        ):
            return False, ("docker_cli",)
        if manager == "user" and not self._lingering():
            return False, ("systemd_linger",)
        if not self._enforcer_imports(interpreter):
            return False, ("deadline_enforcer",)
        if self._systemd_version() is None:
            return False, ("systemd_manager",)
        return True, ()

    def acknowledge(
        self,
        *,
        container_id: str,
        deadline_epoch: float,
        daemon_id: str,
        image: str,
        docker_bin: str = "docker",
    ) -> object:
        del docker_bin
        return self.install(
            DeadlineClaim(
                container_id=container_id,
                daemon_id=daemon_id,
                image_id=image,
                deadline_epoch=deadline_epoch,
            )
        )

    def install(self, claim: DeadlineClaim) -> object:
        from cellertz.application.pdl1_docker import DeadlineAck

        if (
            not _ID.fullmatch(claim.container_id)
            or not _ID.fullmatch(claim.daemon_id)
            or not _ID.fullmatch(claim.image_id)
            or type(claim.deadline_epoch) not in {int, float}
        ):
            return DeadlineAck(ready=False, expired=False)
        if self._clock() >= claim.deadline_epoch:
            return DeadlineAck(ready=False, expired=True)
        endpoint = _configured_endpoint(self.config)
        if endpoint is None:
            return DeadlineAck(ready=False, expired=False)
        unit = _unit(claim.daemon_id, claim.container_id)
        path = os.path.join(self.config.deadline_state_dir or "", unit + ".json")
        document = {
            "container_id": claim.container_id,
            "daemon_id": claim.daemon_id,
            "deadline_epoch": claim.deadline_epoch,
            "docker_host": endpoint,
            "docker_root": getattr(self.config, "docker_root", None),
            "fence": 0,
            "image_id": claim.image_id,
            "phase": "armed",
            "state_path": path,
            "unit": unit,
        }
        try:
            _write_new(path, _canonical(document))
        except ApplicationContractError:
            return DeadlineAck(ready=False, expired=False)
        remaining = int(claim.deadline_epoch - self._clock())
        if remaining < 1:
            _rewrite_phase(path, "uncertain")
            return DeadlineAck(ready=False, expired=True)
        argv = ["systemd-run", "--collect", "--unit", unit, f"--on-active={remaining}s"]
        if self.config.deadline_manager == "user":
            argv.insert(1, "--user")
        argv.extend(
            [
                "--timer-property=AccuracySec=1s",
                f"--setenv={_ENDPOINT_ENV}={endpoint}",
                "--description",
                f"cellertz-deadline:{claim.daemon_id}:{claim.container_id}",
                *_service_argv(self.config.deadline_interpreter or "", path),
            ]
        )
        show = _systemctl(
            self.config.deadline_manager,
            "show",
            unit + ".timer",
            "--property=Id",
            "--property=ActiveState",
        )
        service_show = _systemctl(
            self.config.deadline_manager,
            "show",
            unit + ".service",
            "--property=ExecStart",
            "--property=Environment",
        )
        try:
            self._runner(argv, max_bytes=512, timeout=5)
            shown = self._runner(show, max_bytes=512, timeout=5)
            service = self._runner(service_show, max_bytes=4096, timeout=5)
        except ApplicationContractError:
            _rewrite_phase(path, "uncertain")
            return DeadlineAck(ready=False, expired=False)
        service_props = _properties(service) if type(service) is bytes else {}
        if (
            not _ack(shown, unit)
            or not _exec_matches(service_props.get("ExecStart"), document, self.config)
            or not _environment_matches(service_props.get("Environment"), endpoint)
        ):
            _rewrite_phase(path, "uncertain")
            return DeadlineAck(ready=False, expired=False)
        if self._clock() >= claim.deadline_epoch:
            _rewrite_phase(path, "failed")
            return DeadlineAck(ready=False, expired=True)
        return DeadlineAck(ready=True, expired=False)

    def guarded_start(self, control: object, container_id: str, daemon_id: str) -> bool:
        """Start only while the deadline fence still names this claim.

        The claim lock is released before the external start. Enforcement
        can invalidate the claim without waiting for that start to return.
        """

        ticket = self._claim(container_id, daemon_id)
        if ticket is None:
            return False
        if self._after_claim is not None:
            self._after_claim()
        if not self._claim_current(container_id, daemon_id, ticket):
            return False
        try:
            control.start(container_id)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — start failure must not outlive the claim
            self._fail_claim(container_id, daemon_id, ticket)
            return False
        if self._commit_running(container_id, daemon_id, ticket):
            return True
        image_id = getattr(self.config, "image_id", None)
        endpoint = _state_endpoint(_read_state(self._path(container_id, daemon_id)) or {})
        if type(image_id) is str and endpoint is not None:
            _stop_container(self._runner, container_id, image_id, endpoint)
        return False

    def recover(self, container_id: str, daemon_id: str) -> str:
        path = self._path(container_id, daemon_id)
        document = _read_state(path)
        if document is None:
            return "absent"
        if not _same_identity(document, container_id, daemon_id, self.config):
            return "uncertain"
        phase = document.get("phase")
        if phase == "failed":
            return "failed"
        if phase in {"uncertain", "enforcing"}:
            return "uncertain"
        endpoint = _state_endpoint(document)
        if endpoint is None:
            return "uncertain"
        timer = self._timer(document.get("unit"))
        if phase == "enforced":
            if timer is not None and timer.get("Result") == "failed":
                return "failed"
            if timer is not None and not _unit_matches(timer, document, self.config, endpoint):
                return "uncertain"
            return self._confirmed_stop(document, container_id, daemon_id, endpoint)
        if phase not in {"armed", "starting", "running"}:
            return "uncertain"
        if timer is None or not _unit_matches(timer, document, self.config, endpoint):
            return "uncertain"
        if timer.get("ActiveState") == "failed" or timer.get("Result") == "failed":
            _rewrite_phase(path, "failed")
            return "failed"
        if timer.get("ActiveState") not in {"waiting", "active"}:
            return "uncertain"
        deadline = document.get("deadline_epoch")
        if type(deadline) not in {int, float}:
            return "uncertain"
        if self._clock() >= deadline:
            return enforce_deadline(path, self._runner)
        if phase == "armed":
            return "armed"
        if not _daemon_matches(self._runner, daemon_id, endpoint):
            return "uncertain"
        image_id = document.get("image_id")
        if type(image_id) is not str:
            return "uncertain"
        observed = _termination(self._runner, container_id, image_id, endpoint)
        if observed in {"exited", "present"}:
            return "running"
        return "uncertain"

    def _confirmed_stop(
        self,
        document: dict[str, Any],
        container_id: str,
        daemon_id: str,
        endpoint: str,
    ) -> str:
        if not _daemon_matches(self._runner, daemon_id, endpoint):
            return "uncertain"
        image_id = document.get("image_id")
        if type(image_id) is not str:
            return "uncertain"
        observed = _termination(self._runner, container_id, image_id, endpoint)
        if observed in {"exited", "absent"}:
            return "enforced"
        if observed != "present":
            return "uncertain"
        return _stop_container(self._runner, container_id, image_id, endpoint)

    def _claim(self, container_id: str, daemon_id: str) -> int | None:
        path = self._path(container_id, daemon_id)
        try:
            guard = _exclusive(path)
        except OSError:
            return None
        with guard:
            document = _load_locked(guard.descriptor)
            if not self._startable(document, container_id, daemon_id):
                if document is not None and document.get("phase") == "armed":
                    document["phase"] = "failed"
                    _store_locked(guard.descriptor, document)
                return None
            document["phase"] = "starting"
            document["fence"] = int(document.get("fence") or 0) + 1
            _store_locked(guard.descriptor, document)
            return int(document["fence"])

    def _claim_current(self, container_id: str, daemon_id: str, ticket: int) -> bool:
        path = self._path(container_id, daemon_id)
        try:
            guard = _exclusive(path)
        except OSError:
            return False
        with guard:
            document = _load_locked(guard.descriptor)
            return (
                document is not None
                and document.get("phase") == "starting"
                and document.get("fence") == ticket
                and self._startable(document, container_id, daemon_id)
            )

    def _fail_claim(self, container_id: str, daemon_id: str, ticket: int) -> None:
        path = self._path(container_id, daemon_id)
        try:
            guard = _exclusive(path)
        except OSError:
            return
        with guard:
            document = _load_locked(guard.descriptor)
            if (
                document is not None
                and document.get("phase") == "starting"
                and document.get("fence") == ticket
            ):
                document["phase"] = "failed"
                _store_locked(guard.descriptor, document)

    def _commit_running(self, container_id: str, daemon_id: str, ticket: int) -> bool:
        path = self._path(container_id, daemon_id)
        try:
            guard = _exclusive(path)
        except OSError:
            return False
        with guard:
            document = _load_locked(guard.descriptor)
            if (
                document is None
                or document.get("phase") != "starting"
                or document.get("fence") != ticket
                or not self._startable(document, container_id, daemon_id)
            ):
                return False
            document["phase"] = "running"
            _store_locked(guard.descriptor, document)
            return True

    def _startable(
        self, document: dict[str, Any] | None, container_id: str, daemon_id: str
    ) -> bool:
        if not _same_identity(document, container_id, daemon_id, self.config):
            return False
        assert document is not None
        if document.get("phase") not in {"armed", "starting"}:
            return False
        timer = self._timer(document.get("unit"))
        endpoint = _state_endpoint(document)
        if (
            endpoint is None
            or timer is None
            or timer.get("ActiveState") not in {"waiting", "active"}
            or not _unit_matches(timer, document, self.config, endpoint)
        ):
            return False
        deadline = document.get("deadline_epoch")
        return type(deadline) in {int, float} and self._clock() < deadline

    def _command_ok(self, argv: list[str]) -> bool:
        try:
            payload = self._runner(argv, max_bytes=256, timeout=5)
        except ApplicationContractError:
            return False
        return type(payload) is bytes and bool(payload.strip())

    def _lingering(self) -> bool:
        try:
            payload = self._runner(
                ["loginctl", "show-user", str(os.getuid()), "-p", "Linger"],
                max_bytes=128,
                timeout=5,
            )
        except ApplicationContractError:
            return False
        return type(payload) is bytes and payload.strip() == b"Linger=yes"

    def _enforcer_imports(self, interpreter: str | None) -> bool:
        if type(interpreter) is not str:
            return False
        try:
            payload = self._runner(
                [interpreter, "-c", _ENFORCER_CHECK],
                max_bytes=256,
                timeout=5,
            )
        except ApplicationContractError:
            return False
        return type(payload) is bytes and payload.strip() in {b"", b"ok"}

    def _systemd_version(self) -> str | None:
        argv = _systemctl(
            getattr(self.config, "deadline_manager", None),
            "show",
            "--property=Version",
        )
        try:
            payload = self._runner(argv, max_bytes=256, timeout=5)
        except ApplicationContractError:
            return None
        if type(payload) is not bytes or not payload.startswith(b"Version="):
            return None
        version = payload.split(b"\n", 1)[0][len(b"Version=") :].decode("utf-8", "replace").strip()
        if not version or any(char.isspace() for char in version):
            return None
        return version

    def _timer(self, unit: object) -> dict[str, str] | None:
        if type(unit) is not str or not unit:
            return None
        manager = getattr(self.config, "deadline_manager", None)
        try:
            timer = self._runner(
                _systemctl(
                    manager,
                    "show",
                    unit + ".timer",
                    "--property=Id",
                    "--property=ActiveState",
                    "--property=Result",
                ),
                max_bytes=512,
                timeout=5,
            )
            service = self._runner(
                _systemctl(
                    manager,
                    "show",
                    unit + ".service",
                    "--property=ExecStart",
                    "--property=Result",
                    "--property=Environment",
                ),
                max_bytes=1024,
                timeout=5,
            )
        except ApplicationContractError:
            return None
        if type(timer) is not bytes or type(service) is not bytes:
            return None
        shown = _properties(timer)
        shown.update(_properties(service))
        if shown.get("Id") != unit + ".timer":
            return None
        return shown

    def _path(self, container_id: str, daemon_id: str) -> str:
        return os.path.join(
            self.config.deadline_state_dir or "",
            _unit(daemon_id, container_id) + ".json",
        )


def enforce_deadline(state_path: str, runner: Callable[..., bytes]) -> str:
    """Host entry. Invalidates a pending start, then kills by container id.

    Docker calls run without the claim lock, so a worker paused inside
    start cannot delay this deadline.
    """

    try:
        guard = _exclusive(state_path)
    except OSError:
        return "uncertain"
    with guard:
        document = _load_locked(guard.descriptor)
        if document is None:
            return "uncertain"
        phase = document.get("phase")
        if phase == "failed":
            return "failed"
        if phase == "uncertain":
            return "uncertain"
        container_id = document.get("container_id")
        daemon_id = document.get("daemon_id")
        image_id = document.get("image_id")
        if type(container_id) is not str or type(daemon_id) is not str or type(image_id) is not str:
            document["phase"] = "uncertain"
            _store_locked(guard.descriptor, document)
            return "uncertain"
        if phase == "enforced":
            return "enforced"
        endpoint = _state_endpoint(document)
        if endpoint is None:
            document["phase"] = "uncertain"
            _store_locked(guard.descriptor, document)
            return "uncertain"
        if not _daemon_matches(runner, daemon_id, endpoint):
            document["phase"] = "uncertain"
            _store_locked(guard.descriptor, document)
            return "uncertain"
        document["phase"] = "enforcing"
        document["fence"] = int(document.get("fence") or 0) + 1
        _store_locked(guard.descriptor, document)
    outcome = _stop_container(runner, container_id, image_id, endpoint)
    try:
        guard = _exclusive(state_path)
    except OSError:
        return "uncertain"
    with guard:
        current = _load_locked(guard.descriptor)
        if current is None:
            return "uncertain"
        if current.get("phase") == "enforced":
            return "enforced"
        current["phase"] = outcome
        _store_locked(guard.descriptor, current)
        return outcome


def run(argv: list[str], environ: Mapping[str, str] | None = None) -> int:
    """Process entry used by the transient timer.

    The endpoint comes from the persisted claim and from the unit
    environment. A later deployment file is not read.
    """

    if len(argv) != 3 or argv[0] != "enforce" or argv[1] != "--state" or not os.path.isabs(argv[2]):
        return 1
    document = _read_state(argv[2])
    endpoint = None if document is None else _state_endpoint(document)
    if document is None or endpoint is None:
        return 1
    env = os.environ if environ is None else environ
    if env.get(_ENDPOINT_ENV) != endpoint:
        return 1
    from cellertz.application.pdl1_docker import ProductionCommandRunner

    outcome = enforce_deadline(
        argv[2],
        ProductionCommandRunner(docker_host=endpoint, docker_root=str(document.get("docker_root"))),
    )
    return 0 if outcome == "enforced" else 1


def _stop_container(
    runner: Callable[..., bytes], container_id: str, image_id: str, endpoint: str
) -> str:
    try:
        runner(
            explicit_docker_argv(endpoint, ["docker", "kill", container_id]),
            max_bytes=256,
            timeout=5,
        )
    except ApplicationContractError:
        pass
    observed = _termination(runner, container_id, image_id, endpoint)
    if observed in {"exited", "absent"}:
        return "enforced"
    if observed == "uncertain":
        return "uncertain"
    try:
        runner(
            explicit_docker_argv(endpoint, ["docker", "rm", "--force", container_id]),
            max_bytes=256,
            timeout=5,
        )
    except ApplicationContractError:
        return "failed"
    observed = _termination(runner, container_id, image_id, endpoint)
    if observed in {"exited", "absent"}:
        return "enforced"
    if observed == "uncertain":
        return "uncertain"
    return "failed"


def _termination(
    runner: Callable[..., bytes], container_id: str, image_id: str, endpoint: str
) -> str:
    try:
        listed = runner(
            explicit_docker_argv(
                endpoint,
                ["docker", "ps", "-aq", "--no-trunc", "--filter", f"id={container_id}"],
            ),
            max_bytes=256,
            timeout=5,
        )
    except ApplicationContractError:
        return "uncertain"
    if type(listed) is not bytes:
        return "uncertain"
    found = listed.decode("utf-8", "replace").strip()
    if found == "":
        return "absent"
    if found != container_id:
        return "uncertain"
    try:
        payload = runner(
            explicit_docker_argv(endpoint, ["docker", "inspect", container_id]),
            max_bytes=1_048_576,
            timeout=5,
        )
    except ApplicationContractError:
        return "uncertain"
    return _classify_inspect(payload, container_id, image_id)


def _classify_inspect(payload: object, container_id: str, image_id: str) -> str:
    if type(payload) is not bytes:
        return "uncertain"
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "uncertain"
    if parsed == []:
        return "absent"
    if type(parsed) is list:
        if len(parsed) != 1:
            return "uncertain"
        parsed = parsed[0]
    if type(parsed) is not dict:
        return "uncertain"
    if parsed.get("Id") != container_id or parsed.get("Image") != image_id:
        return "uncertain"
    state = parsed.get("State")
    if type(state) is not dict:
        return "uncertain"
    if state.get("Status") == "exited" and state.get("Running") is False:
        return "exited"
    if state.get("Status") in {"created", "running", "paused"}:
        return "present"
    return "uncertain"


def _daemon_matches(runner: Callable[..., bytes], daemon_id: str, endpoint: str) -> bool:
    try:
        identity = runner(
            explicit_docker_argv(endpoint, ["docker", "info", "--format", "{{.ID}}"]),
            max_bytes=128,
            timeout=5,
        )
    except ApplicationContractError:
        return False
    return type(identity) is bytes and identity.decode("utf-8", "replace").strip() == daemon_id


class _Lock:
    def __init__(self, path: str) -> None:
        self.descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC)

    def __enter__(self) -> Self:
        fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args: object) -> bool:
        fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        os.close(self.descriptor)
        return False


def _exclusive(path: str) -> _Lock:
    return _Lock(path)


def _load_locked(descriptor: int) -> dict[str, Any] | None:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        blob = os.read(descriptor, _STATE_LIMIT + 1)
    except OSError:
        return None
    if not blob or len(blob) > _STATE_LIMIT:
        return None
    try:
        parsed = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if type(parsed) is dict else None


def _store_locked(descriptor: int, document: Mapping[str, Any]) -> None:
    payload = _canonical(document)
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    remaining = payload
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise ApplicationContractError("pdl1_deadline_unavailable")
        remaining = remaining[written:]
    os.fsync(descriptor)


def _configured_endpoint(config: object) -> str | None:
    try:
        return require_docker_endpoint(
            getattr(config, "docker_host", None),
            getattr(config, "docker_root", None),
        )
    except ApplicationContractError:
        return None


def _state_endpoint(document: Mapping[str, Any]) -> str | None:
    try:
        return require_docker_endpoint(document.get("docker_host"), document.get("docker_root"))
    except ApplicationContractError:
        return None


def _environment_matches(value: object, endpoint: str) -> bool:
    return value == f"{_ENDPOINT_ENV}={endpoint}"


def _unit_matches(
    timer: Mapping[str, str],
    document: Mapping[str, Any],
    config: object,
    endpoint: str,
) -> bool:
    return _exec_matches(timer.get("ExecStart"), document, config) and _environment_matches(
        timer.get("Environment"), endpoint
    )


def _same_identity(
    document: dict[str, Any] | None,
    container_id: str,
    daemon_id: str,
    config: object,
) -> bool:
    if document is None:
        return False
    return (
        document.get("container_id") == container_id
        and document.get("daemon_id") == daemon_id
        and document.get("image_id") == getattr(config, "image_id", None)
    )


def _service_argv(interpreter: str, state_path: str) -> tuple[str, ...]:
    return (interpreter, "-m", _MODULE, "enforce", "--state", state_path)


def _exec_matches(text: object, document: Mapping[str, Any], config: object) -> bool:
    if type(text) is not str or "systemd-run" in text or "--description" in text:
        return False
    interpreter = getattr(config, "deadline_interpreter", None)
    state_path = document.get("state_path")
    container_id = document.get("container_id")
    daemon_id = document.get("daemon_id")
    state_dir = getattr(config, "deadline_state_dir", None)
    if (
        type(interpreter) is not str
        or type(state_path) is not str
        or type(container_id) is not str
        or type(daemon_id) is not str
        or type(state_dir) is not str
        or any(char.isspace() for char in interpreter)
        or any(char.isspace() for char in state_path)
        or not os.path.isabs(interpreter)
        or not os.path.isabs(state_path)
    ):
        return False
    expected_path = os.path.join(state_dir, _unit(daemon_id, container_id) + ".json")
    if os.path.abspath(state_path) != os.path.abspath(expected_path):
        return False
    parsed = _parse_exec_argv(text)
    return parsed == _service_argv(interpreter, state_path)


def _parse_exec_argv(text: str) -> tuple[str, ...] | None:
    if "path=" not in text or "argv[]=" not in text:
        return None
    path = text.split("path=", 1)[1].split(" ; ", 1)[0]
    argv_text = text.split("argv[]=", 1)[1].split(" ; ", 1)[0]
    argv = tuple(argv_text.split(" "))
    if not argv or argv[0] != path:
        return None
    if any(item.startswith("--") and item != "--state" for item in argv):
        return None
    return argv


def _properties(payload: bytes) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in payload.decode("utf-8", "replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"Id", "ActiveState", "Result", "ExecStart", "Environment"}:
            found[key] = value
    return found


def _executable(path: str | None) -> bool:
    flags = _flags()
    if flags is None or type(path) is not str or not os.path.isabs(path):
        return False
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return False
    try:
        info = os.fstat(descriptor)
    except OSError:
        return False
    finally:
        try:
            os.close(descriptor)
        except OSError:
            return False
    return stat.S_ISREG(info.st_mode) and info.st_size > 0 and (info.st_mode & 0o111) != 0


def _writable_directory(path: str | None) -> bool:
    if not _directory(path) or type(path) is not str:
        return False
    probe = os.path.join(path, ".cellertz-deadline-access")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        if os.path.lexists(probe):
            os.unlink(probe)
        descriptor = os.open(probe, flags, 0o600)
        os.close(descriptor)
        os.unlink(probe)
    except OSError:
        return False
    return True


def _systemctl(manager: str | None, *args: str) -> list[str]:
    argv = ["systemctl"]
    if manager == "user":
        argv.append("--user")
    argv.extend(args)
    return argv


def _unit(daemon_id: str, container_id: str) -> str:
    digest = hashlib.sha256(f"{daemon_id}:{container_id}".encode()).hexdigest()[:32]
    return f"cellertz-deadline-{digest}"


def _canonical(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _ack(payload: object, unit: str) -> bool:
    if type(payload) is not bytes:
        return False
    text = payload.decode("utf-8", "replace")
    return f"Id={unit}.timer" in text.splitlines() and (
        "ActiveState=waiting" in text.splitlines() or "ActiveState=active" in text.splitlines()
    )


def _flags() -> int | None:
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")):
        return None
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC  # type: ignore[attr-defined]


def _regular_file(path: str | None) -> bool:
    flags = _flags()
    if flags is None or type(path) is not str or not os.path.isabs(path):
        return False
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return False
    try:
        mode = os.fstat(descriptor).st_mode
    except OSError:
        return False
    finally:
        try:
            os.close(descriptor)
        except OSError:
            return False
    return stat.S_ISREG(mode)


def _directory(path: str | None) -> bool:
    if type(path) is not str or not os.path.isabs(path):
        return False
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def _write_new(path: str, payload: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        raise ApplicationContractError("pdl1_deadline_unavailable") from None
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode) or os.fstat(descriptor).st_uid != os.getuid():
            raise ApplicationContractError("pdl1_deadline_unavailable")
        remaining = payload
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise ApplicationContractError("pdl1_deadline_unavailable")
            remaining = remaining[written:]
    except OSError:
        raise ApplicationContractError("pdl1_deadline_unavailable") from None
    finally:
        os.close(descriptor)


def _read_state(path: str) -> dict[str, Any] | None:
    flags = _flags()
    if flags is None:
        return None
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            return None
        chunks: list[bytes] = []
        remaining = _STATE_LIMIT + 1
        while remaining > 0:
            block = os.read(descriptor, min(_READ_CHUNK, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        blob = b"".join(chunks)
    except OSError:
        return None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            return None
    if not blob or len(blob) > _STATE_LIMIT:
        return None
    try:
        parsed = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if type(parsed) is dict else None


def _rewrite_phase(path: str, phase: str) -> None:
    document = _read_state(path)
    if document is None:
        return
    document["phase"] = phase
    payload = _canonical(document)
    flags = os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode) or os.fstat(descriptor).st_uid != os.getuid():
            return
        os.ftruncate(descriptor, 0)
        remaining = payload
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                return
            remaining = remaining[written:]
    except OSError:
        return
    finally:
        try:
            os.close(descriptor)
        except OSError:
            return


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
