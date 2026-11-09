# Protein Design results journey

This note records the professor-facing path before a Django migration.
It describes the code that exists. A later slice is not described as
connected.

## What is implemented

The workspace has separate areas in
`apps/protein-design-workspace/src/App.tsx`: Campaign, Design request,
Conversation, Session, Results, and Manual review.

Design request (`/api/design-draft`, `/api/design-approval`,
`/api/approved-execution`) drafts and gates a design. Those routes do
not launch BindCraft.

Conversation has two readers. The legacy panel posts to
`/api/conversation`. That service can read episodic memory and the
Second Brain through `FileKnowledgeReader` in
`src/cellertz/conversation/__main__.py`. It phrases records. It does
not run the five specialists.

The recorded-results panel posts to `/api/attempt-coordinator`.
`AttemptCoordinator` calls a language model only to choose tools and
write the English answer. The tools in
`src/cellertz/application/attempt_evidence.py` are deterministic:

| Tool | Label | What it does |
| --- | --- | --- |
| `list_verified_attempts` | ranking | Lists catalog attempts. It does not rank them. |
| `get_attempt_evidence` | ranking | Reads the saved review, hashes, and execution constraints. |
| `compare_model_metrics` | scoring | Returns recorded cells. It does not compute ipSAE. |
| `get_geometric_contacts` | critique | Counts heavy-atom pairs at 5 Å from PDB bytes. |
| `link_structural_review` | critique | Returns the existing review link. |
| `propose_generation_plan` | generation | Saves an unapproved plan. |
| `propose_refinement` | generation | Saves a new unapproved revision. |

The five specialist agents live in
`src/cellertz/domains/protein_design/agents/` and run inside
`ProteinSupervisorV1` for a scientific or fixture run: generation,
scoring, ranking, critique, prioritization. Ranking sorts by raw
ipSAE (`ranking_score`). BindCraft `i_pTM` is not that score.
The coordinator labels are not calls to those agents.

Results (`/?area=results`) reads one attempt, candidate, and model
from `/api/attempts/{id}/inspection` and serves verified PDBs from
`/api/attempts/{id}/structures/{model}.pdb`. The route checks the
attempt id, the recorded design, the model id, path containment, and
the SHA-256. `attempt.c5c1f13e` with candidate
`PDL1_l65_s400711_mpnn1` is the recovered demonstration. The manual
area remains the historical review of `attempt.2LUTUOX4`. A missing
results identifier does not open that historical review.

A coordinator question is saved only after that question runs.
`CELLERTZ_COORDINATOR_TRACE_STORE=memory` keeps the trace until the
process stops. `sqlite` with `CELLERTZ_COORDINATOR_TRACE_DB` keeps it
in an application database, using the same convention as generation
plans. The results page lists traces whose saved attempt id matches.
It does not rebuild a trace from `review.json`. An attempt with no
saved question shows "No recorded coordinator trace". Tool steps are
deterministic. The final English step is language-model synthesis.
Neither one means the five specialist agents ran.

## Hermes and the Second Brain

Hermes is an optional explanation of an already built
`GroundedCriticResult` (`critic_explanation.py`, README). The
scientific supervisor can request it. If it is down, the run still
completes. The attempt coordinator and the results inspection do not
call Hermes.

The Second Brain is read-only knowledge. `FileKnowledgeReader` and
`ground_package` can annotate the critic. They do not change rank,
score, or the recorded review. The results inspection does not
retrieve knowledge and does not promote any.

Neither path is a live specialist trace for `attempt.c5c1f13e`.

## Fixture-only, disconnected, or missing

- Approved runs and their artifact route are fixture execution, not
  the recovered BindCraft attempt.
- Generation planning can record approval and an Apptainer
  demonstration dispatch. The results page does not subscribe to
  those events.
- Progress events exist for a demonstration launch. The results page
  shows the saved review only. It does not animate a live run.
- Pinned BindCraft `efb5bfeb` counts `max_trajectories` as PDB files
  in `Trajectory/Relaxed` only. A LowConfidence or Clashing attempt is
  not part of that count, so the pre-launch JSON check did not stop
  another trajectory. `attempt.c5c1f13e` still records
  `execution_constraints.unmet=true` for that historical layout.
  The demonstration bootstrap, adapter
  `bindcraft_attempted_trajectory_budget_v2`, replaces
  `check_n_trajectories` and `binder_hallucination` before the pinned
  script imports them. One reservation is written before each
  hallucination call. A failure that leaves no PDB consumes that
  reservation. Output files do not add extra attempts. An existing
  ledger is not resumed and is not reset. An unknown script or an
  invalid `max_trajectories` value does not start the worker. Final
  candidates and MPNN model structures are not attempts. The journey
  calls that launcher only after a separate execution authorization and
  a separate launch. The default server has no demonstration launcher.
