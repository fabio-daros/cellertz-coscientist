# Scientific Execution Contract

Phase 4A. This document freezes whether a scientific execution
may happen. It does not run BindCraft, RFdiffusion, mBER, GPU,
or AWS Batch.

```
Phase 4A — Scientific Execution Contract
----------------------------------------
Tag: scientific-execution-v1-contract
Tests: 246 passed

AUTHORIZED ≠ EXECUTED
```

Historical sequence:

```
protein-design-v1-fixture
        │  Protein Design funcional, somente fixture
        ▼
second-brain-v1-hardened
        │  governance científica congelada
        ▼
scientific-execution-v1-contract
        │  real execution representável,
        │  mas ainda não executável
        ▼
Phase 4B
first controlled real generator
```

```
Scientific domain
      │
      │ execution intent
      ▼
Scientific Execution Contract
      │
      ├── authorization
      ├── immutable manifest
      ├── provenance
      └── fail-closed policy
      │
      ▼
Domain Adapter
      │
      ▼
ExecutionRuntime
```

Still forbidden:

```
Hermes ──X──> shell
Hermes ──X──> BindCraft
MCP    ──X──> real execution
```

`execution/` is transversal. Protein Design is the first consumer,
not the owner. The same contract later covers mRNA and Target
Discovery.

```
execution/     authorization + intent + provenance
runtime/       subprocess / future AWS Batch
```

Scientific mode is not the backend:

| combination | meaning |
| --- | --- |
| fixture + local | current Protein Design v1 |
| real + local | later controlled local tool |
| real + aws_batch | later controlled batch job |

## Order

```
GenerationRequest
      ↓
normalize request
      ↓
construct manifest
      ↓
validate manifest
      ↓
evaluate authorization
      ↓
freeze + hash manifest
      ↓
ONLY THEN
adapter → runtime
```

Never discover parameters after the tool has run.

## Authorization

`RealExecutionAuthorization` is an immutable object, not a
boolean. Operations are names, not protein-only generators:

```
protein.bindcraft.generate
protein.rfdiffusion.generate
protein.mber.generate
mrna.codonscope.design
```

During 4A the process authorization is:

```
enabled = false
allowed_operations = []
```

Tests may construct a simulated authorization. That does not
launch a generator:

```
simulated authorization
        ↓
AUTHORIZED
        ↓
runtime calls = 0
```

`AUTHORIZED ≠ EXECUTED`. 4B is the first time that boundary
is crossed on purpose. Internal capability is not Hermes
permission. MCP stays:

```
mutation.mode = fixture_only | disabled
real_generators_allowed = false
```

## Fail-closed policy

```
REAL requested + policy denies = STOP
```

There is no fixture fallback. There is no real fallback from a
fixture failure.

Reason codes:

- `AUTHORIZATION_MISSING`
- `REAL_EXECUTION_DISABLED`
- `OPERATION_NOT_ALLOWLISTED`
- `MANIFEST_INCOMPLETE`
- `LABEL_ACCESS_FORBIDDEN`
- `AUTHORIZED`
- `FIXTURE_ALLOWED`

## Provenance

`execution_id` is one attempt. `manifest_sha256` is the frozen
scientific identity. The hash covers mode, backend, adapter,
tool, checkpoint, inputs, parameters, seed, and data-access
flags. It does not cover `execution_id`, `run_id`, or
timestamps.

Label access is declared and hashed:

```
data_access.experimental_labels = false
data_access.evaluation_labels = false
```

REAL with labels true is denied.

## 4A closed surface

| Piece | Status |
| --- | --- |
| Scientific execution contract | complete |
| Real / fixture distinction | complete |
| Local / AWS Batch distinction | complete |
| RealExecutionAuthorization | complete |
| Operation allowlist | complete |
| Fail-closed policy | complete |
| Immutable manifest | complete |
| Deterministic manifest SHA | complete |
| Execution attempt identity | complete |
| Execution result contract | complete |
| Label-access provenance | complete |
| Real authorization enabled | locked (`false`) |
| Allowed real operations | locked (`[]`) |
| BindCraft execution | locked |
| RFdiffusion execution | locked |
| mBER execution | locked |
| GPU | locked |
| AWS Batch | locked |
| MCP real mutation | locked |

## Phase 4B — BindCraft real-execution path

