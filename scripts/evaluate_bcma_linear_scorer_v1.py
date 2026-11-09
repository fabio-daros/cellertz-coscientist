from pathlib import Path

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from cellertz.evaluation.datasets.bcma_features import (
    BCMA_FEATURES_V1,
)


DATASET = "outputs/bcma_training_dataset_v1.csv"

POSITIVE_CAMPAIGNS = [
    "BCMA BindCraft Campaign 1",
    "BCMA RFDiffusion Campaign 5",
]

TOP_K = [10, 25, 50]


df = pd.read_csv(DATASET)

X = df[BCMA_FEATURES_V1].astype(float)
y = df["label_YSD_1000nM"].astype(int)
groups = df["campaign"].astype(str)

assert X.shape == (766, 57)
assert int(y.sum()) == 34
assert not X.isna().any().any()

print("=== BCMA LINEAR SCORER v1 ===")
print(f"Candidates: {len(X)}")
print(f"Positives: {int(y.sum())}")
print(f"Features: {X.shape[1]}")

results = []

for held_out in POSITIVE_CAMPAIGNS:
    train_mask = groups != held_out
    test_mask = groups == held_out

    X_train = X.loc[train_mask]
    y_train = y.loc[train_mask]

    X_test = X.loc[test_mask]
    y_test = y.loc[test_mask]

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=1.0,
                    class_weight="balanced",
                    max_iter=5000,
                ),
            ),
        ]
    )

    model.fit(X_train, y_train)

    ml_score = model.predict_proba(X_test)[:, 1]

    ipsae_score = X_test["Average_ipSAE"].to_numpy()

    print(f"\n=== HELD OUT: {held_out} ===")
    print(
        f"Train: {len(X_train)} "
        f"(positives={int(y_train.sum())})"
    )
    print(
        f"Test:  {len(X_test)} "
        f"(positives={int(y_test.sum())})"
    )

    for model_name, scores in [
        ("ipSAE", ipsae_score),
        ("linear_v1", ml_score),
    ]:
        ap = average_precision_score(
            y_test,
            scores,
        )

        roc = roc_auc_score(
            y_test,
            scores,
        )

        prevalence = y_test.mean()

        print(
            f"\n{model_name}"
            f"\n  AP:             {ap:.4f}"
            f"\n  ROC-AUC:        {roc:.4f}"
            f"\n  AP enrichment:  "
            f"{ap / prevalence:.2f}x"
        )

        ranked = pd.DataFrame(
            {
                "label": y_test.to_numpy(),
                "score": scores,
            }
        ).sort_values(
            "score",
            ascending=False,
        )

        for k in TOP_K:
            subset = ranked.head(k)
            hits = int(subset["label"].sum())

            precision = hits / len(subset)
            recall = hits / int(y_test.sum())
            ef = precision / prevalence

            print(
                f"  Top {k:2d}: "
                f"hits={hits:2d} "
                f"P={precision:.3f} "
                f"R={recall:.3f} "
                f"EF={ef:.2f}x"
            )

        results.append(
            {
                "held_out_campaign": held_out,
                "model": model_name,
                "ap": ap,
                "roc_auc": roc,
                "ap_enrichment":
                    ap / prevalence,
            }
        )

    coefficients = (
        pd.DataFrame(
            {
                "feature": BCMA_FEATURES_V1,
                "coefficient":
                    model.named_steps[
                        "model"
                    ].coef_[0],
            }
        )
        .assign(
            abs_coefficient=lambda x:
                x["coefficient"].abs()
        )
        .sort_values(
            "abs_coefficient",
            ascending=False,
        )
    )

    print(
        "\nTop 10 coefficients "
        "(diagnostic only):"
    )

    print(
        coefficients[
            [
                "feature",
                "coefficient",
            ]
        ]
        .head(10)
        .to_string(
            index=False,
            float_format=lambda x: f"{x:.4f}",
        )
    )


results_df = pd.DataFrame(results)

Path("outputs").mkdir(
    exist_ok=True,
)

results_df.to_csv(
    "outputs/bcma_linear_scorer_v1_results.csv",
    index=False,
)

print(
    "\nSaved: "
    "outputs/bcma_linear_scorer_v1_results.csv"
)