- Ranking evidence for this attempt is absent. `i_pTM` stays a
  BindCraft cell. It is not displayed as ipSAE or `ranking_score`.

## Design journey

Submitting a design request from the Design request area records a
journey at `/api/design-journeys`. The journey is linked to the
request id, request revision, plan id, and plan revision.
`GenerationPlanService.draft` writes the plan.
`ProteinSupervisorV1.run` is not called. Its default scoring path uses
fixture metrics and is not the professor path. The journey records
specialist stages from saved execution evidence, described below.

The journey shows the saved plan revision, digest, inputs, parameters,
resource limits, and limitations. Record approval posts to the journey
and calls `GenerationPlanService.approve` for that plan id, revision,
and digest. The reviewer identity and timestamp are stored on the plan.
A stale revision or digest is rejected. A repeated approval of the same
revision does not add another approval record. A later plan revision
clears that approval. This recorded approval does not satisfy the
execution-readiness gate used by conversation dispatch, and it does not
launch compute or establish scientific validation. After approval, the
approval stage is completed and generation stays blocked. Recorded
plan approval is not execution authorization. Execution authorization
and launch are separate later actions, described below. The default
server still has no demonstration launcher, so a launch request is
refused before any container run.

The professor demonstration startup must select durable stores. Memory
remains the test default and is not the demonstration configuration:

```
CELLERTZ_GENERATION_PLAN_STORE=sqlite
CELLERTZ_GENERATION_PLAN_DB=/mnt/hd10tb/cellertz-pdl1-demonstration-state/generation-plans.sqlite
CELLERTZ_DESIGN_JOURNEY_STORE=sqlite
CELLERTZ_DESIGN_JOURNEY_DB=/mnt/hd10tb/cellertz-pdl1-demonstration-state/design-journeys.sqlite
```

A repeated request revision returns the saved journey and does not
draft another plan revision. The Second Brain is read only. A matching
validated PDL1 source is listed by id and version. If none matches,
the journey says the retrieval was irrelevant or unavailable. Hermes
is not called, because no grounded critic result exists.

The governed fixture specialists in `governed_agent_runtime.py` are
not this journey.

## Execution authorization and launch

Recorded plan approval and execution authorization are separate actions.
Recorded plan approval calls `GenerationPlanService.approve` with
execution readiness off and keeps `scope=recorded_plan`. Reopening the
journey does not upgrade that scope and does not launch. Execution
authorization is a second human action. It calls `approve` with
execution readiness required and replaces the recorded-plan scope. It
is offered when the saved revision has no open questions, its filter
acknowledgement is recorded, its input hashes still match the files,
and the current server execution mode is `apptainer-demonstration`.
The saved journey field `gpu_execution=disabled` is historical and is
not that mode. A server that is not in demonstration mode reports
`demonstration_not_configured` and does not change the plan. If the
inputs require a different plan, that revision stays unapproved until
a person approves it. Authorization does not launch.

Launch is a third human action. It rechecks the revision, digest, and
live input identities, then calls
`GenerationPlanService.launch_apptainer_demonstration`. The approved
plan field `gpu` is the nvidia-smi index as a decimal string. The launch
request `gpu_index` and `CUDA_VISIBLE_DEVICES` use that same integer.
A different index is refused before a launch is allocated or consumed.
A missing authorization, a stale revision, changed inputs, or a second
launch returns before that call. The running record is saved before the
launcher reply. If that reply is not saved, `POST
/api/design-journeys/{journey_id}/execution-reconciliation` reads the
saved attempt directory. BindCraft output for this launcher is
`<attempt_directory>/<attempt_id>/designs`. A budget ledger with one
finished reservation, header-only stat CSVs, and failure counters is a
terminal budget exhaustion without an accepted candidate. Budget
compliance stays separate from scientific validation. The procedure
does not launch, scan other directories, or rewrite the raw files. The
journey stores the authorization, the launch identity, and the link
among journey, attempt, and candidate.
Budget exhaustion without an accepted candidate is a terminal outcome
and is not successful candidate generation. An unresolved launch stays
running across restart and is not replayed or reported as completed.

