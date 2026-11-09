# Protein Ranking and Critic Agents

Architecture contract. Agents are **not implemented** in this document.

This specification sits between scoring and experimental decision:

```
Generation Agent
      ↓
ProteinScoringTool
      ↓
 Structural Evidence Package
      ↓
 Ranking Agent  →  initial order
 Critic Agent   →  risk / agreement annotations
      ↓
 Prioritization Agent / Supervisor
      ↓
 Experiment  →  Feedback
```

The Boltz2 v2 BCMA unblind (`docs/experiments/boltz2_v2_unblind_results.md`,
tag `protein-boltz2-benchmark-v2` at `be40731`) decided **Scenario 3**:

- Primary ranking signal: **raw ipSAE**
- Boltz2 ensemble metrics: **auxiliary structural evidence**
- No composite, weight, or threshold was promoted

This document turns that scientific result into agent responsibilities.

## Frozen constraints

These rules are not preferences. They are the current scientific contract.

1. `ranking_score = raw ipSAE`, descending. Higher is better.
2. `is_probability = false`. ipSAE is not P(binder).
3. `recommended_as_hard_filter = false`. No `ipSAE > t → send to lab`.
4. Boltz2 ipTM mean / median / min / model_0 / std do **not** replace ipSAE.
5. `Δ = Boltz ipTM − ipSAE` is not a scientific quantity. The scores are
   on different scales.
6. Boltz ipTM std measures **ensemble/interface consistency**, not
   experimental binding stability.
7. The Critic must not become a hidden scorer.
8. The Prioritization Agent may change the **experimental portfolio**.
   It must not rewrite `ranking_score`.

Any future change of the primary ranking signal requires a new frozen
benchmark that consistently beats ipSAE on the same protocol. Until then,
Ranking Agent code must not invent one.

## Pipeline layers

| Layer | Owner | Question | May change rank? |
| --- | --- | --- | --- |
| 1. Generation | RFdiffusion / BindCraft / mBER / other | What candidates exist? | n/a |
| 2. Scoring | `ProteinScoringTool` | What structural numbers are attached? | no |
| 3a. Ranking | Ranking Agent | What is the initial order? | **defines** rank |
| 3b. Critic | Critic Agent | Do auxiliary metrics support, contradict, or leave that position uncertain? | **no** |
| 4. Decision | Prioritization / Supervisor | What subset goes to the wet lab, under experimental constraints? | no; selects a portfolio |
| 5. Experiment | wet lab | What bound? | n/a |
| 6. Feedback | experimental results | What should the next round learn? | n/a |

Ranking and Critic are both reasoning agents, but they have different
jobs. Mixing them recreates the composite score that the benchmark
rejected.

## Structural Evidence Package

`ProteinScoringTool` emits numbers. Agents consume a package, not a
single opaque quality score.

```
Structural Evidence Package
│
├── Primary ranking signal
│     raw ipSAE                  ranking_score
│
└── Auxiliary evidence
      paper ipTM                 interface iptm / iptm_single
      Boltz2 model_0 ipTM        single diffusion sample
      Boltz2 mean ipTM           ensemble location
      Boltz2 median ipTM         ensemble location
      Boltz2 min ipTM            conservative ensemble
      Boltz2 std                 ensemble consistency (lower = more consistent)
      optional pTM / protein_ipTM / confidence-derived metrics
```

Boltz max ipTM may be stored as a descriptive envelope. It is **not** a
ranking competitor and must not drive Rank or Critic decisions.

Missing auxiliary fields are allowed. A missing Boltz2 ensemble does not
block ranking by ipSAE. It makes the Critic assessment `unknown`.

Current code mapping (`ProteinScoringToolV2`):

| Package field | Tool field |
| --- | --- |
| raw ipSAE | `ranking_score` / `baseline_score` / `structural_evidence["ipsae"]` |
| paper ipTM | `structural_evidence["iptm_single"]` |
| Boltz2 mean / median / min / std / max | `additional_evidence["boltz2_iptm_*"]` |
| Boltz2 n samples | `additional_evidence["boltz2_n_predictions"]` |
| Boltz2 model_0 ipTM | `structural_evidence["boltz2_iptm_model_0"]` |

