# mBER engager pipeline

Where the platform is going for Protein Design, reverse-engineered from the
professor's Engager Target Atlas (nine engager targets, 76 hotspot blocks,
~24,000 accepted designs across the campaigns it reports).

The atlas is the professor's own record of running mBER. Everything in the
"What the campaigns showed" section is his measurement, not ours, and is
recorded here as the premise this design answers.

## What the campaigns showed

**Hotspot lists do not steer mBER.** Five independent instances:

| campaign | declared hotspots | median contacted |
| --- | --- | --- |
| CD19 (A154-A166) | 13 | 0 |
| CD22 (A624-A657) | 24 | 3 |
| CD3e campaign B | 10 | 0 |
| BCMA directed (6,530 designs) | varies | 12.4 APRIL residues |
| BCMA `NO_hotspots` control | none declared | 12.5 APRIL residues |

CD16a is the exception at 7 of 9, and the atlas explains it: all nine declared
hotspots already lay inside the IgG1 Fc interface, where the optimiser was
going anyway. It is evidence that steering succeeds when it asks for what the
model already does, not that steering works.

**What does move the designs:**

- **Target preparation.** mBER was given `cd19_mber.pdb` (7URV chain C, no
  CD81), so 26 CD81-contact residues sat exposed and the designs took them.
  The albumin fix was campaign D, which used the full-length target so mBER's
  own truncator kept the occluders as context.
- **Seeding on a good design.** On albumin this took the lineage from 2 of 14
  to 13 of 14.

**Consequence for this platform:** at the hotspot step an agent's useful job is
to *propose and then measure*, not to steer. A declared block is a claim that
has to be audited after the run.

## Hotspot block schema

From `targets_data.js` in the atlas. One target carries `key`, `label`, `note`,
`pdb_id`, `target_chain`, `partner_chains`, `extra_chains`, `target_name`,
`partner_name`, `atom_counts`, `verdict`, `drugs`, `contact_label`, and its
blocks. One block:

```
id            "A"
label         "Y157-R163"
lo, hi, n     157, 163, 7
priority      2
sel           "HIGH"            selectivity call
overlap       "OVERLAPPING"     against the partner's epitope
evidence      SASA per residue, secondary structure, known interface
              contacts, PFAM domain boundaries
rationale     why this block
selectivity_text
risks         e.g. competes with FMC63, CAR-T escape risk
contact_n, contact_pct   measured afterwards
```

`contact_n` / `contact_pct` are the audit: they are filled from the designs,
not proposed with the block.

## Ranking formula

A **new scoring protocol**, not the one already recorded. It must carry its own
protocol id and provenance and must never be mixed with `CARPNN_2026_REFERENCE`
values, for the reason in the next section.

Pre-filters, in order (BCMA numbers, 11,430 accepted designs):

1. no unpaired Cys, no N-X-S/T sequon -> 7,540
2. occlusion footprint <= 8 of 18 ligand residues -> 842
3. net charge <= +2 -> 754
4. top composite, one per trajectory -> 30

Then `i_ptm >= 0.80` (Manifold report this raises hit rates up to tenfold), and
two independent percentile axes:

- **i_pTM** on its own.
- **quality composite that excludes i_pTM**: J_clin 1.2 (epitope Jaccard against
  the clinical benchmark, better of its two nanobodies), buried area 1.0, net
  charge -0.7, liabilities -0.3 / -0.3 / -0.2.

Rank on the **worse of the two percentiles**, so a design rises only by being
good on both; the two axes are nearly independent (Spearman rho +0.155,
p 0.016), which is why one blended sum was wrong. Mark the Pareto front.
Selection is greedy with diversity: no two entries share a trajectory, and none
exceeds 0.85 epitope Jaccard together with 0.85 sequence identity against an
entry already chosen.

`liab` sums deamidation (NG/NS), isomerisation (DG/DS) and Met/Trp in CDRs.
Boltz-2 over five seeds is the independent external check; the atlas notes it
disagrees sharply with i_ptm where both exist, and the vault already holds
`boltz2_v2_global_benchmark`, `boltz2_v2_bindcraft1_holdout` and the
`ipsae_primary_ranking` claim.

## Two protocols, never mixed

Replaying `attempt.fff8b608` on 2026-11-19 gave ranking_score 0.5354 under
`CARPNN_2026_REFERENCE` (deterministic across repeats) where the journey had
recorded 0.5817 with no provenance block at all. The recorded value came from
an earlier protocol that did not record how it computed. The formula above is a
third protocol. Every score needs its protocol id beside it, and scores from
different protocols are not comparable.

## The mBER swap point

Today's mBER is the public `manifoldbio/mber-open` (MIT). Verified against the
repository on 2026-11-20, not inferred:

