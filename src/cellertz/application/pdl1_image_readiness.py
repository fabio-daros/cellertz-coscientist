"""Pins for a future PDL1 BindCraft image. This module does not build it.

The recorded BindCraft commit and the public origins below come from
the files at that commit and from the AlphaFold download script. A
base-image digest, a host NVIDIA driver, a CUDA version, a ColabDesign
commit, a PyRosetta wheel, and the AlphaFold parameter SHA-256 are not
published as pins. They stay empty until the operator supplies them.
Nothing here contacts a registry, Docker, or a weight server.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

BINDCRAFT_REPOSITORY = "https://github.com/martinpacesa/BindCraft"
BINDCRAFT_COMMIT = "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d"
COLABDESIGN_REPOSITORY = "https://github.com/sokrypton/ColabDesign"
PYROSETTA_FIND_LINKS = (
    "https://west.rosettacommons.org/pyrosetta/quarterly/release.cxx11thread.serialization"
)
ALPHAFOLD_PARAMS_URL = "https://storage.googleapis.com/alphafold/alphafold_params_2022-12-06.tar"
ALPHAFOLD_PARAMS_NAME = "alphafold_params_2022-12-06.tar"
CUDA_COMPATIBILITY_URL = (
    "https://docs.nvidia.com/deploy/cuda-compatibility/latest/minor-version-compatibility.html"
)
IMAGE_ENTRYPOINT = "pdl1-entrypoint"
SCHEMA = "cellertz.pdl1_image_pins.v1"
SCIENTIFIC_RUN_EXECUTION = "disabled"
DOCKERFILE_LAYOUT = Path("deploy/pdl1-bindcraft/Dockerfile")
VENDOR_LAYOUT = Path("deploy/pdl1-bindcraft/vendor")
WHEEL_ABI_VERIFIED = False

# install_bindcraft.sh at BINDCRAFT_COMMIT. These are the published tokens.
# A package without a bound is not given one here.
BINDCRAFT_PYTHON = "3.10"
# No python3.10 interpreter is available in this workspace. A developer
# interpreter at 3.12 or newer is not this check, and this constant does
# not make the project support 3.10.
PYTHON_310_DYNAMIC_IMPORT = "pending"
BINDCRAFT_CONDA_PACKAGES = (
    "pip",
    "pandas",
    "matplotlib",
    "numpy<2.0.0",
    "biopython",
    "scipy",
    "pdbfixer",
    "seaborn",
    "libgfortran5",
    "tqdm",
    "jupyter",
    "ffmpeg",
    "fsspec",
    "py3dmol",
    "chex",
    "dm-haiku",
    "flax<0.10.0",
    "dm-tree",
    "joblib",
    "ml-collections",
    "immutabledict",
    "optax",
)
BINDCRAFT_JAX = "jax>=0.4,<=0.6.0"
BINDCRAFT_JAXLIB_CUDA = "jaxlib>=0.4,<=0.6.0=*cuda*"
BINDCRAFT_CUDA_PACKAGES = ("cuda-nvcc", "cudnn")
# cuda-version is the conda metapackage observed in the historical SIF.
# It is not an approved cuda_version pin.
OBSERVED_CUDA_VERSION_PACKAGE = "cuda-version"
_LOCK_LINE = re.compile(r"^[a-z0-9_][a-z0-9._+-]*=[A-Za-z0-9._+!-]+=[A-Za-z0-9._+-]+$")
# The script's non-cuda branch installs jaxlib without =*cuda* and omits
# cuda-nvcc and cudnn from that install line. required_packages still names
# both. This module does not choose a resolution.
CELLERTZ_ENTRYPOINT_MODULES = (
    "csv",
    "io",
    "json",
    "os",
    "re",
    "runpy",
    "stat",
    "subprocess",
    "sys",
)
CELLERTZ_WORKER_IMPORT = "numpy"

# Import names for the conda packages above. Bounded modules also carry
# the published comparison. Unpinned modules are presence checks only.
PRESENT_MODULES = (
    "pandas",
    "matplotlib",
    "Bio",
    "scipy",
    "pdbfixer",
    "seaborn",
    "tqdm",
    "fsspec",
    "py3dmol",
    "chex",
    "haiku",
    "tree",
    "joblib",
    "ml_collections",
    "immutabledict",
    "optax",
)
REQUIRED_EXECUTABLES = ("python", "pip", "git", "sha256sum", "ffmpeg", "jupyter", "nvcc")
REQUIRED_LIBRARIES = ("gfortran", "cudnn")
FORTRAN_SONAME = "libgfortran.so.5"
INTENDED_PYTHON = Path("/opt/conda/envs/BindCraft/bin/python")
INTENDED_LIBRARY_DIRECTORIES = (
    "/opt/conda/envs/BindCraft/lib",
    "/opt/conda/lib",
)
DALPHABALL = Path("/opt/BindCraft/functions/DAlphaBall.gcc")
UNRESOLVED_BLOCKERS = (
    "pyrosetta_abi_unverified",
    "jaxlib_cuda_build_unverified",
    "cuda_nvcc_version_unpinned",
    "cudnn_version_unpinned",
    "libgfortran5_version_unpinned",
    "host_cuda_compatibility_unverified",
    "colabdesign_commit_unpinned_by_bindcraft",
    "alphafold_params_sha256_unpublished",
    "unpinned_conda_packages_unresolved",
    "bindcraft_required_packages_cuda_inconsistency",
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^[0-9]{1,4}(?:\.[0-9]{1,4}){1,3}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")
_DISTRIBUTION = re.compile(r"^[a-z0-9_]+$")
_WHEEL_VERSION = re.compile(r"^[0-9A-Za-z_.+]+$")
_BUILD_TAG = re.compile(r"^[0-9][A-Za-z0-9_]*$")
_WHEEL_TAG = re.compile(r"^[a-z0-9_.]+$")
_FIELDS = (
    "schema",
    "base_image_repository",
    "base_image_digest",
    "host_nvidia_driver",
    "cuda_version",
    "colabdesign_commit",
    "pyrosetta_wheel_filename",
    "pyrosetta_wheel_sha256",
    "alphafold_params_sha256",
)
_CHECKS = (
    ("base_image_repository", "base_image_repository_required", _REPOSITORY),
    ("base_image_digest", "base_image_digest_required", _DIGEST),
    ("host_nvidia_driver", "host_nvidia_driver_required", _VERSION),
    ("cuda_version", "cuda_version_required", _VERSION),
    ("colabdesign_commit", "colabdesign_commit_required", _COMMIT),
    ("pyrosetta_wheel_sha256", "pyrosetta_wheel_sha256_required", _SHA256),
    ("alphafold_params_sha256", "alphafold_params_sha256_required", _SHA256),
)
# lower bound inclusive, upper bound exclusive unless upper_inclusive.
_BOUNDS: dict[str, tuple[tuple[int, int, int] | None, tuple[int, int, int], bool]] = {
    "numpy": (None, (2, 0, 0), False),
    "flax": (None, (0, 10, 0), False),
    "jax": ((0, 4, 0), (0, 6, 0), True),
    "jaxlib": ((0, 4, 0), (0, 6, 0), True),
}
_DISTRIBUTION_NAME = {"numpy": "numpy", "flax": "flax", "jax": "jax", "jaxlib": "jaxlib"}
_DOCKERFILE_MARKERS = (
    "COPY deploy/pdl1-bindcraft/vendor/ /tmp/vendor/",
    'python -m pip install --no-deps "/tmp/vendor/${PYROSETTA_WHEEL_FILENAME}"',
    "assert_base_environment",
    "assert_post_install",
    "assert_bindcraft_files",
)


class ImageReadinessError(Exception):
    """The image is not ready to build. ``code`` is the stable distinction."""

    def __init__(self, code: str, missing: tuple[str, ...] = ()) -> None:
        self.code = code
        self.missing = missing
        super().__init__(code)


@dataclass(frozen=True)
class ImagePins:
    """Operator-supplied values that this repository does not invent."""

    base_image_repository: str
    base_image_digest: str
    host_nvidia_driver: str
    cuda_version: str
    colabdesign_commit: str
    pyrosetta_wheel_filename: str
    pyrosetta_wheel_sha256: str
    alphafold_params_sha256: str


@dataclass(frozen=True)
class ImageBuildPlan:
    """Arguments for a later operator build. This object does not run them."""

    argv: tuple[str, ...]
    bindcraft_commit: str
    entrypoint: str
    host_acceptance_complete: bool
    scientific_run_execution: str
    wheel_abi_verified: bool
    unresolved_blockers: tuple[str, ...]


@dataclass(frozen=True)
class EnvironmentSnapshot:
    """Facts a caller observed. The checker does not import or execute them."""

    python: tuple[int, int]
    imports: Mapping[str, str | None]
    executables: frozenset[str]
    libraries: frozenset[str]


@dataclass(frozen=True)
class IntendedRuntimeLayout:
    """BindCraft conda layout. A stock CUDA image does not provide it."""

    python: Path
    library_directories: tuple[Path, ...]
    fortran_soname: str = FORTRAN_SONAME

    @staticmethod
    def production() -> IntendedRuntimeLayout:
        return IntendedRuntimeLayout(
            python=INTENDED_PYTHON,
            library_directories=tuple(Path(path) for path in INTENDED_LIBRARY_DIRECTORIES),
        )


def prepend_library_path(
    existing: str | None,
    directories: Sequence[str] = INTENDED_LIBRARY_DIRECTORIES,
) -> str:
    """Put the intended library directories first and keep every existing entry."""

    current = [part for part in (existing or "").split(":") if part]
    prefix = [directory for directory in directories if directory not in current]
    return ":".join([*prefix, *current])


def unresolved_ldd_libraries(ldd_output: str) -> tuple[str, ...]:
    """Return libraries ``ldd`` reported missing. A zero exit status is not enough."""

    missing: list[str] = []
    for line in ldd_output.splitlines():
        if "not found" not in line:
            continue
        name = line.split("=>", 1)[0].strip()
        missing.append(name or line.strip())
    return tuple(missing)


def assert_intended_runtime_layout(layout: IntendedRuntimeLayout | None = None) -> None:
    """Require the BindCraft interpreter and ``libgfortran.so.5`` in its library dirs.

    The production paths are absolute. Callers building a fixture pass another
    layout. This does not search an arbitrary base image for conda.
    """

    chosen = IntendedRuntimeLayout.production() if layout is None else layout
    missing: list[str] = []
    if not _executable_python(chosen.python):
        missing.append("python_layout_missing")
    soname_found = False
    for directory in chosen.library_directories:
        if directory.is_symlink() or not directory.is_dir():
            missing.append("library_directory_missing")
            continue
        if (directory / chosen.fortran_soname).exists():
            soname_found = True
    if not soname_found:
        missing.append("libgfortran_so_5_missing")
    if missing:
        raise ImageReadinessError("base_environment_incomplete", tuple(missing))


def assert_dalphaball_libraries(
    binary: Path = DALPHABALL,
    *,
    base_environment: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> None:
    """Run ``ldd`` with the intended library path and reject any ``not found`` line.

    ``ldd`` can exit 0 while a needed library is unresolved. The output is the
    check. This does not execute ``DAlphaBall.gcc`` or BindCraft.
    """

    if binary.is_symlink() or not binary.is_file():
        raise ImageReadinessError("base_environment_incomplete", ("DAlphaBall.gcc",))
    source = os.environ if base_environment is None else base_environment
    environment = {
        key: value for key, value in source.items() if type(key) is str and type(value) is str
    }
    environment["LD_LIBRARY_PATH"] = prepend_library_path(environment.get("LD_LIBRARY_PATH"))
    execute = subprocess.run if runner is None else runner
    completed = execute(
        ["ldd", os.fspath(binary)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    missing = unresolved_ldd_libraries(f"{completed.stdout}\n{completed.stderr}")
    if completed.returncode != 0 or missing:
        raise ImageReadinessError("base_environment_incomplete", missing or ("ldd_failed",))


def _executable_python(path: Path) -> bool:
    target = path.resolve() if path.is_symlink() else path
    return target.is_file() and not target.is_symlink() and os.access(target, os.X_OK)


def conda_package_name(token: str) -> str:
    """Return the conda name in a published install token, without a version bound."""

    match = re.match(r"^[_A-Za-z0-9][_A-Za-z0-9._+-]*", token.strip())
    if match is None:
        return ""
    return match.group(0)


def required_conda_names() -> tuple[str, ...]:
    """Names the base lock must pin exactly. Version bounds are not build strings."""

    tokens = (
        *BINDCRAFT_CONDA_PACKAGES,
        BINDCRAFT_JAX,
        BINDCRAFT_JAXLIB_CUDA,
        *BINDCRAFT_CUDA_PACKAGES,
        "python",
        OBSERVED_CUDA_VERSION_PACKAGE,
    )
    names: list[str] = []
    for token in tokens:
        name = conda_package_name(token)
        if name and name not in names:
            names.append(name)
    return tuple(names)


def parse_exact_lock(text: str) -> dict[str, str]:
    """Parse ``name=version=build`` lines. A bare name or floating version is rejected."""

    pins: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _LOCK_LINE.fullmatch(line) is None or line.count("=") != 2:
            raise ImageReadinessError("base_lock_unconstrained", (f"line_{line_number}",))
        name, spec = line.split("=", 1)
        if name in pins:
            raise ImageReadinessError("base_lock_unconstrained", (name,))
        pins[name] = spec
    return pins


def missing_conda_lock_names(pins: Mapping[str, str]) -> tuple[str, ...]:
    """Required names that do not yet have an exact historical build."""

    return tuple(name for name in required_conda_names() if name not in pins)


def depend_name(spec: str) -> str:
    """Return the package token in one conda dependency spec."""

    token = spec.strip().split(" ", 1)[0] if isinstance(spec, str) else ""
    return conda_package_name(token)


def dependency_gaps(
    packages: Mapping[str, Sequence[str]],
    *,
    pip_names: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Names required by installed records and absent from those records.

    A comment that the closure was recorded is not an argument. Pip-installed
    names may satisfy a dependency only when they are passed explicitly.
    Names starting with ``__`` are conda virtual packages.
    """

    installed = set(packages)
    gaps: list[str] = []
    for name in packages:
        for spec in packages[name]:
            required = depend_name(spec)
            if not required or required.startswith("__"):
                continue
            if required in installed or required in pip_names:
                continue
            if required not in gaps:
                gaps.append(required)
    return tuple(gaps)


