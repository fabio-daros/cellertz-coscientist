# mRNA Design: architecture and build plan

Status: **proposal, updated 26 Nov 2026 with the maintainer's answers; five
questions remain (9.1).** Nothing
here is built. Written from the
professor's Fluc campaign artifact (claude.ai/artifact/4ruRozsPN9FytAHQffuFuo)
and the Work Plan v2, phase 01. Every number below was checked against the
artifact's own published data unless it says otherwise.

## 1. What the platform has to do

Take a protein (or gene) and a set of declared objectives, and return a short
list of mRNA designs a person can send to synthesis, **running the steps the
professor currently asks Claude to run one by one**, and trying again when a
round finds nothing. The plan's human review gates stay mandatory.

## 2. What the professor's pipeline actually is

It is the `codonscope` CLI, piped, over a fixed construct:

```
5'UTR (59 nt, HBB + Kozak) | CDS (1650 nt, FREE) | TAA | 3'UTR (266 nt, HBB tandem)     = 1978 nt
```

A mask (`-a "0-59,1709-1978"`) marks the fixed ends, so only the CDS is sampled.

| step | command / script | notes |
| --- | --- | --- |
| construct | `codonscope translate --reverse` | **appends no stop codon**; a `TAA` is added by hand |
| generate | `codonscope sample --generator G -a MASK N` | 8 strategies: greedy, beam 50/150, T = 0.01/0.25/0.5/1.0, `codon-usage` |
| evaluate | `codonscope evaluate -e naturalness -e half_life_h -e te_mean` | three axes, all maximised |
| local front | `codonscope filter` | per strategy; epsilon-approximate, while his global front below is exact |
| pool | his `pareto_with_strands_kozak.py` | dedupe by strand (2701 -> 2173), **global** Pareto |
| shortlist | his `select10_kozak.py` | 10 naturalness deciles over the front; one per decile by greedy max-min Hamming distance, penalty `40 x abs(nat - median) / span`; then a homopolymer QC |
| extra measure | PyTorch + ViennaRNA degradation model | 107-nt windows, stride 34, 3 seeds; **not** a Pareto axis |

The `gene-cellertz-*` generators call `cellertz-ai gene-cellertz generate`. The
whole campaign ran on AWS EC2 `cellertz2`, **not on this server**.

### 2.1 What the maintainer says (26 Nov)

The CLI has eight commands, composed through JSON Lines on stdin/stdout:

| command | what it does |
| --- | --- |
| `build` | builds a strand **with its mask** from building-block files; a block whose path has `masked` as a whole component is masked out. Writes one JSONL record carrying strand and mask, so it pipes straight into the rest. This replaces the hand-assembled construct and the hand-added stop codon |
| `sample` | samples codon sequences with an external generator; **always accepts a seed**, so it can sample around starting sequences |
| `evaluate` | adds properties to each record; `-e` takes a **math expression over metrics** (`log(cai) * log(naturalness)`) |
| `filter` | reduces a pool to its **epsilon-approximate** Pareto frontier |
| `analyze` | summarises a scored pool; a diagnostic, echoes nothing back |
| `minimize` | minimises properties of sequences |
| `translate` | codons to amino acids |
| `doctor` | reports which metric variables each configured evaluator provides, whether its command can be found, and name collisions |

- **No server, no API: a CLI only.** Tuned for CPU; GPU is untested and
  "probably faster". Memory scales with batch size, whose default is what fits a
  16 GB laptop; he can add a flag if a larger batch helps.
- **The mask is always required** and marks everything that is not CDS; the
  unmasked regions must be valid coding regions. It is also how he infers the
  UTRs.
- **The metric list is open.** It comes from a config file (over 3 000 lines as
  shipped) that registers external programs obeying a protocol, which "has
  changed several times". Built-ins include folding (`lfc`, `lfv`, `lfcp`,
  `lfvp`, `mfe`, `lmfe`, `efe`, `circmfe`, `circefe`) and information content
  (`ic`); the list in the message was cut off.
- **Determinism.** In practice every objective so far is deterministic. The
  guarantee he will give is weaker: each objective is deterministic **or a
  convergent estimate** (variance tends to zero as the budget grows), because
  Monte Carlo estimators may come.
- His public `USAGE.md` and `CONFIGURATION.md` links return 404 to us; the
  repository is not publicly readable. This plan has **not** read them.

### Verified

- His Pareto front, recomputed from his library: **identical** (113 = 109
  generated + 4 reference sequences that sit on it).
