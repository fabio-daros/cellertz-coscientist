from pathlib import Path

import pandas as pd

from cellertz.evaluation.datasets.bcma_features import (
    BCMA_FEATURES_V1,
    PRIMARY_LABEL,
    SECONDARY_LABELS,
)


DATASET = (
    "data/external/chow_2026/"
    "41551_2026_1790_MOESM5_ESM.xlsx"
)

CLUSTERS = (
    "outputs/"
    "bcma_sequence_cluster_membership.csv"
)

OUTPUT = (
    "outputs/"
    "bcma_training_dataset_v1.csv"
)


df = pd.read_excel(DATASET)

bcma = df[
    df["campaign"].astype(str).str.contains(
        "BCMA",
        case=False,
        na=False,
    )
    & df[PRIMARY_LABEL].notna()
].copy()


feature_columns = BCMA_FEATURES_V1


metadata = pd.DataFrame(
    {
        "candidate_id": bcma["binder_id"],
        "campaign": bcma["campaign"],
        "campaign_short": bcma["campaign_short"],
        "sequence": bcma["binder_sequence"],
        "target_sequence": bcma["target_sequence"],
    }
)


cluster_df = pd.read_csv(CLUSTERS)

cluster_df = cluster_df[
    [
        "binder_id",
        "sequence_cluster_90",
        "sequence_cluster_80",
    ]
].rename(
    columns={
        "binder_id": "candidate_id",
    }
)


training = metadata.merge(
    cluster_df,
    on="candidate_id",
    how="left",
)


for column in feature_columns:
    training[column] = bcma[
        column
    ].to_numpy()


training["label_YSD_1000nM"] = (
    bcma[PRIMARY_LABEL]
    .astype(bool)
    .astype(int)
    .to_numpy()
)


for label in SECONDARY_LABELS:
    output_name = (
        "label_"
        + label.removeprefix("binder_by_")
    )

    training[output_name] = (
        bcma[label]
        .astype("Int64")
        .to_numpy()
    )


Path("outputs").mkdir(
    exist_ok=True,
)

training.to_csv(
    OUTPUT,
    index=False,
)


print("=== BCMA TRAINING DATASET v1 ===")
print(f"Candidates: {len(training)}")
print(
    f"Primary positives: "
    f"{training['label_YSD_1000nM'].sum()}"
)
print(
    f"Safe features: "
    f"{len(feature_columns)}"
)

print("\nFeatures:")
for feature in feature_columns:
    print(f" - {feature}")

print(
    f"\nSaved: {OUTPUT}"
)
