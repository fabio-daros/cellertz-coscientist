# Databricks Scientific Run Memory — real validation

Phase 5A real integration validation.

This is a trial/serverless environment record. It is not a
production deployment.

## Backend

Databricks serverless SQL Warehouse / Delta.

Logical table only:

```
cellertz.science.scientific_run_memory
```

No workspace host, HTTP path, workspace id, or credential is
recorded here.

## Validated real run

| field | value |
| --- | --- |
| run_id | `protein_run_pdl1_smoke` |
| target | PDL1 |
| candidate | `bindcraft_PDL1_l65_s585903_mpnn1` |
| memory_digest | `3ca3962307cada5ed9b99a3aaf7aded314359e976686f9d057bb1d0b4d22e39e` |
| memory_house | episodic |
| epistemic_status | observed |

## Validated operations

- `ensure_schema`
- `put`
- `get_run`
- `find_runs_by_target`
- `find_candidate`
- repeated same-digest `put` / idempotency

## Expected semantics

```
memory_house = episodic
epistemic_status = observed
Databricks persistence != validated scientific knowledge
```

## What did not happen

- no BindCraft re-execution
- no scientific recomputation
- no semantic KnowledgePackage created
- no RAG involved
- no MCP mutation unlocked

Obsidian / `second_brain/` remains the human-readable semantic
vault. Databricks remains structured persistence for observed
episodic memory. Rows were not mirrored into Markdown.