```
Phase 4B.1 + 4B.2 — BindCraft execution readiness
-------------------------------------------------
Tag: bindcraft-execution-preflight-v1
Tests: 257 passed

PREFLIGHT READY ≠ EXECUTED
REAL OUTPUT ≠ VALIDATED KNOWLEDGE
```

4B.1 and 4B.2 prove readiness, not scientific generation:

```
real intent → manifest → authorization → preflight → environment readiness
```

They do not prove:

```
preflight ready → BindCraft execution
```

| Piece | Status |
| --- | --- |
| 4A scientific execution contract | complete |
| 4B.1 BindCraft execution bridge | complete |
| 4B.2 BindCraft deterministic preflight | complete |
| 4B.3 developer controlled real enablement | locked |
| 4B.4 first actual BindCraft generation | locked |
| GPU | locked |
| AWS | locked |
| MCP real mutation | locked |

Authorization hierarchy:

```
default process
    enabled = false
    allowed_operations = []

developer-controlled test
    explicit local authorization only

MCP / Hermes
    real execution unavailable
```

4B proves that an authorized REAL operation can reach the
adapter contract without opening Hermes, MCP, GPU, or AWS.

```
Developer-controlled invocation
        ↓
GenerationRequest
        ↓
ScientificExecutionManifest
        ↓
RealExecutionPolicy
        ↓
AUTHORIZED
        ↓
PREFLIGHT
        ↓
NOT_READY | ready, generation still gated
```

Still forbidden:

```
Hermes ──X──> real BindCraft
MCP    ──X──> real BindCraft
GPU    ──X──> enabled
AWS    ──X──> enabled
```

```
PREFLIGHT READY ≠ EXECUTED
REAL OUTPUT ≠ VALIDATED KNOWLEDGE
```

A ready environment test is not produced science. A BindCraft
design is episodic run output. It does not enter
`second_brain/` until later human-reviewed science.

4B.1 connects `protein.bindcraft.generate` to the 4A contract
in `domains/protein_design/execution/`. The adapter does not
authorize itself.

4B.2 is deterministic preflight. `execution_attempted` stays
false. Missing GPU is `not_ready`, not a failed generation.

4B.3 developer authorization is constructed explicitly in that
process. `system_real_authorization()` remains disabled.
MCP stays `real_generators_allowed = false`.

A first BindCraft claim that returns

```
status = rejected
error  = bindcraft_run_not_implemented
```

is terminal for that idempotency key. Replay it. Do not
reopen it for later compute. A future real execution must
use a fresh idempotency key.

Fresh keys now follow:

```
authorization
    → read-only scientific identity
    → inspect existing idempotency key
    → replay/resume original locator (no current preflight)
       OR
    → physical preflight (fresh execution only)
    → pending claim
    → snapshot / verify claimed inputs
    → generate (enable_generation=True)
    → execution_complete
    → scientific_pending (atomic CAS)
    → complete | failed
```

```
SCIENTIFIC IDENTITY CLAIMED == SCIENTIFIC INPUTS EXECUTED
```

Target, settings, checkpoint, advanced profile, and the
container image are re-hashed after the claim and copied
into an immutable snapshot. The runtime consumes the
snapshot container, not the original mutable SIF. Drift
fails closed before `runtime.execute()`.

Process `complete` is not lifecycle `complete`. The
execution must also write `final_design_stats.csv` under
the BindCraft output directory. Missing physical output
is `failed` / `bindcraft_stats_missing`. That is not
scientific validation.

After physical complete, science has exactly one owner:

```
pending
    → execution_complete
    → scientific_pending   # atomic CAS
    → complete | failed
```

Only the caller that wins `execution_complete → scientific_pending`
may invoke the Supervisor. Replay uses the original execution
locator stored on the idempotency record (`run_id`,
`execution_id`, `output_dir`, `manifest_sha256`). Those fields
are operational metadata and are not part of the scientific
request hash.

Durable replay does not depend on the new attempt's GPU,
Apptainer, or output directory. `execution_complete` is
actionable: waiters may CAS immediately rather than waiting
out the settle timeout. Handoff persistence validates produced
provenance and never rewrites it to match the claim. Science
resumes from the original canonical GenerationRequest stored
in the handoff, not from unclaimed replay fields.

Crash windows:

