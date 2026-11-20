"""The hotspot audit as the attempt inspection serves it.

The audit is only meaningful when the plan behind the attempt declared blocks.
Every other case is named rather than reported as zero contact, because "0 of 0
reached" about a run nobody gave a block to reads as a failure to hit a target
that was never set.
"""

from __future__ import annotations

from cellertz.application.attempt_evidence import AttemptFiles, load_catalog, workspace_root
from cellertz.application.attempt_inspection import _hotspot_audit, _plan_of_attempt

ATTEMPT = "attempt.2LUTUOX4"
JOURNEYS = [{"plan_id": "plan_1", "execution": {"attempt_id": ATTEMPT, "plan_id": "plan_1"}}]


def item() -> dict:
    root = workspace_root()
    catalog = load_catalog(
        (
            AttemptFiles(
                review_json=root / "src" / "manual-attempt-2LUTUOX4.json",
                pdb_dir=root / "public" / "manual-review" / ATTEMPT,
            ),
        )
    )
    return catalog[ATTEMPT]


def reader(result: dict | None):
    return lambda plan_id: result


def test_the_plan_behind_an_attempt_is_found_through_its_saved_execution() -> None:
    assert _plan_of_attempt(ATTEMPT, JOURNEYS) == "plan_1"
    assert _plan_of_attempt("attempt.other", JOURNEYS) is None


def test_declared_blocks_are_audited_against_the_structure() -> None:
    from cellertz.application.attempt_evidence import geometric_contacts

    hit = geometric_contacts(item())["models"]["model1"]["calculated"]["target_residues"]
    blocks = [{"id": "A", "residues": [f"A{number}" for number in hit[:2]]}]
    audit = _hotspot_audit(ATTEMPT, item(), JOURNEYS, reader({"status": "adopted", "blocks": blocks, "residues": "x"}))
    assert audit["status"] == "calculated"
    assert audit["plan_id"] == "plan_1"
    assert audit["blocks"][0]["contact_n"] == 2
    # These blocks were declared on a plan, not handed to the generator.
    assert audit["measures_overlap_not_steering"] is True


def test_a_hand_written_hotspot_set_is_named_not_audited() -> None:
    audit = _hotspot_audit(ATTEMPT, item(), JOURNEYS, reader({"status": "not_adopted", "blocks": []}))
    assert audit["status"] == "not_adopted"
    assert audit["blocks"] == []
    assert audit["plan_id"] == "plan_1"


def test_an_attempt_without_a_saved_plan_says_so() -> None:
    audit = _hotspot_audit(ATTEMPT, item(), [], reader({"status": "adopted", "blocks": [{"id": "A"}]}))
    assert audit["status"] == "attempt_has_no_saved_plan"
    assert audit["blocks"] == []


def test_without_a_plan_store_the_audit_is_unavailable_not_empty() -> None:
    assert _hotspot_audit(ATTEMPT, item(), JOURNEYS, None)["status"] == "not_available"
    assert _hotspot_audit(ATTEMPT, item(), JOURNEYS, reader(None))["status"] == "not_available"
