from pathlib import Path
import shutil
import pandas as pd

from cellertz.tools.protein_scoring.boltz2_yaml import yaml_filename

DATASET = (
    "data/external/chow_2026/"
    "41551_2026_1790_MOESM5_ESM.xlsx"
)

YAML_DIR = Path("outputs/boltz2_yamls")
PILOT_DIR = Path("outputs/boltz2_pilot")

LABEL = "binder_by_YSD_1000nM"
SCORE = "Average_ipSAE"

PILOT_DIR.mkdir(parents=True, exist_ok=True)

df = pd.read_excel(DATASET)

bcma = df[
    df["campaign"].astype(str).str.contains(
        "BCMA",
        case=False,
        na=False,
    )
    & df[LABEL].notna()
].copy()

bcma[LABEL] = bcma[LABEL].astype(int)

positive = bcma[bcma[LABEL] == 1]
negative = bcma[bcma[LABEL] == 0]

bc1_positive = positive[
    positive["campaign"]
    == "BCMA BindCraft Campaign 1"
]

rfd5_positive = positive[
    positive["campaign"]
    == "BCMA RFDiffusion Campaign 5"
]

median_negative = negative.iloc[
    (negative[SCORE] - negative[SCORE].median())
    .abs()
    .argsort()
    .iloc[0]
]

selected = [
    (
        "bindcraft_positive_high",
        bc1_positive.loc[bc1_positive[SCORE].idxmax()],
    ),
    (
        "rfd5_positive_high",
        rfd5_positive.loc[rfd5_positive[SCORE].idxmax()],
    ),
    (
        "positive_low_ipsae",
        positive.loc[positive[SCORE].idxmin()],
    ),
    (
        "negative_high_ipsae",
        negative.loc[negative[SCORE].idxmax()],
    ),
    (
        "negative_median_ipsae",
        median_negative,
    ),
]

records = []

for role, row in selected:
    candidate_id = str(row["binder_id"])

    source = YAML_DIR / yaml_filename(candidate_id)

    if not source.exists():
        raise FileNotFoundError(
            f"Missing YAML: {source}"
        )

    destination = (
        PILOT_DIR / f"{role}__{yaml_filename(candidate_id)}"
    )

    shutil.copy2(source, destination)

    records.append(
        {
            "role": role,
            "candidate_id": candidate_id,
            "campaign": row["campaign"],
            "YSD": int(row[LABEL]),
            "ipSAE": float(row[SCORE]),
            "yaml": destination.name,
        }
    )

summary = pd.DataFrame(records)

summary.to_csv(
    PILOT_DIR / "pilot_manifest.csv",
    index=False,
)

print(summary.to_string(index=False))
print(
    f"\nPilot files: "
    f"{len(list(PILOT_DIR.glob('*.yaml')))}"
)
