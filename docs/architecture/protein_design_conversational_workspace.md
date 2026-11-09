# Protein Design conversational workspace — Phase 6A

Phase 6A is a navigable prototype. It shows how a scientist will
move from a conversation to a draft, and later to an approved run.
It does not perform that run.

```
scientist conversation
        ↓
ProteinDesignDraft
        ↓
review / explicit approval (future 6D)
        ↓
Supervisor execution (future 6D)
        ↓
candidates / scores / evidence / artifacts
```

```
AUTHORIZED != EXECUTED
PREFLIGHT READY != EXECUTED
REAL OUTPUT != VALIDATED KNOWLEDGE
CONVERSATION STATE != SCIENTIFIC MEMORY
DRAFT != APPROVED REQUEST
UI APPROVAL != SERVER-SIDE AUTHORIZATION
```

## Boundary

The UI is a client of the application layer. It is not a
client of the scientific core.

```
UI → future Application API → Harness / MCP / Supervisor
                         ├── episodic ScientificRunMemory
                         └── validated Second Brain
```

`src/cellertz/application/` holds the display contracts:
session, messages, draft, project context, pipeline and agent
snapshots, candidates, evidence, artifacts, runs, model usage,
and budget. They serialize to JSON. A draft is never executable.
Unknown cost stays `null`. Negative tokens or money are rejected.
A budget object cannot authorize execution. Model cost and
scientific infrastructure cost are separate fields.

The React workspace lives in `apps/protein-design-workspace/`.
Its TypeScript types match `WorkspaceSession.to_dict()`. The
checked-in file `src/session-fixture.json` is that serialization,
and `src/fixture.ts` loads it. A Python parity test rejects drift.
Reload clears local UI state.

`VITE_CELLERTZ_DEMONSTRATION_JOURNEY_ID` names the demonstration
journey for one installation. Vite applies it when the frontend is
built, and when the dev server transforms the module. The browser
does not read it from the host environment at page load, so a change
requires building again. The source has no journey id of its own.
For the Bioturing PDL1 pilot the value is
`journey_ea6cc3b62cff41558d5a4a75c191a1bd`. See
`apps/protein-design-workspace/.env.example`.

Saved campaigns use a separate store. `CELLERTZ_CAMPAIGN_STORE`
defaults to `memory`. An installation that should keep campaigns
sets `CELLERTZ_CAMPAIGN_STORE=sqlite` and
`CELLERTZ_CAMPAIGN_DB` to a new absolute file, for example
`/mnt/hd10tb/cellertz-pdl1-demonstration-state/campaigns.sqlite`.
That file is created at schema version 1. It is not a migration of
`design-journeys.sqlite` or `generation-plans.sqlite`, and those
files are refused if they are given as the campaign database.

The PDL1 fixture names the recorded smoke run
`protein_run_pdl1_smoke` and candidate
`bindcraft_PDL1_l65_s585903_mpnn1` as an observed identity.
`fixture_display_slot` is a placeholder: unknown epistemic
status, no observed evidence, and no artifacts. Evidence and
artifact rows carry the candidate they describe. The fixture
does not republish a score, open an artifact file, or claim a
new observation. `ranking_score` stays raw ipSAE, which is not
a probability and not a hard lab filter. Evidence and provenance
are separate rows. Artifact values are `artifact://` metadata
URIs only.

No model call is recorded, so the usage collection is empty.
Unknown model and infrastructure costs stay `null` on the usage
snapshot.

## What the prototype may do

Choose the recorded run, select a candidate, switch the
evidence, artifact, and budget panels, type a local message,
and edit the in-memory draft preview. Approve & Run stays
disabled and points to Phase 6D.

The fixture load does not fetch a URL. A read-only question
posts to the conversation edge. The browser does not read
`second_brain/`, import Python, or write scientific memory.

## Phase 6B

