"""Phase 6M.3 captured-byte verification. Temporary synthetic files only."""

from __future__ import annotations

import errno
import hashlib
import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.pdl1_executor import (
    ARGV_POLICY_VERSION,
    DIRECTORY_POLICY,
    ENVIRONMENT_POLICY,
    Pdl1ValidatedInputIdentity,
    register_pdl1_executor,
)
from cellertz.application.pdl1_input_verification import (
    MAX_INPUT_BYTES,
    MAX_JSON_DEPTH,
    Pdl1InputLimits,
    Pdl1InputLocations,
    Pdl1VerifiedInputs,
    verify_pdl1_inputs,
)
from cellertz.application.scientific_run_config import resolve_scientific_run_execution
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    canonical_advanced_profile_bytes,
)
from tests.test_pdl1_executor import Double, _opened

_PROFILE = {"b": 1, "a": 2}
_TARGET = b"synthetic-target"
_SETTINGS = b'{"z":1,"a":2}'


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identity(
    target: bytes = _TARGET,
    settings: bytes = _SETTINGS,
    profile: dict | None = None,
) -> Pdl1ValidatedInputIdentity:
    document = _PROFILE if profile is None else profile
    return Pdl1ValidatedInputIdentity(
        "ab" * 32,
        _sha(target),
        _sha(settings),
        _sha(canonical_advanced_profile_bytes(document)),
        ARGV_POLICY_VERSION,
        DIRECTORY_POLICY,
        ENVIRONMENT_POLICY,
    )


def _limits(**overrides: int) -> Pdl1InputLimits:
    values = {
        "target_structure_bytes": 128,
        "settings_bytes": 128,
        "advanced_profile_bytes": 4096,
        "canonical_advanced_profile_bytes": 4096,
    }
    values.update(overrides)
    return Pdl1InputLimits(**values)