`boltz2_iptm_model_0` is the ipTM of the sample whose model index is
`0`, not the first element of a sample list. If that sample is absent
the field is `None`; mean, median, or another sample must not be
substituted. `ranking_score` remains raw ipSAE.

The tool remains the source of numbers. Agents must not recompute a
parallel ranking from YAML, Excel, or free-text.

## Ranking Agent

### Question

> What is the initial order of candidates?

### Input

A list of `ProteinScoreResultV2` (or the equivalent Structural Evidence
Package) for one design set.

The Ranking Agent does not read YSD labels. It does not read developability,
cost, or cluster membership. Those belong later.

### Rule

Primary sort, fully deterministic:

1. raw ipSAE descending
2. `candidate_id` ascending as deterministic tie-breaker

```
scored first, then missing ipSAE
within scored: ipSAE desc, candidate_id asc
within missing: candidate_id asc
```

Equal ipSAE values must not depend on input order, hash seed, or
runtime. No other metric enters this sort.

Forbidden:

```
0.5 × ipSAE + 0.3 × Boltz mean ipTM + ...
rank by Boltz std
rank by critic agreement
rank by “overall structural quality”
```

### Output

One record per candidate. Numbers stay numbers.

```json
{
  "candidate_id": "design_001",
  "ranking_score": 0.91,
  "ranking_signal": "ipsae",
  "rank": 3,
  "is_probability": false,
  "recommended_as_hard_filter": false,
  "benchmark_version": "chow2026_bcma_v1"
}
```

`rank` is 1-based inside the current design set. It is not a global
quality grade.

### Must

- Be deterministic given the same scores.
- Preserve `ranking_signal = "ipsae"` until a new benchmark says otherwise.
- Surface candidates with missing ipSAE instead of silently dropping them.

### Must not

- Invent weights, thresholds, or composites.
- Use Boltz2, paper ipTM, pLDDT, or PAE to break ties. The only
  tie-breaker is `candidate_id` ascending.
- Emit a 0–100 “ranking quality” number.
- Claim that rank 1 is a predicted binder.

## Critic Agent

### Question

> Do the other structural metrics support, contradict, or make uncertain
> this candidate’s ipSAE position?

The Critic does **not** ask “who should be first?”

### Input

For each candidate:

- the Ranking Agent record (`rank`, `ranking_score`, `ranking_signal`)
- the auxiliary fields of the Structural Evidence Package
- optional set-level context (percentiles of Boltz metrics **inside this
  design set**, computed without labels)

The Critic may look at the whole scored set to say “this Boltz std is
high relative to peers.” It may not look at YSD, campaign identity as a
feature, or experimental outcomes.

### Output

Structured assessment plus a short note. The LLM may phrase the note.
The categories and numbers must remain separate and auditable.

```json
{
  "candidate_id": "design_001",
  "primary_ranking": {
    "signal": "ipsae",
    "score": 0.91,
    "rank": 3
  },
  "structural_evidence": {
    "paper_iptm": 0.87,
    "boltz2_iptm_model_0": 0.93,
    "boltz2_iptm_mean": 0.94,
    "boltz2_iptm_median": 0.94,
    "boltz2_iptm_min": 0.92,
    "boltz2_iptm_std": 0.01
  },
  "critic_assessment": {
    "agreement": "high",
    "ensemble_consistency": "high",
    "structural_conflict": false,
    "missing_evidence": false,
    "confidence_note": "Boltz2 ensemble supports the ipSAE ranking."
  }
}
```

### Assessment vocabulary

Closed enums. No 0–100 critic score.

Critic enums (`agreement`, `ensemble_consistency`, and
`structural_conflict`) are qualitative evidence annotations. They
MUST NOT be interpreted as calibrated probabilities, experimental
success probabilities, or replacement ranking scores.

`agreement = high / mixed / low / unknown` classifies whether
auxiliary structural metrics support the ipSAE position. It is not
confidence, not P(binder), and not a second ranking_score.

