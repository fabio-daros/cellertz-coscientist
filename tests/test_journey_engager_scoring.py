"""The score a journey records for an mBER execution.

The launch screens and ranks the cohort on the GPU host and returns the
atlas's two-axis panel. The journey dropped it and the scoring stage fell back
to ipSAE — a protocol the atlas rules out and mBER cannot produce. These tests
cover the panel being saved, read for the accepted candidate, recorded as its
own protocol, and refused rather than approximated when there is no row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cellertz.application.journey_engager_scoring import (
    EngagerScoreUnavailable,
    engager_score,
    specialist_scorer,
)
from cellertz.application.journey_specialist_progress import (
    RECORDED_PROTOCOLS,
    apply_specialist_progress,
)
from cellertz.domains.protein_design.scoring.engager_two_axis import (
    PROTOCOL_ID,
    EngagerDesign,
    rank_engager_designs,
)

CLINICAL = (frozenset({10, 11, 12, 13, 14, 15}),)
CDRS = ((26, 33), (51, 57), (97, 110))
_SEQUENCE = "QVQLVESGGGLVQAGGSLRLSCAASGNTFDWYRQAPGKEREFVAAISWSGGSTYYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYCAAGSRWYGSWGQGTQVTVSS"


def _design(name: str, i_ptm: float, epitope: frozenset[int], area: float, charge: float) -> EngagerDesign:
    return EngagerDesign(
        design_id=name,
        trajectory=f"trajectory_{name}",
        sequence=_SEQUENCE,
        i_ptm=i_ptm,
        epitope=epitope,
        buried_area=area,
        net_charge=charge,
    )


def _panel() -> dict[str, Any]:
    """A real panel from the real function, not a hand-written shape."""

    designs = [
        _design("design_a", 0.91, frozenset({10, 11, 12, 13}), 820.0, 1.0),
        _design("design_b", 0.84, frozenset({10, 11}), 640.0, 2.0),
        _design("design_c", 0.88, frozenset({40, 41, 42}), 700.0, -1.0),
        _design("design_gated", 0.42, frozenset({10, 11, 12}), 900.0, 0.0),
    ]
    return rank_engager_designs(designs, CLINICAL, CDRS).to_dict()


def _ranking_document(panel: dict[str, Any] | None = None) -> dict[str, Any]:
    """What ``rank_mber_output`` returns and the launch hands back."""

    return {
        "campaign_id": "cd22_m971_avoid",
        "generator_version": "mber-vhh-0.4",
        "generated": 4,
        "screened_in": 3,
        "rejected_by_screen": [{"design_id": "design_screened", "rejected_by": "free_cysteine"}],
        "not_measurable": [],
        "ranking": _panel() if panel is None else panel,
    }


def _execution(document: dict[str, Any] | None = None, design: str = "design_a") -> dict[str, Any]:
    execution = {
        "attempt_id": "attempt.mber1",
        "terminal": True,
        "status": "ranked",
        "accepted_candidate": True,
        "candidate_id": design,
        "candidate_identity": {
            "candidate_id": design,
            "design": design,
            "execution_id": "attempt.mber1",
            "sequence": _SEQUENCE,
            "target_id": "CD22",
        },
        "plan_id": "plan_mber",
        "revision": 1,
        "digest": "ab" * 32,
    }
    if document is not None:
        execution["ranking"] = document
    return execution


def _row(panel: dict[str, Any], design: str) -> dict[str, Any]:
    return next(item for item in panel["ranking"]["designs"] if item["design_id"] == design)


def test_the_score_is_the_launch_s_own_panel_row() -> None:
    document = _ranking_document()

    scored = specialist_scorer(_execution(document))

    assert scored.ranking_method == PROTOCOL_ID
    assert scored.ranking_score == _row(document, "design_a")["score"]
    assert scored.is_probability is False
    assert scored.provenance["calculator"]["implementation"] == "rank_engager_designs"
    assert scored.provenance["computed_by"] == "the launch that produced this execution"
    assert scored.provenance["ordering"] == "i_ptm_gated"
    assert scored.provenance["i_ptm_gate"] == 0.80
    # ipSAE appears once and only to say this number must not be put beside it.
    assert "ipsae_primary_ranking" in scored.provenance["not_comparable_with"]
    assert "calculation_started_at" not in scored.provenance


def test_the_cohort_the_percentiles_are_against_is_recorded() -> None:
    """A percentile without its cohort is unreadable, and ours is a filtered one."""

    scored = specialist_scorer(_execution(_ranking_document()))

    assert scored.provenance["cohort"] == {
        "generated": 4,
        "screened_in": 3,
        "ranked": 3,
        "rejected_by_screen": 1,
        "rejected_by_gate": 1,
        "not_measurable": 0,
    }
    assert any("cohort it was computed with" in item for item in scored.limitations)


def test_an_execution_with_no_panel_is_scored_by_the_ipsae_path(monkeypatch: Any) -> None:
    """BindCraft executions keep the replay they had; the seam serves both."""

    seen: list[Any] = []
    monkeypatch.setattr(
        "cellertz.application.journey_engager_scoring.ipsae_score",
        lambda execution: seen.append(execution) or "delegated",
    )

    assert specialist_scorer(_execution()) == "delegated"
    assert seen[0]["attempt_id"] == "attempt.mber1"


def test_a_panel_under_another_protocol_is_refused() -> None:
    """Two protocols' numbers are different claims and cannot share a column."""

    document = _ranking_document()
    document["ranking"]["protocol_id"] = "CARPNN_2026_REFERENCE"

    with pytest.raises(EngagerScoreUnavailable) as refused:
        engager_score(_execution(document))

    assert "ranking_protocol_is_not_this_one" in str(refused.value)


