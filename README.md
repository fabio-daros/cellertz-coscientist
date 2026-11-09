# Cellertz AI Co-Scientist

Local platform for scientific protein design. The first bounded
context is Protein Design. mRNA Design and Target Discovery are
planned sibling domains, not empty folders.

The system is a **deterministic scientific workflow** with an
optional LLM layer for explanation only. Hermes and the Second
Brain do not decide rank, score, or lab selection.

## Language

Project content is English. That covers UI labels, agent responses
and prompts, status and error messages, code comments,
documentation, and test descriptions.

A person may write a request in another language. The platform
interprets that request and answers in English. Recorded scientific
data stays as recorded: sequences, identifiers, filenames, hashes,
raw logs, and original artifacts are not translated.

Developer-authorized real BindCraft execution has been validated
on Bioturing (PDL1 smoke): execution → handoff → Supervisor →
ipSAE → ranking, with same-key durable replay and no second
physical run. Real MCP mutation remains locked. This is not a
production deployment.

## What it does today

Protein Design v1 runs one fixture round end to end:

```
generate → score → rank → critic → [ground] → [explain] → prioritize
```

Brackets are optional audit, not workflow stages.

| Stage | What it does | Decision? |
| --- | --- | --- |
| Generation | Fixture by default. BindCraft real execution is developer-authorized (Bioturing PDL1 smoke), not MCP. RFdiffusion / mBER remain locked. | no score |
| Scoring | `ranking_score` = raw ipSAE. Boltz2 2.2.1 is auxiliary | no composite |
| Ranking | Sort by raw ipSAE, then `candidate_id` | deterministic |
| Critic | Set-relative agreement / structural conflict | qualitative only |
| Grounded context | Validated Second Brain packet annotates the critic | does not change rank |
| Hermes explanation | Natural-language audit of `GroundedCriticResult` | non-blocking |
| Prioritization | Experimental portfolio. Critic flags do not reject | independent of Hermes |

If Hermes is down, the scientific run still completes. If the
Second Brain is unavailable, the deterministic Critic still
operates. If the explanation changes, selected candidates do
not.

```
Hermes DOWN              → scientific run still completes
Second Brain unavailable → deterministic Critic still operates
Explanation changes      → candidate selection does not change
```

## Science that is frozen

- `ranking_score` is raw ipSAE, not a probability and not a
  hard lab filter.
- Boltz2 ensemble metrics are additional evidence. They do not
  override ipSAE.
- No ipSAE + Boltz2 composite score is validated.
- YSD / experimental labels are not ranking features.
- The Supervisor is a stage machine. It does not recompute
  science.
- `ExecutionRuntime` runs argv tokens only. No shell strings.

First validated knowledge package:
`protein_scoring_bcma_boltz2_v2` (Chow 2026 BCMA / Boltz2 v2).

## Second Brain

`second_brain/` is a validated scientific vault, not a RAG
dump and not a company operating system.

```
retrieve → FileKnowledgeReader
           knowledge_get
           knowledge_neighbors
           ground_package / knowledge_ground_package
```

Hermes may verbalize an already-resolved `EvidencePacket`. It
must not walk the vault freely or write memory.

Three governance checks:

| Check | Question |
| --- | --- |
| Health | Is the vault well-formed? |
| Canaries | Are the frozen scientific truths still true? |
| Knowledge receipt | Which knowledge entered this run? |

```
python -m cellertz.knowledge.health
```

Health is CI / local only. It is not an MCP tool.

```
Working    request / session
Episodic   outputs/scientific_memory/episodic/
Semantic   second_brain/
Procedural code + Hermes skills
```

Working is not semantic. Episodic is not validated.
Procedural is not scientific evidence. Agent writeback is
forbidden. `reviewed_at` is not an expiration date.
A completed run remains observed until explicit future promotion.

## Hermes and MCP

Compose runs Hermes (`:8642`) and `cellertz-mcp`. Hermes may
only call the allowlisted MCP tools. It never gets a shell,
filesystem, or scientific generator.

```
read-only
  cellertz_get_capabilities
  protein_get_run_state
  knowledge_get
  knowledge_neighbors
  knowledge_ground_package

mutating
  protein_start_fixture_run     # fixture_only only
```

The MCP server also exposes read-only episodic retrieval
(`scientific_memory_get_run`, `scientific_memory_find_runs_by_target`,
`scientific_memory_find_candidate`). Those tools are not on the
Hermes allowlist in this phase. They return observed run records,
not validated knowledge. See
`docs/architecture/episodic_memory_retrieval.md`.
Explicit promotion of reviewed semantic knowledge is a separate
developer command, not an MCP or Hermes tool. See
`docs/architecture/scientific_knowledge_promotion.md`.

