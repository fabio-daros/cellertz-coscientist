"""The pairing the authorization chain asks about.

Four places used to carry the same conjunction: this profile, this tool name,
this target scope. They now ask one table. What the table is for is the
pairing: each half of an invalid combination appears in it, and the
combination still has to be refused.
"""

from __future__ import annotations

import pytest

from cellertz.application.scientific_runner_readiness import (
    CD22_PROFILE_ID,
    DOCKER_PROFILE_ID,
    PDL1_PROFILE_ID,
    TACI_PROFILE_ID,
    registered_identity,
    registered_tool_target,
)


@pytest.mark.parametrize(
    ("profile_id", "tool_name", "target_scope"),
    [
        (PDL1_PROFILE_ID, "bindcraft", "PDL1"),
        (DOCKER_PROFILE_ID, "bindcraft", "PDL1"),
        (CD22_PROFILE_ID, "mber", "CD22"),
        (TACI_PROFILE_ID, "mber", "TACI"),
    ],
)
def test_the_registered_triples_are_accepted(profile_id, tool_name, target_scope) -> None:
    assert registered_identity(profile_id, tool_name, target_scope) is True


@pytest.mark.parametrize(
    ("profile_id", "tool_name", "target_scope", "why"),
    [
        (PDL1_PROFILE_ID, "mber", "PDL1", "a profile used with a tool it is not registered for"),
        (CD22_PROFILE_ID, "bindcraft", "CD22", "bindcraft against a target it was never run on"),
        (CD22_PROFILE_ID, "mber", "TACI", "the right tool, the wrong target for this profile"),
        (TACI_PROFILE_ID, "mber", "CD22", "the two mBER profiles are not interchangeable"),
        (PDL1_PROFILE_ID, "bindcraft", "CD22", "every half is registered; the pair is not"),
        ("pdl1-bindcraft-historical-v2", "bindcraft", "PDL1", "an unregistered profile"),
        (None, "bindcraft", "PDL1", "no profile at all"),
        (PDL1_PROFILE_ID, "bindcraft", None, "no target at all"),
    ],
)
def test_a_combination_nobody_registered_is_refused(profile_id, tool_name, target_scope, why) -> None:
    assert registered_identity(profile_id, tool_name, target_scope) is False, why


def test_the_pair_check_keeps_the_same_pairing_where_no_profile_is_chosen_yet() -> None:
    """A draft names a generator and a target before any profile exists."""

    assert registered_tool_target("bindcraft", "PDL1") is True
    assert registered_tool_target("mber", "CD22") is True
    assert registered_tool_target("mber", "TACI") is True
    # The combinations that must stay out, each half of them registered.
    assert registered_tool_target("bindcraft", "CD22") is False
    assert registered_tool_target("mber", "PDL1") is False
    assert registered_tool_target("rfdiffusion", "PDL1") is False
    assert registered_tool_target("bindcraft", None) is False


def test_widening_the_gate_did_not_admit_an_unknown_tool() -> None:
    """The other generators the request draft knows about are not runnable."""

    for tool in ("rfdiffusion", "bindcraft2", "rfantibody", "fixture", ""):
        assert registered_tool_target(tool, "PDL1") is False
        assert registered_tool_target(tool, "CD22") is False
