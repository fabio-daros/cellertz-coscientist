"""Explicit Docker endpoint. Simulated processes only. No daemon."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.pdl1_docker import (
    CliDockerControl,
    ProductionCommandRunner,
    explicit_docker_argv,
    load_docker_deployment,
    require_docker_endpoint,
)
from cellertz.application.pdl1_host_acceptance import _endpoint_gaps
from cellertz.application.pdl1_host_deadline import _ENDPOINT_ENV, enforce_deadline, run
from cellertz.application.pdl1_image_readiness import SCHEMA, build_plan

_IMAGE = "sha256:" + ("ab" * 32)
_IMAGE_ID = "sha256:" + ("cd" * 32)


def _endpoint(root: Path) -> tuple[str, str]:
    return f"unix://{root / 'docker.sock'}", str(root)


def _deployment(root: Path, **overrides: object) -> dict[str, object]:
    host, docker_root = _endpoint(root)
    document: dict[str, object] = {
        "repository": "example.invalid/cellertz/pdl1",
        "image_digest": _IMAGE,
        "image_id": _IMAGE_ID,
        "entrypoint": "recorded-entry",
        "argv": ["--server-argv"],
        "gpu_device": "0",
        "deployment_id": "dep1",
        "staging_root": str(root / "stage"),
        "storage_bytes": 1048576,
        "log_bytes": 1048576,
        "manifest_path": str(root / "manifest.json"),
        "input_root": str(root / "inputs"),
        "deadline_manager": "system",
        "deadline_state_dir": str(root / "deadlines"),
        "deadline_interpreter": str(root / "python"),
        "docker_host": host,
        "docker_root": docker_root,
    }
    document.update(overrides)
    return document


def test_endpoint_rejects_missing_invalid_and_ambiguous_forms(tmp_path: Path) -> None:
    host, root = _endpoint(tmp_path)
    assert require_docker_endpoint(host, root) == host
    with pytest.raises(ApplicationContractError) as missing:
        require_docker_endpoint("", root)
    assert missing.value.code == "pdl1_docker_endpoint_required"
    rejected = (
        "tcp://127.0.0.1:2375",
        "ssh://operator@host/var/run/docker.sock",
        "unix://user:secret@/tmp/docker.sock",
        f"unix://{tmp_path / 'docker.sock'}?token=1",
        f"unix://{tmp_path / 'docker.sock'}#fragment",
        "unix://var/run/docker.sock",
        f"unix://{tmp_path}/../docker.sock",
        f"unix://{tmp_path}/./docker.sock",
        f"unix://{tmp_path}/nested//docker.sock",
        "unix:///var/run/docker.sock",
    )
    for value in rejected:
        with pytest.raises(ApplicationContractError) as error:
            require_docker_endpoint(value, root)
        assert error.value.code == "pdl1_docker_endpoint_rejected"


def test_deployment_without_endpoint_has_no_default(tmp_path: Path) -> None:
    from cellertz.application.pdl1_docker import CONFIG_ENV

    path = tmp_path / "deployment.json"
    document = _deployment(tmp_path)
    del document["docker_host"]
    del document["docker_root"]
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ApplicationContractError) as error:
        load_docker_deployment({CONFIG_ENV: str(path)})
    assert error.value.code == "pdl1_docker_endpoint_required"
    document["docker_host"] = "tcp://127.0.0.1:2375"
    document["docker_root"] = str(tmp_path)
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ApplicationContractError) as rejected:
        load_docker_deployment({CONFIG_ENV: str(path)})
    assert rejected.value.code == "pdl1_docker_endpoint_rejected"


def test_every_control_operation_selects_the_configured_endpoint(tmp_path: Path) -> None:
    host, root = _endpoint(tmp_path)
    seen: list[list[str]] = []

    def runner(argv: list[str], *, max_bytes: int) -> bytes:
        del max_bytes
        seen.append(list(argv))
        if "cp" in argv:
            return _tar()
        if argv[-1] == "{{json .}}":
            return b"{}"
        if "image" in argv:
            return b"[{}]"
        return b"cid-endpoint\n"

    control = CliDockerControl(runner, docker_host=host, docker_root=root)
    from cellertz.application.pdl1_docker import ContainerSpec

    spec = ContainerSpec(
        name="cellertz-endpoint",
        image=f"example.invalid/cellertz/pdl1@{_IMAGE}",
        image_id=_IMAGE_ID,
        entrypoint="recorded-entry",
        argv=("--server-argv",),
        inputs=str(tmp_path / "inputs"),
        cpu_count=1,
        memory_mib=512,
        storage_bytes=1048576,
        log_bytes=1048576,
        gpu_device="0",
        labels=(("cellertz.deployment", "dep1"),),
    )
    control.create(spec)
    control.start("cid-endpoint")
    control.stop("cid-endpoint")
    control.kill("cid-endpoint")
    control.inspect("cid-endpoint")
    control.inspect_name("cellertz-endpoint")
    control.logs("cid-endpoint", 64)
    control.copy_out("cid-endpoint", 1024)
    control.daemon_identity()
    control.daemon_info()
    control.image_inspect(f"example.invalid/cellertz/pdl1@{_IMAGE}")
    assert seen
    assert all(call[:3] == ["docker", "--host", host] for call in seen)
    assert any(call[3:5] == ["logs", "--tail"] for call in seen)
    assert any(call[3] == "cp" for call in seen)
    with pytest.raises(ApplicationContractError) as conflict:
        explicit_docker_argv(host, ["docker", "--host", "unix:///var/run/docker.sock", "info"])
    assert conflict.value.code == "pdl1_docker_endpoint_conflict"
    with pytest.raises(ApplicationContractError) as context:
        explicit_docker_argv(host, ["docker", "context", "use", "default"])
    assert context.value.code == "pdl1_docker_endpoint_conflict"


def test_runner_ignores_operator_docker_environment(tmp_path: Path, monkeypatch) -> None:
    host, root = _endpoint(tmp_path)
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "operator")
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "config"))
    seen: list[dict[str, object]] = []

    def factory(argv: list[str], **kwargs: object):
        seen.append({"argv": argv, "env": kwargs["env"]})

        class _Process:
            def __init__(self) -> None:
                self.stdout = _Stream(b"ok\n")
                self.stderr = _Stream(b"")
                self.stdin = None
                self.returncode = 0

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return 0

        return _Process()

    (tmp_path / "docker.sock").write_bytes(b"")
    runner = ProductionCommandRunner(
        process_factory=factory,
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: None,
        set_blocking=lambda _fd, _flag: None,
        docker_host=host,
        docker_root=root,
    )
    assert runner(["docker", "info", "--format", "{{.ID}}"], max_bytes=16) == b"ok\n"
    assert seen[0]["env"] == {"PATH": "/usr/bin:/bin"}
    assert seen[0]["argv"][:3] == ["docker", "--host", host]
    with pytest.raises(ApplicationContractError) as bare:
        ProductionCommandRunner(
            process_factory=factory,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
            set_blocking=lambda _fd, _flag: None,
        )(["docker", "info"], max_bytes=16)
    assert bare.value.code == "pdl1_docker_endpoint_required"

    def systemctl(argv: list[str], **kwargs: object):
        seen.append({"argv": list(argv), "env": kwargs["env"]})

        class _Done:
            stdout = _Stream(b"ok\n")
            stderr = _Stream(b"")
            stdin = None
            returncode = 0

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return 0

        return _Done()

    ProductionCommandRunner(process_factory=systemctl)(["systemctl", "--version"], max_bytes=16)
    assert seen[-1]["argv"] == ["systemctl", "--version"]
    assert seen[-1]["env"] == {"PATH": "/usr/bin:/bin"}


def test_old_deadline_state_does_not_use_the_default_socket(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "container_id": "cid-old",
                "daemon_id": "daemon-1",
                "image_id": _IMAGE_ID,
                "phase": "armed",
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def runner(argv: list[str], *, max_bytes: int, timeout: float | None = None) -> bytes:
        del max_bytes, timeout
        calls.append(list(argv))
        return b"daemon-1\n"

    assert enforce_deadline(str(path), runner) == "uncertain"
    assert calls == []
    assert run(["enforce", "--state", str(path)], environ={}) == 1
    assert calls == []


def test_enforcer_keeps_the_installed_endpoint_after_deployment_changes(tmp_path: Path) -> None:
    from dataclasses import replace

    from cellertz.application.pdl1_host_deadline import DeadlineClaim, SystemdHostDeadline

    host, _root = _endpoint(tmp_path)
    state = tmp_path / "deadlines"
    state.mkdir()
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)
    from tests.test_pdl1_docker_executor import (  # local fixture, not a public API
        _config,
        _deadline_runner,
        _executable_interpreter,
    )

    _executable_interpreter(interpreter)
    config = _config(
        tmp_path,
        deadline_manager="system",
        deadline_state_dir=str(state),
        deadline_interpreter=str(interpreter),
        image_id=_IMAGE_ID,
    )
    runner = _deadline_runner()
    now = {"t": 1_000.0}
    deadline = SystemdHostDeadline(config, runner, clock=lambda: now["t"])
    ack = deadline.install(DeadlineClaim("cid-scripted", "daemon-1", _IMAGE_ID, 2_000.0))
    assert ack.ready is True
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = f"unix://{other_root / 'docker.sock'}"
    deadline.config = replace(config, docker_host=other, docker_root=str(other_root))
    now["t"] = 3_000.0
    assert deadline.recover("cid-scripted", "daemon-1") == "enforced"
    assert runner.hosts
    assert set(runner.hosts) == {host}
    assert other not in runner.hosts
    runner.unit_endpoint_override = other
    assert deadline.recover("cid-scripted", "daemon-1") == "uncertain"


def test_real_socket_rejects_symlink_escapes_and_plan_does_not(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    outside_socket = outside / "docker.sock"
    outside_socket.write_bytes(b"")
    final = root / "docker.sock"
    final.symlink_to(outside_socket)
    ancestor = root / "alias"
    ancestor.symlink_to(outside, target_is_directory=True)
    missing = root / "missing.sock"
    contained = root / "nested"
    contained.mkdir()
    contained_socket = contained / "docker.sock"
    contained_socket.write_bytes(b"")
    inside = root / "inside.sock"
    inside.symlink_to(contained_socket)
    seen: list[list[str]] = []

    def factory(argv: list[str], **kwargs: object):
        del kwargs
        seen.append(list(argv))
        raise AssertionError("popen")

    for endpoint in (f"unix://{final}", f"unix://{ancestor / 'docker.sock'}", f"unix://{missing}"):
        seen.clear()
        runner = _runner(factory, endpoint, str(root))
        with pytest.raises(ApplicationContractError) as error:
            runner(["docker", "info"], max_bytes=16)
        assert error.value.code == "pdl1_docker_endpoint_rejected"
        assert seen == []

    def accept(argv: list[str], **kwargs: object):
        del kwargs
        seen.append(list(argv))

        class _Process:
            stdout = _Stream(b"ok\n")
            stderr = _Stream(b"")
            stdin = None
            returncode = 0

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return 0

        return _Process()

    allowed = _runner(accept, f"unix://{inside}", str(root))
    assert allowed(["docker", "info"], max_bytes=16) == b"ok\n"
    assert seen[-1][:3] == ["docker", "--host", f"unix://{inside}"]
    plan = _plan(tmp_path, f"unix://{final}", str(root))
    assert plan.argv[:3] == ("docker", "--host", f"unix://{final}")
    assert not missing.exists()
    absent = _plan(tmp_path, f"unix://{missing}", str(root))
    assert absent.argv[:3] == ("docker", "--host", f"unix://{missing}")


def test_conflicting_destination_flags_do_not_start_a_process(tmp_path: Path) -> None:
    host, root = _endpoint(tmp_path)
    (tmp_path / "docker.sock").write_bytes(b"")
    seen: list[list[str]] = []

    def factory(argv: list[str], **kwargs: object):
        del kwargs
        seen.append(list(argv))
        raise AssertionError("popen")

    runner = _runner(factory, host, root)
    conflicts = (
        ["docker", "--host=unix:///var/run/docker.sock", "info"],
        ["docker", "--host", "unix:///var/run/docker.sock", "info"],
        ["docker", "-H", "unix:///var/run/docker.sock", "info"],
        ["docker", "-Hunix:///var/run/docker.sock", "info"],
        ["docker", "--context=default", "info"],
        ["docker", "--context", "default", "info"],
        ["docker", "-c", "default", "info"],
        ["docker", "-cdefault", "info"],
        ["docker", "context", "use", "default"],
        ["docker", "--host", host, "--host", host, "info"],
        ["docker", "--host", host, "-H", host, "info"],
    )
    for argv in conflicts:
        with pytest.raises(ApplicationContractError) as error:
            runner(list(argv), max_bytes=16)
        assert error.value.code == "pdl1_docker_endpoint_conflict"
    assert seen == []
    once = explicit_docker_argv(host, ["docker", "info"])
    assert explicit_docker_argv(host, once) == ["docker", "--host", host, "info"]
    assert once.count("--host") == 1

    def accept(argv: list[str], **kwargs: object):
        del kwargs
        seen.append(list(argv))

        class _Process:
            stdout = _Stream(b"ok\n")
            stderr = _Stream(b"")
            stdin = None
            returncode = 0

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return 0

        return _Process()

    runner = _runner(accept, host, root)
    accepted = (
        ["docker", "info"],
        ["docker", "--host", host, "info"],
        ["docker", f"--host={host}", "info"],
        ["docker", "-H", host, "info"],
        ["docker", f"-H{host}", "info"],
        ["docker", "build", "--context", "dir", "-c", "keep"],
    )
    for argv in accepted:
        runner(list(argv), max_bytes=16)
    assert all(
        call[:3] == ["docker", "--host", host] and call.count("--host") == 1 for call in seen
    )
    preserved = seen[-1]
    assert preserved[3:] == ["build", "--context", "dir", "-c", "keep"]


def test_global_option_values_are_not_the_subcommand(tmp_path: Path) -> None:
    host, root = _endpoint(tmp_path)
    (tmp_path / "docker.sock").write_bytes(b"")
    seen: list[list[str]] = []

    def refuse(argv: list[str], **kwargs: object):
        del kwargs
        seen.append(list(argv))
        raise AssertionError("popen")

    runner = _runner(refuse, host, root)
    rejected = (
        ["docker", "--config", "/tmp/config", "--host=unix:///tmp/other.sock", "info"],
        ["docker", "--log-level", "debug", "--context=other", "info"],
        ["docker", "--config", "/tmp/config", "--host", host, "--host", host, "info"],
        ["docker", "--log-level=debug", "--host", host, "-H", host, "info"],
        ["docker", "--not-a-docker-option", "info"],
        ["docker", "--not-a-docker-option=value", "info"],
        ["docker", "--config"],
        ["docker", "--log-level"],
        ["docker", "--config="],
        ["docker", "--debug=true", "info"],
    )
    for argv in rejected:
        with pytest.raises(ApplicationContractError) as error:
            runner(list(argv), max_bytes=16)
        assert error.value.code == "pdl1_docker_endpoint_conflict"
    assert seen == []

    def accept(argv: list[str], **kwargs: object):
        del kwargs
        seen.append(list(argv))

        class _Process:
            stdout = _Stream(b"ok\n")
            stderr = _Stream(b"")
            stdin = None
            returncode = 0

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return 0

        return _Process()

    runner = _runner(accept, host, root)
    accepted = (
        (
            ["docker", "--config", "/tmp/config", "info"],
            ["--config", "/tmp/config", "info"],
        ),
        (
            ["docker", "--config=/tmp/config", "info"],
            ["--config=/tmp/config", "info"],
        ),
        (
            ["docker", "--log-level", "debug", "info"],
            ["--log-level", "debug", "info"],
        ),
        (
            ["docker", "--log-level=debug", "info"],
            ["--log-level=debug", "info"],
        ),
        (
            ["docker", "--tls", "--tlscacert", "/tmp/ca.pem", "info"],
            ["--tls", "--tlscacert", "/tmp/ca.pem", "info"],
        ),
        (
            ["docker", "--tlscert=/tmp/cert.pem", "info"],
            ["--tlscert=/tmp/cert.pem", "info"],
        ),
        (
            ["docker", "--log-level", "debug", "build", "--context", "dir", "-c", "keep"],
            ["--log-level", "debug", "build", "--context", "dir", "-c", "keep"],
        ),
    )
    for argv, tail in accepted:
        seen.clear()
        runner(list(argv), max_bytes=16)
        assert seen[0][:3] == ["docker", "--host", host]
        assert seen[0][3:] == tail
        assert seen[0].count("--host") == 1
        assert explicit_docker_argv(host, seen[0]) == seen[0]


def test_expired_deadline_uses_the_persisted_endpoint(tmp_path: Path, monkeypatch) -> None:
    import os
    import time

    import cellertz.application.pdl1_docker as docker_module
    from cellertz.application.pdl1_docker import CONFIG_ENV

    host, root = _endpoint(tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = f"unix://{other_root / 'docker.sock'}"
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps({"docker_host": other, "docker_root": str(other_root)}),
        encoding="utf-8",
    )
    path = tmp_path / "armed.json"
    _write_claim(path, host, root, phase="armed", deadline_epoch=1)
    assert json.loads(path.read_text(encoding="utf-8"))["deadline_epoch"] < time.time()
    calls: list[list[str]] = []
    constructed: list[dict[str, object]] = []

    class _Recording:
        def __init__(self, **kwargs: object) -> None:
            constructed.append(kwargs)

        def __call__(
            self, argv: list[str], *, max_bytes: int, timeout: float | None = None
        ) -> bytes:
            del max_bytes, timeout
            calls.append(list(argv))
            if argv[3:6] == ["info", "--format", "{{.ID}}"]:
                return b"daemon-1\n"
            if argv[3:5] == ["kill", "cid-scripted"]:
                return b"killed\n"
            if argv[3:4] == ["ps"]:
                return b"cid-scripted\n"
            if argv[3:5] == ["inspect", "cid-scripted"]:
                body = {
                    "Id": "cid-scripted",
                    "Image": _IMAGE_ID,
                    "State": {"Status": "exited", "Running": False},
                }
                return json.dumps([body]).encode()
            raise ApplicationContractError("pdl1_docker_command_failed")

    def reject_deployment(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("deployment")

    monkeypatch.setattr(docker_module, "load_docker_deployment", reject_deployment)
    monkeypatch.setattr(docker_module, "ProductionCommandRunner", _Recording)
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "operator")
    monkeypatch.setenv(CONFIG_ENV, str(deployment))
    monkeypatch.setenv(_ENDPOINT_ENV, host)
    assert run(["enforce", "--state", str(path)], environ=dict(os.environ)) == 0
    assert constructed[0]["docker_host"] == host
    assert "DOCKER_HOST" not in constructed[0]
    assert calls
    assert {call[2] for call in calls} == {host}
    assert any(call[3:6] == ["info", "--format", "{{.ID}}"] for call in calls)
    assert any(call[3:5] == ["kill", "cid-scripted"] for call in calls)
    assert any(call[3:4] == ["ps"] for call in calls)
    assert any(call[3:5] == ["inspect", "cid-scripted"] for call in calls)
    assert other not in {call[2] for call in calls}


def test_unit_environment_divergence_makes_no_docker_call(tmp_path: Path, monkeypatch) -> None:
    import cellertz.application.pdl1_docker as docker_module

    host, root = _endpoint(tmp_path)
    other = f"unix://{tmp_path / 'other' / 'docker.sock'}"
    path = tmp_path / "armed.json"
    _write_claim(path, host, root, phase="armed", deadline_epoch=1)
    calls: list[object] = []

    class _Recording:
        def __init__(self, **_kwargs: object) -> None:
            calls.append("init")

        def __call__(
            self, argv: list[str], *, max_bytes: int, timeout: float | None = None
        ) -> bytes:
            del argv, max_bytes, timeout
            calls.append("call")
            return b""

    monkeypatch.setattr(docker_module, "ProductionCommandRunner", _Recording)
    assert run(["enforce", "--state", str(path)], environ={_ENDPOINT_ENV: other}) == 1
    assert calls == []


def test_enforced_replay_does_not_call_docker(tmp_path: Path, monkeypatch) -> None:
    import cellertz.application.pdl1_docker as docker_module

    host, root = _endpoint(tmp_path)
    path = tmp_path / "done.json"
    _write_claim(path, host, root, phase="enforced", deadline_epoch=1)
    calls: list[list[str]] = []
    constructed: list[dict[str, object]] = []

    class _Recording:
        def __init__(self, **kwargs: object) -> None:
            constructed.append(kwargs)

        def __call__(
            self, argv: list[str], *, max_bytes: int, timeout: float | None = None
        ) -> bytes:
            del max_bytes, timeout
            calls.append(list(argv))
            return b""

    monkeypatch.setattr(docker_module, "ProductionCommandRunner", _Recording)
    assert (
        run(
            ["enforce", "--state", str(path)],
            environ={_ENDPOINT_ENV: host, "DOCKER_HOST": "unix:///var/run/docker.sock"},
        )
        == 0
    )
    assert constructed[0]["docker_host"] == host
    assert calls == []


def test_acceptance_records_endpoint_divergence_without_a_fallback() -> None:
    class _Trace:
        def __init__(self) -> None:
            self.results = {("docker", "info", "--format", "{{json .}}"): "command_failed"}

    gaps = _endpoint_gaps(_Trace(), "unix:///tmp/cellertz-endpoint/docker.sock")
    assert gaps == ("docker_endpoint_divergence",)


def test_build_plan_requires_the_same_endpoint(tmp_path: Path) -> None:
    host, root = _endpoint(tmp_path)
    with pytest.raises(ApplicationContractError) as missing:
        build_plan(
            {},
            wheel=tmp_path / "wheel",
            params=tmp_path / "params",
            docker_host="",
            docker_root=root,
        )
    assert missing.value.code == "pdl1_docker_endpoint_required"
    wheel_name = "pyrosetta-2024.1-cp310-cp310-linux_x86_64.whl"
    vendor = tmp_path / "deploy" / "pdl1-bindcraft" / "vendor"
    vendor.mkdir(parents=True)
    wheel = vendor / wheel_name
    params = vendor / "alphafold_params_2022-12-06.tar"
    wheel.write_bytes(b"wheel-bytes")
    params.write_bytes(b"params-bytes")
    dockerfile = tmp_path / "deploy" / "pdl1-bindcraft" / "Dockerfile"
    dockerfile.write_text(
        "COPY deploy/pdl1-bindcraft/vendor/ /tmp/vendor/\n"
        'python -m pip install --no-deps "/tmp/vendor/${PYROSETTA_WHEEL_FILENAME}"\n'
        "assert_base_environment\n"
        "assert_post_install\n"
        "assert_bindcraft_files\n",
        encoding="utf-8",
    )
    document = {
        "schema": SCHEMA,
        "base_image_repository": "example.invalid/base",
        "base_image_digest": "sha256:" + ("12" * 32),
        "host_nvidia_driver": "535.288.01",
        "cuda_version": "12.2",
        "colabdesign_commit": "ab" * 20,
        "pyrosetta_wheel_filename": wheel_name,
        "pyrosetta_wheel_sha256": hashlib.sha256(b"wheel-bytes").hexdigest(),
        "alphafold_params_sha256": hashlib.sha256(b"params-bytes").hexdigest(),
    }
    plan = build_plan(
        document,
        wheel=wheel,
        params=params,
        docker_host=host,
        docker_root=root,
        root=tmp_path,
    )
    assert plan.argv[:3] == ("docker", "--host", host)
    assert plan.scientific_run_execution == "disabled"
    assert "unix:///var/run/docker.sock" not in plan.argv


def _runner(factory, host: str, root: str) -> ProductionCommandRunner:
    return ProductionCommandRunner(
        process_factory=factory,
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: None,
        set_blocking=lambda _fd, _flag: None,
        docker_host=host,
        docker_root=root,
    )


def _write_claim(path: Path, host: str, root: str, *, phase: str, deadline_epoch: int) -> None:
    path.write_text(
        json.dumps(
            {
                "container_id": "cid-scripted",
                "daemon_id": "daemon-1",
                "image_id": _IMAGE_ID,
                "phase": phase,
                "deadline_epoch": deadline_epoch,
                "docker_host": host,
                "docker_root": root,
                "fence": 0,
                "state_path": str(path),
                "unit": "cellertz-endpoint",
            }
        ),
        encoding="utf-8",
    )


def _plan(root: Path, host: str, docker_root: str):
    wheel_name = "pyrosetta-2024.1-cp310-cp310-linux_x86_64.whl"
    vendor = root / "deploy" / "pdl1-bindcraft" / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    wheel = vendor / wheel_name
    params = vendor / "alphafold_params_2022-12-06.tar"
    wheel.write_bytes(b"wheel-bytes")
    params.write_bytes(b"params-bytes")
    dockerfile = root / "deploy" / "pdl1-bindcraft" / "Dockerfile"
    dockerfile.write_text(
        "COPY deploy/pdl1-bindcraft/vendor/ /tmp/vendor/\n"
        'python -m pip install --no-deps "/tmp/vendor/${PYROSETTA_WHEEL_FILENAME}"\n'
        "assert_base_environment\n"
        "assert_post_install\n"
        "assert_bindcraft_files\n",
        encoding="utf-8",
    )
    document = {
        "schema": SCHEMA,
        "base_image_repository": "example.invalid/base",
        "base_image_digest": "sha256:" + ("12" * 32),
        "host_nvidia_driver": "535.288.01",
        "cuda_version": "12.2",
        "colabdesign_commit": "ab" * 20,
        "pyrosetta_wheel_filename": wheel_name,
        "pyrosetta_wheel_sha256": hashlib.sha256(b"wheel-bytes").hexdigest(),
        "alphafold_params_sha256": hashlib.sha256(b"params-bytes").hexdigest(),
    }
    return build_plan(
        document,
        wheel=wheel,
        params=params,
        docker_host=host,
        docker_root=docker_root,
        root=root,
    )


class _Stream:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def fileno(self) -> int:
        return 3

    def read(self, count: int) -> bytes:
        if self.offset >= len(self.payload):
            return b""
        chunk = self.payload[self.offset : self.offset + count]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        return None


def _tar() -> bytes:
    import io
    import tarfile

    payload = b"Design,Sequence\nexample,ACDE\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as packed:
        info = tarfile.TarInfo("out/design_records.csv")
        info.size = len(payload)
        packed.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()
