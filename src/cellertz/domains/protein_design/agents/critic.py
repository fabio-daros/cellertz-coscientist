"""Protein Critic Agent v1.

Annotates already-ranked candidates. Does not sort, score,
or replace ipSAE.

Agreement is set-relative rank discordance, not an absolute
ipTM cutoff and not a calibrated probability.

V1 auxiliary location, first present field only:
  boltz2_iptm_mean
  boltz2_iptm_median
  boltz2_iptm_model_0

Among candidates with a location value, compare dense order
by Ranking Agent rank vs dense order by that location metric.

  d = |primary_dense_rank - auxiliary_dense_rank| / (n - 1)

  d == 0     → high
  d <= 0.5   → mixed
  d > 0.5    → low

Fewer than two candidates with location evidence → unknown.
Missing location evidence → unknown, never substituted.

ensemble_consistency is unknown in v1. Classifying Boltz std
would require a second unfrozen rule.

Critic agreement categories are deterministic qualitative
heuristics. They are not experimentally calibrated and must
not be interpreted as binding probabilities or validated
decision thresholds. The band d <= 0.5 is a v1 interpretation
rule, not a YSD-tuned cutoff and not a lab gate.
"""

from dataclasses import dataclass, field
from typing import Literal

from cellertz.domains.protein_design.agents.ranking import ProteinRankingRecord


Agreement = Literal["high", "mixed", "low", "unknown"]
Consistency = Literal["high", "mixed", "low", "unknown"]

AUXILIARY_LOCATION_KEYS = (
    "boltz2_iptm_mean",
    "boltz2_iptm_median",
    "boltz2_iptm_model_0",
)

NOTES = {
    "high": (
        "Auxiliary structural evidence supports "
        "the primary ipSAE ranking."
    ),
    "mixed": (
        "Auxiliary structural evidence is mixed "
        "relative to the primary ipSAE ranking."
    ),
    "low": (
        "Auxiliary structural evidence conflicts "
        "with the primary ipSAE ranking."
    ),
    "unknown": (
        "Auxiliary structural evidence is missing "
        "or insufficient."
    ),
}


@dataclass(frozen=True)
class CriticAssessment:
    agreement: Agreement
    ensemble_consistency: Consistency
    structural_conflict: bool | None
    missing_evidence: bool
    confidence_note: str

    def to_dict(self) -> dict[str, object]:
        if self.structural_conflict is None:
            conflict: bool | str = "unknown"
        else:
            conflict = self.structural_conflict

        return {
            "agreement": self.agreement,
            "ensemble_consistency": self.ensemble_consistency,
            "structural_conflict": conflict,
            "missing_evidence": self.missing_evidence,
            "confidence_note": self.confidence_note,
        }


@dataclass(frozen=True)
class ProteinCriticRecord:
    candidate_id: str
    rank: int
    ranking_signal: str
    ranking_score: float | None
    is_probability: bool
    recommended_as_hard_filter: bool
    structural_evidence: dict[str, float | None] = field(
        default_factory=dict
    )
    critic_assessment: CriticAssessment | None = None

    def to_dict(self) -> dict[str, object]:
        assessment = (
            None
            if self.critic_assessment is None
            else self.critic_assessment.to_dict()
        )

        return {
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "ranking_signal": self.ranking_signal,
            "ranking_score": self.ranking_score,
            "is_probability": self.is_probability,
            "recommended_as_hard_filter": (
                self.recommended_as_hard_filter
            ),
            "structural_evidence": dict(
                self.structural_evidence
            ),
            "critic_assessment": assessment,
        }


def auxiliary_location(
    evidence: dict[str, float | None],
) -> float | None:
    for key in AUXILIARY_LOCATION_KEYS:
        value = evidence.get(key)

        if value is not None:
            return float(value)

    return None


def _agreement_from_discordance(
    discordance: float,
) -> Agreement:
    if discordance == 0:
        return "high"

    if discordance <= 0.5:
        return "mixed"

    return "low"


def _auxiliary_sort_key(
    record: ProteinRankingRecord,
) -> tuple[float, str]:
    location = auxiliary_location(
        record.structural_evidence
    )

    if location is None:
        raise ValueError(
            "Auxiliary sort requires a location metric."
        )

    return (-location, record.candidate_id)


class ProteinCriticAgentV1:
    """Set-relative structural critic. Never changes rank."""

    def review(
        self,
        ranked: list[ProteinRankingRecord],
    ) -> list[ProteinCriticRecord]:
        candidate_ids = [
            record.candidate_id
            for record in ranked
        ]

        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(
                "Duplicate candidate_id in critic input."
            )

        pool = [
            record
            for record in ranked
            if auxiliary_location(
                record.structural_evidence
            )
            is not None
        ]

        if len(pool) < 2:
            primary_dense: dict[str, int] = {}
            auxiliary_dense: dict[str, int] = {}
        else:
            primary_dense = {
                record.candidate_id: index
                for index, record in enumerate(
                    sorted(
                        pool,
                        key=lambda item: (
                            item.rank,
                            item.candidate_id,
                        ),
                    ),
                    start=1,
                )
            }
            auxiliary_dense = {
                record.candidate_id: index
                for index, record in enumerate(
                    sorted(
                        pool,
                        key=_auxiliary_sort_key,
                    ),
                    start=1,
                )
            }

        reviewed = []

        for record in ranked:
            location = auxiliary_location(
                record.structural_evidence
            )
            missing = location is None

            if missing or record.candidate_id not in primary_dense:
                agreement: Agreement = "unknown"
                conflict: bool | None = None
            else:
                span = len(pool) - 1
                discordance = abs(
                    primary_dense[record.candidate_id]
                    - auxiliary_dense[record.candidate_id]
                ) / span
                agreement = _agreement_from_discordance(
                    discordance
                )
                conflict = agreement == "low"

            assessment = CriticAssessment(
                agreement=agreement,
                ensemble_consistency="unknown",
                structural_conflict=conflict,
                missing_evidence=missing,
                confidence_note=NOTES[agreement],
            )

            reviewed.append(
                ProteinCriticRecord(
                    candidate_id=record.candidate_id,
                    rank=record.rank,
                    ranking_signal=record.ranking_signal,
                    ranking_score=record.ranking_score,
                    is_probability=record.is_probability,
                    recommended_as_hard_filter=(
                        record.recommended_as_hard_filter
                    ),
                    structural_evidence=dict(
                        record.structural_evidence
                    ),
                    critic_assessment=assessment,
                )
            )

        return reviewed