def test_a_candidate_the_gate_dropped_is_named_as_gated() -> None:
    with pytest.raises(EngagerScoreUnavailable) as refused:
        engager_score(_execution(_ranking_document(), design="design_gated"))

    assert "rejected_by_the_i_ptm_gate" in str(refused.value)


def test_a_candidate_the_panel_never_held_is_refused_not_approximated() -> None:
    for broken, expected in (
        ({"campaign_id": "x"}, "ranking_document_has_no_ranking"),
        (_ranking_document(), "ranking_has_no_row_for"),
    ):
        with pytest.raises(EngagerScoreUnavailable) as refused:
            engager_score(_execution(broken, design="design_absent"))
        assert expected in str(refused.value)


def test_the_launch_s_ranking_is_saved_on_the_execution() -> None:
    """It was computed on the GPU host and then dropped by this function."""

    from cellertz.application.design_journey import _execution_from_reply

    document = _ranking_document()
    finished = _execution_from_reply(
        {"journey_id": "journey_mber"},
        {"attempt_id": "attempt.mber1"},
        {"status": "ranked", "launched": True, "ranking": document},
    )

    assert finished["ranking"] == document


def _recorded(execution: dict[str, Any], monkeypatch: Any) -> dict[str, Any]:
    from cellertz.domains.protein_design.agents.generation import NormalizedCandidate

    monkeypatch.setattr(
        "cellertz.application.journey_specialist_progress._saved_generation",
        lambda _execution: {
            "candidate": NormalizedCandidate(
                candidate_id=execution["candidate_id"],
                sequence=_SEQUENCE,
                target_id="CD22",
                generator="mber",
                generator_metadata={"design": execution["candidate_id"]},
            ),
            "error": None,
            "invoked": True,
        },
    )
    monkeypatch.setattr(
        "cellertz.application.journey_specialist_progress._evidence_block",
        lambda _execution: (None, []),
    )
    document = {
        "journey_id": "journey_mber",
        "active_round_id": "round_mber",
        "stages": [],
        "events": [],
        "execution": execution,
        "rounds": [
            {
                "round_id": "round_mber",
                "plan_id": execution["plan_id"],
                "plan_revision": execution["revision"],
                "digest": execution["digest"],
                "specialist_stages": [],
                "specialist_events": [],
            }
        ],
    }
    assert apply_specialist_progress(
        document, "round_mber", scorer=specialist_scorer, catalog_root=None
    ) is True
    return {
        item["stage_id"]: item for item in document["rounds"][0]["specialist_stages"]
    }


