---
id: boltz2_v2_bindcraft1_holdout
type: evidence
domain: protein_design
status: validated
version: 1
packet_key: bindcraft1
ipsae_ap: 0.3082
boltz2_std_inverted_ap: 0.3553
---

# Boltz2 v2 BindCraft Campaign 1 hold-out

Dataset: [[chow_bcma]]

## Results

| Signal | AP | ROC-AUC | Top-10 | Top-25 | Top-50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw ipSAE | 0.3082 | 0.6453 | 4 | 9 | 14 |
| paper ipTM | 0.2421 | 0.5647 | 1 | 7 | 13 |
| Boltz model_0 ipTM | 0.2052 | 0.5124 | 1 | 3 | 12 |
| Boltz mean ipTM | 0.2339 | 0.5707 | 1 | 5 | 13 |
| Boltz median ipTM | 0.2332 | 0.5660 | 1 | 6 | 15 |
| Boltz min ipTM | 0.2606 | 0.6049 | 0 | 7 | 13 |
| Boltz std inverted | 0.3553 | 0.6410 | 5 | 9 | 15 |

## Interpretation

Boltz std inverted has a local AP gain versus raw ipSAE on this
campaign (0.3553 vs 0.3082). That gain does not, by itself, replace
ipSAE or validate a composite score.

## Supports

- [[ipsae_primary_ranking]]
- [[boltz2_auxiliary_evidence]]
- [[no_validated_composite_score]]

## Part of

- [[protein_scoring_bcma_boltz2_v2]]