```
CELLERTZ_MCP_MUTATION_MODE=fixture_only
real_generators_allowed = false
```

That is not a toggle for BindCraft.

## Protein Design workspace (Phase 6A)

Phase 6A is a fixture-only Protein Design workspace. It does not
call Hermes, MCP, Databricks, or BindCraft. From
`apps/protein-design-workspace/`:

```
npm test -- --run
npm run build
npm run dev
```

`http://127.0.0.1:5173/` opens the audit view. Manual review is
`http://127.0.0.1:5173/?area=manual`. The Conversation tab keeps the
read-only record chat and the generation-plan review. The conversation
coordinator is `POST /api/attempt-coordinator`. It is an API, not a page.
Start the backend from the repository root, with a configured language
model. Credentials stay in the environment and are not written into the
request body. Plan durability is `CELLERTZ_GENERATION_PLAN_STORE=sqlite`
with `CELLERTZ_GENERATION_PLAN_DB` set to a path outside the repository.
Professor design journeys use `CELLERTZ_DESIGN_JOURNEY_STORE=sqlite`
and `CELLERTZ_DESIGN_JOURNEY_DB` the same way. The demonstration
startup must set both sqlite paths. Memory remains the test default.

```
CELLERTZ_CONVERSATION_PROVIDER=openrouter \
CELLERTZ_CONVERSATION_MODEL=<model> \
OPENROUTER_API_KEY=<key> \
python -m cellertz.conversation
```

If those variables are absent, the coordinator says the model is
unavailable and does not answer from a fixture. Do not set
`CELLERTZ_SCIENTIFIC_RUN_EXECUTION`. A generation request records a
versioned draft. Missing inputs, including filter policy, stay
questions. `no_filters` is recorded only when the professor states it,
and it does not establish scientific quality. A stated SHA-256 is not
verification; the plan binds hashes computed from the registered input
files. Approval is `POST /api/generation-plan/decision` for that revision
digest. Binding an existing authorization is
`POST /api/generation-plan/authorization`. That action does not grant the
authorization. Dispatch is `POST /api/generation-plan/dispatch`. A blocked
dispatch can be retried after the gates change; a referred run is not
created again. While scientific
execution is disabled, or the host deadline service is unresolved,
dispatch reports those blockers and does not start BindCraft.
attempt.2LUTUOX4 stays a historical manual attempt. The structural
review is `http://127.0.0.1:5173/?area=manual`.

