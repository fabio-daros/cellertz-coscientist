"""mBER generator adapter.

mBER (manifoldbio mber-open) designs format-specific binders,
typically VHH, by optimizing sequences against AlphaFold-Multimer.
Native accepted output is a sequence table, not a BindCraft CSV
and not an RFdiffusion backbone.

GenerationRequest
        ↓
MBERAdapter
        ↓
MBERExecutor  → raw artifacts
MBERParser    → NormalizedCandidate[]

mBER-native fields stay in generator_metadata.
This adapter never emits ranking_score, rank, ipSAE, or Boltz.
"""

from __future__ import annotations

import csv
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cellertz.domains.protein_design.agents.generation import (
    AMINO_ACIDS,
    GenerationRequest,
    NormalizedCandidate,
    request_input_hash,
)
from cellertz.runtime import (
    ExecutionRequest,
    ExecutionRuntime,
    LocalExecutionRuntime,
)


ADAPTER_VERSION = "mber_adapter_v1"
STATS_FILENAME = "accepted.csv"
FORBIDDEN_METADATA_KEYS = (
    "ipsae",
    "ranking_score",
    "rank",
    "ysd",
    "boltz",
    "boltz2",
    "selected",
    "critic",
)
_DESIGN_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class MBERError(ValueError):
    """Structured mBER failure. Converted to GenerationResult."""


@dataclass(frozen=True)
class MBERArtifacts:
    output_dir: Path
    accepted_csv: Path
    command: tuple[str, ...]
    mber_version: str | None = None
    returncode: int = 0


