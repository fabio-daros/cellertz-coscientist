#!/usr/bin/env bash
# Install mber-open beside the BindCraft SIF, self-contained in this directory.
#
# Nothing here touches ~/projects: the repository holds the recipe and the
# pinned commit, never the artefact, the same way deploy/pdl1-bindcraft holds
# BindCraft's recipe while the 13 GB SIF lives next door.
#
# Layout
#   bin/micromamba   the solver, a single static binary, no sudo, no base env
#   src/             the clone, at one pinned commit
#   env/             the environment, by prefix so it is not a named global
#   weights/         ~9 GB: AlphaFold2, NanoBodyBuilder2, ESM2
#   INSTALLED.txt    what was installed, from where, when
#
# Stages run separately on purpose. Each is slow and each fails differently.
#   ./install.sh clone     cheap: binary + source at the pinned commit
#   ./install.sh env       slow: solve and build the environment
#   ./install.sh weights   slow: ~9 GB from Google Storage, Zenodo, HuggingFace
#   ./install.sh verify    cheap: report what is present and what the GPU sees
set -euo pipefail

# Where the installation lives. Not derived from this script's location: the
# canonical copy is versioned in the repository, and the repository must never
# be an installation target.
ROOT="${MBER_ROOT:-/home/fabio/containers/cellertz/mber}"
COMMIT="5cc8e0534158647e9706b2655630f4449686c600"
REPO="https://github.com/manifoldbio/mber-open.git"
MAMBA="${ROOT}/bin/micromamba"
SRC="${ROOT}/src"
ENV_PREFIX="${ROOT}/env"
WEIGHTS="${ROOT}/weights"

log() { printf '\n== %s\n' "$*"; }

stage_clone() {
  mkdir -p "${ROOT}/bin"
  if [ ! -x "${MAMBA}" ]; then
    log "micromamba"
    # Single static binary. No base environment, no shell init, no sudo.
    curl -fsSL "https://micro.mamba.pm/api/micromamba/linux-64/latest" \
      | tar -xvj -C "${ROOT}/bin" --strip-components=1 bin/micromamba
    chmod +x "${MAMBA}"
  fi
  "${MAMBA}" --version

  log "mber-open at ${COMMIT:0:12}"
  if [ ! -d "${SRC}/.git" ]; then
    git clone --quiet "${REPO}" "${SRC}"
  fi
  git -C "${SRC}" fetch --quiet origin
  git -C "${SRC}" checkout --quiet "${COMMIT}"
  # Refuse to continue on anything but the pinned commit: an install whose
  # version is not the recorded one cannot be the recorded one.
  test "$(git -C "${SRC}" rev-parse HEAD)" = "${COMMIT}"
  echo "HEAD is ${COMMIT}"
}

stage_env() {
  test -d "${SRC}" || { echo "run 'clone' first" >&2; exit 1; }
  if [ ! -x "${ENV_PREFIX}/bin/python" ]; then
    log "environment (conda packages only)"
    # Their environment.yml pins the pip step to PyTorch's own index with
    # --index-url, which REPLACES PyPI rather than adding to it, so packages
    # like transformers resolve only if that index happens to carry them. The
    # conda half is created first and pip runs separately below.
    "${MAMBA}" create --yes --prefix "${ENV_PREFIX}" \
      --channel conda-forge --channel bioconda \
      python=3.11 pip 'openmm>=8.4.0' 'pdbfixer>=1.12' anarci ffmpeg
  fi

  log "mber"
  "${MAMBA}" run --prefix "${ENV_PREFIX}" python -m pip install -e "${SRC}"

  log "mber_protocols (this is what provides the mber-vhh command)"
  # A second install, and the README's own step. The root package declares no
  # console script; protocols/setup.py declares
  # mber-vhh=mber_protocols.stable.VHH_binder_design.cli:main.
  "${MAMBA}" run --prefix "${ENV_PREFIX}" python -m pip install -e "${SRC}/protocols"

  log "torch, from the CUDA 12.8 index"
  # Last on purpose, because the two installs above resolve torch from PyPI,
  # whose current build targets CUDA 13 and reports "driver too old" on this
  # host: the driver here is 535.288.01, which is CUDA 12.2. A cu12x build
  # runs on it under CUDA minor-version compatibility, and 12.8 is what mBER
  # was tested against. --index-url, not --extra, so PyPI cannot win again.
  "${MAMBA}" run --prefix "${ENV_PREFIX}" python -m pip install \
    --index-url https://download.pytorch.org/whl/cu128 \
    --force-reinstall torch torchvision torchaudio
}

