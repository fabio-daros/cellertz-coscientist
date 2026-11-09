"""Phase 6M.2 manifest v2. Synthetic identities and runtime doubles only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
from pathlib import Path

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.pdl1_executor import (
    Pdl1InputBinding,
    Pdl1ValidatedInputIdentity,
    register_pdl1_executor,
)
from cellertz.application.scientific_authorization import (
    ScientificAuthorizationService,
    binding_document,
    binding_hash_for,
)
from cellertz.application.scientific_run import (
    FAILED_TEXT,
    InMemoryScientificRunStore,
    ScientificRunService,
)
from cellertz.application.scientific_run_config import resolve_scientific_run_execution
from cellertz.application.scientific_runner_readiness import (
    CD22_PROFILE_ID,
    TACI_PROFILE_ID,
    ProbeObservation,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    canonical_advanced_profile_bytes,
)
from tests.test_approved_run import _created
from tests.test_pdl1_executor import Double, _opened, _specification
from tests.test_scientific_authorization import SESSION, WHEN, _authorize, _ready_readiness
from tests.test_scientific_runner_readiness import (
    CountingProbe,
    _attestation,
    _load,
    _manifest,
    _write,
)

_TARGET = hashlib.sha256(b"synthetic-target").hexdigest()
_SETTINGS = hashlib.sha256(b"synthetic-settings").hexdigest()
_PROFILE = hashlib.sha256(
    canonical_advanced_profile_bytes({"limit": 1, "name": "synthetic"})
).hexdigest()


def _identity(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "advanced_profile_sha256": _PROFILE,
        "argv_policy_version": "server-constructed-argv-v1",
        "directory_policy": "server-owned-per-attempt",
        "environment_policy": "empty-explicit",
        "settings_sha256": _SETTINGS,
        "target_structure_sha256": _TARGET,
    }
    values.update(overrides)
    return values


def _v2(**overrides: object) -> dict:
    document = _manifest()
    document["schema_version"] = "scientific-runner-manifest-v2"
    document["input_identity"] = _identity()
    document.update(overrides)
    return document


def _ready(tmp_path: Path, document: dict):
    path = tmp_path / "manifest.json"
    digest = _write(path, document)
    reply = _load(path, CountingProbe(_attestation(document, digest))).read()
    return path, digest, reply


def _spec(path: Path, observed_at: str, document: dict):
    identity = document["input_identity"]
    return _specification(
        path,
        observed_at,
        target_structure_sha256=identity["target_structure_sha256"],
        settings_sha256=identity["settings_sha256"],
        advanced_profile_sha256=identity["advanced_profile_sha256"],
    )


def _code(tmp_path: Path, document: dict) -> str:
    path = tmp_path / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, document)
    reply = _load(path, CountingProbe()).read()
    assert reply.checks[0].status == "blocked"
    return reply.checks[0].code


def test_canonical_v2_manifest_is_ready_and_digest_covers_inputs(tmp_path: Path) -> None:
    path, digest, reply = _ready(tmp_path, _v2())
    assert reply.status == "ready"
    assert reply.manifest_digest == digest
    assert reply.execution_authorized is False
    changed = _v2()
    changed["input_identity"] = _identity(target_structure_sha256="ab" * 32)
    other = _write(tmp_path / "other.json", changed)
    assert other != digest
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "field", ["target_structure_sha256", "settings_sha256", "advanced_profile_sha256"]
)
def test_each_input_digest_changes_manifest_identity(tmp_path: Path, field: str) -> None:
    original = _write(tmp_path / "original.json", _v2())
    changed = _v2()
    changed["input_identity"] = _identity(**{field: "cd" * 32})
    assert _write(tmp_path / "changed.json", changed) != original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("argv_policy_version", "server-constructed-argv-v2"),
        ("directory_policy", "client-selected"),
        ("environment_policy", "inherit-parent"),
    ],
)
def test_policy_version_mutations_are_rejected(tmp_path: Path, field: str, value: str) -> None:
    document = _v2()
    document["input_identity"] = _identity(**{field: value})
    assert _code(tmp_path, document) == "unsupported_policy"


def test_v1_is_not_reinterpreted_as_input_identity(tmp_path: Path) -> None:
    path, _digest, reply = _ready(tmp_path, _manifest())
    assert reply.status == "ready"
    assert "input_identity" not in json.loads(path.read_text(encoding="utf-8"))
    _authorizations, _granted, registered, snapshot, spec = _opened(tmp_path / "v1")
    registration = register_pdl1_executor(spec, str(registered), snapshot, Double())
    assert registration.code == "pdl1_input_identity_unbound"
    assert registration.executor is None
    assert registration.input_identity is None


def test_malformed_v2_documents_fail_closed(tmp_path: Path) -> None:
    missing = _v2()
    del missing["input_identity"]
    assert _code(tmp_path / "missing", missing) == "manifest_invalid"
    unknown = _v2()
    unknown["command"] = "bindcraft"
    assert _code(tmp_path / "unknown", unknown) == "manifest_invalid"
    nested = _v2()
    nested["input_identity"] = _identity(note="extra")
    assert _code(tmp_path / "nested", nested) == "manifest_invalid"
    typed = _v2()
    typed["resource_envelope"] = dict(typed["resource_envelope"])
    typed["resource_envelope"]["cpu_count"] = True
    assert _code(tmp_path / "bool", typed) == "invalid_manifest_type"
    bad_digest = _v2()
    bad_digest["input_identity"] = _identity(target_structure_sha256="AB" * 32)
    assert _code(tmp_path / "digest", bad_digest) == "manifest_invalid"
    limits = _v2()
    limits["timeout_seconds"] = 120
    limits["resource_envelope"] = dict(limits["resource_envelope"])
    limits["resource_envelope"]["timeout_seconds"] = 121
    assert _code(tmp_path / "limits", limits) == "manifest_invalid"
    for profile in (CD22_PROFILE_ID, TACI_PROFILE_ID):
        blocked = _v2(profile_id=profile, target_scope=profile.split("-")[0].upper())
        assert _code(tmp_path / profile, blocked) == "unsupported_profile"


def test_duplicate_keys_nonfinite_utf8_and_size_are_rejected(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":"scientific-runner-manifest-v1","schema_version":"x"}', encoding="utf-8"
    )
    assert _load(duplicate, CountingProbe()).read().checks[0].code == "manifest_not_canonical"
    nested = tmp_path / "nested.json"
    nested.write_text('{"input_identity":{"a":1,"a":1}}', encoding="utf-8")
    assert _load(nested, CountingProbe()).read().checks[0].code == "manifest_not_canonical"
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"timeout_seconds":NaN}', encoding="utf-8")
    assert _load(nonfinite, CountingProbe()).read().checks[0].code == "manifest_invalid"
    huge = tmp_path / "huge.json"
    huge.write_bytes(b"{" + b" " * 70000)
    assert _load(huge, CountingProbe()).read().checks[0].code == "manifest_too_large"
    raw = tmp_path / "raw.json"
    raw.write_bytes(b"\xff")
    assert _load(raw, CountingProbe()).read().checks[0].code == "manifest_invalid"


def test_observation_must_match_the_complete_v2_digest(tmp_path: Path) -> None:
    document = _v2()
    path = tmp_path / "manifest.json"
    digest = _write(path, document)
    matched = _load(path, CountingProbe(_attestation(document, digest))).read()
    assert matched.status == "ready"
    assert matched.manifest_digest == digest
    stale = _attestation(document, "ee" * 32)
    mismatched = _load(path, CountingProbe(stale)).read()
    assert mismatched.status == "blocked"
    assert mismatched.checks[-1].code == "readiness_probe_mismatch"
    assert mismatched.manifest_digest == digest


def test_specification_mismatch_rejects_registration(tmp_path: Path) -> None:
    document = _v2()
    path, digest, reply = _ready(tmp_path, document)
    spec = _spec(path, reply.observed_at, document)
    shifted = replace(spec, target_structure_sha256="ab" * 32, manifest_digest=digest)
    registration = register_pdl1_executor(shifted, str(path), reply, Double())
    assert registration.code == "pdl1_execution_policy_mismatch"
    assert registration.field == "target_structure_sha256"
    assert registration.executor is None


def test_matching_v2_identity_does_not_register_an_executor(tmp_path: Path) -> None:
    document = _v2()
    path, digest, reply = _ready(tmp_path, document)
    spec = _spec(path, reply.observed_at, document)
    binding = Pdl1InputBinding(digest, _TARGET, _SETTINGS, _PROFILE)
    registration = register_pdl1_executor(spec, str(path), reply, Double(), input_binding=binding)
    assert registration.code == "pdl1_production_registration_deferred"
    assert registration.accepted is False
    assert registration.executor is None
    identity = registration.input_identity
    assert isinstance(identity, Pdl1ValidatedInputIdentity)
    assert identity.manifest_digest == digest
    assert identity.target_structure_sha256 == _TARGET
    assert identity.settings_sha256 == _SETTINGS
    assert identity.advanced_profile_sha256 == _PROFILE
    names = {item.name for item in fields(identity)}
    assert "request_digest" not in names
    assert "files_verified" not in names
    with pytest.raises(AttributeError):
        identity.target_structure_sha256 = "ff" * 32  # type: ignore[misc]
    with pytest.raises(ApplicationContractError) as error:
        Pdl1ValidatedInputIdentity(
            "zz" * 32,
            _TARGET,
            _SETTINGS,
            _PROFILE,
            "server-constructed-argv-v1",
            "server-owned-per-attempt",
            "empty-explicit",
        )
    assert error.value.code == "pdl1_input_identity_invalid"


def test_authorization_binding_follows_the_v2_digest(tmp_path: Path) -> None:
    document = _v2()
    finished, runs, _runner, gate, drafter, approval = _created()
    readiness, _probe = _ready_readiness(tmp_path, document=document)
    authorizations = ScientificAuthorizationService(
        gate._sessions,
        drafter,
        gate,
        runs,
        readiness,
        clock=lambda: WHEN,
        authorization_ids=lambda: "authorization_" + "ab" * 16,
    )
    granted = _authorize(authorizations, approval.approval_id)
    assert granted.authorization is not None
    digest = granted.authorization.manifest_digest
    assert digest == hashlib.sha256((tmp_path / "manifest.json").read_bytes()).hexdigest()
    assert "target_structure_sha256" not in binding_document(granted.authorization)
    assert granted.authorization.binding_hash == binding_hash_for(granted.authorization)
    mutated = _v2()
    mutated["input_identity"] = _identity(settings_sha256="11" * 32)
    shifted, _probe = _ready_readiness(tmp_path / "shift", document=mutated, when=WHEN)
    assert shifted.read().manifest_digest != digest
    executor = Double()
    store = InMemoryScientificRunStore()
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "ab" * 16,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-v2",
    )
    assert created.run is not None
    assert created.run.manifest_digest == digest
    assert created.run.status == "queued"
    authorizations._readiness = shifted
    failed = service.process_next()
    assert failed is not None
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    assert failed.manifest_digest == digest
    assert executor.calls == 0
    assert finished.run.status == "completed"
    store.close()


def test_production_composition_remains_unavailable() -> None:
    source = Path("src/cellertz/conversation/__main__.py").read_text(encoding="utf-8")
    assert "register_pdl1_executor" not in source
    assert "validate_pdl1_input_identity" not in source
    assert "scientific-runner-manifest-v2" not in source
    assert resolve_scientific_run_execution({}) == "disabled"
    assert ProbeObservation("not_recorded").ready is None
