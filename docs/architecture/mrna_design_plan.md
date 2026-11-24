# mRNA Design: architecture and build plan

Status: **proposal, 24 Nov 2026, waiting on the answers in section 9.** Nothing
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
| local front | `codonscope filter` | per strategy |
| pool | his `pareto_with_strands_kozak.py` | dedupe by strand (2701 -> 2173), **global** Pareto |
| shortlist | his `select10_kozak.py` | 10 naturalness deciles over the front; one per decile by greedy max-min Hamming distance, penalty `40 x abs(nat - median) / span`; then a homopolymer QC |
| extra measure | PyTorch + ViennaRNA degradation model | 107-nt windows, stride 34, 3 seeds; **not** a Pareto axis |

The `gene-cellertz-*` generators call `cellertz-ai gene-cellertz generate`
(GPU). The whole campaign ran on AWS EC2 `cellertz2`, **not on this server**.

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
| **Where Codonscope runs, and how it is called** | CLI on `cellertz2`, driven from a shell. | Whether it has an API or server mode; whether `sample` accepts a seed. |

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
| Evolution | **deterministic** in v1: reallocate the next round's budget by front contribution. Seeded evolution needs Codonscope to accept a seed (open) |
| Meta-review | **LLM**: the round's lesson, recorded beside what the round measured |

Two of seven use a model in v1. This is a change from the earlier sketch, where
Evolution was model-driven: in his data the useful move is picking strategies
(t=0.25 supplies 56 of the 109 global-front designs, t=0.5 supplies 24,
beam-150 supplies 12), and that is arithmetic.

### 4.3 The tool seam

Operations are **named and allow-listed, never a shell**, consistent with the
harness position (Hermes/OpenClaw sit below the engine and get no shell). The
contract already reserves `mrna.codonscope.design`; this proposes finer names:

```
mrna.codonscope.sample      strand, mask, generator, n
mrna.codonscope.evaluate    strands, mask, metrics
```

The domain code talks to a `CodonscopeRunner` protocol. Three ways to satisfy
it, chosen once the API question is answered:

| option | when |
| --- | --- |
| A. binary installed here | `evaluate` and `codon-usage` are native Rust; `gene-cellertz-*` generators still need `cellertz-ai` and a GPU |
| B. thin service on `cellertz2` | Codonscope exposes an API, or we wrap the CLI behind an allow-list |
| C. ssh runtime | last resort; must still kill the whole process group at the deadline, as `GroupSupervisedRuntime` does on the card |

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
  sequences and **re-evaluated at campaign start**, and the Codonscope version
  goes into the pinned snapshot. A new evaluator version is a new bar.
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
strategy, raise N, change a temperature, widen a beam). Repeating the same
experiment is not a search.

**The agent may widen the search; it may never lower the bar.** The reference
set, the floors and the hard constraints are in the approved spec digest. A
change to any of them is a new spec and a new human gate.

**Stops.** Maximum rounds, maximum wall-clock, a token ceiling, and convergence
(no new non-dominated point for k rounds). On exhaustion the answer is "no
design found", with what was tried and what each round measured.

**Diversity trigger.** His shortlist's #9 and #10 differ by 8 nt: the
high-naturalness end is dominated by beam-150, which converges. A declared
Hamming floor makes that a measurable reason for another round.

**Cost.** Rounds are minutes, not seconds: temperature sampling ~552 s per 500
sequences, evaluation ~58 s per 500, `codon-usage` free, a full 8-strategy
campaign ~48 min. The risk of an autonomous loop here is tokens and
wall-clock, not a shared GPU.

## 7. Build order

Each phase ends with a check against his published numbers.

| # | phase | exit check |
| --- | --- | --- |
| 0 | **Facts**: API/runtime answer; reference bar; vendor constraints; acceptance of this plan | answers recorded |
| 1 | **Offline core**: Pareto, dedupe, stratified shortlist, on his published library as a fixture | reproduces 113 = 109 + 4 and one pick per decile. The Hamming picks themselves need strands he did not publish, so they are property-tested until a real run supplies them |
| 2 | **Construct validator** | accepts his 10; rejects the no-stop construct and a two-stop reference |
| 3 | **First real Codonscope call** through the runner | re-evaluating his 10 shortlisted strands returns his nat/te/hl within tolerance, **and the same strand evaluated twice returns the same values** (the bar is re-evaluated per campaign, so this blocks) |
| 4 | **One real round**: `codon-usage` then one temperature | wall-clock and front match the order of magnitude above |
| 5 | **`BarProfile` in the admin (4.5), Supervisor, spec, gate 1** | a campaign cannot be approved without a profile; the spec digest binds criterion, floors, reference set, evaluator version, budget |
| 6 | **The loop**: allocation, stops, lesson | a campaign that cannot beat a reference stops cleanly and says so |
| 7 | **Journey and screen reuse**, shortlist gate, synthesis export | the professor runs one campaign from the screen |