def _write(
    tmp_path: Path,
    target: bytes = _TARGET,
    settings: bytes = _SETTINGS,
    profile: bytes | None = None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if profile is None:
        profile = json.dumps(_PROFILE, separators=(",", ":")).encode()
    paths = {
        "target": tmp_path / "target.bin",
        "settings": tmp_path / "settings.json",
        "profile": tmp_path / "profile.json",
    }
    paths["target"].write_bytes(target)
    paths["settings"].write_bytes(settings)
    paths["profile"].write_bytes(profile)
    locations = Pdl1InputLocations(
        str(paths["target"]), str(paths["settings"]), str(paths["profile"])
    )
    return paths, locations


def _assert_safe(error: ApplicationContractError, *secrets: str) -> None:
    text = f"{error.code} {error}"
    for secret in secrets:
        assert secret not in text


def test_matching_raw_and_canonical_digests(tmp_path: Path) -> None:
    profile = b'{ "b" : 1, "a" : 2 }\n'
    paths, locations = _write(tmp_path, profile=profile)
    snapshot = verify_pdl1_inputs(_identity(), locations, _limits())
    assert isinstance(snapshot, Pdl1VerifiedInputs)
    assert snapshot.target_structure == _TARGET
    assert snapshot.settings == _SETTINGS
    assert snapshot.target_structure_sha256 == _sha(_TARGET)
    assert snapshot.settings_sha256 == _sha(_SETTINGS)
    assert snapshot.advanced_profile_canonical == canonical_advanced_profile_bytes(_PROFILE)
    assert snapshot.advanced_profile_sha256 == _sha(snapshot.advanced_profile_canonical)
    assert snapshot.advanced_profile_canonical != profile
    assert snapshot.target_structure_size == len(_TARGET)
    assert paths["target"].name not in repr(snapshot)
    assert _TARGET not in repr(snapshot).encode()


def test_settings_whitespace_changes_identity_and_profile_order_does_not(tmp_path: Path) -> None:
    _paths, locations = _write(tmp_path, settings=b'{ "z" : 1, "a" : 2 }')
    with pytest.raises(ApplicationContractError) as settings_error:
        verify_pdl1_inputs(_identity(), locations, _limits())
    assert settings_error.value.code == "pdl1_input_digest_mismatch"
    profile = b'{"b":1,"a":2}'
    paths, ordered = _write(tmp_path / "ordered", profile=profile)
    snapshot = verify_pdl1_inputs(_identity(), ordered, _limits())
    assert snapshot.advanced_profile_canonical == canonical_advanced_profile_bytes(_PROFILE)
    paths["profile"].write_bytes(b'{"a":2,"b":1}')
    assert snapshot.advanced_profile_canonical == canonical_advanced_profile_bytes(_PROFILE)


@pytest.mark.parametrize("kind", ["target", "settings", "profile"])
def test_each_input_mutation_is_rejected(tmp_path: Path, kind: str) -> None:
    paths, locations = _write(tmp_path)
    if kind == "target":
        paths["target"].write_bytes(_TARGET + b"x")
    elif kind == "settings":
        paths["settings"].write_bytes(_SETTINGS + b" ")
    else:
        paths["profile"].write_bytes(b'{"a":3,"b":1}')
    with pytest.raises(ApplicationContractError) as error:
        verify_pdl1_inputs(_identity(), locations, _limits())
    assert error.value.code == "pdl1_input_digest_mismatch"
    _assert_safe(error.value, paths["target"].name, "synthetic-target")


def test_missing_empty_and_oversized_inputs(tmp_path: Path) -> None:
    _paths, locations = _write(tmp_path)
    missing = Pdl1InputLocations(
        str(tmp_path / "absent.bin"), locations.settings, locations.advanced_profile
    )
    with pytest.raises(ApplicationContractError) as absent:
        verify_pdl1_inputs(_identity(), missing, _limits())
    assert absent.value.code == "pdl1_input_unreadable"
    empty_paths, empty_locations = _write(tmp_path / "empty", target=b"")
    with pytest.raises(ApplicationContractError) as empty:
        verify_pdl1_inputs(_identity(target=b"x"), empty_locations, _limits())
    assert empty.value.code == "pdl1_input_empty"
    _assert_safe(empty.value, empty_paths["target"].name)
    big, big_locations = _write(tmp_path / "big", target=b"12345")
    with pytest.raises(ApplicationContractError) as oversized:
        verify_pdl1_inputs(
            _identity(target=b"12345"), big_locations, _limits(target_structure_bytes=4)
        )
    assert oversized.value.code == "pdl1_input_too_large"
    _assert_safe(oversized.value, big["target"].name, "12345")


def test_symlink_directory_and_fifo_are_not_regular(tmp_path: Path) -> None:
    paths, locations = _write(tmp_path)
    link = tmp_path / "linked"
    link.symlink_to(paths["target"])
    linked = Pdl1InputLocations(str(link), locations.settings, locations.advanced_profile)
    with pytest.raises(ApplicationContractError) as symlink:
        verify_pdl1_inputs(_identity(), linked, _limits())
    assert symlink.value.code == "pdl1_input_not_regular"
    _assert_safe(symlink.value, "linked", paths["target"].name)
    directory = tmp_path / "directory"
    directory.mkdir()
    nested = Pdl1InputLocations(str(directory), locations.settings, locations.advanced_profile)
    with pytest.raises(ApplicationContractError) as folder:
        verify_pdl1_inputs(_identity(), nested, _limits())
    assert folder.value.code == "pdl1_input_not_regular"
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    piped = Pdl1InputLocations(str(fifo), locations.settings, locations.advanced_profile)
    with pytest.raises(ApplicationContractError) as pipe:
        verify_pdl1_inputs(_identity(), piped, _limits())
    assert pipe.value.code == "pdl1_input_not_regular"
    _assert_safe(pipe.value, "fifo")


def test_malformed_locations_and_limits_fail_before_reading(tmp_path: Path) -> None:
    path = tmp_path / "target.bin"
    path.write_bytes(_TARGET)
    with pytest.raises(ApplicationContractError) as relative:
        Pdl1InputLocations("target.bin", str(path), str(path))
    assert relative.value.code == "pdl1_input_location_invalid"
    with pytest.raises(ApplicationContractError) as nul:
        Pdl1InputLocations(str(path) + "\0", str(path), str(path))
    assert nul.value.code == "pdl1_input_location_invalid"
    assert "target.bin" not in str(nul.value)
    with pytest.raises(ApplicationContractError) as boolean:
        Pdl1InputLimits(True, 8, 8, 8)  # type: ignore[arg-type]
    assert boolean.value.code == "pdl1_input_limit_invalid"
    with pytest.raises(ApplicationContractError) as zero:
        Pdl1InputLimits(0, 8, 8, 8)
    assert zero.value.code == "pdl1_input_limit_invalid"
    with pytest.raises(ApplicationContractError) as huge:
        Pdl1InputLimits(MAX_INPUT_BYTES + 1, 8, 8, 8)
    assert huge.value.code == "pdl1_input_limit_invalid"


def test_stat_size_does_not_decide_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.fstat

    def lying(descriptor: int):
        info = original(descriptor)
        values = list(info)
        values[6] = 0
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", lying)
    _paths, locations = _write(tmp_path)
    snapshot = verify_pdl1_inputs(_identity(), locations, _limits())
    assert snapshot.target_structure == _TARGET
    big, big_locations = _write(tmp_path / "big", target=b"abcdef")
    with pytest.raises(ApplicationContractError) as error:
        verify_pdl1_inputs(
            _identity(target=b"abcdef"), big_locations, _limits(target_structure_bytes=4)
        )
    assert error.value.code == "pdl1_input_too_large"
    _assert_safe(error.value, big["target"].name)


def test_advanced_profile_rejects_unsafe_json(tmp_path: Path) -> None:
    cases = {
        "duplicate": b'{"a":1,"a":2}',
        "utf8": b"\xff",
        "nan": b'{"a":NaN}',
        "huge": b'{"a":1e309}',
        "list": b"[1]",
        "null": b"null",
        "deep": ('{"a":' * (MAX_JSON_DEPTH + 1) + "1" + "}" * (MAX_JSON_DEPTH + 1)).encode(),
    }
    for name, payload in cases.items():
        _paths, locations = _write(tmp_path / name, profile=payload)
        with pytest.raises(ApplicationContractError) as error:
            verify_pdl1_inputs(_identity(), locations, _limits())
        assert error.value.code == "pdl1_advanced_profile_invalid"
        _assert_safe(error.value, name, "NaN")


def test_canonical_profile_limit_is_enforced(tmp_path: Path) -> None:
    profile = {f"key{index:04d}": index for index in range(80)}
    raw = json.dumps(profile, separators=(",", ":"), sort_keys=True).encode()
    canonical = canonical_advanced_profile_bytes(profile)
    assert len(canonical) > len(raw)
    _paths, locations = _write(tmp_path, profile=raw)
    identity = _identity(profile=profile)
    with pytest.raises(ApplicationContractError) as error:
        verify_pdl1_inputs(
            identity,
            locations,
            _limits(
                advanced_profile_bytes=len(raw),
                canonical_advanced_profile_bytes=len(canonical) - 1,
            ),
        )
    assert error.value.code == "pdl1_canonical_profile_too_large"


def test_snapshot_keeps_captured_bytes_after_source_replacement(tmp_path: Path) -> None:
    paths, locations = _write(tmp_path)
    snapshot = verify_pdl1_inputs(_identity(), locations, _limits())
    paths["target"].write_bytes(b"replaced-target")
    paths["settings"].write_bytes(b'{"replaced":true}')
    paths["profile"].write_bytes(b'{"replaced":true}')
    assert snapshot.target_structure == _TARGET
    assert snapshot.settings == _SETTINGS
    assert snapshot.advanced_profile_canonical == canonical_advanced_profile_bytes(_PROFILE)
    with pytest.raises(FrozenInstanceError):
        snapshot.target_structure = b"other"  # type: ignore[misc]
    with pytest.raises(ApplicationContractError) as error:
        Pdl1VerifiedInputs(
            "ab" * 32,
            "cd" * 32,
            "ef" * 32,
            "12" * 32,
            ARGV_POLICY_VERSION,
            DIRECTORY_POLICY,
            ENVIRONMENT_POLICY,
            1,
            1,
            1,
            b"x",
            b"y",
            b"z",
        )
    assert error.value.code == "pdl1_verified_inputs_invalid"
    assert not hasattr(snapshot, "parsed")


def test_failures_close_descriptors(tmp_path: Path) -> None:
    before = set(os.listdir("/dev/fd"))
    paths, locations = _write(tmp_path, target=b"")
    with pytest.raises(ApplicationContractError):
        verify_pdl1_inputs(_identity(), locations, _limits())
    link = tmp_path / "linked"
    link.symlink_to(paths["settings"])
    with pytest.raises(ApplicationContractError):
        verify_pdl1_inputs(
            _identity(),
            Pdl1InputLocations(str(link), locations.settings, locations.advanced_profile),
            _limits(),
        )
    assert set(os.listdir("/dev/fd")) == before


def test_unsupported_open_flags_reject_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paths, locations = _write(tmp_path)
    monkeypatch.setattr(
        "cellertz.application.pdl1_input_verification._flags",
        lambda: None,
    )
    with pytest.raises(ApplicationContractError) as error:
        verify_pdl1_inputs(_identity(), locations, _limits())
    assert error.value.code == "pdl1_input_verification_unsupported"


def _snapshot_fields(**overrides: object) -> dict[str, object]:
    canonical = canonical_advanced_profile_bytes(_PROFILE)
    values: dict[str, object] = {
        "manifest_digest": "ab" * 32,
        "target_structure_sha256": _sha(_TARGET),
        "settings_sha256": _sha(_SETTINGS),
        "advanced_profile_sha256": _sha(canonical),
        "argv_policy_version": ARGV_POLICY_VERSION,
        "directory_policy": DIRECTORY_POLICY,
        "environment_policy": ENVIRONMENT_POLICY,
        "target_structure_size": len(_TARGET),
        "settings_size": len(_SETTINGS),
        "advanced_profile_canonical_size": len(canonical),
        "target_structure": _TARGET,
        "settings": _SETTINGS,
        "advanced_profile_canonical": canonical,
    }
    values.update(overrides)
    return values


def test_valid_immutable_bytes_remain_accepted() -> None:
    snapshot = Pdl1VerifiedInputs(**_snapshot_fields())  # type: ignore[arg-type]
    assert type(snapshot.target_structure) is bytes
    assert snapshot.target_structure == _TARGET
    assert snapshot.settings_size == len(_SETTINGS)


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_structure": bytearray(_TARGET)},
        {"settings": bytearray(_SETTINGS)},
        {
            "advanced_profile_canonical": bytearray(canonical_advanced_profile_bytes(_PROFILE)),
        },
        {"target_structure": memoryview(_TARGET)},
        {"settings": "settings"},
        {"advanced_profile_canonical": None},
        {
            "target_structure": b"x",
            "target_structure_size": True,
            "target_structure_sha256": _sha(b"x"),
        },
        {
            "settings": b"y",
            "settings_size": 1.0,
            "settings_sha256": _sha(b"y"),
        },
        {"advanced_profile_canonical_size": False},
        {"target_structure": b"", "target_structure_size": 0, "target_structure_sha256": _sha(b"")},
        {
            "target_structure": b"x" * (MAX_INPUT_BYTES + 1),
            "target_structure_size": MAX_INPUT_BYTES + 1,
            "target_structure_sha256": _sha(b"x" * (MAX_INPUT_BYTES + 1)),
        },
    ],
)
def test_malformed_snapshot_payloads_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ApplicationContractError) as error:
        Pdl1VerifiedInputs(**_snapshot_fields(**overrides))  # type: ignore[arg-type]
    assert error.value.code == "pdl1_verified_inputs_invalid"
    assert error.value.__cause__ is None
    rendered = f"{error.value} {error.value.code}"
    assert "bytearray" not in rendered
    assert "secret" not in rendered


