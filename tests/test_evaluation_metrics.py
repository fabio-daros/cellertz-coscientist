from cellertz.evaluation.metrics import (
    enrichment_factor_at_k,
    precision_at_k,
    recall_at_k,
)


def test_precision_at_k():
    labels = [True, False, True, False]

    assert precision_at_k(labels, 2) == 0.5


def test_recall_at_k():
    labels = [True, False, True, False]

    assert recall_at_k(labels, 2) == 0.5


def test_enrichment_factor_at_k():
    labels = [True, False, False, False]

    assert enrichment_factor_at_k(labels, 1) == 4.0
