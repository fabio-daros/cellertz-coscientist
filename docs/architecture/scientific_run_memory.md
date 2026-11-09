# Episodic Scientific Run Memory — Second Brain v1

This document freezes the first **episodic** scientific memory
domain. It does not execute BindCraft, unlock MCP, add RAG, or
promote semantic KnowledgePackages. Databricks is an optional
storage adapter; see
`docs/architecture/databricks_scientific_memory.md`.

```
Completed Protein Design Run
        │
        ▼
ScientificRunMemoryProjector
  (project_protein_design_run)
        │
        ▼
ScientificRunMemory
  memory_house = episodic
  epistemic_status = observed
        │
        ▼
ScientificRunMemoryStore
        │
        ├── FileScientificRunMemoryStore              default local
        └── DatabricksScientificRunMemoryStore        optional adapter
```

Phase 5B selects the store through `create_scientific_run_memory_store`.
`CELLERTZ_SCIENTIFIC_MEMORY_BACKEND` is `file` or `databricks`.
Default is `file`. Databricks is never implicit.

`DatabricksScientificRunMemoryStore` has been real-integration
validated against a Databricks Free Trial serverless SQL
Warehouse using a real PDL1 `ScientificRunMemory`. That is a
trial/serverless environment, not a production deployment.
See `docs/validation/databricks_scientific_memory_real_validation.md`.

`ScientificRunMemory` answers:

> What happened in a scientific run?

It does **not** answer:

> What has Cellertz scientifically validated?

```
RUN COMPLETE != SCIENTIFIC CLAIM VALIDATED
SCIENTIFIC EVIDENCE PRODUCED != SEMANTIC KNOWLEDGE PROMOTED
EPISODIC MEMORY != VALIDATED SEMANTIC MEMORY
```

A later retrieval layer may consume episodic records. RAG must not
silently promote an observation into a validated claim.

## Second Brain houses

```
Second Brain
│
├── Working Memory
│      request / session context
│
├── Episodic Scientific Memory
│      ScientificRunMemory
│      completed executions / results
│      epistemic status: observed
│
├── Semantic Memory
│      KnowledgePackage
│      claims / evidence / provenance / status / version
│      validated only through explicit future promotion
│
└── Procedural Memory
      code + Hermes skills
```

Future, not this stage:

```
ScientificRunMemory
        ↓
explicit scientific writeback / knowledge promotion
        ↓
human / scientific review
        ↓
Semantic KnowledgePackage
```

Do not convert `ScientificRunMemory` into a `KnowledgePackage`.
Do not write the Markdown vault `second_brain/` from this projector.

## What this layer is not

| concept | role |
| --- | --- |
| Idempotency store | execution ownership, replay, execution state |
| `ProteinRunState` | operational MCP / Supervisor boundary |
| Scientific artifacts | `generation.json`, scoring, ranking, critique, prioritization, PDB, PAE, handoff |
| Semantic vault `second_brain/` | KnowledgePackage claims and evidence |
| Episodic ScientificRunMemory | immutable record of **observed** scientific execution and results |

```
Second Brain episodic run memory != operational run state
Second Brain episodic run memory != idempotency
Second Brain episodic run memory != RAG
Second Brain episodic run memory != validated semantic knowledge
```

## Epistemic status

Execution status and scientific-pipeline status are not epistemic
status.

| field | example | meaning |
| --- | --- | --- |
| execution status | complete | BindCraft / runtime finished |
| scientific pipeline status | complete | Supervisor stages finished |
| `memory_house` | `episodic` | this is run memory, not a claim |
| `epistemic_status` | `observed` | the run was seen; it is not validated truth |

This stage must not emit `validated`, `approved`, `confirmed`, or
`experimentally validated` merely because the run completed.

## First-class distinction inside a run record

```
execution provenance
    tool / container digest / seed / manifest / request hashes

scientific evidence
    ipSAE models and structural artifact hashes
    copied from scoring provenance, never recomputed

scientific decision
    rank / critic / prioritization selection
    ranking_score is raw ipSAE; it is not a probability
    selected comes from prioritization.json, not from rank == 1
```

MEMORY != SCIENTIFIC EVIDENCE CALCULATION.

The memory layer does not recompute ipSAE, does not recompute rank,
does not reinterpret ipSAE as a probability, and does not turn ipSAE
into a hard threshold.

## Projection

Inputs are explicit. The projector does not search the filesystem.

```
run_artifact_dir/
    run.json
    generation.json
    scoring.json
    ranking.json
    critique.json
    prioritization.json

execution_output_dir/
    .cellertz_execution_handoff.json
```

The projector is pure: it reads, validates, and creates an observed
episodic record. It does not execute BindCraft, invoke the
Supervisor, modify artifacts, modify idempotency, modify run state,
call MCP, call an LLM, call Databricks, or write KnowledgePackages.

Inconsistent provenance fails closed. Artifacts are not repaired.

For a completed run, generated candidate IDs must exactly match
scoring, ranking, critique, and prioritization. Missing or extra
IDs fail closed. Copied scientific values (ranking_score, ipSAE
identity, probability / hard-filter flags) are cross-checked
exactly and never recomputed.

## Immutability

`memory_digest` is a canonical SHA-256 of the scientific content,
including `memory_house` and `epistemic_status` as part of the
record contract. `recorded_at` is excluded. Local filesystem URIs
are excluded. The same observed scientific result yields the same
digest.

On every persisted-memory read, `FileScientificRunMemoryStore`
recomputes that digest and compares it with the stored value.
Mismatch fails closed (`scientific_memory_digest_mismatch`). The
file is not repaired or overwritten. `put()` rejects an in-memory
object whose digest does not match its canonical content.

`FileScientificRunMemoryStore.put`:

- missing `run_id` → persist
- same `run_id` + same digest → idempotent no-op
- same `run_id` + different digest → `ScientificRunMemoryConflict`

No last-writer-wins.

## Local store

Default root: `outputs/scientific_memory/episodic/`
Environment: `CELLERTZ_SCIENTIFIC_MEMORY_ROOT`

This is not `CELLERTZ_SECOND_BRAIN_ROOT`, which still points at the
semantic Markdown vault `second_brain/`.

```
<root>/
    runs/
        <run_id>/
            memory.json
```

Writes are atomic: temporary file + fsync + `os.replace`.

PDB and PAE files are referenced by type, optional URI, and
authoritative SHA-256. They are not copied into the JSON document.

## Queries (v1)

`get_run(run_id)`
`find_runs_by_target(target_id)`
`find_candidate(candidate_id)`

These return **episodic observations**.

`find_runs_by_target("PDL1")` means:

> What runs/results have we observed for PDL1?

It does not mean:

> What has Cellertz scientifically validated about PDL1?

They do not imply a validated binder, a validated biological claim,
a recommended experiment, or experimental success.

## Ingest

Real completed runs are ingested explicitly through the developer
operator:

```
tools/validation/second_brain_ingest_protein_run.py
```

Default is inspect-only. `--persist` is required to write.
`--backend file|databricks` overrides `CELLERTZ_SCIENTIFIC_MEMORY_BACKEND`.
Default backend is `file`. Inspect-only with `--backend databricks`
projects memory and does not connect or write.

The operator is not an MCP tool. LocalProteinRunStarter does not
auto-write episodic run memory in this stage. Persist does not
create or modify semantic KnowledgePackages.
