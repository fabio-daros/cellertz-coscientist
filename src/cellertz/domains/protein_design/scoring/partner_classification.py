"""How much of a declared hotspot block touches the clinical partner.

The atlas's unit of work is a complex, never a bare target: each of its nine
records carries ``target_chain`` beside ``partner_chains`` and the coordinates
of both, and every block it proposes is reported with ``contact_n`` and
``contact_pct`` against that partner. The measurement happens on the structure
**before any trajectory**, which is what makes it worth having: on CD19 it says,
for the price of reading a file, that four of ten blocks sit on the footprint of
FMC63 — the scFv inside every approved CD19 CAR-T — and that six touch none of
it.

**The rule, pinned against the atlas's own numbers.** A target residue is in
contact when any of its heavy atoms is within 5 A of any heavy atom of a
declared partner chain; ``contact_n`` counts the block's residues that are, and
``contact_pct`` is that count over the block's size. Recomputed from the nine
embedded complexes, this reproduces 72 of the atlas's 76 blocks exactly. The
four it misses are internally inconsistent in the atlas itself — their own
``contact_pct`` does not equal ``contact_n``/``n`` either (cd16a block C says
7 of 10 is 50%, gpc3 block B says 5 of 12 is 100%) — so they are its arithmetic
rather than another cutoff. 4.5 A misses nine blocks and 5.5 A misses ten.

**Why the verdict is measured here and not asked of the agent.** The atlas
records a four-word ``overlap`` verdict per block, and that verdict is *not* a
function of its own contact measurement: its ORTHOGONAL blocks run from 0% to
100% contact and two of its OVERLAPPING blocks touch nothing at all. A word
chosen by the agent that disagrees with the structure by 100 points is worse
than no word, and the Hotspot agent is already forbidden from declaring "how
much of a partner epitope a block covers" for exactly this reason. So the four
words are derived from the measurement, and the edge cut below is ours.

**The direction is declared, never inferred.** The atlas carries it per target
as ``contact_good``: touching the partner is the failure on seven of its nine
targets and the goal on two — HSA, where the partner ALB8 is a VHH doing the
job being designed, and GPC3. It inverts what every percentage means, so a block
on the partner's footprint is ON-TARGET under ``compete`` and OVERLAPPING under
``avoid``. With no direction declared the measurement is still reported; only
the reading is withheld.
"""

from __future__ import annotations

from typing import Any

from cellertz.domains.protein_design.scoring.hotspot_null_model import CONTACT_ANGSTROM
from cellertz.domains.protein_design.scoring.interface_area import InterfaceAreaError

# The atlas's four words. ON-TARGET appears on both of its ``contact_good``
# targets and on neither of the other seven, which is why it is the direction's
# word rather than a geometry of its own.
VERDICTS = ("ORTHOGONAL", "ADJACENT", "OVERLAPPING", "ON-TARGET")
# Ours, not the atlas's: its own four words track no percentage at all, so a
# block that touches the partner at its edge has to be separated from one that
# sits on it by a cut this platform declares. Below half the block's residues
# is an edge; at or above half the block is on the footprint.
EDGE_CONTACT_PCT = 50
PARTNER_DIRECTIONS = ("compete", "avoid")
_CELL = CONTACT_ANGSTROM


# Water, by the names the PDB uses for it. A bridging water is not a contact:
# counting it put two HOH residues into the m971 footprint on 7O52 and made it
# 28 residues where the atlas states 26. Heavier solvent additives and glycans
# are left in, since the atlas settles nothing about them and a covalent sugar
# is physically there.
SOLVENT_RESIDUES = frozenset({"HOH", "DOD", "WAT", "H2O"})


def heavy_atoms(pdb: str) -> list[tuple[str, int, float, float, float]]:
    """Chain, residue number and coordinate of every non-hydrogen, non-water atom.

    One atom per (chain, residue, insertion code, name): an alternate location
    is the same atom twice and would double a contact count.
    """

    atoms: list[tuple[str, int, float, float, float]] = []
    seen: set[tuple[str, int, str, str]] = set()
    for line in pdb.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if line[17:20].strip().upper() in SOLVENT_RESIDUES:
            continue
        name = line[12:16].strip()
        element = line[76:78].strip() if len(line) >= 78 else ""
        if (element or name).upper().startswith("H"):
            continue
        chain = line[21:22].strip()
        try:
            resseq = int(line[22:26])
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except ValueError:
            continue
        key = (chain, resseq, line[26:27], name)
        if key in seen:
            continue
        seen.add(key)
        atoms.append((chain, resseq, x, y, z))
    return atoms


