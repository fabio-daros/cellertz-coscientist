from cellertz.evaluation.benchmarks.bcma import (
    select_bcma_candidates,
    split_bcma_inputs_and_labels,
)
from cellertz.schemas import ProteinCandidate


def make_candidate(candidate_id: str, target: str):
    return ProteinCandidate(
        id=candidate_id,
        sequence="MKTIIALSYIFCLVFAD",
        design_type="protein",
        target=target,
    )


def test_select_bcma_candidates():
    candidates = [
        make_candidate("bcma-1", "BCMA"),
        make_candidate("cd19-1", "CD19"),
        make_candidate("bcma-2", "BCMA"),
    ]

    selected = select_bcma_candidates(candidates)

    assert len(selected) == 2
    assert all(candidate.target == "BCMA" for candidate in selected)


def test_split_bcma_inputs_and_labels():
    bcma = make_candidate("bcma-1", "BCMA")
    bcma.experimental_labels["ysd_binding_1000nM"] = True

    cd19 = make_candidate("cd19-1", "CD19")
    cd19.experimental_labels["ysd_binding_1000nM"] = False

    candidates, labels = split_bcma_inputs_and_labels([bcma, cd19])

    assert len(candidates) == 1
    assert candidates[0].id == "bcma-1"
    assert labels["bcma-1"]["ysd_binding_1000nM"] is True
    assert "cd19-1" not in labels
