"""The score a journey records, for whichever generator produced the execution.

This is the ``specialist_scorer`` the composition root injects. Without one the
scoring stage fell back to ``ProteinIpSAECalculator`` for every execution — a
protocol the atlas rules out and mBER cannot produce, written into the record
with ipSAE in its provenance as if it had been measured.

Where the number comes from, per generator:

    BindCraft  ->  ipsae_score: raw ipSAE, replayed from the saved models
    mBER       ->  the launch's own two-axis panel, read, never recomputed

The mBER side is a **read**. ``rank_mber_output`` already screened and ranked
the cohort on the GPU host, and the two axes are cohort statistics: a
percentile only exists against the designs it was computed with. Recomputing
one design's score here would either need the whole cohort again or invent a
number, so this reads the row the launch recorded and refuses when there is
none.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cellertz.application.journey_specialist_progress import ipsae_score
from cellertz.domains.protein_design.scoring.engager_two_axis import (
    PROTOCOL_ID,
    PROTOCOL_VERSION,
)
from cellertz.tools.protein_scoring.scoring_v2 import ProteinScoreResultV2

BENCHMARK = f"{PROTOCOL_ID}.v{PROTOCOL_VERSION}"
LIMITATIONS = (
    "ranking_score is the worse of two cohort percentiles, not a binding probability",
    (
        "a percentile only holds against the cohort it was computed with and "
        "cannot be compared across campaigns"
    ),
    "neither axis is experimentally validated; the composite is a weighted heuristic",
    "this protocol's values must never be mixed with ipSAE or with CARPNN_2026_REFERENCE",
)


class EngagerScoreUnavailable(ValueError):
    """The launch recorded no usable two-axis row for this candidate."""


def specialist_scorer(execution: Mapping[str, Any]) -> ProteinScoreResultV2:
    """Score one accepted candidate of a saved execution.

    An execution that carries a ranking was ranked by its launch, and that
    panel is the score. One that does not is a BindCraft replay, whose models
    the evidence stage has already resolved before this runs.
    """

    if isinstance(execution.get("ranking"), dict):
        return engager_score(execution)
    return ipsae_score(execution)


def engager_score(execution: Mapping[str, Any]) -> ProteinScoreResultV2:
    """Turn the launch's recorded panel row for this candidate into a score."""

    document = execution.get("ranking")
    if not isinstance(document, dict):
        raise EngagerScoreUnavailable("execution_recorded_no_ranking")
    ranking = document.get("ranking")
    if not isinstance(ranking, dict):
        raise EngagerScoreUnavailable("ranking_document_has_no_ranking")
    if ranking.get("protocol_id") != PROTOCOL_ID:
        # A panel under a different protocol is a different claim. Reading its
        # score as this one's would put two orderings in one column.
        raise EngagerScoreUnavailable(
            f"ranking_protocol_is_not_this_one:{ranking.get('protocol_id')}"
        )
    candidate_id, design = _identity(execution)
    rows = ranking.get("designs")
    row = _row(rows if isinstance(rows, list) else [], candidate_id, design)
    if row is None:
        if candidate_id in tuple(ranking.get("rejected_by_gate") or ()):
            raise EngagerScoreUnavailable(f"candidate_rejected_by_the_i_ptm_gate:{candidate_id}")
        raise EngagerScoreUnavailable(f"ranking_has_no_row_for:{candidate_id}")
    score = row.get("score")
    if not isinstance(score, int | float) or isinstance(score, bool):
        raise EngagerScoreUnavailable(f"row_records_no_score_for:{candidate_id}")
    terms = row.get("terms") if isinstance(row.get("terms"), dict) else {}
    return ProteinScoreResultV2(
        candidate_id=candidate_id,
        baseline_score=float(score),
        baseline_method=PROTOCOL_ID,
        structural_evidence={
            "i_ptm": _float(row.get("i_ptm")),
            "i_ptm_percentile": _float(row.get("i_ptm_percentile")),
            "composite": _float(row.get("composite")),
            "composite_percentile": _float(row.get("composite_percentile")),
        },
        additional_evidence={
            # The terms the composite weights, as the launch measured them.
            # Evidence, not a ranking signal: the composite already weighs them.
            **{str(name): _float(value) for name, value in terms.items()},
            "rank_in_cohort": _float(row.get("rank")),
        },
        is_probability=False,
        recommended_as_hard_filter=False,
        benchmark_version=BENCHMARK,
        provenance={
            "calculator": {
                "implementation": "rank_engager_designs",
                "module": "cellertz.domains.protein_design.scoring.engager_two_axis",
            },
            # The launch computed this on the GPU host over the whole cohort.
            # Nothing here scored again, so there is no calculation window to
            # record: the one that matters is the launch's.
            "computed_by": "the launch that produced this execution",
            "attempt_id": execution.get("attempt_id"),
            "campaign_id": document.get("campaign_id"),
            "generator_version": document.get("generator_version"),
            "protocol_id": PROTOCOL_ID,
            "protocol_version": ranking.get("protocol_version"),
            "ordering": ranking.get("ordering"),
            "i_ptm_gate": ranking.get("i_ptm_gate"),
            "weights": ranking.get("weights"),
            "normalisation": ranking.get("normalisation"),
            "score_rule": row.get("score_rule"),
            "on_pareto_front": row.get("on_pareto_front"),
            "selected": row.get("selected"),
            "trajectory": row.get("trajectory"),
            "not_comparable_with": ranking.get("not_comparable_with"),
            "interpretations": ranking.get("interpretations"),
            # The cohort the percentiles are against. Without it the number
            # above is unreadable.
            "cohort": {
                "generated": document.get("generated"),
                "screened_in": document.get("screened_in"),
                "ranked": len(rows) if isinstance(rows, list) else None,
                "rejected_by_screen": len(document.get("rejected_by_screen") or ()),
                "rejected_by_gate": len(ranking.get("rejected_by_gate") or ()),
                "not_measurable": len(document.get("not_measurable") or ()),
            },
        },
        limitations=LIMITATIONS,
    )


def _identity(execution: Mapping[str, Any]) -> tuple[str, str | None]:
    identity = execution.get("candidate_identity")
    if not isinstance(identity, dict):
        raise EngagerScoreUnavailable("execution_has_no_candidate_identity")
    candidate_id = identity.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise EngagerScoreUnavailable("execution_has_no_candidate_id")
    design = identity.get("design")
    return candidate_id, design if isinstance(design, str) and design else None


def _row(rows: list[Any], candidate_id: str, design: str | None) -> dict[str, Any] | None:
    """The panel row for this candidate, by candidate id and then by design name.

    mBER's parser names a candidate after the accepted design, but the two are
    separate fields on the identity and a launcher may set them apart.
    """

    for wanted in (candidate_id, design):
        if wanted is None:
            continue
        for item in rows:
            if isinstance(item, dict) and item.get("design_id") == wanted:
                return item
    return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
