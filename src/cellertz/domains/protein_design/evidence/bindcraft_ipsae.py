from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from cellertz.domains.protein_design.agents.generation import (
    NormalizedCandidate,
)
from cellertz.tools.protein_scoring.ipsae import (
    IpSAEConfig,
    ProteinIpSAECalculator,
)


@dataclass(frozen=True)
class BindCraftModelArtifacts:
    model_number: int
    pdb_path: Path
    pae_path: Path


@dataclass(frozen=True)
class BindCraftResolvedArtifacts:
    candidate_id: str
    design: str
    execution_id: str
    models: tuple[BindCraftModelArtifacts, ...]


@dataclass(frozen=True)
class BindCraftIpSAEModelEvidence:
    model_number: int
    ipsae: float
    ipsae_ab: float
    ipsae_ba: float
    pdb_path: Path
    pae_path: Path
    pdb_sha256: str
    pae_sha256: str
    residue_order_sha256: str | None = None


@dataclass(frozen=True)
class BindCraftIpSAEEvidence:
    candidate_id: str
    design: str
    execution_id: str
    models: tuple[BindCraftIpSAEModelEvidence, ...]
    ipsae_mean: float
    ipsae_min: float
    ipsae_max: float
    n_models: int
    ranking_value: float
    model_aggregation: str
    protocol_id: str
    is_probability: bool = False
    recommended_as_hard_filter: bool = False


class BindCraftArtifactResolver:
    def __init__(
        self,
        *,
        output_root: str | Path,
    ) -> None:
        self.output_root = Path(output_root)

    def resolve(
        self,
        candidate: NormalizedCandidate,
    ) -> BindCraftResolvedArtifacts:
        if candidate.generator != "bindcraft":
            raise ValueError(
                "BindCraft artifact resolution requires "
                "generator='bindcraft'."
            )

        design = candidate.generator_metadata.get("design")
        if not isinstance(design, str) or not design:
            raise ValueError(
                "BindCraft candidate is missing generator_metadata.design."
            )

        provenance = candidate.generator_metadata.get("provenance")
        if provenance is None:
            raise ValueError(
                "BindCraft candidate is missing provenance."
            )
        if not isinstance(provenance, dict):
            raise TypeError(
                "BindCraft candidate provenance must be a mapping."
            )

        execution = provenance.get("execution")
        if execution is None:
            raise ValueError(
                "BindCraft candidate is missing execution provenance."
            )
        if not isinstance(execution, dict):
            raise TypeError(
                "BindCraft candidate execution provenance "
                "must be a mapping."
            )

        execution_id = execution.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError(
                "BindCraft candidate is missing execution_id."
            )

        designs_dir = (
            self.output_root
            / execution_id
            / "designs"
        )

        pdb_dir = (
            designs_dir
            / "MPNN"
            / "Relaxed"
        )

        pae_dir = (
            designs_dir
            / "CellertzEvidence"
            / "PAE"
        )

        pdb_models = _discover_models(
            directory=pdb_dir,
            regex=re.compile(
                rf"^{re.escape(design)}_model(\d+)\.pdb$"
            ),
        )

        pae_models = _discover_models(
            directory=pae_dir,
            regex=re.compile(
                rf"^{re.escape(design)}_model(\d+)_pae\.npz$"
            ),
        )

        if not pdb_models:
            raise FileNotFoundError(
                f"No relaxed PDB models found for design {design!r}."
            )

        if not pae_models:
            raise FileNotFoundError(
                f"No PAE models found for design {design!r}."
            )

        if set(pdb_models) != set(pae_models):
            raise ValueError(
                "BindCraft PDB and PAE model sets do not match."
            )

        models = tuple(
            BindCraftModelArtifacts(
                model_number=model_number,
                pdb_path=pdb_models[model_number],
                pae_path=pae_models[model_number],
            )
            for model_number in sorted(pdb_models)
        )

        return BindCraftResolvedArtifacts(
            candidate_id=candidate.candidate_id,
            design=design,
            execution_id=execution_id,
            models=models,
        )


class BindCraftIpSAEEvidenceProvider:
    def __init__(
        self,
        *,
        resolver: BindCraftArtifactResolver,
        ipsae_config: IpSAEConfig,
        calculator: ProteinIpSAECalculator | None = None,
    ) -> None:
        self.resolver = resolver
        self.ipsae_config = ipsae_config
        self.calculator = (
            calculator
            if calculator is not None
            else ProteinIpSAECalculator()
        )

    def evaluate(
        self,
        candidate: NormalizedCandidate,
    ) -> BindCraftIpSAEEvidence:
        resolved = self.resolver.resolve(candidate)

        model_evidence: list[
            BindCraftIpSAEModelEvidence
        ] = []

        for artifacts in resolved.models:
            result = self.calculator.calculate(
                pae_path=artifacts.pae_path,
                structure_path=artifacts.pdb_path,
                config=self.ipsae_config,
            )

            sidecar = artifacts.pae_path.with_name(
                artifacts.pae_path.name.replace("_pae.npz", "_residue_order.json")
            )
            model_evidence.append(
                BindCraftIpSAEModelEvidence(
                    model_number=artifacts.model_number,
                    ipsae=result.score,
                    ipsae_ab=result.ipsae_ab,
                    ipsae_ba=result.ipsae_ba,
                    pdb_path=artifacts.pdb_path,
                    pae_path=artifacts.pae_path,
                    pdb_sha256=_file_sha256(
                        artifacts.pdb_path
                    ),
                    pae_sha256=_file_sha256(
                        artifacts.pae_path
                    ),
                    residue_order_sha256=(
                        _file_sha256(sidecar) if sidecar.is_file() else None
                    ),
                )
            )

        scores = [
            item.ipsae
            for item in model_evidence
        ]

        ipsae_mean = sum(scores) / len(scores)

        return BindCraftIpSAEEvidence(
            candidate_id=resolved.candidate_id,
            design=resolved.design,
            execution_id=resolved.execution_id,
            models=tuple(model_evidence),
            ipsae_mean=ipsae_mean,
            ipsae_min=min(scores),
            ipsae_max=max(scores),
            n_models=len(scores),
            ranking_value=ipsae_mean,
            model_aggregation="mean",
            protocol_id=self.ipsae_config.protocol_id,
        )


def _discover_models(
    *,
    directory: Path,
    regex: re.Pattern[str],
) -> dict[int, Path]:
    if not directory.is_dir():
        return {}

    models: dict[int, Path] = {}

    for path in directory.iterdir():
        if not path.is_file():
            continue

        match = regex.fullmatch(path.name)
        if match is None:
            continue

        model_number = int(match.group(1))

        if model_number in models:
            raise ValueError(
                f"Duplicate artifact for model {model_number}."
            )

        models[model_number] = path

    return models


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()