`ReadOnlyConversation` answers a question from a session and an
optional run. It calls only the read-only capability allowlist.
A forbidden question returns `forbidden_action` before any port
call. `handle_conversation` serves whatever `ReadOnlyConversation` it is
given. `build_readonly_service(reader, sessions)` reads
`ScientificRunMemory` through the injected reader. The PDL1 fixture
is only the demo catalog of `python -m cellertz.conversation`.
A run is read only when that session lists it, or the memory
document's `session_id` is that session. Otherwise the reply is
`run_not_in_session`. Past questions such as "what was executed?"
stay read-only. Orders such as "execute" stay `forbidden_action`.
No Paperclip adapter is installed.

## Phase 6C

A design intent is posted to `POST /api/design-draft`, separate from
the read-only `POST /api/conversation` path. The use case asks a
provider-neutral extractor for a patch, then accepts only
`target_id`, `objective`, `generator`, `n_candidates`, `length_min`,
and `length_max`. Those names are the `GenerationRequest` inputs the
Supervisor already requires. Unknown fields are rejected. The
documented ranking policy (`raw_ipsae`, Boltz2 as auxiliary evidence)
is attached as a default and cannot be patched.

`ProteinDesignRequestDraft` is an immutable revision and may be
incomplete. `ProteinDesignRequest` exists only when those inputs
validate. Both keep `execution_authorized` and `executable` false.
An isolated "confirmo", "confirm", or "looks good" returns
`confirmation_not_available_until_phase_6d` and keeps the current
draft. "approve and run", "execute", "start BindCraft", promotion,
and ingestion return `forbidden_action` before the extractor. No scientific memory is written. The demo
extractor is `DeterministicDraftExtractor`: it is not an LLM, and no
external provider is required.

Example: "Quero desenhar binders para PDL1, priorizando ipSAE e
mantendo Boltz2 somente como evidência auxiliar." That yields
`target_id=PDL1` and `objective=binder_design` from the user, the
ranking policy as a documented default, and clarification questions
for `generator` and `n_candidates`. Length bounds stay absent until
the user supplies them.

A revision request sends `request_id` (the id the server put on the
draft) and the integer `revision` it expects. There is no
`revision_id`. Neither value, nor `session_id` or provenance, can be
patched. One in-process lock covers lookup, revision check, extraction,
and storage. An unknown id, or an id owned by another session, returns
`draft_not_found`. A stale revision in the right session returns
`revision_conflict` with HTTP 409 and the current revision, without
calling the extractor.

Phase 6D is still the only approval and execution path. A draft is
not authorization. A complete request is not a run.

## Phase 6C.2

The workspace preview presents the Phase 6C.1 operational references
inside the same read-only prototype. CD22 (PDB 7O52) and TACI (PDB
1XU1) are display projections in
`apps/protein-design-workspace/src/campaign-fixtures.json`. The
authoritative contracts stay in Python. The frontend does not
revalidate them, approve them, or execute them.

The guided workspace uses a light research chrome: a soft page gradient,
white surfaces, a teal accent, and rounded cards. The header carries the
wordmark, the platform name, and example account controls. Area navigation
sits on the row below. A disclaimer sits at
the bottom of the page. Selected controls use a teal tint. Focus uses a
teal outline. Incomplete and blocked states keep a discrete amber or red
mark beside the text.

The header wordmark is the white Cellertz bio logo at
`apps/protein-design-workspace/public/cellertz-logo.png`, served as
`/cellertz-logo.png` and painted black. The accessible name stays
`Cellertz bio`. If the file fails to load, the header shows that text
instead. The image is not loaded from the public website.

Switching CD22 and TACI changes only the projected fixture. Chat
remains the Phase 6B read-only assistant. The design draft remains
the Phase 6C unauthorized revision flow, including HTTP 409. `Approve
& Run` stays disabled until Phase 6D.

The same preview is now a guided workspace rather than one long
record sheet. Protein Design is the active module. Target Discovery,
mRNA Design, and Experimental feedback stay labeled Planned and open
only a short description. Inside Protein Design, seven stages separate
campaign configuration, target and epitope reference, named tools,
future evaluation, future comparison, human review, and a planned
mRNA handoff. A stage state such as Incomplete, Reference loaded, Not
recorded, Blocked, or Planned is a label on that stage. It is not a
progress bar, and one evidence record does not fill the next.