class MBERExecutor(Protocol):
    def execute(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> MBERArtifacts:
        ...


def _substitute_work_dir(
    parts: list[str],
    work_dir: Path,
) -> list[str]:
    return [
        part.replace("{work_dir}", work_dir.as_posix())
        for part in parts
    ]


def _forbidden_metadata_key(name: str) -> bool:
    lowered = name.lower().replace("-", "_")
    if lowered in FORBIDDEN_METADATA_KEYS:
        return True
    return any(
        token in lowered
        for token in ("ipsae", "boltz2", "ranking_score", "ysd")
    )


def _require_sequence(raw: str, design: str) -> str:
    sequence = "".join(raw.split()).upper()
    if not sequence:
        raise MBERError(
            f"mBER design {design} is missing binder_seq."
        )
    invalid = sorted(
        {
            residue
            for residue in sequence
            if residue not in AMINO_ACIDS
        }
    )
    if invalid:
        raise MBERError(
            f"mBER design {design} has invalid residues: "
            + ",".join(invalid)
        )
    return sequence


def _candidate_id(design: str) -> str:
    cleaned = _DESIGN_SAFE.sub("_", design).strip("._-")
    if not cleaned:
        raise MBERError("mBER design name produced an empty id.")
    return f"mber_{cleaned}"


def _design_name(row: dict[str, str | None], index: int) -> str:
    trajectory = (row.get("trajectory_name") or "").strip()
    binder_index = (row.get("binder_index") or "").strip()
    if trajectory and binder_index != "":
        return f"{trajectory}_binder_{binder_index}"
    if trajectory:
        return f"{trajectory}_{index}"
    return f"design_{index:03d}"


class MBERParser:
    """Deterministic accepted.csv parser. Does not execute mBER."""

    def parse(
        self,
        request: GenerationRequest,
        artifacts: MBERArtifacts,
    ) -> list[NormalizedCandidate]:
        path = artifacts.accepted_csv
        if not path.is_file():
            raise MBERError(
                f"mBER did not write {STATS_FILENAME}."
            )

        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise MBERError("mBER accepted.csv has no header.")
            fields = {
                name.strip()
                for name in reader.fieldnames
                if name
            }
            if "binder_seq" not in fields:
                raise MBERError(
                    "mBER accepted.csv must include binder_seq."
                )
            rows = list(reader)

        parsed: list[NormalizedCandidate] = []
        seen: set[str] = set()
        input_hash = request_input_hash(request)

        for index, row in enumerate(rows, start=1):
            design = _design_name(row, index)
            candidate_id = _candidate_id(design)
            if candidate_id in seen:
                raise MBERError(
                    f"Duplicate mBER design: {candidate_id}."
                )
            seen.add(candidate_id)
            sequence = _require_sequence(
                row.get("binder_seq") or "",
                design,
            )
            parsed.append(
                NormalizedCandidate(
                    candidate_id=candidate_id,
                    sequence=sequence,
                    target_id=request.target_id,
                    generator="mber",
                    generator_metadata={
                        "source_file": path.name,
                        "design": design,
                        "trajectory_name": (
                            row.get("trajectory_name") or ""
                        ).strip(),
                        "binder_index": (
                            row.get("binder_index") or ""
                        ).strip(),
                        "complex_pdb_path": (
                            row.get("complex_pdb_path") or ""
                        ).strip()
                        or None,
                        "relaxed_pdb_path": (
                            row.get("relaxed_pdb_path") or ""
                        ).strip()
                        or None,
                        "native_metrics": _native_metrics(row),
                        "provenance": {
                            "adapter_version": ADAPTER_VERSION,
                            "mber_version": artifacts.mber_version,
                            "command": list(artifacts.command),
                            "input_hash": input_hash,
                            "returncode": artifacts.returncode,
                        },
                    },
                )
            )

        if len(parsed) < request.n_candidates:
            raise MBERError(
                "mBER returned "
                f"{len(parsed)} designs, expected "
                f"{request.n_candidates}."
            )
        return parsed[: request.n_candidates]


def _native_metrics(
    row: dict[str, str | None],
) -> dict[str, str]:
    skip = {
        "binder_seq",
        "trajectory_name",
        "binder_index",
        "complex_pdb_path",
        "relaxed_pdb_path",
    }
    metrics: dict[str, str] = {}
    for key, value in row.items():
        if not key or key in skip or _forbidden_metadata_key(key):
            continue
        metrics[key] = (value or "").strip()
    return metrics


class FixtureMBERExecutor:
    """Replay recorded mBER artifacts. No GPU, no binary."""

    def __init__(
        self,
        artifact_dir: str | Path,
        mber_version: str = "fixture_outputs_v1",
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.mber_version = mber_version

    def execute(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> MBERArtifacts:
        del request, work_dir
        accepted_csv = self.artifact_dir / STATS_FILENAME
        if not accepted_csv.is_file():
            raise MBERError(
                "Fixture mBER artifacts are missing "
                f"{STATS_FILENAME}."
            )
        return MBERArtifacts(
            output_dir=self.artifact_dir,
            accepted_csv=accepted_csv,
            command=("fixture-mber",),
            mber_version=self.mber_version,
        )


class LocalMBERExecutor:
    """Translate an mBER request and delegate to a runtime."""

    def __init__(
        self,
        runtime: ExecutionRuntime | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.runtime = runtime or LocalExecutionRuntime()
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> MBERArtifacts:
        work_dir.mkdir(parents=True, exist_ok=True)
        command = tuple(_local_command(request, work_dir))
        result = self.runtime.execute(
            ExecutionRequest(
                argv=command,
                cwd=work_dir,
                timeout_seconds=self.timeout_seconds,
            )
        )
        if result.status != "complete":
            raise MBERError(result.error or "mBER process failed.")

        return MBERArtifacts(
            output_dir=work_dir,
            accepted_csv=work_dir / STATS_FILENAME,
            command=command,
            mber_version=str(
                request.constraints.get("mber_version")
            )
            if request.constraints.get("mber_version")
            else None,
            returncode=result.return_code or 0,
        )


def _local_command(
    request: GenerationRequest,
    work_dir: Path,
) -> list[str]:
    constraints = request.constraints
    raw_command = constraints.get("mber_command")
    if raw_command is not None:
        if not isinstance(raw_command, (list, tuple)):
            raise MBERError(
                "constraints.mber_command must be a list."
            )
        if not raw_command:
            raise MBERError("constraints.mber_command is empty.")
        return _substitute_work_dir(
            [str(part) for part in raw_command],
            work_dir,
        )

    mber_bin = constraints.get("mber_bin")
    if not mber_bin:
        raise MBERError(
            "mBER executable is not configured. "
            "Provide constraints.mber_command or mber_bin."
        )
    command = [str(mber_bin)]
    settings = constraints.get("settings")
    if settings:
        command.extend(["--settings", str(settings)])
    return command


class MBERAdapter:
    """Coordinates executor and parser. Does not score or rank."""

    name = "mber"
    version = ADAPTER_VERSION

    def __init__(
        self,
        executor: MBERExecutor | None = None,
        parser: MBERParser | None = None,
        work_dir: str | Path | None = None,
        runtime: ExecutionRuntime | None = None,
    ) -> None:
        self.runtime = runtime or LocalExecutionRuntime()
        self.executor = executor or LocalMBERExecutor(
            runtime=self.runtime
        )
        self.parser = parser or MBERParser()
        self.work_dir = (
            None if work_dir is None else Path(work_dir)
        )

    def generate(
        self,
        request: GenerationRequest,
    ) -> list[NormalizedCandidate]:
        if request.generator != self.name:
            raise MBERError(
                "MBERAdapter received generator="
                f"{request.generator}."
            )

        try:
            if self.work_dir is None:
                with tempfile.TemporaryDirectory(
                    prefix="mber_"
                ) as tmp:
                    return self._run(request, Path(tmp))
            self.work_dir.mkdir(parents=True, exist_ok=True)
            return self._run(request, self.work_dir)
        except MBERError:
            raise
        except Exception as error:
            raise MBERError(
                f"mBER generation failed: {error}"
            ) from error

    def _run(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> list[NormalizedCandidate]:
        artifacts = self.executor.execute(request, work_dir)
        return self.parser.parse(request, artifacts)
