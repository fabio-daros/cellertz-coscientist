"""Read SIF conda identities by prefix. Does not import JAX or use the GPU.

Each record is keyed by prefix and package name. The same name may exist in
both prefixes with different versions. A second record for one name inside
one prefix is a conflict. Package bytes and credential URLs are not printed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

BINDCRAFT_PREFIX = "/opt/conda/envs/BindCraft"
BOOTSTRAP_PREFIX = "/opt/conda"


class InventoryConflict(Exception):
    """One prefix contains two conda-meta records for the same name."""

    def __init__(self, prefix: str, name: str) -> None:
        self.prefix = prefix
        self.name = name
        super().__init__(name)


def channel_label(record: dict) -> str:
    label = record.get("schannel")
    if isinstance(label, str) and label and "://" not in label and "@" not in label:
        return label
    raw = record.get("channel")
    if not isinstance(raw, str) or "://" not in raw:
        return "unrecorded"
    parts = urlsplit(raw)
    host = parts.hostname or "redacted"
    owner = next((piece for piece in parts.path.split("/") if piece), "")
    return f"{host}/{owner}" if owner else host


def _safe_depend(spec: object) -> str:
    text = spec if isinstance(spec, str) else ""
    if "://" in text or "@" in text:
        return "redacted"
    return text


def read_prefix(directory: Path, prefix: str) -> list[dict[str, object]]:
    """Return one row per package. A repeated name raises InventoryConflict."""

    if not directory.is_dir():
        return []
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        name = str(record.get("name", ""))
        if name in seen:
            raise InventoryConflict(prefix, name)
        seen.add(name)
        depends = record.get("depends")
        if not isinstance(depends, list):
            depends = []
        rows.append(
            {
                "name": name,
                "version": record.get("version"),
                "build": record.get("build"),
                "channel": channel_label(record),
                "depends": [_safe_depend(item) for item in depends],
            }
        )
    return rows


def records_by_prefix(
    directories: dict[str, Path],
) -> dict[tuple[str, str], dict[str, object]]:
    """Index conda-meta rows by (prefix, package name). Prefixes stay apart."""

    records: dict[tuple[str, str], dict[str, object]] = {}
    for prefix, directory in directories.items():
        for row in read_prefix(directory, prefix):
            key = (prefix, str(row["name"]))
            if key in records:
                raise InventoryConflict(prefix, str(row["name"]))
            records[key] = row
    return records


def rows_for(
    records: dict[tuple[str, str], dict[str, object]],
    prefix: str,
) -> list[dict[str, object]]:
    return [records[key] for key in sorted(records) if key[0] == prefix]


def bindcraft_rows(
    records: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    """Rows allowed in the BindCraft lock. The base prefix is not included."""

    return rows_for(records, BINDCRAFT_PREFIX)


def emit_section(section: str, prefix: str, rows: list[dict[str, object]]) -> None:
    print(f"section\t{section}\t{prefix}", flush=True)
    for row in rows:
        depends = ",".join(str(item) for item in row["depends"])  # type: ignore[arg-type]
        print(
            f"conda\t{prefix}\t{row['name']}\t{row['version']}\t{row['build']}\t{row['channel']}\t{depends}",
            flush=True,
        )


def pip_rows(prefix: str) -> None:
    from importlib import metadata

    print(f"section\tpip\t{prefix}", flush=True)
    rows: list[tuple[str, str, str]] = []
    for distribution in metadata.distributions():
        installer = distribution.read_text("INSTALLER")
        if installer is None or installer.strip() != "pip":
            continue
        name = distribution.metadata.get("Name") or ""
        commit = ""
        direct = distribution.read_text("direct_url.json")
        if direct:
            try:
                payload = json.loads(direct)
            except json.JSONDecodeError:
                payload = {}
            vcs = payload.get("vcs_info")
            if isinstance(vcs, dict) and isinstance(vcs.get("commit"), str):
                commit = vcs["commit"]
        rows.append((name, distribution.version, commit))
    for name, version, commit in sorted(rows, key=lambda item: item[0].casefold()):
        print(f"pip\t{prefix}\t{name}\t{version}\t{commit}", flush=True)


def installer_identities() -> None:
    """Print four distinct identities. conda --version is only the CLI version."""

    print("section\tinstaller\t/opt/conda", flush=True)
    executable = Path("/opt/conda/bin/conda")
    if executable.is_file():
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        version = (completed.stdout or completed.stderr).strip().splitlines()
        conda_cli = version[0] if version else "unrecorded"
    else:
        conda_cli = "absent"
    print(f"identity\tconda_cli\t{conda_cli}", flush=True)
    print("identity\tminiforge_release\tunrecorded", flush=True)
    print("identity\tinstaller_filename\tunrecorded", flush=True)
    print("identity\tinstaller_sha256\tunrecorded", flush=True)


def inventory(bindcraft_meta: Path, bootstrap_meta: Path) -> int:
    """Print both prefixes. A shared package name is not a merge conflict."""

    print(f"python\t{sys.version.split()[0]}", flush=True)
    try:
        records = records_by_prefix(
            {
                BINDCRAFT_PREFIX: bindcraft_meta,
                BOOTSTRAP_PREFIX: bootstrap_meta,
            }
        )
    except InventoryConflict as conflict:
        print(f"conflict\t{conflict.prefix}\t{conflict.name}", flush=True)
        return 2
    emit_section("bindcraft", BINDCRAFT_PREFIX, bindcraft_rows(records))
    emit_section("bootstrap", BOOTSTRAP_PREFIX, rows_for(records, BOOTSTRAP_PREFIX))
    installer_identities()
    pip_rows(BINDCRAFT_PREFIX)
    return 0


def main() -> int:
    return inventory(
        Path(BINDCRAFT_PREFIX) / "conda-meta",
        Path(BOOTSTRAP_PREFIX) / "conda-meta",
    )


if __name__ == "__main__":
    raise SystemExit(main())
