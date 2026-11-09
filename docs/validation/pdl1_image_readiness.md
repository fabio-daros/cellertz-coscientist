# PDL1 image readiness

This page records what can be built for the governed Docker adapter and
what is still missing. It does not build an image, download weights, or
run BindCraft. Phase 6N is not complete: the first governed real PDL1
run has not happened. Production execution stays disabled.

The repository development `Dockerfile` and `compose.yaml` are not the
scientific image. The historical Apptainer image is not a Docker digest.
Do not copy either into `repository`, `image_digest`, or `image_id`.

## Already fixed

The adapter launches only these image paths:

- `/opt/BindCraft/bindcraft.py`
- `/opt/BindCraft/settings_filters/no_filters.json`
- container entrypoint token `pdl1-entrypoint`

`pdl1-entrypoint` is one executable name. Readiness rejects an entrypoint
that contains a slash. The container command remains the approved flags
`--cellertz-seed`, `--length-min`, `--length-max`, `--n-designs`, and
`--filters`. The filter value remains
`/opt/BindCraft/settings_filters/no_filters.json`. This page does not
change those values.

BindCraft commit `efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d` is the commit
on `martinpacesa/BindCraft` merged on 2026-08-12. At that commit,
`install_bindcraft.sh` and `settings_filters/no_filters.json` are present.
The install script creates a Python 3.10 environment and installs
ColabDesign with `git+https://github.com/sokrypton/ColabDesign.git` and
`--no-deps`. It does not record a ColabDesign commit.

The same script installs PyRosetta from the find-links origin
`https://west.rosettacommons.org/pyrosetta/quarterly/release.cxx11thread.serialization`.
It does not name a wheel or a SHA-256. PyRosetta is license-restricted.
This repository does not vendor a wheel.

AlphaFold parameters are the public archive
`https://storage.googleapis.com/alphafold/alphafold_params_2022-12-06.tar`,
the same URL as `google-deepmind/alphafold` `scripts/download_alphafold_params.sh`.
That script and the AlphaFold README do not publish a SHA-256. The
archive is about 5.3 GB. This repository does not download it.

CUDA compatibility is documented by NVIDIA at
`https://docs.nvidia.com/deploy/cuda-compatibility/latest/minor-version-compatibility.html`.
The BindCraft README shows `--cuda '12.4'` only as an example of the
install flag. It is not the image pin. No host driver was observed.

The executor already passes `--storage-opt size=<max_output_bytes>`.
The deadline is a systemd transient timer whose process is separate
from the worker. Neither check has a recorded Linux result.

## Operator supplies

Create a document with schema `cellertz.pdl1_image_pins.v1`. Leave a
field out rather than inventing it. `python -m` is not required;
`cellertz.application.pdl1_image_readiness.require_pins` rejects the
document before it returns build arguments.

| Field | What to record |
| --- | --- |
| `base_image_repository` | Registry name without a tag or digest of the image produced by `deploy/pdl1-bindcraft/base-environment.md`. A stock `nvidia/cuda` tag is not this value. |
| `base_image_digest` | `sha256:` plus 64 lowercase hex digits from `docker image inspect` of that image. |
| `host_nvidia_driver` | Driver version printed by `nvidia-smi` on the Linux GPU host. |
| `cuda_version` | CUDA toolkit version in that base image. Compare it with the NVIDIA compatibility document above. This checker does not make that comparison. |
| `colabdesign_commit` | 40 lowercase hex digits of a `sokrypton/ColabDesign` commit the operator has opened. Do not use `HEAD` or `main`. |
| `pyrosetta_wheel_filename` | PEP 427 wheel file name from the find-links origin above, under the operator's PyRosetta license. A name that matches that shape is not proof of ABI, Python, or platform compatibility. |
| `pyrosetta_wheel_sha256` | SHA-256 of that wheel, 64 lowercase hex digits. |
| `alphafold_params_sha256` | SHA-256 of `alphafold_params_2022-12-06.tar` after the operator downloads it from the AlphaFold URL. |

Place the wheel, under that original file name, and the parameter archive,
under the name `alphafold_params_2022-12-06.tar`, in
`deploy/pdl1-bindcraft/vendor/`. Those bytes are gitignored. Do not commit
them. `build_plan` reads `deploy/pdl1-bindcraft/Dockerfile` and accepts a
file only when its resolved path is a regular file directly inside that
vendor directory of the same build context. The resolved Dockerfile and
the vendor directory must remain inside the resolved context. A file
outside that directory, a symlink on the final file, a symlink on
`vendor` or an ancestor directory, or a `..` path is rejected before a
plan is returned. The Dockerfile copies that same
`deploy/pdl1-bindcraft/vendor/` directory and passes the original wheel
file name to `pip install --no-deps`.

`build_plan` returns a `docker build` argument list only when both files
match those digests and sit in that layout, and only when `docker_host`
and `docker_root` name one `unix://` socket inside that root. The
command starts `docker --host <docker_host>`. It does not run the
command, it does not require the socket file to exist, and it does not
substitute the default socket. The `--file` and
context arguments are the Dockerfile and repository root that were just
read. The returned plan keeps
`host_acceptance_complete` false, `wheel_abi_verified` false, and
scientific execution `disabled`.

## Base environment contract

`install_bindcraft.sh` at `efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d`
creates Python `3.10` and installs these conda tokens:

`pip`, `pandas`, `matplotlib`, `numpy<2.0.0`, `biopython`, `scipy`,
`pdbfixer`, `seaborn`, `libgfortran5`, `tqdm`, `jupyter`, `ffmpeg`,
`fsspec`, `py3dmol`, `chex`, `dm-haiku`, `flax<0.10.0`, `dm-tree`,
`joblib`, `ml-collections`, `immutabledict`, `optax`,
`jax>=0.4,<=0.6.0`, and, on the `--cuda` branch,
`jaxlib>=0.4,<=0.6.0=*cuda*`, `cuda-nvcc`, and `cudnn`.

The script's non-cuda branch installs `jaxlib` without `=*cuda*` and does
not add `cuda-nvcc` or `cudnn` on that install line. `required_packages`
still names `cuda-nvcc` and `cudnn`. That inconsistency is unresolved.
This repository does not pick a CUDA build, a host driver match, or an
exact version for any package that the script leaves unpinned.

The Cellertz image does not pip-install this project. The entrypoint
module uses the Python standard library (`csv`, `io`, `json`, `os`, `re`,
`runpy`, `stat`, `subprocess`, `sys`, plus `collections.abc`,
`dataclasses`, `pathlib`, and `typing`). `run_worker` imports `numpy`.
The wrapper executes `/opt/conda/envs/BindCraft/bin/python -m cellertz.application.pdl1_container_entrypoint`.
That interpreter is the BindCraft conda environment. The wrapper prepends
`/opt/conda/envs/BindCraft/lib` and `/opt/conda/lib` to `LD_LIBRARY_PATH`
and keeps every entry that was already set.
That command, and the Dockerfile's `from cellertz.application.pdl1_image_readiness import ...`,
load `cellertz/__init__.py` and `cellertz/application/__init__.py` before
the scientific module. The application initializer does not import
`conversation`, `contracts`, or `fixtures`. Those web-application modules
stay out of this startup path. `conversation` and `fixtures` import
`datetime.UTC`, which Python 3.10 does not provide. The image Python
remains the BindCraft `3.10` interpreter. The development package's
`requires-python >=3.12` is unchanged and is not a second pin for this
image. The Python 3.10 dynamic import check is pending: this workspace
has no `python3.10` interpreter. A test run on the developer interpreter
is not that check.

The build also needs `git`, `python`, `pip`, and `sha256sum` on `PATH`.
After checkout it requires `functions/dssp` and `functions/DAlphaBall.gcc`
to be executable files, matching the install script's `chmod`. The build
runs `ldd` on `DAlphaBall.gcc` with the intended library path and rejects
the output when any line says `not found`. A zero exit status from `ldd`
is not acceptance. The binary is not executed as a program. `bindcraft.py`
is checked with a file test only. The build does not import it and does
not run it.

Before BindCraft is checked out, the Dockerfile runs
`assert_base_environment` and `assert_intended_runtime_layout` with
`JAX_PLATFORMS=cpu` and an empty `CUDA_VISIBLE_DEVICES`, using
`/opt/conda/envs/BindCraft/bin/python`. The layout check requires that
interpreter, both conda library directories, and `libgfortran.so.5` in
one of those directories. A stock CUDA image fails this check. The base
probe imports the published modules, checks
the four published version bounds, checks the executables above, looks up
`libgfortran` and `cudnn` without assigning them versions, and imports the
Cellertz entrypoint module. It does not call the entrypoint. After the
wheel and ColabDesign installs, `assert_post_install` imports `pyrosetta`
and `colabdesign`, as the install script does, and imports the entrypoint
module again. A missing checkable fact fails the build with
`base_environment_incomplete`.

These remain blockers after a probe that finds every checkable fact:

- `pyrosetta_abi_unverified`: the wheel file name is format only.
- `jaxlib_cuda_build_unverified`: a CPU import does not prove `=*cuda*`.
- `cuda_nvcc_version_unpinned`, `cudnn_version_unpinned`, and
  `libgfortran5_version_unpinned`.
- `host_cuda_compatibility_unverified`: this checker does not compare the
  operator's driver with the NVIDIA document.
- `colabdesign_commit_unpinned_by_bindcraft`.
- `alphafold_params_sha256_unpublished`.
- `unpinned_conda_packages_unresolved`.
- `bindcraft_required_packages_cuda_inconsistency`.

The image build also fails on its own when an argument is empty, when the
BindCraft checkout is not `efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d`, or
when a supplied file hash does not match. The base image must already
contain the checkable stack above. This repository does not run the
conda solver and does not fill the unpinned ranges.

## Still blocked on the Linux NVIDIA host

These are not closed by an image pin:

- systemd manager, `Linger=yes` for a user manager, and the deadline
  interpreter import, as written in
  `docs/validation/pdl1_linux_gpu_host_acceptance.md`.
- The deadline check in which the worker process is stopped and the
  systemd timer still enforces the container. `start_new_session` is not
  that mechanism.
- Effective `--storage-opt size` enforcement. The daemon must report
  `Driver=overlay2` and `Supports project quotas` true. An inspected
  `StorageOpt` field is not that evidence.
- The image id and repository digest of the image produced from these
  pins. Readiness does not accept a tag in their place.
- Same-uid staging replacement, a readiness manifest that follows a final
  symlink, and the unpublished `design_records.csv`.

A passed pin check is not host acceptance and is not a scientific run.