The dashboard reports the selected campaign, the specification state,
and the main recorded blocker. For the operational references that
blocker is the missing mBER version. Execution stays unauthorized.
Candidate tables stay empty because these fixtures do not contain
generated candidates. No composite score is introduced. `ranking_score`
remains raw ipSAE, and iPTM and pLDDT remain generator filters.

The accessible logo fallback remains `Cellertz bio`.

## Phase 6D foundation

The first 6D slice is a server-side control gate, not scientific
execution. A human decision is recorded at `POST /api/design-approval`.
Authorization, budget enforcement, preflight, and dispatch happen only
at `POST /api/approved-execution`.

```
immutable draft revision
        ↓
explicit human decision
        ↓
approval receipt
        ↓
server-side authorization
        ↓
preflight
        ↓
idempotent fixture-only dispatch
        ↓
execution receipt
```

```
DRAFT != APPROVED REQUEST
APPROVED != AUTHORIZED
AUTHORIZED != PREFLIGHT READY
PREFLIGHT READY != DISPATCHED
DISPATCHED != COMPLETED
COMPLETED != SCIENTIFICALLY VALIDATED
UI CONTROL != SERVER-SIDE AUTHORIZATION
```

The server rebuilds the revision digest from the authoritative draft,
the session `ExecutionBudget`, and the execution profile. A browser
digest is not accepted. `reviewer_id` is operator-asserted in Phase 6D.
It is not production authentication. JSON fields are accepted only at
their exact types. A null, number, boolean, array, or object is not
coerced into an identity string. A boolean revision is rejected.

`approved` requires the current revision to be complete. `rejected`,
`changes_requested`, and `held` can be recorded while that revision is
still incomplete. None of those decisions authorizes or dispatches.
The idempotency scope is `(session_id, idempotency_key)`, and the
revision identity is `(session_id, request_id, revision)`. A newer
draft revision clears the displayed decision and demonstration receipt.
A response that arrives afterwards is kept only when its `request_id`
and revision still match the draft on screen. The review panel, the
demonstration receipt, and the Session trace use that same check.
Invalid UTF-8 is an invalid payload and does not dispatch.

Only `phase6d_fixture_only` can pass. That profile declares
`scientific_execution`, `external_tool_call`, `gpu_execution`,
`memory_write`, and `semantic_promotion` as false. mBER, BindCraft,
BindCraft2, Boltz, RFdiffusion, AWS Batch, and unknown executors fail
closed. The demonstration receipt is not a scientific run, does not
create candidates, and does not write scientific memory.

The default approval store is process-local and non-durable. It is a
prototype application store, not scientific memory. CD22 and TACI stay
`not_ready`, unauthorized, and not executable. Real tool dispatch,
asynchronous production runs, and new scientific candidates remain
future work.

## Phase 6E foundation

Phase 6E connects one valid Phase 6D demonstration to one observable
fixture run. It does not start mBER, BindCraft, Boltz, RFdiffusion, a
GPU, AWS Batch, or the protein Supervisor.

`LocalProteinRunStarter.start_fixture_run` is not reused. That starter
constructs `ProteinSupervisorV1`, writes protein-run state, and can
materialize candidate artifacts. The 6E runner stays inside
`cellertz.application` and returns deterministic operational metadata
only.

```
approved demonstration
        ↓
fixture run created
        ↓
queued → running → completed
                 → failed
        ↓
read-only monitoring
        ↓
retry only from failed, same run id
```

```
PREFLIGHT READY != RUN CREATED
RUN CREATED != RUNNING
RUNNING != COMPLETED
COMPLETED != SCIENTIFICALLY VALIDATED
RETRY != NEW SCIENTIFIC APPROVAL
OPERATIONAL ARTIFACT != SCIENTIFIC EVIDENCE
FIXTURE OUTPUT != SCIENTIFIC CANDIDATE
```

Creation requires the current approved revision, a server-rebuilt
digest, a valid budget, the fixture profile, and a demonstration
receipt already recorded by the gate. The browser sends
`session_id`, `approval_id`, and `idempotency_key`. It does not send
a digest, revision, budget, status, attempt, command, or executor.

