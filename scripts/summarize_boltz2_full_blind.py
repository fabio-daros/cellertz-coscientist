from pathlib import Path

import pandas as pd

from cellertz.tools.protein_scoring.boltz2_collector import (
    collect_prediction_directory,
)
from cellertz.tools.protein_scoring.boltz2_ensemble import (
    load_boltz2_sample,
)


YAML_DIR = Path("outputs/boltz2_yamls")
RESULTS_ROOT = Path("outputs/boltz2_full")
OUTPUT = Path("outputs/bcma_boltz2_v2_summary_blind.csv")
EXPECTED_N = 766
EXPECTED_SAMPLES = 5


def _complete_prediction_dirs() -> dict[str, Path]:
    found: dict[str, Path] = {}

    for prediction_dir in RESULTS_ROOT.glob(
        "boltz_results_*/predictions/*"
    ):
        if not prediction_dir.is_dir():
            continue

        try:
            collected = collect_prediction_directory(
                prediction_dir,
                expected_samples=EXPECTED_SAMPLES,
            )
        except ValueError:
            continue

        found.setdefault(
            collected.candidate_id,
            prediction_dir,
        )

    unified = RESULTS_ROOT / "predictions"

    if unified.is_dir():
        for prediction_dir in unified.iterdir():
            if not prediction_dir.is_dir():
                continue

            try:
                collected = collect_prediction_directory(
                    prediction_dir,
                    expected_samples=EXPECTED_SAMPLES,
                )
            except ValueError:
                continue

            found.setdefault(
                collected.candidate_id,
                prediction_dir,
            )

    return found


expected = {
    path.stem
    for path in YAML_DIR.glob("*.yaml")
}
found = _complete_prediction_dirs()
missing = sorted(expected - set(found))
unexpected = sorted(set(found) - expected)

if missing or unexpected:
    raise SystemExit(
        "Candidate set mismatch: "
        f"missing={len(missing)} "
        f"unexpected={len(unexpected)}"
    )

ensembles = [
    collect_prediction_directory(
        found[candidate_id],
        expected_samples=EXPECTED_SAMPLES,
    )
    for candidate_id in sorted(found)
]

if len(ensembles) != EXPECTED_N:
    raise SystemExit(
        f"Expected {EXPECTED_N} ensembles, "
        f"found {len(ensembles)}"
    )

rows = []

for index, item in enumerate(ensembles, start=1):
    summary = item.summary

    rows.append(
        {
            "blind_id": f"P{index:03d}",
            "candidate_id": item.candidate_id,
            "n_predictions": summary.n_predictions,
            "iptm_mean": summary.iptm_mean,
            "iptm_median": summary.iptm_median,
            "iptm_std": summary.iptm_std,
            "iptm_min": summary.iptm_min,
            "iptm_max": summary.iptm_max,
            "model_0_iptm": next(
                load_boltz2_sample(path).iptm
                for path in item.sample_paths
                if path.name.endswith("_model_0.json")
            ),
            "ptm_mean": summary.ptm_mean,
            "protein_iptm_mean": summary.protein_iptm_mean,
            "complex_plddt_mean": summary.complex_plddt_mean,
            "complex_iplddt_mean": summary.complex_iplddt_mean,
            "structure_confidence_mean": (
                summary.structure_confidence_mean
            ),
        }
    )

df = pd.DataFrame(rows)
numeric = df.select_dtypes(include="number").drop(
    columns=["n_predictions"]
)

if df["candidate_id"].duplicated().any():
    raise SystemExit("Duplicate candidate_id in blind summary.")

if (df["n_predictions"] != EXPECTED_SAMPLES).any():
    raise SystemExit("Not all ensembles have 5 samples.")

if df.isna().any().any():
    raise SystemExit("NaN present in blind summary.")

if not (
    (numeric >= 0) & (numeric <= 1)
).all().all():
    raise SystemExit("Values outside [0, 1].")

df.to_csv(OUTPUT, index=False)

print("candidates", len(df))
print("unique", df["candidate_id"].nunique())
print("n_predictions", df["n_predictions"].unique().tolist())
print("nan_cells", int(df.isna().sum().sum()))
print(
    "iptm_mean_range",
    float(df["iptm_mean"].min()),
    float(df["iptm_mean"].max()),
)
print("std_min", float(df["iptm_std"].min()))
print("std_max", float(df["iptm_std"].max()))
print(f"Saved: {OUTPUT}")
