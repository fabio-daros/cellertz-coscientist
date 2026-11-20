"""Which hotspot blocks a plan actually declared.

Blocks are not stored on the plan. The plan records which suggestion each
field was adopted from and drops that origin the moment a person edits the
value, so following the origin back is the only statement about the plan that
is still true. Anything else is refused by name rather than audited.
"""

from __future__ import annotations

import pytest

from cellertz.application.plan_configuration_suggestion import declared_hotspot_blocks

BLOCKS = [{"id": "A", "residues": ["A19", "A56"], "priority": 1}]
SUGGESTIONS = [
    {
        "suggestion_id": "suggestion_1",
        "suggestions": [
            {"field": "binder_length", "suggested": 80},
            {"field": "hotspot_assumption", "suggested": "A19,A56", "blocks": BLOCKS},
        ],
    }
]
ADOPTED = {
    "hotspot_assumption": "A19,A56",
    "hotspot_assumption_provenance": "adopted_suggestion",
    "hotspot_assumption_suggestion_id": "suggestion_1",
}


def test_an_adopted_suggestion_gives_back_its_blocks() -> None:
    found = declared_hotspot_blocks(ADOPTED, SUGGESTIONS)
    assert found["status"] == "adopted"
    assert found["blocks"] == BLOCKS
    assert found["residues"] == "A19,A56"


def test_residues_edited_after_adoption_refuse_the_blocks() -> None:
    found = declared_hotspot_blocks({**ADOPTED, "hotspot_assumption": "A19"}, SUGGESTIONS)
    assert found["status"] == "residues_changed"
    assert found["blocks"] == []


def test_a_hand_written_hotspot_set_has_no_blocks_and_says_so() -> None:
    found = declared_hotspot_blocks(
        {"hotspot_assumption": "A19,A56", "hotspot_assumption_provenance": "user"},
        SUGGESTIONS,
    )
    assert found["status"] == "not_adopted"
    assert found["blocks"] == []


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({**ADOPTED, "hotspot_assumption_suggestion_id": "gone"}, "suggestion_absent"),
        ({**ADOPTED, "hotspot_assumption_suggestion_id": ""}, "origin_incomplete"),
    ],
)
def test_an_origin_that_leads_nowhere_is_named(fields: dict, status: str) -> None:
    assert declared_hotspot_blocks(fields, SUGGESTIONS)["status"] == status


def test_a_suggestion_without_a_hotspot_field_is_not_read_as_empty_blocks() -> None:
    suggestions = [{"suggestion_id": "suggestion_1", "suggestions": [{"field": "binder_length", "suggested": 80}]}]
    assert declared_hotspot_blocks(ADOPTED, suggestions)["status"] == "suggestion_has_no_hotspots"


def test_an_older_suggestion_without_blocks_is_adopted_with_none() -> None:
    suggestions = [
        {
            "suggestion_id": "suggestion_1",
            "suggestions": [{"field": "hotspot_assumption", "suggested": "A19,A56"}],
        }
    ]
    found = declared_hotspot_blocks(ADOPTED, suggestions)
    assert found["status"] == "adopted"
    assert found["blocks"] == []