Phase 6B posts a read-only question to `/api/conversation`.
The use case does not start a run. The default phrasing model is
recorded facts. `CELLERTZ_CONVERSATION_PROVIDER=openrouter` with
`CELLERTZ_CONVERSATION_MODEL` and `OPENROUTER_API_KEY` selects one
chat-completions adapter. That adapter receives resolved facts only. A provider reply keeps its
provider and model when token usage is absent.
It does not approve, authorize, or execute, and it is not five
specialists. The Hermes gateway is not this adapter: its chat route
runs tools. Phase 6C posts a design intent
to `/api/design-draft` and returns an unauthorized draft. A complete
request is not approval and not execution. Phase 6C.1 adds the VHH
campaign scientific contract. It does not run mBER or BindCraft2.
Phase 6C.2 previews those contracts in the workspace as a guided
Protein Design flow. Neighboring Target Discovery and mRNA Design
modules stay planned. The preview does not authorize or execute a
campaign. Phase 6D records an operator-asserted human decision and can
dispatch only the `phase6d_fixture_only` demonstration. It does not
execute mBER, BindCraft, Boltz, or RFdiffusion, and it does not make
the CD22 or TACI fixtures executable. Phase 6E records a synchronous
fixture-only run for that demonstration and exposes read-only
monitoring plus an idempotent retry. A completed fixture run is not
scientific validation. Phase 6F can persist that operational lifecycle
in a server-owned SQLite database (`CELLERTZ_APPROVED_RUN_STORE` and
`CELLERTZ_APPROVED_RUN_DB`). Restart durability is not scientific
memory, not the semantic Second Brain, and not scientific validation.
One operating-system process owns the resolved database file, so a
symlink is not another owner. A second process, including a forked
child, fails closed and does not fail a run the owner is still
executing.
After the owner exits, an interrupted fixture run is marked failed
and is not resumed. Phase 6G moves adapter execution out of the HTTP
request. `POST /api/approved-runs` and retry now durably enqueue an
attempt and return HTTP 202. A single background worker in the server
process claims queued attempts, commits `running`, invokes only the
fixture adapter, and commits the terminal state. The workspace polls
the read-only `GET` route while the run is active. Queued work survives
a restart; only an abandoned `running` attempt is recovered as failed.
The worker revalidates the approval and demonstration immediately
before invoking the adapter. Phase 6H stores the fixture adapter's
operational artifact bytes with that terminal commit. The workspace
can list the current server session's runs and view or download those
artifacts. Bytes are hashed and sized on the server, checked again on
every read, and are not a protein structure, a candidate, or scientific
validation. A Phase 6F/6G database at schema version 1 migrates to
version 2 inside one transaction. A newer schema is left unchanged.
History and artifact routes do not accept a session selector, do not
call the runner, and do not write scientific memory or the Second
Brain. Phase 6I adds a read-only readiness snapshot for a future
versioned scientific runner. The default is disabled. The historical
PDL1 BindCraft profile can become ready only when the server has
recorded every required identity, limit, and contract, and an injected
observation matches that manifest exactly. Production startup does not
launch a probe process. No BindCraft, Apptainer, CUDA, driver, image,
or mBER version is invented here. CD22 and TACI stay blocked. `GET
/api/scientific-runner-readiness` reads the startup snapshot and does
not probe, enqueue, retry, or execute. Ready is not authorization,
authorization is not execution, and execution would still not be
scientific validation. Phase 6J records a separate, explicit
permission for one future scientific run. The scientist reviews an
immutable summary and confirms it with a second action. That record
is operational state: it does not execute BindCraft, generate a
candidate, validate a result, or write scientific memory or the
Second Brain. A ready snapshot, an approval, or a completed fixture
run does not create it. The browser sends only the approval id and
an idempotency key. `claim_once` rechecks the live binding inside
the store write, and a consumed authorization cannot be revoked.
Phase 6K creates one scientific run after consuming one current
authorization. The authorization store and the scientific-run store
are separate, so startup rejects a durable store paired with a memory
store. When both are durable, startup finishes a provisional run left
unconfirmed. The browser sends only the authorization id and an
idempotency key. A separate worker rechecks the binding before calling
a server-owned executor. Production execution stays disabled, and
enabling it without an executor refuses to boot. A completed run is
not scientific validation and is not written to scientific memory or
the Second Brain. Phase 6L puts a fixed, sequential Protein Design
workflow behind that executor. Five deterministic fixture specialists
run in the supervisor stage order, using that module's specialist
versions at execution time. A drift in that order or those versions
fails the new execution closed and stores no workflow artifact.
Persisted `cellertz.governed_agent_workflow.v1` records stay readable
under that frozen schema. The runtime does not construct the supervisor class,
because that class writes files and can emit candidate-like outputs.
It does not run a model or a scientific tool, and it does not create a
scientific candidate. Copying stage names is not the runtime. Dynamic
subagent creation and parallel fan-out stay deferred. Phase 6M.1
adds the PDL1 registration contract only. Phase 6M.2 adds manifest v2
so the manifest digest binds PDL1 input identity. Phase 6M.3 verifies
the captured bytes for that declaration. Enabled startup loads the
server-owned deployment, manifest, and input root, observes Docker, and
checks that a systemd transient timer can run. Readiness comes from that
observation, not from the presence of the config file. It does not
require an approved draft. Each run resolves that draft from the
authorization service and stages only the selected bytes. The executor
accepts a run only when its authorization matches the full manifest
identity. Deadline enforcement can invalidate a start while the worker
is paused, and a resumed worker cannot start afterward. A sleeper
is not the host deadline. Absent or invalid infrastructure still
refuses to boot as `scientific_run_executor_unconfigured`. Production
execution stays disabled unless that infrastructure is configured. The
systemd manager,
overlay2 project quotas, json-file logs, and the NVIDIA runtime are
environment prerequisites and are not invented here. The host acceptance
plan is `docs/validation/pdl1_linux_gpu_host_acceptance.md`. It does not
validate Linux, systemd, Docker, or GPU enforcement.
`CELLERTZ_SCIENTIFIC_RUN_EXECUTION=governed-fixture` is an opt-in
server fixture, not production execution. Poll intervals are
server-owned:

```text
CELLERTZ_APPROVED_RUN_WORKER_POLL_SECONDS=0.25
CELLERTZ_SCIENTIFIC_RUNNER_READINESS=disabled
CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE=memory
CELLERTZ_SCIENTIFIC_AUTHORIZATION_DB=/server/owned/path/scientific-authorizations.sqlite3
CELLERTZ_SCIENTIFIC_RUN_STORE=memory
CELLERTZ_SCIENTIFIC_RUN_DB=/server/owned/path/scientific-runs.sqlite3
CELLERTZ_SCIENTIFIC_RUN_WORKER_POLL_SECONDS=0.25
CELLERTZ_SCIENTIFIC_RUN_EXECUTION=disabled
```

