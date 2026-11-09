def precision_at_k(
    ranked_labels: list[bool],
    k: int,
) -> float:
    top_k = ranked_labels[:k]

    if not top_k:
        return 0.0

    return sum(top_k) / len(top_k)


def recall_at_k(
    ranked_labels: list[bool],
    k: int,
) -> float:
    total_positives = sum(ranked_labels)

    if total_positives == 0:
        return 0.0

    return sum(ranked_labels[:k]) / total_positives


def enrichment_factor_at_k(
    ranked_labels: list[bool],
    k: int,
) -> float:
    if not ranked_labels:
        return 0.0

    prevalence = sum(ranked_labels) / len(ranked_labels)

    if prevalence == 0:
        return 0.0

    precision = precision_at_k(ranked_labels, k)

    return precision / prevalence