| Field | Allowed values | Meaning |
| --- | --- | --- |
| `agreement` | `high`, `mixed`, `low`, `unknown` | Do Boltz location metrics (mean / median / min / model_0) and paper ipTM support the ipSAE position? |
| `ensemble_consistency` | `high`, `mixed`, `low`, `unknown` | Is Boltz ipTM std low relative to this design set? Not experimental stability. |
| `structural_conflict` | `true`, `false`, `unknown` | High ipSAE rank with weak/unstable Boltz evidence, or the reverse. |
| `missing_evidence` | `true`, `false` | Boltz2 ensemble or paper ipTM absent. |
| `confidence_note` | short string | Must cite the numbers already in `structural_evidence`. Must not introduce a new score. |

`unknown` is required when auxiliary metrics are missing. Absence of
Boltz2 is not disagreement.

### Critic v1 rule

v1 does **not** use absolute ipTM cutoffs such as `mean > 0.8`.

It compares, inside the current design set, the dense order by Ranking
Agent rank with the dense order by one auxiliary location metric
(`boltz2_iptm_mean`, else median, else `model_0`):

```
d = |primary_dense_rank - auxiliary_dense_rank| / (n - 1)

d == 0    → agreement high
d <= 0.5  → mixed
d > 0.5   → low
```

`n` is the number of candidates that have that location metric.
If `n < 2`, agreement is `unknown`. Missing evidence is never
filled in with mean, median, or another sample.

`ensemble_consistency` is `unknown` in v1. `structural_conflict`
is true only when agreement is `low`.

The rank and `ranking_score` on the output must equal the input.

Critic agreement categories are deterministic qualitative heuristics.
They are not experimentally calibrated and must not be interpreted as
binding probabilities or validated decision thresholds. The band
`d <= 0.5` is a v1 interpretation rule, not a YSD-tuned cutoff and not
a lab gate.

## Grounded critic context (3F.1)

The Second Brain may enrich interpretation. It does not participate
in agreement, `structural_conflict`, rank, or `ranking_score`.

```
ProteinCriticAgentV1 → CriticAssessment
GroundedCriticContextProvider → EvidencePacket
                 ↓
         GroundedCriticResult
```

`ProteinCriticAgentV1` does not open the vault. The provider currently
resolves `(protein_design, structural_scoring)` to the validated
package `protein_scoring_bcma_boltz2_v2`. Missing or non-validated
packages yield `grounded_context.status = unavailable` and the Critic
still runs.

Normative context accepts only `status=validated`. `reference` and
`observed` do not change critic constraints. `superseded` is never
the current rule.

Hermes may later phrase a `GroundedCriticResult`. Hermes does not
decide rank. That is 3F.2, not this slice.

## Grounded critic explanation (3F.2)

```
GroundedCriticResult
        │
        │ immutable scientific state
        ▼
CriticExplanationRequest
        │
        ▼
      Hermes
        │
        │ natural-language explanation only
        ▼
GroundedCriticExplanation
```

Hermes verbalizes the lean request. Python sets `knowledge_used`,
`rank_preserved`, and `ranking_score_preserved` from the original
result. Doubt may be stated. Doubt is not a decision.

Rules:

1. Explain the supplied assessment.
2. Do not recalculate any scientific field.
3. Do not recommend rank changes.
4. Do not add scientific claims absent from `grounded_context`.

Explanation failure must not block the scientific pipeline.

## Supervisor-owned grounded critique (3F.3)

```
generate
  ↓
score
  ↓
rank
  ↓
CriticAssessment                 ← determinístico
  ↓
GroundedCriticResult             ← Second Brain validado
  ↓
[GroundedCriticExplanation]      ← Hermes, opcional
  ↓
prioritize
```

The brackets are the rule: explanation is an interpretation /
audit artifact, not a prerequisite for prioritization.

`ProteinSupervisorV1` may take an optional
`GroundedCriticContextProvider` and an optional `CriticExplainer`.
It does not import Hermes, OpenRouter, or MCP. CI injects
`FixtureCriticExplainer`, `FailingCriticExplainer`, or `None`.
Local verbalization injects `HermesCriticExplainer`.

Nested run state stays under `critique`. There is no top-level
`explanation` stage:

```
critique.status = complete
critique.grounded_context.status = available | unavailable
critique.explanation.status = complete | failed | skipped
prioritization.status = complete
```

If Hermes fails, science is still finished: critique remains
`complete` and explanation is `failed`. Prioritization still
consumes `ProteinCriticRecord` only.

### What the Critic may use

Label-free, set-relative comparisons, for example:

- Boltz mean / median / min sit in a similar band of the current set as
  the ipSAE rank.
- Boltz min is far below Boltz mean (location vs conservative envelope).
- Boltz std is high relative to the current set.
- paper ipTM and Boltz location tell different stories.

Exact cutoffs for `high` / `mixed` / `low`, if implemented as code, must
be frozen **without using YSD**. Prefer ranks or percentiles inside the
current design set over absolute thresholds copied from Chow 2026.

The BindCraft-only std result from the v2 unblind is **not** a license
to treat low std as a second ranking signal. It is campaign-dependent
evidence. The Critic may *mention* high dispersion as uncertainty. It
may not promote low-std candidates up the list.

### Worked examples

These are qualitative illustrations of the contract, not calibrated
thresholds.

**Candidate A — support**

| Metric | Value |
| --- | ---: |
| ipSAE | 0.91 |
| Boltz mean ipTM | 0.94 |
| Boltz median ipTM | 0.94 |
| Boltz min ipTM | 0.92 |
| Boltz std | 0.01 |

```
agreement: high
ensemble_consistency: high
structural_conflict: false
confidence_note: Strong agreement between the primary ranking
signal and the Boltz2 ensemble evidence.
```

**Candidate B — disagreement**

| Metric | Value |
| --- | ---: |
| ipSAE | 0.89 |
| Boltz mean ipTM | 0.61 |
| Boltz median ipTM | 0.58 |
| Boltz min ipTM | 0.37 |
| Boltz std | 0.19 |

```
agreement: low
ensemble_consistency: low
structural_conflict: true
confidence_note: High ipSAE ranking but weak and unstable Boltz2
ensemble evidence. Structural disagreement detected.
```

Candidate B **keeps** its ipSAE rank. The Critic only annotates risk.

### Must not: hidden composite

Forbidden Critic behavior:

```
ipSAE good + Boltz good → 95/100
ipSAE good + Boltz weak → 60/100
then sort by that number
```

Required shape:

```
Rank = 3
Primary evidence = strong
Auxiliary evidence = supportive
Discordance = low

Rank = 7
Primary evidence = strong
Auxiliary evidence = conflicting
Discordance = high
```

The rank column comes only from the Ranking Agent. The Critic fills the
other columns.

### Must

- Keep `primary_ranking.rank` identical to the Ranking Agent output.
- Keep raw auxiliary numbers in `structural_evidence`.
- Allow `structural_conflict = true` without changing order.
- Degrade to `unknown` when evidence is missing.

### Must not

- Re-rank, re-weight, or drop candidates.
- Emit `critic_score`, `quality`, `priority_score`, or similar.
- Treat Boltz std as predicted KD, on-rate, or developability.
- Fit rules on YSD, campaign labels, or the v2 unblind table.
- Use `Δ(ipTM, ipSAE)` as an agreement metric.

## Prioritization Agent

### Question

> Given experimental slots, cost, and other constraints, which portfolio
> should be tested?

This is the first agent that takes a **portfolio decision**. It does not
describe ranking. It does not rewrite ranking.

### Input

Already ranked and criticized candidates:

- `candidate_id`
- `rank`
- `ranking_score` = raw ipSAE
- `critic.agreement`
- `critic.structural_conflict`
- `structural_evidence`
- optional `sequence_cluster` map

### Output

New properties only. Rank and score stay put.

```json
{
  "candidate_id": "design_042",
  "rank": 7,
  "ranking_score": 0.887,
  "selected": true,
  "selection_bucket": "core",
  "selection_order": 4,
  "selection_reason": "High-ranked representative selected under portfolio policy."
}
```

`rank` and `ranking_score` MUST equal the input. Being selected does not
mean the candidate is structurally better than an unselected neighbor.

