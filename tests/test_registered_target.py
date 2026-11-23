"""Which target a registered root is for, and what a plan says about a mismatch.

The plan assumed every root was PD-L1's. A CD22 plan came back with a blocking
question — "The stated target conflicts with the registered PDL1 inputs" — that
no answer could clear, so it could never reach Run from the screen.
"""

from __future__ import annotations

import json

from cellertz.application.generation_plan import (
    LEGACY_REGISTERED_TARGET,
    GenerationPlanService,
    open_registered_input_catalog,
)
from cellertz.application.mber_demonstration import MberDemonstrationLauncher

TARGET = b"ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n"


def _catalog(tmp_path, preparation=None):
    root = tmp_path / "inputs"
    root.mkdir(parents=True)
    (root / "target.pdb").write_bytes(TARGET)
    if preparation is not None:
        (root / "preparation.json").write_text(
            preparation if isinstance(preparation, str) else json.dumps(preparation), encoding="utf-8"
        )
    return open_registered_input_catalog(
        {"CELLERTZ_PDL1_INPUT_ROOT": str(root)},
        required_roles=MberDemonstrationLauncher.required_input_roles,
    )


def _questions(catalog, target):
    plan = GenerationPlanService(None, inputs=catalog).draft(
        "design-request:request_1:revision:1",
        {
            "request_text": f"Design a {target} binder.",
            "target_identity": target,
            "target_identity_provenance": "user",
            "target_identity_source": "professor",
        },
    )
    return plan, [item for item in plan["questions"] if item["field"] == "target_identity"]


def test_a_root_names_the_target_it_was_registered_for(tmp_path) -> None:
    assert _catalog(tmp_path, {"target_id": "CD22"}).registered_target() == "CD22"


def test_a_root_with_no_usable_record_is_the_legacy_pdl1_root(tmp_path) -> None:
    """The first root this server held had no preparation record, and was PD-L1's."""

    for preparation in (None, "not json", "[]", {"target_id": ""}, {"target_id": 22}, {"other": "x"}):
        found = _catalog(tmp_path / str(abs(hash(json.dumps(preparation, default=str)))), preparation)
        assert found.registered_target() == LEGACY_REGISTERED_TARGET, preparation


def test_a_cd22_plan_on_the_cd22_root_has_no_target_conflict(tmp_path) -> None:
    plan, questions = _questions(_catalog(tmp_path, {"target_id": "CD22"}), "CD22")

    assert questions == []
    assert plan["fields"]["target_identity"] == "CD22"
    assert "target_identity_conflict" not in plan["fields"]


def test_the_comparison_ignores_case(tmp_path) -> None:
    _plan, questions = _questions(_catalog(tmp_path, {"target_id": "CD22"}), "cd22")

    assert questions == []


def test_a_target_the_root_was_not_registered_for_is_still_a_blocking_conflict(tmp_path) -> None:
    """The check stays: CD22 against a PD-L1 root is exactly what it exists to catch."""

    plan, questions = _questions(_catalog(tmp_path), "CD22")

    assert len(questions) == 1
    assert "registered PDL1 inputs" in questions[0]["question"]
    assert questions[0].get("blocking", True) is True
    assert plan["fields"]["target_identity_registered"] == "PDL1"


def test_the_conflict_names_the_root_s_own_target(tmp_path) -> None:
    _plan, questions = _questions(_catalog(tmp_path, {"target_id": "CD22"}), "PDL1")

    assert len(questions) == 1
    assert "registered CD22 inputs" in questions[0]["question"]


def test_the_recorded_fact_shown_to_the_front_is_the_roots_target(tmp_path) -> None:
    plans = GenerationPlanService(None, inputs=_catalog(tmp_path, {"target_id": "CD22"}))

    assert plans.recorded_input_facts()["target_identity"] == "CD22"