- after `execution_complete`, before CAS: a later caller may
  atomically acquire scientific ownership and resume
- after `scientific_pending`: no automatic takeover. Duplicate
  science is forbidden; recovery of a crashed owner is a later
  stage

```
EXECUTION COMPLETE ≠ SCIENTIFIC COMPLETE
REAL OUTPUT ≠ VALIDATED KNOWLEDGE
```

```
claimed advanced_profile_sha256
    ==
BindCraftApptainerPlan.advanced_sha256
    ==
execution provenance advanced_profile_sha256
```

MCP stays fixture-only. Real BindCraft execution is still
unreachable through MCP.

## Phase 6D foundation

Phase 6D adds a human decision in front of one demonstration profile.
It does not enable `system_real_authorization()` and it does not call
the BindCraft, mBER, Boltz, or RFdiffusion runtimes.

The application gate reuses `ExecutionBudget`. A budget still cannot
set `authorizes_execution`. Unknown ceilings stay null. The fixture
demonstration records zero observed model and tool calls only because
the adapter performs none. That zero is not an unknown cost rewritten
as zero, and it is not authorization.

Idempotency is in-memory and scoped to the workspace session:
`(session_id, idempotency_key)`. A revision is
`(session_id, request_id, revision)`. The same scope and the same
content return the same receipt. The same scope with different content
conflicts. One approval produces at most one demonstration dispatch.

`approved` requires a complete revision. `rejected`,
`changes_requested`, and `held` can be recorded on the current
incomplete revision. Those decisions do not authorize and do not
dispatch.

Preflight is separate from dispatch. `preflight_ready` does not mean
the demonstration was dispatched. Real execution, GPU, AWS Batch,
scientific memory writes, and knowledge promotion remain out of this
slice.

## Phase 6E foundation

Phase 6E records a fixture-only run after a Phase 6D demonstration.
The run is an application lifecycle, not a scientific execution.

`LocalProteinRunStarter.start_fixture_run` remains the path that
builds `ProteinSupervisorV1` and writes protein-run artifacts. Phase
6E does not call it. The fixture runner in
`src/cellertz/application/approved_run.py` does not import the
execution package, the runs package, a subprocess, or a network client.

Within one request the recorded transitions are `queued → running →
completed` or `queued → running → failed`. A failed run can return to
`queued` as a new attempt of the same `run_id`. `completed` does not
run again. Events are append-only. A runner exception is stored as a
fixed sentence and does not keep a traceback, path, or token.

The store is in memory for the process. It is not scientific memory
and it does not survive a restart. Monitoring is a read. Retry
revalidates the approval, the revision digest, the budget, and the
fixture profile. It does not accept a client-selected executor.

Operational artifacts are digests and labels. They are not candidates,
scores, or experimental evidence. Observed calls and costs are zero
only because the fixture runner calls nothing. Unknown ceilings stay
null. Completion is not scientific validation. CD22 and TACI are not
executed. Production authentication and a scheduler remain later work.

## Phase 6F foundation

Phase 6F persists the Phase 6E fixture lifecycle in a server-owned
SQLite database, schema version 1. The memory store remains the
default for isolated tests. The durable store is operational state.
It is not `ScientificRunMemory` and it is not the semantic Second
Brain. A row that survives a process restart is still not scientific
validation and not observed scientific evidence.

`queued` and `running` are committed before the fixture runner is
called. The terminal state is committed after it returns. A read does
not call the runner. Only the claim that wins creation or retry calls
the runner. Create and retry idempotency, including the snapshot for
each retry key, survive restart.

Phase 6F has no worker. One operating-system process owns the resolved
database file through an exclusive instance lock. A symlink is not a
second owner. A second process, including a forked child, fails
closed and cannot recover a run that the owner is still executing.
After the owner exits, startup does not resume an interrupted run. It
marks abandoned `queued` or `running` as `failed` with one server
recovery event and a fixed sentence. `queued → failed` is permitted
only for that recovery. An explicit retry can open the next attempt
afterward.
The database is local prototype infrastructure. A scheduler, real
tools, measured provider costs, authentication, and scientific
execution remain future work.

## Phase 6G foundation

Phase 6G adds a single background worker inside the server process.
The create and retry HTTP paths no longer call the fixture runner.
They commit `queued` and return HTTP 202. The worker atomically claims
one queued attempt, commits `running`, revalidates the approval and
demonstration, calls the fixture adapter outside the transaction, and
then commits `completed` or `failed`.

