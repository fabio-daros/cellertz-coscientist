from pathlib import Path

import pandas as pd


BLIND = Path(
    "outputs/boltz2_pilot_ensemble_summary_blind.csv"
)

MANIFEST = Path(
    "outputs/boltz2_pilot_manifest.csv"
)

OUTPUT = Path(
    "outputs/boltz2_pilot_exploratory_unblinded.csv"
)


blind = pd.read_csv(BLIND)
manifest = pd.read_csv(MANIFEST)

df = blind.merge(
    manifest,
    on="candidate_id",
    how="left",
    validate="one_to_one",
)

if df["YSD"].isna().any():
    raise ValueError(
        "Some candidates could not be matched "
        "to the pilot manifest."
    )

df["delta_boltz_vs_ipsae"] = (
    df["iptm_mean"] - df["ipSAE"]
)

df["rank_ipsae"] = (
    df["ipSAE"]
    .rank(
        ascending=False,
        method="min",
    )
    .astype(int)
)

df["rank_boltz_mean"] = (
    df["iptm_mean"]
    .rank(
        ascending=False,
        method="min",
    )
    .astype(int)
)

columns = [
    "blind_id",
    "role",
    "candidate_id",
    "campaign",
    "YSD",
    "ipSAE",
    "iptm_mean",
    "iptm_median",
    "iptm_std",
    "iptm_min",
    "iptm_max",
    "delta_boltz_vs_ipsae",
    "rank_ipsae",
    "rank_boltz_mean",
]

df = df[columns]

df.to_csv(
    OUTPUT,
    index=False,
)

print(df.to_string(index=False))
print(f"\nSaved: {OUTPUT}")
