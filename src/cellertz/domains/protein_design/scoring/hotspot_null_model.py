"""Is a hotspot audit's recall better than a random patch of the same size?

The atlas re-audited GPC3 on 6 Nov 2026 against "a null model of 400 random
size-matched surface patches per design", and the result changed its
conclusions: the unseeded arm sits at 2.95x chance with only 8.7% of its
designs individually clearing their own null, while the three seeded arms sit
near 9x. Without that denominator "median 3 of 8 hotspots" is a number with
nothing to compare to.

Two measurements live here, both prerequisites for the audit:

**Reachability.** A declared hotspot that is buried cannot be a design target.
GPC3's ILE215 sits at relative SASA 0.018 and albumin's 319 at 0.036; both are
excluded from the denominator, which is why GPC3's mode moved from 8 of 9 to
7 of 8. 92-97% of seeded designs still register a 5 A contact with ILE215,
which is proximity to a subsurface side chain through its neighbours rather
than surface engagement — so contact counting alone does not catch this.

**Chance.** A patch is size-matched to the design's own footprint and
contiguous in space, grown from a random accessible residue outwards, because
a binder covers a patch rather than a scattered set. Enrichment is only
informative when the declared list is a small share of the surface: the atlas
notes that on TACI, where 44% of a 34-residue surface is declared, the same
test returns 1.86 and says little.

The draw count and the seed are both recorded in the result. A null model
whose seed is not written down cannot be re-checked.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

from cellertz.domains.protein_design.scoring.interface_area import (
    ALGORITHM,
    N_POINTS,
    PROBE_RADIUS,
    InterfaceAreaError,
)

# Theoretical maximum solvent accessibility per residue, Tien et al. 2013
# (PLoS ONE 8:e80635), the Gly-X-Gly tripeptide values. Used only as the
# denominator of a ratio, so a residue absent from this table is reported
# without one rather than guessed at.
MAX_ASA = {
    "ALA": 129.0, "ARG": 274.0, "ASN": 195.0, "ASP": 193.0, "CYS": 167.0,
    "GLN": 225.0, "GLU": 223.0, "GLY": 104.0, "HIS": 224.0, "ILE": 197.0,
    "LEU": 201.0, "LYS": 236.0, "MET": 224.0, "PHE": 240.0, "PRO": 159.0,
    "SER": 155.0, "THR": 172.0, "TRP": 285.0, "TYR": 263.0, "VAL": 174.0,
}
# This platform's cut, not the atlas's: it gives examples of buried hotspots
# (0.018, 0.036) and of a reachable one (0.593) but states no threshold. Both
# of its exclusions fall below this and its reachable example is far above.
BURIED_RELATIVE_SASA = 0.05
DEFAULT_DRAWS = 400
DEFAULT_SEED = 20261106
# The atlas measures contact at heavy atom <= 5 A, stated in its own footer.
CONTACT_ANGSTROM = 5.0


def _target_model(pdb_path: str | Path, chain: str) -> Any:
    from Bio.PDB import PDBParser

    path = Path(pdb_path)
    if not path.is_file():
        raise InterfaceAreaError("structure_not_found")
    structure = PDBParser(QUIET=True).get_structure("target", str(path))
    try:
        model = next(iter(structure))
    except StopIteration:
        raise InterfaceAreaError("structure_has_no_model") from None
    if chain not in {item.id for item in model}:
        raise InterfaceAreaError(f"chain_absent:{chain}")
    for other in [item.id for item in model if item.id != chain]:
        model.detach_child(other)
    return model


def relative_sasa(pdb_path: str | Path, chain: str = "A") -> dict[str, Any]:
    """Per-residue solvent accessibility of one chain, absolute and relative.

    The chain is measured **alone**: a partner left in the file would bury the
    very surface this is asking about.
    """

    from Bio.PDB.SASA import ShrakeRupley

    model = _target_model(pdb_path, chain)
    ShrakeRupley(probe_radius=PROBE_RADIUS, n_points=N_POINTS).compute(model, level="R")
    absolute: dict[int, float] = {}
    relative: dict[int, float] = {}
    unknown: list[int] = []
    for residue in model[chain]:
        number = residue.id[1]
        area = float(residue.sasa)
        absolute[number] = round(area, 2)
        reference = MAX_ASA.get(residue.get_resname().strip().upper())
        if reference is None:
            unknown.append(number)
            continue
        relative[number] = round(area / reference, 4)
    return {
        "chain": chain,
        "absolute_angstrom2": absolute,
        "relative": relative,
        "no_reference_area": sorted(unknown),
        "algorithm": ALGORITHM,
        "max_asa_reference": "tien_2013_theoretical",
    }


def reachable_hotspots(
    pdb_path: str | Path,
    declared: tuple[int, ...] | list[int],
    chain: str = "A",
    buried_limit: float = BURIED_RELATIVE_SASA,
) -> dict[str, Any]:
    """Split a declared hotspot list into what a binder could reach and what it could not.

    The reachable count is the audit's denominator. Reporting coverage against
    the declared count instead understates every campaign by however many of
    its hotspots were buried.
    """

    measured = relative_sasa(pdb_path, chain)
    relative = measured["relative"]
    wanted = sorted({int(number) for number in declared})
    if not wanted:
        raise InterfaceAreaError("no_hotspot_declared")
    reachable: list[int] = []
    buried: list[int] = []
    absent: list[int] = []
    for number in wanted:
        if number not in relative:
            absent.append(number)
        elif relative[number] < buried_limit:
            buried.append(number)
        else:
            reachable.append(number)
    accessible = sorted(
        number for number, value in relative.items() if value >= buried_limit
    )
    return {
        "chain": chain,
        "declared": wanted,
        "reachable": reachable,
        "buried": buried,
        "not_in_structure": absent,
        "relative_sasa": {number: relative[number] for number in wanted if number in relative},
        "buried_limit": buried_limit,
        "accessible_residues": len(accessible),
        # The atlas's own caveat: enrichment says little when the declared list
        # is a large share of the surface. On TACI, 44% returns 1.86.
        "declared_share_of_surface": (
            round(len(reachable) / len(accessible), 4) if accessible else None
        ),
    }


def _patch_pool(pdb_path: str | Path, chain: str, buried_limit: float) -> dict[int, tuple[float, float, float]]:
    """Accessible residues and one coordinate each, for growing patches."""

    measured = relative_sasa(pdb_path, chain)
    accessible = {
        number for number, value in measured["relative"].items() if value >= buried_limit
    }
    model = _target_model(pdb_path, chain)
    centres: dict[int, tuple[float, float, float]] = {}
    for residue in model[chain]:
        number = residue.id[1]
        if number not in accessible:
            continue
        atom = residue["CA"] if "CA" in residue else next(iter(residue), None)
        if atom is None:
            continue
        x, y, z = (float(value) for value in atom.coord)
        centres[number] = (x, y, z)
    return centres


def _grow_patch(
    centres: dict[int, tuple[float, float, float]],
    seed_residue: int,
    size: int,
) -> list[int]:
    """The ``size`` accessible residues nearest the seed, including it.

    Nearest-by-distance rather than a flood fill: it needs no contact graph,
    it cannot strand itself in a pocket, and it is what "contiguous surface
    patch of this size" means for a binder footprint.
    """

    origin = centres[seed_residue]
    ordered = sorted(
        centres,
        key=lambda number: math.dist(centres[number], origin),
    )
    return sorted(ordered[:size])


def hotspot_null_model(
    pdb_path: str | Path,
    hotspots: tuple[int, ...] | list[int],
    footprint_size: int,
    chain: str = "A",
    draws: int = DEFAULT_DRAWS,
    seed: int = DEFAULT_SEED,
    buried_limit: float = BURIED_RELATIVE_SASA,
) -> dict[str, Any]:
    """Recall a random size-matched contiguous surface patch achieves, over ``draws``.

    ``hotspots`` should already be the reachable list: a buried hotspot is
    unreachable for the null patches too, so leaving it in deflates both the
    observed recall and the null by the same amount and makes the ratio
    harder to read, not more honest.
    """

    wanted = {int(number) for number in hotspots}
    if not wanted:
        raise InterfaceAreaError("no_hotspot_declared")
    if footprint_size < 1:
        raise InterfaceAreaError("footprint_must_have_a_residue")
    if draws < 1:
        raise InterfaceAreaError("null_model_needs_a_draw")
    centres = _patch_pool(pdb_path, chain, buried_limit)
    if not centres:
        raise InterfaceAreaError("no_accessible_residue")
    size = min(footprint_size, len(centres))
    pool = sorted(centres)
    rng = random.Random(seed)
    recalls: list[float] = []
    for _ in range(draws):
        patch = _grow_patch(centres, rng.choice(pool), size)
        recalls.append(len(wanted & set(patch)) / len(wanted))
    recalls.sort()
    index = min(len(recalls) - 1, int(math.ceil(0.95 * len(recalls))) - 1)
    return {
        "chain": chain,
        "hotspots": sorted(wanted),
        "footprint_size": footprint_size,
        "patch_size": size,
        "draws": draws,
        "seed": seed,
        "mean_recall": round(sum(recalls) / len(recalls), 4),
        "p95_recall": round(recalls[index], 4),
        "max_recall": round(recalls[-1], 4),
        "accessible_residues": len(centres),
        "buried_limit": buried_limit,
    }


def audit_against_null(
    pdb_path: str | Path,
    reachable: tuple[int, ...] | list[int],
    footprints: dict[str, tuple[int, ...]] | dict[str, list[int]],
    chain: str = "A",
    draws: int = DEFAULT_DRAWS,
    seed: int = DEFAULT_SEED,
    buried_limit: float = BURIED_RELATIVE_SASA,
) -> dict[str, Any]:
    """Observed recall against each design's own null, as the atlas reports it.

    Three numbers per campaign in its tables: ``recall`` observed, ``null``
    from the random patches, ``enrich`` their ratio, and the share of designs
    beating their **own** null distribution at p < 0.05 — which is a different
    and stricter statement than the mean clearing chance. The atlas's campaign
    A sits at 2.95x enrichment with only 8.7% of designs individually clear.
    """

    wanted = {int(number) for number in reachable}
    if not wanted:
        raise InterfaceAreaError("no_hotspot_declared")
    if not footprints:
        raise InterfaceAreaError("no_design_footprint")
    nulls: dict[int, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    cleared = 0
    for design_id, residues in footprints.items():
        covered = {int(number) for number in residues}
        recall = len(wanted & covered) / len(wanted)
        size = len(covered)
        if size not in nulls:
            nulls[size] = hotspot_null_model(
                pdb_path,
                sorted(wanted),
                size if size else 1,
                chain=chain,
                draws=draws,
                seed=seed,
                buried_limit=buried_limit,
            )
        null = nulls[size]
        beats = recall > null["p95_recall"]
        cleared += 1 if beats else 0
        rows.append(
            {
                "design_id": design_id,
                "footprint_size": size,
                "hotspots_reached": sorted(wanted & covered),
                "recall": round(recall, 4),
                "null_mean_recall": null["mean_recall"],
                "null_p95_recall": null["p95_recall"],
                "beats_own_null_p05": beats,
            }
        )
    observed = sum(row["recall"] for row in rows) / len(rows)
    null_mean = sum(row["null_mean_recall"] for row in rows) / len(rows)
    return {
        "chain": chain,
        "reachable_hotspots": sorted(wanted),
        "designs": len(rows),
        "recall": round(observed, 4),
        "null": round(null_mean, 4),
        # Chance recall of zero would make a ratio meaningless, so it is
        # reported as absent rather than as infinity.
        "enrichment": round(observed / null_mean, 2) if null_mean > 0 else None,
        "share_beating_own_null_p05": round(cleared / len(rows), 4),
        "draws": draws,
        "seed": seed,
        "rows": rows,
    }