- All 10 shortlisted designs are on the front, one per decile.
- All 10 CDS are 1650 nt, translate to the 550 aa protein, have no internal
  stop.
- The axes trade off. On the generated front, naturalness x half-life
  correlates **-0.775** and naturalness x TE +0.517. This is why the problem
  is multi-objective and a single score would hide it.

### His own inconsistencies (fix when porting, do not copy)

- Prose says "80 Pareto sequences" in step 3; it is 109.
- `pareto_with_strands_kozak.py` reads `fluc_stop_eval_*`; the documented
  commands rename files to `fluc_kozak_eval_*`.
- `deg_score_library.py` asserts strand length 1975; this campaign's strands
  are 1978 (the stop codon was added), so that script is the pre-stop version.
- The dedupe keeps a strand in the *first* strategy that produced it, so the
  strategy a design is credited to depends on list order.

## 3. What the artifact cannot tell us

| open | what the artifact gives | what is missing |
| --- | --- | --- |
| **When is nothing found?** | No acceptance threshold anywhere. The front is relative and can never be empty. His one "rerun" was a construct bug (no stop codon) found by a person. | A rule for "no hit". See section 6 for one the data supports. |
| **Why naturalness deciles?** | One sentence: the metric "we've observed correlates with measured expression". | No expression measurement exists in any file; every number is a model prediction. "Previously-validated" (5 references) is never explained. |
| **Manufacturability limits** | Homopolymer max run is reported (6 nt) and called "not a concern". | No limit. The embedded GenScript/FastBio order form asks for restriction sites to avoid, stop-codon policy, organism, vector. |
| **Where Codonscope runs, and how it is called** | CLI on `cellertz2`, driven from a shell. | **Answered** (2.1): a CPU CLI, no server, accepts seeds. Still open: who owns `cellertz-ai`, and how we get the binary and its docs. |

## 4. Architecture

### 4.1 Principle

The protein side taught one thing repeatedly: **the agent proposes, a tool
measures, and a verdict is derived from the measurement, never claimed by the
agent.** So only the steps that need language use a model.

```
 intent (free text)
   |  Supervisor [LLM]            extracts DECLARED fields; never infers them
   v
 CampaignSpec  --- human gate 1: approve the spec digest -------------------+
   |   protein, UTRs, mask, stop policy, objectives, hit criterion,          |
   |   reference set, hard constraints, strategy portfolio, budget           |
   v                                                                         |
 ConstructValidator [deterministic]  translate-back == protein, one stop,    |
   |                                  mask matches, no internal stop         |
   v                                                                         |
 round N:                                                                    |
   Generate  [tool]   codonscope sample, per strategy in the portfolio       |
   Evaluate  [tool]   codonscope evaluate                                    |
   Pool      [det.]   dedupe by strand, global Pareto                        |
   Critique  [det.]   hard constraints, homopolymer, (degradation: descriptive)
   Verdict   [det.]   hit / no hit against the declared criterion            |
   Allocate  [det.]   next portfolio from each strategy's front contribution |
   Review    [LLM]    writes the round's lesson                              |
   |  stop? -> budget, convergence, or hit                                   |
   v                                                                         |
 Shortlist [det.]  deciles + max-min Hamming -- human gate 2 ----------------+
   v
 synthesis export (order-form fields)
```

### 4.2 The seven plan agents, mapped

| plan agent | v1 implementation |
| --- | --- |
| Supervisor | **LLM**, governed prompt in the admin like the protein supervisor |
| Generation | **tool**: `codonscope sample`; the model does not write sequences |
| Proximity | **deterministic**: Hamming clustering (already in his shortlist step) |
| Reflection / Critique | **deterministic** checks; a model may only *explain* a result |
| Ranking | **deterministic**: global Pareto + stratified shortlist |
| Evolution | **deterministic**: reallocate the next round's budget by front contribution, and **resample around the current front** (`sample` always accepts a seed) |
| Meta-review | **LLM**: the round's lesson, recorded beside what the round measured |

Two of seven use a model in v1. This is a change from the earlier sketch, where
Evolution was model-driven: in his data the useful move is picking strategies
(t=0.25 supplies 56 of the 109 global-front designs, t=0.5 supplies 24,
beam-150 supplies 12), and resampling around survivors is a flag, not a
judgment.

### 4.3 The tool seam

Operations are **named and allow-listed, never a shell**, consistent with the
harness position (Hermes/OpenClaw sit below the engine and get no shell). The
contract already reserves `mrna.codonscope.design`; this proposes finer names:

