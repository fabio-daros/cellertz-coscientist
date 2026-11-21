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


def test_the_screen_declares_the_native_ligand_footprint_it_measured_against() -> None:
    """PD-1's site on PD-L1, from the vault, translated into this structure's
    numbering. Nothing is left unscreened once it is declared."""

    doc = document()
    _add_developability_screen(doc)
    screen = doc["results"][0]["developability_screen"]
    assert screen["native_ligand"]["status"] == "declared"
    assert screen["native_ligand"]["source"] == "pdl1_pd1_interface_4zqk"
    assert screen["models"]["model1"]["unscreened"] == []
    occlusion = screen["models"]["model1"]["occlusion"]
    assert (occlusion["footprint"], occlusion["ligand_residues"]) == (12, 22)
    # Measured, not a gate: this campaign declared no limit.
    assert occlusion["is_a_gate"] is False


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