In Phase 6E the lifecycle is synchronous and recorded in one call.
There is no production scheduler. Later reads use `GET /api/approved-runs/{run_id}`
and do not execute the runner. `POST /api/approved-runs/{run_id}/retry`
revalidates the approval and increments the attempt once. The same
retry key returns the same attempt.

The run store is process-local, non-durable, and separate from
scientific memory. One approval has at most one logical run. Artifacts
are in-memory metadata with a content digest. They are not scientific
evidence. Observed fixture usage can be zero because no model or
scientific tool is called. Unknown budget ceilings stay null. A
completed run means the fixture adapter finished.

The workspace shows the run beside the demonstration receipt and
labels it “Fixture-only run” and “Not scientific validation”. A newer
draft revision hides the previous approval, demonstration, and run.
CD22 and TACI stay blocked. There is no production authentication.

## Phase 6F foundation

Phase 6F adds a durable operational store for the Phase 6E fixture
run. The default store remains process-local and non-durable so unit
tests stay isolated. The conversation entrypoint selects the backend
from the server environment:

```text
CELLERTZ_APPROVED_RUN_STORE=memory|sqlite
CELLERTZ_APPROVED_RUN_DB=/server/owned/path/approved-runs.sqlite3
```

`sqlite` requires the database path. An unknown backend fails closed
and does not start the server. The browser cannot choose the backend
or the path. Importing the store module does not create a database.

The SQLite schema is version 1. A newer version is rejected and the
file is left unchanged. Each transition is committed before the next
one. The run is durably `queued`, then durably `running`, before the
fixture runner is called. The terminal state is committed after the
runner returns. A read does not call the runner. Only the operation
that claims creation or retry may call the runner. Create keys, the
one-run-per-approval binding, and retry snapshots survive a restart.
The same retry key still returns the attempt it created.

Phase 6F has no worker. One operating-system process owns the resolved
database file. Opening the store takes an exclusive instance lock. A
symlink is not a second owner. A second process, including a forked
child, fails closed and does not mark an active run failed. After
the owner exits, the next process may open the database. Startup then
marks abandoned `queued` or `running` runs `failed`, appends one
server recovery event, and keeps the run id. It does not resume them.
The fixed text is: “The fixture run was interrupted before completion.
No scientific action was taken.” `queued → failed` exists only for
that recovery. A later explicit retry can open the next attempt.
Recovery does not call the runner.

```text
durable operational run != scientific result
run completed != scientifically validated
persisted != observed scientific evidence
persisted != semantic knowledge
```

The database is local prototype infrastructure. It is separate from
`ScientificRunMemory` and from the semantic Second Brain. It does not
make a completed fixture run scientifically valid.

## Phase 6G foundation

Phase 6G separates durable enqueue from fixture execution. Create and
retry revalidate the current approval, write a `queued` attempt, and
return HTTP 202 without calling the runner. The browser cannot start
the worker or select an executor. A server-owned background thread in
the same operating-system process polls the approved-run store:

```text
POST create/retry -> durable queued -> HTTP 202
worker claim      -> durable running
fixture adapter   -> durable completed | failed
GET polling       -> read only
```

The store claims one queued attempt atomically before the adapter is
called. Even if two worker objects race in tests, one attempt is
invoked once. The worker revalidates the approval, revision digest,
demonstration profile, and recorded budget after the claim and before
the adapter. A failed revalidation records the fixed safe failure and
does not call the adapter.

Startup recovery now distinguishes work that never began from work
that may have produced an uncommitted effect. A durable `queued` run
survives restart and remains eligible for the worker. An abandoned
`running` attempt becomes `failed` and is not automatically replayed.
This preserves at-most-once automatic invocation across a process
crash; a human-triggered idempotent retry is required afterward.

The worker stops cooperatively: it stops claiming new work and allows
an in-flight fixture call a bounded opportunity to finish. The poll
interval is process configuration, bounded to greater than zero and at
most 60 seconds:

```text
CELLERTZ_APPROVED_RUN_WORKER_POLL_SECONDS=0.25
```

The workspace polls `GET` while status is `queued` or `running` and
stops at `completed` or `failed`. A response with an older operation,
attempt, or shorter event history cannot replace the visible run.
Manual refresh remains read-only.