```
mrna.codonscope.sample      strand, mask, generator, n
mrna.codonscope.evaluate    strands, mask, metrics
```

The domain code talks to a `CodonscopeRunner` protocol whose wire format is the
CLI's own: **JSON Lines on stdin and stdout, no files to glue together**.

| option | status after the 26 Nov answers |
| --- | --- |
| **A. binary installed on this server** | **the plan.** It is a CPU CLI with no server; `evaluate`, `build`, `filter` and the native `codon-usage` generator need nothing else. Needs the binary, its config and its docs (open) |
| B. thin service on another host | only if the binary cannot run here |
| C. ssh runtime | last resort; must still kill the whole process group at the deadline, as `GroupSupervisedRuntime` does on the card |

Two things that follow from the CLI's shape:

- **`codonscope doctor` is the readiness gate.** Before a campaign it reports
  which metric variables the configured evaluators provide and whether their
  commands exist. A campaign whose objective needs a metric that `doctor`
  cannot resolve is refused before any run, like the GPU and image checks on the
  protein side.
- **The strategies other than `codon-usage` use an external generator,
  `cellertz-ai gene-cellertz generate`.** Whether it runs on CPU, who maintains
  it and whether it can be installed here is **not answered** (his answers are
  about Codonscope). Until it is, the portfolio is `codon-usage` plus seeded
  resampling.

### 4.4 Data model (additions)

`MRNASequenceCandidate` exists with `cds`, UTRs and three metric dicts. It
lacks: the stop codon, the mask, the strategy and the strand-level identity.
A scoring protocol id carries the axes and their directions
(`CODONSCOPE_PARETO_3AXIS_v1`), and results from different protocols are never
compared, as with `ENGAGER_TWO_AXIS_2026`.

### 4.5 Where the bar lives: a versioned profile in the Django admin

The bar (reference set, per-axis floors, hard constraints, diversity floor,
shortlist size) is configured in the admin, **as the agent prompts already are**:

- A **`BarProfile` row is a version.** Editing creates a new row; saved rows are
  read-only. A result already recorded names the version that judged it, and
  editing in place would make past and future results look comparable when they
  are not. This is the `AgentPrompt` rule, for the same reason.
- A campaign **copies the values, and pins name + version + sha256**, into its
  spec at approval (as `ResolvedPrompt.provenance()` does). Changing the admin
  afterwards never changes an approved campaign.
- The bar's numbers depend on the evaluator: reference sequences are stored as
  sequences and **re-evaluated at campaign start**. The pinned snapshot holds
  the `codonscope --version`, the **exact `-e` objective expressions**, and the
  `doctor` report, because the metrics protocol has changed several times and
  an objective is a free expression, not a fixed name. A new evaluator version
  or a new expression is a new bar.
- A scientist may **override per campaign**; the override is a diff shown at
  gate 1 and is part of the digest.
- **Governance stays in code and tests, not in the table:** the agent has no
  write path to a profile, and "a round may widen the search, never lower the
  bar" cannot be edited away from the admin.
- The `cellertz` package imports no framework, so it defines a provider
  `Protocol`, Django implements it, and `coscientist/conversation.py`
  registers it, exactly as `AgentPromptProvider` is wired.

Loop budgets (rounds, wall-clock, tokens) are defaults in the admin but are
copied into each campaign's spec; what a campaign spent is already recorded.
This also turns the open questions to the professor into admin fields he fills
in himself, instead of answers in a chat.

## 5. Reuse from the protein side

| need | already there |
| --- | --- |
| governed agent prompts and models | `agent_prompts.register_agent`, the admin |
| request analysis | `request_supervisor.py` |
| rounds, parent design, lesson | `design_journey.py` (`seeded_from`, `lesson`), `journey_orchestration` |
| record a score under its protocol | `RECORDED_PROTOCOLS` in `journey_specialist_progress.py` |
| front: rounds panel, lesson box | `DesignRounds.tsx`, `rounds.ts` |
| process-group deadline | `GroupSupervisedRuntime` |
| operation names, authorization | `execution/` contract |

## 6. The loop: what "found" and "again" mean

**Hit.** The professor declares a *reference set* and a bar. The data supports
this directly: against his own 2173 generated designs, the five validated
references and two benchmarks are dominated by

```
validated 1: 0      validated 2: 0     validated 3: 91    validated 4: 0
validated 5: 1      benchmark 1: 0     benchmark 2: 15
```