Each journey presents one card per round. A journey without stored
rounds is shown as one card and is not rewritten by that read. `POST
/api/design-journeys/{journey_id}/rounds` with `parent_round_id` freezes
that round, including its plan revisions, recorded review, execution
authorization, launch identity, attempt, evidence, and outcome, then
opens one child round. The child copies the parent parameters as
editable proposals sourced from the previous round. It has no approval,
authorization, consumed launch, or execution. A repeated submission for
a parent that already has an open child returns the saved journey and
does not draft again. Cancelling that child records `cancelled` and a
timestamp, keeps the round and its parent link, and does not change the
parent's plan, approval, or execution. A cancelled round cannot be
approved, authorized, or launched. A consumed launch cannot be cancelled
through this action. After cancellation, preparing again from the parent
opens one replacement and keeps the cancelled child. Another open child
is not created. The child does not launch. The technical demonstration
profile does not assign a host GPU. A new plan must record an explicit
GPU index and be reviewed and authorized on its own. The approved plan,
the launch request, and the runtime device must use that same index.
The profile does not choose or substitute a GPU. An unselected
assignment stays unselected. The copied trajectory budget is not
increased. Historical round 1 keeps its recorded approved GPU and its
recorded launch GPU.

The journey allocates `attempt.` plus 8 characters, which is the
identifier `_safe_attempt_directory` accepts. The same value is the
attempt directory name, the launcher request, the persisted outcome,
and the result link. `attempt.7d818cba2a1f` was allocated by an earlier
journey build and echoed by a CPU launcher double. The real launcher
rejects that 12-character form before it creates a directory.

The conversation process reads its execution mode and stores when it
starts. The default mode is `disabled`, and that process does not
construct the demonstration launcher. A launch is then refused.
`CELLERTZ_SCIENTIFIC_RUN_EXECUTION=apptainer-demonstration` constructs
the launcher in a new process and leaves scientific execution disabled.
These values are also read only at process start:
`CELLERTZ_JOURNEY_ATTEMPT_ROOT`, `CELLERTZ_PDL1_INPUT_ROOT`,
`CELLERTZ_APPTAINER_DEMONSTRATION_ROOT`, and the sqlite paths for the
generation-plan store and the design-journey store. Changing them in a
shell does not change a server that is already running. A restart is
required before that process uses the new values.

On a real launch, after the existing approval, input, deadline, and
budget gates, the launcher reads GPU occupancy and free memory before
the runtime. `launch_ready` requires the selected index to be present.
A compute process on that index is recorded and does not refuse the
launch. A missing or unreadable index returns before the runtime is
invoked. The query does not reserve VRAM and does not signal compute
processes. CPU-double and
inert-runner tests do not query the host GPU, do not read the host SIF,
and do not verify the script embedded in that SIF. The bootstrap still
refuses a BindCraft script whose SHA-256 differs from the pinned source.

Scoring, ranking, critique, and prioritization stay blocked until
their own evidence exists. BindCraft `i_pTM` is not ipSAE.
`POST /api/design-journeys/{journey_id}/rounds/{round_id}/specialist-progress`
reads the saved execution for that round and writes stage events.
GET does not call it. A terminal execution with no accepted candidate
records scoring, ranking, critique, and prioritization as blocked and
does not invoke those specialists. ProteinGenerationAgent stays
unexecuted; the producer remains
`GenerationPlanService.launch_apptainer_demonstration`. An accepted
candidate is scored only when the saved attempt contains the relaxed
PDB and PAE pair that `BindCraftArtifactResolver` requires. That path
uses `BindCraftIpSAEEvidenceProvider` and the existing
`BINDCRAFT_IPSAE_CONFIG`. It does not use fixture structural metrics.
Ranking, critique, and prioritization then run as the existing
deterministic specialists. A missing artifact, a failed score, or an
interrupted running stage is stored and is not replayed. Hermes and
the Second Brain are not called. `attempt.c5c1f13e` is not attached
to another round.

`python -m cellertz.application.recovered_ipsae_acceptance` is a
separate post-hoc read of that recovered attempt's designs directory.
It uses `BindCraftArtifactResolver`, `BindCraftIpSAEEvidenceProvider`,
and `BINDCRAFT_IPSAE_CONFIG`. It does not launch generation. Before
any score, it checks the PAE `candidate_id`, `model_number`, file
hashes, float32 matrix, and PDB CA file order. The calculator indexes
PAE row `i` to the `i`-th CA atom. The accepted order is one contiguous
chain A block followed by one contiguous chain B block whose length is
the binder length in the design name. A matching filename and matrix
size do not pass that check. The NPZ does not store a residue index, so
any other CA order is a blocker and is not scored. The representative
value remains the mean of the per-model raw ipSAE scores. The command
writes a new report outside the attempt directory. It does not rewrite
`review.json`, change raw artifacts, or clear
`execution_constraints.unmet`. A CPU test of the command is not the
host score. Scientific validation remains unestablished until that
host command has run.