The SQLite instance lock from Phase 6F remains. This phase does not
introduce a second process or a distributed lease. Concurrent worker
objects over the same owned store are safe because the queued claim is
one store transaction. Retry snapshots are sealed by run id and
attempt when the worker commits a terminal state.

On restart, `queued` remains queued and can be claimed. Only an
abandoned `running` attempt is marked interrupted. It is not resumed
automatically because the adapter may have returned before the
terminal commit. Explicit retry opens the next attempt. Reads and UI
polling never call the runner.

The worker still cannot execute mBER, BindCraft, Boltz, RFdiffusion,
GPU, AWS Batch, a model, or any scientific tool. It writes no
scientific candidate, evidence, episodic memory, or semantic knowledge.
An external scheduler, multi-process workers, measured provider costs,
production authentication, and real scientific execution remain
future work.

## Phase 6H foundation

Phase 6H persists the fixture adapter's operational artifact bytes in
the approved-run database at schema version 2. A version 1 database
migrates transactionally. A newer schema is not modified. The server
calculates SHA-256 and size, allows only `application/json`,
`text/plain`, and `text/markdown`, and verifies those values on every
read. The workspace lists runs for the server's session and can view
or download the visible attempt. That access does not authorize
execution, does not promote a result, and does not write scientific
memory or the Second Brain. The artifacts are not structures or
candidates. A PDB or CIF viewer waits until a real scientific executor
exists.

## Phase 6I foundation

Phase 6I answers whether a versioned, server-configured scientific
runner is sufficiently defined to be considered ready for a future
controlled execution. It does not run that execution.

The first profile id is `pdl1-bindcraft-historical-v1`. Its tool
identity is BindCraft and its target scope is PDL1. Tool version,
runtime version, image digest, entrypoint, argv template, resource
envelope, canonical inputs and outputs, timeout, and output limit are
not invented. Until a server-owned manifest records them, the snapshot
is disabled or blocked. A passing observation is not a process launch.
Production reads no probe executable. Tests may inject an observation,
and it is ready only when its digest, profile, tool, versions, image,
target, and boolean `ready` flag match the parsed manifest exactly.
CD22 (`cd22-mber-v1`) and TACI (`taci-mber-v1`) stay blocked because
an mBER version is not an accepted execution requirement in this phase.

`GET /api/scientific-runner-readiness` returns the snapshot built at
startup. It does not start a probe, a worker, a retry, a recovery, or
a scientific tool. The browser cannot supply a backend, manifest,
executable, image, version, digest, argv, environment, working
directory, resource limit, target scope, or readiness result. Unknown
configuration fails closed with a stable code and without a path,
command, or traceback. A malformed readiness reply becomes the fixed
adapter-failure body.

```
FIXTURE COMPLETED != SCIENTIFIC EXECUTION
RUNNER READY != EXECUTION AUTHORIZED
EXECUTION AUTHORIZED != EXECUTED
EXECUTION COMPLETED != SCIENTIFIC VALIDATION
```

## Phase 6J foundation

Phase 6J records explicit permission only. A ready Phase 6I snapshot
does not authorize a run. An authorization does not execute a tool,
does not generate a scientific candidate, and is not scientific
validation. The record is operational state. Creating or revoking it
does not write scientific memory or the Second Brain.

The permission is bound to the server session, the current request
revision, the approval, the reconstructed request digest, the
execution budget, and the readiness observation. The browser cannot
supply those values. `claim_once` rechecks that live binding inside
the store write, and a consumed authorization cannot be revoked.
Phase 6K creates one scientific run only after consuming one current
authorization. The authorization store and the scientific-run store
are separate databases, so the pair is not one transaction. Startup
refuses to boot when one store is durable and the other is memory.
When both are durable, startup finishes a provisional run left
unconfirmed. The browser sends only the authorization id and an
idempotency key. A separate worker rechecks the binding and calls a
server-owned executor outside the database transaction. Production
startup leaves execution disabled and refuses to boot if execution is
enabled without a configured executor. No BindCraft command is
invented. A completed run is not scientific validation and is not
written to scientific memory or the Second Brain.

## Phase 6L governed agent runtime