Four of seven are dominated by **nothing**, and validated 1 has naturalness
0.836 against a generated maximum of 0.825. So "no design dominates the chosen
reference" is a real, measurable *no hit*, and it would have fired for four
references in this very campaign. Absolute per-axis floors may be added.

**Again.** Each round changes **one declared thing** from an allowed set (add a
strategy, raise N, change a temperature, widen a beam, **resample around the
current front**). Repeating the same experiment is not a search.

**The agent may widen the search; it may never lower the bar.** The reference
set, the floors and the hard constraints are in the approved spec digest. A
change to any of them is a new spec and a new human gate.

**Stops.** Maximum rounds, maximum wall-clock, a token ceiling, and convergence
(no new non-dominated point for k rounds). On exhaustion the answer is "no
design found", with what was tried and what each round measured.

**Diversity trigger.** His shortlist's #9 and #10 differ by 8 nt: the
high-naturalness end is dominated by beam-150, which converges. A declared
Hamming floor makes that a measurable reason for another round.

**Estimators.** If an objective becomes a Monte Carlo estimate, a hit has to
beat the estimate's own noise, and the evaluation budget joins the spec. Today
every objective is deterministic, so this is a recorded property of each
metric, not a code path to build yet.

**Cost.** Rounds are minutes, not seconds: temperature sampling ~552 s per 500
sequences, evaluation ~58 s per 500, `codon-usage` free, a full 8-strategy
campaign ~48 min. The risk of an autonomous loop here is tokens and
wall-clock. Codonscope itself runs on CPU, so it no longer competes for the card.

## 7. Build order

Each phase ends with a check against his published numbers.

| # | phase | exit check |
| --- | --- | --- |
| 0 | **Facts**: how to get the binary and docs (N1); the reference bar and vendor limits (P1, P2); acceptance of this plan | answers recorded |
| 1 | **Offline core**: Pareto, dedupe, stratified shortlist, on his published library as a fixture; a design's identity is the **UTR+CDS combination** (10.3) | reproduces 113 = 109 + 4 and one pick per decile. The Hamming picks themselves need strands he did not publish, so they are property-tested until a real run supplies them |
| 2 | **Construct validator** | accepts his 10; rejects the no-stop construct and a two-stop reference |
| 3 | **Install the binary here; `doctor`; first real call** through the runner | `doctor` resolves his three objectives; re-evaluating his 10 shortlisted strands returns his nat/te/hl within tolerance; the same strand twice returns the same values (cheap, and the bar is re-evaluated per campaign) |
| 4 | **One real round**: `codon-usage`, then one seeded resample around its front; a temperature strategy only once `cellertz-ai` is reachable | wall-clock and front match the order of magnitude above |
| 5 | **`BarProfile` in the admin (4.5), Supervisor, spec, gate 1** | a campaign cannot be approved without a profile; the spec digest binds criterion, floors, reference set, evaluator version, budget |
| 6 | **The loop**: allocation, stops, lesson | a campaign that cannot beat a reference stops cleanly and says so |
| 7 | **Journey and screen reuse**, shortlist gate, synthesis export | the professor runs one campaign from the screen |

## 8. Risks and decisions needed

1. **Getting the binary and its docs** decides phase 3. The repository is not
   publicly readable and the metrics protocol is, in the maintainer's words,
   an implementation detail that has changed several times: pin the version and
   the `doctor` report, and expect to re-pin.
2. **Determinism has no contract.** It holds today; the promise is only
   "deterministic or convergent". Record, per metric, which it is.
3. **Scale.** His exact Pareto is O(n^2): fine at 2 000 designs, ~2.5e9
   comparisons at 50 000. Either a sort-based exact front, or `filter`'s
   epsilon-approximate one with the epsilon in the pinned snapshot; the two are
   not interchangeable and the plan must say which a result used.
4. **The bar is the professor's.** Which reference, which floors, which
   vendor limits. The plan does not invent them; he sets them in the admin
   profile (4.5), and no profile means no campaign can be approved.
5. **Approval must bind what matters.** On the protein side the reviewer signs a
   plan digest while the verdict, seed and lesson ride beside it. Here the spec
   digest covers the criterion and the bar from the start.

## 9. Pending answers, and what each one changes

Nothing below is built until these are answered. The right-hand columns are the
plan's branch for each answer, so the next step is known whichever way it goes.

### 9.1 For the Codonscope maintainer

**Answered 26 Nov** (details in 2.1):