There is still no external queue, Redis, Celery, second worker process,
GPU scheduler, model call, scientific tool, candidate, or semantic
promotion. `completed` means only that the fixture adapter returned.

## Phase 6H foundation

Phase 6H lets a scientist return to the current server session, list
its fixture runs, open one, and view or download the immutable
operational artifacts from the visible attempt. The list and artifact
routes are read-only. They do not claim work, call the runner, recover
a run, or retry it. The browser does not choose another session, and
it does not upload artifact bytes, digests, paths, commands, or media
types. `run_id` and `artifact_id` are identifiers, not filesystem paths.

The fixture adapter writes a small deterministic package: receipt,
manifest, summary, input digest, output digest, and operational log.
Each artifact says it is a fixture operational artifact, not scientific
validation, that no scientific action was taken, and that no candidate
protein was generated. The package is not a PDB, CIF, sequence,
ranking, affinity, or structure. A future structure viewer is out of
scope; this phase does not render one.

Schema version 2 adds `artifact_bytes` and `runs.updated_at`. Opening a
valid version 1 database migrates it in one transaction and keeps the
existing run documents. Version 1 rows have no stored bytes, so an
artifact read fails closed. A schema newer than version 2 is rejected
and the file is not modified. Importing the store module does not
create a database. Every artifact read checks the stored bytes against
the persisted SHA-256 and size. A mismatch, a disallowed media type, or
an oversized record fails closed with a fixed message and no driver
text, path, or traceback.

The terminal run update and its artifact rows commit together. A reader
sees the previous run or the complete artifact set. Closing the store
waits for an in-flight artifact read. A retry seals the snapshot for
that attempt and does not rewrite an earlier snapshot. Artifact bytes
for an older attempt stay stored and are not served as the visible
attempt.

Operational artifacts do not enter scientific memory or the semantic
Second Brain. They are not associated with CD22 or TACI candidates.
`completed` is still not scientific validation.

## Phase 6I foundation

The campaign workspace has a separate Scientific runner readiness
section. It is not the fixture-run panel. Refresh is a read-only `GET`
of `/api/scientific-runner-readiness`. The server builds one immutable
snapshot at startup; the request does not probe and does not change an
approved run, scientific memory, or the Second Brain.

The section shows the profile, tool, public versions, status, checks,
missing requirements, and the observation time. It states that real
scientific execution remains disabled and that the result is
operational readiness, not scientific validation. There is no Execute,
Run BindCraft, or Generate candidates action. A slower or older refresh
does not replace a newer observation.

The default process configuration is:

```text
CELLERTZ_SCIENTIFIC_RUNNER_READINESS=disabled
```

`manifest` reads `CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST`. There is no
probe executable setting. A manifest without an injected observation
stays blocked. An injected observation can mark only the manifest it
names, and only in tests. The browser compares observation times as
UTC instants and accepts only the canonical `+00:00` form.

## Phase 6J foundation

Phase 6J records explicit permission for one future scientific run.
It does not execute that run.

The Human review stage keeps readiness and fixture runs in their own
sections. After the current request is approved, the fixture
demonstration is recorded, the fixture-only run is completed, and a
Phase 6I snapshot is ready, the scientist chooses Review scientific
authorization. The summary shows only public server values. Confirm
scientific authorization is a second action. Success says that no
scientific execution has started. Readiness refresh, approval,
demonstration, fixture completion, page load, and history selection
do not authorize.

