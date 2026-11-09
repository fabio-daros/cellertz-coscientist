"""Container-side adapter for one governed PDL1 BindCraft attempt.

The Docker executor stages three read-only files and passes the manifest
``argv_template`` as the container command. This module is that command.
It does not start Docker, download weights, or launch BindCraft on import.

Scientific values are not invented here. Seed, length bounds, design
count, and the filter path must all be present on argv. Those tokens are
the existing immutable container command: readiness requires
``manifest.argv_template == deployment.argv``, and that template is
covered by the manifest digest. Path relocation of ``starting_pdb`` and
``design_path`` is operational. It points the unchanged target bytes at
the read-only mount and the design tree at the writable workspace.

The host executor compares ``--n-designs``, both length bounds,
``--cellertz-seed``, and ``--filters`` with the approved revision before
``docker create``. ``seed`` is an integer constraint. ``filter_policy``
is the closed string ``no_filters``, mapped only to this image path.
A revision that omitted either value keeps its digest and cannot be
launched by this adapter. Manifest argv equality is not that check.
Design count is 1 through 64, each length is 1 through 2147483647 with
the minimum at most the maximum, and seed is 0 through 4294967295.
The host uses these same bounds.

Seed mechanism, pinned BindCraft ``efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d``
``bindcraft.py``: the trajectory seed is
``np.random.randint(0, high=999999)`` and the binder length is
``np.random.choice`` over the settings range. There is no seed flag.
``np.random.seed`` affects that draw only in the same process, so the
child process seeds NumPy and then ``runpy`` runs BindCraft. This does
not seed JAX and does not claim GPU bitwise reproducibility.

Image-build pins and Linux checks that this module does not select:
a base-image digest, the host CUDA version, a ColabDesign commit, a
PyRosetta wheel digest, and the SHA-256 of
``alphafold_params_2022-12-06.tar``. The writable layer must be measured
on a Linux NVIDIA host before a real attempt, because the executor sets
``--storage-opt size`` equal to ``max_output_bytes``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import runpy
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cellertz.application.pdl1_image_readiness import prepend_library_path

# Same path as bindcraft_apptainer.CONTAINER_NO_FILTERS. The only filter
# policy this adapter will launch.
IMAGE_NO_FILTERS = "/opt/BindCraft/settings_filters/no_filters.json"
IMAGE_BINDCRAFT_SCRIPT = "/opt/BindCraft/bindcraft.py"
CONTAINER_TARGET = "/cellertz/input/target.pdb"
CONTAINER_SETTINGS = "/cellertz/input/settings.json"
CONTAINER_ADVANCED = "/cellertz/input/advanced.json"
CONTAINER_DESIGN_RECORDS = "/cellertz/output/design_records.csv"
CONTAINER_WORKSPACE = "/tmp/cellertz-pdl1"
STATS_FILENAME = "final_design_stats.csv"
AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
_MAX_RECORD_BYTES = 1_073_741_824
_READ_CHUNK = 65536
_MAX_DATA_ROWS = 64
DESIGN_COUNT_MIN = 1
DESIGN_COUNT_MAX = _MAX_DATA_ROWS
LENGTH_MIN = 1
LENGTH_MAX = 2**31 - 1
SEED_MIN = 0
SEED_MAX = 2**32 - 1
_DETAIL_LIMIT = 160
_ASCII_INTEGER = re.compile(r"^(0|[1-9][0-9]{0,9})$")
_FLAGS = (
    "--cellertz-seed",
    "--length-min",
    "--length-max",
    "--n-designs",
    "--filters",
)
_REQUIRED_SETTINGS = (
    "binder_name",
    "chains",
    "design_path",
    "lengths",
    "number_of_final_designs",
    "starting_pdb",
    "target_hotspot_residues",
)


class Pdl1EntrypointError(Exception):
    """Bounded entrypoint failure. ``code`` is the stable distinction."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail[:_DETAIL_LIMIT]
        super().__init__(code)


class ProcessRunner(Protocol):
    def __call__(self, argv: Sequence[str], env: Mapping[str, str]) -> int: ...