stage_weights() {
  test -d "${SRC}" || { echo "run 'clone' first" >&2; exit 1; }
  test -x "${ENV_PREFIX}/bin/python" || { echo "run 'env' first" >&2; exit 1; }
  mkdir -p "${WEIGHTS}"
  log "weights into ${WEIGHTS}"
  # Through the environment, not the system python: the ESM2 step of their
  # script calls python with transformers, and outside the environment it
  # fails with ModuleNotFoundError while the script still exits 0. A quiet
  # 5 GB hole, which only shows up as a network fetch on the first run.
  #
  # The script takes the destination as a positional argument, so the ~9 GB
  # land beside the SIF instead of in ~/.mber. ESMFold (~16 GB) is not asked
  # for: nbb2 is the VHH folding model the protocol defaults to.
  "${MAMBA}" run --prefix "${ENV_PREFIX}" bash "${SRC}/download_weights.sh" "${WEIGHTS}"
  # Their script reports success whatever happened, so check the one piece it
  # can silently miss.
  if [ -z "$(find "${WEIGHTS}/huggingface" -name '*.safetensors' -o -name '*.bin' 2>/dev/null | head -1)" ]; then
    echo "ESM2 did not land in ${WEIGHTS}/huggingface" >&2
    exit 1
  fi
  echo "ESM2 present"
}

stage_configure() {
  log "numpy and matplotlib"
  # requirements.txt says numpy<2, and the torch reinstall above dragged 2.x
  # back in. AlphaFold and ColabDesign are the reason for that pin.
  #
  # matplotlib<3.9 is ours, and it is not cosmetic. matplotlib 3.9 removed
  # matplotlib.cm.get_cmap, which colabdesign 1.1.3 still calls in
  # shared/plot.py:117, reached from af_model.animate(). mBER calls animate()
  # in trajectory.py:480 inside _update_design_state **unconditionally**, so
  # --no-animations does not avoid it: every trajectory dies at teardown with
  # AttributeError after the design work is already done. Their requirements
  # pin neither package's upper bound.
  "${MAMBA}" run --prefix "${ENV_PREFIX}" python -m pip install --quiet \
    'numpy<2' 'matplotlib<3.9'

  log "~/.mber"
  # The weight paths are hardcoded in their source as ~/.mber/af_params,
  # ~/.mber/nbb2_weights, and no environment variable is read:
  #   models/folding/af2_model.py   data_dir='~/.mber/af_params'
  #   models/folding/nbb2_model.py  weights_dir="~/.mber/nbb2_weights"
  #   core/modules/config.py        af_params_dir = "~/.mber/af_params"
  # A link satisfies that default while the 9 GB stay beside the SIF. Patching
  # their files instead would mean the install is no longer the pinned commit.
  if [ -e "${HOME}/.mber" ] && [ ! -L "${HOME}/.mber" ]; then
    echo "refusing to replace ${HOME}/.mber: it exists and is not a link" >&2
    exit 1
  fi
  ln -sfn "${WEIGHTS}" "${HOME}/.mber"
  ls -ld "${HOME}/.mber"

  log "run wrapper"
  # download_weights.sh exports HF_HOME only for its own duration, so at run
  # time ESM2 would be fetched from the network instead of from disk. This
  # wrapper is also what a registered profile should put in argv.
  cat > "${ROOT}/mber-vhh.sh" <<WRAP
#!/usr/bin/env bash
# mber-vhh with this installation's environment and weights. Generated by
# install.sh; the platform's registered profile should name this file.
set -euo pipefail
export HF_HOME="${WEIGHTS}/huggingface"
exec "${MAMBA}" run --prefix "${ENV_PREFIX}" mber-vhh "\$@"
WRAP
  chmod +x "${ROOT}/mber-vhh.sh"
  echo "wrote ${ROOT}/mber-vhh.sh"
}

