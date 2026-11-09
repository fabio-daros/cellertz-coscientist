# Episodic scientific memory retrieval — Phase 5C

Read-only MCP access to recorded `ScientificRunMemory`.

This answers: what happened in a recorded run?

It does not answer: what has Cellertz scientifically validated?

```
ScientificRunMemoryReader
        ↓
scientific_memory_get_run
scientific_memory_find_runs_by_target
scientific_memory_find_candidate
```

`response_schema` is `episodic_retrieval_v1`.
`memory_house` is `episodic`.
`epistemic_status` is `observed`.

Every response includes:

```
Recorded execution observations; not validated scientific knowledge.
```

Those fields describe the retrieval surface. An empty or failed
response does not mean an observed record was found.

## Backend

The adapter uses `ScientificRunMemoryReader`. The default reader
is created lazily by `create_scientific_run_memory_store`.

File is the default. Databricks is used only when the server
process is configured with `CELLERTZ_SCIENTIFIC_MEMORY_BACKEND=databricks`
and the existing Databricks environment variables.

MCP callers cannot select the backend, credentials, SQL, file
root, or artifact paths.

Import, tool registration, capability discovery, and `/health`
do not open a Databricks connection. A real Databricks read may
connect and run `SELECT`. It never calls `put`, `ensure_schema`,
ingest, projection, or execution.

A Databricks configuration or connection failure is an error.
It does not fall back to the file store.

## Tools

`scientific_memory_get_run(run_id)`

Returns one complete stored document. It does not recompute
scores, digest, `recorded_at`, evidence, critic flags, or
selection. Artifact URIs stay references; their contents are
not read. v1 does not return a compact EvidencePacket. A large
run can produce a large payload. The scientific document is
not truncated.

`scientific_memory_find_runs_by_target(target_id, limit=20)`

Exact target match. Each item is compact: `run_id`, `target_id`,
`domain`, `generator`, `objective`, `execution_id`,
`memory_digest`, `recorded_at`, `memory_house`,
`epistemic_status`, `candidate_count`. No candidate arrays and
no artifact documents.

`scientific_memory_find_candidate(candidate_id, limit=20)`

Exact candidate match. One item per occurrence, including the
same candidate in several runs. Items are not collapsed to a
best or latest result. Each item is `run_id` plus
`candidate.to_document()`. The list does not invent a digest.
Use `scientific_memory_get_run` for the enclosing run.

List results are ordered by `run_id` ascending, not by score.
`limit` is an integer from 1 to 100. Booleans are rejected.
The response includes `items`, `returned_count`, `total_count`,
`limit`, and `truncated`.

The limit bounds the response, not the scan. Current readers
materialize every match. Candidate lookup can read every run.
There is no pagination in this phase.

Missing memory is `ok=true`, `found=false`. It does not ingest,
generate, replay, or create a run.

Corrupt JSON, digest mismatch, duplicate physical rows, and
backend failure are `ok=false`. They are not ordinary absence
and they are not partial success. Errors are stable codes.
Raw exception text, tokens, and connection settings are not
returned.

## Not this phase

- Hermes allowlist changes
- semantic promotion or Markdown mirroring
- RAG
- schema creation or memory writes on read
- phase freeze or Git close

## Real read-only check — executed 2026-09-23

The operator ran the four reads below against the already
persisted PDL1 memory on the trial/serverless warehouse.
This record does not mark Phase 5C FROZEN. Review of the
published diff and the Git close are still pending.

No credential, host, or warehouse path is recorded here.
The run did not ingest, call `ensure_schema`, or start
BindCraft, the Supervisor, or a GPU job.

Reported results:

| read | ok | found | result |
| --- | --- | --- | --- |
| `scientific_memory_get_run("protein_run_pdl1_smoke")` | true | true | `epistemic_status=observed`; stored digest matched |
| `scientific_memory_find_runs_by_target("PDL1")` | true | true | `protein_run_pdl1_smoke` present with the same digest |
| `scientific_memory_find_candidate("bindcraft_PDL1_l65_s585903_mpnn1")` | true | true | that candidate present on `protein_run_pdl1_smoke` |
| `scientific_memory_get_run("protein_run_absent_phase5c")` | true | false | `memory` is null; the missing run was not created |

Expected identity:

```
run_id: protein_run_pdl1_smoke
target_id: PDL1
candidate_id: bindcraft_PDL1_l65_s585903_mpnn1
memory_digest: 3ca3962307cada5ed9b99a3aaf7aded314359e976686f9d057bb1d0b4d22e39e
memory_house: episodic
epistemic_status: observed
```

The operator configured the server process with the existing
Databricks variables (`CELLERTZ_SCIENTIFIC_MEMORY_BACKEND=databricks`
plus the Phase 5A `CELLERTZ_DATABRICKS_*` settings) and ran:

```
python - << 'PY'
from cellertz.mcp.episodic_memory import (
    scientific_memory_find_candidate,
    scientific_memory_find_runs_by_target,
    scientific_memory_get_run,
    set_scientific_memory_reader,
)

set_scientific_memory_reader(None)
digest = "3ca3962307cada5ed9b99a3aaf7aded314359e976686f9d057bb1d0b4d22e39e"

run = scientific_memory_get_run("protein_run_pdl1_smoke")
assert run["ok"] is True and run["found"] is True
assert run["memory_digest"] == digest
assert run["memory"]["memory_digest"] == digest
assert run["epistemic_status"] == "observed"
assert run["memory_house"] == "episodic"

by_target = scientific_memory_find_runs_by_target("PDL1")
assert by_target["ok"] is True and by_target["found"] is True
assert any(
    item["run_id"] == "protein_run_pdl1_smoke"
    and item["memory_digest"] == digest
    and item["epistemic_status"] == "observed"
    for item in by_target["items"]
)

by_candidate = scientific_memory_find_candidate(
    "bindcraft_PDL1_l65_s585903_mpnn1"
)
assert by_candidate["ok"] is True and by_candidate["found"] is True
assert any(
    item["run_id"] == "protein_run_pdl1_smoke"
    for item in by_candidate["items"]
)

missing = scientific_memory_get_run("protein_run_absent_phase5c")
assert missing["ok"] is True and missing["found"] is False
assert missing["memory"] is None
PY
```

Status: real read completed. Phase 5C is not FROZEN.
