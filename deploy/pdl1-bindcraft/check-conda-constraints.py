"""Check observed conda match specs against installed version and build strings.

A package name being present is not dependency closure. Virtual packages are
reported and are not treated as installed conda records. Pip rows are separate.
Satisfied metadata is not a reproducible build.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

_LOCK_LINE = re.compile(r"^[a-z0-9_][a-z0-9._+-]*=[A-Za-z0-9._+!-]+=[A-Za-z0-9._+-]+$")
_PRE_RELEASE = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}
_POST_RELEASE = {"post": 1, "r": 1, "rev": 1}
_RECORDED = {
    "python": "3.10.21=h267e890_0_cpython",
    "jax": "0.6.0=pyhd8ed1ab_0",
    "jaxlib": "0.6.0=cuda126py310hec873cc_200",
    "cuda-version": "12.9=h4f385c5_3",
    "cuda-nvcc": "12.9.86=hcdd1206_6",
    "cudnn": "9.10.2.21=hbcb9cd8_0",
    "libgfortran5": "16.2.0=h6b99dfc_5",
}


def reassemble_specs(field: str) -> list[str]:
    """Join operator fragments that were split on commas inside one match spec."""

    specs: list[str] = []
    for piece in field.split(","):
        item = piece.strip()
        if not item:
            continue
        if specs and item.startswith(("==", "!=", ">=", "<=", ">", "<", "=")):
            specs[-1] = f"{specs[-1]},{item}"
            continue
        specs.append(item)
    return specs


def version_key(version: str) -> tuple[int, tuple[tuple[int, int | str], ...]]:
    """Return a conda-like sort key. This is a checker, not the conda solver."""

    text = version.strip().lower().replace("-", ".").replace("_", ".")
    epoch = 0
    if "!" in text:
        epoch_text, text = text.split("!", 1)
        epoch = int(epoch_text) if epoch_text else 0
    pieces: list[tuple[int, int | str]] = []
    for token in re.findall(r"\d+|[a-z]+", text):
        if token.isdigit():
            pieces.append((0, int(token)))
        elif token in _PRE_RELEASE:
            pieces.append((-1, _PRE_RELEASE[token]))
        elif token in _POST_RELEASE:
            pieces.append((1, _POST_RELEASE[token]))
        else:
            pieces.append((2, token))
    return epoch, tuple(pieces)


def compare_versions(left: str, right: str) -> int:
    left_epoch, left_parts = version_key(left)
    right_epoch, right_parts = version_key(right)
    if left_epoch != right_epoch:
        return -1 if left_epoch < right_epoch else 1
    width = max(len(left_parts), len(right_parts))
    pad = (0, 0)
    for index in range(width):
        left_part = left_parts[index] if index < len(left_parts) else pad
        right_part = right_parts[index] if index < len(right_parts) else pad
        if left_part != right_part:
            return -1 if left_part < right_part else 1
    return 0


def version_glob(version: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    current = version.lower()
    wanted = pattern.lower()
    if wanted.endswith(".*"):
        prefix = wanted[:-2]
        return current == prefix or current.startswith(prefix + ".")
    return fnmatch.fnmatchcase(current, wanted)


def _clause(text: str) -> tuple[str, str, str | None]:
    body = text.strip()
    operator = "=="
    for candidate in ("==", "!=", ">=", "<=", "=", ">", "<"):
        if body.startswith(candidate):
            operator = candidate
            body = body[len(candidate) :].strip()
            break
    parts = body.split()
    version = parts[0] if parts else "*"
    build = parts[1] if len(parts) > 1 else None
    return operator, version, build


def spec_clauses(spec: str) -> tuple[str, tuple[tuple[str, str, str | None], ...]]:
    name, _, rest = spec.strip().partition(" ")
    if not rest:
        return name, ()
    return name, tuple(_clause(piece) for piece in rest.split(",") if piece.strip())


def build_matches(installed: str, pattern: str | None) -> bool:
    if pattern is None:
        return True
    if any(mark in pattern for mark in "*?[]"):
        return fnmatch.fnmatchcase(installed, pattern)
    return installed == pattern


def clause_holds(version: str, build: str, operator: str, wanted: str, wanted_build: str | None) -> bool:
    glob = "*" in wanted
    if operator == "!=":
        version_hit = version_glob(version, wanted) if glob else compare_versions(version, wanted) == 0
        return not (version_hit and build_matches(build, wanted_build))
    if wanted_build is not None and not build_matches(build, wanted_build):
        return False
    if glob or wanted == "*":
        return version_glob(version, wanted)
    if operator in {"==", "="}:
        if operator == "=" and wanted != "*":
            return version_glob(version, wanted if wanted.endswith(".*") else f"{wanted}.*")
        return compare_versions(version, wanted) == 0
    order = compare_versions(version, wanted)
    if operator == ">=":
        return order >= 0
    if operator == "<=":
        return order <= 0
    if operator == ">":
        return order > 0
    if operator == "<":
        return order < 0
    return False


def spec_holds(version: str, build: str, spec: str) -> bool:
    _name, clauses = spec_clauses(spec)
    return all(clause_holds(version, build, operator, wanted, wanted_build) for operator, wanted, wanted_build in clauses)


def parse_lock(text: str) -> dict[str, tuple[str, str]]:
    packages: dict[str, tuple[str, str]] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _LOCK_LINE.fullmatch(line) is None or line.count("=") != 2:
            raise ValueError(f"base_lock_unconstrained:line_{line_number}")
        name, version, build = line.split("=")
        if name in packages:
            raise ValueError(f"base_lock_conflict:{name}")
        packages[name] = (version, build)
    return packages


def parse_depends(text: str) -> tuple[dict[str, list[str]], dict[str, tuple[str, str]]]:
    conda: dict[str, list[str]] = {}
    pip: dict[str, tuple[str, str]] = {}
    seen_conda: set[str] = set()
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if fields[0] == "conda" and len(fields) == 3:
            name, spec = fields[1], fields[2]
            if name not in seen_conda:
                seen_conda.add(name)
                conda[name] = []
            if spec:
                conda[name].append(spec)
            continue
        if fields[0] == "pip" and len(fields) == 4:
            if fields[1] in pip:
                raise ValueError(f"base_lock_conflict:{fields[1]}")
            pip[fields[1]] = (fields[2], fields[3])
            continue
        raise ValueError(f"base_lock_dependencies_unverified:line_{line_number}")
    return conda, pip


def check_constraints(lock_text: str, depends_text: str) -> tuple[list[str], list[str]]:
    """Return stdout findings and stderr failures. Failures mean the check stops."""

    packages = parse_lock(lock_text)
    conda_specs, pip_rows = parse_depends(depends_text)
    failures: list[str] = []
    findings: list[str] = []
    virtuals: dict[str, int] = {}
    for name in packages:
        if name not in conda_specs:
            failures.append(f"base_lock_dependencies_unverified:{name}")
    for name, specs in conda_specs.items():
        if name not in packages:
            failures.append(f"base_lock_dependencies_unverified:{name}")
            continue
        for spec in specs:
            depend_name, _clauses = spec_clauses(spec)
            if depend_name.startswith("__"):
                virtuals[depend_name] = virtuals.get(depend_name, 0) + 1
                continue
            if depend_name not in packages:
                if depend_name in pip_rows:
                    failures.append(f"base_lock_pip_replacement:{depend_name}")
                else:
                    failures.append(f"base_lock_constraint_unsatisfied:{depend_name}:{spec}")
                continue
            installed_version, installed_build = packages[depend_name]
            if not spec_holds(installed_version, installed_build, spec):
                failures.append(f"base_lock_constraint_unsatisfied:{name}:{spec}")
    if failures:
        return findings, failures
    findings.append(f"constraint_check\tconda_records\t{len(packages)}")
    for name in sorted(virtuals):
        findings.append(f"unresolved_virtual\t{name}\t{virtuals[name]}")
    for name, (version, commit) in sorted(pip_rows.items()):
        commit_label = commit or "commit_not_in_pip_row"
        findings.append(f"pip_observation\t{name}\t{version}\t{commit_label}")
    findings.append("metadata_is_not_a_reproducible_build")
    return findings, []


def _inventory_rows(text: str) -> dict[str, list[tuple[str, ...]]]:
    sections: dict[str, list[tuple[str, ...]]] = {}
    current = ""
    for raw in text.splitlines():
        fields = raw.split("\t")
        if not fields:
            continue
        if fields[0] == "section" and len(fields) >= 2:
            current = fields[1]
            sections.setdefault(current, [])
            continue
        if fields[0] == "conda" and len(fields) == 7 and current in {"bindcraft", "bootstrap"}:
            prefix, name, version, build, channel, depends = fields[1:]
            if any(row[1] == name for row in sections[current]):
                raise ValueError(f"conflict\t{prefix}\t{name}")
            sections[current].append((prefix, name, version, build, channel, depends))
        elif fields[0] == "pip" and len(fields) == 5 and current == "pip":
            if any(row[1] == fields[2] for row in sections[current]):
                raise ValueError(f"conflict\t{fields[1]}\t{fields[2]}")
            sections[current].append(tuple(fields[1:]))
    return sections


def write_observed(inventory_text: str, directory: Path) -> None:
    """Write BindCraft lock files. Bootstrap and pip stay in their own files."""

    sections = _inventory_rows(inventory_text)
    bindcraft = sections.get("bindcraft", [])
    channels = {row[4] for row in bindcraft}
    if channels != {"conda.anaconda.org/conda-forge"}:
        raise ValueError("channel_unrecorded:" + ",".join(sorted(channels)))
    for prefix, name, version, build, _channel, _depends in bindcraft:
        if prefix != "/opt/conda/envs/BindCraft":
            raise ValueError(f"conflict\t{prefix}\t{name}")
        line = f"{name}={version}={build}"
        if _LOCK_LINE.fullmatch(line) is None:
            raise ValueError(f"base_lock_unconstrained:{name}")
        recorded = _RECORDED.get(name)
        if recorded is not None and recorded != f"{version}={build}":
            raise ValueError(f"base_lock_conflict:{name}")
    present = {name: f"{version}={build}" for _prefix, name, version, build, _channel, _depends in bindcraft}
    missing = [name for name, spec in _RECORDED.items() if present.get(name) != spec]
    if missing:
        raise ValueError("base_lock_incomplete:" + ",".join(missing))
    if any(name == "python" and version == "3.14.7" for _p, name, version, _b, _c, _d in bindcraft):
        raise ValueError("base_lock_conflict:python")

    lock_lines = [
        "# Historical conda-meta observations from the BindCraft SIF prefix.",
        "# Prefix: /opt/conda/envs/BindCraft. Channel label: conda.anaconda.org/conda-forge.",
        "# Each line is name=version=build. These rows are not approved Docker pins.",
        "# approved_pin: false",
        "# driver_compatibility: unresolved",
        "# scientific_run_execution: disabled",
        "# python 3.14.7 is the /opt/conda prefix and is not a row in this file.",
        "# Installed metadata matching these specs is not a reproducible build.",
        "# Virtual packages and pip distributions are outside this lock.",
    ]
    depends_lines = [
        "# BindCraft prefix dependency specs. One conda spec per row.",
        "# A comment is not closure. Virtual packages stay in the spec text.",
        "# pip rows are observations and do not close a conda dependency.",
    ]
    for _prefix, name, version, build, _channel, depends in sorted(bindcraft, key=lambda row: row[1]):
        lock_lines.append(f"{name}={version}={build}")
        specs = reassemble_specs(depends)
        if not specs:
            depends_lines.append(f"conda\t{name}\t")
        for spec in specs:
            depends_lines.append(f"conda\t{name}\t{spec}")
    for prefix, name, version, commit in sections.get("pip", []):
        if prefix != "/opt/conda/envs/BindCraft":
            raise ValueError(f"conflict\t{prefix}\t{name}")
        depends_lines.append(f"pip\t{name}\t{version}\t{commit}")

    bootstrap_lines = [
        "# /opt/conda bootstrap observations. These rows are not BindCraft lock entries.",
        "# belongs_in_bindcraft_lock: false",
        "# scientific_run_execution: disabled",
    ]
    for prefix, name, version, build, channel, depends in sections.get("bootstrap", []):
        for spec in reassemble_specs(depends) or [""]:
            bootstrap_lines.append(f"conda\t{prefix}\t{name}\t{version}\t{build}\t{channel}\t{spec}")
    pip_lines = [
        "# Pip observations from the BindCraft interpreter. Commits absent here stay absent.",
        "# The ColabDesign commit is kept in historical-observations.json.",
    ]
    for prefix, name, version, commit in sections.get("pip", []):
        pip_lines.append(f"pip\t{prefix}\t{name}\t{version}\t{commit}")

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "conda-observed.lock").write_text("\n".join(lock_lines) + "\n", encoding="utf-8")
    (directory / "conda-bindcraft.depends.tsv").write_text("\n".join(depends_lines) + "\n", encoding="utf-8")
    (directory / "conda-bootstrap.observed.tsv").write_text("\n".join(bootstrap_lines) + "\n", encoding="utf-8")
    (directory / "pip-observed.tsv").write_text("\n".join(pip_lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--write-observed":
        try:
            write_observed(Path(argv[2]).read_text(encoding="utf-8"), Path(argv[3]))
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        return 0
    if len(argv) != 3:
        print("base_lock_dependencies_unverified", file=sys.stderr)
        return 1
    try:
        findings, failures = check_constraints(
            Path(argv[1]).read_text(encoding="utf-8"),
            Path(argv[2]).read_text(encoding="utf-8"),
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("\n".join(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
