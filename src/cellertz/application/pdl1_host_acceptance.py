"""Plan Linux GPU host checks for the merged PDL1 Docker executor.

The default mode prints a plan and runs no external command. ``--execute``
observes Docker and systemd through the existing command boundary. It does
not create a container, install a timer, authorize a run, or mark the
scientific runner ready. A passing observation is not host acceptance and
is not a scientific result.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.pdl1_docker import (
    CliDockerControl,
    ContainerSpec,
    DockerExecutionConfig,
    docker_gaps,
    explicit_docker_argv,
    require_docker_endpoint,
)
from cellertz.application.pdl1_host_deadline import (
    _ENFORCER_CHECK,
    DeadlineClaim,
    SystemdHostDeadline,
    _systemctl,
)

_ACCEPTANCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")
_FIELDS = (
    "acceptance_input_dir",
    "argv",
    "cpu_count",
    "deadline_interpreter",
    "deadline_manager",
    "deadline_state_dir",
    "deployment_id",
    "docker_host",
    "docker_root",
    "entrypoint",
    "gpu_device",
    "image_digest",
    "image_id",
    "log_bytes",
    "memory_mib",
    "repository",
    "staging_root",
    "storage_bytes",
)
_LIMITATIONS = (
    "same_uid_staging_replacement",
    "readiness_manifest_follows_final_symlink",
    "design_csv_is_not_a_published_artifact",
)
_OBSERVED_LIMIT = 240
_CLEANUP = (
    "docker inspect the recorded id. docker rm --force that id only when Id, Image, "
    "and label cellertz.acceptance match this invocation. Never remove a container "
    "because its name matches."
)
_DAEMON_GAPS = frozenset(
    {
        "architecture",
        "storage_driver",
        "storage_quota",
        "log_driver",
        "daemon_id",
        "gpu_runtime",
    }
)
_IMAGE_GAPS = frozenset({"image_repo_digest", "image_id"})
_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9. ()_:+/-]{0,79}$")
_VERSION_PROPERTY = re.compile(r"^Version=[A-Za-z0-9._:+-]{1,40}$")


@dataclass(frozen=True)
class AcceptanceSpec:
    """Operator values for a benign host check. Not a scientific manifest."""

    config: DockerExecutionConfig
    cpu_count: int
    memory_mib: int
    acceptance_input_dir: str

    @property
    def container_name(self) -> str:
        return f"cellertz-accept-{self.config.deployment_id}"


@dataclass(frozen=True)
class AcceptanceCheck:
    code: str
    mode: str
    purpose: str
    argv: tuple[str, ...]
    evidence: str
    passing: str
    failure: str
    cleanup: str
    status: str = "not_run"
    observed: str = ""
    recipe: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "cleanup": self.cleanup,
            "code": self.code,
            "evidence": self.evidence,
            "failure": self.failure,
            "mode": self.mode,
            "observed": self.observed,
            "passing": self.passing,
            "purpose": self.purpose,
            "recipe": list(self.recipe),
            "status": self.status,
        }


@dataclass(frozen=True)
class AcceptanceReport:
    mode: str
    missing: tuple[str, ...]
    gaps: tuple[str, ...]
    deadline_missing: tuple[str, ...]
    checks: tuple[AcceptanceCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "checks": [check.to_dict() for check in self.checks],
            "created_resources": [],
            "deadline_missing": list(self.deadline_missing),
            "execution_authorized": False,
            "gaps": list(self.gaps),
            "host_acceptance_complete": False,
            "limitations": list(_LIMITATIONS),
            "missing": list(self.missing),
            "mode": self.mode,
            "scientific_runner_ready": False,
        }


class AcceptanceError(Exception):
    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__("pdl1_host_acceptance_incomplete")


def parse_acceptance(document: object) -> tuple[AcceptanceSpec | None, tuple[str, ...]]:
    """Validate operator fields. Absent values stay missing."""

    if type(document) is not dict:
        return None, ("acceptance_document",)
    missing: list[str] = []
    if "vram_mib" in document:
        missing.append("vram_quota")
    unexpected = sorted(set(document) - set(_FIELDS) - {"vram_mib"})
    missing.extend(f"unexpected:{name}" for name in unexpected)
    for name in _FIELDS:
        if name not in document:
            missing.append(name)
    if missing:
        return None, tuple(dict.fromkeys(missing))
    argv = document["argv"]
    numbers = ("cpu_count", "memory_mib", "storage_bytes", "log_bytes")
    strings = tuple(name for name in _FIELDS if name not in {"argv", *numbers})
    if (
        type(argv) is not list
        or not argv
        or any(type(item) is not str for item in argv)
        or any(type(document[name]) is not str for name in strings)
        or any(
            type(document[name]) is not int or isinstance(document[name], bool) for name in numbers
        )
        or not _ACCEPTANCE_ID.fullmatch(document["deployment_id"])
        or not _bounded(document["cpu_count"], 1, 64)
        or not _bounded(document["memory_mib"], 1, 262144)
        or not _absolute(document["acceptance_input_dir"])
    ):
        return None, ("acceptance_document",)
    try:
        require_docker_endpoint(document["docker_host"], document["docker_root"])
    except ApplicationContractError as error:
        return None, (error.code,)
    try:
        config = DockerExecutionConfig(
            repository=document["repository"],
            image_digest=document["image_digest"],
            image_id=document["image_id"],
            entrypoint=document["entrypoint"],
            argv=tuple(argv),
            gpu_device=document["gpu_device"],
            deployment_id=document["deployment_id"],
            staging_root=document["staging_root"],
            storage_bytes=document["storage_bytes"],
            log_bytes=document["log_bytes"],
            docker_host=document["docker_host"],
            docker_root=document["docker_root"],
            deadline_manager=document["deadline_manager"],
            deadline_state_dir=document["deadline_state_dir"],
            deadline_interpreter=document["deadline_interpreter"],
        )
    except ApplicationContractError:
        return None, ("acceptance_document",)
    return (
        AcceptanceSpec(
            config=config,
            cpu_count=document["cpu_count"],
            memory_mib=document["memory_mib"],
            acceptance_input_dir=document["acceptance_input_dir"],
        ),
        (),
    )


def plan_acceptance(document: object) -> AcceptanceReport:
    """Return the check plan. This does not call Docker or systemd."""

    spec, missing = parse_acceptance(document)
    checks = () if spec is None else _checks(spec)
    return AcceptanceReport("plan", missing, (), (), checks)


def execute_acceptance(
    document: object,
    runner: Callable[..., bytes],
    *,
    execute: bool = False,
) -> AcceptanceReport:
    """Observe host facts only when ``execute`` is true.

    Manual container, deadline, and restart checks stay ``not_run``.
    """

    if execute is not True:
        return plan_acceptance(document)
    spec, missing = parse_acceptance(document)
    if spec is None:
        raise AcceptanceError(missing)
    trace = _Trace(runner)
    control = CliDockerControl(
        trace,
        docker_host=spec.config.docker_host,
        docker_root=spec.config.docker_root,
    )
    info: object = None
    image: object = None
    try:
        info = control.daemon_info()
    except ApplicationContractError:
        info = None
    reference = f"{spec.config.repository}@{spec.config.image_digest}"
    info_argv = _docker(spec.config.docker_host, "info", "--format", "{{json .}}")
    image_argv = _docker(spec.config.docker_host, "image", "inspect", reference)
    if info is not None:
        try:
            image = control.image_inspect(reference)
        except ApplicationContractError:
            image = None
    gaps = _endpoint_gaps(trace, spec.config.docker_host) + _observation_gaps(
        info if type(info) is dict else None,
        image if type(image) is dict else None,
        spec.config,
        info_ran=info_argv in trace.results,
        image_ran=image_argv in trace.results,
    )
    deadline = SystemdHostDeadline(spec.config, trace)
    try:
        ready, deadline_missing = deadline.readiness()
        readiness_returned = True
    except ApplicationContractError:
        ready, deadline_missing = False, ("host_deadline_service",)
        readiness_returned = False
    if ready is not True and not deadline_missing:
        deadline_missing = ("host_deadline_service",)
    findings = _findings(
        spec,
        trace,
        info if type(info) is dict else None,
        image if type(image) is dict else None,
        gaps,
        tuple(deadline_missing),
        readiness_returned,
    )
    return AcceptanceReport(
        "executed",
        (),
        gaps,
        tuple(deadline_missing),
        _checks(spec, findings),
    )


def observation_argv(spec: AcceptanceSpec) -> tuple[tuple[str, ...], ...]:
    """Commands ``execute_acceptance`` may run. Create and start are absent."""

    config = spec.config
    reference = f"{config.repository}@{config.image_digest}"
    host = config.docker_host
    commands: list[tuple[str, ...]] = [
        _docker(host, "info", "--format", "{{json .}}"),
        _docker(host, "image", "inspect", reference),
        ("systemctl", "--version"),
        ("systemd-run", "--version"),
        _docker(host, "--version"),
    ]
    if config.deadline_manager == "user":
        commands.append(("loginctl", "show-user", str(os.getuid()), "-p", "Linger"))
    interpreter = config.deadline_interpreter or ""
    commands.append((interpreter, "-c", _ENFORCER_CHECK))
    commands.append(tuple(_systemctl(config.deadline_manager, "show", "--property=Version")))
    return tuple(commands)


def main(argv: list[str] | None = None) -> int:
    """Plan by default. ``--execute`` is the only host-observation opt-in."""

    args = list(sys.argv[1:] if argv is None else argv)
    execute = "--execute" in args
    paths = [item for item in args if item != "--execute"]
    if len(paths) != 1:
        report = AcceptanceReport("plan", ("acceptance_file",), (), (), ())
        sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
        return 2
    try:
        document = json.loads(_read_acceptance(paths[0]).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ApplicationContractError):
        report = AcceptanceReport("plan", ("acceptance_file",), (), (), ())
        sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
        return 2
    runner: Callable[..., bytes]
    if execute is True:
        from cellertz.application.pdl1_docker import ProductionCommandRunner

        spec, missing = parse_acceptance(document)
        if spec is None:
            report = AcceptanceReport("plan", missing, (), (), ())
            sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
            return 2
        runner = ProductionCommandRunner(
            docker_host=spec.config.docker_host,
            docker_root=spec.config.docker_root,
        )
    else:
        runner = _refuse_host
    try:
        report = execute_acceptance(document, runner, execute=execute)
    except AcceptanceError as error:
        report = AcceptanceReport("plan", error.missing, (), (), ())
        sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
        return 2
    sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
    if report.missing or report.gaps or report.deadline_missing:
        return 2
    if any(check.status == "failed" for check in report.checks):
        return 2
    return 0


def _docker(endpoint: str, *argv: str) -> tuple[str, ...]:
    return tuple(explicit_docker_argv(endpoint, ["docker", *argv]))


def _endpoint_gaps(trace: _Trace, endpoint: str) -> tuple[str, ...]:
    """Name a Docker command that did not use the configured endpoint.

    This does not issue another command against the default socket.
    """

    for argv in trace.results:
        if not argv or argv[0] != "docker":
            continue
        if len(argv) < 3 or argv[1] != "--host" or argv[2] != endpoint:
            return ("docker_endpoint_divergence",)
    return ()


def _observation_gaps(
    info: dict[str, object] | None,
    image: dict[str, object] | None,
    config: DockerExecutionConfig,
    *,
    info_ran: bool,
    image_ran: bool,
) -> tuple[str, ...]:
    """Policy gaps for each observation that produced a document.

    ``docker_gaps`` requires both documents. A missing image is paired with
    an empty document only so the daemon half can be filtered back out;
    those placeholder image names are not reported. The same applies in
    reverse. A command that ran and produced no document stays
    ``docker_facts`` without erasing the other side.
    """

    if info is not None and image is not None:
        return docker_gaps(info, image, config)
    names: list[str] = []
    if info is not None:
        names.extend(_side_gaps(docker_gaps(info, {}, config), _DAEMON_GAPS))
    elif info_ran:
        names.append("docker_facts")
    if image is not None:
        names.extend(_side_gaps(docker_gaps({}, image, config), _IMAGE_GAPS))
    elif image_ran:
        names.append("docker_facts")
    return tuple(dict.fromkeys(names))


def _side_gaps(found: tuple[str, ...], keep: frozenset[str]) -> tuple[str, ...]:
    if found == ("docker_facts",):
        return found
    return tuple(gap for gap in found if gap in keep or gap == "vram_quota")


def _refuse_host(*_args: object, **_kwargs: object) -> bytes:
    raise ApplicationContractError("pdl1_host_acceptance_not_executed")


class _Trace:
    """Records commands the wrapped runner actually executed."""

    def __init__(self, runner: Callable[..., bytes]) -> None:
        self._runner = runner
        self.results: dict[tuple[str, ...], str] = {}

    def __call__(self, argv: list[str], *, max_bytes: int, timeout: float | None = None) -> bytes:
        key = tuple(argv)
        try:
            payload = self._runner(argv, max_bytes=max_bytes, timeout=timeout)
        except ApplicationContractError:
            self.results[key] = "command_failed"
            raise
        self.results[key] = _command_fact(key, payload if type(payload) is bytes else b"")
        if type(payload) is not bytes:
            raise ApplicationContractError("pdl1_docker_command_failed")
        return payload


def _findings(
    spec: AcceptanceSpec,
    trace: _Trace,
    info: dict[str, object] | None,
    image: dict[str, object] | None,
    gaps: tuple[str, ...],
    missing: tuple[str, ...],
    readiness_returned: bool,
) -> dict[str, tuple[str, str]]:
    """Status and sanitized facts for checks whose commands ran.

    Interpreter and state-directory checks are the local probes
    ``readiness`` finishes before it can run a command. Every later
    deadline check is taken from the command trace. A missing token does
    not mark a command that was not issued as passed.
    """

    config = spec.config
    reference = f"{config.repository}@{config.image_digest}"
    info_argv = _docker(config.docker_host, "info", "--format", "{{json .}}")
    image_argv = _docker(config.docker_host, "image", "inspect", reference)
    findings: dict[str, tuple[str, str]] = {}
    if info_argv not in trace.results:
        findings["docker_daemon"] = ("not_run", "")
    elif info is None:
        findings["docker_daemon"] = ("failed", _bound(trace.results[info_argv]))
    else:
        daemon_gaps = _DAEMON_GAPS.intersection(gaps)
        findings["docker_daemon"] = (
            "failed" if daemon_gaps else "passed",
            _daemon_fact(info),
        )
    if image_argv not in trace.results:
        findings["image_identity"] = ("not_run", "")
    elif image is None:
        findings["image_identity"] = ("failed", _bound(trace.results[image_argv]))
    else:
        image_gaps = _IMAGE_GAPS.intersection(gaps)
        findings["image_identity"] = (
            "failed" if image_gaps else "passed",
            _image_fact(image, reference),
        )
    findings["deadline_interpreter"] = _local_finding(
        readiness_returned,
        "deadline_interpreter" in missing,
        "executable",
        "not_executable",
    )
    findings["state_directory"] = _local_finding(
        readiness_returned,
        "deadline_state_dir" in missing,
        "writable",
        "not_writable",
    )
    findings["systemctl_version"] = _traced(trace, ("systemctl", "--version"), _version_ok)
    findings["systemd_run_version"] = _traced(trace, ("systemd-run", "--version"), _version_ok)
    findings["docker_cli_version"] = _traced(
        trace, _docker(config.docker_host, "--version"), _version_ok
    )
    linger_argv = ("loginctl", "show-user", str(os.getuid()), "-p", "Linger")
    if config.deadline_manager == "user":
        findings["systemd_linger"] = _traced(trace, linger_argv, lambda fact: fact == "Linger=yes")
    else:
        findings["systemd_linger"] = ("not_run", "")
    interpreter = config.deadline_interpreter or ""
    findings["enforcer_import"] = _traced(
        trace,
        (interpreter, "-c", _ENFORCER_CHECK),
        lambda fact: fact == "ok",
    )
    show = tuple(_systemctl(config.deadline_manager, "show", "--property=Version"))
    findings["systemd_version_property"] = _traced(trace, show, _version_property_ok)
    return findings


def _checks(
    spec: AcceptanceSpec,
    findings: dict[str, tuple[str, str]] | None = None,
) -> tuple[AcceptanceCheck, ...]:
    config = spec.config
    name = spec.container_name
    create = tuple(
        explicit_docker_argv(
            config.docker_host,
            ContainerSpec(
                name=name,
                image=f"{config.repository}@{config.image_digest}",
                image_id=config.image_id,
                entrypoint=config.entrypoint,
                argv=config.argv,
                inputs=spec.acceptance_input_dir,
                cpu_count=spec.cpu_count,
                memory_mib=spec.memory_mib,
                storage_bytes=config.storage_bytes,
                log_bytes=config.log_bytes,
                gpu_device=config.gpu_device,
                labels=(
                    ("cellertz.acceptance", config.deployment_id),
                    ("cellertz.acceptance_check", "container_configuration"),
                ),
            ).command(),
        )
    )
    cleanup = _CLEANUP
    observe = (
        (
            "docker_daemon",
            _docker(config.docker_host, "info", "--format", "{{json .}}"),
            "Record the daemon identity and the storage, log, and GPU runtime facts.",
            "Allowlisted daemon id, OSType, Driver, project quota, logging driver, and nvidia runtime.",
            "linux, overlay2, project quotas true, json-file, a daemon id, and the nvidia runtime.",
            "Stop. Do not create a container.",
            "No resource is created.",
        ),
        (
            "image_identity",
            _docker(
                config.docker_host,
                "image",
                "inspect",
                f"{config.repository}@{config.image_digest}",
            ),
            "Compare the inspected image with the operator-recorded digest and id.",
            "Allowlisted image id and whether RepoDigests contains the operator reference.",
            "Id equals image_id, image_id differs from image_digest, and the operator reference is present.",
            "Stop. Do not retag another image into this acceptance.",
            "Do not delete the operator image.",
        ),
        (
            "deadline_interpreter",
            (),
            "Confirm the deadline interpreter is a nonempty executable file.",
            "The local executable probe result.",
            "The probe result is executable.",
            "Stop. Later deadline commands are skipped.",
            "No unit is installed.",
        ),
        (
            "state_directory",
            (),
            "Confirm the deadline state directory is writable by this service user.",
            "The local writable-directory probe result.",
            "The probe result is writable and the access file is gone.",
            "Stop. Later deadline commands are skipped.",
            "Remove only the access file this check created.",
        ),
        (
            "systemctl_version",
            ("systemctl", "--version"),
            "Confirm systemctl answers before any timer is installed.",
            "The sanitized first line of systemctl --version.",
            "The command ran and returned a version line.",
            "Stop. Do not install a timer. Later deadline commands stay not_run.",
            "No unit is installed.",
        ),
        (
            "systemd_run_version",
            ("systemd-run", "--version"),
            "Confirm systemd-run answers before any timer is installed.",
            "The sanitized first line of systemd-run --version.",
            "The command ran and returned a version line.",
            "Stop. Do not install a timer. Later deadline commands stay not_run.",
            "No unit is installed.",
        ),
        (
            "docker_cli_version",
            _docker(config.docker_host, "--version"),
            "Confirm the docker CLI answers before any timer is installed.",
            "The sanitized first line of docker --version.",
            "The command ran and returned a version line.",
            "Stop. Do not install a timer. Later deadline commands stay not_run.",
            "No unit is installed.",
        ),
        (
            "systemd_linger",
            ("loginctl", "show-user", str(os.getuid()), "-p", "Linger")
            if config.deadline_manager == "user"
            else (),
            "User-manager mode queries lingering. System mode does not.",
            "Linger=yes or Linger=no when loginctl ran.",
            "User mode ran and the line is Linger=yes. System mode stays not_run.",
            "Reject user-manager mode when the command ran and was not Linger=yes.",
            "No unit is installed.",
        ),
        (
            "enforcer_import",
            (config.deadline_interpreter or "", "-c", _ENFORCER_CHECK),
            "Import the deadline module with the configured interpreter.",
            "The import result ok, or rejected when the process output was not ok.",
            "The import command ran and returned ok.",
            "Stop. Do not treat a skipped import as success.",
            "No unit is installed.",
        ),
        (
            "systemd_version_property",
            tuple(_systemctl(config.deadline_manager, "show", "--property=Version")),
            "Read the systemd Version property.",
            "The Version= line when systemctl show ran.",
            "The command ran and the line is Version= with no whitespace in the value.",
            "Stop. Do not install a timer.",
            "No unit is installed.",
        ),
    )
    checks = [_check("observe", item, findings) for item in observe]
    checks.extend(_manual_checks(create, cleanup, name, config.deployment_id))
    return tuple(checks)


def _manual_checks(
    create: tuple[str, ...],
    cleanup: str,
    name: str,
    deployment_id: str,
) -> tuple[AcceptanceCheck, ...]:
    rows = (
        (
            "container_configuration",
            create,
            "Inspect one new container. Memory, CPU, and device fields are configuration, not enforcement.",
            "docker inspect of the recorded container id.",
            "Network none, restart no, no-new-privileges, one read-only mount, and HostConfig values matching the document.",
            "Do not start a second container.",
            _configuration_recipe(name),
        ),
        (
            "cpu_memory_enforcement",
            (),
            "Show that the CPU cap throttles a busy workload and the memory cap stops an allocation above it.",
            "cgroup throttle counters and OOMKilled or exit 137 for the recorded container id.",
            "Throttle time increases, and the over-limit allocation does not finish successfully.",
            "Stop that container. Do not treat HostConfig.Memory or NanoCpus as this result.",
            _behavior_recipe(
                "cpu",
                "an operator workload that exceeds one CPU, then one that allocates more than memory_mib",
            ),
        ),
        (
            "storage_write_limit",
            (),
            "Show that a write above storage_bytes fails in the container writable layer.",
            "The workload exit code for a bounded write of storage_bytes plus one byte outside /cellertz/input.",
            "A write within the limit can succeed and the over-limit write fails.",
            "Stop that container. StorageOpt in docker inspect is not this result.",
            _behavior_recipe(
                "storage",
                "an operator workload that writes storage_bytes plus one byte to its writable layer",
            ),
        ),
        (
            "log_retention",
            (),
            "Show that stdout beyond log_bytes is not retained.",
            "The byte length of docker logs for the recorded container id.",
            "The retained log is at most log_bytes and max-file remains 1.",
            "Stop that container. Do not copy the log payload into the acceptance report.",
            _behavior_recipe(
                "logs",
                "an operator workload that writes log_bytes plus one byte to stdout and exits",
            ),
        ),
        (
            "readonly_mount",
            (),
            "Show that the staged input mount rejects writes.",
            "A failed write to /cellertz/input and an unchanged host directory listing.",
            "The write fails and the host directory does not gain the probe name.",
            "Stop that container. Do not read file bytes from the input directory.",
            _behavior_recipe(
                "mount",
                "an operator workload that tries to create /cellertz/input/cellertz-accept-probe",
            ),
        ),
        (
            "gpu_visibility",
            (),
            "Show which GPU device the container can see. This is not a VRAM quota.",
            "One device id printed by the operator workload.",
            "The printed id equals gpu_device. No memory total is required.",
            "Stop that container. Do not record a memory quantity as success.",
            _behavior_recipe("gpu", "an operator workload that prints the visible GPU device id"),
        ),
        (
            "worker_loss_deadline",
            (),
            "Install the existing deadline, start the benign workload, and leave the driver alive until the operator terminates it.",
            "The running signal, the state file unit, ExecStart, and the container status after the deadline.",
            "ExecStart is the interpreter module enforce --state path, and the container is exited or absent after the driver is gone.",
            "Leave the claim fenced. Do not start another container.",
            _deadline_recipe(deployment_id),
        ),
        (
            "deadline_recovery",
            (),
            "Deadline recovery calls recover for the recorded ids. It does not validate the scientific executor external-attempt store, terminal commit, or retry reconciliation.",
            "The recover result and the container ids carrying this acceptance label.",
            "recover does not create a container. Scientific executor integration remains pending.",
            "Do not call the scientific executor or create a replacement container.",
            _recovery_recipe(),
        ),
    )
    return tuple(
        _check(
            "manual",
            (code, argv, purpose, evidence, passing, failure, cleanup),
            None,
            recipe,
        )
        for code, argv, purpose, evidence, passing, failure, recipe in rows
    )


def _configuration_recipe(name: str) -> tuple[str, ...]:
    return (
        f"If docker inspect {name} exits 0, stop and do not remove that container.",
        "Run the planned docker create. Record stdout as the container id.",
        "docker inspect the recorded id. Compare HostConfig with the acceptance document.",
        "HostConfig.Memory and NanoCpus are configuration evidence, not enforcement.",
        _CLEANUP,
    )


def _behavior_recipe(suffix: str, workload: str) -> tuple[str, ...]:
    name = f"cellertz-accept-<deployment_id>-{suffix}"
    return (
        f"The benign image must provide {workload}.",
        f"If docker inspect {name} exits 0, stop and do not remove that container.",
        (
            "docker create uses the same flags as container_configuration, "
            f"with name {name} and label cellertz.acceptance_check={suffix}."
        ),
        "Record the create stdout as the container id and start that id.",
        "Collect only the evidence named for this check.",
        _CLEANUP,
    )


def _deadline_recipe(deployment_id: str) -> tuple[str, ...]:
    name = f"cellertz-accept-{deployment_id}-deadline"
    return (
        "Use the acceptance deadline_state_dir. Do not use the scientific deployment state.",
        f"If docker inspect {name} exits 0, stop and do not remove that container.",
        "docker create the long-running benign argv. Record the container id and docker info ID.",
        "Call drive_acceptance_deadline with the acceptance document and those recorded ids.",
        "The driver installs SystemdHostDeadline, calls guarded_start, prints cellertz-acceptance-running, and sleeps.",
        "Terminate that driver process. Enforcement and recovery stay in other processes.",
        "After the deadline, inspect the recorded id. Exited or absent on the same image is the result.",
        "Stop only the unit named in that state file, then remove only the recorded container id.",
    )


def _recovery_recipe() -> tuple[str, ...]:
    return (
        "In another process, call SystemdHostDeadline.recover for the recorded container and daemon ids.",
        "This deadline recovery does not read the scientific external-attempt store.",
        "It does not perform terminal commit or retry reconciliation.",
        "Do not call authorization, bind, or docker create.",
        "List containers by label cellertz.acceptance and keep only ids this invocation recorded.",
        _CLEANUP,
    )


def drive_acceptance_deadline(
    document: object,
    *,
    container_id: str,
    daemon_id: str,
    deadline_epoch: float,
    runner: Callable[..., bytes] | None = None,
    deadline: object | None = None,
    control: object | None = None,
    sleep: Callable[[float], None] | None = None,
    notify: Callable[[str], None] | None = None,
) -> int:
    """Operator driver for one recorded acceptance container.

    Plan mode and ``--execute`` do not call this. It installs the existing
    deadline, starts through ``guarded_start``, signals that the container
    is running, and then stays alive so the operator can terminate it.
    Enforcement and ``recover`` belong to other processes. This does not
    authorize a scientific run.
    """

    spec, missing = parse_acceptance(document)
    if spec is None:
        raise AcceptanceError(missing)
    active = runner
    if deadline is None:
        if active is None:
            from cellertz.application.pdl1_docker import ProductionCommandRunner

            active = ProductionCommandRunner(
                docker_host=spec.config.docker_host,
                docker_root=spec.config.docker_root,
            )
        deadline = SystemdHostDeadline(spec.config, active)
    if control is None:
        if active is None:
            from cellertz.application.pdl1_docker import ProductionCommandRunner

            active = ProductionCommandRunner(
                docker_host=spec.config.docker_host,
                docker_root=spec.config.docker_root,
            )
        control = CliDockerControl(
            active,
            docker_host=spec.config.docker_host,
            docker_root=spec.config.docker_root,
        )
    claim = DeadlineClaim(
        container_id=container_id,
        daemon_id=daemon_id,
        image_id=spec.config.image_id,
        deadline_epoch=deadline_epoch,
    )
    ack = deadline.install(claim)  # type: ignore[attr-defined]
    if getattr(ack, "ready", False) is not True or getattr(ack, "expired", True) is True:
        return 2
    started = deadline.guarded_start(control, container_id, daemon_id)  # type: ignore[attr-defined]
    if started is not True:
        return 2
    line = f"cellertz-acceptance-running {container_id}"
    if notify is None:
        sys.stdout.write(line + "\n")
    else:
        notify(line)
    waiter = sleep
    if waiter is None:
        import time

        waiter = time.sleep
    while True:
        waiter(1.0)


def _check(
    mode: str,
    row: tuple[str, tuple[str, ...], str, str, str, str, str],
    findings: dict[str, tuple[str, str]] | None,
    recipe: tuple[str, ...] = (),
) -> AcceptanceCheck:
    code, argv, purpose, evidence, passing, failure, cleanup = row
    status, observed = ("not_run", "") if findings is None else findings.get(code, ("not_run", ""))
    if mode == "manual":
        status, observed = "not_run", ""
    return AcceptanceCheck(
        code,
        mode,
        purpose,
        argv,
        evidence,
        passing,
        failure,
        cleanup,
        status,
        _bound(observed),
        recipe,
    )


def _local_finding(returned: bool, failed: bool, ok_text: str, bad_text: str) -> tuple[str, str]:
    if not returned:
        return "not_run", ""
    if failed:
        return "failed", bad_text
    return "passed", ok_text


def _traced(
    trace: _Trace,
    argv: tuple[str, ...],
    accepted: Callable[[str], bool],
) -> tuple[str, str]:
    if argv not in trace.results:
        return "not_run", ""
    fact = _bound(trace.results[argv])
    return ("passed" if accepted(fact) else "failed"), fact


def _command_fact(argv: tuple[str, ...], payload: bytes) -> str:
    if argv[:3] == ("docker", "info", "--format") or argv[:3] == ("docker", "image", "inspect"):
        return "received"
    if "-c" in argv:
        return "ok" if payload.strip() in {b"", b"ok"} else "rejected"
    if argv[:2] == ("loginctl", "show-user"):
        line = _first_line(payload)
        return line if line in {"Linger=yes", "Linger=no"} else "rejected"
    if argv[:2] == ("systemctl", "show") or (
        len(argv) >= 3 and argv[0] == "systemctl" and argv[1] == "--user" and argv[2] == "show"
    ):
        line = _first_line(payload)
        return line if _VERSION_PROPERTY.fullmatch(line) else "rejected"
    line = _first_line(payload)
    return line if _VERSION_LINE.fullmatch(line) else "rejected"


def _daemon_fact(info: dict[str, object]) -> str:
    pairs = info.get("DriverStatus")
    quota = "absent"
    if type(pairs) is list:
        for pair in pairs:
            if pair == ["Supports project quotas", "true"]:
                quota = "true"
            elif pair == ["Supports project quotas", "false"]:
                quota = "false"
    runtimes = info.get("Runtimes")
    nvidia = "true" if type(runtimes) is dict and "nvidia" in runtimes else "false"
    return " ".join(
        (
            f"id={_token(info.get('ID'))}",
            f"os_type={_token(info.get('OSType'))}",
            f"driver={_token(info.get('Driver'))}",
            f"project_quotas={quota}",
            f"logging_driver={_token(info.get('LoggingDriver'))}",
            f"nvidia_runtime={nvidia}",
        )
    )


def _image_fact(image: dict[str, object], reference: str) -> str:
    image_id = image.get("Id")
    ident = image_id if type(image_id) is str and _DIGEST.fullmatch(image_id) else "rejected"
    digests = image.get("RepoDigests")
    present = "present" if type(digests) is list and reference in digests else "absent"
    return f"id={ident} repository_digest={present}"


def _token(value: object) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        return "rejected"
    return value


def _first_line(payload: bytes) -> str:
    if len(payload) > 512:
        payload = payload[:512]
    line = payload.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
    if not line:
        return "empty"
    if len(line) > 80:
        return "rejected"
    return line


def _version_ok(fact: str) -> bool:
    if fact in {"command_failed", "empty", "rejected", "unreadable", "received"}:
        return False
    return _VERSION_LINE.fullmatch(fact) is not None


def _version_property_ok(fact: str) -> bool:
    return _VERSION_PROPERTY.fullmatch(fact) is not None


def _bound(value: str) -> str:
    if len(value) <= _OBSERVED_LIMIT:
        return value
    return "rejected"


def _bounded(value: object, low: int, high: int) -> bool:
    return type(value) is int and not isinstance(value, bool) and low <= value <= high


def _absolute(value: object) -> bool:
    return type(value) is str and os.path.isabs(value) and "\0" not in value and "\n" not in value


def _read_acceptance(path: str) -> bytes:
    from cellertz.application.pdl1_docker import _read_config_bytes

    return _read_config_bytes(path)


if __name__ == "__main__":
    sys.exit(main())