@dataclass(frozen=True)
class ContainerLayout:
    """Staged inputs, the executor's CSV path, and the writable workspace."""

    target_pdb: Path
    settings_json: Path
    advanced_json: Path
    design_records: Path
    workspace: Path

    @staticmethod
    def production() -> ContainerLayout:
        return ContainerLayout(
            target_pdb=Path(CONTAINER_TARGET),
            settings_json=Path(CONTAINER_SETTINGS),
            advanced_json=Path(CONTAINER_ADVANCED),
            design_records=Path(CONTAINER_DESIGN_RECORDS),
            workspace=Path(CONTAINER_WORKSPACE),
        )


@dataclass(frozen=True)
class ImageLayout:
    """Binaries and the filter file owned by the image, not by the mount."""

    bindcraft_script: Path
    filters: Path
    python: str

    @staticmethod
    def production() -> ImageLayout:
        return ImageLayout(
            bindcraft_script=Path(IMAGE_BINDCRAFT_SCRIPT),
            filters=Path(IMAGE_NO_FILTERS),
            python=sys.executable,
        )


@dataclass(frozen=True)
class Pdl1ContainerPolicy:
    """Scientific overrides taken from the container command. No defaults.

    ``hotspot`` is absent on the container argv. None keeps the residue
    list already stored in the staged settings. An empty string writes
    ``target_hotspot_residues`` as ``""``, which installed BindCraft
    converts to None. Any other string replaces that field.
    """

    seed: int
    length_min: int
    length_max: int
    n_designs: int
    filters: str
    hotspot: str | None = None


@dataclass(frozen=True)
class DerivedConfiguration:
    settings_path: Path
    design_path: Path
    advanced_path: Path


def parse_policy(argv: Sequence[str], *, filters_path: str) -> Pdl1ContainerPolicy:
    """Require every scientific override. Reject unknown tokens."""

    if type(argv) is not list or any(type(item) is not str for item in argv):
        raise Pdl1EntrypointError("malformed_value", "argv must be a list of strings")
    if type(filters_path) is not str or not filters_path:
        raise Pdl1EntrypointError("filter_policy_rejected", "image filter path is empty")
    values: dict[str, str] = {}
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag not in _FLAGS:
            raise Pdl1EntrypointError("unsupported_argument", _brief(flag))
        if flag in values:
            raise Pdl1EntrypointError("unsupported_argument", f"duplicate {flag}")
        if index + 1 >= len(argv):
            raise Pdl1EntrypointError("malformed_value", f"missing value for {flag}")
        values[flag] = argv[index + 1]
        index += 2
    missing = [flag for flag in _FLAGS if flag not in values]
    if missing:
        raise Pdl1EntrypointError("policy_incomplete", "missing " + " ".join(missing))
    filters = values["--filters"]
    if filters != filters_path or ".." in filters.split("/"):
        raise Pdl1EntrypointError(
            "filter_policy_rejected",
            "filters path is not the image no_filters policy",
        )
    policy = Pdl1ContainerPolicy(
        seed=_integer(values["--cellertz-seed"], minimum=SEED_MIN, maximum=SEED_MAX),
        length_min=_integer(values["--length-min"], minimum=LENGTH_MIN, maximum=LENGTH_MAX),
        length_max=_integer(values["--length-max"], minimum=LENGTH_MIN, maximum=LENGTH_MAX),
        n_designs=_integer(
            values["--n-designs"],
            minimum=DESIGN_COUNT_MIN,
            maximum=DESIGN_COUNT_MAX,
        ),
        filters=filters,
    )
    if policy.length_min > policy.length_max:
        raise Pdl1EntrypointError("malformed_value", "length_min exceeds length_max")
    return policy


