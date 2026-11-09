import json
import re

from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median, pstdev


_MODEL_INDEX_RE = re.compile(r"_model_(\d+)(?:\.json)?$")


@dataclass(frozen=True)
class Boltz2Sample:
    sample_id: str
    iptm: float
    ptm: float | None = None
    protein_iptm: float | None = None
    complex_plddt: float | None = None
    complex_iplddt: float | None = None
    structure_confidence: float | None = None
    model_index: int | None = None


def extract_model_index(name: str) -> int | None:
    match = _MODEL_INDEX_RE.search(name)

    if match is None:
        return None

    return int(match.group(1))


@dataclass(frozen=True)
class Boltz2EnsembleSummary:
    n_predictions: int

    iptm_mean: float
    iptm_median: float
    iptm_std: float
    iptm_min: float
    iptm_max: float

    ptm_mean: float | None = None
    protein_iptm_mean: float | None = None
    complex_plddt_mean: float | None = None
    complex_iplddt_mean: float | None = None
    structure_confidence_mean: float | None = None
    iptm_model_0: float | None = None


def _optional_mean(
    values: list[float | None],
) -> float | None:
    present = [
        value
        for value in values
        if value is not None
    ]

    if not present:
        return None

    return mean(present)


def _sample_model_index(sample: Boltz2Sample) -> int | None:
    if sample.model_index is not None:
        return sample.model_index

    return extract_model_index(sample.sample_id)


def model_0_iptm(samples: list[Boltz2Sample]) -> float | None:
    matches = [
        sample
        for sample in samples
        if _sample_model_index(sample) == 0
    ]

    if len(matches) > 1:
        raise ValueError(
            "Multiple Boltz2 samples have model_index 0."
        )

    if not matches:
        return None

    return matches[0].iptm


def load_boltz2_sample(
    path: str | Path,
) -> Boltz2Sample:
    path = Path(path)

    with path.open() as handle:
        data = json.load(handle)

    # Local Boltz output commonly calls this
    # confidence_score; API representations may
    # expose structure_confidence.
    structure_confidence = data.get(
        "structure_confidence",
        data.get("confidence_score"),
    )

    return Boltz2Sample(
        sample_id=path.stem,
        iptm=float(data["iptm"]),
        model_index=extract_model_index(path.name),
        ptm=(
            float(data["ptm"])
            if data.get("ptm") is not None
            else None
        ),
        protein_iptm=(
            float(data["protein_iptm"])
            if data.get("protein_iptm") is not None
            else None
        ),
        complex_plddt=(
            float(data["complex_plddt"])
            if data.get("complex_plddt") is not None
            else None
        ),
        complex_iplddt=(
            float(data["complex_iplddt"])
            if data.get("complex_iplddt") is not None
            else None
        ),
        structure_confidence=(
            float(structure_confidence)
            if structure_confidence is not None
            else None
        ),
    )


def summarize_boltz2_ensemble(
    samples: list[Boltz2Sample],
) -> Boltz2EnsembleSummary:
    if not samples:
        raise ValueError(
            "At least one Boltz2 sample is required."
        )

    iptm_values = [
        sample.iptm
        for sample in samples
    ]

    return Boltz2EnsembleSummary(
        n_predictions=len(samples),
        iptm_mean=mean(iptm_values),
        iptm_median=median(iptm_values),
        iptm_std=(
            pstdev(iptm_values)
            if len(iptm_values) > 1
            else 0.0
        ),
        iptm_min=min(iptm_values),
        iptm_max=max(iptm_values),
        ptm_mean=_optional_mean(
            [sample.ptm for sample in samples]
        ),
        protein_iptm_mean=_optional_mean(
            [
                sample.protein_iptm
                for sample in samples
            ]
        ),
        complex_plddt_mean=_optional_mean(
            [
                sample.complex_plddt
                for sample in samples
            ]
        ),
        complex_iplddt_mean=_optional_mean(
            [
                sample.complex_iplddt
                for sample in samples
            ]
        ),
        structure_confidence_mean=_optional_mean(
            [
                sample.structure_confidence
                for sample in samples
            ]
        ),
        iptm_model_0=model_0_iptm(samples),
    )
