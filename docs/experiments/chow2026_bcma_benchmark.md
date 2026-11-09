# Chow 2026 — BCMA Benchmark

Technical record of the first Cellertz protein-design benchmark, derived from Chow et al., *Nature Biomedical Engineering* (2026), Supplementary Table 5.

This document is the source of truth for later reporting. It records what was tested, what was validated, and what was rejected.

## Decision (frozen)

**Validated ranking signal:** raw ipSAE  
**Validated use:** relative candidate ranking inside a design set  

**Not validated:**

- absolute binding probability
- hard threshold for lab selection
- learned multivariate scorer

**Linear Scorer v1:** rejected as production / default scorer.

Chow 2026 is an **external benchmark**, not a sufficient training set for a generalizable learned scorer. A future model requires additional Cellertz / internal labels.

The agent-facing contract is `ProteinScoringTool` v1:

- `ranking_score` = raw ipSAE
- `is_probability = False`
- `recommended_as_hard_filter = False`
- `benchmark_version = chow2026_bcma_v1`

## Cohort

| Stage | n |
|---|---|
| Public protein designs in the supplement | 1,758 |
| BCMA candidates (`target == BCMA`) | 791 |
| BCMA with YSD 1000 nM ground truth | 766 |
| Experimental YSD+ binders | 34 |
| Deep-characterized BCMA (Kd present) | 22 |

Primary label: `binder_by_YSD_1000nM`.  
Prevalence in the 766: **34 / 766 = 0.0444**.

YSD 500 nM and 100 nM are secondary labels. Kd, staining, coculture, and CAR activity are experimental labels but **100% missing** on the 766 YSD-labeled rows. They exist only on the 22-deep subset and must stay hidden from any scorer.

`binder_creation_method` is **non-informative in this subset**: it is constantly `BindCraft` on the 766 YSD rows, while `campaign` still preserves RFD vs BindCraft provenance. Semantics differ from campaign provenance.

There is no scaffold / template / family column in the Excel. Sequence families were computed from sequences, not from `binder_id`.

## Structural Baseline v0 — univariate ranking

766 BCMA candidates with YSD labels. Higher-is-better except PAE (inverted).

**Top-K enrichment (YSD 1000 nM)**

| Feature | Top 10 EF | Top 50 EF | Top 100 EF |
|---|---|---|---|
| ipSAE | 0.00x | 1.35x | **2.70x** |
| ipTM | 0.00x | 1.80x | 2.48x |
| confidence | 2.25x | **2.25x** | 1.58x |
| complex_pLDDT | 2.25x | 0.90x | 1.80x |
| PAE interaction | 0.00x | 0.90x | 1.35x |

**Global ranking (Average Precision)**

Random AP = 0.0444.

| Feature | AP | ROC-AUC | AP enrichment |
|---|---|---|---|
| **ipSAE** | **0.0924** | **0.7535** | **2.08x** |
| ipTM | 0.0887 | 0.7413 | 2.00x |
| confidence | 0.0774 | 0.6879 | 1.74x |
| PAE interaction | 0.0634 | 0.6377 | 1.43x |
| complex_pLDDT | 0.0614 | 0.6149 | 1.38x |

ipSAE is the best single global ranking signal. Confidence is better at Top 50; ipSAE / ipTM only enrich later (Top 100). pLDDT and PAE are at or below chance at Top 50.

**Spearman (direction-adjusted)**

- ipSAE ↔ ipTM = 0.91
- confidence ↔ pLDDT = 0.90
- ipTM ↔ PAE = 0.84
- ipSAE / ipTM ↔ confidence ≈ 0.70

The five metrics are two redundant clusters, not five independent signals.

## Structural Baseline v1 — percentile composite

`mean(percentile(ipSAE), percentile(confidence))`.

| Model | AP | ROC-AUC |
|---|---|---|
| ipSAE alone | **0.0924** | **0.7535** |
| ipSAE + confidence | 0.0925 | 0.7415 |

The composite does not beat ipSAE globally. It stitches confidence’s Top 50 (5 hits, 2.25x) to ipSAE’s Top 100 (12 hits, 2.70x). Top 10 remains empty (all decoys). **Structural Baseline v1 is therefore ipSAE alone.**

## Campaign-aware validation

Positive campaigns used as outer hold-outs:

| Campaign | n | positives | prevalence |
|---|---|---|---|
| BindCraft 1 | 125 | 26 | 20.8% |
| RFDiffusion 5 | 134 | 8 | 6.0% |

Negative challenge campaigns (0 binders): RFD 1 (100), RFD 2 (100), RFD 3 (200), RFD 4 (107).

**ipSAE on the two positive hold-outs** (logistic of ipSAE is monotonic and does not change rank):

| Held out | AP | ROC-AUC |
|---|---|---|
| BindCraft 1 | 0.3082 | 0.6453 |
| RFDiffusion 5 | 0.2501 | 0.8214 |

**ML ablation v0** — same `StandardScaler` + `LogisticRegression(C=1.0, class_weight="balanced")`, no hyperparameter search:

| Held out | ipSAE only AP | ipSAE+confidence AP | all 5 AP |
|---|---|---|---|
| BindCraft 1 | **0.3082** | 0.2893 | 0.2773 |
| RFDiffusion 5 | 0.2501 | 0.2513 | **0.2968** |

