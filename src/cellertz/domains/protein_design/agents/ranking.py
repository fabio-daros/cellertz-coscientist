from dataclasses import dataclass, field

from cellertz.schemas import ProteinCandidate
from cellertz.tools.protein_scoring.boltz2_ensemble import (
    Boltz2EnsembleSummary,
)
from cellertz.tools.protein_scoring.scoring import (
    BENCHMARK_VERSION,
)
from cellertz.tools.protein_scoring.scoring_v2 import (
    ProteinScoreResultV2,
    ProteinScoringToolV2,
)


@dataclass(frozen=True)
class ProteinRankingRecord:
    candidate_id: str
    rank: int
    ranking_signal: str
    ranking_score: float | None
    is_probability: bool = False
    recommended_as_hard_filter: bool = False
    benchmark_version: str = BENCHMARK_VERSION
    structural_evidence: dict[str, float | None] = field(
        default_factory=dict
    )
    additional_evidence: dict[str, float | None] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "ranking_signal": self.ranking_signal,
            "ranking_score": self.ranking_score,
            "is_probability": self.is_probability,
            "recommended_as_hard_filter": (
                self.recommended_as_hard_filter
            ),
            "benchmark_version": self.benchmark_version,
            "structural_evidence": dict(
                self.structural_evidence
            ),
        }


def ranking_sort_key(
    result: ProteinScoreResultV2,
) -> tuple[int, float, str]:
    if result.ranking_score is None:
        return (1, 0.0, result.candidate_id)

    return (0, -result.ranking_score, result.candidate_id)


class ProteinRankingAgentV1:
    """Deterministic ranking by raw ipSAE.

    Auxiliary Boltz2 evidence is copied onto the record
    and is never used to sort.
    """

    def __init__(
        self,
        scorer: ProteinScoringToolV2 | None = None,
    ) -> None:
        self._scorer = scorer or ProteinScoringToolV2()

    def rank(
        self,
        results: list[ProteinScoreResultV2],
    ) -> list[ProteinRankingRecord]:
        candidate_ids = [
            result.candidate_id
            for result in results
        ]

        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(
                "Duplicate candidate_id in ranking input."
            )

        ordered = sorted(
            results,
            key=ranking_sort_key,
        )

        return [
            ProteinRankingRecord(
                candidate_id=result.candidate_id,
                rank=index,
                ranking_signal=result.ranking_method,
                ranking_score=result.ranking_score,
                is_probability=result.is_probability,
                recommended_as_hard_filter=(
                    result.recommended_as_hard_filter
                ),
                benchmark_version=result.benchmark_version,
                structural_evidence=dict(
                    result.structural_evidence
                ),
                additional_evidence=dict(
                    result.additional_evidence
                ),
            )
            for index, result in enumerate(
                ordered,
                start=1,
            )
        ]

    def rank_candidates(
        self,
        candidates: list[ProteinCandidate],
        ensembles: dict[str, Boltz2EnsembleSummary] | None = None,
    ) -> list[ProteinRankingRecord]:
        ensembles = ensembles or {}

        results = [
            self._scorer.score(
                candidate,
                ensembles.get(candidate.id),
            )
            for candidate in candidates
        ]

        return self.rank(results)
