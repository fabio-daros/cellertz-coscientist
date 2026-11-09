"""RFdiffusion generator adapter.

RFdiffusion native output is a backbone/structure, not a designed
sequence. ProteinMPNN or an equivalent step may attach a sequence
later. This adapter records structure provenance always and only
emits NormalizedCandidate.sequence when a designed sequence is
actually present.

GenerationRequest
        ↓
RFdiffusionAdapter
        ↓
RFdiffusionExecutor  → raw artifacts
RFdiffusionParser    → NormalizedCandidate[]

Poly-G / poly-A PDB traces stay in generator_metadata as
backbone_sequence. They are not treated as designed sequences.
"""

from __future__ import annotations

import json
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


ADAPTER_VERSION = "rfdiffusion_adapter_v1"
PLACEHOLDER_RESIDUES = frozenset({"A", "G", "X"})
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
AA3_TO_1 = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "UNK": "X",
}
_DESIGN_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_IDENTITY_JSON_KEYS = (
    "design",
    "contig",
    "target",
    "seed",
    "binder_chain",
    "sequence",
    "sequence_source",
    "sequence_source_version",
    "diffusion_parameters",
)


class RFdiffusionError(ValueError):
    """Structured RFdiffusion failure. Converted to GenerationResult."""


@dataclass(frozen=True)
class RFdiffusionArtifacts:
    output_dir: Path
    command: tuple[str, ...]
    rfdiffusion_version: str | None = None
    returncode: int = 0


