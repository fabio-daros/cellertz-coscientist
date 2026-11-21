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

## Seven things that are not in their README

Each cost an iteration on 2026-11-21. None is a fault of theirs; all seven are
silent.

**-2. `XLA_PYTHON_CLIENT_PREALLOCATE=false` is required for any run longer
than one trajectory.** JAX preallocates most of the card by default, and mBER
builds a **torch** ESM2 model per trajectory in
`core/modules/template.py:_setup_models`. From the second trajectory on there
is nothing left for it: 31.51 of 31.60 GiB in use, only 3.93 GiB of that
PyTorch's, and `torch.OutOfMemoryError` while allocating 26 MiB. A
single-trajectory run never shows this and every real run does, which makes it
the worst of the seven to discover late. The wrapper exports it.

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
- **Yield, as observed: 1 accepted design in 8 trajectories**, counted across
  every run of 2026-11-21 that wrote a trajectory directory. The one that
  accepted did so on its first binder, at `i_ptm` 0.7833 and `plddt` 0.9230;
  the other seven never cleared `i_ptm >= 0.75`, topping out at 0.68. Eight
  trajectories is still few, and the figure is their bundled PDL1 example
  against one hotspot, not a campaign.
- A trajectory does not always produce the same number of binders: 10, 10, 9
  and 2 were observed, which is also why one took 114 s and another 455 s.
  Per-trajectory cost is a distribution, not a constant.
- **`complex_pdb_path` and `relaxed_pdb_path` are absolute paths**, into
  `<output_dir>/Accepted/`. Settled by a run that accepted something.
- **The accepted complex is chain A 1 to 115 and chain B 1 to 118** — so mBER
  renumbers the target on output exactly as the BindCraft attempts do, and
  `pdl1_numbering_offset` detects the same offset of 17 on it. That function
  was built against a BindCraft output and works unchanged on an mBER one.
- Structures are written per binder as
  `runs/<trajectory>/evaluation_data/binders/<trajectory>_binder-<n>_iptm-<score>_{complex,monomer,relaxed}.pdb`,
  and `design_summary.yaml` records the target as an absolute path. What
  `accepted.csv` puts in `complex_pdb_path` is still unconfirmed, because no
  run has yet produced an accepted row.
- Installing it does not make it runnable by the platform. The runner manifest
  and lifting `_BLOCKED_PROFILES` are separate, and neither is done.

## The image

Built 2026-11-21 from `mber.def`, **14 GB**, at
`/home/fabio/containers/cellertz/mber/mber.sif`:

```
sha256 edadcf1a7cd26c56ff7e2cd2412746e003b27f2de3353431341e484fa5b98e93
```

That digest is what a runner manifest names, and it is the identity: the recipe
is not bit-reproducible, so a rebuild is a different image and gets a different
digest. Inside it: `mber 1.0.0`, `torch 2.11.0+cu128` (the cu128 index's
newest, where the native install got 2.15.0 from PyPI), `jax 0.5.2`,
`colabdesign 1.1.3`, `numpy 1.26.4`, `matplotlib 3.8.4`. Verified on the card
through the image: `torch.cuda.is_available()` True and
`jax.devices() [CudaDevice(id=0)]`.

Run it through `mber-vhh-sif.sh`, which binds the weights and is what a
registered profile should name in argv:

```
apptainer run --nv --bind <weights>:/mber/.mber mber.sif --settings s.json
```

`install.sh batch` takes `MBER_RUNNER` to choose between the native
environment and the image, so the same orchestration drives both.

**This image predates two lines now in `mber.def`**: `MPLCONFIGDIR` and
`XDG_CACHE_HOME`, added because HOME is the bind mount point in a read-only
image, so matplotlib and fontconfig each warn and fall back on every run. The
wrapper sets both, so the built image behaves as though they were baked in.

## The image, run

Three single-trajectory runs through `mber-vhh-sif.sh`, 2026-11-21: three
trajectory directories written, 21 binders evaluated, **zero failed
trajectories**, and no accepted design — the best evaluated `i_ptm` was 0.66
against the 0.75 filter. So the image designs, evaluates and writes its state,
and the one-trajectory-per-process orchestration holds through the container
exactly as it does natively. The absence of an accepted design there is the
generator's own filter, not a fault of the image.

## The launcher, run

2026-11-21, one trajectory driven through `MberDemonstrationLauncher` — the
same `DemonstrationLaunch` the plan service builds, the same `launch()`, so
this is the frontend's path and not `install.sh batch`:

```
launched true, trajectories 1, accepted_designs 0,
profile_id pdl1-mber-apptainer-demonstration-v1,
generator_version mber-open-1.0.0@5cc8e0534158647e9706b2655630f4449686c600
```

440.8 s of trajectory, about 11 minutes wall, the full `runs/<trajectory>/`
tree written, and `i_ptm` topping out at **0.74** against the generator's 0.75
filter: rejected by one hundredth. The ninth trajectory of the day, and the
yield stands at 1 accepted in 9.

Two things the log showed that `install.sh`'s wrapper had been hiding:

**The cache variables have to be on the command line.** HOME is the bind mount
point and the image is read-only, so torch's kernel cache defaults to
`/mber/.cache/torch/kernels` and cannot be created — "This disables kernel
caching". The built image predates `MPLCONFIGDIR` and `XDG_CACHE_HOME` in
`mber.def`, and calling `apptainer run` directly loses what the wrapper
exports, so `trajectory_argv` passes both with `--env`.

**ESM2's weights load from the bind, but the run still talks to
huggingface.co.** The weights came off disk (540 shards instantly), yet
`huggingface.co/api/models/facebook/esm2_t33_650M_UR50D/tree/main` was fetched
first. That is a revision check, not a download. It is also an outbound
dependency nobody declared: a node without egress has not been tested, and
`HF_HUB_OFFLINE=1` is the thing to try there.
