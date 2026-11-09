from dataclasses import dataclass, field
from typing import Any

from cellertz.schemas import ProteinCandidate


BENCHMARK_VERSION = "chow2026_bcma_v1"
RANKING_METHOD = "ipsae"

EVIDENCE_FEATURES = (
    "ipsae",
    "iptm",
    "complex_plddt",
    "pae_interaction",
    "confidence",
)

DEFAULT_LIMITATIONS = (
    "ranking_score is raw ipSAE, not a binding probability",
    "ipSAE is not validated as a hard filter for experimental selection",
    "learned multivariate scorers were not promoted from Chow 2026 BCMA",
)


@dataclass(frozen=True)
class ProteinScoreResult:
    candidate_id: str
    ranking_score: float | None
    ranking_method: str = RANKING_METHOD
    evidence: dict[str, float | None] = field(default_factory=dict)
    is_probability: bool = False
    recommended_as_hard_filter: bool = False
    benchmark_version: str = BENCHMARK_VERSION
    provenance: dict[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = DEFAULT_LIMITATIONS
    warnings: tuple[str, ...] = ()


class ProteinScoringTool:
    """Agent-facing scorer for de novo protein binders.

    Chow 2026 BCMA validated raw ipSAE as a relative ranking
    signal. It did not validate ipSAE as a binding probability
    or as a hard gate for lab selection.
    """

    def score(
        self,
        candidate: ProteinCandidate,
    ) -> ProteinScoreResult:
        evidence = {
            feature: candidate.interface_metrics.get(feature)
            for feature in EVIDENCE_FEATURES
        }

        ranking_score = evidence["ipsae"]
        warnings: list[str] = []

        if ranking_score is None:
            warnings.append(
                "Missing ipSAE; cannot assign a ranking_score."
            )

        provenance = {
            "source": candidate.source,
            "target": candidate.target,
            "campaign": candidate.metadata.get("campaign"),
            "benchmark_version": BENCHMARK_VERSION,
            "ranking_method": RANKING_METHOD,
        }

        return ProteinScoreResult(
            candidate_id=candidate.id,
            ranking_score=ranking_score,
            ranking_method=RANKING_METHOD,
            evidence=evidence,
            is_probability=False,
            recommended_as_hard_filter=False,
            benchmark_version=BENCHMARK_VERSION,
            provenance=provenance,
            limitations=DEFAULT_LIMITATIONS,
            warnings=tuple(warnings),
        )

    def rank(
        self,
        candidates: list[ProteinCandidate],
    ) -> list[ProteinScoreResult]:
        results = [
            self.score(candidate)
            for candidate in candidates
        ]

        return sorted(
            results,
            key=_ranking_sort_key,
        )


def _ranking_sort_key(
    result: ProteinScoreResult,
) -> tuple[int, float]:
    if result.ranking_score is None:
        return (1, 0.0)

    return (0, -result.ranking_score)
