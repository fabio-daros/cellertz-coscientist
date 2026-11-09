"""Phase 6I scientific-runner readiness. Not an execution."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.conversation_transport import configured_handler
from cellertz.application.scientific_runner_readiness import (
    CD22_PROFILE_ID,
    PDL1_PROFILE_ID,
    TACI_PROFILE_ID,
    ProbeObservation,
    ReadinessCheck,
    ReadinessReply,
    load_scientific_runner_readiness,
)
from cellertz.application.scientific_runner_readiness_config import (
    ScientificRunnerConfigurationError,
    ScientificRunnerSettings,
    resolve_scientific_runner_settings,
)
from cellertz.application.scientific_runner_readiness_transport import (
    READINESS_PATH,
    handle_read_scientific_runner_readiness,
)
from cellertz.conversation.__main__ import main

SESSION = "session_pdl1_prototype"
WHEN = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
OBSERVED = "2026-10-12T15:00:00+00:00"
_OMITTED = (
    "argv_template",
    "entrypoint",
    "expected_inputs",
    "expected_outputs",
    "image_digest",
    "max_output_bytes",
    "profile_id",
    "resource_envelope",
    "runtime_name",
    "runtime_version",
    "target_scope",
    "timeout_seconds",
    "tool_name",
    "tool_version",
)


class CountingProbe:
    def __init__(self, observation: ProbeObservation | None = None, error: Exception | None = None):
        self.calls = 0
        self.observation = observation or ProbeObservation("passed", True)
        self.error = error

    def probe(self) -> ProbeObservation:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.observation


def _clock() -> datetime:
    return WHEN


def _manifest() -> dict:
    return {
        "argv_template": ["--recorded-template"],
        "entrypoint": "recorded-entry",
        "expected_inputs": [
            {"media_type": "application/json", "name": "binder_specification"},
            {"media_type": "chemical/x-pdb", "name": "target_structure"},
        ],
        "expected_outputs": [{"media_type": "text/csv", "name": "design_records"}],
        "image_digest": "sha256:" + ("ab" * 32),
        "max_output_bytes": 1048576,
        "profile_id": PDL1_PROFILE_ID,
        "resource_envelope": {
            "cpu_count": 2,
            "gpu_count": 0,
            "max_output_bytes": 1048576,
            "memory_mib": 4096,
            "timeout_seconds": 120,
        },
        "runtime_name": "recorded-runtime",
        "runtime_version": "recorded-runtime-1",
        "schema_version": "scientific-runner-manifest-v1",
        "target_scope": "PDL1",
        "timeout_seconds": 120,
        "tool_name": "bindcraft",
        "tool_version": "recorded-tool-1",
    }


def _canonical(document: dict) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _write(path: Path, document: dict) -> str:
    text = _canonical(document)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(path: Path, probe: CountingProbe | None = None, **env: str):
    settings = {
        "CELLERTZ_SCIENTIFIC_RUNNER_READINESS": "manifest",
        "CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST": str(path),
    }
    settings.update(env)
    return load_scientific_runner_readiness(
        session_id=SESSION,
        environ=settings,
        probe=probe,
        clock=_clock,
    )


def _codes(reply: ReadinessReply) -> dict[str, str]:
    return {check.code: check.status for check in reply.checks}


def _attestation(document: dict, digest: str, **overrides: object) -> ProbeObservation:
    fields: dict[str, object] = {
        "outcome": "passed",
        "ready": True,
        "manifest_digest": digest,
        "profile_id": document["profile_id"],
        "tool_name": document["tool_name"],
        "tool_version": document["tool_version"],
        "runtime_name": document["runtime_name"],
        "runtime_version": document["runtime_version"],
        "image_digest": document["image_digest"],
        "target_scope": document["target_scope"],
    }
    fields.update(overrides)
    return ProbeObservation(**fields)  # type: ignore[arg-type]


def test_default_configuration_is_disabled_and_does_not_probe(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("probe")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    probe = CountingProbe()
    service = load_scientific_runner_readiness(
        session_id=SESSION, environ={}, probe=probe, clock=_clock
    )
    reply = service.read()
    assert probe.calls == 0
    assert reply.status == "disabled"
    assert reply.execution_authorized is False
    assert reply.scientific_execution is False
    assert reply.scientific_validation is False
    assert type(reply.execution_authorized) is bool
    assert reply.profile_id == PDL1_PROFILE_ID
    assert reply.tool_name == "bindcraft"
    assert reply.tool_version is None
    assert reply.runtime_name is None
    assert reply.image_digest is None
    assert reply.manifest_digest is None
    assert reply.observed_at == OBSERVED
    assert _codes(reply)["runner_disabled"] == "blocked"
    assert _codes(reply)["tool_version"] == "blocked"
    assert _codes(reply)["readiness_probe_not_recorded"] == "blocked"
    service.read()
    assert probe.calls == 0


def test_injected_profile_can_be_ready_without_authorizing_execution(tmp_path: Path) -> None:
    document = _manifest()
    digest = _write(tmp_path / "manifest.json", document)
    probe = CountingProbe(_attestation(document, digest))
    service = _load(tmp_path / "manifest.json", probe)
    reply = service.read()
    body = reply.to_dict()
    assert probe.calls == 1
    assert reply.status == "ready"
    assert body["manifest_digest"] == digest
    assert body["tool_version"] == "recorded-tool-1"
    assert body["runtime_version"] == "recorded-runtime-1"
    assert body["image_digest"] == "sha256:" + ("ab" * 32)
    assert body["execution_authorized"] is False
    assert body["scientific_execution"] is False
    assert body["scientific_validation"] is False
    assert all(check["status"] == "passed" for check in body["checks"])
    encoded = json.dumps(body)
    for hidden in ("--recorded-template", "recorded-entry", "4096", "/"):
        assert hidden not in encoded
    service.read()
    assert probe.calls == 1


@pytest.mark.parametrize("field", _OMITTED)
def test_each_missing_requirement_blocks_readiness(tmp_path: Path, field: str) -> None:
    document = _manifest()
    document.pop(field)
    probe = CountingProbe()
    _write(tmp_path / "manifest.json", document)
    reply = _load(tmp_path / "manifest.json", probe).read()
    assert reply.status == "blocked"
    assert reply.execution_authorized is False
    assert any(check.status == "blocked" for check in reply.checks)
    assert probe.calls == 1


def test_manifest_digest_is_the_canonical_document(tmp_path: Path) -> None:
    text = _canonical(_manifest())
    path = tmp_path / "manifest.json"
    path.write_text(text, encoding="utf-8")
    reply = _load(path, CountingProbe()).read()
    assert reply.manifest_digest == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_noncanonical_oversized_and_invalid_manifests_fail_closed(tmp_path: Path) -> None:
    probe = CountingProbe()
    pretty = tmp_path / "pretty.json"
    pretty.write_text(json.dumps(_manifest(), indent=2), encoding="utf-8")
    rejected = _load(pretty, probe).read()
    assert rejected.status == "configuration_failure"
    assert rejected.checks[0].code == "manifest_not_canonical"
    assert probe.calls == 0

    huge = tmp_path / "huge.json"
    huge.write_bytes(b"SECRET_BODY" + (b"x" * 70000))
    oversized = _load(huge, probe).read()
    assert oversized.status == "configuration_failure"
    assert oversized.checks[0].code == "manifest_too_large"
    assert "SECRET_BODY" not in json.dumps(oversized.to_dict())

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    invalid = _load(broken, probe).read()
    assert invalid.checks[0].code == "manifest_invalid"


def test_wrong_types_extra_keys_and_unsupported_identity_fail_closed(tmp_path: Path) -> None:
    probe = CountingProbe()
    boolean = _manifest()
    boolean["resource_envelope"]["cpu_count"] = True
    boolean["resource_envelope"]["gpu_count"] = False
    _write(tmp_path / "bool.json", boolean)
    typed = _load(tmp_path / "bool.json", probe).read()
    assert typed.status == "configuration_failure"
    assert typed.checks[0].code == "invalid_manifest_type"
    assert typed.to_dict()["execution_authorized"] is False
    assert probe.calls == 0

    extra = _manifest()
    extra["command"] = "/secret/command"
    _write(tmp_path / "extra.json", extra)
    leaked = _load(tmp_path / "extra.json", probe).read()
    assert leaked.checks[0].code == "manifest_invalid"
    assert "/secret/command" not in json.dumps(leaked.to_dict())

    schema = _manifest()
    schema["schema_version"] = "scientific-runner-manifest-v2"
    _write(tmp_path / "schema.json", schema)
    assert _load(tmp_path / "schema.json", probe).read().checks[0].code == "manifest_invalid"

    unknown_schema = _manifest()
    unknown_schema["schema_version"] = "scientific-runner-manifest-v3"
    _write(tmp_path / "unknown-schema.json", unknown_schema)
    assert (
        _load(tmp_path / "unknown-schema.json", probe).read().checks[0].code == "unsupported_schema"
    )

    profile = _manifest()
    profile["profile_id"] = "other-profile"
    _write(tmp_path / "profile.json", profile)
    unknown = _load(tmp_path / "profile.json", probe).read()
    assert unknown.checks[0].code == "unsupported_profile"
    assert unknown.profile_id is None
    assert "other-profile" not in json.dumps(unknown.to_dict())


def test_latest_and_non_digest_image_stay_blocked(tmp_path: Path) -> None:
    document = _manifest()
    document["tool_version"] = "latest"
    document["image_digest"] = "latest"
    _write(tmp_path / "manifest.json", document)
    reply = _load(tmp_path / "manifest.json", CountingProbe()).read()
    assert reply.status == "blocked"
    assert reply.tool_version is None
    assert reply.image_digest is None
    assert "latest" not in json.dumps(reply.to_dict())


@pytest.mark.parametrize(
    "profile_id, scope, tool",
    [
        (CD22_PROFILE_ID, "CD22", "mber"),
        (TACI_PROFILE_ID, "TACI", "mber"),
    ],
)
def test_cd22_and_taci_stay_blocked_without_a_probe(
    tmp_path: Path, profile_id: str, scope: str, tool: str
) -> None:
    document = _manifest()
    document["profile_id"] = profile_id
    document["target_scope"] = scope
    document["tool_name"] = tool
    document["tool_version"] = "mber-recorded-9"
    probe = CountingProbe()
    _write(tmp_path / "manifest.json", document)
    reply = _load(tmp_path / "manifest.json", probe).read()
    assert reply.status == "blocked"
    assert reply.tool_version is None
    assert _codes(reply)["target_execution_blocked"] == "blocked"
    assert _codes(reply)["generator_version_not_recorded"] == "blocked"
    assert probe.calls == 0
    assert reply.execution_authorized is False


def test_environment_does_not_launch_a_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "launched"
    script = tmp_path / "probe"
    script.write_text(f"#!/bin/sh\necho launched > {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    path = tmp_path / "manifest.json"
    _write(path, _manifest())

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    reply = load_scientific_runner_readiness(
        session_id=SESSION,
        environ={
            "CELLERTZ_SCIENTIFIC_RUNNER_READINESS": "manifest",
            "CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST": str(path),
            "CELLERTZ_SCIENTIFIC_RUNNER_PROBE": str(script),
            "CELLERTZ_SCIENTIFIC_RUNNER_PROBE_TIMEOUT_SECONDS": "1",
        },
        clock=_clock,
    ).read()
    assert reply.status == "blocked"
    assert _codes(reply)["readiness_probe_not_recorded"] == "blocked"
    assert reply.execution_authorized is False
    assert not marker.exists()
    assert resolve_scientific_runner_settings(
        {
            "CELLERTZ_SCIENTIFIC_RUNNER_READINESS": "manifest",
            "CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST": str(path),
            "CELLERTZ_SCIENTIFIC_RUNNER_PROBE": str(script),
        }
    ) == ScientificRunnerSettings("manifest", str(path))
    assert str(script) not in json.dumps(reply.to_dict())


def test_probe_timeout_malformed_reply_and_exceptions_are_safe(tmp_path: Path) -> None:
    _write(tmp_path / "manifest.json", _manifest())
    timeout = CountingProbe(ProbeObservation("timeout", None))
    timed = _load(tmp_path / "manifest.json", timeout).read()
    assert timed.status == "blocked"
    assert _codes(timed)["readiness_probe_timeout"] == "blocked"

    malformed = CountingProbe(ProbeObservation("malformed", None))
    bad = _load(tmp_path / "manifest.json", malformed).read()
    assert _codes(bad)["readiness_probe_malformed"] == "blocked"

    boom = CountingProbe(error=RuntimeError("Traceback /Users/secret TOKEN=abc"))
    failed = _load(tmp_path / "manifest.json", boom).read()
    encoded = json.dumps(failed.to_dict())
    assert failed.status == "adapter_failure"
    for leak in ("Traceback", "/Users/secret", "TOKEN", "RuntimeError"):
        assert leak not in encoded


_MISMATCH = {
    "manifest_digest": "11" * 32,
    "profile_id": CD22_PROFILE_ID,
    "tool_name": "mber",
    "tool_version": "other-tool-1",
    "runtime_name": "other-runtime",
    "runtime_version": "other-runtime-1",
    "image_digest": "sha256:" + ("cd" * 32),
    "target_scope": "CD22",
    "ready": False,
}


@pytest.mark.parametrize("field", _MISMATCH)
def test_probe_identity_mismatch_cannot_mark_the_manifest_ready(tmp_path: Path, field: str) -> None:
    document = _manifest()
    digest = _write(tmp_path / "manifest.json", document)
    observation = _attestation(document, digest, **{field: _MISMATCH[field]})
    reply = _load(tmp_path / "manifest.json", CountingProbe(observation)).read()
    encoded = json.dumps(reply.to_dict())
    assert reply.status == "blocked"
    assert _codes(reply)["readiness_probe_mismatch"] == "blocked"
    assert reply.manifest_digest == digest
    assert reply.execution_authorized is False
    if field != "ready":
        assert str(_MISMATCH[field]) not in encoded


@pytest.mark.parametrize("field", [name for name in _MISMATCH if name != "ready"])
def test_probe_missing_identity_field_is_a_mismatch(tmp_path: Path, field: str) -> None:
    document = _manifest()
    digest = _write(tmp_path / "manifest.json", document)
    observation = _attestation(document, digest, **{field: None})
    reply = _load(tmp_path / "manifest.json", CountingProbe(observation)).read()
    assert reply.status == "blocked"
    assert _codes(reply)["readiness_probe_mismatch"] == "blocked"


def test_probe_ready_must_be_an_exact_boolean(tmp_path: Path) -> None:
    document = _manifest()
    digest = _write(tmp_path / "manifest.json", document)
    observation = _attestation(document, digest, ready=1)
    reply = _load(tmp_path / "manifest.json", CountingProbe(observation)).read()
    assert reply.status == "blocked"
    assert _codes(reply)["readiness_probe_mismatch"] == "blocked"
    assert reply.execution_authorized is False


def test_observation_from_another_manifest_cannot_mark_this_one_ready(tmp_path: Path) -> None:
    original = _manifest()
    original_digest = _write(tmp_path / "original.json", original)
    attestation = _attestation(original, original_digest)
    current = _manifest()
    current["tool_version"] = "recorded-tool-2"
    digest = _write(tmp_path / "manifest.json", current)
    reply = _load(tmp_path / "manifest.json", CountingProbe(attestation)).read()
    assert reply.status == "blocked"
    assert _codes(reply)["readiness_probe_mismatch"] == "blocked"
    assert reply.manifest_digest == digest
    assert reply.tool_version == "recorded-tool-2"
    assert original_digest not in json.dumps(reply.to_dict())
    assert "recorded-tool-1" not in json.dumps(reply.to_dict())


def test_configuration_errors_hide_paths_and_do_not_start_the_server(
    tmp_path: Path, monkeypatch
) -> None:
    with pytest.raises(ScientificRunnerConfigurationError) as caught:
        resolve_scientific_runner_settings({"CELLERTZ_SCIENTIFIC_RUNNER_READINESS": "gpu"})
    assert caught.value.code == "invalid_scientific_runner_readiness"
    assert str(caught.value) == "invalid_scientific_runner_readiness"

    with pytest.raises(ScientificRunnerConfigurationError) as missing:
        resolve_scientific_runner_settings({"CELLERTZ_SCIENTIFIC_RUNNER_READINESS": "manifest"})
    assert missing.value.code == "scientific_runner_manifest_required"
    with pytest.raises(ScientificRunnerConfigurationError) as relative:
        resolve_scientific_runner_settings(
            {
                "CELLERTZ_SCIENTIFIC_RUNNER_READINESS": "manifest",
                "CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST": "SECRET_PATH_COMPONENT/manifest.json",
            }
        )
    assert relative.value.code == "invalid_scientific_runner_manifest"
    assert "SECRET_PATH_COMPONENT" not in str(relative.value)
    absent = tmp_path / "SECRET_PATH_COMPONENT" / "manifest.json"
    reply = _load(absent, CountingProbe()).read()
    assert reply.status == "configuration_failure"
    assert "SECRET_PATH_COMPONENT" not in json.dumps(reply.to_dict())

    def started(*_args, **_kwargs):
        raise AssertionError("server started")

    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUNNER_READINESS", "gpu")
    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    with pytest.raises(ScientificRunnerConfigurationError) as startup:
        main()
    assert startup.value.code == "invalid_scientific_runner_readiness"
    assert str(startup.value) == startup.value.code


def test_get_is_read_only_session_bound_and_rejects_queries(tmp_path: Path) -> None:
    from tests.test_approved_run_transport import RUNS_PATH, _body, _create_payload, _ready

    document = _manifest()
    digest = _write(tmp_path / "manifest.json", document)
    probe = CountingProbe(_attestation(document, digest))
    readiness = _load(tmp_path / "manifest.json", probe)
    runs, runner, gate, _drafter, _draft, _adapter, approval_id, _status, _done = _ready()
    _created_status, created = __import__(
        "cellertz.application.approved_run_transport", fromlist=["handle_create_approved_run"]
    ).handle_create_approved_run(runs, _body(_create_payload(approval_id)))
    run_id = created["run"]["run_id"]
    before, before_body = __import__(
        "cellertz.application.approved_run_transport", fromlist=["handle_read_approved_run"]
    ).handle_read_approved_run(runs, f"{RUNS_PATH}/{run_id}?session_id={SESSION}")
    assert before == 200
    handler = configured_handler(
        object(), approvals=gate, runs=runs, session_id=SESSION, readiness=readiness
    )
    status, body = _invoke(handler, "GET", READINESS_PATH, b"")
    again, again_body = _invoke(handler, "GET", READINESS_PATH, b"")
    assert status == 200
    assert again == 200
    assert body["status"] == "ready"
    assert body["manifest_digest"] == digest
    assert body["session_id"] == SESSION
    assert again_body == body
    assert probe.calls == 1
    after, after_body = __import__(
        "cellertz.application.approved_run_transport", fromlist=["handle_read_approved_run"]
    ).handle_read_approved_run(runs, f"{RUNS_PATH}/{run_id}?session_id={SESSION}")
    assert after == 200
    assert after_body == before_body
    assert runner.calls == 0

    query_status, query_body = handle_read_scientific_runner_readiness(
        readiness, f"{READINESS_PATH}?session_id=other&token=SECRET", SESSION
    )
    assert query_status == 400
    assert query_body["status"] == "invalid_payload"
    assert "SECRET" not in json.dumps(query_body)
    assert "other" not in json.dumps(query_body)
    posted, _payload = _invoke(handler, "POST", READINESS_PATH, b"{}")
    assert posted == 404
    assert probe.calls == 1


def test_invalid_adapter_result_fails_closed() -> None:
    class Bad:
        def read(self):
            return {"status": "ready", "execution_authorized": 1, "argv": "/tmp/run"}

    status, body = handle_read_scientific_runner_readiness(Bad(), READINESS_PATH, SESSION)
    encoded = json.dumps(body)
    assert status == 200
    assert body["status"] == "adapter_failure"
    assert body["execution_authorized"] is False
    assert "/tmp/run" not in encoded
    with pytest.raises(ApplicationContractError):
        ReadinessReply(
            status="ready",
            text="x",
            session_id=SESSION,
            profile_id=None,
            tool_name=None,
            tool_version=None,
            runtime_name=None,
            runtime_version=None,
            image_digest=None,
            target_scope=None,
            manifest_digest=None,
            observed_at=OBSERVED,
            checks=(),
            execution_authorized=True,  # type: ignore[arg-type]
        )


def test_transport_rejects_a_malicious_readiness_subclass(tmp_path: Path) -> None:
    calls = 0

    class Leaky(ReadinessReply):
        def to_dict(self) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "status": "ready",
                "text": (
                    "Traceback /tmp/cellertz-secret-path token=cellertz-secret "
                    "SELECT cellertz_secret_table"
                ),
                "secret_path": "/Users/cellertz-secret-home",
                "argv": "C:\\cellertz-secret-windows",
                "command": "`cellertz-secret-command`",
                "execution_authorized": True,
                "checks": [],
            }

    leaked = object.__new__(Leaky)
    for name in ReadinessReply.__dataclass_fields__:
        object.__setattr__(leaked, name, None)
    object.__setattr__(leaked, "status", "ready")
    object.__setattr__(leaked, "text", "Traceback /tmp/cellertz-secret-path")
    object.__setattr__(leaked, "session_id", SESSION)
    object.__setattr__(leaked, "observed_at", OBSERVED)
    object.__setattr__(leaked, "checks", ())
    object.__setattr__(leaked, "execution_authorized", True)
    object.__setattr__(leaked, "scientific_execution", True)
    object.__setattr__(leaked, "scientific_validation", True)

    class Service:
        def read(self) -> Leaky:
            return leaked

    status, body = handle_read_scientific_runner_readiness(Service(), READINESS_PATH, SESSION)
    encoded = json.dumps(body)
    assert status == 200
    assert calls == 0
    assert body["status"] == "adapter_failure"
    assert (
        body["text"]
        == "The scientific runner readiness adapter failed. No scientific action was taken."
    )
    assert body["execution_authorized"] is False
    assert body["scientific_execution"] is False
    assert body["scientific_validation"] is False
    assert body["profile_id"] is None
    assert body["checks"][0]["code"] == "readiness_adapter_failed"
    assert set(body) == set(ReadinessReply.public_fields())
    for leak in (
        "cellertz-secret",
        "/tmp/",
        "/Users/",
        "Traceback",
        "SELECT",
        "cellertz-secret-windows",
        "cellertz-secret-command",
        "`",
    ):
        assert leak not in encoded

    document = _manifest()
    digest = _write(tmp_path / "manifest.json", document)
    ready = _load(tmp_path / "manifest.json", CountingProbe(_attestation(document, digest))).read()

    def overridden() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"status": "ready", "text": "cellertz-instance-secret", "argv": "/tmp/instance"}

    object.__setattr__(ready, "to_dict", overridden)
    overridden_status, overridden_body = handle_read_scientific_runner_readiness(
        _FixedReadiness(ready), READINESS_PATH, SESSION
    )
    overridden_encoded = json.dumps(overridden_body)
    assert overridden_status == 200
    assert calls == 0
    assert overridden_body["status"] == "ready"
    assert overridden_body["execution_authorized"] is False
    assert "cellertz-instance-secret" not in overridden_encoded
    assert "/tmp/instance" not in overridden_encoded


def _forge(reply: ReadinessReply, **overrides: object) -> ReadinessReply:
    forged = object.__new__(ReadinessReply)
    for name in reply.__dataclass_fields__:
        object.__setattr__(forged, name, overrides.get(name, getattr(reply, name)))
    return forged


class _FixedReadiness:
    def __init__(self, reply: ReadinessReply) -> None:
        self._reply = reply

    def read(self) -> ReadinessReply:
        return self._reply


def test_transport_rejects_malformed_readiness_reply_objects(tmp_path: Path) -> None:
    document = _manifest()
    digest = _write(tmp_path / "manifest.json", document)
    ready = _load(tmp_path / "manifest.json", CountingProbe(_attestation(document, digest))).read()
    assert ready.status == "ready"
    blocked_checks = tuple(
        ReadinessCheck(
            check.code,
            "blocked" if check.code == "tool_version" else check.status,
            check.text,
        )
        for check in ready.checks
    )
    cases = {
        "ready-without-checks": _forge(ready, checks=()),
        "ready-with-blocked-check": _forge(ready, checks=blocked_checks),
        "blocked-with-every-check-passed": _forge(ready, status="blocked"),
        "invalid-timestamp": _forge(ready, observed_at="1999-01-01T00:00:00Z"),
        "invalid-digest": _forge(ready, manifest_digest=("AB" * 32)),
        "unsafe-text": _forge(
            ready,
            text=(
                "Traceback /tmp/cellertz-secret-path token=cellertz-secret "
                "SELECT cellertz_secret_table cellertz-secret-command"
            ),
        ),
        "duplicate-codes": _forge(ready, checks=(ready.checks[0],) + ready.checks),
        "wrong-types": _forge(ready, tool_version=1, image_digest=False),
        "inconsistent-identity": _forge(
            ready,
            profile_id=PDL1_PROFILE_ID,
            tool_name="mber",
            target_scope="CD22",
        ),
    }
    leaks = (
        "1999-01-01",
        "AB" * 32,
        "cellertz-secret",
        "/tmp/",
        "Traceback",
        "SELECT",
        "mber",
        "CD22",
        "cellertz-secret-command",
    )
    for name, forged in cases.items():
        assert isinstance(forged, ReadinessReply)
        status, body = handle_read_scientific_runner_readiness(
            _FixedReadiness(forged), READINESS_PATH, SESSION
        )
        encoded = json.dumps(body)
        assert status == 200, name
        assert body["status"] == "adapter_failure", name
        assert (
            body["text"]
            == "The scientific runner readiness adapter failed. No scientific action was taken."
        ), name
        assert body["execution_authorized"] is False
        assert body["scientific_execution"] is False
        assert body["scientific_validation"] is False
        assert body["profile_id"] is None
        assert body["tool_name"] is None
        assert body["tool_version"] is None
        assert body["image_digest"] is None
        assert body["manifest_digest"] is None
        assert body["checks"][0]["code"] == "readiness_adapter_failed"
        assert body["checks"][0]["status"] == "blocked"
        for leak in leaks:
            assert leak not in encoded, name


def test_readiness_module_does_not_name_execution_machinery() -> None:
    modules = (
        Path("src/cellertz/application/scientific_runner_readiness.py"),
        Path("src/cellertz/application/scientific_runner_readiness_config.py"),
    )
    for path in modules:
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            "ScientificRunMemory",
            "ApprovedRunWorker",
            "bindcraft_execution",
            "Apptainer",
            "aws_batch",
            "subprocess",
            "Popen",
            "LocalReadinessProbe",
            "CELLERTZ_SCIENTIFIC_RUNNER_PROBE",
        ):
            assert forbidden not in source


def _invoke(handler: type, method: str, path: str, body: bytes) -> tuple[int, dict]:
    instance = handler.__new__(handler)
    instance.path = path
    instance.headers = {"Content-Length": str(len(body))}
    instance.rfile = BytesIO(body)
    instance.wfile = BytesIO()
    instance.status_code = None

    def send_response(code: int) -> None:
        instance.status_code = code

    instance.send_response = send_response
    instance.send_header = lambda *_args: None
    instance.end_headers = lambda: None
    if method == "GET":
        instance.do_GET()
    else:
        instance.do_POST()
    raw = instance.wfile.getvalue()
    payload = json.loads(raw) if raw else {}
    return instance.status_code, payload
