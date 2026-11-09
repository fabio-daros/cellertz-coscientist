from pathlib import Path

import pandas as pd

from cellertz.schemas import ProteinCandidate

EXPERIMENTAL_COLUMNS = [
    # Binding
    "binder_by_YSD_1000nM",
    "binder_by_YSD_500nM",
    "binder_by_YSD_100nM",

    # Affinity
    "kd_fit_quality",
    "kd_R2",
    "neg_log10_Kd_M",
    "binder_by_estimated_KD_1000nM",
    "binder_by_estimated_KD_500nM",
    "binder_by_estimated_KD_100nM",
    "binder_by_estimated_KD_10nM",

    # Functional activity
    "BCMA_activity_ratio",
    "BCMA_activity_gain",
    "CD19_activity_ratio",
    "CD19_activity_gain",
    "CD22_activity_ratio",
    "CD22_activity_gain",
    "overall_activity_ratio",
    "overall_activity_gain",
]


def clean_value(value):
    if pd.isna(value):
        return None
    return value


def clean_float(value) -> float | None:
    if pd.isna(value):
        return None

    return float(value)


def infer_target(campaign: str) -> str | None:
    campaign_upper = campaign.upper()

    for target in ("BCMA", "CD19", "CD22"):
        if target in campaign_upper:
            return target

    return None


def add_experimental_labels(candidate: ProteinCandidate, row) -> None:
    for column in EXPERIMENTAL_COLUMNS:
        value = row.get(column)

        if pd.notna(value):
            candidate.experimental_labels[column] = value


def load_chow2026_candidates(path: str | Path) -> list[ProteinCandidate]:
    df = pd.read_excel(path)

    candidates: list[ProteinCandidate] = []

    for _, row in df.iterrows():
        campaign = str(row["campaign"])

        candidate = ProteinCandidate(
            id=str(row["binder_id"]),
            sequence=str(row["binder_sequence"]),
            design_type="protein",
            source="Chow et al. 2026",
            target=infer_target(campaign),
            metadata={
                "campaign": campaign,
                "campaign_short": clean_value(row.get("campaign_short")),
                "binder_creation_method": clean_value(row.get("binder_creation_method")),
                "binder_type": clean_value(row.get("binder_type")),
                "target_sequence": clean_value(row.get("target_sequence")),
            },
            interface_metrics={
                "ipsae": clean_float(row.get("Average_ipSAE")),
                "iptm": clean_float(row.get("Average_ipTM_af")),
                "complex_plddt": clean_float(row.get("Average_complex_plddt")),
                "pae_interaction": clean_float(row.get("Average_pae_interaction")),
                "confidence": clean_float(row.get("Average_confidence_score")),
                "ipsae_max": clean_float(row.get("Average_ipSAE_max")),
                "ipsae_min": clean_float(row.get("Average_ipSAE_min")),
            },
            developability_metrics={
                "net_charge": row.get("binder_seq_net_charge"),
                "hydrophobic_residues": row.get("n_hydrophobic_residues"),
            },
        )

        add_experimental_labels(candidate, row)

        candidates.append(candidate)

    return candidates