- The command is **`mber-vhh`**, not `mber`, and it takes the whole campaign as
  `mber-vhh --settings <file>` (YAML or JSON). The flag form
  (`--input-pdb --output-dir --chains --hotspots ...`) is the alternative.
- Settings keys: `output.dir`, `target.pdb|name|chains|hotspots`,
  `binder.masked_sequence`, `stopping.num_accepted|max_trajectories`,
  `filters.min_iptm|min_plddt`. The CD22 and TACI specifications already carry
  every one of them.
- A run writes `accepted.csv`, `Accepted/` and `runs/<trajectory>/`, and
  re-running resumes from the row count in `accepted.csv`. The header, read at
  the pinned commit, is exactly `trajectory_name, binder_index, binder_seq,
  i_ptm, plddt, ptm, complex_pdb_path, relaxed_pdb_path`, and `MBERParser`
  already matches it. Accepted PDBs are named
  `{trajectory_name}_binder-{binder_index}_iptm-{i_ptm:.4f}_complex.pdb`.
- **mBER writes no PAE matrix.** There is no `.npz` in the repository, and
  `evaluation.py` persists `aux["log"]`, which holds `pae` and `i_pae` as
  scalars. What reaches disk is PDBs, those scalars, and
  `design_state.pickle` unless `--no-pickle`. This matters more than anything
  else here: see "Scoring mBER" below.
- **The repository has no tag and no release**, so a commit is the only
  concrete version. `mber_settings.MBER_OPEN_COMMIT` pins it.
- Install is conda + `pip install -e protocols` + `download_weights.sh` (~9 GB:
  AlphaFold2, NanoBodyBuilder2, ESM2). Tested on A10G, A100, L4, L40S, H100;
  at least 32 GB of VRAM recommended.

`MBERAdapter` takes the command from `constraints.mber_command`
(`{work_dir}` is substituted) or from `mber_bin` + `settings`. What was
missing is that **nothing ever wrote the settings file** — the adapter passes a
path that no caller created, which is why the generator never ran outside its
own tests. `application/mber_settings.py` builds that document from a campaign
specification.

The professor's modified mBER is a later configuration change on top of this,
once his fork has a version and a host.

## Scoring mBER

The platform's primary signal is raw ipSAE, and
`BindCraftIpSAEEvidenceProvider.evaluate` computes it from a `*_pae.npz` plus
the model PDB. mBER emits no PAE matrix, so **the specialist chain cannot score
an mBER design as it stands**: `journey_specialist_progress` reads BindCraft's
stats CSV through `BindCraftAdapter` and scores with
`BindCraftIpSAEScoreProvider`. Generation normalisation is fine — the parser
matches the real header — and everything after it is BindCraft-shaped.

Two ways out, and only one of them is honest:

- **Re-fold independently and keep the PAE.** This is what the CD22 and TACI
  protocols already declare: `evaluation_strategy = "independent_refold"`.
  ipSAE then stays ipSAE, computed by us from a matrix we wrote.
- **Rank with the professor's own two-axis formula (step 4)**, which needs
  i_pTM and a quality composite, not ipSAE. `accepted.csv` carries `i_ptm`
  already, and the composite's terms come from the structure.

Substituting mBER's `i_ptm` for `ranking_score` is not an option: the adapter
forbids emitting `ranking_score` or `ipsae`, the platform's invariant says
i_pTM is not ipSAE, and the atlas's own finding is that one blended axis was
wrong. **Step 4 is therefore not decoration; it is the scoring path an mBER run
can actually feed.**

**The admin offers a short list of registered profiles, never a free-text
command.** A text field whose value becomes argv on a GPU host is arbitrary
execution for anyone who can reach the admin. This follows `AvailableModel`:
the installation registers the profiles, a person picks one.

## Steps

1. **Hotspot blocks.** The Hotspot Identification Agent returns blocks in the
   schema above instead of loose residues. Its prompt is already in the admin.
2. **Hotspot audit.** After a run, measure how many declared blocks the designs
   actually contacted, and record it. This is what makes a block a claim rather
   than decoration, and it is what the atlas does by hand.
3. **Screening.** Cys, sequon, charge and occlusion filters, in the atlas's own
   order. `screen_developability` measures them from the verified PDB: the
   sequence and the disulfide pairing are read from the structure, never from a
   review field, and a rejected design is named by the filter that rejected it.
   The results panel shows the screen for the model on screen.
   **Occlusion does not run yet:** no plan field declares the natural ligand's
   footprint, so that filter reports `not_declared` and the design is reported
   as having passed the *measured* filters, never as having passed. Step 5
   declares the footprint and turns it on.
