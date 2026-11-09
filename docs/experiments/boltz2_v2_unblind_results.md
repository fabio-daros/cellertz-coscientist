# Boltz2 v2 BCMA Benchmark — Unblinded Results

## Status

The Boltz2 v2 BCMA benchmark was frozen before YSD unblinding.

Pre-unblind freeze:

- Tag: `protein-boltz2-benchmark-v2`
- Freeze commit: `be40731`
- Blind summary:
  `outputs/bcma_boltz2_v2_summary_blind.csv`
- Blind summary SHA256:
  `d174f2a00629d1011619b6b1575b1a0a320c8ec4875fc3f6be89976fdc7d3751`
- Candidates: 766
- Boltz2 samples: 3830
- Samples per candidate: 5
- Missing values: 0
- YSD was not accessed before the freeze.

The YSD labels were opened once after the freeze and evaluated using
the pre-specified protocol.

No weights, thresholds, composite scores, or metric selections were
tuned using YSD.

## Primary conclusion

**Decision: Scenario 3.**

Raw ipSAE remains the validated Structural Baseline.

Boltz2 ensemble metrics provide additional structural evidence but do
not replace ipSAE as the ranking signal.

No Boltz2 comparator consistently outperformed raw ipSAE in Average
Precision across the global benchmark and the two positive campaign
hold-outs.

## Global benchmark

BCMA benchmark:

- n = 766
- YSD positive = 34
- YSD negative = 732

| Signal | AP | ROC-AUC | Top-10 | Top-25 | Top-50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw ipSAE | **0.0924** | **0.7535** | 0 | 1 | 3 |
| paper ipTM | 0.0887 | 0.7413 | 0 | 2 | 4 |
| Boltz model_0 ipTM | 0.0690 | 0.6905 | 0 | 1 | 2 |
| Boltz mean ipTM | 0.0825 | 0.7320 | 0 | 1 | 2 |
| Boltz median ipTM | 0.0834 | 0.7317 | 0 | 1 | 4 |
| Boltz min ipTM | 0.0839 | 0.7360 | 0 | 0 | 0 |
| Boltz std (inverted) | 0.0770 | 0.6828 | 0 | 1 | 5 |

## Campaign hold-outs

### BindCraft Campaign 1

| Signal | AP | ROC-AUC | Top-10 | Top-25 | Top-50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw ipSAE | **0.3082** | **0.6453** | 4 | 9 | 14 |
| paper ipTM | 0.2421 | 0.5647 | 1 | 7 | 13 |
| Boltz model_0 | 0.2052 | 0.5124 | 1 | 3 | 12 |
| Boltz mean | 0.2339 | 0.5707 | 1 | 5 | 13 |
| Boltz median | 0.2332 | 0.5660 | 1 | 6 | 15 |
| Boltz min | 0.2606 | 0.6049 | 0 | 7 | 13 |
| Boltz std (inverted) | **0.3553** | 0.6410 | 5 | 9 | 15 |

### RFD5

| Signal | AP | ROC-AUC | Top-10 | Top-25 | Top-50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw ipSAE | **0.2501** | **0.8214** | 3 | 4 | 7 |
| paper ipTM | 0.1589 | 0.7763 | 1 | 4 | 7 |
| Boltz model_0 | 0.1159 | 0.6875 | 1 | 3 | 4 |
| Boltz mean | 0.1542 | 0.7242 | 1 | 4 | 5 |
| Boltz median | 0.1971 | 0.7460 | 2 | 4 | 5 |
| Boltz min | 0.1211 | 0.7242 | 0 | 3 | 6 |
| Boltz std (inverted) | 0.0962 | 0.6210 | 1 | 1 | 4 |

## Interpretation

The lower Boltz2 ensemble dispersion signal showed a local improvement
in BindCraft Campaign 1:

- AP: 0.3553 vs 0.3082 for ipSAE
- Top-10: 5 vs 4

However, this signal did not generalize.

In RFD5:

- Boltz std inverted AP: 0.0962
- ipSAE AP: 0.2501

Therefore the apparent BindCraft Campaign 1 improvement is treated as
campaign-dependent evidence rather than justification for replacing the
baseline or constructing a composite score.

## ProteinScoringTool v2 decision

The v2 contract remains:

- `ranking_score = raw ipSAE`
- `is_probability = false`
- `recommended_as_hard_filter = false`

Boltz2 ensemble metrics remain structural evidence:

- ipTM mean
- ipTM median
- ipTM minimum
- ipTM standard deviation
- model-level ipTM
- optional pTM / protein-ipTM / confidence-derived metrics

They may be exposed to Ranking, Critic, or Prioritization agents as
additional evidence, but they are not currently validated as the primary
ranking signal.

## Scientific interpretation

This benchmark does not show that Boltz2 structural predictions are
unhelpful.

It shows that, for this BCMA/YSD benchmark, Boltz2 ipTM ensemble
statistics do not provide a consistently superior univariate ranking of
experimental binders compared with raw ipSAE.

The ensemble can therefore be retained as orthogonal structural evidence
for downstream agent reasoning.

## Generated artifacts

Unblinded artifacts:

- `outputs/bcma_boltz2_v2_unblinded.csv`
- `outputs/bcma_boltz2_v2_results.csv`

Evaluation implementation:

- `scripts/evaluate_boltz2_v2.py`

The generated CSV artifacts remain outside Git.

## Post-unblind provenance

SHA256 of the generated artifacts:

- `outputs/bcma_boltz2_v2_unblinded.csv`
  `f6c5fb9750ead6bf17ec6cd7023662036b0abde1ce78fe16c7474aaa2ee30596`
- `outputs/bcma_boltz2_v2_results.csv`
  `9e0e23a2e0b1410dd9f637c21318d652437cbb5bae4b55a4971d61de83860a10`

Provenance chain:

```
blind CSV → hash
        ↓
pre-unblind tag protein-boltz2-benchmark-v2 (be40731)
        ↓
YSD opened once
        ↓
scripts/evaluate_boltz2_v2.py
        ↓
unblinded CSV + results CSV → hashes
        ↓
documented conclusion: Scenario 3
```

The pre-unblind tag must remain on `be40731`. This results commit does
not move it.
