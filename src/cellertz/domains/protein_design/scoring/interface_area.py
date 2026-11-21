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
