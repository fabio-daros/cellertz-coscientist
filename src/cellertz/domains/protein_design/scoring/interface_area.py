"""Buried interface area, the heaviest positive term of the two-axis composite.

Shrake-Rupley from Biopython, which is already a declared dependency, rather
than a solvent-accessible-surface implementation of our own.

    buried = SASA(target alone) + SASA(binder alone) - SASA(complex)

That difference is reported as ``delta_sasa_angstrom2``, and the conventional
buried surface area is half of it. Which of the two feeds the ranking cannot
change it: the composite normalises every term to its cohort percentile, and a
constant factor leaves an order untouched.

This module measures one structure. It does not rank, score or fold.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

PROBE_RADIUS = 1.40
N_POINTS = 100
ALGORITHM = "shrake_rupley_biopython"
# The sphere is sampled at fixed points, so a residue's area moves by about a
# square angstrom between two placements. Below this, a difference is sampling.
OCCLUSION_ANGSTROM2 = 1.0


class InterfaceAreaError(ValueError):
    """The structure does not carry the two chains the measurement needs."""


def interface_buried_area(
    pdb_path: str | Path,
    target_chain: str = "A",
    binder_chain: str = "B",
) -> dict[str, Any]:
    """How much surface the two chains bury against each other, in square angstroms."""

    from Bio.PDB import PDBParser
    from Bio.PDB.SASA import ShrakeRupley

    path = Path(pdb_path)
    if not path.is_file():
        raise InterfaceAreaError("structure_not_found")
    structure = PDBParser(QUIET=True).get_structure("complex", str(path))
    try:
        model = next(iter(structure))
    except StopIteration:
        raise InterfaceAreaError("structure_has_no_model") from None
    present = {chain.id for chain in model}
    for chain in (target_chain, binder_chain):
        if chain not in present:
            raise InterfaceAreaError(f"chain_absent:{chain}")
    if target_chain == binder_chain:
        raise InterfaceAreaError("chains_must_differ")

    calculator = ShrakeRupley(probe_radius=PROBE_RADIUS, n_points=N_POINTS)
    calculator.compute(model, level="C")
    complexed = {chain: float(model[chain].sasa) for chain in (target_chain, binder_chain)}

    alone: dict[str, float] = {}
    for chain in (target_chain, binder_chain):
        isolated = copy.deepcopy(model)
        for other in [item.id for item in isolated if item.id != chain]:
            isolated.detach_child(other)
        calculator.compute(isolated, level="C")
        alone[chain] = float(isolated[chain].sasa)

    per_chain = {chain: alone[chain] - complexed[chain] for chain in alone}
    delta = sum(per_chain.values())
    return {
        "buried_area_angstrom2": round(delta / 2, 2),
        "delta_sasa_angstrom2": round(delta, 2),
        "per_chain_buried": {chain: round(value, 2) for chain, value in per_chain.items()},
        "sasa_alone": {chain: round(value, 2) for chain, value in alone.items()},
        "sasa_in_complex": {chain: round(value, 2) for chain, value in complexed.items()},
        "target_chain": target_chain,
        "binder_chain": binder_chain,
        "algorithm": ALGORITHM,
        "probe_radius_angstrom": PROBE_RADIUS,
        "n_points": N_POINTS,
    }


def occluded_by_partners(
    pdb_path: str | Path,
    target_chain: str,
    partner_chains: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """Which target residues the partner chains cover, and by how much.

    One measurement, three uses, which is why it lives here rather than in
    three places:

    - **Target preparation.** These are exactly the residues that extracting
      the target chain leaves artificially exposed. The atlas's CD19 campaign
      was given 7URV chain C without CD81, and 26 CD81-contact residues sat
      bare; the designs took them. Knowing the list before a run is the lever.
    - **The native ligand's footprint**, which the developability screen needs
      to measure occlusion against.
    - **The clinical benchmark epitope** for the two-axis composite's J_clin,
      when the partner is the clinical antibody.

    A residue counts as occluded when removing the partners raises its own
    solvent-accessible area by more than the sampling noise.
    """

    from Bio.PDB import PDBParser
    from Bio.PDB.SASA import ShrakeRupley

    path = Path(pdb_path)
    if not path.is_file():
        raise InterfaceAreaError("structure_not_found")
    if not partner_chains:
        raise InterfaceAreaError("no_partner_chain_named")
    if target_chain in partner_chains:
        raise InterfaceAreaError("target_cannot_be_its_own_partner")
    structure = PDBParser(QUIET=True).get_structure("complex", str(path))
    try:
        model = next(iter(structure))
    except StopIteration:
        raise InterfaceAreaError("structure_has_no_model") from None
    present = {chain.id for chain in model}
    for chain in (target_chain, *partner_chains):
        if chain not in present:
            raise InterfaceAreaError(f"chain_absent:{chain}")

    calculator = ShrakeRupley(probe_radius=PROBE_RADIUS, n_points=N_POINTS)
    # The target with its partners, then the target by itself. Chains that are
    # neither are dropped from both, so a third protein in the file cannot
    # change the answer.
    with_partners = copy.deepcopy(model)
    for other in [item.id for item in with_partners if item.id not in (target_chain, *partner_chains)]:
        with_partners.detach_child(other)
    calculator.compute(with_partners, level="R")
    covered = {
        residue.id[1]: float(residue.sasa) for residue in with_partners[target_chain]
    }

    alone = copy.deepcopy(model)
    for other in [item.id for item in alone if item.id != target_chain]:
        alone.detach_child(other)
    calculator.compute(alone, level="R")
    bare = {residue.id[1]: float(residue.sasa) for residue in alone[target_chain]}

    exposed: dict[int, float] = {}
    for number, value in bare.items():
        gained = value - covered.get(number, value)
        if gained > OCCLUSION_ANGSTROM2:
            exposed[number] = round(gained, 2)
    return {
        "target_chain": target_chain,
        "partner_chains": list(partner_chains),
        "occluded_residues": sorted(exposed),
        "occluded_area_angstrom2": {number: exposed[number] for number in sorted(exposed)},
        "total_occluded_area_angstrom2": round(sum(exposed.values()), 2),
        "residues_measured": len(bare),
        "cutoff_angstrom2": OCCLUSION_ANGSTROM2,
        "algorithm": ALGORITHM,
        # Removing the partners is what extracting the target chain does, so
        # ``occluded_residues`` is also the list such a preparation leaves bare.
        "occluded_is_what_extraction_would_expose": True,
    }
