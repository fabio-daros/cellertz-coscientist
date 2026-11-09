from pathlib import Path

import pandas as pd

from cellertz.tools.protein_scoring.boltz2_collector import (
    collect_prediction_root,
)


ROOT = Path(
    "outputs/"
    "boltz_results_boltz2_pilot/"
    "predictions"
)

OUTPUT = Path(
    "outputs/"
    "boltz2_pilot_ensemble_summary_blind.csv"
)


ensembles = collect_prediction_root(
    ROOT,
    expected_samples=5,
)

# Important:
# blind IDs are assigned without using role/YSD.
ensembles = sorted(
    ensembles,
    key=lambda item: item.candidate_id,
)

rows = []

for index, item in enumerate(
    ensembles,
    start=1,
):
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
            "ptm_mean": summary.ptm_mean,
            "protein_iptm_mean": (
                summary.protein_iptm_mean
            ),
            "complex_plddt_mean": (
                summary.complex_plddt_mean
            ),
            "complex_iplddt_mean": (
                summary.complex_iplddt_mean
            ),
            "structure_confidence_mean": (
                summary.structure_confidence_mean
            ),
        }
    )

df = pd.DataFrame(rows)

df.to_csv(
    OUTPUT,
    index=False,
)

print(df.to_string(index=False))
print(f"\nSaved: {OUTPUT}")
