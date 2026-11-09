from pathlib import Path
import math
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

YAML_DIR = Path("outputs/boltz2_yamls")
RESULTS_ROOT = Path("outputs/boltz2_full")
PREDICTIONS_ROOT = RESULTS_ROOT / "predictions"
RESUME_BATCH_DIR = Path("outputs/boltz2_resume_batches")
LOG_DIR = RESULTS_ROOT / "logs"

BATCH_SIZE = 40
EXPECTED_SAMPLES = 5
BOLTZ = Path(".venv/bin/boltz")


def expected_candidate_ids(yaml_dir: Path) -> set[str]:
    return {
        path.stem
        for path in yaml_dir.glob("*.yaml")
    }


def is_complete_prediction_dir(
    prediction_dir: Path,
    expected_samples: int = EXPECTED_SAMPLES,
) -> bool:
    if not prediction_dir.is_dir():
        return False

    confidence_files = list(
        prediction_dir.glob(
            "confidence_*_model_*.json"
        )
    )

    return len(confidence_files) == expected_samples


def discover_completed_ids(
    results_root: Path,
    expected_samples: int = EXPECTED_SAMPLES,
) -> set[str]:
    completed: set[str] = set()

    for prediction_dir in results_root.glob(
        "boltz_results_*/predictions/*"
    ):
        if is_complete_prediction_dir(
            prediction_dir,
            expected_samples,
        ):
            completed.add(prediction_dir.name)

    unified = results_root / "predictions"

    if unified.is_dir():
        for prediction_dir in unified.iterdir():
            if is_complete_prediction_dir(
                prediction_dir,
                expected_samples,
            ):
                completed.add(prediction_dir.name)

    return completed


def remaining_candidate_ids(
    yaml_dir: Path,
    results_root: Path,
    expected_samples: int = EXPECTED_SAMPLES,
) -> list[str]:
    expected = expected_candidate_ids(yaml_dir)
    completed = discover_completed_ids(
        results_root,
        expected_samples,
    )
    return sorted(expected - completed)


def _stage_batch(
    batch_index: int,
    yamls: list[Path],
    remaining: set[str],
    completed: set[str],
) -> Path:
    batch_ids = [path.stem for path in yamls]

    if not set(batch_ids).issubset(remaining):
        raise AssertionError(
            "Batch contains candidates that are "
            "not in the remaining set."
        )

    overlap = set(batch_ids) & completed

    if overlap:
        raise AssertionError(
            "Refusing to spend GPU on already "
            f"complete candidates: {sorted(overlap)}"
        )

    batch_path = (
        RESUME_BATCH_DIR / f"resume_{batch_index:03d}"
    )

    if batch_path.exists():
        shutil.rmtree(batch_path)

    batch_path.mkdir(parents=True)

    for yaml_path in yamls:
        shutil.copy2(
            yaml_path,
            batch_path / yaml_path.name,
        )

    staged = {
        path.stem
        for path in batch_path.glob("*.yaml")
    }

    if staged != set(batch_ids):
        raise AssertionError(
            "Staged batch YAMLs do not match "
            "the intended remaining candidates."
        )

    return batch_path


def _merge_predictions(batch_results: Path) -> None:
    predictions = batch_results / "predictions"

    if not predictions.is_dir():
        raise FileNotFoundError(predictions)

    PREDICTIONS_ROOT.mkdir(parents=True, exist_ok=True)

    for prediction_dir in predictions.iterdir():
        if not prediction_dir.is_dir():
            continue

        destination = PREDICTIONS_ROOT / prediction_dir.name

        if destination.exists():
            shutil.rmtree(destination)

        shutil.copytree(prediction_dir, destination)


def run_batch(
    batch_index: int,
    yamls: list[Path],
    remaining: set[str],
    completed: set[str],
) -> None:
    batch_path = _stage_batch(
        batch_index,
        yamls,
        remaining,
        completed,
    )
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"resume_{batch_index:03d}.log"

    command = [
        str(BOLTZ),
        "predict",
        str(batch_path),
        "--out_dir",
        str(RESULTS_ROOT),
        "--diffusion_samples",
        str(EXPECTED_SAMPLES),
        "--accelerator",
        "gpu",
        "--no_kernels",
        "--seed",
        "0",
    ]

    print(
        f"Resume batch {batch_index:03d}: "
        f"{len(yamls)} complexes",
        flush=True,
    )

    with log_path.open("w") as log:
        log.write(" ".join(command) + "\n")
        finished = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )

    if finished.returncode != 0:
        raise RuntimeError(
            f"Resume batch {batch_index:03d} failed. "
            f"See {log_path}"
        )

    _merge_predictions(
        RESULTS_ROOT / f"boltz_results_{batch_path.name}"
    )

    missing = [
        yaml_path.stem
        for yaml_path in yamls
        if not is_complete_prediction_dir(
            PREDICTIONS_ROOT / yaml_path.stem
        )
    ]

    if missing:
        raise RuntimeError(
            f"Resume batch {batch_index:03d} "
            f"incomplete: {missing}"
        )


def main() -> int:
    expected = expected_candidate_ids(YAML_DIR)
    completed = discover_completed_ids(RESULTS_ROOT)
    remaining_ids = sorted(expected - completed)
    pending = [
        YAML_DIR / f"{candidate_id}.yaml"
        for candidate_id in remaining_ids
    ]

    resume_batches = (
        math.ceil(len(pending) / BATCH_SIZE)
        if pending
        else 0
    )

    print(f"Expected candidates:       {len(expected)}")
    print(f"Already complete 5/5:      {len(completed)}")
    print(f"Remaining:                 {len(remaining_ids)}")
    print(f"Batch size:                {BATCH_SIZE}")
    print(
        f"Resume batches required:   {resume_batches}"
    )

    if not pending:
        return 0

    remaining_set = set(remaining_ids)

    for batch_index, start in enumerate(
        range(0, len(pending), BATCH_SIZE)
    ):
        run_batch(
            batch_index,
            pending[start:start + BATCH_SIZE],
            remaining_set,
            completed,
        )
        completed = discover_completed_ids(RESULTS_ROOT)
        remaining_set = expected - completed

    still_remaining = remaining_candidate_ids(
        YAML_DIR,
        RESULTS_ROOT,
    )

    if still_remaining:
        print(
            f"Still incomplete: {len(still_remaining)}",
            file=sys.stderr,
        )
        return 1

    print("All complexes have 5/5 samples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
