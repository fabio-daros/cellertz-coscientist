# Boltz2 v2 — evaluation protocol (frozen)

Frozen **before** the 766-candidate unblind. This document is the ruler, not a result.

## Pilot (already run, n=5)

The pilot validated instrumentation only:

- 5/5 diffusion samples per candidate
- no NaN in confidence metrics
- ipTM std was small (0.002–0.009)

It did **not** show improved prioritization. Ranks by ipSAE and by Boltz mean ipTM were identical. The five cases were chosen as extremes and must not be used for AP, ROC, Top-K, or any benchmark claim.

`Δ = Boltz ipTM − ipSAE` is **not** a scientific quantity. The two scores are not on the same scale. An absolute lift (for example 0.744 → 0.911) is not “recovery.” Only rank and hold-out metrics on the 766 can support a prioritization claim.

## Baseline (unchanged from v1)

| Metric | Direction |
|---|---|
| raw ipSAE | higher = better |

`ranking_score` for ProteinScoringTool v1/v2 remains raw ipSAE until this protocol says otherwise.

## Comparators (v2 evidence)

| Metric | Direction | Role |
|---|---|---|
| Chow paper ipTM | higher = better | published structural score |
| Boltz `model_0` ipTM | higher = better | single sample |
| Boltz mean ipTM | higher = better | ensemble location |
| Boltz median ipTM | higher = better | ensemble location |
| Boltz min ipTM | higher = better | conservative ensemble |
| Boltz ipTM std | lower = more consistent | ensemble consistency, not stability |

Descriptive only (not a ranking competitor):

| Metric | Role |
|---|---|
| Boltz max ipTM | upper envelope |

Do not invent a composite (weighted sum, Δ, learned mix). Do not tune weights or thresholds on YSD.

## Ruler (same as v1)

Primary: **Average Precision**  
Secondary: **ROC-AUC**  
Also report: Top-K / enrichment factor at **K = 10, 25, 50**

Campaign hold-outs (outer, frozen):

- train on remaining campaigns, test **BCMA BindCraft Campaign 1**
- train on remaining campaigns, test **BCMA RFDiffusion Campaign 5**

No hyperparameter search on these two folds. A v2 metric replaces ipSAE as the default ranking baseline only if it **consistently** beats ipSAE on this protocol.

## Full-run configuration (frozen)

| Item | Value |
|---|---|
| Boltz | 2.2.1 |
| Model | `boltz2` |
| `diffusion_samples` | 5 |
| `seed` | 0 |
| `--use_msa_server` | **off** |
| Chain A (BCMA) | `data/processed/boltz2/bcma_target.csv` |
| Chain B (binder) | `msa: empty` |
| Recycling / sampling steps | Boltz defaults (3 / 200) |
| Accelerator | `gpu` (MPS on this Mac; CUDA on a GPU host) |
| `--no_kernels` | on for this Mac; omit on CUDA hosts that support kernels |

The BCMA MSA was computed once in the pilot (ColabFold). All five pilot CSVs were byte-identical (`sha256 648df93d5851d49a…`). That file is the frozen evolutionary context for all 766 complexes.

N=5 is frozen for the full run. If ipTM std later looks informative, a **stratified subset** may be repeated at larger N. Do not change N after seeing the 766 YSD labels.

## Execution

Run in resumable batches of ~40. After each batch the collector must confirm 5/5 confidence JSONs. Retry only incompletes. Unblind YSD only after the collector finishes.
