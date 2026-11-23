# The atlas procedure, step by step

What the professor actually did, read from the artifact and its data files
rather than from a description of it. Sources: `index.html` and
`targets_data.js` (9 targets, 76 blocks) and `bcma_panel_data.js` (the ranked
panel) of the Engager Target Atlas.

Every number and every vocabulary below is his. Where this platform differs,
that is marked as ours.

---

## The unit of work is a complex, not a target

Each of the nine entries is a **source complex**: a target chain plus its
clinical binder or native ligand, and sometimes a third chain kept only as
context. Nothing in the atlas reasons about a bare target.

| target | PDB | target chain | partner | context | partner is |
| --- | --- | --- | --- | --- | --- |
| CD19 | 7URV | C | D | — | FMC63 scFv |
| CD22 | 7O52 | U | H, L | — | m971 Fab |
| TACI | 1XU1 | S | A, B, D | — | APRIL trimer |
| BCMA | 1XU2 | S | A, B, D | — | APRIL trimer |
| CD3ε | 1XIW | A | C, D | **B** | UCHT1 Fv |
| EGFR | 1YY9 | A | C, D | — | Cetuximab Fab |
| HSA | 8Z8V | A | B | — | ALB8 VHH (ozoralizumab) |
| CD16a | 5VU0 | A | B, C | — | IgG1 Fc |
| GPC3 | 9NTQ | A | B | **U** | SAR444200-programme binder |

The partner is the measuring instrument for every later step: the epitope to
match, or the surface to avoid.

---

## Step 1 — The agent proposes blocks

`hotspot_agent` output, dated 22 Sep 2026. Ten blocks per target for seven of
the nine; CD16a has four and GPC3 two.

Each block carries, from the agent:

| field | example |
| --- | --- |
| `label`, `lo`, `hi`, `n` | `TRP632-HIS640`, 632, 640, 9 |
| `priority` | 1, 2 or 3 (11 / 33 / 32 blocks) |
| `sel` | HIGH, MODERATE, LOW (45 / 19 / 12) |
| `evidence` | "SASA: TRP632(109), HIS640(137), PRO638(115). Loop/coil. Within known m971 footprint (631-642 overlap). Aromatic/His rich." |
| `rationale` | why this patch |
| `selectivity_text` | what it would and would not discriminate |
| `risks` | "OVERLAPPING with FMC63 — will compete for same epitope. Not orthogonal. CAR-T escape risk if selected as sole target." |

The evidence is per-residue SASA, secondary structure, known interface
contacts and PFAM domain boundaries. It is a structural argument, not a
citation.

---

## Step 2 — The guard returns a verdict

`hotspot_guard.py` at `max_block 6`, `max_total 40`. Its output per target is
a `verdict` record, and its real shape is richer than a pass/fail:

```json
"verdict": {
  "proposed": 100,
  "exclusions": [],
  "forbidden": [38, 97],
  "oversized": [[23,39,17], [54,65,12], [86,98,13], [116,131,16],
                [157,172,16], [174,185,12], [203,209,7], [217,223,7]],
  "total_violation": [100, 40],
  "guarded_written": true
}
```

- `proposed` — unique residues the agent asked for
- `oversized` — every block over `max_block`, as `[lo, hi, n]`
- `total_violation` — `[proposed, limit]` when the budget is blown
- `forbidden` — residues the guard refuses outright
- `exclusions` — residues excluded on **clinical** grounds
- `guarded_written` — whether a guarded version was emitted

Across the nine: CD19 blew the budget 100 against 40 with 8 oversized blocks;
CD22 124 against 40 with 5; TACI, BCMA, CD3ε and GPC3 stayed inside it.
`guarded_written` is true for six, false for CD3ε and CD16a, absent for GPC3 —
so emitting a guarded version was a decision, not an automatic consequence.

**`exclusions` is where clinical knowledge enters.** BCMA's is `[27]`, and the
atlas's own note on its clinical field says: *"R27P has been reported as an
escape mutation under teclistamab pressure — residue 27 is exactly what the
guard caught here."* The guard is not only a size check.

