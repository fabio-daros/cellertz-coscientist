from collections.abc import Sequence

import pandas as pd

from cellertz.schemas import ProteinCandidate


FEATURE_DIRECTIONS = {
    "ipsae": 1,
    "iptm": 1,
    "complex_plddt": 1,
    "pae_interaction": -1,
    "confidence": 1,
}


def rank_by_feature(
    candidates: list[ProteinCandidate],
    feature: str,
) -> list[ProteinCandidate]:
    direction = FEATURE_DIRECTIONS[feature]

    def sort_key(candidate: ProteinCandidate) -> float:
        value = candidate.interface_metrics.get(feature)

        if value is None:
            return float("-inf") if direction > 0 else float("inf")

        return value

    return sorted(
        candidates,
        key=sort_key,
        reverse=direction > 0,
    )


def percentile_composite_scores(
    candidates: Sequence[ProteinCandidate],
    features: Sequence[str],
) -> dict[str, float]:
    rows = []

    for candidate in candidates:
        row = {"id": candidate.id}

        for feature in features:
            value = candidate.interface_metrics.get(feature)
            direction = FEATURE_DIRECTIONS[feature]

            if value is None:
                row[feature] = None
            else:
                row[feature] = value * direction

        rows.append(row)

    df = pd.DataFrame(rows).set_index("id")

    percentile_df = df.rank(
        pct=True,
        method="average",
        na_option="bottom",
    )

    composite = percentile_df[list(features)].mean(axis=1)

    return composite.to_dict()


def rank_by_composite(
    candidates: list[ProteinCandidate],
    features: Sequence[str],
) -> list[ProteinCandidate]:
    scores = percentile_composite_scores(
        candidates,
        features,
    )

    return sorted(
        candidates,
        key=lambda candidate: scores[candidate.id],
        reverse=True,
    )