Phase 6L is the first governed Protein Design agent runtime. The
worker still claims the attempt, rechecks the live binding, and calls
the executor outside the store transaction. The executor is
`GovernedProteinAgentRuntime`. It delegates each stage to one of five
injected deterministic fixture specialists: generation planning
(request grounding, no sequence), scoring planning (hotspot constraints,
no biological score), ranking planning (no ranked candidate), critique
(consistency review, no validation), and prioritization (explanation
plan, no acceptance). Copying stage names is not the runtime. Each
specialist receives a typed handoff and returns a typed fixture plan.
The runtime computes the semantic digest from that verified plan. A
specialist cannot supply its own digest.

New execution reads the supervisor module's `STAGE_ORDER` and specialist
version constants before any specialist is called. If those values
differ from the supported Phase 6L contract, the attempt fails closed,
no specialist runs, and no workflow artifact is stored. A future
supervisor requires an explicit new workflow schema before new
executions can proceed. A persisted
`cellertz.governed_agent_workflow.v1` trace is interpreted by that
frozen schema: the five v1 stages, their v1 order, their v1
implementation versions, and the v1 handoff rules. Historical validation
does not consult the live supervisor, so a later deployment leaves
existing v1 records readable. `ProteinSupervisorV1` is not constructed.
That class creates directories, writes filesystem artifacts, and can
emit candidate-like outputs. The legacy generation, scoring, ranking,
critique, and prioritization classes are not called for the same
reason.

The workflow is one server-owned plan. Each specialist runs once, in
order, and the next handoff's upstream digest is the previous stage's
semantic output digest. A failed, malformed, or tampered stage stops
the rest and stores no partial artifact set. The trace is the
`workflow_trace` artifact `scientific-agent-workflow.json`. Semantic
stage digests omit the observation timestamp. The artifact SHA-256
covers the complete canonical trace, including timestamps. Duplicate
JSON keys are rejected. Operational completion still has
`scientific_candidate_generated`, `scientific_validation`, `ranked`,
and `accepted` false. `scientific_execution` stays the Phase 6K flag:
true only when the scientific-run lifecycle completed. For this fixture
that means operational completion, not a scientific tool run. The trace
and the workspace say so. No scientific memory or Second Brain write
occurs.

This is the Cellertz subset of a multi-agent research system: one fixed
plan, narrow fixture specialists, explicit handoffs, bounded outputs,
provenance, and an append-only trace. It is not dynamic Anthropic-style
subagent spawning. Dynamic lead planning, runtime creation of
subagents, free-form prompts, autonomous tool selection, parallel
fan-out, model-driven delegation, unbounded search, self-improving
agents, a live citation agent, and real model calls are not
implemented. Phase 6M.1 adds the registration contract for a
separately governed PDL1 executor. It does not register a production
executor and does not launch BindCraft. Scientific validation,
visualization, acceptance, and memory promotion remain later.

`CELLERTZ_SCIENTIFIC_RUN_EXECUTION=enabled` without a registered real
executor still refuses to boot with `scientific_run_executor_unconfigured`.
`governed-fixture` is opt-in, server-owned, and limited to this
deterministic prototype. The browser cannot select it. Default
execution stays disabled.

## Phase 6M.1 registration contract

Phase 6M.1 is the preparatory boundary only. `register_pdl1_executor`
in `src/cellertz/application/pdl1_executor.py` is the trusted
composition seam. It is not an HTTP or MCP route, and
`cellertz.conversation.__main__` does not call it. `enabled` still
raises `scientific_run_executor_unconfigured`. `disabled` stays the
default. `governed-fixture` still constructs `GovernedProteinAgentRuntime`.

A registration is valid only when the execution specification, the
parsed `scientific-runner-manifest-v1` document, and the readiness
snapshot name the same profile, target, tool, runtime, image, manifest
digest, entrypoint, recorded argv template, timeout, output ceiling,
and resource envelope. The snapshot must be `ready`, with execution,
scientific execution, and scientific validation still false. The
readiness `argv_template` is an identity record. It is not process argv.
The execution plan uses `server-constructed-argv-v1` and directory
policy `server-owned-per-attempt`. Its environment policy is
`empty-explicit`: the environment tuple is the whole environment, and
an empty tuple does not inherit the parent process.

Digest conventions:

| Field | Convention |
| --- | --- |
| `image_digest` | `sha256:` plus 64 lowercase hex digits |
| `manifest_digest` | SHA-256 of the readiness canonical manifest |
| `target_structure_sha256` | SHA-256 of raw file bytes, no prefix |
| `settings_sha256` | SHA-256 of raw file bytes, no prefix |
| `advanced_profile_sha256` | SHA-256 of canonical JSON (`indent=2`, `sort_keys`, trailing newline) |

No historical smoke pin is a default or a verified runtime observation.

`scientific-runner-manifest-v1` expected inputs are name and media type
only. `ScientificRun` and `cellertz.scientific_execution_record.v1`
cannot store the three scientific-input digests. A matching manifest
digest therefore does not stop those inputs from changing. Registration
returns `pdl1_input_identity_unbound` and no executor. Supplying an
input binding does not override that rejection. The follow-up contract
is a new manifest schema that records those digests under the
conventions above. This phase does not extend the frozen public schemas.

`register_pdl1_executor` never returns an executor in Phase 6M.1.
`Pdl1ScientificRunExecutor` implements `ScientificRunExecutor` only as
a test and prototype seam. Direct construction is not production
registration. The adapter accepts only a confirmed running PDL1 attempt
whose public identity matches the specification, and whose
`request_digest` matches an input binding carrying the three digests.
A target label alone is not enough. It calls an injected runtime only
when that runtime declares timeout enforcement, process-group cleanup,
in-flight output limiting, explicit environment, and cpu, memory, and
gpu enforcement. Those declarations are not verified production
enforcement. A post-run directory measurement is not in-flight
enforcement. Success also requires integer exit code 0, cleanup
`stopped`, both enforcement flags, and exactly one `design_records` /
`text/csv` output inside the byte ceiling. A passing runtime double
validates orchestration and the execution-record metadata contract. It
does not validate scientific output contents. The only stored artifact
is `cellertz.scientific_execution_record.v1`. There is no workflow
trace, candidate record, structure file, or score.

Startup recovery still marks an abandoned `running` row interrupted and
does not call the runtime. An explicit retry is a new attempt and can
call the runtime again. Idempotent create replay does not. An
interrupted row is not proof that an external process stopped. This
phase does not claim exactly-once external execution. No production
subprocess launcher is registered.

## Phase 6M.2 input manifest

`scientific-runner-manifest-v2` keeps every v1 execution-policy field
and adds one `input_identity` object. Its keys are exactly
`target_structure_sha256`, `settings_sha256`, `advanced_profile_sha256`,
`argv_policy_version`, `directory_policy`, and `environment_policy`.
The two file digests are SHA-256 of raw bytes. `advanced_profile_sha256`
is SHA-256 of `canonical_advanced_profile_bytes` (indent 2, sorted keys,
trailing newline). Policy values are only `server-constructed-argv-v1`,
`server-owned-per-attempt`, and `empty-explicit`. The manifest digest
remains SHA-256 of the canonical manifest (`sort_keys`, separators `,`
and `:`, `ensure_ascii`). No historical pin is a default.

V1 documents stay name-and-media-type manifests. A v1 body labeled v2
is invalid. CD22 and TACI stay blocked. The public readiness,
authorization, run, artifact, and workflow-trace schemas are unchanged.
`manifest_digest` already belongs to the Phase 6J binding, so a changed
input changes that digest and fails live revalidation of a queued run.
The readiness observation must match the whole digest. There is no
second public digest field.

`Pdl1ValidatedInputIdentity` means the v2 declaration and the execution
specification name the same digests and policies. It does not mean the
files were read, and it does not mean an approved `ProteinDesignRequest`
selected them. That request digest still omits the three inputs.
Matching v2 registration returns `pdl1_production_registration_deferred`
and no executor. V1 registration still returns
`pdl1_input_identity_unbound`. Production execution stays disabled.

## Phase 6M.3 verified input bytes

`verify_pdl1_inputs` reads three absolute paths supplied by trusted
server composition. Paths are not added to the manifest, browser
payload, authorization, or run. Each limit is a positive integer no
greater than 1 MiB. The read stops after limit + 1 bytes, so `stat`
size is not the ceiling. Empty files are rejected.