A consumed launch cannot be revised through Complete this plan. The
change belongs to a new round. Recorded review stays completed after
execution authorization. A GPU value copied from an earlier round is
historical and is not that round's approved GPU. A completed round
does not ask for another GPU index.

## New-round acceptance

This procedure is for a new round. It does not rewrite Round 8 and
does not attach `attempt.c5c1f13e`.

1. Submit a new design request and save the immutable plan revision.
2. Review that exact revision. Recorded review is not authorization.
3. Authorize execution explicitly. Authorization does not launch.
4. Launch once, on an approved GPU index that is present and free.
   The launch keeps the input, deadline, source-pin, and
   attempted-trajectory-budget gates. The HTTP request saves the
   attempt identity and a queued post-generation dispatch, then
   returns. It does not wait for the GPU process. The materialized
   bootstrap captures each model PAE and, in that same call, records
   the residue order from the prediction model's `aatype`,
   `residue_index`, and chain lengths. It writes
   `{design}_modelN_residue_order.json` from that snapshot. It does
   not rebuild the order from the relaxed PDB, and it does not
   calculate ipSAE. Relaxation keeps the pose residue sequence.
   Scoring accepts the relaxed PDB only when its CA identity matches
   the captured list and both file hashes match the sidecar.
5. The journey worker continues that new dispatch after the launcher
   returns a terminal outcome. A saved budget-exhausted launch blocks
   scoring, ranking, critique, and prioritization. A verified launch
   is accepted only when the saved review, both models, the ledger,
   and the capture-time sidecars agree. Missing or contradictory
   evidence stays unresolved and does not start generation again.
   Rounds saved before this dispatch existed stay manual. The scoring
   audit stays a separate operator action. ProteinGenerationAgent is
   not the launcher.
6. An accepted candidate is normalized by ProteinGenerationAgent
   through `ExistingBindCraftArtifactsExecutor`. That read does not
   launch again and does not use fixture generation. Scoring then
   requires the relaxed PDB, the matching PAE, and residue-order
   evidence. Ranking, critique, and prioritization follow only after
   a raw ipSAE `ranking_score`. Each stage records its own start and
   completion. A publication failure stays visible beside a completed
   score.
7. Inspect the round card. It separates generation status from
   post-processing status. The page reads the saved journey while
   work is queued or running and stops when the dispatch is terminal.
   Opening the page does not launch or score. The card shows
   implementation, sequence, model paths, limitations, and
   `scientific_validation_established: false`.
8. Results reads `CELLERTZ_ATTEMPT_CATALOG/<attempt_id>/`. Specialist
   progress publishes that attempt only after scoring records a raw
   ipSAE. It writes each `modelN.pdb` first and `review.json` last.
   A repeat returns the same publication. A different file already at
   the destination is refused. The inspection and structure routes
   then resolve that attempt id and its exact model ids. A scoring
   double is not copied into the review as ipSAE, and publication
   does not set scientific validation.

The procedure still cannot finish on the current host until the
updated bootstrap is loaded and a new authorized launch both accepts
a candidate and retains the residue-order file. Round 8 has no
accepted candidate, so it stops at step 5 and is not published.
`attempt.c5c1f13e` is not published by this path. An inert launcher
writes stats without that PAE evidence, so it cannot pass step 6.
CPU tests of a scoring double are not that host score. Boltz2
ensemble evidence is still absent and is not a ranking signal.

Rounds 8 and 9 used bootstrap seed 1 and `max_trajectories` 1. Each
round attempted one trajectory, classified it LowConfidence, and
stopped because that reservation consumed the budget. `--cellertz-seed`
is `np.random.seed` in the container process. BindCraft then draws the
trajectory seed with `np.random.randint(0, high=999999)`. Those two
integers are not the same value, and the bootstrap does not seed JAX.
Repeating seed 1 repeats that NumPy stream. It does not label the
trajectory low confidence by itself.

