# BindCraft base image recipe

`deploy/pdl1-bindcraft/Dockerfile` checks a base image that already
contains the BindCraft conda environment. It does not install that
environment. `nvidia/cuda:12.2.2-base-ubuntu22.04` cannot pass
`assert_base_environment` or `assert_intended_runtime_layout`.

Scientific execution stays disabled. This page does not build an image,
download a package, or approve a pin. A version observed in the historical
Apptainer image is not a statement that every distribution of that version
uses the same CUDA stack.

## Intended layout

The governed image uses these paths and no others for the runtime:

- Python: `/opt/conda/envs/BindCraft/bin/python`
- Environment libraries: `/opt/conda/envs/BindCraft/lib`
- Conda libraries: `/opt/conda/lib`
- `libgfortran.so.5` in one of those two directories
- BindCraft checkout: `/opt/BindCraft`
- `DAlphaBall.gcc`: `/opt/BindCraft/functions/DAlphaBall.gcc`

`pdl1-entrypoint` and `operational_environment()` prepend the two library
directories to `LD_LIBRARY_PATH` and keep every existing entry. The build
runs `ldd` on `DAlphaBall.gcc` with that path and fails when the output
contains `not found`, including when `ldd` exits 0.

## Observations, not pins

The historical SIF inspection reported these values. `host_cuda_compatibility`
is not verified. `require_pins` still has no approved base digest, CUDA
version, ColabDesign commit, PyRosetta wheel, or AlphaFold parameter digest.

`conda-observed.lock` records the BindCraft prefix only: 431 exact `name=version=build` rows from `/opt/conda/envs/BindCraft`, channel label `conda.anaconda.org/conda-forge`. The seven earlier builds are included and unchanged:

| Package | Version | Build |
| --- | --- | --- |
| python | 3.10.21 | `h267e890_0_cpython` |
| jax | 0.6.0 | `pyhd8ed1ab_0` |
| jaxlib | 0.6.0 | `cuda126py310hec873cc_200` |
| cuda-version | 12.9 | `h4f385c5_3` |
| cuda-nvcc | 12.9.86 | `hcdd1206_6` |
| cudnn | 9.10.2.21 | `hbcb9cd8_0` |
| libgfortran5 | 16.2.0 | `h6b99dfc_5` |

Python `3.14.7=hcd007b5_106_cp314` and the base-prefix packages, including `anyio=4.15.1`, stay in `conda-bootstrap.observed.tsv`. The BindCraft `anyio` row is `4.14.2=pyhcf101f3_0`.

`conda-bindcraft.depends.tsv` stores every BindCraft dependency spec, including version and build constraints. A comment is not closure. The checker reports these virtual packages as unresolved because conda-meta does not record them: `__glibc` (230), `__unix` (8), `__linux` (2), and `__cuda` (1, from jaxlib). No conda dependency is satisfied only by a pip package.

Pip observations are separate in `pip-observed.tsv`:

- ColabDesign 1.1.3. The pip row has no commit. The earlier commit `e31a56fe1d9b4de25c8697f3a28b75892941cc72` stays in `historical-observations.json`.
- PyRosetta `2026.29+releasequarterly.80a0635615`. The wheel filename and SHA-256 are still absent.

These identities stay distinct. Conda CLI `26.7.2` is the inventory identity. The base-prefix conda package is `26.7.2=py314h9e666f3_1`. The MiniforgeVersion label `26.7.2-0` is an earlier observation; the SIF inventory did not record the installer. The official release https://github.com/conda-forge/miniforge/releases/tag/26.7.2-0 publishes `Miniforge3-26.7.2-0-Linux-x86_64.sh`. Its official checksum file matches SHA-256 `281b0ac7d550802efc81af633225a5e6116d29ae72f3ab4eae7168c3931a4c05`. Those bytes are at `installer/miniforge.sh` and are gitignored. They were not compared with the SIF.

The jaxlib build is the CUDA 12.6 variant named by that build string. The cuda-version metapackage is 12.9. Another jax 0.6.0 or jaxlib 0.6.0 distribution can use a different CUDA stack. The declared SIF tag `nvidia/cuda:12.2.2-base-ubuntu22.04` remains SIF metadata, not `BASE_IMAGE`. Matching installed metadata is not a reproducible build.

## How the base is built, identified, and consumed

`Dockerfile.base` pins `docker.io/library/ubuntu:22.04` at the linux/amd64 OCI image manifest `sha256:98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04`. The tag also has an OCI image index, `sha256:0a9887931b0e38af2e3487ed39e29436b0535c4f3f52e57745baaf79d1898956`, which is a different digest and is not the `FROM` pin. The image config blob is a third digest. Its verified config says `os=linux`, `architecture=amd64`, and version label `22.04`. It does not record a glibc version. Ubuntu `python3` runs the lock checker before conda exists. It is not the BindCraft interpreter. `check-conda-lock.sh` compares version and build constraints in `conda-bindcraft.depends.tsv`. A missing or comment-only file stops the build with `base_lock_dependencies_unverified`. A second record for the same name is `base_lock_conflict`. Python `3.14.7` is not a BindCraft row. Pip rows are reported and do not close a conda dependency. Virtual packages stay unresolved. They are not replaced with dummy records.