def derive_settings(layout: ContainerLayout, policy: Pdl1ContainerPolicy) -> DerivedConfiguration:
    """Write a derived settings file under the workspace. Sources stay put."""

    if policy.length_min > policy.length_max:
        raise Pdl1EntrypointError("malformed_value", "length_min exceeds length_max")
    source_path = layout.settings_json
    try:
        source = source_path.read_bytes() if _is_file(source_path) else None
    except OSError:
        raise Pdl1EntrypointError(
            "settings_unreadable",
            "staged settings could not be read",
        ) from None
    if source is None:
        raise Pdl1EntrypointError("settings_unreadable", "staged settings are missing")
    try:
        _reject_workspace_overlap(layout)
    except OSError:
        raise Pdl1EntrypointError(
            "settings_unreadable",
            "staged paths could not be checked",
        ) from None
    try:
        text = source.decode("utf-8")
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, Pdl1EntrypointError):
        raise Pdl1EntrypointError(
            "settings_invalid",
            "staged settings are not one JSON object",
        ) from None
    if type(parsed) is not dict or any(name not in parsed for name in _REQUIRED_SETTINGS):
        raise Pdl1EntrypointError("settings_invalid", "staged settings lack BindCraft fields")
    try:
        if source_path.read_bytes() != source:
            raise Pdl1EntrypointError("settings_invalid", "staged settings changed while reading")
    except OSError:
        raise Pdl1EntrypointError(
            "settings_unreadable",
            "staged settings could not be read",
        ) from None
    design_path = layout.workspace / "designs"
    derived = dict(parsed)
    derived["starting_pdb"] = str(layout.target_pdb)
    derived["design_path"] = str(design_path)
    derived["lengths"] = [policy.length_min, policy.length_max]
    derived["number_of_final_designs"] = policy.n_designs
    if policy.hotspot is not None:
        if policy.hotspot == "":
            derived["target_hotspot_residues"] = ""
        elif not _residue_list(policy.hotspot):
            raise Pdl1EntrypointError("settings_invalid", "hotspot is not a residue list")
        else:
            derived["target_hotspot_residues"] = policy.hotspot
    try:
        layout.workspace.mkdir(parents=True, exist_ok=False)
        design_path.mkdir(parents=True, exist_ok=True)
        destination = layout.workspace / "settings.json"
        payload = json.dumps(derived, indent=2, sort_keys=True) + "\n"
        destination.write_text(payload, encoding="utf-8")
        unchanged = source_path.read_bytes() == source
    except FileExistsError:
        raise Pdl1EntrypointError(
            "workspace_not_fresh",
            "attempt workspace already exists",
        ) from None
    except OSError:
        raise Pdl1EntrypointError(
            "settings_unreadable",
            "derived settings could not be written",
        ) from None
    if not unchanged:
        raise Pdl1EntrypointError("settings_invalid", "staged settings changed during derivation")
    return DerivedConfiguration(destination, design_path, layout.advanced_json)


def worker_argv(
    policy: Pdl1ContainerPolicy,
    image: ImageLayout,
    derived: DerivedConfiguration,
) -> list[str]:
    """Child command. The seed is an argument because a parent RNG does not cross a process."""

    return [
        image.python,
        str(Path(__file__).resolve()),
        "worker",
        "--cellertz-seed",
        str(policy.seed),
        "--bindcraft-script",
        str(image.bindcraft_script),
        "--settings",
        str(derived.settings_path),
        "--filters",
        policy.filters,
        "--advanced",
        str(derived.advanced_path),
    ]