> Correction to our implementation: `plan_configuration_suggestion.hotspot_guard`
> reports `max_block`, `max_total`, `total_residues`, `over_block_limit` and
> `over_total_limit`. It has no `forbidden`, no `exclusions`, does not report
> oversized blocks as `[lo, hi, n]`, and has no notion of writing a guarded
> version. The limits are right; the shape is a subset.

---

## Step 3 — Classify every block against the partner, before any run

This is the step this platform does not have at all, and it is cheap.

Each block gets an `overlap` verdict and a measured contact count against the
partner chains, at heavy atom ≤ 5 Å — **computed on the structure, before a
single trajectory**:

| verdict | blocks | meaning |
| --- | --- | --- |
| `OVERLAPPING` | 41 | inside the partner's footprint |
| `ADJACENT` | 14 | beside it |
| `ORTHOGONAL` | 19 | elsewhere on the molecule |
| `ON-TARGET` | 2 | GPC3 only, where the partner *is* the goal |

With `contact_n` and `contact_pct` of the block's own residues that touch the
partner. CD22's ten blocks, measured:

| block | range | n | overlap | touches partner |
| --- | --- | --- | --- | --- |
| E | GLN635-SER641 | 7 | OVERLAPPING | 7 / 100% |
| D | TRP632-HIS640 | 9 | OVERLAPPING | 8 / 89% |
| F | VAL624-ASP631 | 8 | OVERLAPPING | 6 / 75% |
| G | ARG645-GLN652 | 8 | OVERLAPPING | 6 / 75% |
| J | GLU608-PRO623 | 16 | ADJACENT | 2 / 12% |
| A | ASP507-LEU516 | 10 | ADJACENT | 0 |
| C | ARG548-ARG597 | **50** | ADJACENT | 0 |
| B, H, I | — | 21, 3, 13 | ORTHOGONAL | 0 |

### And the direction is declared per target

A `contact_good` flag says whether touching the partner is what the programme
wants. It is **not** a constant:

| direction | targets |
| --- | --- |
| `good: false` — touching the partner is the failure | CD19, CD22, TACI, BCMA, CD3ε, EGFR, **CD16a** ("contact IgG1 Fc — native ligand") |
| `good: true` — touching the partner is the goal | **HSA** ("contact ALB8 — validated epitope"), **GPC3** ("touch the 9NTQ binder") |

Seven to two. This is the same decision our developability screen already
takes — occlusion is measured always and gated only against a declared limit —
but here it is declared per target, up front, and it reverses the meaning of
every number in step 3.

### Built 21 Nov 2026 — and recomputing it corrected two things

`partner_classification.partner_contact_residues` measures the contact and
`classify_blocks` attaches it to each declared block. Recomputed from the nine
complexes the artifact embeds, **72 of its 76 blocks reproduce exactly** at
heavy atom ≤ 5 Å. The four that do not are inconsistent in the artifact itself:
their own `contact_pct` does not equal `contact_n`/`n` either — cd16a block C
states 7 of 10 is 50%, gpc3 block B states 5 of 12 is 100% — so that is its
arithmetic rather than another cutoff. 4.5 Å misses nine blocks, 5.5 Å ten.
Water is excluded on both sides: two bridging `HOH` residues put the m971
footprint on 7O52 at 28 residues, and dropping them gives the 26 the artifact
states.

**The `overlap` verdict is not a function of its own contact measurement.** Its
ORTHOGONAL blocks run from 0% to 100% contact, 13 of its OVERLAPPING blocks sit
at 100% but 2 sit at 0%, and its ADJACENT blocks span the whole range:

| claim | at 0% | in between | at 100% |
| --- | --- | --- | --- |
| `ORTHOGONAL` | 16 | 2 | 1 |
| `ADJACENT` | 8 | 5 | 1 |
| `OVERLAPPING` | 2 | 26 | 13 |
| `ON-TARGET` | 0 | 1 | 1 |

