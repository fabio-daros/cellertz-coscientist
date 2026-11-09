# Databricks Episodic Scientific Run Memory — Phase 5A

Optional storage adapter for observed `ScientificRunMemory`.
It does not execute BindCraft, unlock MCP, add RAG, or promote
semantic KnowledgePackages.

```
ScientificRunMemory
        ↓
ScientificRunMemoryStore
       / \
      /   \
FileScientificRunMemoryStore
DatabricksScientificRunMemoryStore
```

The domain model does not know which backend is in use.
`memory_house` remains `episodic`. `epistemic_status` remains
`observed`.

File remains the default local backend. Databricks is opt-in.

## What Databricks is here

Databricks is **storage**, not scientific authority.

A persisted row is not validated evidence. Completing a MERGE does
not promote an observation into a KnowledgePackage.

```
Delta table  ≠  scientific truth
memory JSON  =  authoritative v1 document
flattened columns  =  identity / query metadata only
```

Flattened columns copy identity fields already present on the
memory object. They do not recompute ipSAE, rank, or selection.
On read, flattened `run_id` / `memory_digest` / `target_id` are
cross-checked against the reconstructed JSON. Disagreement fails
closed (`scientific_memory_index_mismatch`). The JSON remains
authoritative.

## Table contract (v1)

One authoritative Delta table: `scientific_run_memory`.

One row per run. No candidate table. No Volumes. No PDB/PAE
upload.

Columns:

- `schema_version`
- `memory_id`
- `run_id`
- `domain`
- `memory_house`
- `epistemic_status`
- `execution_id`
- `target_id`
- `objective`
- `generator`
- `scientific_request_sha256`
- `request_hash`
- `manifest_sha256`
- `memory_digest`
- `recorded_at`
- `memory_document_json`

`memory_document_json` is the complete `ScientificRunMemory`
document. Reads reconstruct memory only from that JSON through
`scientific_run_memory_from_document()`, which recomputes the
canonical digest and fails closed on mismatch.

v1 CREATE TABLE keeps identity columns `NOT NULL` and
`delta.isolationLevel = Serializable`. It does **not** emit
inline CHECK constraints. Databricks Delta CHECK constraints
require `ALTER TABLE … ADD CONSTRAINT`, which is not made
idempotent in this phase. Domain validation already fail-closes
on `schema_version`, `domain`, `memory_house`, and
`epistemic_status`. The store remains the scientific invariant
authority.

## Immutability

`put(memory)`:

1. validate `ScientificRunMemory` and `memory_digest`
2. MERGE on `run_id`

```
missing run_id              → insert
same run_id + same digest   → idempotent no-op, return existing
same run_id + different digest
                            → ScientificRunMemoryConflict
```

`put()` uses insert-only MERGE. There is no `WHEN MATCHED` clause
and no identity UPDATE. An existing row is never modified.

```
MERGE INTO target
USING source
ON target.run_id = source.run_id
WHEN NOT MATCHED THEN INSERT ...
```

After MERGE, the store always reads `run_id` back:

```
0 rows  → not found; put failed or unresolved concurrent write
1 row   → deserialize + digest validate
>1 rows → scientific_memory_duplicate_run_id (fail closed)
```

Delta PRIMARY KEY / UNIQUE constraints are not a uniqueness
guarantee. The application contract remains authoritative.

`ensure_schema()` sets `delta.isolationLevel = Serializable`.
Default WriteSerializable can let two concurrent `NOT MATCHED`
inserts both succeed. Serializable reduces that race; it does
not replace post-read duplicate detection.

Named SQL parameters (`:run_id`, `:target_id`, …) are passed to
`cursor.execute()`. Catalog/schema/table names are allowlisted
identifiers, never bound parameters.

Concurrent MERGE conflicts (`ConcurrentAppendException` and
siblings) are not overwrite signals. The store retries MERGE at
most once, then reads back:

- one row + same digest → idempotent success
- one row + different digest → `ScientificRunMemoryConflict`
- multiple rows → fail closed
- still missing after a recognized conflict →
  `databricks_concurrent_write_unresolved`

Unrecognized backend errors are not retried.

## Queries

Same contract as `FileScientificRunMemoryStore`:

- `get_run(run_id)`
- `find_runs_by_target(target_id)`
- `find_candidate(candidate_id)`

Every query uses the same one-run-one-row helper. Duplicate
physical `run_id` rows fail closed
(`scientific_memory_duplicate_run_id`) in `get_run`,
`find_runs_by_target`, and `find_candidate`.

`find_candidate` v1 reads candidate identity from the authoritative
JSON. A derived candidate index, if added later, must be rebuildable
and non-authoritative.

## Artifacts

This adapter does not store PDB, PAE, CSV, or execution trees.
`ArtifactReference` hashes stay inside the memory JSON. Volumes are
future. Physical artifact placement is a separate interface.

## Configuration

Optional extra:

```
pip install 'cellertz-ai-coscientist[databricks]'
```

Backend selector (Phase 5B, default `file`):

```
CELLERTZ_SCIENTIFIC_MEMORY_BACKEND=file|databricks
```

Databricks is explicit. File remains the default. The developer
ingest operator uses this selector; inspect-only does not open a
Databricks connection.

Environment (never commit tokens):

```
CELLERTZ_DATABRICKS_SERVER_HOSTNAME
CELLERTZ_DATABRICKS_HTTP_PATH
CELLERTZ_DATABRICKS_ACCESS_TOKEN
CELLERTZ_DATABRICKS_CATALOG
CELLERTZ_DATABRICKS_SCHEMA
CELLERTZ_DATABRICKS_TABLE   # default scientific_run_memory
```

Production SQL uses `databricks-sql-connector`, imported lazily.
Tests use `InMemoryDatabricksSqlExecutor` (no network, credentials,
Spark, or JVM).

Schema creation is explicit: `ensure_schema()` or the developer
operator `--ensure-schema`. Reads do not create tables. Bootstrap
sets `delta.isolationLevel = Serializable` because default
WriteSerializable can admit two concurrent insert-only MERGEs.
That property is not a uniqueness constraint.

```
tools/validation/databricks_scientific_memory.py
```

Default is inspect-only. Not an MCP tool.

## Not this phase

- semantic KnowledgePackage promotion
- RAG / embeddings
- LocalProteinRunStarter auto-wiring
- MCP real mutation
- BindCraft / Supervisor / ipSAE execution
- Databricks Volumes
```
