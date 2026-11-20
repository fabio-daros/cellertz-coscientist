"""The hotspot audit as the live results document carries it.

The journey service knows nothing of plans or suggestions, so the join happens
at the transport. A result the audit cannot speak about keeps the reason, never
a zero that would read as a run that missed its target.
"""

from __future__ import annotations

import pytest

from cellertz.application.conversation_transport import _add_hotspot_audit

ATTEMPT = "attempt.2LUTUOX4"


def document(**result):
    base = {"attempt_id": ATTEMPT, "plan_id": "plan_1"}
    base.update(result)
    return {"request_id": "request_1", "results": [base]}


def reader(found):
    return lambda plan_id: found


def audit_of(doc) -> dict:
    return doc["results"][0]["hotspot_audit"]


def test_a_plan_with_declared_blocks_is_measured() -> None:
    from cellertz.application.attempt_evidence import (
        AttemptFiles,
        geometric_contacts,
        load_catalog,
        workspace_root,
    )

    root = workspace_root()
    item = load_catalog(
        (AttemptFiles(review_json=root / "src" / "manual-attempt-2LUTUOX4.json", pdb_dir=root / "public" / "manual-review" / ATTEMPT),)
    )[ATTEMPT]
    hit = geometric_contacts(item)["models"]["model1"]["calculated"]["target_residues"][:2]
    blocks = [{"id": "A", "residues": [f"A{number}" for number in hit]}]
    doc = document()
    _add_hotspot_audit(doc, reader({"status": "adopted", "blocks": blocks, "residues": "declared"}))
    audit = audit_of(doc)
    assert audit["status"] == "calculated"
    assert audit["blocks"][0]["contact_n"] == 2
    assert audit["declared_residues"] == "declared"
    assert audit["measures_overlap_not_steering"] is True


@pytest.mark.parametrize(
    ("declared", "status"),
    [
        ({"status": "not_adopted", "blocks": []}, "not_adopted"),
        ({"status": "residues_changed", "blocks": []}, "residues_changed"),
        (None, "not_available"),
    ],
)
def test_a_plan_without_blocks_keeps_the_reason(declared, status) -> None:
    doc = document()
    _add_hotspot_audit(doc, reader(declared))
    assert audit_of(doc) == {"status": status, "blocks": []}


def test_an_attempt_outside_the_catalogue_is_named_not_zero() -> None:
    doc = document(attempt_id="attempt.ffffffff")
    _add_hotspot_audit(doc, reader({"status": "adopted", "blocks": [{"id": "A", "residues": ["A56"]}]}))
    assert audit_of(doc) == {"status": "attempt_not_in_catalog", "blocks": []}


def test_a_result_without_a_plan_is_not_audited() -> None:
    doc = document(plan_id=None)
    _add_hotspot_audit(doc, reader({"status": "adopted", "blocks": [{"id": "A", "residues": ["A56"]}]}))
    assert audit_of(doc) == {"status": "not_available", "blocks": []}


def test_a_document_without_results_is_left_alone() -> None:
    doc = {"request_id": "request_1"}
    _add_hotspot_audit(doc, reader({"status": "adopted", "blocks": []}))
    assert doc == {"request_id": "request_1"}