def wheel_filename_accepted(name: str) -> bool:
    """Return whether ``name`` has PEP 427 wheel shape.

    A true result does not prove that the tags match this image's Python,
    ABI, or platform. ``WHEEL_ABI_VERIFIED`` stays false.
    """

    if type(name) is not str or not name.endswith(".whl") or "/" in name or ".." in name:
        return False
    parts = name[: -len(".whl")].split("-")
    if len(parts) == 5:
        distribution, version, python_tag, abi_tag, platform_tag = parts
        build = None
    elif len(parts) == 6:
        distribution, version, build, python_tag, abi_tag, platform_tag = parts
    else:
        return False
    if _DISTRIBUTION.fullmatch(distribution) is None or _WHEEL_VERSION.fullmatch(version) is None:
        return False
    if build is not None and _BUILD_TAG.fullmatch(build) is None:
        return False
    return all(_WHEEL_TAG.fullmatch(tag) is not None for tag in (python_tag, abi_tag, platform_tag))


def pin_failures(document: Mapping[str, object]) -> tuple[str, ...]:
    """Name every absent or malformed pin. An empty result is not a host pass."""

    if type(document) is not dict:
        return ("image_pins_rejected",)
    unknown = tuple(sorted(set(document) - set(_FIELDS)))
    if unknown or document.get("schema") != SCHEMA:
        return ("image_pins_rejected",)
    failures: list[str] = []
    for name, code, pattern in _CHECKS:
        value = document.get(name)
        if type(value) is not str or pattern.fullmatch(value) is None or ".." in value.split("/"):
            failures.append(code)
    filename = document.get("pyrosetta_wheel_filename")
    if not wheel_filename_accepted(filename if type(filename) is str else ""):
        failures.append("pyrosetta_wheel_required")
    return tuple(failures)


