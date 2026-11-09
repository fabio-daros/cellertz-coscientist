from dataclasses import dataclass, field
from typing import Any

from cellertz.schemas import ProteinCandidate
from cellertz.tools.protein_scoring.boltz2_ensemble import (
    Boltz2EnsembleSummary,
)
from cellertz.tools.protein_scoring.scoring import (
    BENCHMARK_VERSION,
    RANKING_METHOD,
)


STRUCTURAL_EVIDENCE_KEYS = (
    "ipsae",
    "ptm",
    "iptm_single",
    "boltz2_iptm_model_0",
    "boltz2_iptm_mean",
    "boltz2_iptm_median",
    "boltz2_iptm_std",
    "boltz2_iptm_min",
    "boltz2_iptm_max",
)

ADDITIONAL_EVIDENCE_KEYS = (
    "boltz2_iptm_model_0",
    "boltz2_iptm_mean",
    "boltz2_iptm_median",
    "boltz2_iptm_std",
    "boltz2_iptm_min",
    "boltz2_iptm_max",
    "boltz2_n_predictions",
)

DEFAULT_LIMITATIONS_V2 = (
    "ranking_score is raw ipSAE, not a binding probability",
    "ipSAE is not validated as a hard filter for experimental selection",
    "Boltz2 ensemble metrics are additional evidence, not a validated ranking signal",
    "ipTM dispersion measures ensemble/interface consistency, not experimental binding stability",
)


@dataclass(frozen=True)
class ProteinScoreResultV2:
    candidate_id: str
    baseline_score: float | None
    baseline_method: str = RANKING_METHOD
    structural_evidence: dict[str, float | None] = field(default_factory=dict)
    additional_evidence: dict[str, float | None] = field(default_factory=dict)
    is_probability: bool = False
    recommended_as_hard_filter: bool = False
    benchmark_version: str = BENCHMARK_VERSION
    provenance: dict[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = DEFAULT_LIMITATIONS_V2
    warnings: tuple[str, ...] = ()

    @property
    def ranking_score(self) -> float | None:
        return self.baseline_score

    @property
    def ranking_method(self) -> str:
        return self.baseline_method


class ProteinScoringToolV2:
    """Agent-facing scorer v2.

    Validated ranking remains raw ipSAE from Chow 2026 BCMA.
    Boltz2 ensemble statistics are attached as additional
    evidence until they beat ipSAE on the same hold-outs.
    """

    def score(
        self,
        candidate: ProteinCandidate,
        ensemble: Boltz2EnsembleSummary | None = None,
    ) -> ProteinScoreResultV2:
        ipsae = candidate.interface_metrics.get("ipsae")
        warnings: list[str] = []

        if ipsae is None:
            warnings.append(
                "Missing ipSAE; cannot assign a baseline_score."
            )

        if ensemble is None:
            warnings.append(
                "Missing Boltz2 ensemble; additional evidence is unavailable."
            )
        elif ensemble.iptm_model_0 is None:
            warnings.append(
                "Missing Boltz2 model_0 sample; "
                "boltz2_iptm_model_0 is unavailable."
            )

        structural_evidence = {
            "ipsae": ipsae,
            "ptm": None if ensemble is None else ensemble.ptm_mean,
            "iptm_single": candidate.interface_metrics.get("iptm"),
            "boltz2_iptm_model_0": (
                None if ensemble is None else ensemble.iptm_model_0
            ),
            "boltz2_iptm_mean": (
                None if ensemble is None else ensemble.iptm_mean
            ),
            "boltz2_iptm_median": (
                None if ensemble is None else ensemble.iptm_median
            ),
            "boltz2_iptm_std": (
                None if ensemble is None else ensemble.iptm_std
            ),
            "boltz2_iptm_min": (
                None if ensemble is None else ensemble.iptm_min
            ),
            "boltz2_iptm_max": (
                None if ensemble is None else ensemble.iptm_max
            ),
        }

        additional_evidence = {
            "boltz2_iptm_model_0": structural_evidence[
                "boltz2_iptm_model_0"
            ],
            "boltz2_iptm_mean": structural_evidence["boltz2_iptm_mean"],
            "boltz2_iptm_median": structural_evidence["boltz2_iptm_median"],
            "boltz2_iptm_std": structural_evidence["boltz2_iptm_std"],
            "boltz2_iptm_min": structural_evidence["boltz2_iptm_min"],
            "boltz2_iptm_max": structural_evidence["boltz2_iptm_max"],
            "boltz2_n_predictions": (
                None if ensemble is None else float(ensemble.n_predictions)
            ),
        }

        provenance = {
            "source": candidate.source,
            "target": candidate.target,
            "campaign": candidate.metadata.get("campaign"),
            "benchmark_version": BENCHMARK_VERSION,
            "baseline_method": RANKING_METHOD,
            "boltz2_n_predictions": (
                None if ensemble is None else ensemble.n_predictions
            ),
        }

        return ProteinScoreResultV2(
            candidate_id=candidate.id,
            baseline_score=ipsae,
            baseline_method=RANKING_METHOD,
            structural_evidence=structural_evidence,
            additional_evidence=additional_evidence,
            is_probability=False,
            recommended_as_hard_filter=False,
            benchmark_version=BENCHMARK_VERSION,
            provenance=provenance,
            limitations=DEFAULT_LIMITATIONS_V2,
            warnings=tuple(warnings),
        )

    def rank(
        self,
        candidates: list[ProteinCandidate],
        ensembles: dict[str, Boltz2EnsembleSummary] | None = None,
    ) -> list[ProteinScoreResultV2]:
        ensembles = ensembles or {}

        results = [
            self.score(
                candidate,
                ensembles.get(candidate.id),
            )
            for candidate in candidates
        ]

        return sorted(
            results,
            key=_baseline_sort_key,
        )


def _baseline_sort_key(
    result: ProteinScoreResultV2,
) -> tuple[int, float, str]:
    if result.baseline_score is None:
        return (1, 0.0, result.candidate_id)

    return (0, -result.baseline_score, result.candidate_id)
