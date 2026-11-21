"""Execute the preparation a campaign specification already records.

``TargetPreparationStep`` named six operations and nothing ever performed one:
the steps were a record of what someone did by hand. So the recorded
preparation and the file on disk could disagree and nothing would say so.

This module takes the steps from the specification and runs exactly those, in
the recorded order. It does not decide what preparation a target needs — that
is the specification's claim — and it refuses an operation it cannot perform
rather than skipping it, because a step that silently did nothing turns the
record into fiction.

Transforms are at the text level, on columns, not through a structure parser.
Two reasons. The campaigns are explicit that residue numbers were measured on
the original chain and **kept** after relabelling, and a parser that rewrites
a file renumbers and reorders it. And the registered PD-L1 target the platform
already verified is bare ``ATOM`` records with no header, so emitting the same
shape keeps one convention rather than two.

PDB column positions, 0-based slices, from the format specification:
``resName[17:20]``, ``chainID[21:22]``, ``resSeq[22:26]``, ``segID[72:76]``.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from typing import Any

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.vhh_campaign import (
    VHHCampaignSpecification,
    validate_preparation,
)

# Water, by the names the PDB uses for it. One list, shared with the contact
# measurement: a water this strips has to be the same water that cannot make a
# contact, or the prepared file and the classification would disagree about
# what is in the structure.
from cellertz.domains.protein_design.scoring.partner_classification import SOLVENT_RESIDUES

_COORDINATE_RECORDS = ("ATOM  ", "HETATM")


def _is_coordinate(line: str) -> bool:
    return line.startswith(_COORDINATE_RECORDS) and len(line) >= 54


def _resname(line: str) -> str:
    return line[17:20].strip().upper()


def _chain(line: str) -> str:
    return line[21:22]


def _pad(line: str, width: int) -> str:
    return line if len(line) >= width else line + " " * (width - len(line))


def _relabel(line: str, from_chain: str, to_chain: str) -> str:
    if _chain(line) != from_chain:
        return line
    return line[:21] + to_chain + line[22:]


def _set_segid(line: str, chain_id: str, segid: str) -> str:
    if _chain(line) != chain_id:
        return line
    padded = _pad(line, 76)
    return padded[:72] + f"{segid:<4.4s}" + padded[76:]


def _parameters(step: Any) -> dict[str, str]:
    return {key: value for key, value in step.parameters}


def _require(step: Any, names: tuple[str, ...]) -> dict[str, str]:
    parameters = _parameters(step)
    missing = [name for name in names if not parameters.get(name)]
    if missing:
        raise ApplicationContractError(
            f"preparation_step_{step.operation}_missing_{'_'.join(missing)}"
        )
    return parameters


def _apply(lines: list[str], step: Any) -> list[str]:
    operation = step.operation
    if operation == "remove_solvent":
        return [line for line in lines if _resname(line) not in SOLVENT_RESIDUES]
    if operation == "remove_selected_heteroatoms":
        parameters = _require(step, ("resname",))
        target = parameters["resname"].strip().upper()
        return [
            line
            for line in lines
            if not (line.startswith("HETATM") and _resname(line) == target)
        ]
    if operation == "extract_chain":
        parameters = _require(step, ("chain_id",))
        keep = parameters["chain_id"]
        return [line for line in lines if _chain(line) == keep]
    if operation == "remove_non_target_chains":
        parameters = _require(step, ("chain_ids",))
        keep = {item.strip() for item in parameters["chain_ids"].split(",") if item.strip()}
        if not keep:
            raise ApplicationContractError("preparation_step_remove_non_target_chains_empty")
        return [line for line in lines if _chain(line) in keep]
    if operation == "relabel_chain":
        parameters = _require(step, ("from_chain", "to_chain"))
        return [
            _relabel(line, parameters["from_chain"], parameters["to_chain"]) for line in lines
        ]
    if operation == "set_segid":
        parameters = _require(step, ("chain_id", "segid"))
        return [_set_segid(line, parameters["chain_id"], parameters["segid"]) for line in lines]
    # Every name in PREPARATION_OPERATIONS is handled above. A new one must
    # fail here rather than pass through as a step that did nothing.
    raise ApplicationContractError(f"preparation_operation_not_executable_{operation}")


@dataclass(frozen=True)
class PreparedTarget:
    """The prepared bytes and what they contain, measured rather than claimed."""

    pdb: str
    chains: tuple[str, ...]
    residue_count: int
    first_residue: int
    last_residue: int
    heteroatom_residues: tuple[str, ...]
    atom_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chains": list(self.chains),
            "residue_count": self.residue_count,
            "first_residue": self.first_residue,
            "last_residue": self.last_residue,
            "heteroatom_residues": list(self.heteroatom_residues),
            "atom_count": self.atom_count,
        }


def _measure(pdb: str) -> PreparedTarget:
    chains: list[str] = []
    residues: set[tuple[str, int]] = set()
    hetero: set[str] = set()
    atoms = 0
    for line in pdb.splitlines():
        if not _is_coordinate(line):
            continue
        atoms += 1
        chain = _chain(line)
        if chain not in chains:
            chains.append(chain)
        try:
            residues.add((chain, int(line[22:26])))
        except ValueError:
            continue
        if line.startswith("HETATM"):
            hetero.add(_resname(line))
    numbers = [number for _, number in residues]
    return PreparedTarget(
        pdb=pdb,
        chains=tuple(chains),
        residue_count=len(residues),
        first_residue=min(numbers) if numbers else 0,
        last_residue=max(numbers) if numbers else 0,
        heteroatom_residues=tuple(sorted(hetero)),
        atom_count=atoms,
    )


def prepare_target(pdb: str, steps: Any) -> PreparedTarget:
    """Run the recorded steps, in their recorded order, and measure the result.

    The caller passes the specification's own steps. Nothing here invents a
    step, reorders them, or drops one.
    """

    ordered = tuple(steps)
    if not ordered:
        raise ApplicationContractError("preparation_records_no_step")
    validate_preparation(ordered)
    lines = [line for line in pdb.splitlines() if _is_coordinate(line)]
    if not lines:
        raise ApplicationContractError("source_structure_has_no_coordinates")
    for step in sorted(ordered, key=lambda item: item.order):
        lines = _apply(lines, step)
        if not lines:
            raise ApplicationContractError(
                f"preparation_step_{step.order}_{step.operation}_removed_everything"
            )
    return _measure("\n".join(lines) + "\nEND\n")


def executed_steps(specification: VHHCampaignSpecification, extra_steps: Any = ()) -> tuple[Any, ...]:
    """The recorded steps, then the caller's, renumbered to one sequence."""

    recorded = tuple(sorted(specification.preparation, key=lambda item: item.order))
    extra = tuple(extra_steps)
    return recorded + tuple(
        dataclasses.replace(step, order=len(recorded) + index)
        for index, step in enumerate(extra, start=1)
    )