def test_the_stage_records_the_engager_protocol_instead_of_refusing_it(monkeypatch: Any) -> None:
    """The completion path accepted raw ipSAE and nothing else."""

    document = _ranking_document()

    stages = _recorded(_execution(document), monkeypatch)

    scoring = stages["scoring"]
    assert scoring["status"] == "completed"
    assert scoring["output"]["ranking_signal"] == PROTOCOL_ID
    assert scoring["output"]["score_source"] == "rank_engager_designs"
    assert scoring["output"]["ranking_score"] == _row(document, "design_a")["score"]
    assert "ipsae_provenance" not in scoring["output"]
    assert RECORDED_PROTOCOLS[PROTOCOL_ID]["label"] in scoring["contribution"]
    # The deterministic specialists are protocol agnostic and still run.
    assert stages["ranking"]["output"]["ranking_signal"] == PROTOCOL_ID
    assert stages["critique"]["status"] == "completed"
    assert stages["prioritization"]["status"] == "completed"


def test_a_refused_score_fails_the_stage_with_the_reason_it_gave(monkeypatch: Any) -> None:
    stages = _recorded(_execution(_ranking_document(), design="design_absent"), monkeypatch)

    assert stages["scoring"]["status"] == "failed"
    assert "ranking_has_no_row_for:design_absent" in stages["scoring"]["failure_reason"]
    assert stages["ranking"]["status"] != "completed"


def test_a_ranked_launch_reaches_the_journey_as_an_accepted_execution() -> None:
    """The round reads these three fields to let specialists run at all."""

    from cellertz.application.design_journey import _execution_from_reply

    finished = _execution_from_reply(
        {"journey_id": "journey_mber"},
        {"attempt_id": "attempt.mber1"},
        {
            "status": "ranked",
            "launched": True,
            "accepted_candidate": True,
            "successful_candidate_generation": True,
            "candidate_id": "mber_design_a",
            "candidate_identity": {
                "candidate_id": "mber_design_a",
                "design": "design_a",
                "execution_id": "attempt.mber1",
                "sequence": _SEQUENCE,
                "target_id": "CD22",
            },
            "ranking": _ranking_document(),
        },
    )

    assert finished["accepted_candidate"] is True
    assert finished["successful_candidate_generation"] is True
    assert finished["terminal"] is True
    assert finished["candidate_identity"]["design"] == "design_a"
    assert finished["ranking"]["ranking"]["protocol_id"] == PROTOCOL_ID


def test_a_launcher_cannot_claim_success_on_a_status_that_earned_none() -> None:
    """verified is BindCraft's terminal status and ranked is mBER's. No others."""

    from cellertz.application.design_journey import _execution_from_reply

    for status in ("launch_failed", "no_accepted_design", "outputs_rejected", "failed"):
        finished = _execution_from_reply(
            {"journey_id": "journey_mber"},
            {"attempt_id": "attempt.mber1"},
            {
                "status": status,
                "launched": True,
                "accepted_candidate": True,
                "successful_candidate_generation": True,
            },
        )
        assert finished["successful_candidate_generation"] is False, status


def _ranked_attempt(tmp_path, designs=None):
    """A real mBER attempt: accepted.csv, the structures, and the launch's panel."""

    import dataclasses

    from cellertz.application.mber_campaign import rank_mber_output
    from cellertz.application.mber_settings import GENERATOR_VERSION
    from tests.test_mber_campaign import CLINICAL, cohort, versioned

    attempt = tmp_path / "attempt.mberlive"
    directory = cohort(
        attempt / "cohort",
        designs
        or [
            {"trajectory": "traj_a", "i_ptm": 0.93},
            {"trajectory": "traj_b", "i_ptm": 0.86},
            {"trajectory": "traj_c", "i_ptm": 0.82},
        ],
    )
    specification = dataclasses.replace(
        versioned(),
        generator_protocol=dataclasses.replace(
            versioned().generator_protocol, generator_version=GENERATOR_VERSION
        ),
    )
    ranked = rank_mber_output(specification, directory, CLINICAL)
    accepted = ranked["accepted_design"]
    assert accepted["accepted"] is True, ranked
    execution = {
        "attempt_id": "attempt.mberlive",
        "attempt_directory": str(attempt),
        "terminal": True,
        "status": "ranked",
        "accepted_candidate": True,
        "candidate_id": accepted["candidate_id"],
        "candidate_identity": {
            **accepted["candidate_identity"],
            "execution_id": "attempt.mberlive",
        },
        "plan_id": "plan_cd22",
        "revision": 1,
        "digest": "ab" * 32,
        "ranking": ranked,
    }
    return attempt, execution


