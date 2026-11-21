# mber-open install recipe

The recipe, not the artefact. `install.sh` is versioned here; the 12 GB
environment and the 9 GB of weights live outside the repository, beside the
BindCraft SIF, exactly as `deploy/pdl1-bindcraft/` holds BindCraft's recipe
while its SIF does not. `_FORBIDDEN_ROOTS` in `apptainer_demonstration.py`
rejects any execution directory under `~/projects`, so the repository is never
an installation target: `install.sh` reads `MBER_ROOT` and defaults to
`/home/fabio/containers/cellertz/mber`.

```
install.sh clone      micromamba binary + source at the pinned commit
install.sh env        the environment
install.sh weights    ~9 GB: AlphaFold2, NanoBodyBuilder2, ESM2
install.sh configure  numpy pin, ~/.mber link, run wrapper
install.sh verify     what is present, and what the GPU is visible to
```

The pinned commit is the one in `application/mber_settings.MBER_OPEN_COMMIT`,
and the script refuses to continue on any other: an install whose version is
not the recorded one cannot be the recorded one.

## Six things that are not in their README

Each cost an iteration on 2026-11-21. None is a fault of theirs; all six are
silent.

**-1. `matplotlib<3.9` is required, and no requirement file says so.**
matplotlib 3.9 removed `matplotlib.cm.get_cmap`, which colabdesign 1.1.3 still
calls in `shared/plot.py:117`, reached from `af_model.animate()`. mBER calls
`animate()` in `core/modules/trajectory.py:480` inside `_update_design_state`
**unconditionally**, so `--no-animations` does not avoid it. Every trajectory
therefore dies at teardown with `AttributeError` *after* the design work is
finished: 432 seconds of GPU spent, `accepted.csv` written with its header and
no rows. Their requirements pin an upper bound on neither package.

**0. `download_weights.sh` reports success whatever happened.** Run outside the
environment it prints `ModuleNotFoundError: No module named 'transformers'` at
its ESM2 step, continues, and exits 0, leaving a 5 GB hole that shows up later
as a network fetch in the middle of a run. The `weights` stage therefore runs
the script through the environment **and** checks for a model file itself,
failing if none is there. Its exit code is not evidence. AlphaFold2's and
NanoBodyBuilder2's weights are only checked by presence, since no checksums
are published.

**1. `mber-vhh` comes from a second install.** The root package declares no
console script. `protocols/setup.py` declares
`mber-vhh=mber_protocols.stable.VHH_binder_design.cli:main`, so
`pip install -e protocols` is a separate step. Without it the environment
builds cleanly and the command does not exist.

**2. The pip index in `environment.yml` replaces PyPI.** Their pip section is
`-e . --index-url https://download.pytorch.org/whl/cu128`, and `--index-url`
replaces rather than adds, so everything else must happen to be on that index.
This recipe creates the conda half first and runs pip separately.

**3. PyPI's current torch is a CUDA 13 build, and this host is CUDA 12.2.**
The driver is 535.288.01. A default `pip install torch` yields torch 2.15.0
with `nvidia-*-cu13` wheels and `torch.cuda.is_available()` is **False** with
"driver too old". Torch is therefore installed last, from
`--index-url https://download.pytorch.org/whl/cu128`, which runs on a 12.2
driver under CUDA minor-version compatibility. JAX is unaffected:
`jax[cuda12]==0.5.2` sees the GPU either way, which matters because
AlphaFold2 and ColabDesign — the trajectory and evaluation engine — are JAX.

**4. The weight paths are hardcoded and no environment variable is read.**

| file | path |
| --- | --- |
| `models/folding/af2_model.py` | `data_dir='~/.mber/af_params'` |
| `models/folding/nbb2_model.py` | `weights_dir="~/.mber/nbb2_weights"` |
| `core/modules/config.py` | `af_params_dir = "~/.mber/af_params"` |

`download_weights.sh` takes a destination argument, so the bytes can live
beside the SIF, but nothing at run time looks there. `~/.mber` is therefore a
symlink to this installation's `weights/`. Patching their files instead would
mean the install is no longer the pinned commit.

`download_weights.sh` also exports `HF_HOME` only for its own duration, so
ESM2 would be fetched over the network at run time. `mber-vhh.sh`, written by
the `configure` stage, exports it and is what a registered profile should name
in its argv — never a free-text command, which is the rule in
`docs/architecture/mber_engager_pipeline.md`.

## Recorded state

- Pinned commit `5cc8e0534158647e9706b2655630f4449686c600`, 2026-10-21.
- The package declares `version = "1.0.0"`, which does not distinguish
  commits; the recorded generator version carries both.
- `torch.cuda.is_available()` True, `jax.devices()` `[CudaDevice(id=0)]`,
  `numpy 1.26.4` (their `numpy<2` pin), on an RTX 5000 Ada with 32 GB.
- Weights 8.3 GB: `af_params` 5.3 GB, `huggingface` 2.5 GB (ESM2),
  `nbb2_weights` 672 MB, plus `stereo_chemical_props.txt`.
- Environment 12 GB, source 6.4 MB. `matplotlib 3.8.4`.
- **One trajectory costs about 8.8 minutes** on that card: 433.6 s of
  trajectory plus 97.0 s evaluating the binders it produced. Measured twice on
  their bundled PDL1 example with `--max-trajectories 1`.
- **One trajectory yielded 10 candidate binders and 0 accepted ones.** That is
  `greedy_tries: 10`, each folded and scored. All ten passed `plddt >= 0.70`
  (0.88 to 0.90) and none reached `i_ptm >= 0.75` (0.56 to 0.67), so mBER's own
  filter rejected them. `accepted.csv` therefore held its header and no rows,
  which is the legitimate outcome and not a failure; the difference from the
  matplotlib crash is in the log, not in the file. Note the atlas's own gate
  for ranking is `i_ptm >= 0.80`, stricter than the generator's filter.
- At roughly 8.8 minutes per trajectory and no accepted design in one, the CD22
  stopping policy of 5000 accepted designs out of at most 50000 trajectories is
  not this machine's work.
- **mBER writes no PAE matrix**, re-checked against a real run and not only
  against their source: `evaluation_data.json` holds `pae` and `i_pae` as
  scalars (0.205, 0.226) beside `i_ptm` and `plddt`. ipSAE needs a per-residue
  matrix, so it stays uncomputable from mBER output without an independent
  refold. See the scoring section of
  `docs/architecture/mber_engager_pipeline.md`.
- Structures are written per binder as
  `runs/<trajectory>/evaluation_data/binders/<trajectory>_binder-<n>_iptm-<score>_{complex,monomer,relaxed}.pdb`,
  and `design_summary.yaml` records the target as an absolute path. What
  `accepted.csv` puts in `complex_pdb_path` is still unconfirmed, because no
  run has yet produced an accepted row.
- Installing it does not make it runnable by the platform. The runner manifest
  and lifting `_BLOCKED_PROFILES` are separate, and neither is done.
