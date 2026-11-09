# MCP mutating capabilities

Until MCP v1.1, Hermes is an observer. It may list Cellertz
capabilities and read persisted operational run state. It may not
start, retry, or resume a Protein Design run.

MCP v1.2 adds one mutating tool: `protein_start_fixture_run`.
Authorization is fixture-only. BindCraft, RFdiffusion, mBER, GPU,
and wet lab remain closed.

## 1. Mutation is explicitly separate from read

```
MCP v1.3

read-only:
  - cellertz_get_capabilities
  - protein_get_run_state
  - scientific_memory_get_run
  - scientific_memory_find_runs_by_target
  - scientific_memory_find_candidate
  - knowledge_get
  - knowledge_neighbors
  - knowledge_ground_package

mutating:
  - protein_start_fixture_run

authorization:
  mode: fixture_only
```

Second Brain retrieval is read-only and independent of
`CELLERTZ_MCP_MUTATION_MODE`. Hermes must not open Markdown
files. It calls `knowledge_get` / `knowledge_neighbors` /
`knowledge_ground_package`, which forward to `FileKnowledgeReader`.
Scientific why questions use `knowledge_ground_package` and receive
a compact EvidencePacket, not Markdown bodies.

`scientific_memory_*` reads recorded episodic observations
(`memory_house=episodic`, `epistemic_status=observed`). It is not
operational run state and not semantic knowledge. These tools are
not on the Hermes allowlist in this phase. Discovery does not
connect to Databricks.

A tool must not "look up a run and create it if missing".
`protein_get_run_state` stays read-only: missing IDs return
`found: false` and create nothing.

`cellertz_get_capabilities()` is explicit about what is open:

```json
{
  "mutation": {
    "enabled": true,
    "mode": "fixture_only",
    "real_generators_allowed": false
  }
}
```

That is the MCP v1.2 contract when Cellertz is running with
`CELLERTZ_MCP_MUTATION_MODE=fixture_only`:

```
read-only capabilities      yes
fixture mutation            yes
BindCraft / RFdiffusion / mBER  no
GPU                         no
wet lab                     no
```

Do not report `mutating_operations: false`. Fixture start is a real
mutation, even though scientific generators stay closed. The default
process mode is still `disabled` (`enabled: false`, `mode: disabled`,
`real_generators_allowed: false`).

## 2. Dual authorization

Cellertz fail-closed default:

```
CELLERTZ_MCP_MUTATION_MODE=disabled
```

The MCP process enables fixture starts only when:

```
CELLERTZ_MCP_MUTATION_MODE=fixture_only
```

Hermes must also allowlist `protein_start_fixture_run`. Changing
the Hermes allowlist cannot bypass Cellertz: an unknown or empty
mode is treated as disabled.

The LLM cannot choose a generator. The service forces `fixture`.

## 3. Idempotency is mandatory

The mutating tool requires `idempotency_key`. Records live in a
store separate from run state:

```
outputs/idempotency/<sha256(idempotency_key)>.json
```

The raw key is never used as a filename.

```json
{
  "idempotency_key": "...",
  "request_hash": "...",
  "run_id": "protein_run_...",
  "mode": "fixture",
  "created_at": "...",
  "status": "complete"
}
```

Same key + same request:

- same `run_id`
- no second Supervisor execution

Same key + different request:

- explicit `conflict`
- no write, no run

New key:

- new run

The claim is atomic. Two concurrent callers with the same key must
not both execute:

```
request A ─┐
           ├── same idempotency key
request B ─┘
                ↓
        only one wins the claim
                ↓
           one Supervisor execution
           one run_id
           both responses point at that run
```

Locally this is an exclusive file create (`O_CREAT|O_EXCL`) of
`outputs/idempotency/<sha256(key)>.json` with `status: pending`.
The loser waits until the record settles, or conflicts if the
request hash differs. A later DynamoDB/Postgres backend can keep
the same contract with a conditional write.

## 4. The MCP tool does not implement science

```
Hermes
  ↓
protein_start_fixture_run()
  ↓
server-side authorization
  ↓
idempotency store
  ↓
ProteinRunStarter
  ↓
ProteinSupervisorV1
  ↓
ProteinRunStateStore
```

The MCP adapter validates input and authorization, calls
`ProteinRunStarter`, and serializes the result. Scoring, ranking,
critique, and selection stay behind the Supervisor.

Request (v1):

```json
{
  "target_id": "BCMA",
  "objective": "binder_design",
  "n_candidates": 10,
  "idempotency_key": "..."
}
```

First call:

```json
{
  "run_id": "protein_run_...",
  "mode": "fixture",
  "created": true,
  "idempotent_replay": false,
  "status": "complete"
}
```

Replay of the same key + same input:

```json
{
  "run_id": "protein_run_...",
  "mode": "fixture",
  "created": false,
  "idempotent_replay": true,
  "status": "complete"
}
```

Fixture v1 completes synchronously. Hermes then inspects the run
with `protein_get_run_state(run_id)`.

## Implementation order

1. This contract
2. Authorization: fixture-only allowlist
3. Idempotency store
4. `ProteinRunStarter`
5. `protein_start_fixture_run`
6. First Hermes-controlled fixture run