def _failing_close(original, marker: str):
    def close(descriptor: int) -> None:
        original(descriptor)
        raise OSError(errno.EIO, marker, "/tmp/secret-marker")

    return close


def _driver_error(_descriptor: int, _size: int = 0) -> None:
    raise OSError(errno.EIO, "secret-marker", "/tmp/secret-marker")


def test_filesystem_failures_stay_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, locations = _write(tmp_path)
    original_close = os.close

    def check(code: str, **patches: object) -> None:
        before = set(os.listdir("/dev/fd"))
        for name, function in patches.items():
            monkeypatch.setattr(os, name, function)
        try:
            with pytest.raises(ApplicationContractError) as error:
                verify_pdl1_inputs(_identity(), locations, _limits())
        finally:
            monkeypatch.undo()
        assert error.value.code == code
        assert error.value.__cause__ is None
        _assert_safe(error.value, "secret-marker", paths["target"].name, "Input/output")
        assert set(os.listdir("/dev/fd")) == before

    check("pdl1_input_unreadable", fstat=_driver_error)
    check("pdl1_input_unreadable", read=_driver_error)
    check("pdl1_input_unreadable", close=_failing_close(original_close, "secret-marker"))
    check(
        "pdl1_input_unreadable",
        fstat=_driver_error,
        close=_failing_close(original_close, "secret-marker"),
    )


def test_close_failure_keeps_a_specific_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paths, locations = _write(tmp_path, target=b"")
    monkeypatch.setattr(os, "close", _failing_close(os.close, "close secret-marker"))
    before = set(os.listdir("/dev/fd"))
    with pytest.raises(ApplicationContractError) as error:
        verify_pdl1_inputs(_identity(), locations, _limits())
    assert error.value.code == "pdl1_input_empty"
    assert error.value.__cause__ is None
    _assert_safe(error.value, "secret-marker", "Input/output")
    assert set(os.listdir("/dev/fd")) == before


def test_production_registration_stays_blocked(tmp_path: Path) -> None:
    _authorizations, _granted, path, snapshot, spec = _opened(tmp_path)
    registration = register_pdl1_executor(spec, str(path), snapshot, Double())
    assert registration.code == "pdl1_input_identity_unbound"
    assert registration.executor is None
    source = Path("src/cellertz/conversation/__main__.py").read_text(encoding="utf-8")
    assert "verify_pdl1_inputs" not in source
    assert "pdl1_input_verification" not in source
    assert resolve_scientific_run_execution({}) == "disabled"