So the four words are an agent's claim, and three of its blocks claim to be
orthogonal to a partner they touch. This platform therefore **derives** the
verdict from the measurement instead of asking the agent for it — which is also
what the Hotspot agent's existing governance already requires, since it is
forbidden from stating "how much of a partner epitope a block covers". The cut
between ADJACENT and OVERLAPPING is `EDGE_CONTACT_PCT = 50` and is **ours**: the
artifact's own words track no percentage, so there is nothing to inherit.
`ON-TARGET` is the direction's word, not a geometry — it appears on both of its
`contact_good` targets and on none of the other seven.

**And this resolves the reconciliation left open on 21 Nov.** `n == hi - lo + 1`
for all 76 blocks: the displayed blocks are **contiguous ranges**, not the
guarded residue picks. That is why CD19's blocks sum to 106 residues against
`proposed: 100` and why one block reaches 50. Our blocks are explicit residue
sets that survived hotspot validation, so `classify_blocks` counts the block's
own residues and never its span — counting the span would count residues the
plan never declared.

**Where the complex comes from.** Both input roots on this server hold a single
prepared chain, so the partner is not in the file the campaign runs on. It is
read from `complex.pdb` beside `target.pdb` and is deliberately **not** a role
of the input identity: mBER never opens it, and adding it would move the
manifest digest of every registered root over a file no execution reads. The
complex is tied to the prepared target by residue number — a preparation only
ever removes, so the prepared numbers must all be present in the source's
target chain, and a complex that fails that is refused as a different protein.

---

## Step 4 — Campaigns as a series, one change each

Not one run. Nine campaigns against one epitope on albumin, ten on BCMA, four
on GPC3, twelve audited in all. Each campaign records four things: **what
changed, why, what happened, what it taught.**

The albumin progression is the cleanest:

| | the one change | ALB8 epitope covered | enrichment |
| --- | --- | --- | --- |
| B | target hand-cut to a contiguous 290-390 window | **1 / 14** | **0.63× — below chance** |
| D | whole 573-residue molecule, mBER's own 25 Å truncator decides | 8 / 14 | 8.39× |
| F | seeded on the one D design that touched arm 2, CDR3 held | 12 / 14 | 9.75× |
| G | keep F's CDR1 and CDR2, **free** CDR3 | **13 / 14** | — |

Campaign B's designs all bound the artificial face the cut exposed: 100% clash
with intact albumin, 82% on the FcRn footprint. Campaign D's fix needed
`XLA_PYTHON_CLIENT_PREALLOCATE=false` — the OOM had been misread as the
protein being too big.

> **Never hand-truncate on sequence. mBER already truncates on distance.**

Of ALB8's 15 epitope residues, 319 is buried at relative SASA 0.036, so 14 is
the ceiling — the reachability rule appears here too.

### The two levers, and the seven refutations

Hotspot lists do not steer the optimiser. Seven independent instances:
BCMA's no-hotspot control (12.5 APRIL residues against 12.4 for the directed
run), albumin's arm-2-only run (26 of 26 designs on arm 1), CD19 campaign C
(median 0 of 13), CD3ε campaign B (0 of 10 after its block was moved 10 Å
clear), GPC3's first pass, and GPC3 again. The cleanest is CD19 **K1 vs K4**:
an identical 19-residue list, same target, same scaffold, differing only in
the seed — K1 reaches 16 of 19 with 100% of designs on-hotspot, K4 reaches
**0**, nearest approach 11.6 Å.

CD16a is the apparent exception at a median 7 of 9, and the atlas disposes of
it: all nine declared hotspots already lay inside the IgG1 Fc interface, where
the optimiser was going anyway. *"Not evidence that hotspot steering works —
evidence that steering succeeds when it asks for where the model was already
going."*

What moves designs is **target preparation** and **seeding on a prior design**.

---

## Step 5 — Audit the run, against a denominator

Re-audited 6 Nov 2026, and the conclusions changed.

**Reachability.** A buried hotspot leaves the denominator. GPC3's ILE215 sits
at relative SASA 0.018; dropping it moved the mode from 8 of 9 to 7 of 8 and
surfaced a new rank 1 the old cut had discarded. ASN210 at 0.593 is exposed,
reachable, and reached by **1 design of 750** — the real miss.