## 8. Risks and decisions needed

1. **Codonscope access** (section 4.3) decides phase 3. If it only exists as a
   CLI on `cellertz2`, option B or C and a network path from this server.
2. **Evaluation determinism** is unknown. He notes the degradation model
   differs by ~0.015 between independently trained models; phase 3 must check
   that `evaluate` is repeatable on the same strand.
3. **Scale.** His Pareto is O(n^2): fine at 2 000 designs, ~2.5e9 comparisons
   at 50 000. Use a sort-based front before the loop can generate that many.
4. **The bar is the professor's.** Which reference, which floors, which
   vendor limits. The plan does not invent them; he sets them in the admin
   profile (4.5), and no profile means no campaign can be approved.
5. **Approval must bind what matters.** On the protein side the reviewer signs a
   plan digest while the verdict, seed and lesson ride beside it. Here the spec
   digest covers the criterion and the bar from the start.

## 9. Pending answers, and what each one changes

Nothing below is built until these are answered. The right-hand columns are the
plan's branch for each answer, so the next step is known whichever way it goes.

### 9.1 For whoever operates Codonscope and `cellertz2`

| # | question | blocks | if yes | if no |
| --- | --- | --- | --- | --- |
| O1 | Is there an API, server mode or MCP? Can this server reach `cellertz2`, and by what path? | phase 3 | option B: call it | wrap the CLI behind an allow-list on `cellertz2` (B), or ssh (C) |
| O2 | Can the `codonscope` binary be installed on this server (version, licence)? | phase 3 | option A for `evaluate` and `codon-usage` | everything goes through `cellertz2` |
| O3 | Does `gene-cellertz` need a GPU, which, how much memory, and can it be called from outside `cellertz2`? | phase 4 | the full 8-strategy portfolio is available | the portfolio is **`codon-usage` only** until it is reachable; temperature and beam strategies wait |
| O4 | Does `sample` accept a seed (starting sequences)? | v2 only | Evolution can be seeded from the front | v1 stays an allocator across strategies |
| O5 | Is `evaluate` deterministic: same strand, same values, twice? | **phase 3** | the bar is re-evaluated each campaign | the bar is stored with a measured noise floor, and a hit needs a margin larger than it |
| O6 | Which metrics does `evaluate -e` accept beyond naturalness, half-life and TE? | phase 2 | GC, CAI, folding energy etc. become measured constraints | we compute what is cheap ourselves (homopolymer, restriction sites) and leave the rest out |

### 9.2 For the professor

| # | question | blocks | effect |
| --- | --- | --- | --- |
| P1 | Which reference sequence is the bar for "found", or which per-axis floors? | **phase 5** | it is the `BarProfile`; with no answer no campaign can be approved |
| P2 | Which manufacturing limits apply: restriction enzymes to avoid, stop-codon policy, organism, vector, maximum homopolymer run? | phase 5 | hard constraints in the profile and fields of the synthesis export |
| P3 | Are the UTRs fixed, or part of the design? Is the target host human? | phase 1 | variable UTRs add the plan's step 03 (UTR and CDS library) and a generator that varies them |
| P4 | What is the first real protein after Fluc? | phase 4 | a second worked example, so nothing is tuned to one construct |
| P5 | What is behind "naturalness correlates with measured expression", and what does "previously-validated" mean for the 5 references? | none | decides whether naturalness deciles are a method or a convention |
| P6 | Is "10 sequences, naturalness deciles, Hamming diversity" a standing method or this campaign's choice? | phase 1 | the shortlist size and stratifying axis become profile fields |
| P7 | Agree: a round may widen the search, never lower the bar, and changing the bar needs approval? | phase 6 | a rule enforced in code, not configuration |

### 9.3 What can start before any answer

Phases 1 and 2 need none of them: the offline core is exercised on his
published library (reproduce 113 = 109 + 4 and one pick per decile) and the
construct validator on his 10 published CDS plus constructed failures. Only
the dependencies on the answers above wait.
