"""Protein Generation Agent v1.

Normalizes generator output. Does not score, rank, critique,
or select candidates.

GenerationRequest
        ↓
ProteinGenerationAgent
        ↓
GeneratorAdapter
        ↓
NormalizedCandidates
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
FORBIDDEN_FIELDS = (
    "ranking_score",
    "rank",
    "critic",
    "selected",
    "ysd",
    "ipsae",
)


@dataclass(frozen=True)
class GenerationRequest:
    target_id: str
    objective: str
    generator: str
    n_candidates: int
    constraints: dict[str, object] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class NormalizedCandidate:
    candidate_id: str
    sequence: str
    target_id: str
    generator: str
    generator_metadata: dict[str, object] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "sequence": self.sequence,
            "target_id": self.target_id,
            "generator": self.generator,
            "generator_metadata": dict(
                self.generator_metadata
            ),
        }


@dataclass(frozen=True)
class GenerationResult:
    status: Literal["complete", "failed"]
    request: GenerationRequest
    candidates: tuple[NormalizedCandidate, ...]
    adapter_name: str | None
    adapter_version: str | None
    input_hash: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "input_hash": self.input_hash,
            "error": self.error,
            "request": {
                "target_id": self.request.target_id,
                "objective": self.request.objective,
                "generator": self.request.generator,
                "n_candidates": self.request.n_candidates,
                "constraints": dict(self.request.constraints),
            },
            "candidates": [
                candidate.to_dict()
                for candidate in self.candidates
            ],
        }


class GeneratorAdapter(Protocol):
    name: str
    version: str

    def generate(
        self,
        request: GenerationRequest,
    ) -> list[NormalizedCandidate]:
        ...


def request_input_hash(request: GenerationRequest) -> str:
    payload = {
        "target_id": request.target_id,
        "objective": request.objective,
        "generator": request.generator,
        "n_candidates": request.n_candidates,
        "constraints": request.constraints,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _failed(
    request: GenerationRequest,
    input_hash: str,
    error: str,
    adapter: GeneratorAdapter | None = None,
) -> GenerationResult:
    return GenerationResult(
        status="failed",
        request=request,
        candidates=(),
        adapter_name=None if adapter is None else adapter.name,
        adapter_version=(
            None if adapter is None else adapter.version
        ),
        input_hash=input_hash,
        error=error,
    )


class FixtureGeneratorAdapter:
    """Deterministic in-process adapter. Not a real generator."""

    def __init__(
        self,
        name: str = "fixture",
        version: str = "fixture_v1",
    ) -> None:
        self.name = name
        self.version = version

    def generate(
        self,
        request: GenerationRequest,
    ) -> list[NormalizedCandidate]:
        digest = request_input_hash(request)
        short = digest[:12]
        length = _fixture_length(request)
        candidates = []

        for index in range(request.n_candidates):
            candidates.append(
                NormalizedCandidate(
                    candidate_id=(
                        f"{self.name}_{short}_{index:03d}"
                    ),
                    sequence=_fixture_sequence(
                        digest,
                        index,
                        length,
                    ),
                    target_id=request.target_id,
                    generator=self.name,
                    generator_metadata={
                        "adapter": self.name,
                        "adapter_version": self.version,
                        "index": index,
                    },
                )
            )

        return candidates


def _fixture_length(request: GenerationRequest) -> int:
    constraints = request.constraints
    length_min = int(constraints.get("length_min", 50))
    length_max = int(constraints.get("length_max", 100))

    if length_min < 1 or length_max < length_min:
        raise ValueError(
            "Invalid length constraints for fixture adapter."
        )

    return min(max(length_min, 50), length_max)


def _fixture_sequence(
    digest: str,
    index: int,
    length: int,
) -> str:
    offset = (int(digest[:8], 16) + index) % len(AMINO_ACIDS)
    return "".join(
        AMINO_ACIDS[(offset + position) % len(AMINO_ACIDS)]
        for position in range(length)
    )


class ProteinGenerationAgent:
    """Resolves a generator adapter and validates normalized output."""

    def __init__(
        self,
        adapters: dict[str, GeneratorAdapter] | None = None,
    ) -> None:
        self._adapters = dict(adapters or {})

        if "fixture" not in self._adapters:
            self._adapters["fixture"] = (
                FixtureGeneratorAdapter()
            )

        if "bindcraft" not in self._adapters:
            from cellertz.domains.protein_design.adapters.bindcraft import BindCraftAdapter

            self._adapters["bindcraft"] = BindCraftAdapter()

        if "rfdiffusion" not in self._adapters:
            from cellertz.domains.protein_design.adapters.rfdiffusion import RFdiffusionAdapter

            self._adapters["rfdiffusion"] = RFdiffusionAdapter()

        if "mber" not in self._adapters:
            from cellertz.domains.protein_design.adapters.mber import MBERAdapter

            self._adapters["mber"] = MBERAdapter()

    def generate(
        self,
        request: GenerationRequest,
    ) -> GenerationResult:
        input_hash = request_input_hash(request)

        if request.n_candidates < 0:
            return _failed(
                request,
                input_hash,
                "n_candidates must be >= 0.",
            )

        adapter = self._adapters.get(request.generator)

        if adapter is None:
            return _failed(
                request,
                input_hash,
                f"Unknown generator: {request.generator}",
            )

        try:
            raw = adapter.generate(request)
            candidates = _validate_candidates(
                request,
                adapter,
                raw,
            )
        except ValueError as error:
            return _failed(
                request,
                input_hash,
                str(error),
                adapter,
            )

        return GenerationResult(
            status="complete",
            request=request,
            candidates=tuple(candidates),
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            input_hash=input_hash,
            error=None,
        )


def _validate_candidates(
    request: GenerationRequest,
    adapter: GeneratorAdapter,
    raw: list[NormalizedCandidate],
) -> list[NormalizedCandidate]:
    if len(raw) != request.n_candidates:
        raise ValueError(
            "Adapter returned "
            f"{len(raw)} candidates, expected "
            f"{request.n_candidates}."
        )

    seen: set[str] = set()
    validated: list[NormalizedCandidate] = []

    for candidate in raw:
        if not isinstance(candidate, NormalizedCandidate):
            raise ValueError(
                "Adapter returned a candidate that is "
                "not a NormalizedCandidate."
            )

        for field_name in FORBIDDEN_FIELDS:
            if hasattr(candidate, field_name):
                raise ValueError(
                    "NormalizedCandidate must not include "
                    f"{field_name}."
                )

        if not candidate.candidate_id:
            raise ValueError(
                "Candidate is missing candidate_id."
            )

        if not candidate.sequence:
            raise ValueError(
                "Candidate is missing sequence."
            )

        if candidate.generator != adapter.name:
            raise ValueError(
                "Candidate generator does not match "
                "the adapter name."
            )

        if candidate.candidate_id in seen:
            raise ValueError(
                "Adapter returned duplicate candidate_id: "
                f"{candidate.candidate_id}"
            )

        seen.add(candidate.candidate_id)
        validated.append(
            NormalizedCandidate(
                candidate_id=candidate.candidate_id,
                sequence=candidate.sequence,
                target_id=candidate.target_id or request.target_id,
                generator=adapter.name,
                generator_metadata=dict(
                    candidate.generator_metadata
                ),
            )
        )

    return validated
