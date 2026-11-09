"""Frozen Boltz2 v2 unblind and evaluation.

Run once after tag protein-boltz2-benchmark-v2.
Does not invent composites, weights, or thresholds.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    roc_auc_score,
)

from cellertz.evaluation.metrics import (
    enrichment_factor_at_k,
    precision_at_k,
    recall_at_k,
)
from cellertz.tools.protein_scoring.boltz2_yaml import (
    yaml_filename,
)


BLIND = Path("outputs/bcma_boltz2_v2_summary_blind.csv")
LABELS = Path("docs/experiments/bcma_training_dataset_v1.csv")
UNBLINDED = Path("outputs/bcma_boltz2_v2_unblinded.csv")
RESULTS = Path("outputs/bcma_boltz2_v2_results.csv")
PROVENANCE = Path("outputs/boltz2_bcma_run_provenance.txt")

EXPECTED_SHA256 = (
    "d174f2a00629d1011619b6b1575b1a0a320c8ec4875fc3f6be89976fdc7d3751"
)
EXPECTED_N = 766
EXPECTED_POSITIVES = 34
TOP_K = (10, 25, 50)

BINDCRAFT_1 = "BCMA BindCraft Campaign 1"
RFD5 = "BCMA RFDiffusion Campaign 5"

# ipSAE v1 ruler. Abort if the merge does not reproduce it.
IPSAE_RULER = {
    ("global", "ap"): 0.0924,
    ("global", "roc_auc"): 0.7535,
    (BINDCRAFT_1, "ap"): 0.3082,
    (BINDCRAFT_1, "roc_auc"): 0.6453,
    (RFD5, "ap"): 0.2501,
    (RFD5, "roc_auc"): 0.8214,
}

SIGNALS = (
    ("ipSAE", "Average_ipSAE", False),
    ("paper_ipTM", "Average_ipTM_af", False),
    ("boltz_model_0_ipTM", "model_0_iptm", False),
    ("boltz_mean_ipTM", "iptm_mean", False),
    ("boltz_median_ipTM", "iptm_median", False),
    ("boltz_min_ipTM", "iptm_min", False),
    ("boltz_std", "iptm_std", True),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)

    return digest.hexdigest()


def ranking_score(series: pd.Series, invert: bool) -> pd.Series:
    if invert:
        return -series.astype(float)

    return series.astype(float)


def split_metrics(
    labels: pd.Series,
    scores: pd.Series,
    top_k: tuple[int, ...] = TOP_K,
) -> dict[str, float | int]:
    y = labels.astype(int).to_numpy()
    score = scores.to_numpy()
    ranked_labels = [
        bool(label)
        for label, _ in sorted(
            zip(y, score, strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
    ]
    prevalence = float(y.mean())
    result: dict[str, float | int] = {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "prevalence": prevalence,
        "ap": float(average_precision_score(y, score)),
        "roc_auc": float(roc_auc_score(y, score)),
    }
    result["ap_enrichment"] = result["ap"] / prevalence

    for k in top_k:
        hits = int(sum(ranked_labels[:k]))
        result[f"top{k}_hits"] = hits
        result[f"top{k}_precision"] = precision_at_k(
            ranked_labels,
            k,
        )
        result[f"top{k}_recall"] = recall_at_k(
            ranked_labels,
            k,
        )
        result[f"top{k}_ef"] = enrichment_factor_at_k(
            ranked_labels,
            k,
        )

    return result


def load_unblinded() -> pd.DataFrame:
    digest = sha256_file(BLIND)

    if digest != EXPECTED_SHA256:
        raise SystemExit(
            "Blind CSV hash does not match the freeze. "
            f"expected={EXPECTED_SHA256} "
            f"found={digest}"
        )

    blind = pd.read_csv(BLIND)
    labels = pd.read_csv(LABELS)

    labels = labels[
        [
            "candidate_id",
            "campaign",
            "label_YSD_1000nM",
            "Average_ipSAE",
            "Average_ipTM_af",
        ]
    ].copy()
    labels["boltz_id"] = [
        Path(yaml_filename(candidate_id)).stem
        for candidate_id in labels["candidate_id"]
    ]

    merged = blind.merge(
        labels,
        left_on="candidate_id",
        right_on="boltz_id",
        how="inner",
        validate="one_to_one",
        suffixes=("", "_label"),
    )

    if len(merged) != EXPECTED_N:
        raise SystemExit(
            "Unblind merge is not 1:1 with 766 rows: "
            f"blind={len(blind)} labels={len(labels)} "
            f"merged={len(merged)}"
        )

    if int(merged["label_YSD_1000nM"].sum()) != EXPECTED_POSITIVES:
        raise SystemExit(
            "Primary label count is not 34 after merge."
        )

    if merged["label_YSD_1000nM"].isna().any():
        raise SystemExit("Missing YSD labels after merge.")

    return merged


def evaluate(df: pd.DataFrame) -> pd.DataFrame:
    splits = (
        ("global", pd.Series(True, index=df.index)),
        (BINDCRAFT_1, df["campaign"] == BINDCRAFT_1),
        (RFD5, df["campaign"] == RFD5),
    )
    rows = []

    for split_name, mask in splits:
        subset = df.loc[mask]

        for signal, column, invert in SIGNALS:
            metrics = split_metrics(
                subset["label_YSD_1000nM"],
                ranking_score(subset[column], invert),
            )
            rows.append(
                {
                    "split": split_name,
                    "signal": signal,
                    "column": column,
                    "inverted": invert,
                    **metrics,
                }
            )

    results = pd.DataFrame(rows)

    for (split_name, metric), expected in IPSAE_RULER.items():
        observed = results.loc[
            (results["split"] == split_name)
            & (results["signal"] == "ipSAE"),
            metric,
        ].iloc[0]

        if round(float(observed), 4) != expected:
            raise SystemExit(
                "ipSAE ruler mismatch after unblind: "
                f"{split_name} {metric} "
                f"expected={expected:.4f} "
                f"observed={observed:.4f}"
            )

    return results


def print_results(results: pd.DataFrame) -> None:
    print("=== BOLTZ2 v2 UNBLIND (frozen protocol) ===")
    print(f"Blind CSV: {BLIND}")
    print(f"SHA256:    {EXPECTED_SHA256}")
    print("No composite, weight, or threshold was fit.")
    print("Boltz std is inverted (lower std ranks better).")

    for split_name in results["split"].unique():
        subset = results[results["split"] == split_name]
        baseline_ap = float(
            subset.loc[subset["signal"] == "ipSAE", "ap"].iloc[0]
        )
        n = int(subset["n"].iloc[0])
        positives = int(subset["positives"].iloc[0])

        print(f"\n=== {split_name} (n={n}, positives={positives}) ===")

        for _, row in subset.iterrows():
            delta = float(row["ap"]) - baseline_ap
            print(
                f"\n{row['signal']}"
                f"\n  AP:            {row['ap']:.4f}"
                f"  (Δ vs ipSAE {delta:+.4f})"
                f"\n  ROC-AUC:       {row['roc_auc']:.4f}"
                f"\n  AP enrichment: {row['ap_enrichment']:.2f}x"
            )

            for k in TOP_K:
                print(
                    f"  Top {k:2d}: "
                    f"hits={int(row[f'top{k}_hits']):2d} "
                    f"P={row[f'top{k}_precision']:.3f} "
                    f"R={row[f'top{k}_recall']:.3f} "
                    f"EF={row[f'top{k}_ef']:.2f}x"
                )


def append_provenance() -> None:
    with PROVENANCE.open("a") as handle:
        handle.write("---\n")
        handle.write("ysd_unblinded=true\n")
        handle.write(
            "unblind_labels=docs/experiments/bcma_training_dataset_v1.csv\n"
        )
        handle.write(
            "unblind_script=scripts/evaluate_boltz2_v2.py\n"
        )
        handle.write(
            f"unblinded_csv={UNBLINDED.as_posix()}\n"
        )
        handle.write(
            f"results_csv={RESULTS.as_posix()}\n"
        )
        handle.write("protocol=docs/experiments/boltz2_v2_evaluation_protocol.md\n")
        handle.write("no_composite=true\n")
        handle.write("no_threshold=true\n")
        handle.write("no_weight_search=true\n")


def main() -> None:
    df = load_unblinded()
    results = evaluate(df)

    UNBLINDED.parent.mkdir(exist_ok=True)
    df.to_csv(UNBLINDED, index=False)
    results.to_csv(RESULTS, index=False)
    append_provenance()
    print_results(results)
    print(f"\nSaved: {UNBLINDED}")
    print(f"Saved: {RESULTS}")


if __name__ == "__main__":
    main()