The browser posts only an approval id and an idempotency key. The
server rebuilds the request digest, budget, fixture run, and readiness
binding. CD22 and TACI stay blocked. The record is operational state
in `CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE` (`memory` by default, or
`sqlite`). It is not scientific memory and not a Second Brain entry.
Revocation is explicit and cannot be undone by another key. A later
revision, approval, budget, manifest, or readiness observation makes
the stored permission unusable. `claim_once` rechecks that live
binding inside the store write, so a stale authorization is not
consumed. A consumed authorization cannot be revoked. Phase 6J does
not expose a consume route. Phase 6K creates one scientific run
after consuming one current authorization. The two stores are
separate, so startup rejects a durable store paired with a memory
store. When both are durable, startup finishes a provisional run
left unconfirmed. The worker calls a server-owned executor outside
the database transaction. Production execution stays disabled until
an executor is configured, and a completed run is not scientific
validation. Phase 6L runs five deterministic fixture specialists, in
the supervisor's fixed order, on that executor for PDL1 only. New
executions require the installed supervisor to match the supported
contract. A stored v1 workflow trace stays readable under that schema
after the supervisor changes. Request
grounding sits in generation planning, hotspot constraints in scoring
planning, and explanation in prioritization. Those are not extra
agents. The supervisor class is not constructed. The trace is
operational orchestration evidence. It is not a scientific candidate
and not scientific validation. A completed lifecycle can still report
`scientific_execution` under the Phase 6K rule; the trace labels that
completion as a deterministic prototype, not a tool run.
`governed-fixture` is an opt-in server mode. `enabled` without a real
executor still refuses to boot. Visualization, scientific validation,
acceptance, and memory promotion remain later.

```text
CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE=memory
CELLERTZ_SCIENTIFIC_AUTHORIZATION_DB=/server/owned/path/scientific-authorizations.sqlite3
CELLERTZ_SCIENTIFIC_RUN_STORE=memory
CELLERTZ_SCIENTIFIC_RUN_DB=/server/owned/path/scientific-runs.sqlite3
CELLERTZ_SCIENTIFIC_RUN_WORKER_POLL_SECONDS=0.25
CELLERTZ_SCIENTIFIC_RUN_EXECUTION=disabled
```

## Interactive demonstration

The Conversation tab phrases records the server has already resolved.
`CELLERTZ_CONVERSATION_PROVIDER` defaults to `recorded`. `openrouter`
also requires `CELLERTZ_CONVERSATION_MODEL` and `OPENROUTER_API_KEY`.
`CELLERTZ_CONVERSATION_API_BASE` is optional and must stay an
`https://` chat-completions origin. Do not point it at the Hermes
gateway: that gateway executes tools and exposes run approval.

Questions can read the current design request, the local package
`protein_scoring_bcma_boltz2_v2` through `FileKnowledgeReader`, and the
newest fixture run plus a workflow trace when one is stored. That
package is the BCMA scoring benchmark. Its interpretation limits and
missing-evidence notes stay in the facts. They do not validate PDL1
or generalize to other generators, and the package id alone does not
establish applicability. A missing source is returned as
`status=unavailable`. A found package with missing evidence is
`status=incomplete`. Fixture history is newest-first, and the selected
run id stays on the fixture fact. A provider reply keeps its provider
and model when token usage is absent or incomplete. Recorded facts
stay the no-origin path. Stage names are copied from a stored
`cellertz.governed_agent_workflow.v1` trace. They are not created for
the demonstration. Scientific execution stays
`CELLERTZ_SCIENTIFIC_RUN_EXECUTION=disabled`.

## Deferred

- 6D real scientific dispatch: still future. This foundation slice
  authorizes only the fixture demonstration.
- 6K executes only through a configured scientific executor. The
  built-in server leaves execution disabled and does not invent a
  BindCraft command. Completion is not scientific validation, and the
  run is not written to scientific memory or the Second Brain.
- 6L proves orchestration, contracts, handoffs, provenance, failure
  isolation, and auditability with deterministic fixture specialists.
  Dynamic subagents and parallel fan-out are deferred. 6M.1 adds the
  PDL1 registration contract. 6M.2 binds input identity in manifest v2.
  6M.3 verifies those captured bytes. The Docker closeout can compare
  a `pdl1-bindcraft-docker-v1` bundle with the daemon it would use,
  and registration stays blocked on the missing host deadline service.
  Production execution stays disabled.
  Validation, visualization,
  acceptance, and memory promotion stay later.
- 6E/6F/6G/6H/6I production follow-up: an external scheduler, multi-process
  workers, measured provider usage, production authentication, real
  scientific tools, and a structure viewer for real PDB or CIF artifacts.
  A future `PaperclipUsageAdapter` may
  supply cost figures after that. Paperclip does not replace
  the Supervisor, the MCP allowlist, human approval, episodic
  memory, or semantic promotion.
- Later workspace depth beyond this prototype.

No Paperclip package is installed in 6A.
