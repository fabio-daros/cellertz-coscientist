# Protein Design Supervisor and Generation Loop

Architecture contract. Supervisor, Generation Agent, and the
multi-round evolution loop are **not implemented** in this document.

The ranking nucleus is already executable:

```
ProteinScoringToolV2
        ↓
Ranking Agent v1
        ↓
Critic Agent v1
        ↓
Prioritization Agent v1
```

That nucleus answers score → rank → critique → select. It must not be
reopened here. This document defines the coordinator that *calls* those
components, and the Generation Agent that feeds them normalized
candidates.

Scientific freeze (unchanged):

- `ranking_score = raw ipSAE`
- Boltz2 is auxiliary evidence
- Critic enums are qualitative heuristics, not calibrated probabilities
- Prioritization is an experimental portfolio policy, not a new ranking

## Why a Supervisor

The next step is not another isolated specialist.

Specialists already own their questions:

| Agent / tool | Question | May change rank? |
| --- | --- | --- |
| Generation | What candidates exist? | n/a |
| `ProteinScoringTool` | What structural numbers are attached? | no |
| Ranking | What is the initial order? | defines rank |
| Critic | Do auxiliaries support, conflict, or leave that position uncertain? | no |
| Prioritization | Which subset enters the experimental set? | no; selects a portfolio |

The Supervisor owns a different question:

> Which stage of this design run should execute next, given explicit
> pipeline state?

It is an orchestrator plus specialist workers. It must not calculate
ipSAE, interpret Boltz, invent ranks, or silently replace the portfolio
policy.

```
                 Protein Design Supervisor
                           │
          ┌────────────────┴────────────────┐
          │                                 │
     Generation                         Evaluation
          │                                 │
 RFdiffusion / BindCraft          ProteinScoringTool
 / mBER / future adapters                 │
                                          ↓
                                      Ranking
                                          ↓
                                       Critic
                                          ↓
                                  Prioritization
                                          ↓
                                      Selection
                                          │
                         ┌────────────────┴──────────────┐
                         │                               │
                    experiment                     next round
```

One-round v1 stops at selection. Evolution / regenerate is a later
round, not part of Supervisor v1.

## 1. Supervisor responsibilities

The Supervisor:

- Holds a `ProteinDesignRun` state object
- Advances **one missing stage** at a time
- Delegates each stage to the specialist that already owns it
- Records stage completion, artifacts, and failures
- Never inspects YSD to choose a stage

Deterministic one-round schedule:

```
if generation.status != complete:
    generate
elif scoring.status != complete:
    score
elif ranking.status != complete:
    rank
elif critique.status != complete:
    critique
elif prioritization.status != complete:
    prioritize
else:
    run is complete for this round
```

The Supervisor may read stage status, provenance, artifact references,
and error codes. It may not read Boltz numbers in order to “fix” a
ranking.

### Must

- Be restartable from recorded state
- Leave completed stages untouched
- Treat every stage as **idempotent**: the same specialist input must
  produce the same artifact and must not append a second copy
- Pass Ranking output to Critic without reordering
- Pass Critic output to Prioritization without dropping conflict cases
- Keep `run_id` / `generation_run_id` on every artifact
- Mark a stage `complete` only from a structured specialist result,
  never because a file appeared on disk

### Must not