def require_pins(document: Mapping[str, object]) -> ImagePins:
    """Reject an incomplete pin document before any build argument exists."""

    failures = pin_failures(document)
    if failures:
        raise ImageReadinessError(
            "image_pins_incomplete" if failures != ("image_pins_rejected",) else failures[0],
            failures,
        )
    return ImagePins(
        base_image_repository=str(document["base_image_repository"]),
        base_image_digest=str(document["base_image_digest"]),
        host_nvidia_driver=str(document["host_nvidia_driver"]),
        cuda_version=str(document["cuda_version"]),
        colabdesign_commit=str(document["colabdesign_commit"]),
        pyrosetta_wheel_filename=str(document["pyrosetta_wheel_filename"]),
        pyrosetta_wheel_sha256=str(document["pyrosetta_wheel_sha256"]),
        alphafold_params_sha256=str(document["alphafold_params_sha256"]),
    )


def build_plan(
    document: Mapping[str, object],
    *,
    wheel: Path,
    params: Path,
    docker_host: str,
    docker_root: str,
    root: Path = Path("."),
    dockerfile: Path = DOCKERFILE_LAYOUT,
) -> ImageBuildPlan:
    """Return build arguments only after vendor files sit in the build context.

    ``docker_host`` is required. The command names that socket and does not
    fall back to the daemon's default socket.
    """

    from cellertz.application.pdl1_docker import explicit_docker_argv, require_docker_endpoint

    endpoint = require_docker_endpoint(docker_host, docker_root)
    pins = require_pins(document)
    root_resolved = root.resolve()
    dockerfile_file = _require_dockerfile(root_resolved, dockerfile)
    _require_vendor_member(wheel, root_resolved, pins.pyrosetta_wheel_filename)
    _require_vendor_member(params, root_resolved, ALPHAFOLD_PARAMS_NAME)
    _require_hash(wheel, pins.pyrosetta_wheel_sha256, "pyrosetta_hash_mismatch")
    _require_hash(params, pins.alphafold_params_sha256, "alphafold_params_hash_mismatch")
    reference = f"{pins.base_image_repository}@{pins.base_image_digest}"
    argv = tuple(
        explicit_docker_argv(
            endpoint,
            [
                "docker",
                "build",
                "--file",
                str(dockerfile_file),
                "--build-arg",
                f"BASE_IMAGE={reference}",
                "--build-arg",
                f"COLABDESIGN_COMMIT={pins.colabdesign_commit}",
                "--build-arg",
                f"PYROSETTA_WHEEL_FILENAME={pins.pyrosetta_wheel_filename}",
                "--build-arg",
                f"PYROSETTA_WHEEL_SHA256={pins.pyrosetta_wheel_sha256}",
                "--build-arg",
                f"ALPHAFOLD_PARAMS_SHA256={pins.alphafold_params_sha256}",
                "--build-arg",
                f"HOST_NVIDIA_DRIVER={pins.host_nvidia_driver}",
                "--build-arg",
                f"CUDA_VERSION={pins.cuda_version}",
                str(root_resolved),
            ],
        )
    )
    return ImageBuildPlan(
        argv=argv,
        bindcraft_commit=BINDCRAFT_COMMIT,
        entrypoint=IMAGE_ENTRYPOINT,
        host_acceptance_complete=False,
        scientific_run_execution=SCIENTIFIC_RUN_EXECUTION,
        wheel_abi_verified=WHEEL_ABI_VERIFIED,
        unresolved_blockers=UNRESOLVED_BLOCKERS,
    )


