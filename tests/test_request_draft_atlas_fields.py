"""Step 0 of the atlas procedure, extracted from the free-text intent.

Its unit of work is a complex — a target chain and the clinical binder or
native ligand beside it — never a bare target, because the partner is the
instrument every later step measures with. Today's draft asks for
``target_id`` and four runner parameters, and none of what the atlas needs
first.

``partner_direction`` is the field to be careful with. The atlas declares it
per target as ``contact_good``, and it is not a constant: touching the partner
is the failure on seven of its nine targets and the goal on two. It inverts
the sign of every contact percentage in the block classification, so nothing
here infers it from a target's name.
"""

from __future__ import annotations

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.request_draft import (
    PARTNER_DIRECTIONS,
    PATCHABLE,
    _accepted_atlas_value,
    _atlas_proposals,
)

ATLAS_FIELDS = (
    "source_structure",
    "target_chain",
    "partner_chains",
    "partner_direction",
    "clinical_anchor",
)


def _proposals(message: str) -> dict[str, object]:
    folded = " ".join(message.casefold().split())
    found = {}
    for proposal in _atlas_proposals(message, folded):
        found[proposal.name] = "AMBIGUOUS" if proposal.ambiguous else proposal.value
    return found


def test_every_step_zero_field_is_patchable() -> None:
    for name in ATLAS_FIELDS:
        assert name in PATCHABLE, name


def test_the_direction_is_closed_to_two_words() -> None:
    """A third value would leave every contact percentage uninterpretable."""

    assert PARTNER_DIRECTIONS == {"compete", "avoid"}
    assert _accepted_atlas_value("partner_direction", "AVOID") == "avoid"
    assert _accepted_atlas_value("partner_direction", " compete ") == "compete"
    for rejected in ("maybe", "orthogonal", "", "both", "true"):
        with pytest.raises(ApplicationContractError):
            _accepted_atlas_value("partner_direction", rejected)


def test_a_structure_identity_is_not_a_path() -> None:
    assert _accepted_atlas_value("source_structure", "7o52") == "7O52"
    for rejected in ("../7O52", "7O52.pdb", "/tmp/7O52", "7O5", "7O522", "7O5 2"):
        with pytest.raises(ApplicationContractError):
            _accepted_atlas_value("source_structure", rejected)


def test_chains_are_single_characters_and_a_partner_list_has_no_duplicate() -> None:
    assert _accepted_atlas_value("target_chain", "U") == "U"
    assert _accepted_atlas_value("partner_chains", "H, L") == "H,L"
    assert _accepted_atlas_value("partner_chains", "A,B,D") == "A,B,D"
    # A trailing comma is a typo, not another chain.
    assert _accepted_atlas_value("partner_chains", "H,") == "H"
    for rejected in ("HL", "H,H", "", ","):
        with pytest.raises(ApplicationContractError):
            _accepted_atlas_value("partner_chains", rejected)
    with pytest.raises(ApplicationContractError):
        _accepted_atlas_value("target_chain", "HL")


def test_the_clinical_anchor_is_bounded_text() -> None:
    assert _accepted_atlas_value("clinical_anchor", "cilta-cel (8HXQ/8HXR)") == (
        "cilta-cel (8HXQ/8HXR)"
    )
    # Whitespace is flattened first, so a wrapped name survives as one line.
    assert _accepted_atlas_value("clinical_anchor", "cilta-cel\n  VHH1") == "cilta-cel VHH1"
    for rejected in ("", "x" * 81, "anchor; rm -rf /", "m971 <script>", "a|b"):
        with pytest.raises(ApplicationContractError):
            _accepted_atlas_value("clinical_anchor", rejected)


def test_one_documented_form_carries_the_whole_complex() -> None:
    """CD22 as the atlas records it: 7O52, chain U, m971's H and L."""

    found = _proposals("design a VHH against 7O52:U/H,L")

    assert found["source_structure"] == "7O52"
    assert found["target_chain"] == "U"
    assert found["partner_chains"] == "H,L"


def test_an_unsettled_direction_is_left_for_the_agent_to_ask() -> None:
    """Absent beats guessed: the agent's governance forbids inferring it."""

    assert "partner_direction" not in _proposals("design a VHH against CD22")
    assert "partner_direction" not in _proposals("make me a binder for 7O52:U/H,L")


def test_the_phrases_that_do_settle_it() -> None:
    assert _proposals("a non-competing NK engager")["partner_direction"] == "avoid"
    assert _proposals("queremos algo que nao competir com o parceiro")["partner_direction"] == "avoid"
    assert _proposals("it should compete with m971")["partner_direction"] == "compete"
    assert _proposals("bind the same epitope as the Fab")["partner_direction"] == "compete"


def test_a_message_pulling_both_ways_is_ambiguous_not_a_coin_flip() -> None:
    """Reading this wrongly inverts every number in the classification."""

    found = _proposals("a non-competing engager that should compete with IgG")

    assert found["partner_direction"] == "AMBIGUOUS"


def test_a_negated_compete_is_never_read_as_compete() -> None:
    """The first CD22 trial: "does not compete with the partner" became compete.

    The phrase list names a few negations whole (non-competing, nao competir),
    and any other way of saying not-to-compete fell through to the compete
    family, recorded as what the person stated. A closed list cannot name every
    negation, so a negator near the phrase leaves the direction for the agent
    to ask. Wrong in the other direction only costs a question.
    """

    for message in (
        "design a VHH to CD22 that does not compete with the partner",
        "a binder that doesn't compete with m971",
        "it must not compete with the antibody",
        "we do not want it to compete with IgG",
        "nunca deve competir com o parceiro",
        "sem competir com o anticorpo",
        "não pode competir com m971",
        "it should never compete with m971",
        "no, it should not compete with m971",
    ):
        found = _proposals(message)
        assert found["partner_direction"] == "AMBIGUOUS", message


def test_a_plain_compete_still_settles_and_a_distant_negator_does_not_block_it() -> None:
    assert _proposals("it should compete with m971")["partner_direction"] == "compete"
    # The negator is far from the phrase, so it is not about it.
    assert (
        _proposals("this is not a drill and we are in no rush, but it must compete with m971")[
            "partner_direction"
        ]
        == "compete"
    )