The virtual constraints in the BindCraft dependency inventory are:

- `__glibc >=2.17,<3.0.a0`, `__glibc >=2.28,<3.0.a0`, and `__glibc >=2.34`. These are host-libc constraints. They can be checked by reading libc from the pinned Ubuntu root filesystem. The registry config does not contain that version, so this recipe does not mark them satisfied.
- `__linux` and `__unix`. These name the Linux platform. The pinned image is `linux/amd64`. They are not Ubuntu package versions, and the checker still reports them.
- `__cuda`, required by jaxlib. Ubuntu 22.04 does not provide it. It requires the Linux NVIDIA driver or GPU runtime. It remains unresolved.

When both gates pass, comment lines are removed and conda is invoked only as:

`conda create --no-deps --override-channels --strict-channel-priority --channel conda-forge --file conda-explicit.txt`

`--no-deps` keeps the solver from selecting a newer build. `MINIFORGE_SHA256` is the verified installer checksum. The installer bytes are not in git. `ca-certificates` and `bzip2` are the installer prerequisites from Ubuntu and are not scientific pins.

`docker image inspect` field `Id` is the local image ID. A local build does not fill `RepoDigests`. Push the image to a registry, then read `RepoDigests`. The governed `Dockerfile` takes that `repository@sha256` value as `BASE_IMAGE`. It still requires the pin arguments, `assert_intended_runtime_layout`, and the `ldd` check. `UNRESOLVED_BLOCKERS` stays in place.

The historical Apptainer run is evidence that this SIF executed on the host. It does not establish Docker compatibility with driver `535.288.01`. `host_cuda_compatibility_unverified` remains.

## Remaining external assets

The labeled inventory has been applied. Do not collect it again.

- PyRosetta wheel `pyrosetta-2026.29+releasequarterly.80a0635615-cp310-cp310-linux_x86_64.whl`, 1667097173 bytes, from `https://west.rosettacommons.org/pyrosetta/quarterly/release.cxx11thread.serialization/`. It is the only Linux x86_64 CPython 3.10 file for that version in that index. The wheel metadata tag is `cp310-cp310-linux_x86_64`. The index directory is the official cxx11thread serialization build. No upstream SHA-256 is published. The locally computed SHA-256 is `4383d8d1a14fd3aff52983de936908791cc77bc6ac418e3bc53bb963a42c5242`. The zip is readable, and its metadata version matches. `pyrosetta/rosetta.so` is an x86_64 shared object that needs `libz.so.1`, `libstdc++.so.6`, `libm.so.6`, `libgcc_s.so.1`, `libc.so.6`, and `ld-linux-x86-64.so.2`. The highest observed symbol versions are GLIBC 2.27, GLIBCXX 3.4.22, and CXXABI 1.3.11. That inspection does not verify the wheel against the Ubuntu base, so `pyrosetta_abi_unverified` and `wheel_abi_verified: false` stay in place. The download URL returned HTTP 200 without a credential challenge. License terms were not accepted. The bytes are gitignored under `deploy/pdl1-bindcraft/vendor/`.
- AlphaFold parameter archive `alphafold_params_2022-12-06.tar`, 5587968000 bytes, from `https://storage.googleapis.com/alphafold/alphafold_params_2022-12-06.tar`. The locally computed SHA-256 is `36d4b0220f3c735f3296d301152b738c9776d16981d054845a68a1370b26cfe3`. Upstream does not publish a SHA-256, so `alphafold_params_sha256_unpublished` stays set. The locally computed MD5 `5cc59a561e80601862948ff36c6b040b` matches the published ETag and `x-goog-hash` MD5. The archive is readable and contains the sixteen AlphaFold parameter members. The bytes are gitignored under `deploy/pdl1-bindcraft/vendor/`.
- Registry repository name and `RepoDigests` value for the built base.
- Approved `colabdesign_commit`, `cuda_version`, and `host_nvidia_driver`. Copying these observations does not approve them.
- Host driver compatibility for jaxlib's `__cuda` dependency and build `cuda126py310hec873cc_200`.

`Dockerfile.base` does not copy or hash the PyRosetta wheel or the AlphaFold archive. The governed `Dockerfile` requires both through `PYROSETTA_WHEEL_FILENAME`, `PYROSETTA_WHEEL_SHA256`, `ALPHAFOLD_PARAMS_SHA256`, and `COPY deploy/pdl1-bindcraft/vendor/`.
