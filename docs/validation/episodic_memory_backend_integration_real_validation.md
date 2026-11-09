# Phase 5B — Episodic Memory Backend Integration — Real Validation

Backend-neutral explicit ingest, real-validated against the existing
Databricks trial/serverless environment. Not a production deployment.

```
completed existing PDL1 artifacts
        ↓
project_protein_design_run
        ↓
ScientificRunMemory
        ↓
backend selector
        ↓
DatabricksScientificRunMemoryStore
        ↓
existing same-digest memory
        ↓
idempotent persistence
```

## Validated run

| field | value |
| --- | --- |
| run_id | `protein_run_pdl1_smoke` |
| execution_id | `exec_pdl1_smoke` |
| target | PDL1 |
| candidate | `bindcraft_PDL1_l65_s585903_mpnn1` |
| memory_digest | `3ca3962307cada5ed9b99a3aaf7aded314359e976686f9d057bb1d0b4d22e39e` |
| memory_house | episodic |
| epistemic_status | observed |

No workspace host, HTTP path, workspace id, or credential is
recorded here.

## 1. Inspect-only Databricks

```
--backend databricks
```

without `--persist`:

- `backend = databricks`
- `persist_attempted = false`
- `status = inspect_only`
- no persistence requested
- same canonical digest

## 2. Explicit Databricks persist

```
--backend databricks --persist
```

- `backend = databricks`
- `persist_attempted = true`
- `status = persisted`
- same `run_id`
- same canonical digest

## 3. Independent `get_run`

- `found = true`
- same digest
- `memory_house = episodic`
- `epistemic_status = observed`

## 4. Idempotency

The newly projected `recorded_at` differed from the stored row.
The persisted / read-back record retained the existing stored
`recorded_at`. The digest was unchanged. Scientific identity was
not overwritten.

`recorded_at` and local artifact URIs are excluded from the
canonical scientific digest. Same observed science yields the
same digest across inspect, persist, and later `get_run`.

## Expected semantics

```
RUN COMPLETE != SCIENTIFIC CLAIM VALIDATED
EPISODIC MEMORY != SEMANTIC MEMORY
OBSERVED != VALIDATED
PERSISTED != PROMOTED
```

## What did not happen

- no BindCraft execution
- no GPU execution
- no Supervisor execution
- no scientific recomputation
- no semantic KnowledgePackage created
- no RAG involved
- no MCP mutation unlocked
- no Hermes capability added
- no automatic post-run ingest added

Databricks environment was trial/serverless, not production.