stage_smoke() {
  # Their own example, capped. The example's own settings say 100 accepted out
  # of 10000 trajectories, which is days of GPU.
  #
  #   ./install.sh smoke [max_trajectories] [num_accepted]   default 1 1
  #
  # num_accepted is a stopping condition, not a quota: the run ends at the
  # first acceptance, so a larger trajectory budget costs nothing when the
  # yield is good and bounds the loss when it is not. Measured cost is about
  # 8.8 minutes per trajectory on this card.
  local max_traj="${2:-1}"
  local n_accept="${3:-1}"
  #
  # The flag form is used rather than --settings: their documentation does not
  # say whether file keys and flags can be mixed, or which wins.
  local out="${ROOT}/smoke/$(date -u +%Y%m%dT%H%M%SZ)-t${max_traj}"
  local pdb="${SRC}/protocols/src/mber_protocols/stable/VHH_binder_design/examples/PDL1.pdb"
  test -f "${pdb}" || { echo "example PDB missing: ${pdb}" >&2; exit 1; }
  test -s "${WEIGHTS}/af_params/params_model_1_multimer_v3.npz" \
    || test -d "${WEIGHTS}/af_params" \
    || { echo "weights absent; run 'weights' first" >&2; exit 1; }
  mkdir -p "${out}"
  log "smoke run into ${out}"
  # A56 is their example hotspot, and it is Tyr56, one of the 22 PD-L1
  # residues PD-1 contacts in 4ZQK. Their example PDB is chain A numbered 18
  # to 132, the same frame as the registered target file, so that number means
  # the same residue in both.
  "${ROOT}/mber-vhh.sh" \
    --input-pdb "${pdb}" \
    --output-dir "${out}" \
    --chains A \
    --target-name PDL1 \
    --hotspots A56 \
    --num-accepted "${n_accept}" \
    --max-trajectories "${max_traj}" \
    --no-animations --no-pickle --no-png
  log "what it wrote"
  find "${out}" -maxdepth 2 | head -30
  if [ -f "${out}/accepted.csv" ]; then
    log "accepted.csv header, against what MBERParser expects"
    head -2 "${out}/accepted.csv"
  else
    echo "no accepted.csv at all"
  fi
}

stage_verify() {
  log "sizes"
  du -sh "${SRC}" "${ENV_PREFIX}" "${WEIGHTS}" 2>/dev/null || true
  log "versions"
  "${MAMBA}" run --prefix "${ENV_PREFIX}" python - <<'PY' || true
import importlib.metadata as md
for name in ("mber", "torch", "jax", "colabdesign", "transformers", "numpy"):
    try:
        print(f"{name:14} {md.version(name)}")
    except Exception as error:
        print(f"{name:14} MISSING ({type(error).__name__})")
try:
    import torch
    print("torch.cuda.is_available:", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("device:", torch.cuda.get_device_name(0))
except Exception as error:
    print("torch import failed:", error)
try:
    import jax
    print("jax devices:", jax.devices())
except Exception as error:
    print("jax device query failed:", type(error).__name__, error)
PY
  log "cli"
  "${MAMBA}" run --prefix "${ENV_PREFIX}" mber-vhh -h 2>&1 | head -20 || true
}

case "${1:-}" in
  clone)     stage_clone ;;
  env)       stage_env ;;
  weights)   stage_weights ;;
  configure) stage_configure ;;
  smoke)     stage_smoke "$@" ;;
  verify)    stage_verify ;;
  *) echo "usage: $0 {clone|env|weights|configure|smoke|verify}" >&2; exit 2 ;;
esac