def environment_failures(snapshot: EnvironmentSnapshot) -> tuple[str, ...]:
    """Name missing base-environment facts. This does not import BindCraft."""

    failures: list[str] = []
    if snapshot.python != (3, 10):
        failures.append("python_version_rejected")
    for name in PRESENT_MODULES + tuple(_BOUNDS):
        version = snapshot.imports.get(name)
        if version is None:
            failures.append(f"{name}_missing")
            continue
        if name not in _BOUNDS:
            continue
        parsed = _release(version)
        lower, upper, upper_inclusive = _BOUNDS[name]
        if parsed is None or not _in_range(parsed, lower, upper, upper_inclusive):
            failures.append(f"{name}_version_rejected")
    for name in REQUIRED_EXECUTABLES:
        if name not in snapshot.executables:
            failures.append(f"{name}_missing")
    if "gfortran" not in snapshot.libraries:
        failures.append("libgfortran5_missing")
    if "cudnn" not in snapshot.libraries:
        failures.append("cudnn_missing")
    return tuple(failures)


def assert_base_environment() -> None:
    """Fail the image build when the published base stack is incomplete.

    Sets a CPU JAX platform for this process only. Does not import or run
    ``bindcraft.py``. An empty failure list does not clear
    ``UNRESOLVED_BLOCKERS`` and does not enable scientific execution.
    """

    os.environ.setdefault("JAX_PLATFORMS", "cpu")
    failures = environment_failures(_observe_base_environment())
    if failures:
        raise ImageReadinessError("base_environment_incomplete", failures)
    import importlib

    importlib.import_module("cellertz.application.pdl1_container_entrypoint")


