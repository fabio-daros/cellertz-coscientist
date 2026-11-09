from dataclasses import dataclass
from pathlib import Path

from cellertz.tools.protein_scoring.boltz2_ensemble import (
    Boltz2EnsembleSummary,
    extract_model_index,
    load_boltz2_sample,
    summarize_boltz2_ensemble,
)


@dataclass(frozen=True)
class CollectedBoltz2Ensemble:
    prediction_stem: str
    candidate_id: str
    sample_paths: tuple[Path, ...]
    summary: Boltz2EnsembleSummary


def extract_candidate_id(prediction_stem: str) -> str:
    if "__" not in prediction_stem:
        if not prediction_stem:
            raise ValueError(
                "Prediction stem must not be empty."
            )
        return prediction_stem

    _, candidate_id = prediction_stem.split("__", 1)

    if not candidate_id:
        raise ValueError(
            f"Missing candidate_id in: {prediction_stem}"
        )

    return candidate_id


def _model_index(path: Path) -> int:
    index = extract_model_index(path.name)

    if index is None:
        raise ValueError(
            f"Cannot extract model index from {path.name}"
        )

    return index


def collect_prediction_directory(
    prediction_dir: str | Path,
    expected_samples: int | None = None,
) -> CollectedBoltz2Ensemble:
    prediction_dir = Path(prediction_dir)

    if not prediction_dir.is_dir():
        raise NotADirectoryError(prediction_dir)

    paths = sorted(
        prediction_dir.glob(
            "confidence_*_model_*.json"
        ),
        key=_model_index,
    )

    if not paths:
        raise ValueError(
            f"No confidence JSONs found in "
            f"{prediction_dir}"
        )

    model_indices = [
        _model_index(path)
        for path in paths
    ]

    if len(model_indices) != len(set(model_indices)):
        raise ValueError(
            f"Duplicate model indices in "
            f"{prediction_dir}"
        )

    if expected_samples is not None:
        if len(paths) != expected_samples:
            raise ValueError(
                f"{prediction_dir.name}: expected "
                f"{expected_samples} samples, found "
                f"{len(paths)}."
            )

    samples = [
        load_boltz2_sample(path)
        for path in paths
    ]

    summary = summarize_boltz2_ensemble(
        samples
    )

    stem = prediction_dir.name

    return CollectedBoltz2Ensemble(
        prediction_stem=stem,
        candidate_id=extract_candidate_id(stem),
        sample_paths=tuple(paths),
        summary=summary,
    )


def collect_prediction_root(
    predictions_root: str | Path,
    expected_samples: int | None = None,
) -> list[CollectedBoltz2Ensemble]:
    predictions_root = Path(predictions_root)

    if not predictions_root.is_dir():
        raise NotADirectoryError(
            predictions_root
        )

    directories = sorted(
        path
        for path in predictions_root.iterdir()
        if path.is_dir()
    )

    return [
        collect_prediction_directory(
            prediction_dir,
            expected_samples=expected_samples,
        )
        for prediction_dir in directories
    ]