A later round can record a different bootstrap seed, from 1 through
4294967295, and a verified `max_trajectories` from 1 through 4.
`num_seqs` and `max_mpnn_sequences` stay 1. The registered advanced
profile is the source of the budget. The plan must match it. A new
revision drops the previous execution authorization. The suggested
next round is seed 2 and `max_trajectories` 4, with every other
advanced setting left as registered. Four is a ceiling, not a promise
of an accepted candidate. A rejected trajectory still consumes one
reservation, and nothing retries or launches on its own.

Specialist evidence cites the plan revision and digest saved on the
execution. A child round revised before authorization and launch
records that later identity. If the round snapshot still names a
different plan id, revision, or digest, specialist progress is refused
before any specialist runs. Evidence already stored for an attempt is
not rewritten. Round 9's recorded specialist events still cite the
snapshot from when that round was prepared: revision 1, digest
`0c13573d8ef08391cddf7c62ea60031ef2c30d2f52ef29f64355c0860ae2f0d7`.
The approved and executed plan is revision 2, digest
`c9b4a395b93fa1ea2ac59616bbdeba70b362d29b20a067e93c9059626b90025a`.

Results inspection reads the catalog review and, separately, the
configured design-journey store. A persisted raw ipSAE is shown only
when the saved execution and specialist record match the attempt, the
BindCraft candidate id, the executed plan revision and digest, and
both model hashes. The review file stays unchanged, so its recorded
metrics and `ranking_score` remain the original generation review.
Attempted-trajectory-budget compliance does not establish overall
execution compliance. A missing journey record is not a violation.
Coordinator traces stay question records. A trace without a question
is not shown as valid. Reloading Results does not score, publish, or
write the journey database. The running host shows this projection
only after these files are loaded and the process is restarted.

A launcher status of `verified` does not by itself accept a candidate.
The saved review, both model structures, their binder sequences, the
capture-time PAE and residue-order sidecars, the one final-design row,
and the trajectory ledger have to agree. Round 10 returned `verified`
for `attempt.96138b26` and the files identify `PDL1_l65_s875688_mpnn1`,
while the journey stored `accepted_candidate=false`. That projection
and its no-candidate specialist events stay in an explicit correction
record. Recovery binds the corrected execution to plan
`plan_e48d2b2dde43412c8666c06c0f9be2c8` revision 4, digest
`bb3c0f1a507b4a38d2a5a9cb9770012830124b5425ec0aa8b4097e6a84d9877e`.
It does not launch. A later specialist-progress call can then normalize,
score, rank, critique, prioritize, and publish. A repeat of either
request does not replay that work.

A later scoring record stores the calculator's per-model ipSAE, both
directional components, the existing within-model max and across-model
mean, the effective protocol, artifact hashes, and the calculation
timestamps. A saved record without that evidence stays `not_recorded`.
An explicit scoring audit recalculates from the saved artifacts and
keeps the original ranking_score. It does not replace ranking, critique,
prioritization, or the published review. Absolute tolerance `1e-9` is
only the numerical comparison used to say whether the two values agree.

## Orchestration

A new authorized launch is still a separate human action from recorded
review and from execution authorization. The launch request commits the
attempt identity and a queued automatic dispatch, then returns. It does
not wait for the GPU process. A single journey worker, separate from the
approved-run worker and the scientific-run worker, claims that dispatch
inside the journey-store transaction and calls the existing launcher once.

The claim is not exactly-once because of an in-process lock. Two
processes can serialize only through the store transaction. A crash after
the claim is saved and before the launcher returns does not start the GPU
again. If the attempt is not terminal, recovery marks that dispatch
unresolved. A queued dispatch that was never claimed can be claimed after
restart. A running specialist stage is marked unresolved and is not
replayed. Rounds saved before this dispatch existed are not processed.
The scoring audit remains an explicit operator action.

Generation status and post-processing status are separate. A finished
GPU job can still have scoring or publication in progress. A publication
failure stays visible and does not remove a completed score. Scientific
and experimental validation stay false. The professor page polls the
saved journey while the dispatch is queued or running, or while a
specialist stage is running. The poll is a GET. Leaving the page clears
it. Opening the page does not launch or score.

## Later slices

The recovered attempt has a CPU acceptance command, and that command
has not been run on the host artifacts. Until it exits successfully,
there is no recorded ipSAE for `attempt.c5c1f13e`. A professor-authorized
round whose accepted candidate has its own relaxed PDB and PAE is still
a separate path. CPU tests and scoring doubles do not establish either
score. Boltz2 ensemble evidence is still absent and is not a ranking
signal. A real GPU launch remains a separate operator decision.