def assert_bindcraft_files(root: Path) -> None:
    """Require the checkout files to exist. Does not execute ``bindcraft.py``."""

    required = (
        root / "bindcraft.py",
        root / "settings_filters" / "no_filters.json",
        root / "functions" / "dssp",
        root / "functions" / "DAlphaBall.gcc",
    )
    missing: list[str] = []
    for path in required:
        if path.is_symlink() or not path.is_file():
            missing.append(path.name)
            continue
        if path.name in {"dssp", "DAlphaBall.gcc"} and path.stat().st_mode & 0o111 == 0:
            missing.append(path.name)
    if missing:
        raise ImageReadinessError("base_environment_incomplete", tuple(missing))


def assert_post_install() -> None:
    """Import the packages the install script imports after pip.

    Does not import or run ``bindcraft.py``, and does not call the
    Cellertz entrypoint.
    """

    import importlib

    missing: list[str] = []
    for name in ("pyrosetta", "colabdesign"):
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(f"{name}_missing")
    if missing:
        raise ImageReadinessError("base_environment_incomplete", tuple(missing))
    importlib.import_module("cellertz.application.pdl1_container_entrypoint")


def assert_wheel_filename(name: str) -> None:
    """Reject a wheel name that is not PEP 427 shape. This is not an ABI check."""

    if not wheel_filename_accepted(name):
        raise ImageReadinessError("pyrosetta_wheel_required")


