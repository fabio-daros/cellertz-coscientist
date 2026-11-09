---
id: boltz2_v2_rfd5_holdout
type: evidence
domain: protein_design
status: validated
version: 1
packet_key: rfd5
ipsae_ap: 0.2501
boltz2_std_inverted_ap: 0.0962
---

# Boltz2 v2 RFD5 hold-out

Dataset: [[chow_bcma]]

## Results

| Signal | AP | ROC-AUC | Top-10 | Top-25 | Top-50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw ipSAE | 0.2501 | 0.8214 | 3 | 4 | 7 |
| paper ipTM | 0.1589 | 0.7763 | 1 | 4 | 7 |
| Boltz model_0 ipTM | 0.1159 | 0.6875 | 1 | 3 | 4 |
| Boltz mean ipTM | 0.1542 | 0.7242 | 1 | 4 | 5 |
| Boltz median ipTM | 0.1971 | 0.7460 | 2 | 4 | 5 |
| Boltz min ipTM | 0.1211 | 0.7242 | 0 | 3 | 6 |
| Boltz std inverted | 0.0962 | 0.6210 | 1 | 1 | 4 |

## Interpretation

On RFD5, raw ipSAE remains stronger than Boltz std inverted
(0.2501 vs 0.0962 AP). The BindCraft Campaign 1 local gain does not
generalize, so it does not justify replacing ipSAE or constructing a
composite score.

## Supports

- [[ipsae_primary_ranking]]
- [[boltz2_auxiliary_evidence]]
- [[no_validated_composite_score]]

## Part of

- [[protein_scoring_bcma_boltz2_v2]]