Opening uses `O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC` and then
`fstat` on that descriptor. The final path component is not followed,
and a directory or FIFO is not a regular file. Linux and macOS provide
those flags. If they are absent, verification is rejected.

Target and settings digests are SHA-256 of the captured raw bytes.
Settings are not canonicalized. The advanced profile is strict UTF-8
JSON, one object, no duplicate keys, no nonfinite numbers, and at most
32 nesting levels. Its declared digest is SHA-256 of
`canonical_advanced_profile_bytes`. The source file need not already be
in that form. The canonical bytes have their own ceiling. Those bytes
are what the snapshot keeps. Byte identity is not scientific validity.

`Pdl1VerifiedInputs` binds the manifest digest, the three verified
digests, the policy versions, and the captured bytes. It stores no
path and no mutable parse tree. Replacing a source file afterward does
not change the snapshot. A later reopen is not the verified input.
Request compatibility for the historical profile remains unresolved,
so that registration still returns no executor.

## Phase 6M Docker closeout

`pdl1-bindcraft-docker-v1` is a separate profile. It does not reinterpret
`pdl1-bindcraft-historical-v1`. A new revision may carry
`cellertz.pdl1_input_selection.v1` with the manifest digest and the three
input digests. Revisions without that object keep their previous digest.
The browser does not supply paths, commands, or readiness.

A BindCraft request keeps `generator=bindcraft`. Its fixture demonstration
records the same revision digest and does not run a tool. Authorization
of that revision requires the demonstration, the Docker readiness profile,
and the existing budget checks. It does not require a fixture scientific
run. Changing the selection starts a new revision, so the previous
approval no longer applies.

`register_pdl1_executor` compares the manifest, the verified snapshot, the
approved selection, and a daemon observation taken through the same
control that would create the container. Private bundle limits cannot
override that policy. Production startup does not require a scientist
draft. It loads the server-owned deployment, manifest, and input root,
constructs `ProductionCommandRunner`, and queries Docker and systemd
through those boundaries. Enabled startup then loads readiness with a
probe built from that same observation and parsed manifest. A passing
probe requires the inspected image id and repository digest; the
presence of a config file is not readiness. Disabled and governed-fixture
startup do not use that probe. A missing or invalid file, a failed
observation, a readiness snapshot that is not ready, or a systemd
manager that does not answer remains
`scientific_run_executor_unconfigured` with fixed missing-token
diagnostics. Disabled mode does not construct that control.

The host deadline is a systemd transient timer. Readiness accepts the
interpreter only when it is a nonempty executable file, the enforcer
module imports under that interpreter, `systemctl`, `systemd-run`, and
`docker` answer, and the state directory is writable. User-manager mode
also requires `Linger=yes`. `systemd-run` installs the timer before
`docker start`, bound to the container id, daemon id, and image id. The
timer's process is the configured interpreter running
`cellertz.application.pdl1_host_deadline enforce --state` with the
deadline file for that container. `systemctl show` must report that
command. The unit description is not the command. Start records a claim
and releases it before `docker start`, so enforcement can invalidate the
claim while the worker is paused. A resumed worker cannot start after
the claim is enforced, failed, or uncertain. An inspection error is not
proof that the container exited. Recovery of a running claim uses the
live timer, service, daemon, and container. A missing, failed, or
mismatched unit stays fenced, and a confirmed exited container can be
reconciled without another create.
`start_new_session` is not that mechanism. A scripted or detached
sleeper still returns `pdl1_worker_loss_deadline_unresolved`.

The executor carries the full execution identity from that manifest:
digest, profile, tool and runtime versions, image identity, scientific
inputs, and the effective resource policy. Create and terminal
reconciliation both require the consumed authorization and the run to
match it. Recovery does not skip that check.

Each scientific run resolves the consumed authorization and the live
approved revision from the authorization service, verifies the selected
bytes under the server input root, and stages only those bytes.
Registering infrastructure does not authorize a run. A requested VRAM
quota is rejected. GPU assignment is `--gpus device=<id>`. Writable storage uses
`--storage-opt size`. The host environment is not passed through.
Scientific containers are not privileged and do not receive the Docker
socket.

