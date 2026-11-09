---
id: protein_scoring_bcma_boltz2_v2
type: validated_evidence
domain: protein_design
status: validated
version: 1
validated_at: 2026-09-19
reviewed_at: 2026-09-19
review_reason: fixture baseline after 3F.3
local_boltz_gain: BindCraft1 only
generalizes_to_rfd5: false
ranking_decision: raw ipSAE remains primary
---

# Protein Scoring BCMA — Boltz2 v2

## Question

Which structural signal should be used as the primary ranking signal for
BCMA protein-design candidates?

## Validated conclusions

- [[ipsae_primary_ranking]]
- [[boltz2_auxiliary_evidence]]
- [[no_validated_composite_score]]

## Dataset

- [[chow_bcma]]

## Evidence

- [[boltz2_v2_global_benchmark]]
- [[boltz2_v2_bindcraft1_holdout]]
- [[boltz2_v2_rfd5_holdout]]

## Provenance

- Pre-unblind tag: `protein-boltz2-benchmark-v2`
- Freeze commit: `be40731`
- Evaluation commit: `2e3a4dc`
- Blind summary SHA256:
  `d174f2a00629d1011619b6b1575b1a0a320c8ec4875fc3f6be89976fdc7d3751`
- Unblinded CSV SHA256:
  `f6c5fb9750ead6bf17ec6cd7023662036b0abde1ce78fe16c7474aaa2ee30596`
- Results CSV SHA256:
  `9e0e23a2e0b1410dd9f637c21318d652437cbb5bae4b55a4971d61de83860a10`

## Interpretation

Raw ipSAE remains the validated primary structural ranking signal.

Boltz2 ensemble metrics remain auxiliary structural evidence.

No ipSAE + Boltz2 composite score is currently validated.