def _drive(execution: dict[str, Any]) -> dict[str, Any]:
    document = {
        "journey_id": "journey_mber",
        "active_round_id": "round_mber",
        "stages": [],
        "events": [],
        "execution": execution,
        "rounds": [
            {
                "round_id": "round_mber",
                "plan_id": execution["plan_id"],
                "plan_revision": execution["revision"],
                "digest": execution["digest"],
                "specialist_stages": [],
                "specialist_events": [],
            }
        ],
    }
    assert apply_specialist_progress(
        document, "round_mber", scorer=specialist_scorer, catalog_root=None
    ) is True
    return {item["stage_id"]: item for item in document["rounds"][0]["specialist_stages"]}


def test_a_ranked_cohort_reaches_every_stage_from_its_own_artifacts(tmp_path) -> None:
    """No monkeypatching: the stages read accepted.csv and the structures.

    The generation stage read BindCraft's design stats and the evidence gate
    wanted a PAE file per model, so every mBER round stopped at generation
    with a ranked cohort sitting on disk.
    """

    _attempt, execution = _ranked_attempt(tmp_path)

    stages = _drive(execution)

    assert [stages[name]["status"] for name in ("generation", "scoring", "ranking", "critique", "prioritization")] == [
        "completed"
    ] * 5
    generation = stages["generation"]["output"]
    assert generation["adapter"] == "mber_adapter_v1"
    assert generation["protein_generation_agent_executed"] is False
    assert generation["candidate_id"] == execution["candidate_id"]
    assert stages["scoring"]["output"]["ranking_signal"] == PROTOCOL_ID
    assert stages["scoring"]["output"]["score_source"] == "rank_engager_designs"
    # The evidence is the complex the screen measured, with its digest, and no
    # PAE because mBER writes none.
    model = stages["scoring"]["output"]["models"][0]
    assert model["model_id"] == "complex"
    assert model["pae_path"] is None
    assert len(model["pdb_sha256"]) == 64
    assert Path(model["pdb_path"]).is_file()


def test_a_cohort_whose_accepted_csv_is_gone_blocks_instead_of_scoring(tmp_path) -> None:
    attempt, execution = _ranked_attempt(tmp_path)
    (attempt / "cohort" / "accepted.csv").unlink()

    stages = _drive(execution)

    assert stages["generation"]["status"] == "blocked"
    assert "no accepted cohort" in stages["generation"]["blocking_reason"]
    assert stages["scoring"]["status"] != "completed"


def test_a_design_whose_structure_is_gone_blocks_before_scoring(tmp_path) -> None:
    """The screen measured that file; the ranking is not evidence it exists."""

    attempt, execution = _ranked_attempt(tmp_path)
    for pdb in (attempt / "cohort").rglob("*_complex.pdb"):
        pdb.unlink()

    stages = _drive(execution)

    assert stages["scoring"]["status"] == "blocked"
    assert "not in the cohort" in stages["scoring"]["blocking_reason"]


def test_a_declared_sequence_that_disagrees_with_the_cohort_is_refused(tmp_path) -> None:
    _attempt, execution = _ranked_attempt(tmp_path)
    execution["candidate_identity"]["sequence"] = "QQQQQQQQQQ"

    stages = _drive(execution)

    assert stages["generation"]["status"] == "blocked"
    assert "different molecule" in stages["generation"]["blocking_reason"]