def operational_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Image environment plus the BindCraft library path and JAX preallocation.

    The seed is intentionally absent. ``empty-explicit`` still applies to
    the host execution plan; this mapping is the container process
    environment the child needs in order to start. Existing
    ``LD_LIBRARY_PATH`` entries stay after the intended conda directories.
    """

    env = {key: value for key, value in base.items() if type(key) is str and type(value) is str}
    env["LD_LIBRARY_PATH"] = prepend_library_path(env.get("LD_LIBRARY_PATH"))
    env["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def subprocess_runner(argv: Sequence[str], env: Mapping[str, str]) -> int:
    """Spawn the child with ``shell=False``. OSError becomes ``launch_failed``."""

    if (
        type(argv) is not list
        or not argv
        or any(type(item) is not str or not item for item in argv)
    ):
        raise Pdl1EntrypointError("launch_failed", "argv rejected")
    try:
        completed = subprocess.run(
            list(argv),
            env=operational_environment(env),
            shell=False,
            check=False,
        )
    except OSError:
        raise Pdl1EntrypointError("launch_failed", "process did not start") from None
    return int(completed.returncode)


def run_entrypoint(
    layout: ContainerLayout,
    image: ImageLayout,
    policy: Pdl1ContainerPolicy,
    *,
    runner: ProcessRunner = subprocess_runner,
) -> Path:
    """Derive settings, run the child, and publish one validated CSV."""

    if not _is_file(layout.target_pdb) or not _is_file(layout.advanced_json):
        raise Pdl1EntrypointError(
            "settings_unreadable",
            "staged target or advanced file is missing",
        )
    if not _is_file(image.bindcraft_script) or not _is_file(image.filters):
        raise Pdl1EntrypointError("launch_failed", "image BindCraft files are missing")
    if policy.filters != str(image.filters):
        raise Pdl1EntrypointError(
            "filter_policy_rejected",
            "filters path is not the image no_filters policy",
        )
    derived = derive_settings(layout, policy)
    argv = worker_argv(policy, image, derived)
    env = operational_environment(os.environ)
    try:
        code = runner(argv, env)
    except Pdl1EntrypointError:
        raise
    except OSError:
        raise Pdl1EntrypointError("launch_failed", "process did not start") from None
    if type(code) is not int or isinstance(code, bool):
        raise Pdl1EntrypointError("launch_failed", "runner returned no exit code")
    if code != 0:
        raise Pdl1EntrypointError("bindcraft_failed", f"exit {code}")
    payload = _read_stats(derived.design_path / STATS_FILENAME)
    try:
        _publish(layout.design_records, payload)
    except OSError:
        raise Pdl1EntrypointError(
            "design_records_unpublished",
            "design_records.csv was not created",
        ) from None
    return layout.design_records


def run_worker(argv: Sequence[str]) -> None:
    """Seed NumPy in this process, then execute the pinned BindCraft script."""

    values = _worker_flags(argv)
    seed = _integer(values["--cellertz-seed"], minimum=0, maximum=2**32 - 1)
    script = Path(values["--bindcraft-script"])
    settings = Path(values["--settings"])
    advanced = Path(values["--advanced"])
    filters = values["--filters"]
    if ".." in filters.split("/") or not _is_file(script) or not _is_file(settings):
        raise Pdl1EntrypointError("launch_failed", "worker inputs are missing")
    if not _is_file(advanced) or not _is_file(Path(filters)):
        raise Pdl1EntrypointError("launch_failed", "worker inputs are missing")
    os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"
    os.environ["PYTHONUNBUFFERED"] = "1"
    import numpy as np

    np.random.seed(seed)
    sys.path.insert(0, str(script.resolve().parent))
    sys.argv = [
        str(script),
        "--settings",
        str(settings),
        "--filters",
        filters,
        "--advanced",
        str(advanced),
    ]
    runpy.run_path(str(script), run_name="__main__")


def main(argv: Sequence[str] | None = None) -> int:
    """Operator entry. Prints only the stable code on failure."""

    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args[:1] == ["worker"]:
            run_worker(args[1:])
            return 0
        policy = parse_policy(args, filters_path=IMAGE_NO_FILTERS)
        run_entrypoint(ContainerLayout.production(), ImageLayout.production(), policy)
    except Pdl1EntrypointError as error:
        print(error.code, file=sys.stderr)
        return 1
    return 0


def _worker_flags(argv: Sequence[str]) -> dict[str, str]:
    flags = ("--cellertz-seed", "--bindcraft-script", "--settings", "--filters", "--advanced")
    if type(argv) is not list or any(type(item) is not str for item in argv):
        raise Pdl1EntrypointError("malformed_value", "worker argv rejected")
    values: dict[str, str] = {}
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag not in flags:
            raise Pdl1EntrypointError("unsupported_argument", _brief(flag))
        if flag in values or index + 1 >= len(argv):
            raise Pdl1EntrypointError("malformed_value", _brief(flag))
        values[flag] = argv[index + 1]
        index += 2
    missing = [flag for flag in flags if flag not in values]
    if missing:
        raise Pdl1EntrypointError("policy_incomplete", "missing " + " ".join(missing))
    return values


def _residue_list(value: str) -> bool:
    """A non-empty residue list. An explicit empty hotspot is handled by the caller."""

    return (
        type(value) is str
        and value == value.strip()
        and bool(value)
        and len(value) <= 160
        and "\n" not in value
        and "\r" not in value
    )


def _integer(value: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not str or _ASCII_INTEGER.fullmatch(value) is None:
        raise Pdl1EntrypointError("malformed_value", "expected an integer")
    try:
        number = int(value)
    except ValueError:
        raise Pdl1EntrypointError("malformed_value", "expected an integer") from None
    if number < minimum or number > maximum:
        raise Pdl1EntrypointError("malformed_value", "integer is outside the accepted range")
    return number


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise Pdl1EntrypointError("settings_invalid", "duplicate settings key")
        parsed[key] = value
    return parsed


def _reject_workspace_overlap(layout: ContainerLayout) -> None:
    staged = layout.settings_json.resolve().parent
    workspace = layout.workspace.resolve()
    records = layout.design_records.resolve()
    if (
        workspace == staged
        or staged in workspace.parents
        or workspace in staged.parents
        or records == staged
        or staged in records.parents
    ):
        raise Pdl1EntrypointError("settings_invalid", "writable paths overlap the staged inputs")


def _read_stats(path: Path) -> bytes:
    try:
        missing = not path.exists() and not path.is_symlink()
    except OSError:
        raise Pdl1EntrypointError("design_records_invalid", "stats could not be read") from None
    if missing:
        raise Pdl1EntrypointError(
            "design_records_missing",
            "final_design_stats.csv is absent",
        )
    payload = _read_bounded(path, _MAX_RECORD_BYTES)
    _validate_records(payload)
    return payload


def _read_bounded(path: Path, limit: int) -> bytes:
    """Read until EOF, refusing more than ``limit`` bytes.

    A short read is not EOF. Only an empty read ends the file. The size
    check rejects an already oversized regular file before any byte is read.
    """

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW  # type: ignore[attr-defined]
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise Pdl1EntrypointError(
            "design_records_missing",
            "final_design_stats.csv is absent",
        ) from None
    except OSError:
        raise Pdl1EntrypointError("design_records_invalid", "stats could not be read") from None
    try:
        try:
            info = os.fstat(descriptor)
        except OSError:
            raise Pdl1EntrypointError("design_records_invalid", "stats could not be read") from None
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise Pdl1EntrypointError("design_records_invalid", "stats path is not a regular file")
        if info.st_size > limit:
            raise Pdl1EntrypointError(
                "design_records_invalid",
                "stats exceed the design_records ceiling",
            )
        payload = bytearray()
        while len(payload) <= limit:
            try:
                chunk = os.read(descriptor, min(_READ_CHUNK, limit - len(payload) + 1))
            except OSError:
                raise Pdl1EntrypointError(
                    "design_records_invalid",
                    "stats could not be read",
                ) from None
            if chunk == b"":
                break
            payload.extend(chunk)
        if len(payload) > limit:
            raise Pdl1EntrypointError(
                "design_records_invalid",
                "stats exceed the design_records ceiling",
            )
        return bytes(payload)
    finally:
        os.close(descriptor)


def _validate_records(payload: bytes) -> None:
    if not payload or b"\0" in payload:
        raise Pdl1EntrypointError("design_records_invalid", "stats are empty or binary")
    try:
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error):
        raise Pdl1EntrypointError("design_records_invalid", "stats are not UTF-8 CSV") from None
    if len(rows) == 1 and _header_ok(rows[0]):
        raise Pdl1EntrypointError(
            "design_records_header_only",
            "header only; absence of accepted designs is not scientific validation",
        )
    if len(rows) < 2 or len(rows) > _MAX_DATA_ROWS + 1 or not _header_ok(rows[0]):
        raise Pdl1EntrypointError("design_records_invalid", "stats do not match design_records.csv")
    header = rows[0]
    design_at = header.index("Design")
    sequence_at = header.index("Sequence")
    width = len(header)
    for row in rows[1:]:
        if len(row) != width:
            raise Pdl1EntrypointError("design_records_invalid", "a stats row has the wrong width")
        design = row[design_at].strip()
        sequence = "".join(row[sequence_at].split()).upper()
        if not design or any(char in design for char in ",\r\n"):
            raise Pdl1EntrypointError("design_records_invalid", "a design id is empty")
        if not sequence or any(residue not in AMINO_ACIDS for residue in sequence):
            raise Pdl1EntrypointError("design_records_invalid", "a sequence is not amino acids")


def _header_ok(header: list[str]) -> bool:
    return bool(
        header
        and all(name and name.strip() == name for name in header)
        and len(header) == len(set(header))
        and header.count("Design") == 1
        and header.count("Sequence") == 1
    )


def _publish(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    try:
        view = payload
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)


def _is_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _brief(value: str) -> str:
    token = value.replace("\n", " ")
    return token[:80]


if __name__ == "__main__":
    sys.exit(main())