def preparation_manifest(
    specification: VHHCampaignSpecification,
    source_pdb: str,
    prepared: PreparedTarget,
    extra_steps: Any = (),
) -> dict[str, Any]:
    """What ran, on what bytes, producing what. The auditable half.

    The digests are of the bytes, computed here, because a stated digest is
    not verification anywhere else in this codebase either.
    """

    return {
        "campaign_id": specification.campaign_id,
        "target_id": specification.target_id,
        "source_sha256": hashlib.sha256(source_pdb.encode("utf-8")).hexdigest(),
        "prepared_sha256": hashlib.sha256(prepared.pdb.encode("utf-8")).hexdigest(),
        "recorded_steps": [step.to_dict() for step in specification.preparation],
        "additional_steps": [step.to_dict() for step in tuple(extra_steps)],
        "executed": [
            {"order": step.order, "operation": step.operation, "parameters": dict(step.parameters)}
            for step in executed_steps(specification, extra_steps)
        ],
        "result": prepared.to_dict(),
        "epitope_residues": sorted(
            residue.residue_number for residue in specification.epitope.residues
        )
        if specification.epitope is not None
        else [],
    }


def prepare_from_specification(
    specification: VHHCampaignSpecification,
    pdb: str,
    extra_steps: Any = (),
) -> PreparedTarget:
    """Prepare, then check the result against what the specification declares.

    This is the half that makes the preparation worth executing: the chain the
    specification says it produced has to be there, and every epitope residue
    it declares has to be present in that chain. An epitope that does not
    survive the preparation would be a hotspot list pointing at nothing.

    ``extra_steps`` run after the recorded ones and are renumbered to follow
    them. They exist because a recorded preparation can be incomplete for a
    purpose it was not written for, and the alternative — editing the
    specification — would change what the record says someone did. A caller
    that passes extra steps is declaring its own preparation, and
    ``preparation_manifest`` writes down the whole sequence that ran.
    """

    structure = specification.target_structure
    if structure is None:
        raise ApplicationContractError("specification_declares_no_target_structure")
    prepared = prepare_target(pdb, executed_steps(specification, extra_steps))
    declared = tuple(structure.prepared_target_chain_ids)
    if prepared.chains != declared:
        raise ApplicationContractError(
            f"prepared_chains_{''.join(prepared.chains)}_not_declared_{''.join(declared)}"
        )
    epitope = specification.epitope
    if epitope is not None:
        present = {
            (_chain(line), int(line[22:26]))
            for line in prepared.pdb.splitlines()
            if _is_coordinate(line) and line[22:26].strip().lstrip("-").isdigit()
        }
        absent = sorted(
            residue.residue_number
            for residue in epitope.residues
            if (residue.chain_id, residue.residue_number) not in present
        )
        if absent:
            raise ApplicationContractError(
                "epitope_residues_absent_after_preparation_"
                + ",".join(str(number) for number in absent)
            )
    return prepared