4. **Two-axis ranking** with Pareto front and greedy diversity, as its own
   protocol. Built: `scoring/engager_two_axis.py`, protocol id
   `ENGAGER_TWO_AXIS_2026`, which every row carries beside its score and which
   is declared not comparable with `CARPNN_2026_REFERENCE` or
   `ipsae_primary_ranking`. Buried area comes from
   `scoring/interface_area.py` (Shrake-Rupley via Biopython, already a
   dependency).
   **Two things are not settled.** The atlas states the weights but not how
   terms of different units are combined, so per-term percentile
   normalisation before weighting is this platform's reading and is declared
   as such in the result; it needs checking against one of his own runs before
   a number is quoted as his. And `J_clin` weighs 1.2, so a cohort is
   *refused* without a declared clinical epitope rather than ranked on a
   formula with a term silently zeroed. For CD22 that epitope is derivable —
   the fixture's own rationale names the m971 Fab in 7O52, whose chains H and
   L it already records as the partners — but no prepared file with those
   chains is in hand.
5. **Target preparation and seeding**, the two levers that work. Built.

   **Preparation.** `occluded_by_partners` measures which target residues a
   partner chain covers, by solvent-accessible area. That one list answers
   three questions: it is what extracting the target chain would leave
   artificially exposed (the CD19 failure, 26 CD81-contact residues bare), it
   is the natural ligand's footprint for the developability screen, and it is
   the clinical benchmark epitope for the composite's J_clin. Cross-checked
   against the independent 5 Å contact calculation: every contacted residue is
   occluded, plus the ones the partner covers without touching, and the
   per-residue areas sum to the chain's buried area.

   **Seeding.** mBER has no seeding flag. The mechanism is the masked
   sequence: `seeded_masked_sequence` reseats the scaffold's mask on a parent
   design, holding the parent's residues wherever the bare scaffold would have
   left a position open, and opening only the spans to redesign. A parent off
   another scaffold, a span over the framework, and a seeding that leaves
   nothing open are each refused rather than quietly accepted.

## One path, joined

`application/mber_campaign.py` is the single join. Before it, each piece had
only its own tests and none had seen a real mBER output.

```
specification --mber_settings--> settings.json
      |
      |  launch_request: the adapter gets constraints.mber_command,
      |  built from a binary the installation registers, never from a field
      v
mber-vhh --settings            (the GPU host; this module runs nothing)
      |  MBERParser           accepted.csv -> candidates
      v
screen_structure               the atlas pre-filters, his order, first
      |
      v
rank_engager_designs           two axes, worse of the two
```

What it enforces, because a cohort's percentiles depend on who is in it:

- **The screen runs before the ranking.** A rejected design never reaches the
  percentiles, which is the point of pre-filtering.
- **The row count is read before parsing**, since the parser caps its result
  at the requested candidate count and a silently shortened cohort would move
  every percentile in it.
- **A design that cannot be measured is named, not dropped**: a missing
  structure, an unrecorded `i_ptm`, or a sequence in `accepted.csv` that
  disagrees with the structure's own chain B — the last one matters because
  the charge and the liabilities are read off the structure while the ranked
  sequence comes from the table, so a mismatch means two different molecules.
- **One surface pass per design.** The occlusion measurement yields both the
  residues the binder covers and the area it buries, and those agree with the
  per-chain calculation to within a square angstrom.

`screen_structure` is the screen's per-structure core, extracted so a recorded
attempt and a design mBER has just written go through the same rule. Which
residues count as reached is the caller's definition and is named in the
result: heavy-atom contact for the catalogue, shielded surface for mBER.

## Two numbering frames, and the error between them

The attempt structures on record are numbered **chain A, 1 to 115**. The
registered target file is numbered **18 to 132**. They are the same residues,
shifted by 17, confirmed by all 22 interface identities in
`pdl1_pd1_interface_4zqk` matching at that offset and only at that offset.

A residue number carried across those frames without the shift names a
different residue. `pdl1_numbering_offset` establishes the offset from
identity, never by assumption, and the native-ligand footprint is translated
by it; the hotspot audit now states the frame it compared in and warns when an
offset exists, because it cannot know which frame a declared block used.

With the offset applied, the design on record covers 12 of PD-1's 22 interface
residues on PD-L1. Without it, the same design would appear to bind elsewhere.

## Occlusion is a measurement, and its direction is the campaign's

The atlas's limit of 8 of 18 belongs to a BCMA engager that had to leave
APRIL's site alone. A PD-L1 binder that covers PD-1's site is doing its job.
So the screen measures occlusion whenever the footprint is declared and
rejects only when a campaign declares a limit. A screen that rejected by
default would discard exactly the designs a blockade campaign is for.
6. **Visualization** in the workspace: 3Dmol (the atlas uses `3dmol@2.0.4`)
   showing the target with each block in its own colour, and the audit beside
   it.

Steps 1 and 2 need no container and no GPU.