**Chance.** 400 random size-matched contiguous surface patches per design.

| GPC3 campaign | n | hotspot median | recall | null | enrichment | designs clearing own null |
| --- | --- | --- | --- | --- | --- | --- |
| A — unseeded | 150 | 3 | 0.375 | 0.116 | 2.95× | **8.7%** |
| B — seeded on 6767415 | 200 | 7 | 0.875 | 0.097 | 9.00× | 88.0% |
| C — seeded on 2604812 | 200 | 7 | 0.875 | 0.100 | 8.75× | 95.0% |
| D — seeded on 6767415 b-1 | 200 | 7 | 0.875 | 0.097 | 9.00× | 82.0% |

All four declared the **same nine hotspots** and used the same target file.
Enrichment is only informative when the declared list is a small share of the
surface — the nine cover 3.2% of GPC3's 250 accessible residues; on **TACI**,
44% of a 34-residue surface returns 1.86 and says nothing.

**Per-hotspot engagement**, not just a median: GPC3's 318 at 98.8%, 322 at
94.4%, 325 at 92.3%, then 214/218/326/329 at 82-86%, and 210 at 0.1%.

---

## Step 6 — Pre-filter, then rank on two axes

The BCMA funnel, 11,430 accepted designs across ten campaigns:

| | remaining |
| --- | --- |
| accepted designs | 11,430 |
| no unpaired Cys, no N-glycosylation sequon | 7,540 |
| APRIL footprint ≤ 8 of 18 | 842 |
| net charge ≤ +2 | 754 |
| top composite, one per trajectory | **30** |

34% carry a free cysteine or an N-X-S/T sequon, and *"neither appears in any
in-silico filter the campaigns ran."*

Then: clear `i_ptm ≥ 0.80` (242 of 457 scored designs), and give each design a
percentile on two axes — i_pTM, and a **z-scored weighted composite that
excludes it**: J_clin 1.2, buried area 1.0, net charge −0.7, liabilities
−0.3 / −0.3 / −0.2. Rank on the **worse of the two percentiles**. The axes are
nearly independent (Spearman ρ = +0.155), which is why a blended sum was wrong.

The panel data confirms the mechanics on every row: `qs` (the composite) runs
−1.71 to +2.14, the range of a z-scored sum, and `m` is exactly
`min(pi, pq)`.

Diversity: no two entries share a trajectory, and none exceeds 0.85 epitope
Jaccard **together with** 0.85 sequence identity against an entry already
chosen.

### The ordering is not the same on every target

| | BCMA panel | GPC3 panel |
| --- | --- | --- |
| i_pTM | gate at ≥ 0.80, then one of two axes | **tie-break only, no gate** |
| why | Chow et al. rank by i_pTM before selection | pooled Spearman **−0.199** against hotspot coverage |

On GPC3 the off-epitope designs score *higher*, and the best i_pTM in the set
(0.8675) binds the wrong face at 3 of 8. *"Applied first it selects exactly
the designs you do not want."* The effect is entirely between campaigns;
within one arm the correlation is zero, which is the mBER preprint's own caveat
that it ranks within a campaign aimed at one site.

GPC3's composite is a third weight set: buried area 1.3, interface hydrogen
bonds 1.0, charge −0.7, same liabilities, no J_clin.

---

## Step 7 — Anchor to a clinical agent

