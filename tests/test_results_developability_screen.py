"""The developability screen as the live results document carries it.

The screen needs no plan, only the verified PDB. A result it cannot speak
about keeps the reason: a design that was never screened must not read as one
that passed.
"""

from __future__ import annotations

from cellertz.application.conversation_transport import _add_developability_screen

ATTEMPT = "attempt.2LUTUOX4"


def document(**result):
    base = {"attempt_id": ATTEMPT, "plan_id": "plan_1"}
    base.update(result)
    return {"request_id": "request_1", "results": [base]}


def test_a_verified_attempt_is_screened_per_model() -> None:
    doc = document()
    _add_developability_screen(doc)
    screen = doc["results"][0]["developability_screen"]
    assert screen["status"] == "calculated"
    assert screen["models"]["model1"]["verdict"] == "passed_measured_filters"
    assert screen["models"]["model1"]["unscreened"] == ["occlusion_footprint"]


def test_an_attempt_outside_the_catalogue_is_named_not_passed() -> None:
    doc = document(attempt_id="attempt.ffffffff")
    _add_developability_screen(doc)
    assert doc["results"][0]["developability_screen"] == {"status": "attempt_not_in_catalog", "models": {}}


def test_a_result_with_no_attempt_is_not_screened() -> None:
    doc = document(attempt_id=None)
    _add_developability_screen(doc)
    assert doc["results"][0]["developability_screen"] == {"status": "not_available", "models": {}}


def test_a_document_without_results_is_left_alone() -> None:
    doc = {"request_id": "request_1"}
    _add_developability_screen(doc)
    assert doc == {"request_id": "request_1"}