See
`docs/architecture/protein_design_conversational_workspace.md` and
`docs/architecture/vhh_campaign_scientific_contract.md`.

## Scientific execution

`src/cellertz/execution/` decides **whether** a real scientific
job may happen. `src/cellertz/runtime/` decides **how** a
process launches.

Developer-authorized real BindCraft (local Apptainer, not MCP)
has completed a PDL1 smoke on Bioturing. AWS Batch remains
future. Hermes cannot start BindCraft.

```
AUTHORIZED ≠ EXECUTED
PREFLIGHT READY ≠ EXECUTED
EXECUTION COMPLETE ≠ SCIENTIFIC CLAIM VALIDATED
```

A completed BindCraft run may be recorded as episodic
`ScientificRunMemory` (`observed`). It does not become a
semantic KnowledgePackage until explicit future promotion.

## What is locked

| Surface | Status |
| --- | --- |
| Fixture Protein Design v1 | complete |
| Second Brain governance | complete |
| Scientific execution contract | complete |
| BindCraft real local execution | validated PDL1 smoke on Bioturing (developer-authorized, not production) |
| Durable replay of that execution | validated (same-key, no second physical run) |
| Episodic ScientificRunMemory | current stage (File default; Databricks explicit optional backend; `CELLERTZ_SCIENTIFIC_MEMORY_BACKEND`) |
| RFdiffusion / mBER real runs | locked |
| AWS Batch | future |
| MCP real mutation | locked |
| Hermes → BindCraft | locked |
| Semantic knowledge writeback | locked |

Real MCP mutation, Hermes → BindCraft, AWS Batch, RFdiffusion /
mBER real execution, and automatic semantic writeback stay
locked. Episodic ingest of a completed run is explicit and
developer-only.

## Layout

```
src/cellertz/
  domains/protein_design/    agents, adapters, execution bridge
  execution/                 authorize + manifest + provenance
  runtime/                   argv / subprocess
  knowledge/                 reader, EvidencePacket, health, canaries
  mcp/                       allowlisted tools
  runs/                      fixture starter + operational state
  tools/protein_scoring/     scoring v2, Boltz2 ensemble
  state/                     lean run-state JSON

second_brain/                validated vault (Obsidian-compatible)
hermes/skills/               pinned Hermes skills
docs/architecture/           contracts
docs/experiments/            Chow / Boltz2 v2 records
```

## Run

```bash
docker compose up -d
docker compose exec cellertz-mcp pytest -q
docker compose exec cellertz-mcp python -m cellertz.knowledge.health
```

`.env` holds local keys and stays out of git. Never paste
secrets into chat or commits.

## Milestones

| Tag | Meaning | Tests |
| --- | --- | ---: |
| `protein-design-v1-fixture` | One-round fixture pipeline | 215 |
| `second-brain-v1-hardened` | Health, canaries, context receipts | 228 |
| `scientific-execution-v1-contract` | Real execution representable, not executable | 246 |
| `bindcraft-execution-preflight-v1` | BindCraft readiness at tag time | 257 |

## Architecture

- [Protein Design Supervisor](docs/architecture/protein_supervisor_generation_loop.md)
- [Ranking / Critic / Prioritization](docs/architecture/protein_ranking_critic_agent.md)
- [Second Brain](docs/architecture/second_brain.md)
- [Episodic scientific run memory](docs/architecture/scientific_run_memory.md)
- [Databricks episodic run memory](docs/architecture/databricks_scientific_memory.md)
- [Phase 5B ingest real validation](docs/validation/episodic_memory_backend_integration_real_validation.md)
- [Second Brain fixture baseline](docs/architecture/protein_design_v1_fixture.md)
- [Scientific execution](docs/architecture/scientific_execution.md)
- [Episodic memory retrieval](docs/architecture/episodic_memory_retrieval.md)
- [Scientific knowledge promotion](docs/architecture/scientific_knowledge_promotion.md)
- [Protein Design conversational workspace](docs/architecture/protein_design_conversational_workspace.md)
- [MCP mutating capabilities](docs/architecture/mcp_mutating_capabilities.md)
- [Chow 2026 BCMA benchmark](docs/experiments/chow2026_bcma_benchmark.md)
- [Boltz2 v2 unblind](docs/experiments/boltz2_v2_unblind_results.md)
