"""BindCraft generator adapter.

The adapter translates a GenerationRequest into a tool command.
ExecutionRuntime runs that command. BindCraftParser interprets
the artifacts. The runtime never sees scientific meaning.

BindCraftAdapter
        ├── runtime.execute(...)
        └── BindCraftParser
                ↓
        NormalizedCandidate[]
"""

from __future__ import annotations

import csv
import re
import tempfile
from dataclasses import dataclass, field
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

ADAPTER_VERSION = "bindcraft_adapter_v1"
STATS_FILENAME = "final_design_stats.csv"
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


class BindCraftError(ValueError):
    """Structured BindCraft failure. Converted to GenerationResult."""


@dataclass(frozen=True)
class BindCraftArtifacts:
    output_dir: Path
    stats_csv: Path
    command: tuple[str, ...]
    bindcraft_version: str | None = None
    returncode: int = 0
    execution_provenance: dict[str, object] = field(
        default_factory=dict
    )


class BindCraftExecutor(Protocol):
    def execute(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> BindCraftArtifacts:
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
        raise BindCraftError(
            f"BindCraft design {design} is missing Sequence."
        )
    invalid = sorted(
        {
            residue
            for residue in sequence
            if residue not in AMINO_ACIDS
        }
    )
    if invalid:
        raise BindCraftError(
            f"BindCraft design {design} has invalid residues: "
            + ",".join(invalid)
        )
    return sequence


def _candidate_id(design: str) -> str:
    cleaned = _DESIGN_SAFE.sub("_", design).strip("._-")
    if not cleaned:
        raise BindCraftError(
            "BindCraft Design column produced an empty id."
        )
    return f"bindcraft_{cleaned}"


def candidate_id_for_design(design: str) -> str:
    """Map a published design identity through the BindCraft candidate contract."""

    return _candidate_id(design)


class BindCraftParser:
    """Deterministic CSV parser. Does not execute BindCraft."""

    def parse(
        self,
        request: GenerationRequest,
        artifacts: BindCraftArtifacts,
    ) -> list[NormalizedCandidate]:
        path = artifacts.stats_csv
        if not path.is_file():
            raise BindCraftError(
                "BindCraft did not write "
                f"{STATS_FILENAME}."
            )

        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise BindCraftError(
                    "BindCraft stats CSV has no header."
                )
            fields = {
                name.strip()
                for name in reader.fieldnames
                if name
            }
            if "Design" not in fields or "Sequence" not in fields:
                raise BindCraftError(
                    "BindCraft stats CSV must include "
                    "Design and Sequence columns."
                )
            rows = list(reader)

        parsed: list[NormalizedCandidate] = []
        seen: set[str] = set()
        input_hash = request_input_hash(request)

        for index, row in enumerate(rows, start=1):
            design = (row.get("Design") or "").strip()
            if not design:
                raise BindCraftError(
                    f"BindCraft row {index} is missing Design."
                )

            candidate_id = _candidate_id(design)
            if candidate_id in seen:
                raise BindCraftError(
                    "Duplicate BindCraft design: "
                    f"{candidate_id}."
                )
            seen.add(candidate_id)

            sequence = _require_sequence(
                row.get("Sequence") or "",
                design,
            )
            native_metrics = _native_metrics(row)
            parsed.append(
                NormalizedCandidate(
                    candidate_id=candidate_id,
                    sequence=sequence,
                    target_id=request.target_id,
                    generator="bindcraft",
                    generator_metadata={
                        "source_file": path.name,
                        "design": design,
                        "design_number": index,
                        "native_metrics": native_metrics,
                        "provenance": {
                            "adapter_version": ADAPTER_VERSION,
                            "bindcraft_version": (
                                artifacts.bindcraft_version
                            ),
                            "command": list(artifacts.command),
                            "input_hash": input_hash,
                            "returncode": artifacts.returncode,
                            **(
                                {
                                    "execution": dict(
                                        artifacts.execution_provenance
                                    )
                                }
                                if artifacts.execution_provenance
                                else {}
                            ),
                        },
                    },
                )
            )

        if len(parsed) < request.n_candidates:
            raise BindCraftError(
                "BindCraft returned "
                f"{len(parsed)} designs, expected "
                f"{request.n_candidates}."
            )

        return parsed[: request.n_candidates]


def _native_metrics(
    row: dict[str, str | None],
) -> dict[str, str]:
    metrics: dict[str, str] = {}
    for key, value in row.items():
        if not key or key == "Sequence":
            continue
        if key == "Rank":
            metrics["bindcraft_rank"] = (value or "").strip()
            continue
        if _forbidden_metadata_key(key):
            continue
        metrics[key] = (value or "").strip()
    return metrics


class ExistingBindCraftArtifactsExecutor:
    """Reuse BindCraft artifacts produced by an earlier execution."""

    def __init__(
        self,
        artifacts: BindCraftArtifacts,
    ) -> None:
        self.artifacts = artifacts

    def execute(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> BindCraftArtifacts:
        del request, work_dir
        return self.artifacts


class FixtureBindCraftExecutor:
    """Replay recorded BindCraft artifacts. No GPU, no binary."""

    def __init__(
        self,
        artifact_dir: str | Path,
        bindcraft_version: str = "fixture_outputs_v1",
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.bindcraft_version = bindcraft_version

    def execute(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> BindCraftArtifacts:
        del request, work_dir
        stats_csv = self.artifact_dir / STATS_FILENAME
        if not stats_csv.is_file():
            raise BindCraftError(
                "Fixture BindCraft artifacts are missing "
                f"{STATS_FILENAME}."
            )
        return BindCraftArtifacts(
            output_dir=self.artifact_dir,
            stats_csv=stats_csv,
            command=("fixture-bindcraft",),
            bindcraft_version=self.bindcraft_version,
        )


class LocalBindCraftExecutor:
    """Translate a BindCraft request and delegate to a runtime."""

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
    ) -> BindCraftArtifacts:
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
            raise BindCraftError(
                result.error
                or "BindCraft process failed."
            )

        return BindCraftArtifacts(
            output_dir=work_dir,
            stats_csv=work_dir / STATS_FILENAME,
            command=command,
            bindcraft_version=str(
                request.constraints.get("bindcraft_version")
            )
            if request.constraints.get("bindcraft_version")
            else None,
            returncode=result.return_code or 0,
        )


def _local_command(
    request: GenerationRequest,
    work_dir: Path,
) -> list[str]:
    constraints = request.constraints
    raw_command = constraints.get("bindcraft_command")
    if raw_command is not None:
        if not isinstance(raw_command, (list, tuple)):
            raise BindCraftError(
                "constraints.bindcraft_command must be a list."
            )
        if not raw_command:
            raise BindCraftError(
                "constraints.bindcraft_command is empty."
            )
        return _substitute_work_dir(
            [str(part) for part in raw_command],
            work_dir,
        )

    bindcraft_bin = constraints.get("bindcraft_bin")
    settings = constraints.get("settings")
    if not bindcraft_bin or not settings:
        raise BindCraftError(
            "BindCraft executable is not configured. "
            "Provide constraints.bindcraft_command or "
            "bindcraft_bin and settings."
        )

    command = [
        str(bindcraft_bin),
        "--settings",
        str(settings),
    ]
    filters = constraints.get("filters")
    if filters:
        command.extend(["--filters", str(filters)])
    advanced = constraints.get("advanced")
    if advanced:
        command.extend(["--advanced", str(advanced)])
    return command


class BindCraftAdapter:
    """Coordinates executor and parser. Does not score or rank."""

    name = "bindcraft"
    version = ADAPTER_VERSION

    def __init__(
        self,
        executor: BindCraftExecutor | None = None,
        parser: BindCraftParser | None = None,
        work_dir: str | Path | None = None,
        runtime: ExecutionRuntime | None = None,
    ) -> None:
        self.runtime = runtime or LocalExecutionRuntime()
        self.executor = executor or LocalBindCraftExecutor(
            runtime=self.runtime
        )
        self.parser = parser or BindCraftParser()
        self.work_dir = (
            None if work_dir is None else Path(work_dir)
        )

    def generate(
        self,
        request: GenerationRequest,
    ) -> list[NormalizedCandidate]:
        if request.generator != self.name:
            raise BindCraftError(
                "BindCraftAdapter received generator="
                f"{request.generator}."
            )

        try:
            if self.work_dir is None:
                with tempfile.TemporaryDirectory(
                    prefix="bindcraft_"
                ) as tmp:
                    return self._run(request, Path(tmp))
            self.work_dir.mkdir(parents=True, exist_ok=True)
            return self._run(request, self.work_dir)
        except BindCraftError:
            raise
        except Exception as error:
            raise BindCraftError(
                f"BindCraft generation failed: {error}"
            ) from error

    def _run(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> list[NormalizedCandidate]:
        artifacts = self.executor.execute(request, work_dir)
        return self.parser.parse(request, artifacts)
