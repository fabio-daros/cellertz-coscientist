from dataclasses import dataclass, field
from typing import Any


@dataclass
class Score:
    name: str
    value: float
    source: str | None = None


@dataclass
class Evidence:
    source: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DesignCandidate:
    id: str
    sequence: str
    design_type: str
    source: str | None = None
    target: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)
    predictions: dict[str, Any] = field(default_factory=dict)
    scores: list[Score] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    rank: int | None = None


@dataclass
class ProteinCandidate(DesignCandidate):
    structure_path: str | None = None
    interface_metrics: dict[str, float | None] = field(default_factory=dict)
    affinity_predictions: dict[str, float | None] = field(default_factory=dict)
    developability_metrics: dict[str, float | None] = field(default_factory=dict)
    experimental_labels: dict[str, Any] = field(default_factory=dict)


@dataclass
class MRNASequenceCandidate(DesignCandidate):
    cds: str | None = None
    five_prime_utr: str | None = None
    three_prime_utr: str | None = None

    translation_metrics: dict[str, float] = field(default_factory=dict)
    stability_metrics: dict[str, float] = field(default_factory=dict)
    manufacturability_metrics: dict[str, float] = field(default_factory=dict)
