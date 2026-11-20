"""Settings for today's mBER, built from a VHH campaign specification.

``mber-vhh --settings <file>`` is how the public tool takes a whole campaign.
Nothing here ever wrote that file: ``MBERAdapter`` passes a path from
``constraints.settings`` and no caller created one, which is why the generator
has never run outside its own tests.

This module builds the settings document and nothing else. It does not run
mBER, read or write a PDB, prepare a target, install anything, or authorize an
execution. The prepared structure arrives as an argument because the
specification carries a locator, and 6C.1 is explicit that a locator is not the
scientific identity of a file.

The pinned version below is recorded the way ``pdl1_image_readiness`` pins
BindCraft: naming a version is not installing it. The public repository carries
no tag or release, so a commit is the only concrete version that exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.vhh_campaign import VHHCampaignSpecification

MBER_OPEN_REPOSITORY = "https://github.com/manifoldbio/mber-open"
# Read from the GitHub API on 2026-11-20. The repository has no tags and no
# releases, so this commit is the version. "latest" is rejected by contract.
MBER_OPEN_COMMIT = "5cc8e0534158647e9706b2655630f4449686c600"
MBER_OPEN_COMMITTED_AT = "2026-10-21T22:37:33Z"
MBER_VHH_COMMAND = "mber-vhh"
GENERATOR_VERSION = f"mber-open@{MBER_OPEN_COMMIT}"
# README: tested on A10G, A100, L4, L40S and H100; at least 32 GB of VRAM
# recommended. Not a promise that a smaller card fails.
RECOMMENDED_VRAM_GB = 32
_FILTER_KEYS = {"i_ptm": "min_iptm", "plddt": "min_plddt"}


def mber_vhh_settings(
    specification: VHHCampaignSpecification,
    output_dir: str | Path,
    prepared_pdb: str | Path,
) -> dict[str, Any]:
    """The settings document ``mber-vhh --settings`` consumes.

    Every value comes from the specification. The filters are read from the
    acceptance policy by metric name rather than defaulted, because mBER's own
    defaults and a campaign's recorded thresholds are different claims that
    happen to agree today.
    """

    structure = specification.target_structure
    epitope = specification.epitope
    scaffold = specification.scaffold
    protocol = specification.generator_protocol
    acceptance = specification.acceptance_policy
    stopping = specification.stopping_policy
    if structure is None or scaffold is None or acceptance is None or stopping is None:
        raise ApplicationContractError("incomplete_specification_for_settings")
    if protocol is None or protocol.generator_name != "mber":
        raise ApplicationContractError("specification_is_not_an_mber_campaign")
    if protocol.generator_version is None:
        # The one field 6C.1 requires before a reproducible run, and the
        # manifest is not allowed to supply it.
        raise ApplicationContractError("missing_generator_version")
    chains = "".join(structure.prepared_target_chain_ids)
    if not chains:
        raise ApplicationContractError("missing_prepared_target_chain")
    settings: dict[str, Any] = {
        "output": {"dir": str(output_dir)},
        "target": {
            "pdb": str(prepared_pdb),
            "name": structure.target_name,
            "chains": chains,
            "hotspots": _hotspots(epitope),
        },
        "binder": {"masked_sequence": scaffold.masked_sequence},
        "stopping": {
            "num_accepted": stopping.accepted_design_target,
            "max_trajectories": stopping.max_trajectories,
        },
        "filters": _filters(acceptance),
    }
    return settings


def write_mber_vhh_settings(
    specification: VHHCampaignSpecification,
    output_dir: str | Path,
    prepared_pdb: str | Path,
    path: str | Path,
) -> Path:
    """Write the settings as JSON, which the tool reads as well as YAML.

    shortcut: JSON avoids a YAML dependency for one document; if a build of
    mBER turns out to select its parser by file extension, name the file
    ``.yml`` and emit the same content.
    """

    target = Path(path)
    document = mber_vhh_settings(specification, output_dir, prepared_pdb)
    target.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    return target


def mber_command(settings_path: str | Path, binary: str = MBER_VHH_COMMAND) -> list[str]:
    """The argv for a registered profile. The caller owns the binary's path."""

    return [str(binary), "--settings", str(settings_path)]


def _hotspots(epitope: Any) -> list[str]:
    """Epitope residues as mBER writes them, e.g. ``A624``.

    The atlas says a hotspot list does not steer mBER, so these are declared
    for the audit to measure afterwards, not because they will be obeyed.
    """

    if epitope is None:
        return []
    labels = []
    for residue in epitope.residues:
        if residue.chain_namespace != "prepared":
            # mBER reads the prepared file; a source-numbered residue would
            # point at a different position in it.
            raise ApplicationContractError("epitope_must_use_the_prepared_namespace")
        insertion = residue.insertion_code or ""
        labels.append(f"{residue.chain_id}{residue.residue_number}{insertion}")
    return labels


def _filters(acceptance: Any) -> dict[str, float]:
    filters: dict[str, float] = {}
    for metric in acceptance.metrics:
        key = _FILTER_KEYS.get(metric.metric_name)
        if key is None:
            continue
        if metric.direction != "gte":
            # mBER takes these as minima only.
            raise ApplicationContractError("generator_filter_must_be_a_minimum")
        filters[key] = metric.threshold
    if not filters:
        raise ApplicationContractError("no_generator_filter_recorded")
    return filters
