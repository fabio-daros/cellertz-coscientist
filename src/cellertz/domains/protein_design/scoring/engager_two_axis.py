"""The professor's two-axis ranking, as its own scoring protocol.

From the Engager Target Atlas. It is a **third** protocol: its values must
never be mixed with ``CARPNN_2026_REFERENCE`` or with the older unprovenanced
scores, and it emits no ipSAE and no ranking_score under any other name.

Why two axes and not one sum: in the atlas the two are nearly independent
(Spearman rho +0.155, p 0.016), so a blended sum let a design ride one axis.
Ranking on the **worse** of the two percentiles means a design rises only by
being good on both.

    i_ptm >= 0.80 gate
            |
            v
    axis A: i_pTM percentile
    axis B: quality composite percentile, i_pTM excluded
            |
            v
    score = min(A, B)   ->   Pareto front   ->   greedy diverse selection

This module ranks. It does not fold, score a structure, compute buried area,
read a file, or decide that a design may be made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROTOCOL_ID = "ENGAGER_TWO_AXIS_2026"
PROTOCOL_VERSION = 1
# "Manifold report this raises hit rates up to tenfold." His gate, not ours.
I_PTM_GATE = 0.80
# The atlas's weights. The composite excludes i_pTM on purpose: it is axis A.
WEIGHTS: dict[str, float] = {
    "epitope_jaccard_clinical": 1.2,
    "buried_area": 1.0,
    "net_charge": -0.7,
    "deamidation": -0.3,
    "isomerisation": -0.3,
    "met_trp_in_cdr": -0.2,
}
# Both must be exceeded for a design to be dropped as a near-duplicate.
JACCARD_LIMIT = 0.85
IDENTITY_LIMIT = 0.85
_DEAMIDATION = ("NG", "NS")
_ISOMERISATION = ("DG", "DS")


class EngagerRankingError(ValueError):
    """A term the formula weights is missing, or the cohort cannot be ranked."""


@dataclass(frozen=True)
class EngagerDesign:
    """One accepted design, with every term the composite weights.

    ``net_charge`` and ``buried_area`` are supplied rather than derived here:
    the charge comes from the developability screen that already computes it,
    and buried area needs a solvent-accessible-surface calculation this module
    deliberately does not own.
    """

    design_id: str
    trajectory: str
    sequence: str
    i_ptm: float
    epitope: frozenset[int]
    buried_area: float
    net_charge: float


@dataclass(frozen=True)
class RankedEngagerDesign:
    design_id: str
    trajectory: str
    i_ptm: float
    i_ptm_percentile: float
    composite: float
    composite_percentile: float
    score: float
    terms: dict[str, float]
    on_pareto_front: bool
    selected: bool
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "design_id": self.design_id,
            "trajectory": self.trajectory,
            "protocol_id": PROTOCOL_ID,
            "protocol_version": PROTOCOL_VERSION,
            "i_ptm": self.i_ptm,
            "i_ptm_percentile": self.i_ptm_percentile,
            "composite": self.composite,
            "composite_percentile": self.composite_percentile,
            "score": self.score,
            "score_rule": "the worse of the two percentiles",
            "terms": dict(self.terms),
            "on_pareto_front": self.on_pareto_front,
            "selected": self.selected,
            "rank": self.rank,
        }


@dataclass(frozen=True)
class EngagerRanking:
    designs: tuple[RankedEngagerDesign, ...]
    rejected_by_gate: tuple[str, ...] = ()
    normalisation: str = "each term to its cohort percentile before weighting"
    interpretations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": PROTOCOL_ID,
            "protocol_version": PROTOCOL_VERSION,
            "i_ptm_gate": I_PTM_GATE,
            "weights": dict(WEIGHTS),
            "normalisation": self.normalisation,
            "interpretations": list(self.interpretations),
            "not_comparable_with": ["CARPNN_2026_REFERENCE", "ipsae_primary_ranking"],
            "rejected_by_gate": list(self.rejected_by_gate),
            "designs": [design.to_dict() for design in self.designs],
        }


def rank_engager_designs(
    designs: list[EngagerDesign],
    clinical_epitopes: list[frozenset[int]] | tuple[frozenset[int], ...],
    cdr_spans: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    select: int | None = None,
) -> EngagerRanking:
    """Rank a cohort on the worse of its two percentiles.

    ``clinical_epitopes`` is the benchmark antibody's epitope, or both of its
    nanobodies: the atlas takes the better Jaccard of the two. It carries a
    weight of 1.2, so the cohort cannot be ranked without it rather than being
    ranked on a formula with a term silently set to zero.
    """

    if not clinical_epitopes:
        raise EngagerRankingError("clinical_epitope_not_declared")
    if not cdr_spans:
        raise EngagerRankingError("cdr_spans_not_declared")
    identifiers = [design.design_id for design in designs]
    if len(identifiers) != len(set(identifiers)):
        raise EngagerRankingError("duplicate_design_id")
    gated = [design for design in designs if design.i_ptm >= I_PTM_GATE]
    rejected = tuple(
        design.design_id for design in designs if design.i_ptm < I_PTM_GATE
    )
    if not gated:
        return EngagerRanking(designs=(), rejected_by_gate=rejected)

    terms = [_terms(design, clinical_epitopes, cdr_spans) for design in gated]
    # Each term reaches the weighted sum as its own cohort percentile, so one
    # term's unit (buried area in square angstroms) cannot swamp another's
    # (a Jaccard in 0..1).
    per_term = {
        name: _percentiles([row[name] for row in terms]) for name in WEIGHTS
    }
    composites = [
        sum(WEIGHTS[name] * per_term[name][index] for name in WEIGHTS)
        for index in range(len(gated))
    ]
    i_ptm_percentile = _percentiles([design.i_ptm for design in gated])
    composite_percentile = _percentiles(composites)
    scores = [
        min(i_ptm_percentile[index], composite_percentile[index])
        for index in range(len(gated))
    ]
    front = _pareto_front(i_ptm_percentile, composite_percentile)
    order = sorted(
        range(len(gated)),
        key=lambda index: (-scores[index], gated[index].design_id),
    )
    chosen = _greedy_diverse(order, gated, select)
    ranked = []
    for position, index in enumerate(order, start=1):
        ranked.append(
            RankedEngagerDesign(
                design_id=gated[index].design_id,
                trajectory=gated[index].trajectory,
                i_ptm=gated[index].i_ptm,
                i_ptm_percentile=i_ptm_percentile[index],
                composite=composites[index],
                composite_percentile=composite_percentile[index],
                score=scores[index],
                terms=terms[index],
                on_pareto_front=front[index],
                selected=index in chosen,
                rank=position,
            )
        )
    return EngagerRanking(
        designs=tuple(ranked),
        rejected_by_gate=rejected,
        interpretations=(
            # The atlas states the weights but not how terms of different units
            # are brought together. Percentile normalisation is ours and has to
            # be confirmed against his own run before a number is quoted as his.
            "Per-term percentile normalisation before weighting is this "
            "platform's reading of the formula, not a stated step of it.",
        ),
    )


def _terms(
    design: EngagerDesign,
    clinical_epitopes: Any,
    cdr_spans: Any,
) -> dict[str, float]:
    liabilities = count_liabilities(design.sequence, cdr_spans)
    return {
        "epitope_jaccard_clinical": max(
            epitope_jaccard(design.epitope, clinical) for clinical in clinical_epitopes
        ),
        "buried_area": design.buried_area,
        "net_charge": design.net_charge,
        "deamidation": float(liabilities["deamidation"]),
        "isomerisation": float(liabilities["isomerisation"]),
        "met_trp_in_cdr": float(liabilities["met_trp_in_cdr"]),
    }


def count_liabilities(
    sequence: str,
    cdr_spans: list[tuple[int, int]] | tuple[tuple[int, int], ...],
) -> dict[str, int]:
    """Deamidation (NG/NS), isomerisation (DG/DS), and Met or Trp in a CDR.

    The spans are 1-based and inclusive, as the scaffold's CDR regions record
    them. Met and Trp count only inside a CDR: in the framework they are
    structural, in a CDR they are an oxidation risk at the interface.
    """

    upper = sequence.upper()
    motifs = {
        "deamidation": sum(upper.count(motif) for motif in _DEAMIDATION),
        "isomerisation": sum(upper.count(motif) for motif in _ISOMERISATION),
    }
    in_cdr = 0
    for start, end in cdr_spans:
        if start < 1 or end < start:
            raise EngagerRankingError("invalid_cdr_span")
        for residue in upper[start - 1 : end]:
            if residue in {"M", "W"}:
                in_cdr += 1
    return {**motifs, "met_trp_in_cdr": in_cdr}


def epitope_jaccard(left: frozenset[int], right: frozenset[int]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def sequence_identity(left: str, right: str) -> float:
    """Position-wise identity over the longer sequence.

    Dividing by the longer length means a short sequence that matches a prefix
    exactly is not called identical to a long one.
    """

    longest = max(len(left), len(right))
    if longest == 0:
        return 0.0
    matches = sum(1 for a, b in zip(left.upper(), right.upper()) if a == b)
    return matches / longest


def _percentiles(values: list[float]) -> list[float]:
    """Average-rank percentile in 0..1, 1.0 being the best value present.

    Ties share a percentile, so two equal designs cannot be separated by the
    order they happened to arrive in.
    """

    count = len(values)
    if count == 1:
        return [1.0]
    order = sorted(range(count), key=lambda index: values[index])
    ranks = [0.0] * count
    position = 0
    while position < count:
        end = position
        while end + 1 < count and values[order[end + 1]] == values[order[position]]:
            end += 1
        shared = (position + end) / 2 / (count - 1)
        for index in order[position : end + 1]:
            ranks[index] = shared
        position = end + 1
    return ranks


def _pareto_front(axis_a: list[float], axis_b: list[float]) -> list[bool]:
    """A design is on the front when nothing else is at least as good on both
    axes and strictly better on one."""

    front = []
    for index in range(len(axis_a)):
        dominated = any(
            other != index
            and axis_a[other] >= axis_a[index]
            and axis_b[other] >= axis_b[index]
            and (axis_a[other] > axis_a[index] or axis_b[other] > axis_b[index])
            for other in range(len(axis_a))
        )
        front.append(not dominated)
    return front


def _greedy_diverse(
    order: list[int],
    designs: list[EngagerDesign],
    select: int | None,
) -> set[int]:
    """Take the best first, then skip anything too close to what is taken.

    One design per trajectory, because two designs from the same trajectory are
    the same search, not two findings. A near-duplicate is dropped only when it
    is close on **both** epitope and sequence: the same epitope reached by a
    different sequence is a result, not a repeat.
    """

    chosen: set[int] = set()
    trajectories: set[str] = set()
    for index in order:
        if select is not None and len(chosen) >= select:
            break
        candidate = designs[index]
        if candidate.trajectory in trajectories:
            continue
        if any(
            epitope_jaccard(candidate.epitope, designs[taken].epitope) > JACCARD_LIMIT
            and sequence_identity(candidate.sequence, designs[taken].sequence)
            > IDENTITY_LIMIT
            for taken in chosen
        ):
            continue
        chosen.add(index)
        trajectories.add(candidate.trajectory)
    return chosen