def _require_dockerfile(root: Path, dockerfile: Path) -> Path:
    if dockerfile.as_posix() != DOCKERFILE_LAYOUT.as_posix():
        raise ImageReadinessError("vendor_layout_rejected")
    path = _real_layout_path(root, dockerfile)
    if not path.is_file():
        raise ImageReadinessError("vendor_layout_rejected")
    text = path.read_text(encoding="utf-8")
    if "pyrosetta.whl" in text or any(marker not in text for marker in _DOCKERFILE_MARKERS):
        raise ImageReadinessError("vendor_layout_rejected")
    for line in text.splitlines():
        if "bindcraft.py" in line and ("python" in line or "import" in line):
            raise ImageReadinessError("vendor_layout_rejected")
    return path


def _require_vendor_member(path: Path, root: Path, expected_name: str) -> None:
    context = _context(root)
    vendor = _real_layout_path(root, VENDOR_LAYOUT)
    if not vendor.is_dir() or path.is_symlink():
        raise ImageReadinessError("vendor_layout_rejected")
    try:
        lexical = path.absolute().relative_to(root.absolute())
    except ValueError:
        raise ImageReadinessError("vendor_layout_rejected") from None
    if ".." in lexical.parts or "." in lexical.parts:
        raise ImageReadinessError("vendor_layout_rejected")
    current = root
    for part in lexical.parts:
        current = current / part
        if current.is_symlink():
            raise ImageReadinessError("vendor_layout_rejected")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(context)
    except (OSError, ValueError):
        raise ImageReadinessError("vendor_layout_rejected") from None
    if resolved.parent != vendor or resolved.name != expected_name or not resolved.is_file():
        raise ImageReadinessError("vendor_layout_rejected")


def _context(root: Path) -> Path:
    if root.is_symlink():
        raise ImageReadinessError("vendor_layout_rejected")
    return root.resolve()


def _real_layout_path(root: Path, relative: Path) -> Path:
    context = _context(root)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ImageReadinessError("vendor_layout_rejected")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ImageReadinessError("vendor_layout_rejected")
    if not current.exists():
        raise ImageReadinessError("vendor_layout_rejected")
    resolved = current.resolve()
    try:
        resolved.relative_to(context)
    except ValueError:
        raise ImageReadinessError("vendor_layout_rejected") from None
    return resolved


def _require_hash(path: Path, expected: str, code: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ImageReadinessError(code)


def _observe_base_environment() -> EnvironmentSnapshot:
    import importlib
    import shutil
    import sys
    from ctypes.util import find_library
    from importlib import metadata

    imports: dict[str, str | None] = {}
    for name in PRESENT_MODULES + tuple(_BOUNDS):
        try:
            importlib.import_module(name)
        except ImportError:
            imports[name] = None
            continue
        distribution = _DISTRIBUTION_NAME.get(name)
        if distribution is None:
            imports[name] = ""
            continue
        try:
            imports[name] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            imports[name] = ""
    executables = frozenset(name for name in REQUIRED_EXECUTABLES if shutil.which(name))
    libraries = frozenset(name for name in REQUIRED_LIBRARIES if find_library(name))
    return EnvironmentSnapshot(
        python=(sys.version_info[0], sys.version_info[1]),
        imports=imports,
        executables=executables,
        libraries=libraries,
    )


def _release(text: str) -> tuple[int, int, int] | None:
    if type(text) is not str or text == "":
        return None
    parts: list[int] = []
    for piece in text.split(".")[:3]:
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        if digits == "":
            break
        parts.append(int(digits))
    if not parts:
        return None
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def _in_range(
    parsed: tuple[int, int, int],
    lower: tuple[int, int, int] | None,
    upper: tuple[int, int, int],
    upper_inclusive: bool,
) -> bool:
    if lower is not None and parsed < lower:
        return False
    if upper_inclusive:
        return parsed <= upper
    return parsed < upper
