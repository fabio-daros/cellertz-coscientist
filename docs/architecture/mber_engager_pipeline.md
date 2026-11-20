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

`MBERAdapter` already takes the command from the request:

```
constraints.mber_command = ["/opt/mber-edroaldo/bin/mber",
                            "--settings", "{work_dir}/settings.yaml"]
```

`{work_dir}` is substituted; `mber_bin` + `settings` is the alternative. The
professor's modified mBER is therefore a configuration change, not a code
change.

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
3. **Screening.** Cys, sequon, charge and occlusion filters in the critique
   stage. This is Phase 3 item 06, developability, which has no implementation.
4. **Two-axis ranking** with Pareto front and greedy diversity, as its own
   protocol.
5. **Target preparation and seeding** exposed as plan fields, since they are the
   two levers that work.
6. **Visualization** in the workspace: 3Dmol (the atlas uses `3dmol@2.0.4`)
   showing the target with each block in its own colour, and the audit beside
   it.

Steps 1 and 2 need no container and no GPU.