| # | question | answer | effect |
| --- | --- | --- | --- |
| O1 | API, server or MCP? | none: a CLI only | runner speaks JSON Lines over a process; no network seam |
| O3 | GPU needed? | no, CPU-tuned; GPU untested | runs here; memory follows batch size |
| O4 | Does `sample` take a seed? | always, and the mask is always required | Evolution resamples around the front in v1 |
| O5 | Is `evaluate` deterministic? | in practice yes; contract is "deterministic or convergent estimate" | record it per metric; an estimator needs a noise margin |
| O6 | Which metrics? | open list from a config file; `-e` is a math expression | pin the expressions and the `doctor` report |

My reading of "Sim, pra esses objetivos mencionados sim" is that it answers O5 for
naturalness, TE and half-life; his longer reply to it supports that.

**Still open** (send these):

| # | question | blocks | effect |
| --- | --- | --- | --- |
| N1 | How do we get the binary, its config and `USAGE.md` / `CONFIGURATION.md` (the repo returns 404 to us)? Any licence limit on installing it here? | **phase 3** | without them nothing runs |
| N2 | What is `cellertz-ai gene-cellertz generate`: who maintains it, does it run on CPU, can it be installed here? | phase 4 | if not, the portfolio is `codon-usage` plus seeded resampling |
| N3 | What is `filter`'s default epsilon, and does epsilon 0 give the exact front? | phase 1 | decides exact versus approximate fronts at scale |
| N4 | Is there a version we can pin for the metrics protocol and config (beyond `--version`)? | phase 3 | what goes in the bar's snapshot |
| N5 | What does `minimize` do, and does it take a mask and a seed? | none | may replace part of the resampling move |
| N6 | Does the config register an **immunogenicity** evaluator, and anything for **ribosome dynamics** (codon ramp, tRNA adaptation, pause sites) beyond `te_mean`? | phase 5 | decides whether these are axes we can measure or gaps we declare |

### 9.2 For the professor

| # | question | blocks | effect |
| --- | --- | --- | --- |
| P1 | Which reference sequence is the bar for "found", or which per-axis floors? | **phase 5** | it is the `BarProfile`; with no answer no campaign can be approved |
| P2 | Which manufacturing limits apply: restriction enzymes to avoid, stop-codon policy, organism, vector, maximum homopolymer run? | phase 5 | hard constraints in the profile and fields of the synthesis export |
| P3 | ~~Are the UTRs fixed, or part of the design?~~ **Answered by his question 1: they vary.** Still open: is the target host human? | phase 1 | the interaction study (10.3) is now a campaign type, and a design's identity is the UTR+CDS combination |
| P4 | What is the first real protein after Fluc? | phase 4 | a second worked example, so nothing is tuned to one construct |
| P5 | What is behind "naturalness correlates with measured expression", and what does "previously-validated" mean for the 5 references? | none | decides whether naturalness deciles are a method or a convention |
| P6 | Is "10 sequences, naturalness deciles, Hamming diversity" a standing method or this campaign's choice? | phase 1 | the shortlist size and stratifying axis become profile fields |
| P7 | Agree: a round may widen the search, never lower the bar, and changing the bar needs approval? | phase 6 | a rule enforced in code, not configuration |
| P8 | Should **immunogenicity** be a fourth axis, a hard constraint, or out of scope? And the degradation model: axis, constraint, or descriptive as today? | phase 5 | both are profile fields; today decay is measured and unused |

### 9.3 What can start before any answer

Phases 1 and 2 need none of the open answers: the offline core is exercised on his
published library (reproduce 113 = 109 + 4 and one pick per decile) and the
construct validator on his 10 published CDS plus constructed failures. Only
the dependencies on the answers above wait.

## 10. The professor's three questions, and Therna's platform

Added 26 Nov 2026, from three questions the professor sent and the public
page at `therna.com/platform` (RNA-Logix). Their page is a vendor claim, read
once; nothing here treats it as a specification.

### 10.1 His three questions against this plan

| # | his question | where it lands |
| --- | --- | --- |
| 1 | How synergistic or additive is the interaction between 5'UTR, coding region and 3'UTR? | **new: a second campaign type** (10.3). His Fluc data cannot answer it — both UTRs are fixed there — and it settles the open P3: the UTRs vary |
| 2 | IP agent: how distinct are the sequences from patented mRNA? | **new: a gate, not an axis** (10.4), at phase 7 |
| 3 | Generation, reasoning and prioritisation on model metrics **plus** secondary structure and ribosome dynamics / decay, grounded in PubMed | **mostly already here** (10.5) |