class RFdiffusionExecutor(Protocol):
    def execute(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> RFdiffusionArtifacts:
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


def _candidate_id(design: str) -> str:
    cleaned = _DESIGN_SAFE.sub("_", design).strip("._-")
    if not cleaned:
        raise RFdiffusionError(
            "RFdiffusion design name produced an empty id."
        )
    return f"rfdiffusion_{cleaned}"


def _require_sequence(raw: str, design: str) -> str:
    sequence = "".join(raw.split()).upper()
    if not sequence:
        raise RFdiffusionError(
            f"RFdiffusion design {design} is missing sequence."
        )
    invalid = sorted(
        {
            residue
            for residue in sequence
            if residue not in AMINO_ACIDS
        }
    )
    if invalid:
        raise RFdiffusionError(
            f"RFdiffusion design {design} has invalid residues: "
            + ",".join(invalid)
        )
    return sequence


def _is_backbone_placeholder(sequence: str) -> bool:
    residues = set(sequence)
    return len(residues) == 1 and residues <= PLACEHOLDER_RESIDUES


def pdb_chain_sequence(path: Path, chain: str) -> str:
    residues: dict[int, str] = {}
    for line in path.read_text().splitlines():
        if not line.startswith("ATOM"):
            continue
        atom = line[12:16].strip()
        if atom != "CA":
            continue
        if len(line) < 26 or line[21] != chain:
            continue
        resname = line[17:20].strip()
        resseq = int(line[22:26])
        residues[resseq] = AA3_TO_1.get(resname, "X")
    return "".join(residues[index] for index in sorted(residues))


def _read_fasta(path: Path) -> str:
    chunks: list[str] = []
    started = False
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(">"):
            if started:
                break
            started = True
            continue
        if started:
            chunks.append(stripped)
    return "".join(chunks)


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise RFdiffusionError(
            f"{path.name} must contain a JSON object."
        )
    return payload


def _native_metrics(sidecar: dict[str, object]) -> dict[str, object]:
    metrics: dict[str, object] = {}
    for key, value in sidecar.items():
        if key in _IDENTITY_JSON_KEYS or _forbidden_metadata_key(key):
            continue
        metrics[key] = value
    return metrics


class RFdiffusionParser:
    """Parse RFdiffusion directories. Does not execute diffusion."""

    def parse(
        self,
        request: GenerationRequest,
        artifacts: RFdiffusionArtifacts,
    ) -> list[NormalizedCandidate]:
        output_dir = artifacts.output_dir
        pdbs = sorted(
            path
            for path in output_dir.glob("*.pdb")
            if path.is_file() and not path.name.startswith(".")
        )
        if not pdbs:
            raise RFdiffusionError(
                "RFdiffusion artifacts contain no PDB files."
            )

        parsed: list[NormalizedCandidate] = []
        seen: set[str] = set()
        input_hash = request_input_hash(request)
        default_chain = str(
            request.constraints.get("binder_chain") or "B"
        )

        for pdb_path in pdbs:
            candidate = _parse_design(
                request=request,
                artifacts=artifacts,
                pdb_path=pdb_path,
                input_hash=input_hash,
                default_chain=default_chain,
            )
            if candidate.candidate_id in seen:
                raise RFdiffusionError(
                    "Duplicate RFdiffusion design: "
                    f"{candidate.candidate_id}."
                )
            seen.add(candidate.candidate_id)
            parsed.append(candidate)

        if len(parsed) < request.n_candidates:
            raise RFdiffusionError(
                "RFdiffusion returned "
                f"{len(parsed)} designs, expected "
                f"{request.n_candidates}."
            )
        return parsed[: request.n_candidates]


def _parse_design(
    request: GenerationRequest,
    artifacts: RFdiffusionArtifacts,
    pdb_path: Path,
    input_hash: str,
    default_chain: str,
) -> NormalizedCandidate:
    sidecar_path = pdb_path.with_suffix(".json")
    sidecar = (
        _load_json(sidecar_path) if sidecar_path.is_file() else {}
    )
    binder_chain = str(
        sidecar.get("binder_chain") or default_chain
    )
    design = str(sidecar.get("design") or pdb_path.stem)
    backbone_sequence = pdb_chain_sequence(pdb_path, binder_chain)
    sequence, sequence_source = _designed_sequence(
        pdb_path=pdb_path,
        sidecar=sidecar,
        design=design,
        backbone_sequence=backbone_sequence,
    )
    if sequence is None:
        raise RFdiffusionError(
            f"RFdiffusion design {design} has structure "
            "but no designed sequence. Backbone-only "
            "placeholders are not NormalizedCandidate.sequence."
        )

    if (
        backbone_sequence
        and not _is_backbone_placeholder(backbone_sequence)
        and len(sequence) != len(backbone_sequence)
    ):
        raise RFdiffusionError(
            f"RFdiffusion design {design} sequence length "
            "does not match the binder backbone."
        )

    candidate_id = _candidate_id(design)
    native_metrics = _native_metrics(sidecar)
    return NormalizedCandidate(
        candidate_id=candidate_id,
        sequence=sequence,
        target_id=request.target_id,
        generator="rfdiffusion",
        generator_metadata={
            "structure_path": pdb_path.name,
            "design": design,
            "contig": sidecar.get("contig"),
            "target": sidecar.get("target") or request.target_id,
            "seed": sidecar.get("seed"),
            "binder_chain": binder_chain,
            "backbone_sequence": backbone_sequence or None,
            "structure_generator": "rfdiffusion",
            "sequence_source": sequence_source,
            "sequence_source_version": sidecar.get(
                "sequence_source_version"
            )
            or request.constraints.get(
                "sequence_source_version"
            ),
            "diffusion_parameters": sidecar.get(
                "diffusion_parameters"
            )
            or {},
            "native_metrics": native_metrics,
            "provenance": {
                "adapter_version": ADAPTER_VERSION,
                "rfdiffusion_version": (
                    artifacts.rfdiffusion_version
                ),
                "command": list(artifacts.command),
                "input_hash": input_hash,
                "returncode": artifacts.returncode,
            },
        },
    )


def _designed_sequence(
    pdb_path: Path,
    sidecar: dict[str, object],
    design: str,
    backbone_sequence: str,
) -> tuple[str | None, str | None]:
    fasta = None
    for suffix in (".fa", ".fasta"):
        candidate = pdb_path.with_suffix(suffix)
        if candidate.is_file():
            fasta = _require_sequence(_read_fasta(candidate), design)
            break

    json_sequence = sidecar.get("sequence")
    if isinstance(json_sequence, str) and json_sequence.strip():
        json_sequence = _require_sequence(json_sequence, design)
    else:
        json_sequence = None

    if fasta and json_sequence and fasta != json_sequence:
        raise RFdiffusionError(
            f"RFdiffusion design {design} FASTA and JSON "
            "sequences do not match."
        )
    if fasta:
        return fasta, str(
            sidecar.get("sequence_source") or "fasta"
        )
    if json_sequence:
        return json_sequence, str(
            sidecar.get("sequence_source") or "json"
        )
    if backbone_sequence and not _is_backbone_placeholder(
        backbone_sequence
    ):
        return backbone_sequence, "pdb"
    return None, None


class FixtureRFdiffusionExecutor:
    """Replay recorded RFdiffusion artifacts. No GPU, no binary."""

    def __init__(
        self,
        artifact_dir: str | Path,
        rfdiffusion_version: str = "fixture_outputs_v1",
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.rfdiffusion_version = rfdiffusion_version

    def execute(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> RFdiffusionArtifacts:
        del request, work_dir
        if not any(self.artifact_dir.glob("*.pdb")):
            raise RFdiffusionError(
                "Fixture RFdiffusion artifacts are missing PDB files."
            )
        return RFdiffusionArtifacts(
            output_dir=self.artifact_dir,
            command=("fixture-rfdiffusion",),
            rfdiffusion_version=self.rfdiffusion_version,
        )


class LocalRFdiffusionExecutor:
    """Translate an RFdiffusion request and delegate to a runtime."""

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
    ) -> RFdiffusionArtifacts:
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
            raise RFdiffusionError(
                result.error
                or "RFdiffusion process failed."
            )

        return RFdiffusionArtifacts(
            output_dir=work_dir,
            command=command,
            rfdiffusion_version=str(
                request.constraints.get("rfdiffusion_version")
            )
            if request.constraints.get("rfdiffusion_version")
            else None,
            returncode=result.return_code or 0,
        )


def _local_command(
    request: GenerationRequest,
    work_dir: Path,
) -> list[str]:
    constraints = request.constraints
    raw_command = constraints.get("rfdiffusion_command")
    if raw_command is not None:
        if not isinstance(raw_command, (list, tuple)):
            raise RFdiffusionError(
                "constraints.rfdiffusion_command must be a list."
            )
        if not raw_command:
            raise RFdiffusionError(
                "constraints.rfdiffusion_command is empty."
            )
        return _substitute_work_dir(
            [str(part) for part in raw_command],
            work_dir,
        )

    rfdiffusion_bin = constraints.get("rfdiffusion_bin")
    if not rfdiffusion_bin:
        raise RFdiffusionError(
            "RFdiffusion executable is not configured. "
            "Provide constraints.rfdiffusion_command or "
            "rfdiffusion_bin."
        )
    command = [str(rfdiffusion_bin)]
    config = constraints.get("config")
    if config:
        command.extend(["--config-name", str(config)])
    return command


class RFdiffusionAdapter:
    """Coordinates executor and parser. Does not score or rank."""

    name = "rfdiffusion"
    version = ADAPTER_VERSION

    def __init__(
        self,
        executor: RFdiffusionExecutor | None = None,
        parser: RFdiffusionParser | None = None,
        work_dir: str | Path | None = None,
        runtime: ExecutionRuntime | None = None,
    ) -> None:
        self.runtime = runtime or LocalExecutionRuntime()
        self.executor = executor or LocalRFdiffusionExecutor(
            runtime=self.runtime
        )
        self.parser = parser or RFdiffusionParser()
        self.work_dir = (
            None if work_dir is None else Path(work_dir)
        )

    def generate(
        self,
        request: GenerationRequest,
    ) -> list[NormalizedCandidate]:
        if request.generator != self.name:
            raise RFdiffusionError(
                "RFdiffusionAdapter received generator="
                f"{request.generator}."
            )

        try:
            if self.work_dir is None:
                with tempfile.TemporaryDirectory(
                    prefix="rfdiffusion_"
                ) as tmp:
                    return self._run(request, Path(tmp))
            self.work_dir.mkdir(parents=True, exist_ok=True)
            return self._run(request, self.work_dir)
        except RFdiffusionError:
            raise
        except Exception as error:
            raise RFdiffusionError(
                f"RFdiffusion generation failed: {error}"
            ) from error

    def _run(
        self,
        request: GenerationRequest,
        work_dir: Path,
    ) -> list[NormalizedCandidate]:
        artifacts = self.executor.execute(request, work_dir)
        return self.parser.parse(request, artifacts)
