from cellertz.schemas import ProteinCandidate


def select_bcma_candidates(
    candidates: list[ProteinCandidate],
) -> list[ProteinCandidate]:
    return [
        candidate
        for candidate in candidates
        if candidate.target == "BCMA"
    ]


def split_bcma_inputs_and_labels(
    candidates: list[ProteinCandidate],
) -> tuple[list[ProteinCandidate], dict[str, dict]]:
    bcma_candidates = select_bcma_candidates(candidates)

    labels = {
        candidate.id: candidate.experimental_labels.copy()
        for candidate in bcma_candidates
    }

    return bcma_candidates, labels