### 10.2 What RNA-Logix is, and where we differ

| theirs | ours |
| --- | --- |
| **Chronos**: proprietary wet-lab data — 10M measurements, ~60 cell contexts, ~60k UTR fragments | the professor's 5 validated sequences and 2 benchmarks. **This is the real asymmetry; no amount of architecture closes it** |
| **T-Series**: proprietary foundation models, one per axis (UTRs, splicing, interactions, translation, structure, design) | Codonscope evaluators, an open metric list from config, auditable through `doctor` |
| **Proto**: "optimises all attributes at once", contrasted with one at a time in the order 5'UTR, CDS, 3'UTR, immunogenicity | a Pareto front over several axes **is** simultaneous optimisation; we already do this, over 3 axes, with the UTRs fixed |
| **BioReason-RNA**: a reasoning model whose chain is "the proposed mechanism" | a critic that must **cite retrieved literature**; the mechanism is evidence-backed rather than model-asserted |
| **Clotho**: the loop — design, mechanism, test against their data, refine until a hypothesis holds | the journey loop: rounds, seed, lesson, declared budget and stops |
| no human gate described: "a scientist asks the question and a scientist receives the report" | **mandatory human gates**, as the work plan requires. A deliberate difference, not a gap |

**The one claim worth answering with measurement.** Proto's selling point is
that attributes are optimised together rather than in sequence. The professor's
question 1 asks whether that matters here — is the combination additive or not.
They assert; we can **measure** it, and cheaply. Either answer is useful: if
additive, optimising the CDS alone is defensible and the search space collapses;
if not, joint UTR+CDS design is necessary and that is a finding.

**Immunogenicity** is on their axis list and on none of ours. The professor's
three axes are naturalness, TE and half-life. Open: does the Codonscope config
register an immunogenicity evaluator (ask Felipe), and should it be an axis or a
constraint (ask the professor)?

### 10.3 Campaign type 2: interaction study

The optimisation campaign searches for the best designs. This one **measures
whether the parts interact**, and is a different run with a different output.

- **Library**: a factorial sweep over (5'UTR x CDS x 3'UTR) building blocks,
  assembled by `codonscope build` — which takes building-block files and emits
  strand **and** mask in one record, so it is the primitive for this.
- **Analysis**: is `f(u5, cds, u3)` predicted by the sum of each part's effect?
  The deviation is the interaction. Deterministic, no model needed.
- **Cost is the reason to do it early**: evaluation is ~58 s per 500 sequences
  while sampling is ~552 s per 500. A factorial sweep over CDS that already
  exist is nearly free; it reuses designs instead of generating them.
- **It changes phase 1**: a design's identity is the *combination*, not the CDS.
  Cheap to carry from the start, expensive to retrofit.

### 10.4 The IP distance gate

A constraint before the shortlist gate and the synthesis export, never a Pareto
axis. Same shape as the literature layer: an external source, a cache behind a
protocol, provenance on every answer.

**Two boundaries, in the code and on the screen:**

1. **It measures distance; it does not clear anything.** "No patent found" is
   not a freedom-to-operate opinion, and the platform must never present it as
   one.
2. Whether a sequence search over patents is available at all is **unknown**:
   Google Patents has no documented free sequence-similarity API. Public patent
   data on BigQuery, EBI patent sequence databases and Lens are candidates to
   investigate. Phase 7, because there is nothing to screen until designs exist.

### 10.5 What question 3 already has

| he asks for | status |
| --- | --- |
| secondary structure | **already in Codonscope**: `lfc`, `lfv`, `lfcp`, `lfvp` (LinearFold / LinearPartition), `mfe`, `lmfe`, `efe`, and circular variants. `-e` takes expressions, so this is profile configuration, not new code |
| mRNA decay | **already measured** by his PyTorch degradation model, but deliberately kept off the Pareto axes. Promoting it to an axis or a hard constraint is his decision to record in the profile |
| ribosome dynamics | partly `te_mean`. Codon ramp, tRNA adaptation and pause sites would be new evaluators — open question for Felipe |
| PubMed knowledge base | **already integrated**: `literature_search.py` (EuropePMC, which indexes PubMed) with a cache protocol and a Django implementation; the hotspot agent already cites what it retrieved |
| reasoning that proposes a mechanism | the round's **lesson** already exists and is recorded beside what the round measured. Make the mechanism an explicit, cited field of it — this is the auditable version of BioReason-RNA's reasoning chain |