def _grid(atoms: list[tuple[float, float, float]]) -> dict[tuple[int, int, int], list[tuple[float, float, float]]]:
    """Partner atoms in 5 A cells, so a contact test reads 27 cells rather than every atom.

    EGFR's complex carries about eight thousand heavy atoms. The full product is
    tens of millions of distance tests inside a request that is already waiting
    on a model call.
    """

    cells: dict[tuple[int, int, int], list[tuple[float, float, float]]] = {}
    for x, y, z in atoms:
        cells.setdefault((int(x // _CELL), int(y // _CELL), int(z // _CELL)), []).append((x, y, z))
    return cells


def partner_contact_residues(
    pdb: str,
    target_chain: str,
    partner_chains: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """Which residues of the target chain touch the declared partner chains.

    Chains that are neither are ignored rather than treated as partners. The
    atlas needs that: its CD3e record holds CD3d and its GPC3 record holds a
    third binder, and counting those as the partner would change both answers.
    """

    partners = tuple(partner_chains)
    if not partners:
        raise InterfaceAreaError("no_partner_chain_named")
    if len(set(partners)) != len(partners):
        raise InterfaceAreaError("partner_chain_named_twice")
    if target_chain in partners:
        raise InterfaceAreaError("target_cannot_be_its_own_partner")
    atoms = heavy_atoms(pdb)
    if not atoms:
        raise InterfaceAreaError("structure_has_no_coordinates")
    target = [(resseq, x, y, z) for chain, resseq, x, y, z in atoms if chain == target_chain]
    if not target:
        raise InterfaceAreaError(f"chain_absent:{target_chain}")
    per_partner: dict[str, set[int]] = {}
    for partner in partners:
        coordinates = [(x, y, z) for chain, _resseq, x, y, z in atoms if chain == partner]
        if not coordinates:
            raise InterfaceAreaError(f"chain_absent:{partner}")
        cells = _grid(coordinates)
        limit = CONTACT_ANGSTROM * CONTACT_ANGSTROM
        touching: set[int] = set()
        for resseq, x, y, z in target:
            if resseq in touching:
                continue
            cx, cy, cz = int(x // _CELL), int(y // _CELL), int(z // _CELL)
            near = (
                coordinate
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                for dz in (-1, 0, 1)
                for coordinate in cells.get((cx + dx, cy + dy, cz + dz), ())
            )
            if any((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2 <= limit for px, py, pz in near):
                touching.add(resseq)
        per_partner[partner] = touching
    union: set[int] = set()
    for touching in per_partner.values():
        union |= touching
    return {
        "target_chain": target_chain,
        "partner_chains": list(partners),
        "contact_residues": sorted(union),
        "per_partner": {partner: sorted(per_partner[partner]) for partner in partners},
        # Every residue number of the target chain, so a caller can see whether
        # the complex and the prepared file are even in the same frame before
        # joining a block to this measurement by residue number.
        "target_residues": sorted({resseq for resseq, _x, _y, _z in target}),
        "cutoff_angstrom": CONTACT_ANGSTROM,
        "target_heavy_atoms": len(target),
    }


def block_verdict(contact_pct: int, direction: str | None) -> str:
    """One of the atlas's four words, from the measurement and the declared direction."""

    if contact_pct <= 0:
        return "ORTHOGONAL"
    if contact_pct < EDGE_CONTACT_PCT:
        return "ADJACENT"
    return "ON-TARGET" if direction == "compete" else "OVERLAPPING"


_READING = {
    ("avoid", "OVERLAPPING"): "Competes with the partner: at least half this block sits on the partner's footprint, and contact with it is the declared failure.",
    ("avoid", "ADJACENT"): "Touches the partner's edge. Part of this block is on the footprint it is meant to avoid.",
    ("avoid", "ORTHOGONAL"): "Clear of the partner, which is what this campaign declared it wants.",
    ("compete", "ON-TARGET"): "On the partner's epitope, which is what this campaign declared it wants.",
    ("compete", "ADJACENT"): "Only the edge of the partner's epitope. Most of this block is off the footprint it is meant to cover.",
    ("compete", "ORTHOGONAL"): "Misses the partner's epitope entirely, and covering it is the declared goal.",
}
_NO_DIRECTION = (
    "partner_direction is not declared, so what this contact means is not decided here. "
    "On the atlas's own targets the same percentage is the goal on two and the failure on seven."
)


def classify_blocks(
    blocks: list[dict[str, Any]],
    contact: dict[str, Any],
    direction: str | None = None,
) -> list[dict[str, Any]]:
    """Attach the partner measurement to each declared block.

    The block's own residues are the denominator, not its numeric span: the
    atlas's blocks are contiguous ranges and these are the residues that
    survived hotspot validation, so a range would count residues the plan never
    declared. Blocks are joined to the measurement by residue number, which is
    what the recorded preparation preserves — the chain letter is not, since
    extracting a chain relabels it.
    """

    if direction is not None and direction not in PARTNER_DIRECTIONS:
        raise InterfaceAreaError(f"partner_direction_not_declared_as_a_known_value:{direction}")
    touching = set(contact["contact_residues"])
    classified: list[dict[str, Any]] = []
    for block in blocks:
        numbers = _block_residues(block)
        if not numbers:
            classified.append({**block, "contact_status": "block_names_no_residue_number"})
            continue
        hit = sorted(number for number in numbers if number in touching)
        pct = round(100 * len(hit) / len(numbers))
        verdict = block_verdict(pct, direction)
        classified.append(
            {
                **block,
                "contact_n": len(hit),
                "contact_pct": pct,
                "contact_residues": hit,
                "verdict": verdict,
                "partner_direction": direction,
                "reading": _READING[(direction, verdict)] if direction is not None else _NO_DIRECTION,
                "contact_status": "measured",
            }
        )
    return classified


def _block_residues(block: dict[str, Any]) -> list[int]:
    numbers: list[int] = []
    for label in block.get("residues") or ():
        if not isinstance(label, str):
            continue
        digits = label[1:]
        if digits.isdigit():
            numbers.append(int(digits))
    return numbers
