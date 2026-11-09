---
id: boltz2_v2_global_benchmark
type: evidence
domain: protein_design
status: validated
version: 1
packet_key: global
ipsae_ap: 0.0924
boltz2_mean_ap: 0.0825
---

# Boltz2 v2 global BCMA benchmark

Dataset: [[chow_bcma]]

## Results

| Signal | AP | ROC-AUC | Top-10 | Top-25 | Top-50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw ipSAE | 0.0924 | 0.7535 | 0 | 1 | 3 |
| paper ipTM | 0.0887 | 0.7413 | 0 | 2 | 4 |
| Boltz model_0 ipTM | 0.0690 | 0.6905 | 0 | 1 | 2 |
| Boltz mean ipTM | 0.0825 | 0.7320 | 0 | 1 | 2 |
| Boltz median ipTM | 0.0834 | 0.7317 | 0 | 1 | 4 |
| Boltz min ipTM | 0.0839 | 0.7360 | 0 | 0 | 0 |
| Boltz std inverted | 0.0770 | 0.6828 | 0 | 1 | 5 |

## Supports

- [[ipsae_primary_ranking]]
- [[boltz2_auxiliary_evidence]]
- [[no_validated_composite_score]]

## Part of

- [[protein_scoring_bcma_boltz2_v2]]