Each target carries a `drugs` record: approved agents, modality, status, and
what each one implies. BCMA's four include the note that ties `exclusions:
[27]` to teclistamab escape, and cilta-cel — *"the closest precedent for this
programme: a clinical product built from two VHH domains against this same
surface"* — whose two nanobodies are 8HXQ and 8HXR and are the J_clin
reference.

The anchor is what makes the ranking a statement: both cilta-cel nanobodies
put three quarters of their epitope on the APRIL footprint, which is why
*avoiding* that footprint was the wrong criterion and an earlier version of
the panel built on it was wrong. And cilta-cel VHH2 is net charge **+3**, so
an approved therapy fails the ≤ +2 filter the panel applies — *"charge is a
graded risk heuristic, not a threshold."*

---


### Seeding the next campaign, 22 Nov 2026

The atlas's second lever, and the one this journey had no way to pull: on
albumin, seeding on a good design took the lineage from 2 of 14 to 13 of 14. A
child round carried the parent's *plan* and not the parent's *design*, so every
campaign in a series restarted from the bare framework and the series measured
nothing it was meant to measure. The domain logic had been written and nothing
in the application ever built a `SeedDesign`: it existed only in a test.

The chain is now parent design → round → launch → settings:

| where | what |
| --- | --- |
| `prepare_next_round` | reads the parent's accepted candidate onto the child as `seeded_from` |
| the launch | passes the round's own record down; `DemonstrationLaunch` carries it |
| the launcher | builds the `SeedDesign` and writes it into every trajectory's settings |
| the reply | records `seeded_from`, or `None`, so the execution says which it was |

mBER has no seeding flag — the mechanism is the masked sequence, with the
parent's residues held where the bare scaffold would have left a design
position open. What that does to the document the card reads:

```
spans reopened  : ((96, 109),)
scaffold open   : 41 positions
seeded open     : 14 positions
scaffold: SCAAS**********WFRQA ... YYC**************WGQ
seeded  : SCAASGGGGGGGGGGWFRQA ... YYC**************WGQ
```

**Which loop is reopened is ours**, like the classification's edge cut: the
last declared CDR region, CDR3 at 96-109 on this scaffold, which is what the
seeding test in this repository reopens. Reopening *every* CDR is not seeding
at all — the parent's residues sit only in design positions, so opening all of
them reproduces the bare scaffold exactly. That was the first version of this
code, and the equality check that now refuses a seed reproducing the bare
scaffold is what caught it.

A seed that cannot be reseated is **refused with the other pre-flight checks**,
never dropped: falling back to the bare framework would run a different
experiment from the one the round was opened for, and the series exists to
measure precisely that difference. A parent that accepted nothing leaves the
child unseeded, which is not an error but a fresh start.

What the approval still does not cover: the seed is journey state, not plan
state, so the reviewer signs the plan digest and the round's seed rides beside
it — the same gap as the verdict in step 3.

---


### The lesson a round declares, 22 Nov 2026

The series changes one thing per round, and the change is only a finding if the
round says what it is responding to. Nothing recorded that: a child round
carried its parent's identity and, from today, its design, but no statement of
why it differs.

The statement is the professor's — nothing infers it — and it is recorded
**beside the parent's own measured outcome**, so it can be read against what
the parent produced rather than against a memory of it:

```json
{
  "declared": "O painel colocou CDR3 fora do footprint do m971; reabrir CDR3 sozinha.",
  "status": "declared",
  "parent_round_id": "round_j_1",
  "learned_from": {
    "status": "ranked", "attempt_id": "attempt.a1b2", "accepted_candidate": true,
    "design": "t1_binder_1", "scoring_protocol": "ENGAGER_TWO_AXIS_2026",
    "ranked_designs": 2
  }
}
```

It is always a record and never absent: a round that declares nothing writes
`not_declared`, and a parent that never ran writes `no_execution_recorded`. The
scoring protocol travels with it because a lesson about ranking is unreadable
without the protocol that produced the numbers.

A lesson the route cannot record — not a string, blank, over 500 characters, or
carrying control characters — is **refused** rather than trimmed away, because
trimming would file `not_declared` against text the person did write. It is not
required: opening a child round without one still works, which is what keeps
this a declaration rather than a form.

---


### What the front collects, and what it cannot reach, 22 Nov 2026

**The direction is a closed choice with no default.** The request form asked
for it in a free-text box; it is now two radios — avoid the partner, compete
with it — and neither is pre-selected, because it reverses the meaning of every
contact percentage and cannot be inferred from the target. The server already
refused any word but `compete` and `avoid`, so a typo was never silent; the
choice shows the vocabulary and spares the round trip. The value sent is
exactly the word `request_draft` accepts, checked against the real draft code:
`avoid` and `compete` enter, `yes` and an empty answer are refused.

**The lesson has an input that nothing mounts.** The box sits in
`DesignJourney`, beside "Prepare next round", with the 500-character limit the
route enforces, sent only when written. Child cards show what they start from
and the lesson beside what the parent measured. All of it is tested. But
`DesignJourney` is mounted only by `LegacyWorkspace`, which the exported `App`
never renders: the shipped front is `ProteinDesignModule` — request, plan,
results — and its single run action posts to `/api/design-journeys/run`. It
never calls `/rounds`. The production bundle confirms it: none of
`DesignJourney`'s text is in it, the deployed one included.

So today the live front cannot open a child round at all, and the seeding and
the lesson that the backend now records have no way in from the screen the
professor uses. Making them reachable is not a field: a child round is a new
plan under its own conversation id, and the live plan view selects by request.
That integration is the open piece.

---

## What this platform has, against those seven steps

| step | state |
| --- | --- |
| 0 · source complex, target + partner + context | extracted into the draft ✅ (21 Nov); the complex itself is read from `complex.pdb`, which neither root holds yet |
| 1 · agent proposes blocks | `hotspot_identification_agent.suggestion` ✅ |
| 2 · guard verdict | limits ✅; `forbidden`, `exclusions`, oversized triples, guarded output ❌ |
| 3 · classify blocks against the partner, with a declared direction | built ✅ (21 Nov) — `partner_classification`, reproducing 72 of the atlas's 76 blocks |
| 4 · campaign series, one change each | rounds with parent/child lineage ✅; seeding on a parent design ✅ (22 Nov); a lesson field ✅ (22 Nov) |
| 5 · audit with reachability and a null model | built ✅, and inside `audit_hotspot_blocks` |
| 6 · pre-filters, then two-axis rank | built ✅; injected ✅ (22 Nov); a journey round reaches it end to end ✅ (22 Nov) |
| 7 · clinical anchor | **nothing** — no `drugs` record, and J_clin has to be supplied by hand |

Steps 0 and 3 were built on 21 Nov 2026. What is left on them is operational
rather than code: the PD-L1 input root still holds no `complex.pdb` (the CD22
root does, since 22 Nov), so its blocks report why they were not measured
rather than a verdict. The direction is collected as of 22 Nov (below).

### Step 6, injected 22 Nov 2026 — and the cohort it needs was being thrown away

The silent corruption is closed: the server injects a scorer, and a score is
only recorded under a protocol this journey names. ipSAE stays BindCraft's,
replayed from saved models; an mBER execution is scored from the atlas's
two-axis panel. A result under any third protocol fails the stage with the
reason the scorer gave instead of being stored.

The score is **read, never recomputed**. `rank_mber_output` already screens and
ranks on the GPU host, and both axes are cohort statistics — a percentile only
exists against the designs it was computed with. Scoring one design again here
would need the whole cohort or would invent a number. So the panel row is the
score, and its provenance carries the cohort it came from (generated, screened
in, ranked, rejected by the screen, rejected by the gate, not measurable),
without which the number is unreadable.

That panel was being **computed and discarded**: `_execution_from_reply` copies
a fixed list of keys from the launcher's reply and `ranking` was not among
them. The launch did the atlas's step 6 on the GPU and the journey kept none
of it, which is why ipSAE was the only thing left to fall back on.

### The accepted candidate, declared 22 Nov 2026

An mBER launch returned `status: "ranked"` and never set `accepted_candidate`
or `candidate_identity` — only the BindCraft path did, through
`read_accepted_launch_evidence` on `status: "verified"` — so a run that
screened and ranked a real cohort arrived as "no accepted candidate" and no
specialist ran.

The panel now declares one. It is a **set**, not a winner: with no `select` the
diverse selection keeps every design that is neither a trajectory repeat nor a
near duplicate, while a round carries one candidate. The declaration is
therefore **rank 1 among the selected**, the panel's own ordering and not a new
one, and only a design the panel selected can be it.

Being in the panel is the acceptance evidence. A design reaches it only after
its structure was found, its sequence was read off that structure and matched
`accepted.csv`, the atlas's pre-filters passed, and i_pTM was recorded and
cleared the gate. `rank_mber_output` emits the identity and the rule; the
launch adds the attempt id, because the panel does not know which attempt read
the output directory. `successful_candidate_generation` now accepts `ranked`
beside `verified` — both are terminal statuses a launcher had to earn, and a
claim on any other status is still refused.

### Generation and evidence, routed 22 Nov 2026 — the round completes

The two stages before scoring were BindCraft-shaped: generation read
BindCraft's design stats, and the evidence gate wanted a relaxed PDB and a PAE
matrix per predicted model, matched to the capture-time residue order. mBER
writes none of that, so every round stopped at generation with a ranked cohort
sitting on disk.

Both now route on the same signal the scorer uses — an execution carrying a
`ranking` was screened and ranked by its launch:

| stage | BindCraft | mBER |
| --- | --- | --- |
| generation | design stats → `ProteinGenerationAgent` | `accepted.csv` → `MBERParser` |
| evidence | relaxed PDB + PAE + residue order, per model | the one complex structure, with its digest |

The saved identity is trusted no more than BindCraft's: the candidate is parsed
back out of the cohort's own bytes, and a declared sequence that disagrees with
`accepted.csv` is refused — the identity and the artifacts would be describing
different molecules. The evidence row is `model_id: "complex"`, not `model1`:
mBER accepts one refolded complex per design, and a model number would imply
an ensemble it never made. There is no residue-order sidecar to check because
there is no PAE whose index order could disagree with one.

Driven end to end on a real cohort, no stubbing:

```
generation       completed   adapter=mber_adapter_v1 design=t1_binder_1
scoring          completed   signal=ENGAGER_TWO_AXIS_2026 score=0.5 source=rank_engager_designs
ranking          completed
critique         completed
prioritization   completed
progress        : recorded
```

The stage names what read the artifacts rather than a constant, so an mBER
design is not recorded as BindCraft's adapter with
`protein_generation_agent_executed: true`.

### The target comes from the plan, 22 Nov 2026

`demonstration_specification` was the PD-L1 demonstration's whatever the plan
asked for — target `PDL1`, structure 4ZQK, the PD-1 footprint as the epitope —
so a CD22 campaign recorded PDL1 on its accepted candidate and measured J_clin
against the wrong surface. Not incomplete: false.

`declared_target` now reads the campaign off the approved plan, and **the
epitope is measured**: the partner's footprint on the target, from the
`complex.pdb` beside the registered file, through the same
`partner_contact_residues` the block classification uses. So the surface the
ranking scores against is the one the plan was approved against. On the real
CD22 root that is **26 residues** — the professor's own number — against chains
H,L of 7O52 at 5 Å, and the specification says `structure_contact` with the
cutoff recorded rather than claiming a curation.

A footprint residue the prepared file no longer holds cannot be in the epitope,
so the declaration intersects the two and the note carries how many the
preparation removed.

PD-L1 is the one exception and **not** a fallback: that campaign's epitope is a
recorded operational reference (curated, 22 residues) and its root holds no
complex. It is accepted only when the registered file's own residues still
match that record by identity, so a plan cannot borrow it by writing the name.
Measured against both real roots:

| root | declares | result |
| --- | --- | --- |
| cd22-professor | CD22 + chains H,L | 26 residues, measured |
| cd22-professor | PDL1 | refused — not the recorded reference |
| pdl1-professor | PDL1 | 22 residues, recorded reference |
| pdl1-professor | CD22 + chains | refused — no complex to measure |

The frame check generalised with it: `numbering_offset(pdb, identities)` takes
the declared epitope's identities, so an offset into mBER's renumbered output
is still accepted only when every declared residue matches — it is just no
longer only PD-L1's that it will accept. A launch that cannot declare its
target is refused as `target_not_declared` **before** the GPU is touched.

---

## The journey we agreed to build

Decided 2026-11-21. This is the target shape; each step above lands in it.

```
intent (free text)
  -> analysis extracts: source complex + direction + clinical anchor
      -> hotspot agent proposes blocks              [exists]
          -> guard: verdict                          [built]
              -> CLASSIFICATION against the partner   <- the gap
                  -> human approval against the verdict, not a residue list
                      -> campaign 1 on the GPU
                          -> audit: reachability + null model    [built]
                              -> pre-filters -> two-axis rank    [built, not injected]
                                  -> lesson -> campaign N+1, seeded
