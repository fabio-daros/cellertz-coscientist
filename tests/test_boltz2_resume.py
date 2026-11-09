import importlib.util
from pathlib import Path


def _load_runner():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_boltz2_bcma_batches.py"
    )
    spec = importlib.util.spec_from_file_location(
        "run_boltz2_bcma_batches",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _touch_yaml(directory: Path, candidate_id: str) -> None:
    (directory / f"{candidate_id}.yaml").write_text(
        "version: 1\n"
    )


def _write_confidence(
    prediction_dir: Path,
    candidate_id: str,
    n: int,
) -> None:
    prediction_dir.mkdir(parents=True)

    for index in range(n):
        (
            prediction_dir
            / f"confidence_{candidate_id}_model_{index}.json"
        ).write_text("{}")


def test_global_scan_deduplicates_completed_candidates(
    tmp_path,
):
    runner = _load_runner()

    yaml_dir = tmp_path / "yamls"
    results = tmp_path / "results"
    yaml_dir.mkdir()

    _touch_yaml(yaml_dir, "done_a")
    _touch_yaml(yaml_dir, "done_b")
    _touch_yaml(yaml_dir, "missing")

    _write_confidence(
        results
        / "boltz_results_batch_000"
        / "predictions"
        / "done_a",
        "done_a",
        5,
    )
    _write_confidence(
        results
        / "boltz_results_batch_001"
        / "predictions"
        / "done_a",
        "done_a",
        5,
    )
    _write_confidence(
        results
        / "boltz_results_batch_001"
        / "predictions"
        / "done_b",
        "done_b",
        5,
    )
    _write_confidence(
        results
        / "boltz_results_batch_002"
        / "predictions"
        / "missing",
        "missing",
        3,
    )

    completed = runner.discover_completed_ids(results)
    remaining = runner.remaining_candidate_ids(
        yaml_dir,
        results,
    )

    assert completed == {"done_a", "done_b"}
    assert remaining == ["missing"]


def test_incomplete_dir_is_not_treated_as_complete(
    tmp_path,
):
    runner = _load_runner()
    prediction_dir = tmp_path / "candidate"

    _write_confidence(prediction_dir, "candidate", 4)

    assert runner.is_complete_prediction_dir(
        prediction_dir
    ) is False
