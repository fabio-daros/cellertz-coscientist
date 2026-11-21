"""Which input roles a root has to verify, and who decides.

``settings.json`` and ``advanced.json`` are BindCraft's configuration. A
generator that writes its own settings reads neither, so requiring them would
mean verifying bytes nothing opens — and the CD22 root holds only the prepared
target. The launcher declares what it needs, as it declares which image it
runs, and the three-role path is left exactly as it was.
"""

from __future__ import annotations

import hashlib

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.generation_plan import (
    GENERATOR_INPUT_SCHEMA,
    open_registered_input_catalog,
)
from cellertz.application.mber_demonstration import MberDemonstrationLauncher
from cellertz.application.pdl1_selection import SELECTION_SCHEMA

MBER_ROLES = MberDemonstrationLauncher.required_input_roles
TARGET = b"ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n"


def _root(tmp_path, *, full: bool):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "target.pdb").write_bytes(TARGET)
    if full:
        (root / "settings.json").write_bytes(b'{"chain": "A"}\n')
        (root / "advanced.json").write_bytes(b'{"k": 1}\n')
    return root


def _catalog(root, roles):
    return open_registered_input_catalog(
        {"CELLERTZ_PDL1_INPUT_ROOT": str(root)}, required_roles=roles
    )


def test_a_target_only_root_verifies_for_the_generator_that_needs_only_it(tmp_path) -> None:
    catalog = _catalog(_root(tmp_path, full=False), MBER_ROLES)

    assert catalog.registration_status() == "verified"
    identities = catalog.identities()
    assert identities is not None
    assert identities["input_selection"]["target_structure_sha256"] == hashlib.sha256(TARGET).hexdigest()
    assert [artifact["role"] for artifact in identities["artifacts"]] == ["target_structure"]


def test_the_same_root_stays_unverified_for_a_generator_that_needs_all_three(tmp_path) -> None:
    """BindCraft cannot run from a root that has no settings or profile."""

    catalog = _catalog(_root(tmp_path, full=False), None)

    assert catalog.registration_status() == "input_files_unverified"
    assert catalog.identities() is None
    assert catalog.verified_paths() is None


def test_the_three_role_path_is_unchanged(tmp_path) -> None:
    catalog = _catalog(_root(tmp_path, full=True), None)

    identities = catalog.identities()
    assert identities is not None
    assert identities["input_selection"]["schema"] == SELECTION_SCHEMA
    assert len(identities["artifacts"]) == 3


def test_the_two_selection_schemas_can_never_compare_equal() -> None:
    """input_selection is compared for equality to decide identity."""

    assert GENERATOR_INPUT_SCHEMA != SELECTION_SCHEMA


def test_the_target_role_cannot_be_dropped() -> None:
    """Settings are a generator's business; the structure is the science."""

    for roles in ((), ("settings",), ("settings", "advanced_profile")):
        with pytest.raises(ApplicationContractError) as refused:
            open_registered_input_catalog({"CELLERTZ_PDL1_INPUT_ROOT": "/tmp"}, required_roles=roles)
        assert "must_include_target" in str(refused.value)


def test_an_unknown_role_name_does_not_widen_anything(tmp_path) -> None:
    catalog = _catalog(_root(tmp_path, full=False), ("target_structure", "something_else"))

    identities = catalog.identities()
    assert identities is not None
    assert [artifact["role"] for artifact in identities["artifacts"]] == ["target_structure"]


def test_bytes_that_change_between_the_two_reads_are_refused(tmp_path, monkeypatch) -> None:
    """The one guarantee verify_pdl1_inputs adds over hashing once."""

    from cellertz.application import generation_plan

    root = _root(tmp_path, full=False)
    reads = []
    real = generation_plan._read_regular

    def _swapped(path, flags):
        reads.append(path)
        return real(path, flags) if len(reads) == 1 else b"DIFFERENT BYTES\n"

    monkeypatch.setattr(generation_plan, "_read_regular", _swapped)

    assert _catalog(root, MBER_ROLES).identities() is None
    assert len(reads) == 2, "the file has to be read twice for the check to exist"


def test_an_unconfigured_root_still_reports_its_reason(tmp_path) -> None:
    for environ, expected in (
        ({}, "input_root_unconfigured"),
        ({"CELLERTZ_PDL1_INPUT_ROOT": "relative/path"}, "input_root_invalid"),
    ):
        catalog = open_registered_input_catalog(environ, required_roles=MBER_ROLES)
        assert catalog.registration_status() == expected
