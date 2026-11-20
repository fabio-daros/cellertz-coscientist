"""The settings document today's mBER actually takes.

Built from the campaign specification alone. The prepared structure is an
argument because the specification carries a locator, and a locator is not a
file. Nothing here runs mBER or reads a PDB.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.mber_settings import (
    GENERATOR_VERSION,
    MBER_OPEN_COMMIT,
    mber_command,
    mber_vhh_settings,
    write_mber_vhh_settings,
)
from cellertz.application.vhh_campaign_fixtures import cd22_operational_reference


def versioned():
    """The CD22 campaign with a concrete version, which the fixture lacks.

    The fixture's provenance is ``operational_reference``: it records what was
    run, and stamping today's commit onto it would claim that run used this
    version. A campaign that will run today's mBER records it instead.
    """

    specification = cd22_operational_reference()
    protocol = dataclasses.replace(
        specification.generator_protocol,
        generator_version=GENERATOR_VERSION,
    )
    return dataclasses.replace(
        specification,
        generator_protocol=protocol,
        completeness="complete",
    )


def test_the_pinned_version_is_a_commit_because_the_repository_has_no_tag() -> None:
    assert len(MBER_OPEN_COMMIT) == 40
    assert set(MBER_OPEN_COMMIT) <= set("0123456789abcdef")
    assert GENERATOR_VERSION == f"mber-open@{MBER_OPEN_COMMIT}"
    assert GENERATOR_VERSION.casefold() != "latest"


def test_recording_the_version_completes_the_campaign_but_authorizes_nothing() -> None:
    """The one field that was missing, and the limit of what fixing it buys.

    ``complete`` and ``version_recorded`` are the states 6C.1 defines. Neither
    is approval and neither is an execution.
    """

    specification = versioned()
    assert specification.completeness == "complete"
    assert specification.reproducible_readiness == "version_recorded"
    assert specification.execution_authorized is False
    assert specification.executable is False


def test_every_field_of_the_settings_comes_from_the_specification() -> None:
    specification = versioned()
    settings = mber_vhh_settings(specification, "/runs/attempt-1", "/inputs/cd22_prepared.pdb")
    assert settings["output"] == {"dir": "/runs/attempt-1"}
    assert settings["target"]["pdb"] == "/inputs/cd22_prepared.pdb"
    assert settings["target"]["name"] == "CD22"
    assert settings["target"]["chains"] == "A"
    assert settings["binder"]["masked_sequence"] == specification.scaffold.masked_sequence
    assert settings["stopping"] == {"num_accepted": 5000, "max_trajectories": 50000}
    assert settings["filters"] == {"min_iptm": 0.75, "min_plddt": 0.70}


def test_the_hotspots_are_the_declared_epitope_in_the_prepared_namespace() -> None:
    settings = mber_vhh_settings(versioned(), "/runs/a", "/inputs/t.pdb")
    hotspots = settings["target"]["hotspots"]
    assert hotspots[:4] == ["A624", "A625", "A626", "A627"]
    assert all(label.startswith("A") for label in hotspots)
    assert len(hotspots) == len(versioned().epitope.residues)


def test_a_source_numbered_epitope_is_refused_rather_than_pointed_at_the_wrong_residue() -> None:
    specification = versioned()
    residues = tuple(
        dataclasses.replace(residue, chain_namespace="source", chain_id="U")
        for residue in specification.epitope.residues
    )
    broken = dataclasses.replace(
        specification,
        epitope=dataclasses.replace(specification.epitope, residues=residues),
    )
    with pytest.raises(ApplicationContractError) as error:
        mber_vhh_settings(broken, "/runs/a", "/inputs/t.pdb")
    assert str(error.value) == "epitope_must_use_the_prepared_namespace"


def test_a_campaign_without_a_recorded_version_cannot_produce_settings() -> None:
    with pytest.raises(ApplicationContractError) as error:
        mber_vhh_settings(cd22_operational_reference(), "/runs/a", "/inputs/t.pdb")
    assert str(error.value) == "missing_generator_version"


def test_filters_are_read_from_the_policy_not_defaulted_to_mbers_own() -> None:
    specification = versioned()
    metrics = tuple(
        dataclasses.replace(metric, threshold=0.9) if metric.metric_name == "i_ptm" else metric
        for metric in specification.acceptance_policy.metrics
    )
    stricter = dataclasses.replace(
        specification,
        acceptance_policy=dataclasses.replace(specification.acceptance_policy, metrics=metrics),
    )
    settings = mber_vhh_settings(stricter, "/runs/a", "/inputs/t.pdb")
    assert settings["filters"]["min_iptm"] == 0.9


def test_the_written_file_is_the_document_and_the_argv_names_it(tmp_path) -> None:
    path = write_mber_vhh_settings(versioned(), tmp_path / "out", "/inputs/t.pdb", tmp_path / "settings.json")
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == mber_vhh_settings(versioned(), tmp_path / "out", "/inputs/t.pdb")
    assert mber_command(path) == ["mber-vhh", "--settings", str(path)]


def test_a_specification_for_another_generator_is_refused() -> None:
    specification = versioned()
    other = dataclasses.replace(
        specification,
        generator_protocol=dataclasses.replace(
            specification.generator_protocol,
            generator_name="bindcraft",
        ),
    )
    with pytest.raises(ApplicationContractError) as error:
        mber_vhh_settings(other, "/runs/a", "/inputs/t.pdb")
    assert str(error.value) == "specification_is_not_an_mber_campaign"