More features do not generalize. BindCraft 1 holds 26 of 34 positives and gets worse as features are added. RFDiffusion 5 improves. That is campaign-dependent signal / feature instability, not a license to ship a five-feature model.

## Threshold transfer — ipSAE is not a hard gate

Cutoffs were calibrated for 50% and 80% recall on one positive campaign and applied to the other plus RFD 1–4.

**A. Calibrate BindCraft 1**

| Target recall | Threshold | RFD5 TP/selected | Negative-campaign FPs |
|---|---|---|---|
| 50% | 0.8812 | 4 / 28 | **114** |
| 80% | 0.8572 | 7 / 50 | **192** |

Recall transfers to RFD5 (50% → 50%, 80% → 87.5%). The experimental cost on campaigns with zero binders is 114–192 false positives.

**B. Calibrate RFDiffusion 5**

| Target recall | Threshold | BindCraft recall | Negative-campaign FPs |
|---|---|---|---|
| 50% | 0.8989 | **27%** (7 / 26) | 56 |
| 80% | 0.8699 | **62%** (16 / 26) | 149 |

The cutoff is not portable in this direction. BindCraft binders sit lower in the ipSAE tail.

Negative campaigns are not structurally “ugly”: RFD 2 has the highest max ipSAE (**0.948**) and the highest q95 (**0.924**). BindCraft 1, with 26 binders, has the highest median and the **lowest** tail. Several real binders sit below decoys with ipSAE > 0.94.

**ipSAE as hard filter: no. ipSAE as ranking signal: yes.**

## Sequence leakage audit

Global identity = identities / alignment length, gaps in the denominator. Biopython `PairwiseAligner(scoring="blastp")`, mode `global`. Single-linkage clusters. 292,995 pairs.

| | 90% identity | 80% identity |
|---|---|---|
| Pairs ≥ threshold | ⊂ 25 pairs ≥ 80% | **25** |
| Clusters | 763 | 741 |
| Singletons | 760 | 716 |
| Largest cluster | 2 | 2 |
| Cross-campaign clusters | **0** | **0** |
| Clusters with binders | **34** | **31** |

The 34 binders are 34 families at 90% and 31 families at 80% (three binder pairs merge). Campaign hold-out did **not** leak close sequence relatives. The dataset is sequence-diverse; the limiting factor is the number of independent positive labels, not redundancy.

## Training Dataset v1

Frozen contract: `BCMA_FEATURES_V1` (57 enumerated columns). A new Excel column cannot enter the model silently.

| Field | Role |
|---|---|
| `candidate_id`, `campaign`, `sequence` | identity / split / input |
| `sequence_cluster_90`, `sequence_cluster_80` | family metadata |
| 57 pre-experimental features | model inputs |
| `label_YSD_1000nM` | hidden primary label |

Safe features = `binder_seq_net_charge` + `n_hydrophobic_residues` + numeric non-constant `Average_*`, excluding experimental patterns and `Average_ligand_iptm` / `Average_InterfaceAAs`.

Artifacts:

- `docs/experiments/bcma_training_dataset_v1.csv`
- `src/cellertz/evaluation/datasets/bcma_features.py`

## Linear Scorer v1 — rejected

`StandardScaler` + `LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000)`. **No C search.** Tuning on these two outer folds would convert the benchmark into a development set.

| | BindCraft 1 | RFDiffusion 5 |
|---|---|---|
| Train positives | 8 / 641 | 26 / 632 |
| ipSAE AP | **0.3082** | **0.2501** |
| linear_v1 AP | 0.2757 | 0.1314 |
| ipSAE ROC | **0.6453** | **0.8214** |
| linear_v1 ROC | 0.5672 | 0.7004 |
| ipSAE Top 10 / 25 / 50 | 4 / 9 / 14 | 3 / 4 / 7 |
| linear_v1 Top 10 / 25 / 50 | 2 / 5 / 14 | 1 / 4 / 4 |

The 57-feature model is worse on **both** AP and ROC in **both** positive hold-outs. Coefficients do not transfer (BindCraft hold-out: dG, charge, pTM; RFD5 hold-out: hydrophobics, pDockQ, beta sheet). That is expected with 34 positives, 57 collinear features, and one fold trained on 8 positives.

Artifact: `docs/experiments/bcma_linear_scorer_v1_results.csv`

## Implications for the agent

```
Protein Design Agent
        │
        ▼
ProteinScoringTool v1
        │
        ├── ranking_score = raw ipSAE
        ├── evidence: ipTM, pLDDT, PAE, confidence
        ├── is_probability = False
        └── recommended_as_hard_filter = False
        │
        ▼
shortlist + critic / additional evidence
        │
        ▼
experimental validation
```

ipSAE may rank candidates. It must not be treated as P(binder) or as `ipSAE > threshold → send to lab`.

A learned scorer is a future component, to be trained when Cellertz has enough independent experimental labels to beat this benchmark on the same campaign-aware protocol.

## Repro commands

```bash
docker compose run --rm app pytest
docker compose run --rm app python scripts/build_bcma_training_dataset_v1.py
docker compose run --rm app python scripts/evaluate_bcma_linear_scorer_v1.py
```