```

### The intent box stays

It is load-bearing, not decoration. The atlas's own CD16a note — *"If a
non-competing engager was the goal, the hotspot block belongs near 136-143,
which no campaign has tried"* — is an intent statement deciding a hotspot
choice. Intent is what fixes the **direction**, which is step 3's declared
input.

### What the analysis extracts changes

`request_draft` today requires `target_id`, `objective`, `generator`,
`n_candidates`, with optional `length_min`, `length_max`, `seed`. Three of the
four required fields are runner parameters, and `generator` is asked in prose
and then validated against `registered_identity`, which already knows the
answer.

| today | should come from |
| --- | --- |
| `generator` | the installation (`registered_identity`) |
| `n_candidates` | the stopping policy |
| `length_min` / `length_max` | the scaffold |

| not asked today | the atlas needs it first |
| --- | --- |
| source complex: PDB, target chain, partner chains, context chains | step 0 |
| direction: compete with the partner, or avoid it | step 3 |
| clinical anchor | the J_clin reference, and `exclusions` |

**The fields stay in `GenerationRequest`.** It is an execution contract and it
appears in the episodic memory of runs already recorded. What changes is that
the user is no longer asked for them.

### Approval moves from a list to a verdict

Today the front renders each proposed block and ends with *"Contact with this
block is not measured yet."* That sentence is exactly the gap. After step 3 a
reviewer approves against a table:

| block | n | guard | overlap | touches partner | direction wants it |
| --- | --- | --- | --- | --- | --- |
| E | 7 | ok | OVERLAPPING | 7 / 100% | ✗ |
| J | 16 | oversized | ADJACENT | 2 / 12% | — |
| B | 21 | oversized | ORTHOGONAL | 0 | ✓ |

On CD19 that table would have shown, before any GPU time, that six of ten
blocks touch 0% of the partner and the other four are `OVERLAPPING` with
FMC63 — the approved CAR binder this engager must not compete with. The agent
already says so in its `risks` prose; the verdict gives the number.

### Rounds gain a seed and a lesson

The parent/child lineage already present **is** the campaign series. Two things
are missing from it:

- **the seed** — a child round copies plan *fields*, not a parent **design**.
  `seeded_masked_sequence` exists and nothing calls it. This is the lever that
  took albumin from 2/14 to 13/14 and GPC3 from 2.95x to 9x.
- **the lesson** — the atlas records four fields per campaign: what changed,
  why, what happened, what it taught. `follow_up_decision` routes on launcher
  status alone.

### Build order

1. ~~**Partner classification.**~~ Built 21 Nov 2026. The verdict is derived
   from the measurement rather than claimed by the agent, because the atlas's
   own four words do not track its own contact numbers. Outstanding: place
   `complex.pdb` in the PD-L1 root. The direction is collected in the front
   end as a closed choice, 22 Nov 2026.
2. ~~**The analysis extraction**~~ — built 21 Nov 2026.
3. ~~**Inject `specialist_scorer`**~~ — injected 22 Nov 2026. Outstanding, and
   upstream of it: an mBER launch declares no accepted candidate, so a journey
   round never reaches the scorer (below).
4. ~~**Seed and lesson on rounds**~~ — both 22 Nov 2026. Outstanding: a
   declaration of which loop a child round reopens (today the last CDR, ours),
   and the approval binding the seed and the lesson rather than the digest
   alone.
