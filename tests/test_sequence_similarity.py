from cellertz.evaluation.sequence_similarity import (
    SimilarityEdge,
    cluster_from_edges,
    global_sequence_identity,
)


def test_identical_sequences_have_identity_one():
    identity = global_sequence_identity(
        "ACDEFGHIK",
        "ACDEFGHIK",
    )

    assert identity == 1.0


def test_different_sequences_have_identity_below_one():
    identity = global_sequence_identity(
        "ACDEFGHIK",
        "ACDEYGHIK",
    )

    assert 0.0 < identity < 1.0


def test_similarity_clusters_are_transitive():
    ids = ["a", "b", "c", "d"]

    edges = [
        SimilarityEdge("a", "b", 0.95),
        SimilarityEdge("b", "c", 0.92),
        SimilarityEdge("c", "d", 0.70),
    ]

    clusters = cluster_from_edges(
        ids,
        edges,
        threshold=0.90,
    )

    assert clusters["a"] == clusters["b"]
    assert clusters["b"] == clusters["c"]
    assert clusters["c"] != clusters["d"]