The start protocol asks a supervisor to acknowledge the immutable
container id before `docker start`. Expiry or a missing acknowledgement
does not start the container. `start_new_session` is not treated as
worker-loss enforcement. Startup reconciliation does not relaunch. It
accepts output only when the current attempt, authorization, daemon,
container id, image, and container limits match. A reserved attempt with
a confirmed-absent container may be retried. Create, start, a daemon
error, a daemon identity change, or a foreign container stays blocked.

Operational completion stores `cellertz.scientific_execution_record.v1`
only. The design CSV stays in the attempt directory. It is not a
`candidate_record` and is not published through the artifact API. That is
the provenance gap. There is no workflow trace, ranking, acceptance, or
memory ingest.

Enabled startup calls `prepare_enabled_infrastructure` and fails closed
unless the observed readiness status is `ready`. No image, entrypoint,
or BindCraft argv is compiled into the server. Mac tests use
`ScriptedDockerControl`. They are not Linux GPU validation. The host
acceptance procedure is
`docs/validation/pdl1_linux_gpu_host_acceptance.md`. It plans checks for
one Linux GPU machine and does not record a host result. Same-uid
staging replacement, a readiness manifest read that follows a final
symlink, and the unpublished design CSV remain after that procedure.
The first governed real PDL1 run remains Phase 6N.

## Phase 6N approved runtime policy

`seed` and `filter_policy` are recorded only when a revision sets them.
`seed` is an integer constraint from 0 through 4294967295. Booleans are
rejected. `filter_policy` is the closed string `no_filters`, not an
integer. Absent values are omitted from `revision_document`, so a
revision approved without them keeps its digest. They are not filled in
later.

The Docker adapter, before `docker create`, requires the approved
`n_candidates`, both length bounds, `seed`, and `filter_policy` to equal
the container command and to sit inside the entrypoint ranges. Design
count is 1 through 64. Each length is 1 through 2147483647, and the
minimum is at most the maximum. Seed is 0 through 4294967295. An
approved revision may record a wider integer; that revision still does
not create a container. `no_filters` maps only to
`/opt/BindCraft/settings_filters/no_filters.json`. The command is those
five flags and their values. Readiness argv tokens stay slash-free
except that exact path. A missing or different value does not create a
container. Manifest equality remains an identity check and is not this
approval. A scripted Docker result is simulated.

SQLite scientific authorization stores the authorization document,
including `request_digest` and `binding_hash`. It does not store the
draft or the protein-design request, so a restart restores approval
identity and does not recover `seed` or `filter_policy`. Those values
remain on the in-memory draft. This does not change
`cellertz.scientific_execution_record.v1` or the Phase 6D session
fixture. Production execution stays disabled.

Image readiness is recorded in
`docs/validation/pdl1_image_readiness.md`. The BindCraft commit
`efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d` is the only source pin fixed
there. The base-image digest, host NVIDIA driver, CUDA version,
ColabDesign commit, PyRosetta wheel digest, and AlphaFold parameter
SHA-256 are operator-supplied. A missing value fails before a build
argument is returned. The base environment contract is the published
`install_bindcraft.sh` package list at that commit plus the Cellertz
entrypoint imports. Unpinned dependencies stay blockers. A CPU probe
checks the published facts before BindCraft is checked out and does not
execute `bindcraft.py`. A wheel file name with valid PEP 427 shape is
not an ABI proof. Vendor files must resolve inside
`deploy/pdl1-bindcraft/vendor/` of the same build context the Dockerfile
copies. A symlink on the final file, on `vendor`, or on an ancestor
directory is rejected. The scientific entrypoint and image probe do not
load the web application initializer imports. BindCraft stays on Python
3.10, and the project `requires-python` stays `>=3.12`. The Python 3.10
dynamic import check is pending where that interpreter is absent. That
check does not run Docker, systemd, or BindCraft, and it does not make
Phase 6N complete.

A temporary Bioturing environment is specified in
`docs/validation/pdl1_bioturing_temporary_environment.md`. It is a plan
for a removable XFS file on `/mnt/hd10tb` and a second Docker daemon.
Governed Docker commands now require `docker_host` and `docker_root`
in the private deployment and pass that socket as `--host`. They do
not inherit `DOCKER_HOST` and they do not fall back to the default
socket. The deadline stores that endpoint in its private state. The
worker is still not authorized to install timers in the system
manager, and the temporary daemon has not been provisioned or
validated on the host. The plan does not enable scientific execution,
and it does not make Phase 6N complete.
