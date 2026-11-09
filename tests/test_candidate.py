from cellertz.schemas import MRNASequenceCandidate, ProteinCandidate, Score


def test_protein_candidate():
    candidate = ProteinCandidate(
        id="protein-001",
        sequence="MKTIIALSYIFCLVFAD",
        design_type="protein",
        target="BCMA",
    )

    candidate.scores.append(
        Score(
            name="binding_score",
            value=0.92,
            source="benchmark",
        )
    )

    assert candidate.target == "BCMA"
    assert candidate.scores[0].value == 0.92


def test_mrna_candidate():
    candidate = MRNASequenceCandidate(
        id="mrna-001",
        sequence="AUGGCC",
        design_type="mrna",
        cds="AUGGCC",
    )

    assert candidate.design_type == "mrna"
    assert candidate.cds == "AUGGCC"


def test_protein_experimental_labels():
    candidate = ProteinCandidate(
        id="protein-001",
        sequence="MKTIIALSYIFCLVFAD",
        design_type="protein",
        target="BCMA",
    )

    candidate.experimental_labels["ysd_binding_1000nM"] = True

    assert candidate.experimental_labels["ysd_binding_1000nM"] is True