### Experimental portfolio policy v1

Configurable, deterministic, not an LLM:

```
core_slots = 6
novelty_slots = 2
exploratory_slots = 2
```

This is an **experimental portfolio policy**, never a new structural
baseline.

v1 fill order:

1. **core** — next unused candidates by ipSAE rank until `core_slots`
2. **novel** — remaining candidates whose sequence cluster is not yet
   in the selected set, by rank
3. **exploratory** — remaining candidates with structural uncertainty
   or disagreement (`unknown` / `mixed` / `low`, missing evidence, or
   `structural_conflict`), by rank

If cluster metadata is absent, each `candidate_id` is its own cluster.

Missing ipSAE is ineligible for any slot.

### Critic is not a hard filter

v1 must **not** do `agreement=low → reject`.

Boltz2 is not validated to replace ipSAE. Automatic exclusion would let
auxiliary evidence change the experimental set more strongly than the
benchmark allows.

- `agreement=low` → annotation / `review_flag`
- `structural_conflict=true` → flag for review
- Rank 1 with conflict can still fill a core slot

Later Cellertz labels can test whether these flags have decision value.

### Must not

- Write `ranking_score` or change `rank`.
- Collapse Critic categories into a second sort key.
- Treat “portfolio selected” as “structurally better than unselected.”
- Use an LLM to invent the v1 policy.

## Agent-facing guarantees

Consumers (LLM or code) should be able to say, for any candidate:

1. What is its ipSAE rank? → Ranking Agent
2. Do Boltz2 / paper ipTM support that rank? → Critic Agent
3. Why is it in or out of the lab set? → Prioritization Agent

If those three answers share one number, the architecture has failed.

## Implementation notes (later)

Ranking Agent v1, Critic Agent v1, and Prioritization Agent v1 are
implemented.

Suggested next code slices:

1. Done: `ProteinScoringToolV2` exposes Boltz2 `model_0` ipTM in
   `structural_evidence`. `ranking_score` remains raw ipSAE.
2. Done: deterministic Ranking Agent v1
   (`src/cellertz/domains/protein_design/agents/ranking.py`).
3. Done: deterministic, label-free Critic Agent v1
   (`src/cellertz/domains/protein_design/agents/critic.py`). Agreement uses
   set-relative rank discordance. No absolute ipTM cutoffs.
   `ensemble_consistency` is `unknown` until a std rule is frozen.
   Categories are qualitative heuristics, not calibrated probabilities.
4. Done: deterministic Prioritization Agent v1
   (`src/cellertz/domains/protein_design/agents/prioritization.py`). Experimental
   portfolio policy; Critic flags do not reject.
5. Done: optional LLM layer writes notes from already-computed
   categories. It does not decide rank.
6. Done (3F.1): grounded critic context provider. The EvidencePacket
   annotates interpretation. It does not enter agreement, rank, or
   `ranking_score`.
7. Done (3F.2): Hermes verbalizes `GroundedCriticResult`. Python
   keeps invariants. Explanation is not a gate for rank,
   prioritization, or the next stage.
8. Done (3F.3): Supervisor wires grounded context and optional
   explanation without making Hermes a workflow stage. Failed or
   missing explanation still completes generate → prioritize.
   Phase 3 closes at `docs/architecture/protein_design_v1_fixture.md`.

Tests that must exist before the agents are trusted:

- Ranking ignores Boltz2 even when ensembles strongly disagree.
- Critic conflict does not change `rank`.
- No function named or behaving as a composite structural score.
- Missing ensemble → `unknown`, not `low` agreement.

## References

- Structural Baseline v1: `docs/experiments/chow2026_bcma_benchmark.md`
- Boltz2 v2 protocol (frozen before unblind):
  `docs/experiments/boltz2_v2_evaluation_protocol.md`
- Boltz2 v2 unblind (Scenario 3):
  `docs/experiments/boltz2_v2_unblind_results.md`
- Tool contract: `src/cellertz/tools/protein_scoring/scoring_v2.py`
- Pre-unblind freeze tag: `protein-boltz2-benchmark-v2` (`be40731`)
