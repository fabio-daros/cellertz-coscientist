"""Host acceptance planning. Injected commands only. No Docker and no systemd."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cellertz.application.pdl1_host_acceptance import (
    AcceptanceError,
    execute_acceptance,
    main,
    observation_argv,
    parse_acceptance,
    plan_acceptance,
)

_IMAGE = "sha256:" + ("ab" * 32)
_IMAGE_ID = "sha256:" + ("cd" * 32)


def _document(tmp_path: Path, **overrides: object) -> dict[str, object]:
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)
    state = tmp_path / "deadlines"
    state.mkdir()
    mount = tmp_path / "mount"
    mount.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    document: dict[str, object] = {
        "acceptance_input_dir": str(mount),
        "argv": ["1"],
        "cpu_count": 1,
        "deadline_interpreter": str(interpreter),
        "deadline_manager": "system",
        "deadline_state_dir": str(state),
        "deployment_id": "accept1",
        "docker_host": f"unix://{tmp_path / 'docker.sock'}",
        "docker_root": str(tmp_path),
        "entrypoint": "sleep",
        "gpu_device": "0",
        "image_digest": _IMAGE,
        "image_id": _IMAGE_ID,
        "log_bytes": 1048576,
        "memory_mib": 512,
        "repository": "example.invalid/cellertz/acceptance",
        "staging_root": str(stage),
        "storage_bytes": 1048576,
    }
    document.update(overrides)
    return document


def _facts() -> tuple[dict[str, object], dict[str, object]]:
    info = {
        "OSType": "linux",
        "Driver": "overlay2",
        "DriverStatus": [["Supports project quotas", "true"]],
        "LoggingDriver": "json-file",
        "ID": "daemon-1",
        "Runtimes": {"nvidia": {}},
        "Credentials": "secret-token",
    }
    image = {
        "Id": _IMAGE_ID,
        "RepoDigests": [f"example.invalid/cellertz/acceptance@{_IMAGE}"],
    }
    return info, image


def _runner(
    info: dict[str, object],
    image: dict[str, object],
    *,
    systemctl_version: bytes = b"ok\n",
    linger: bytes = b"Linger=yes\n",
    enforcer: bytes = b"ok\n",
    fail_info: bool = False,
    fail_image: bool = False,
):
    from cellertz.application.contracts import ApplicationContractError

    def run(argv: list[str], *, max_bytes: int, timeout: float | None = None) -> bytes:
        del max_bytes, timeout
        run.calls.append(list(argv))
        if argv and argv[0] == "docker":
            if len(argv) < 4 or argv[1] != "--host":
                raise ApplicationContractError("pdl1_docker_command_failed")
            argv = ["docker", *argv[3:]]
        if argv[:3] == ["docker", "info", "--format"]:
            if fail_info:
                raise ApplicationContractError("pdl1_docker_command_failed")
            return json.dumps(info).encode()
        if argv[:3] == ["docker", "image", "inspect"]:
            if fail_image:
                raise ApplicationContractError("pdl1_docker_command_failed")
            return json.dumps([image]).encode()
        if argv == ["systemctl", "--version"]:
            return systemctl_version
        if argv[:2] == ["systemctl", "show"] or argv[:3] == ["systemctl", "--user", "show"]:
            return b"Version=256\n"
        if "--version" in argv:
            return b"ok\n"
        if argv and argv[0] == "loginctl":
            return linger
        if "-c" in argv:
            return enforcer
        raise ApplicationContractError("pdl1_docker_command_failed")

    run.calls = []
    return run


def test_plan_lists_missing_operator_values_without_commands() -> None:
    def forbidden(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("command")

    report = execute_acceptance({}, forbidden, execute=False)
    assert report.mode == "plan"
    assert "repository" in report.missing
    assert "image_digest" in report.missing
    assert "image_id" in report.missing
    assert "entrypoint" in report.missing
    assert "argv" in report.missing
    assert "gpu_device" in report.missing
    assert report.checks == ()
    body = report.to_dict()
    assert body["scientific_runner_ready"] is False
    assert body["execution_authorized"] is False
    assert body["host_acceptance_complete"] is False
    assert body["created_resources"] == []


def test_plan_rejects_a_vram_quota_and_builds_no_container(tmp_path: Path) -> None:
    document = _document(tmp_path, vram_mib=1024)
    report = plan_acceptance(document)
    assert report.missing == ("vram_quota",)
    assert report.checks == ()


def test_plan_uses_the_operator_image_and_does_not_execute(tmp_path: Path) -> None:
    document = _document(tmp_path)
    spec, missing = parse_acceptance(document)
    assert spec is not None and missing == ()
    report = plan_acceptance(document)
    assert report.missing == ()
    assert all(check.status == "not_run" for check in report.checks)
    create = next(check for check in report.checks if check.code == "container_configuration")
    assert create.mode == "manual"
    assert create.observed == ""
    assert "example.invalid/cellertz/acceptance@" + _IMAGE in create.argv
    assert "--network" in create.argv and "none" in create.argv
    assert "--gpus" in create.argv and "device=0" in create.argv
    assert "docker rm --force cellertz-accept-accept1" not in create.cleanup
    assert "Never remove" in create.cleanup
    codes = {check.code for check in report.checks}
    assert {
        "cpu_memory_enforcement",
        "storage_write_limit",
        "log_retention",
        "readonly_mount",
        "gpu_visibility",
        "worker_loss_deadline",
        "deadline_recovery",
    } <= codes
    assert not any(
        check.argv and check.argv[0] == "systemd-run" and "--unit" in check.argv
        for check in report.checks
    )
    commands = observation_argv(spec)
    assert ("docker", "create") not in commands
    assert all(command[0] != "systemd-run" or command[1:] == ("--version",) for command in commands)
    assert not any(command[0] == "loginctl" for command in commands)


def test_user_manager_plan_requires_linger(tmp_path: Path) -> None:
    spec, missing = parse_acceptance(_document(tmp_path, deadline_manager="user"))
    assert spec is not None and missing == ()
    linger = ("loginctl", "show-user", str(os.getuid()), "-p", "Linger")
    assert linger in observation_argv(spec)


def test_execute_observes_and_does_not_create_or_authorize(tmp_path: Path) -> None:
    document = _document(tmp_path)
    info, image = _facts()
    runner = _runner(info, image)
    (tmp_path / "mount" / "input.txt").write_text("scientific-bytes", encoding="utf-8")
    report = execute_acceptance(document, runner, execute=True)
    spec, _missing = parse_acceptance(document)
    assert spec is not None
    assert runner.calls == [list(command) for command in observation_argv(spec)]
    assert report.gaps == ()
    assert report.deadline_missing == ()
    body = report.to_dict()
    assert body["mode"] == "executed"
    assert body["scientific_runner_ready"] is False
    assert body["execution_authorized"] is False
    assert body["host_acceptance_complete"] is False
    assert body["created_resources"] == []
    statuses = {check.code: check.status for check in report.checks}
    assert statuses["docker_daemon"] == "passed"
    assert statuses["image_identity"] == "passed"
    assert statuses["systemd_linger"] == "not_run"
    assert statuses["enforcer_import"] == "passed"
    assert statuses["container_configuration"] == "not_run"
    assert statuses["cpu_memory_enforcement"] == "not_run"
    assert statuses["worker_loss_deadline"] == "not_run"
    assert statuses["deadline_recovery"] == "not_run"
    recovery = next(check for check in report.checks if check.code == "deadline_recovery")
    assert "external-attempt store" in recovery.purpose
    assert "retry reconciliation" in recovery.purpose
    daemon = next(check for check in report.checks if check.code == "docker_daemon")
    image_check = next(check for check in report.checks if check.code == "image_identity")
    assert daemon.observed != daemon.evidence
    assert "id=daemon-1" in daemon.observed
    assert "nvidia_runtime=true" in daemon.observed
    assert f"id={_IMAGE_ID}" in image_check.observed
    assert "repository_digest=present" in image_check.observed
    assert "secret-token" not in json.dumps(body)
    assert "scientific-bytes" not in json.dumps(body)
    assert "same_uid_staging_replacement" in body["limitations"]


def test_execute_reports_an_image_mismatch_without_readiness(tmp_path: Path) -> None:
    document = _document(tmp_path)
    info, image = _facts()
    image = dict(image)
    image["Id"] = "sha256:" + ("ff" * 32)
    runner = _runner(info, image)
    report = execute_acceptance(document, runner, execute=True)
    assert "image_id" in report.gaps
    assert report.to_dict()["scientific_runner_ready"] is False
    assert all(call[:2] != ["docker", "create"] for call in runner.calls)


def test_equal_image_id_and_digest_is_rejected_before_commands(tmp_path: Path) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("command")

    document = _document(tmp_path, image_id=_IMAGE)
    with pytest.raises(AcceptanceError) as error:
        execute_acceptance(document, forbidden, execute=True)
    assert error.value.missing == ("acceptance_document",)
    assert plan_acceptance(document).checks == ()


def test_invalid_interpreter_skips_later_deadline_checks(tmp_path: Path) -> None:
    document = _document(tmp_path, deadline_manager="user")
    Path(str(document["deadline_interpreter"])).chmod(0o644)
    runner = _runner(*_facts())
    report = execute_acceptance(document, runner, execute=True)
    statuses = {check.code: check.status for check in report.checks}
    assert statuses["deadline_interpreter"] == "failed"
    assert statuses["state_directory"] == "passed"
    assert statuses["systemctl_version"] == "not_run"
    assert statuses["systemd_run_version"] == "not_run"
    assert statuses["docker_cli_version"] == "not_run"
    assert statuses["systemd_linger"] == "not_run"
    assert statuses["enforcer_import"] == "not_run"
    assert statuses["systemd_version_property"] == "not_run"
    assert not any(call[0] == "systemctl" for call in runner.calls)
    assert not any(call[0] == "loginctl" for call in runner.calls)


def test_failed_version_command_skips_the_commands_after_it(tmp_path: Path) -> None:
    document = _document(tmp_path)
    runner = _runner(*_facts(), systemctl_version=b"\n")
    report = execute_acceptance(document, runner, execute=True)
    statuses = {check.code: check.status for check in report.checks}
    assert statuses["deadline_interpreter"] == "passed"
    assert statuses["systemctl_version"] == "failed"
    assert statuses["systemd_run_version"] == "not_run"
    assert statuses["docker_cli_version"] == "not_run"
    assert statuses["systemd_linger"] == "not_run"
    assert statuses["enforcer_import"] == "not_run"
    assert statuses["systemd_version_property"] == "not_run"
    assert ["systemd-run", "--version"] not in runner.calls


def test_failed_linger_skips_import_and_records_the_result(tmp_path: Path) -> None:
    document = _document(tmp_path, deadline_manager="user")
    runner = _runner(*_facts(), linger=b"Linger=no\n")
    report = execute_acceptance(document, runner, execute=True)
    statuses = {check.code: check.status for check in report.checks}
    linger = next(check for check in report.checks if check.code == "systemd_linger")
    assert statuses["systemctl_version"] == "passed"
    assert statuses["systemd_linger"] == "failed"
    assert linger.observed == "Linger=no"
    assert statuses["enforcer_import"] == "not_run"
    assert statuses["systemd_version_property"] == "not_run"
    assert not any("-c" in call for call in runner.calls)


def test_failed_import_skips_the_version_property(tmp_path: Path) -> None:
    document = _document(tmp_path)
    runner = _runner(*_facts(), enforcer=b"no\nSECRET-TAIL")
    report = execute_acceptance(document, runner, execute=True)
    statuses = {check.code: check.status for check in report.checks}
    imported = next(check for check in report.checks if check.code == "enforcer_import")
    assert statuses["enforcer_import"] == "failed"
    assert imported.observed == "rejected"
    assert "SECRET-TAIL" not in json.dumps(report.to_dict())
    assert statuses["systemd_version_property"] == "not_run"
    assert not any(call[:2] == ["systemctl", "show"] for call in runner.calls)


def test_version_dump_is_not_copied_into_the_report(tmp_path: Path) -> None:
    document = _document(tmp_path)
    runner = _runner(*_facts(), systemctl_version=b"A" * 500 + b"\nSECRET-TAIL")
    report = execute_acceptance(document, runner, execute=True)
    version = next(check for check in report.checks if check.code == "systemctl_version")
    assert version.status == "failed"
    assert version.observed == "rejected"
    assert "SECRET-TAIL" not in json.dumps(report.to_dict())


def test_invalid_daemon_survives_image_inspection_failure(tmp_path: Path) -> None:
    document = _document(tmp_path)
    info, image = _facts()
    info = dict(info)
    info["OSType"] = "windows"
    runner = _runner(info, image, fail_image=True)
    report = execute_acceptance(document, runner, execute=True)
    statuses = {check.code: check.status for check in report.checks}
    daemon = next(check for check in report.checks if check.code == "docker_daemon")
    assert statuses["docker_daemon"] == "failed"
    assert "architecture" in report.gaps
    assert "os_type=windows" in daemon.observed
    assert statuses["image_identity"] == "failed"
    assert any(
        call[1:2] == ["--host"] and call[3:5] == ["image", "inspect"] for call in runner.calls
    )


def test_valid_daemon_survives_image_inspection_failure(tmp_path: Path) -> None:
    document = _document(tmp_path)
    runner = _runner(*_facts(), fail_image=True)
    report = execute_acceptance(document, runner, execute=True)
    statuses = {check.code: check.status for check in report.checks}
    assert statuses["docker_daemon"] == "passed"
    assert "architecture" not in report.gaps
    assert statuses["image_identity"] == "failed"
    assert "docker_facts" in report.gaps


def test_daemon_command_failure_leaves_image_inspection_not_run(tmp_path: Path) -> None:
    document = _document(tmp_path)
    runner = _runner(*_facts(), fail_info=True)
    report = execute_acceptance(document, runner, execute=True)
    statuses = {check.code: check.status for check in report.checks}
    assert statuses["docker_daemon"] == "failed"
    assert statuses["image_identity"] == "not_run"
    assert not any(call[3:5] == ["image", "inspect"] for call in runner.calls)
    assert "docker_facts" in report.gaps


def test_execute_stops_before_commands_when_values_are_missing() -> None:
    def forbidden(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("command")

    with pytest.raises(AcceptanceError) as error:
        execute_acceptance({}, forbidden, execute=True)
    assert "repository" in error.value.missing


def test_cli_rejected_version_exits_nonzero(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(_document(tmp_path)), encoding="utf-8")
    runner = _runner(*_facts(), systemctl_version=b"not a version!\n")

    def factory(*_args: object, **_kwargs: object) -> object:
        return runner

    monkeypatch.setattr(
        "cellertz.application.pdl1_docker.ProductionCommandRunner",
        factory,
    )
    code = main(["--execute", str(path)])
    assert code == 2
    body = json.loads(capsys.readouterr().out)
    assert body["host_acceptance_complete"] is False
    assert body["missing"] == []
    assert body["gaps"] == []
    assert body["deadline_missing"] == []
    statuses = {check["code"]: check["status"] for check in body["checks"]}
    assert statuses["systemctl_version"] == "failed"
    version = next(check for check in body["checks"] if check["code"] == "systemctl_version")
    assert version["observed"] == "rejected"
    assert "not a version!" not in json.dumps(body)
    assert statuses["container_configuration"] == "not_run"
    assert statuses["deadline_recovery"] == "not_run"
    assert not any(call[:2] == ["docker", "create"] for call in runner.calls)


def test_cli_plan_does_not_construct_a_host_runner(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(_document(tmp_path)), encoding="utf-8")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("runner")

    monkeypatch.setattr(
        "cellertz.application.pdl1_docker.ProductionCommandRunner",
        forbidden,
    )
    code = main([str(path)])
    assert code == 0
    assert main([]) == 2


def test_cli_passed_observations_keep_manual_checks_not_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(_document(tmp_path)), encoding="utf-8")
    runner = _runner(*_facts())

    def factory(*_args: object, **_kwargs: object) -> object:
        return runner

    monkeypatch.setattr(
        "cellertz.application.pdl1_docker.ProductionCommandRunner",
        factory,
    )
    assert main(["--execute", str(path)]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["host_acceptance_complete"] is False
    assert all(
        check["status"] == "not_run" for check in body["checks"] if check["mode"] == "manual"
    )


def test_deadline_driver_signals_and_stays_alive_on_injected_boundaries(
    tmp_path: Path, monkeypatch
) -> None:
    from cellertz.application.pdl1_docker import DeadlineAck

    document = _document(tmp_path)
    calls: list[str] = []

    def refuse_runner(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("runner")

    monkeypatch.setattr(
        "cellertz.application.pdl1_docker.ProductionCommandRunner",
        refuse_runner,
    )

    class _Deadline:
        def install(self, claim: object) -> DeadlineAck:
            calls.append(f"install:{getattr(claim, 'container_id', '')}")
            return DeadlineAck(ready=True, expired=False)

        def guarded_start(self, control: object, container_id: str, daemon_id: str) -> bool:
            del control, daemon_id
            calls.append(f"start:{container_id}")
            return True

        def recover(self, container_id: str, daemon_id: str) -> str:
            del container_id, daemon_id
            raise AssertionError("recover")

    def notify(line: str) -> None:
        calls.append(line)

    def sleep(seconds: float) -> None:
        calls.append(f"sleep:{seconds}")
        raise InterruptedError

    from cellertz.application.pdl1_host_acceptance import drive_acceptance_deadline

    with pytest.raises(InterruptedError):
        drive_acceptance_deadline(
            document,
            container_id="cid-accept",
            daemon_id="daemon-1",
            deadline_epoch=1_700_000_000,
            deadline=_Deadline(),
            control=object(),
            sleep=sleep,
            notify=notify,
        )
    assert calls == [
        "install:cid-accept",
        "start:cid-accept",
        "cellertz-acceptance-running cid-accept",
        "sleep:1.0",
    ]
