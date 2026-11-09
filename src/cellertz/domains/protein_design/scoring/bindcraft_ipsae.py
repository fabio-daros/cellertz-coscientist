from __future__ import annotations

from dataclasses import replace
from typing import Any

from cellertz.domains.protein_design.agents.generation import (
    NormalizedCandidate,
)
from cellertz.domains.protein_design.evidence.bindcraft_ipsae import (
    BindCraftIpSAEEvidence,
    BindCraftIpSAEEvidenceProvider,
)
from cellertz.schemas import ProteinCandidate
from cellertz.tools.protein_scoring.scoring_v2 import (
    ProteinScoreResultV2,
    ProteinScoringToolV2,
)


class BindCraftIpSAEScoreProvider:
    """Bridge BindCraft structural evidence into scoring v2."""

    def __init__(
        self,
        *,
        evidence_provider: BindCraftIpSAEEvidenceProvider,
        scorer: ProteinScoringToolV2 | None = None,
    ) -> None:
        self.evidence_provider = evidence_provider
        self.scorer = scorer or ProteinScoringToolV2()

    def __call__(
        self,
        candidates: tuple[NormalizedCandidate, ...],
    ) -> list[ProteinScoreResultV2]:
        return [
            self.score(candidate)
            for candidate in candidates
        ]

    def score(
        self,
        candidate: NormalizedCandidate,
    ) -> ProteinScoreResultV2:
        evidence = self.evidence_provider.evaluate(candidate)

        protein = ProteinCandidate(
            id=candidate.candidate_id,
            sequence=candidate.sequence,
            design_type="protein",
            source=candidate.generator,
            target=candidate.target_id,
            metadata=dict(candidate.generator_metadata),
            interface_metrics={
                "ipsae": evidence.ranking_value,
            },
        )

        result = self.scorer.score(protein)

        provenance = {
            **result.provenance,
            "ipsae_evidence": _evidence_to_dict(
                evidence,
                self.evidence_provider,
            ),
        }

        return replace(
            result,
            provenance=provenance,
        )


def _evidence_to_dict(
    evidence: BindCraftIpSAEEvidence,
    provider: BindCraftIpSAEEvidenceProvider,
) -> dict[str, Any]:
    config = provider.ipsae_config

    return {
        "candidate_id": evidence.candidate_id,
        "design": evidence.design,
        "execution_id": evidence.execution_id,
        "protocol_id": evidence.protocol_id,
        "model_aggregation": evidence.model_aggregation,
        "n_models": evidence.n_models,
        "ipsae_mean": evidence.ipsae_mean,
        "ipsae_min": evidence.ipsae_min,
        "ipsae_max": evidence.ipsae_max,
        "ranking_value": evidence.ranking_value,
        "is_probability": evidence.is_probability,
        "recommended_as_hard_filter": (
            evidence.recommended_as_hard_filter
        ),
        "protocol_parameters": {
            "pae_cutoff": config.pae_cutoff,
            "distance_cutoff": config.distance_cutoff,
            "chain_a": config.chain_a,
            "chain_b": config.chain_b,
            "within_model_aggregation": config.aggregation,
        },
        "models": [
            {
                "model_number": model.model_number,
                "ipsae": model.ipsae,
                "ipsae_ab": model.ipsae_ab,
                "ipsae_ba": model.ipsae_ba,
                "pdb_sha256": model.pdb_sha256,
                "pae_sha256": model.pae_sha256,
                "residue_order_sha256": model.residue_order_sha256,
            }
            for model in evidence.models
        ],
    }