See [Prohibitions](#prohibitions).

## 2. Generation Agent contract

Generation Agent v1 is a **tool orchestrator**, not an LLM that invents
sequences.

```
Generation Agent
    ├── RFdiffusion adapter
    ├── BindCraft adapter
    ├── mBER adapter
    └── future generators
```

```
GenerationRequest
        ↓
GenerationAgent
        ↓
GeneratorAdapter   (bindcraft | rfdiffusion | mber | future)
        ↓
NormalizedCandidates
```

The Supervisor talks only to `GenerationAgent`. It never imports a
BindCraft, RFdiffusion, or mBER client. If Supervisor is implemented
before this interface, it will couple to a generator and the abstraction
is lost.

### Input

```json
{
  "target_id": "BCMA",
  "objective": "binder_design",
  "generator": "bindcraft",
  "n_candidates": 100,
  "constraints": {
    "length_min": 50,
    "length_max": 100
  }
}
```

`generator` selects an adapter. Constraints are adapter arguments, not
ranking features.

### Output

```json
{
  "generation_run_id": "run_001",
  "generator": "bindcraft",
  "target_id": "BCMA",
  "candidates": [
    {
      "candidate_id": "design_001",
      "sequence": "...",
      "target_id": "BCMA",
      "generator": "bindcraft",
      "generator_metadata": {}
    }
  ]
}
```

Rules:

- `candidate_id` is unique inside the run
- `sequence` is the binder sequence
- `generator_metadata` may store adapter-native fields
- Scoring, Ranking, Critic, and Prioritization ignore
  `generator_metadata` as a ranking signal
- No YSD, ipSAE, or Boltz values belong in this record

Adapters that fail to return a sequence fail the generation stage with
a structured `status=failed` result. The Supervisor does not fill in
placeholder proteins and does not mark generation complete because a
work directory exists.

BindCraft adapter v1 lives in `src/cellertz/domains/protein_design/adapters/bindcraft.py`.
Execution and parsing are separate:

```
BindCraftExecutor  → raw BindCraft artifacts
BindCraftParser    → NormalizedCandidates
BindCraftAdapter   → coordinates the two
```

BindCraft-native columns stay in `generator_metadata`. The adapter
does not emit `ranking_score`, `rank`, ipSAE, or Boltz. CI tests replay
recorded `final_design_stats.csv`; they do not run BindCraft on GPU.
`LocalBindCraftExecutor` translates a request into structured argv and
delegates to `ExecutionRuntime`. Today's implementation is
`LocalExecutionRuntime` (subprocess). Hermes/OpenClaw can implement
the same runtime contract later without becoming “a remote
subprocess” conceptually.

RFdiffusion adapter v1 lives in `src/cellertz/domains/protein_design/adapters/rfdiffusion.py`
with the same executor/parser split. Native RFdiffusion output is a
backbone/structure. `NormalizedCandidate.sequence` is emitted only
when a designed sequence is present (FASTA / JSON / non-placeholder
PDB). Poly-G / poly-A traces stay in `generator_metadata` as
`backbone_sequence`. When a sequence comes from ProteinMPNN, metadata
records `structure_generator="rfdiffusion"`,
`sequence_source="proteinmpnn"`, and `sequence_source_version`.
`generator="rfdiffusion"` names the structure adapter, not the
sequence designer. The schema was not widened: backbone-only
artifacts fail generation instead of inventing a sequence.

mBER adapter v1 lives in `src/cellertz/domains/protein_design/adapters/mber.py`. Native
accepted output is `accepted.csv` with `binder_seq`. mBER iPTM /
pLDDT stay in `generator_metadata.native_metrics` and never become
`ranking_score`. CI replays recorded `accepted.csv`; it does not
run mBER on GPU.

v1 does not ask an LLM to “create a binder.” LLM-conditioned generation,
if it ever exists, is another adapter behind the same interface.

## 3. Pipeline state model

Explicit nested state. No hidden memory inside the Supervisor.
Booleans such as `ranking_complete` are derived from
`ranking.status == "complete"`, not stored as a second source of truth.

```json
{
  "run_id": "protein_run_001",
  "round": 1,
  "target": "BCMA",
  "stage": "prioritization",
  "generation": {
    "status": "complete",
    "artifact": "artifacts/protein_run_001/generation.json",
    "input_hash": "...",
    "version": "generation_agent_v1",
    "started_at": "2026-09-14T23:00:00-03:00",
    "completed_at": "2026-09-14T23:04:00-03:00",
    "error": null
  },
  "scoring": {
    "status": "complete",
    "artifact": "artifacts/protein_run_001/scoring.json",
    "input_hash": "...",
    "version": "protein_scoring_tool_v2",
    "started_at": "...",
    "completed_at": "...",
    "error": null,
    "missing_ipsae": 3,
    "missing_boltz2": 5
  },
  "ranking": {
    "status": "complete",
    "artifact": "artifacts/protein_run_001/ranking.json",
    "input_hash": "...",
    "version": "protein_ranking_agent_v1",
    "started_at": "...",
    "completed_at": "...",
    "error": null,
    "missing_ipsae_ranked_last": 3
  },
  "critique": {
    "status": "complete",
    "artifact": "artifacts/protein_run_001/critique.json",
    "input_hash": "...",
    "version": "protein_critic_agent_v1",
    "started_at": "...",
    "completed_at": "...",
    "error": null
  },
  "prioritization": {
    "status": "pending",
    "artifact": null,
    "input_hash": null,
    "version": null,
    "started_at": null,
    "completed_at": null,
    "error": null
  }
}
```

Per-stage provenance (required once a stage has been attempted):

| Field | Meaning |
| --- | --- |
| `status` | `pending` / `running` / `complete` / `failed` |
| `artifact` | Path or handle returned by the specialist, or null |
| `input_hash` | Hash of the exact specialist input for idempotent replay |
| `version` | Tool or agent version that produced the artifact |
| `started_at` | When the stage began |
| `completed_at` | When the specialist returned a structured result |
| `error` | Specialist error; null unless `status=failed` |

Run-level fields:

| Field | Meaning |
| --- | --- |
| `run_id` | Stable id for the design run |
| `round` | 1 for Supervisor v1. Later evolution increments this |
| `stage` | Stage currently due or in progress |
| `target` | Design target id |

`stage` values for one-round v1:

```
generation
scoring
ranking
critique
prioritization
complete
failed
```

### Structured completion, not file presence

Each specialist MUST return a structured stage result, conceptually:

```
status: complete | failed
artifact: ... | null
counts / warnings: ...
error: ... | null
```

The Supervisor copies that result into run state. It MUST NOT infer
`status=complete` because some file appeared on disk, because a
directory is non-empty, or because a process exited 0 without a payload.

A stage is `complete` only when that structured result says so and the
artifact validates as the expected type:

| Stage | Artifact |
| --- | --- |
| generation | normalized candidate list |
| scoring | `ProteinScoreResultV2` list |
| ranking | `ProteinRankingRecord` list |
| critique | `ProteinCriticRecord` list |
| prioritization | `ProteinPortfolioRecord` list |

### Idempotency

Re-running a completed stage with the same `input_hash` must:

- yield the same artifact bytes / records
- leave a single artifact in place (no `ranking.json.2`)
- leave provenance `version` unchanged unless the specialist version
  actually changed, in which case it is a new input and a new hash

If `status=complete` and `input_hash` matches the next request, the
Supervisor skips the specialist call.

### Incomplete structural evidence

These are **candidate-level** conditions, not Supervisor failures.

**Missing ipSAE**

- Scoring records `missing_ipsae` on the stage
- Ranking Agent v1 places those candidates **last**, after all scored
  rows, ordered by `candidate_id`
- The run continues
- Prioritization v1 leaves them ineligible for portfolio slots

**Missing Boltz2**

- Scoring records `missing_boltz2` on the stage
- Auxiliary evidence is reduced; `ranking_score` is unchanged
- Critic Agent v1 returns `agreement=unknown` when location evidence
  is insufficient
- The run continues

A scoring stage that returns a structured complete result with
`missing_ipsae > 0` is still `complete`. A scoring stage that crashes
before returning a result is `failed`.

## 4. Failure / retry semantics

Failures are stage-local.

- If generation fails, do not score
- If scoring fails, do not rank
- If ranking fails, do not critique
- If critique fails, do not prioritize
- If prioritization fails, keep rank and critic artifacts

Retry means **re-run the failed stage**, not the whole pipeline.
Because stages are idempotent, a retry with the same input replaces the
failed attempt’s provenance (`status`, `error`, timestamps) rather than
duplicating artifacts.

Completed stages are not recomputed on retry unless `input_hash` no
longer matches. Ranking is deterministic; re-ranking after a critic
failure is allowed only if the ranking artifact is missing, corrupt, or
the hash diverged.

The Supervisor must not:

- Retry by mixing in Boltz as a fallback rank
- Drop candidates with `agreement=low` in order to “make ranking work”
- Mark a stage complete after a timeout with empty output
- Treat “a JSON file exists” as success

Adapter / tool crashes surface as that stage `status=failed` plus
`error`. Human or scheduler resumes from that stage.

Missing ipSAE or missing Boltz2 on some candidates are not Supervisor
failures. They are counted on the scoring/ranking/critique provenance
as above. The run continues.

## 5. One-round workflow

Supervisor v1 implements a single round, end to end:

```
generate
    → score
    → rank
    → critique
    → [explain]
    → select
```

Brackets mean optional audit, not a workflow stage. Explanation
lives under `critique` in run state.

Worked path:

1. Generation Agent produces normalized candidates for target T
2. `ProteinScoringToolV2` attaches ipSAE + Boltz2 evidence
3. Ranking Agent sorts by raw ipSAE, then `candidate_id`
4. Critic Agent annotates set-relative agreement; rank unchanged
5. Optional grounded context annotates the Critic result; rank
   unchanged. Optional `CriticExplainer` may write
   `GroundedCriticExplanation`. Failure here does not fail critique
   or block prioritization.
6. Prioritization Agent applies the experimental portfolio policy
   to `ProteinCriticRecord` only; rank unchanged; Critic flags do
   not reject; Hermes text is not an input

When prioritization `status` is `complete`, the round is done. Output of
record is the portfolio list plus the unchanged ranking/critic tables.

Not in v1:

```
→ evolve
→ regenerate
→ repeat
```

A later round may look like the AI Co-Scientist sketch:

```
Generation  ↕  Reflection  ↕  Ranking  ↕  Evolution
```

That loop is forbidden until one-round generate → select is tested as
an integration path. Evolution must not become a vehicle for a hidden
composite score.

## Prohibitions

Supervisor MUST NOT:

- alter `ranking_score`
- recompute structural metrics
- invent Critic classifications
- override prioritization policy silently
- mix Boltz2 into a hidden composite score
- use YSD, campaign identity, or unblind tables as runtime features
- promote `agreement` or `selection_bucket` into a new rank
- skip Ranking, Critic, or Prioritization because a generator already
  emitted a “score”
- infer stage success from leftover files
- couple itself to BindCraft, RFdiffusion, or mBER APIs
- import Hermes, OpenRouter, or MCP
- treat explanation as a top-level stage or a gate for select

Generation Agent MUST NOT:

- emit `ranking_score`
- call Critic or Prioritization
- require downstream agents to parse adapter-specific schemas

## Mapping to existing code

Already implemented; Supervisor v1 must call these, not fork them:

| Stage | Code |
| --- | --- |
| generation | `src/cellertz/domains/protein_design/agents/generation.py` |
| BindCraft adapter | `src/cellertz/domains/protein_design/adapters/bindcraft.py` |
| RFdiffusion adapter | `src/cellertz/domains/protein_design/adapters/rfdiffusion.py` |
| mBER adapter | `src/cellertz/domains/protein_design/adapters/mber.py` |
| execution runtime | `src/cellertz/runtime/` |
| scoring | `src/cellertz/tools/protein_scoring/scoring_v2.py` |
| ranking | `src/cellertz/domains/protein_design/agents/ranking.py` |
| critique | `src/cellertz/domains/protein_design/agents/critic.py` |
| grounded context | `src/cellertz/domains/protein_design/agents/grounded_critic.py` |
| explanation | `src/cellertz/domains/protein_design/agents/critic_explanation.py` (optional) |
| select | `src/cellertz/domains/protein_design/agents/prioritization.py` |
| specialist contracts | `docs/architecture/protein_ranking_critic_agent.md` |
| Protein Design home | `src/cellertz/domains/protein_design/` |

Protein Design is a bounded context. Agents and scientific
adapters live together under `domains/protein_design/`. BindCraft,
RFdiffusion, and mBER are adapters, not agents. Shared knowledge,
MCP, runtime, and run state stay outside the domain. mRNA Design
and Target Discovery are future sibling domains, not folders under
`agents/`.

Implemented with fixtures only:

| Piece | Status |
| --- | --- |
| Generation Agent + fixture adapter | implemented |
| BindCraft adapter + parser | implemented (fixture artifacts; local executor) |
| RFdiffusion adapter + parser | implemented (fixture artifacts; local executor) |
| Protein Design Supervisor v1 | implemented (`domains/protein_design/agents/supervisor.py`) |
| mBER adapter + parser | implemented (fixture artifacts; local executor) |
| ExecutionRuntime + LocalExecutionRuntime | implemented |
| Hermes gateway + MCP v1.3 | implemented (fixture mutation + read-only Second Brain retrieval) |
| HermesExecutionRuntime / OpenClawExecutionRuntime | deferred |
| multi-round evolution | deferred |

MCP v1.3 is intentionally allowlisted. Hermes reaches
`http://cellertz-mcp:8000/mcp` on the Compose network only.
The server exposes no filesystem, shell, or scientific generators:

```
MCP v1.3:
  resources: disabled
  prompts: disabled
  tools:
    read-only:
      - cellertz_get_capabilities
      - protein_get_run_state
      - knowledge_get
      - knowledge_neighbors
      - knowledge_ground_package
    mutating:
      - protein_start_fixture_run
  mutation:
    enabled: true
    mode: fixture_only
    real_generators_allowed: false
```

`cellertz_get_capabilities` reports which protein-design stages exist.
`protein_get_run_state` reads operational stage status from
`ProteinRunStateReader` (`outputs/runs/{run_id}/state.json` locally).
`protein_start_fixture_run` is the only mutating tool. Cellertz
enables it only when `CELLERTZ_MCP_MUTATION_MODE=fixture_only`
(default `disabled`). The adapter is forced to fixture; BindCraft,
RFdiffusion, mBER, GPU, and wet lab stay closed. The MCP package
does not import `ProteinSupervisorV1`. Science runs only through
`ProteinRunStarter`. Missing run IDs return `found: false` and
are not created.

Supervisor v1 scoring uses deterministic fixture structural inputs
so the loop can run without GPU or scientific generators. Those
metrics are not a scientific baseline. Real adapters plug in behind
`NormalizedCandidate` without changing the Supervisor.

## Implementation notes

Phase 3 is closed at the fixture baseline
(`docs/architecture/protein_design_v1_fixture.md`, tag
`protein-design-v1-fixture`, 215 passed). Real generators and
GPU execution stay locked. `fixture_only` is not a toggle for
BindCraft, RFdiffusion, or mBER.

Suggested remaining order:

1. Done (3G): Second Brain hardening.
2. Phase 4A: Scientific Execution Contract
   (`docs/architecture/scientific_execution.md`). No GPU job.
   `execution/` authorizes; `runtime/` launches later.
3. Phase 4B.1–4B.2: BindCraft execution bridge and preflight.
   No generation. GPU/MCP remain locked. 4B.4 stays compute-gated.
4. HermesExecutionRuntime / OpenClawExecutionRuntime behind the
   existing `ExecutionRuntime` contract, after that contract.
5. Evolution loop remains deferred; one-round generate → select
   is the integration path in `tests/test_protein_supervisor.py`
   and `tests/test_generation_adapters_supervisor.py`

Integration tests that must exist before the Supervisor is trusted:

- Completed ranking is not recomputed when critique is retried
- Re-running ranking with the same `input_hash` does not duplicate
  artifacts
- Stage `status` stays `failed` until a structured complete result
  is returned, even if a file is on disk
- Critic `agreement=low` still reaches Prioritization
- Portfolio `selected=true` does not rewrite `rank`
- Generator name is metadata, not a ranking feature
- Missing ipSAE candidates are last in ranking, not a failed run
- Missing Boltz2 does not change `ranking_score`
- No composite structural score appears in run state
- Missing or failed explanation still completes prioritization
- Selected candidates are identical with and without explanation
- Explanation retry does not re-run generation, scoring, ranking,
  or critic

## References

- Fixture baseline (Phase 3 closed):
  `docs/architecture/protein_design_v1_fixture.md`
- Ranking / Critic / Prioritization contract:
  `docs/architecture/protein_ranking_critic_agent.md`
- Boltz2 v2 unblind (Scenario 3):
  `docs/experiments/boltz2_v2_unblind_results.md`
- Pre-unblind freeze tag: `protein-boltz2-benchmark-v2` (`be40731`)
- Ranking Agent: `0c885da`
- Critic Agent: `14420ca`
- Prioritization Agent: `3d9c1e4`
- MCP mutating capability contract (not yet implemented):
  `docs/architecture/mcp_mutating_capabilities.md`
