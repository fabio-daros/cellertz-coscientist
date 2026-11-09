"""A design journey records planning and does not run later specialists."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cellertz.application.apptainer_demonstration import (
    _safe_attempt_directory,
    ingest_bindcraft_output,
    publish_ingested_review,
    publish_verified_attempt_to_catalog,
)
from cellertz.application.attempt_evidence import AttemptFiles, default_attempt_files, load_catalog
from cellertz.application.attempt_inspection import StructureArtifact, handle_attempt_get
from cellertz.application.design_journey import (
    DesignJourneyService,
    SQLiteDesignJourneyStore,
    handle_design_journey,
)
from cellertz.application.generation_plan import (
    GenerationPlanService,
    RegisteredInputCatalog,
    handle_design_request_plan,
    handle_design_request_plan_revision,
)
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DesignDrafting,
    DeterministicDraftExtractor,
)
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.generation_plan_store import SQLiteGenerationPlanStore
from cellertz.application.trajectory_budget import ADAPTER_VERSION
from cellertz.tools.protein_scoring.ipsae import ProteinIpSAECalculator
from cellertz.tools.protein_scoring.scoring_v2 import ProteinScoreResultV2

_TEXT = "Design a PDL1 binder. Do not launch generation."


def test_saved_requests_are_listed_for_a_new_browser() -> None:
    plans = GenerationPlanService(None)
    service = DesignJourneyService(plans)
    status, empty = handle_design_journey(service, "GET", "/api/design-journeys", b"")
    assert status == 200
    assert empty == {"requests": []}
    created = service.open_or_get("request_listed", 1, "Design a PDL1 binder for review.")
    status, listed = handle_design_journey(service, "GET", "/api/design-journeys", b"")
    assert status == 200
    assert listed["requests"] == [
        {
            "request_id": "request_listed",
            "request_revision": 1,
            "request_text": "Design a PDL1 binder for review.",
            "journey_id": created["journey_id"],
            "updated_at": created["updated_at"],
            "plan_id": created["plan_id"],
            "plan_revision": created["plan_revision"],
        }
    ]
    assert service.get(created["journey_id"])["updated_at"] == created["updated_at"]


def test_requests_stay_isolated_and_later_stages_do_not_complete() -> None:
    plans = GenerationPlanService(None)
    knowledge = _Knowledge(
        [
            SimpleNamespace(
                id="claim.pdl1.interface",
                version=3,
                type="claim",
                domain="protein_design",
                status="validated",
                title="PDL1 interface note",
            ),
            SimpleNamespace(
                id="claim.other.target",
                version=1,
                type="claim",
                domain="protein_design",
                status="validated",
                title="Unrelated target",
            ),
        ]
    )
    service = DesignJourneyService(plans, knowledge=knowledge)
    first = service.open_or_get("request_one", 1, _TEXT)
    second = service.open_or_get("request_two", 1, "Design a different target binder.")
    assert first["journey_id"] != second["journey_id"]
    assert first["plan_id"] != second["plan_id"]
    assert second["request_id"] not in _stage_text(first)
    assert first["gpu_execution"] == "disabled"
    assert first["approved"] is False
    assert first["specialist_agents_executed"] is False
    assert first["supervisor"]["run_called"] is False
    assert first["knowledge"]["status"] == "retrieved"
    assert first["knowledge"]["sources"] == [
        {
            "id": "claim.pdl1.interface",
            "version": 3,
            "type": "claim",
            "domain": "protein_design",
            "status": "validated",
            "title": "PDL1 interface note",
        }
    ]
    assert "source_path" not in str(first["knowledge"])
    assert second["knowledge"]["status"] == "irrelevant"
    assert first["hermes"]["status"] == "not_applicable"
    statuses = {stage["stage_id"]: stage["status"] for stage in first["stages"]}
    assert statuses == {
        "design_request": "completed",
        "generation_planning": "completed",
        "approval": "pending",
        "execution_authorization": "pending",
        "generation": "blocked",
        "scoring": "not_applicable",
        "ranking": "not_applicable",
        "critique": "not_applicable",
        "prioritization": "not_applicable",
    }
    planning = next(stage for stage in first["stages"] if stage["stage_id"] == "generation_planning")
    assert planning["implementation_kind"] == "deterministic"
    assert planning["tools"] == ["generation_plan_draft"]
    assert planning["agent_executed"] is False
    assert planning["output"]["approved"] is False
    assert planning["output"]["execution_connected"] is False
    assert "ranking_score" not in planning["output"]
    assert any("i_pTM" in item for item in planning["limitations"])
    code, refusal = service.record_approval(
        first["journey_id"],
        {
            "plan_id": first["plan_id"],
            "revision": first["plan_revision"],
            "digest": "0" * 64,
            "reviewer_id": "professor",
            "reason": "Reviewed the saved plan.",
            "idempotency_key": "approval-stale",
        },
    )
    assert code == 409
    assert refusal["approved"] is False
    assert refusal["gpu_execution"] == "disabled"
    assert service.get(first["journey_id"])["approved"] is False


def test_a_planning_failure_does_not_complete_later_stages() -> None:
    service = DesignJourneyService(_BrokenPlans(), knowledge=None)
    document = service.open_or_get("request_fail", 1, _TEXT)
    statuses = {stage["stage_id"]: stage["status"] for stage in document["stages"]}
    assert statuses["generation_planning"] == "failed"
    assert statuses["approval"] == "blocked"
    assert statuses["generation"] == "blocked"
    assert statuses["scoring"] == "not_applicable"
    assert document["plan_id"] is None
    assert document["knowledge"]["status"] == "unavailable"
    assert document["hermes"]["status"] == "not_applicable"


def test_sqlite_reload_does_not_replay_the_plan(tmp_path: Path) -> None:
    path = tmp_path / "journeys.sqlite"
    plans = _CountingPlans()
    service = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(path))
    first = service.open_or_get("request_keep", 1, _TEXT)
    service.close()
    reopened = DesignJourneyService(_BrokenPlans(), store=SQLiteDesignJourneyStore(path))
    again = reopened.open_or_get("request_keep", 1, _TEXT)
    assert again["journey_id"] == first["journey_id"]
    assert again["plan_revision"] == 1
    assert plans.calls == 1
    revised = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(path))
    updated = revised.open_or_get("request_keep", 2, _TEXT + " Revision two.")
    assert updated["journey_id"] == first["journey_id"]
    assert updated["request_revision"] == 2
    assert updated["plan_revision"] == 2
    assert updated["recorded_at"] == first["recorded_at"]
    assert len(updated["events"]) == 2
    assert plans.calls == 2
    reopened.close()
    revised.close()


def test_a_repeated_request_returns_the_saved_journey_without_another_plan(tmp_path: Path) -> None:
    plans = GenerationPlanService(None, store=SQLiteGenerationPlanStore(tmp_path / "plans.sqlite"))
    service = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(tmp_path / "journeys.sqlite"))
    entered = threading.Event()
    release = threading.Event()
    builds: list[str] = []
    original = service._build

    def slow_build(*args: object, **kwargs: object) -> dict:
        builds.append("build")
        entered.set()
        assert release.wait(timeout=2)
        return original(*args, **kwargs)

    service._build = slow_build  # type: ignore[method-assign]
    body = json.dumps(
        {
            "request_id": "request_repeat",
            "request_revision": 1,
            "request_text": "Design a PDL1 binder for review.",
        }
    ).encode()

    def post() -> tuple[int, dict]:
        return handle_design_journey(service, "POST", "/api/design-journeys", body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(post)
        assert entered.wait(timeout=2)
        second_future = pool.submit(post)
        release.set()
        first = first_future.result(timeout=2)
        second = second_future.result(timeout=2)
    assert first[0] == 200
    assert second[0] == 200
    assert first[1]["journey_id"] == second[1]["journey_id"]
    assert first[1]["plan_id"] == second[1]["plan_id"]
    assert first[1]["plan_revision"] == 1
    assert second[1]["plan_revision"] == 1
    assert builds == ["build"]
    assert plans.published(first[1]["plan_id"])["revision"] == 1
    assert sqlite3.connect(tmp_path / "plans.sqlite").execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
    service.close()
    plans.close()


def test_an_explicit_target_revises_the_saved_plan_and_leaves_other_settings_open(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    plans = GenerationPlanService(None, store=SQLiteGenerationPlanStore(plan_path))
    service = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(journey_path))
    created = service.open_or_get("request_target", 1, "Design a PDL1 binder for review.")
    original = plans.published(created["plan_id"])
    assert original is not None
    assert original["revision"] == 1
    assert original["fields"].get("target_identity") is None
    original_digest = original["digest"]

    status, revised = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys",
        json.dumps(
            {
                "request_id": "request_target",
                "request_revision": 1,
                "request_text": "Design a PDL1 binder for review.",
                "fields": [
                    {"name": "target_id", "value": "PDL1", "provenance": "user"},
                    {"name": "objective", "value": "binder_design", "provenance": "user"},
                    {"name": "generator", "value": None, "provenance": "absent"},
                    {"name": "ranking_signal", "value": "raw_ipsae", "provenance": "documented_default"},
                    {"name": "seed", "value": None, "provenance": "absent"},
                    {"name": "gpu", "value": "1", "provenance": "documented_default"},
                ],
            }
        ).encode(),
    )
    assert status == 200
    assert revised["journey_id"] == created["journey_id"]
    assert revised["plan_id"] == created["plan_id"]
    assert revised["plan_revision"] == 2
    assert revised["recorded_at"] == created["recorded_at"]
    published = plans.published(created["plan_id"])
    assert published is not None
    assert published["revision"] == 2
    assert published["digest"] != original_digest
    assert published["fields"]["target_identity"] == "PDL1"
    assert published["fields"]["target_identity_provenance"] == "user"
    assert published["fields"].get("target_identity_conflict") is not True
    for name in (
        "hotspot_assumption",
        "filter_policy",
        "binder_length",
        "seed",
        "gpu",
        "cpu_count",
        "memory_mib",
        "execution_deadline_seconds",
    ):
        assert published["fields"].get(name) in (None, "")
    assert "objective" not in published["fields"]
    assert "ranking_signal" not in published["fields"]
    assert any(item["field"] == "seed" for item in published["questions"])
    assert any(event.get("kind") == "plan_revised" and event.get("previous_revision") == 1 for event in revised["events"])
    stored_revision = sqlite3.connect(plan_path).execute("SELECT document FROM plans").fetchone()
    assert stored_revision is not None
    assert json.loads(stored_revision[0])["revision"] == 2

    again_status, again = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys",
        json.dumps(
            {
                "request_id": "request_target",
                "request_revision": 1,
                "request_text": "Design a PDL1 binder for review.",
                "fields": [{"name": "target_id", "value": "PDL1", "provenance": "user"}],
            }
        ).encode(),
    )
    assert again_status == 200
    assert again["plan_revision"] == 2
    assert plans.published(created["plan_id"])["revision"] == 2
    service.close()
    plans.close()


def test_a_target_that_conflicts_with_registered_inputs_is_kept() -> None:
    plans = GenerationPlanService(None, inputs=_VerifiedPdl1Inputs())
    service = DesignJourneyService(plans)
    service.open_or_get("request_conflict", 1, "Design a binder for review.")
    _status, revised = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys",
        json.dumps(
            {
                "request_id": "request_conflict",
                "request_revision": 1,
                "request_text": "Design a binder for review.",
                "fields": [{"name": "target_id", "value": "BCMA", "provenance": "user"}],
            }
        ).encode(),
    )
    published = plans.published(revised["plan_id"])
    assert published is not None
    assert published["fields"]["target_identity"] == "BCMA"
    assert published["fields"]["target_identity_provenance"] == "user"
    assert published["fields"]["target_identity_conflict"] is True
    assert any(item["field"] == "target_identity" for item in published["questions"])
    assert published["approved"] is False


def test_saving_reviewed_fields_revises_once_and_keeps_approval_history(tmp_path: Path) -> None:
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    plans = GenerationPlanService(None, store=SQLiteGenerationPlanStore(plan_path))
    service = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(journey_path))
    created = service.open_or_get("request_complete", 1, "Design a PDL1 binder for review.")
    digest = _digest(created)
    status, approved = service.record_approval(created["journey_id"], _approval(created, digest))
    assert status == 200
    assert approved["approved"] is True
    offer = approved["completion"]["technical_demonstration"]
    assert offer["name"] == "Technical demonstration"
    assert offer["profile_id"] == "pdl1-bindcraft-apptainer-demonstration-v1"
    assert offer["authorizes_execution"] is False
    assert offer["launches_compute"] is False
    by_name = {item["name"]: item for item in offer["values"]}
    assert "hotspot_assumption" not in by_name
    assert by_name["filter_policy"]["value"] == "no_filters"
    assert "does not establish scientific quality" in by_name["filter_policy"]["note"]
    assert by_name["memory_mib"]["value"] == 32768
    assert "Host RAM" in by_name["memory_mib"]["note"]
    assert "GPU VRAM" in by_name["memory_mib"]["note"]
    assert by_name["gpu"]["value"] is None
    assert by_name["gpu"]["recordable"] is False
    assert "does not assign" in by_name["gpu"]["note"]
    assert "unselected" in by_name["gpu"]["note"]
    assert by_name["seed"]["value"] == 1
    assert by_name["binder_length"]["value"] == 65
    assert by_name["cpu_count"]["value"] == 4
    assert by_name["execution_deadline_seconds"]["value"] == 7200
    assert by_name["n_candidates"]["value"] == 1
    assert by_name["sequence_limits"]["recordable"] is False
    assert by_name["sequence_limits"]["value"] == {
        "max_trajectories": 1,
        "num_seqs": 1,
        "max_mpnn_sequences": 1,
    }

    fields = [
        {"name": "target_id", "value": "PDL1", "provenance": "user"},
        {"name": "target_identity", "value": "PDL1", "provenance": "user"},
        {"name": "objective", "value": "binder_design", "provenance": "user"},
        {"name": "generator", "value": "bindcraft", "provenance": "user"},
        {"name": "n_candidates", "value": 1, "provenance": "user", "source": "technical_demonstration_profile"},
        {"name": "binder_length", "value": 65, "provenance": "user", "source": "technical_demonstration_profile"},
        {"name": "seed", "value": 1, "provenance": "user", "source": "technical_demonstration_profile"},
        {"name": "cpu_count", "value": 4, "provenance": "user", "source": "technical_demonstration_profile"},
        {"name": "memory_mib", "value": 32768, "provenance": "user", "source": "technical_demonstration_profile"},
        {"name": "gpu", "value": "0", "provenance": "user", "source": "professor"},
        {
            "name": "execution_deadline_seconds",
            "value": 7200,
            "provenance": "user",
            "source": "technical_demonstration_profile",
        },
        {
            "name": "filter_policy",
            "value": "no_filters",
            "provenance": "user",
            "source": "technical_demonstration_profile",
        },
        {
            "name": "sequence_limits",
            "value": {"max_trajectories": 1, "num_seqs": 1, "max_mpnn_sequences": 1},
            "provenance": "user",
            "source": "technical_demonstration_profile",
        },
    ]
    status, revised = _complete(service, "request_complete", fields, ["technical-smoke-test"])
    assert status == 200
    assert revised["journey_id"] == created["journey_id"]
    assert revised["request_id"] == "request_complete"
    assert revised["request_revision"] == 1
    assert revised["plan_revision"] == 2
    assert revised["approved"] is False
    assert revised.get("approval") is None
    assert revised["approval_history"][0]["revision"] == 1
    assert revised["approval_history"][0]["digest"] == digest
    assert revised["approval_history"][0]["scope"] == "recorded_plan"
    assert revised["gpu_execution"] == "disabled"
    assert revised["execution_review"]["execution_authorized"] is False
    assert revised["execution_review"]["launches_compute"] is False
    assert _status(revised, "approval") == "pending"
    assert _status(revised, "generation") == "blocked"
    assert revised["request_fields"]["objective"]["value"] == "binder_design"
    assert revised["request_fields"]["generator"]["value"] == "bindcraft"
    assert revised["request_fields"]["n_candidates"]["value"] == 1
    assert revised["request_fields"]["target_id"]["value"] == "PDL1"
    published = plans.published(created["plan_id"])
    assert published is not None
    assert published["revision"] == 2
    assert published["approved"] is False
    assert published["approval"] is None
    assert published["approval_history"][0]["revision"] == 1
    assert published["fields"]["target_identity"] == "PDL1"
    assert published["fields"]["seed"] == 1
    assert published["fields"]["seed_provenance"] == "user"
    assert published["fields"]["seed_source"] == "technical_demonstration_profile"
    assert published["fields"]["memory_mib"] == 32768
    assert published["fields"]["filter_policy"] == "no_filters"
    assert published["fields"]["filter_policy_establishes_scientific_quality"] is False
    assert "sequence_limits" not in published["fields"]
    assert any(item["field"] == "sequence_limits" for item in revised["conflicts"])
    assert published["fields"].get("hotspot_assumption") in (None, "")
    assert "objective" not in published["fields"]

    again_status, again = _complete(service, "request_complete", fields, ["technical-smoke-test"])
    assert again_status == 200
    assert again["plan_revision"] == 2
    assert len(again["approval_history"]) == 1
    read_status, read = handle_design_journey(
        service, "GET", "/api/design-journeys/by-request/request_complete", b""
    )
    assert read_status == 200
    assert read["journey_id"] == created["journey_id"]
    assert read["plan_revision"] == 2
    plans.draft = _refuse_draft  # type: ignore[method-assign]
    reread_status, reread = handle_design_journey(
        service, "GET", "/api/design-journeys/by-request/request_complete", b""
    )
    assert reread_status == 200
    assert reread["plan_revision"] == 2

    reloaded = GenerationPlanService(None, store=SQLiteGenerationPlanStore(plan_path))
    stored = reloaded.published(created["plan_id"])
    assert stored is not None
    assert stored["revision"] == 2
    assert stored["approval_history"][0]["digest"] == digest
    service.close()
    plans.close()
    reloaded.close()


def test_request_and_plan_conflicts_stay_visible_without_a_new_revision() -> None:
    plans = GenerationPlanService(None, inputs=_VerifiedPdl1Inputs())
    service = DesignJourneyService(plans)
    created = service.open_or_get("request_fields", 1, "Design a binder for review.")
    status, conflicted = _complete(
        service,
        "request_fields",
        [
            {"name": "target_id", "value": "PDL1", "provenance": "user"},
            {"name": "target_identity", "value": "BCMA", "provenance": "user"},
            {"name": "binder_length", "value": 10, "provenance": "user"},
            {"name": "length_min", "value": 20, "provenance": "user"},
            {"name": "length_max", "value": 30, "provenance": "user"},
            {"name": "filter_policy", "value": "no_filters", "provenance": "user"},
        ],
    )
    assert status == 200
    assert conflicted["plan_revision"] == created["plan_revision"]
    assert conflicted["approved"] is False
    published = plans.published(created["plan_id"])
    assert published is not None
    assert published["fields"].get("target_identity") in (None, "")
    assert published["fields"].get("binder_length") in (None, "")
    assert published["fields"].get("filter_policy") in (None, "")
    fields = {item["field"] for item in conflicted["conflicts"]}
    assert "target_identity" in fields
    assert "binder_length" in fields
    assert "filter_policy" in fields
    assert any("technical demonstration" in item["conflict"] for item in conflicted["conflicts"])
    assert conflicted["historical_conflicts"] == []
    assert conflicted["acknowledgement_evidence"]["recorded"] is False
    assert conflicted["acknowledgement_evidence"]["text"] is None
    assert "filter_policy_acknowledgement_required" not in conflicted["execution_review"]["unresolved_blockers"]
    assert "open_questions" in conflicted["execution_review"]["unresolved_blockers"]
    assert conflicted["execution_review"]["launches_compute"] is False


def test_recorded_acknowledgement_stays_current_when_a_later_save_omits_it() -> None:
    plans = GenerationPlanService(None, inputs=_VerifiedPdl1Inputs())
    service = DesignJourneyService(plans)
    service.open_or_get("request_ack", 1, "Design a PDL1 binder for review.")
    fields = [
        {"name": "target_id", "value": "PDL1", "provenance": "user"},
        {"name": "objective", "value": "binder_design", "provenance": "user"},
        {"name": "generator", "value": "bindcraft", "provenance": "user"},
        {"name": "n_candidates", "value": 1, "provenance": "user"},
        {
            "name": "filter_policy",
            "value": "no_filters",
            "provenance": "user",
            "source": "technical_demonstration_profile",
        },
        {"name": "seed", "value": 1, "provenance": "user"},
        {"name": "binder_length", "value": 65, "provenance": "user"},
        {"name": "cpu_count", "value": 4, "provenance": "user"},
        {"name": "memory_mib", "value": 32768, "provenance": "user"},
        {"name": "gpu", "value": "1", "provenance": "user"},
        {"name": "execution_deadline_seconds", "value": 7200, "provenance": "user"},
    ]
    status, revised = _complete(service, "request_ack", fields, ["technical-smoke-test"])
    assert status == 200
    published = plans.published(str(revised["plan_id"]))
    assert published is not None
    sentence = (
        "technical-smoke-test no_filters. This filter policy is for the technical "
        "demonstration and does not establish scientific quality."
    )
    assert published["fields"]["filter_policy"] == "no_filters"
    assert sentence in published["fields"]["request_text"]
    assert revised["acknowledgement_evidence"]["recorded"] is True
    assert revised["acknowledgement_evidence"]["text"] == sentence
    assert revised["acknowledgement_evidence"]["establishes_scientific_quality"] is False
    assert "filter_policy_acknowledgement_required" not in revised["execution_review"]["unresolved_blockers"]
    assert revised["execution_review"]["launches_compute"] is False
    assert revised["gpu_execution"] == "disabled"
    output = _stage(revised, "design_request")["output"]
    assert output["acknowledgement_evidence"]["text"] == sentence
    assert all("not recorded" not in item.get("conflict", "") for item in output["conflicts"])
    digest = published["digest"]
    revision = published["revision"]

    omitted, again = _complete(service, "request_ack", fields, [])
    assert omitted == 200
    assert again["plan_revision"] == revision
    stored_plan = plans.published(str(revised["plan_id"]))
    assert stored_plan is not None
    assert stored_plan["digest"] == digest
    assert again["acknowledgement_evidence"]["recorded"] is True
    assert again["acknowledgement_evidence"]["text"] == sentence
    assert all("not recorded" not in item.get("conflict", "") for item in again["conflicts"])
    assert again["historical_conflicts"] == []
    assert again["execution_review"]["launches_compute"] is False

    conflict = {
        "field": "filter_policy",
        "conflict": (
            "no_filters was not recorded. The technical demonstration "
            "acknowledgement is required. no_filters does not establish "
            "scientific quality."
        ),
    }
    stored = service._store.get(str(revised["journey_id"]))
    assert stored is not None
    stored["conflicts"] = [conflict]
    for stage in stored["stages"]:
        if stage["stage_id"] == "design_request":
            stage["output"]["conflicts"] = [dict(conflict)]
    service._store.put(stored)
    shown = service.get(str(revised["journey_id"]))
    assert shown is not None
    assert all("not recorded" not in item.get("conflict", "") for item in shown["conflicts"])
    assert shown["historical_conflicts"][0]["status"] == "historical"
    assert shown["historical_conflicts"][0]["digest"] == digest
    assert shown["historical_conflicts"][0]["plan_revision"] == revision
    assert "not current evidence" in shown["historical_conflicts"][0]["detail"]
    request_output = _stage(shown, "design_request")["output"]
    assert request_output["acknowledgement_evidence"]["text"] == sentence
    assert request_output["historical_conflicts"][0]["conflict"] == conflict["conflict"]
    assert all("not recorded" not in item.get("conflict", "") for item in request_output["conflicts"])
    raw = service._store.get(str(revised["journey_id"]))
    assert raw is not None
    assert raw["conflicts"] == [conflict]
    unchanged = plans.published(str(revised["plan_id"]))
    assert unchanged is not None
    assert unchanged["revision"] == revision
    assert unchanged["digest"] == digest
    assert "filter_policy_acknowledgement_required" not in shown["execution_review"]["unresolved_blockers"]
    assert shown["execution_review"]["launches_compute"] is False
    service.close()
    plans.close()


def test_no_filters_without_the_recorded_sentence_keeps_the_execution_blocker() -> None:
    plans = GenerationPlanService(None)
    service = DesignJourneyService(plans)
    created = service.open_or_get("request_unacked", 1, "Design a PDL1 binder for review.")
    plans.draft(
        f"design-journey:{created['request_id']}",
        {
            "request_text": "technical-smoke-test uses no_filters.",
            "filter_policy": "no_filters",
            "target_identity": "PDL1",
            "hotspot_assumption": "56",
            "binder_length": 65,
            "seed": 1,
            "cpu_count": 4,
            "memory_mib": 32768,
            "execution_deadline_seconds": 7200,
            "gpu": "1",
        },
    )
    shown = service.get(str(created["journey_id"]))
    published = plans.published(str(created["plan_id"]))
    assert shown is not None
    assert published is not None
    assert published["fields"]["filter_policy"] == "no_filters"
    assert "does not establish scientific quality" not in published["fields"]["request_text"]
    assert shown["acknowledgement_evidence"]["recorded"] is False
    assert shown["acknowledgement_evidence"]["text"] is None
    assert "filter_policy_acknowledgement_required" in shown["execution_review"]["unresolved_blockers"]
    assert shown["execution_review"]["launches_compute"] is False
    assert shown["gpu_execution"] == "disabled"
    assert published["revision"] != created["plan_revision"] or published["digest"] != _digest(created)
    service.close()
    plans.close()


def test_saved_revision_without_request_text_acknowledgement_blocks_until_a_new_revision(
    tmp_path: Path,
) -> None:
    sentence = (
        "technical-smoke-test no_filters. This filter policy is for the technical "
        "demonstration and does not establish scientific quality."
    )
    plain = "Design a PDL1 binder for review."
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    catalog = _input_catalog(tmp_path / "inputs")
    plans = GenerationPlanService(
        None, inputs=catalog, store=SQLiteGenerationPlanStore(plan_path)
    )
    service = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(journey_path))
    created = service.open_or_get("request_saved", 1, plain)
    plans.draft(
        "design-journey:request_saved",
        {
            "request_text": f"{plain}\n{sentence}",
            "target_identity": "PDL1",
            "target_identity_provenance": "user",
            "hotspot_assumption": "56",
            "hotspot_assumption_provenance": "user",
            "filter_policy": "no_filters",
            "filter_policy_provenance": "user",
            "filter_policy_source": "technical_demonstration_profile",
            "binder_length": 65,
            "binder_length_provenance": "user",
            "seed": 1,
            "seed_provenance": "user",
            "cpu_count": 4,
            "cpu_count_provenance": "user",
            "memory_mib": 32768,
            "memory_mib_provenance": "user",
            "gpu": "1",
            "gpu_provenance": "user",
            "execution_deadline_seconds": 7200,
            "execution_deadline_seconds_provenance": "user",
        },
    )
    plan = plans._plans[str(created["plan_id"])]
    plan.fields["request_text"] = plain
    plan.fields["user_requests"] = [plain]
    assert plan.questions == []
    assert plan.fields["filter_policy"] == "no_filters"
    assert sentence not in plan.fields["request_text"]
    plans._persist(plan)
    published = plans.published(str(created["plan_id"]))
    assert published is not None
    conflict = {
        "field": "filter_policy",
        "conflict": (
            "no_filters was not recorded. The technical demonstration "
            "acknowledgement is required. no_filters does not establish "
            "scientific quality."
        ),
    }
    stored = service._store.get(str(created["journey_id"]))
    assert stored is not None
    stored["plan_revision"] = published["revision"]
    stored["conflicts"] = [conflict]
    for stage in stored["stages"]:
        if stage.get("stage_id") == "design_request":
            stage["output"]["request_text"] = plain
            stage["output"]["conflicts"] = [dict(conflict)]
        if stage.get("stage_id") == "generation_planning" and isinstance(stage.get("output"), dict):
            stage["output"]["plan_revision"] = published["revision"]
            stage["output"]["digest"] = published["digest"]
            stage["output"]["questions"] = []
    service._store.put(stored)
    saved_revision = published["revision"]
    saved_digest = published["digest"]
    plans.close()
    service.close()

    reopened_plans = GenerationPlanService(
        None, inputs=catalog, store=SQLiteGenerationPlanStore(plan_path)
    )
    reopened = DesignJourneyService(
        reopened_plans, store=SQLiteDesignJourneyStore(journey_path)
    )
    plan_rows_before = _sqlite_documents(plan_path, "plans", "plan_id")
    journey_rows_before = _sqlite_documents(journey_path, "design_journeys", "journey_id")
    status, shown = handle_design_journey(
        reopened, "GET", f"/api/design-journeys/{created['journey_id']}", b""
    )
    assert status == 200
    assert _sqlite_documents(plan_path, "plans", "plan_id") == plan_rows_before
    assert _sqlite_documents(journey_path, "design_journeys", "journey_id") == journey_rows_before
    evidence = shown["acknowledgement_evidence"]
    assert evidence["plan_request_text"] == plain
    assert sentence not in evidence["plan_request_text"]
    assert evidence["recorded"] is False
    assert evidence["text"] is None
    assert evidence["filter_policy"] == "no_filters"
    assert shown["execution_review"]["revision"] == saved_revision
    assert shown["execution_review"]["digest"] == saved_digest
    assert shown["execution_review"]["unresolved_blockers"] == [
        "filter_policy_acknowledgement_required"
    ]
    assert shown["execution_review"]["launches_compute"] is False
    assert shown["gpu_execution"] == "disabled"
    assert any(item["conflict"] == conflict["conflict"] for item in shown["conflicts"])
    assert shown["historical_conflicts"] == []
    request_output = _stage(shown, "design_request")["output"]
    assert request_output["acknowledgement_evidence"]["recorded"] is False
    assert request_output["acknowledgement_evidence"]["plan_request_text"] == plain
    assert shown["completion"]["acknowledgement_evidence"]["blocker"] == (
        "filter_policy_acknowledgement_required"
    )

    refused_status, refused = reopened.authorize_execution(
        str(created["journey_id"]),
        {
            "plan_id": shown["plan_id"],
            "revision": saved_revision,
            "digest": saved_digest,
            "reviewer_id": "professor.ada",
            "reason": "This must not authorize the unacknowledged revision.",
            "idempotency_key": "authorize-unacknowledged",
        },
    )
    assert refused_status == 409
    assert refused["unresolved_blockers"] == ["filter_policy_acknowledgement_required"]
    assert refused["execution_authorized"] is False
    assert refused["launches_compute"] is False
    untouched = reopened_plans.published(str(created["plan_id"]))
    assert untouched is not None
    assert untouched["revision"] == saved_revision
    assert untouched["digest"] == saved_digest
    assert untouched["approved"] is False
    assert sentence not in untouched["fields"]["request_text"]

    fields = [
        {"name": "target_identity", "value": "PDL1", "provenance": "user"},
        {"name": "hotspot_assumption", "value": "56", "provenance": "user"},
        {
            "name": "filter_policy",
            "value": "no_filters",
            "provenance": "user",
            "source": "technical_demonstration_profile",
        },
        {"name": "binder_length", "value": 65, "provenance": "user"},
        {"name": "seed", "value": 1, "provenance": "user"},
        {"name": "cpu_count", "value": 4, "provenance": "user"},
        {"name": "memory_mib", "value": 32768, "provenance": "user"},
        {"name": "gpu", "value": "1", "provenance": "user"},
        {"name": "execution_deadline_seconds", "value": 7200, "provenance": "user"},
    ]
    saved_status, revised = _complete(reopened, "request_saved", fields, ["technical-smoke-test"])
    assert saved_status == 200
    assert revised["plan_revision"] == saved_revision + 1
    assert revised["execution_review"]["digest"] != saved_digest
    assert revised["acknowledgement_evidence"]["recorded"] is True
    assert revised["acknowledgement_evidence"]["text"] == sentence
    assert sentence in revised["acknowledgement_evidence"]["plan_request_text"]
    assert "filter_policy_acknowledgement_required" not in revised["execution_review"]["unresolved_blockers"]
    assert revised["execution_review"]["execution_authorized"] is False
    assert revised["execution_review"]["launches_compute"] is False
    assert revised["gpu_execution"] == "disabled"
    assert all("not recorded" not in item.get("conflict", "") for item in revised["conflicts"])
    revised_plan = reopened_plans.published(str(created["plan_id"]))
    assert revised_plan is not None
    assert revised_plan["revision"] == saved_revision + 1
    assert sentence in revised_plan["fields"]["request_text"]
    assert revised_plan["fields"]["filter_policy"] == "no_filters"
    assert revised_plan["approved"] is False
    reopened.close()
    reopened_plans.close()

    reread_plans = GenerationPlanService(
        None, inputs=catalog, store=SQLiteGenerationPlanStore(plan_path)
    )
    reread = DesignJourneyService(reread_plans, store=SQLiteDesignJourneyStore(journey_path))
    reread_status, again = handle_design_journey(
        reread, "GET", f"/api/design-journeys/{created['journey_id']}", b""
    )
    assert reread_status == 200
    assert again["plan_revision"] == saved_revision + 1
    assert again["acknowledgement_evidence"]["text"] == sentence
    assert again["execution_review"]["digest"] == revised["execution_review"]["digest"]
    assert "filter_policy_acknowledgement_required" not in again["execution_review"]["unresolved_blockers"]
    assert again["execution_review"]["launches_compute"] is False
    stored_plan = reread_plans.published(str(created["plan_id"]))
    assert stored_plan is not None
    assert stored_plan["revision"] == saved_revision + 1
    assert sentence in stored_plan["fields"]["request_text"]
    reread.close()
    reread_plans.close()


def test_a_verified_hotspot_is_offered_and_a_different_statement_is_kept() -> None:
    inputs = _VerifiedPdl1Inputs(hotspot="A56", sequence_limits={
        "max_trajectories": 1,
        "num_seqs": 1,
        "max_mpnn_sequences": 1,
    })
    plans = GenerationPlanService(None, inputs=inputs)
    service = DesignJourneyService(plans)
    created = service.open_or_get("request_hotspot", 1, "Design a PDL1 binder for review.")
    by_name = {
        item["name"]: item
        for item in created["completion"]["technical_demonstration"]["values"]
    }
    assert by_name["hotspot_assumption"]["value"] == "A56"
    assert by_name["hotspot_assumption"]["source"] == "verified_input"
    assert by_name["hotspot_assumption"]["recordable"] is True
    assert by_name["sequence_limits"]["recordable"] is True
    assert by_name["target_identity"]["value"] == "PDL1"
    status, revised = _complete(
        service,
        "request_hotspot",
        [
            {"name": "hotspot_assumption", "value": "B12", "provenance": "user"},
            {
                "name": "sequence_limits",
                "value": {"max_trajectories": 1, "num_seqs": 1, "max_mpnn_sequences": 1},
                "provenance": "user",
                "source": "verified_input",
            },
        ],
    )
    assert status == 200
    assert revised["plan_revision"] == 2
    published = plans.published(created["plan_id"])
    assert published is not None
    assert published["fields"]["hotspot_assumption"] == "B12"
    assert published["fields"]["hotspot_assumption_conflict"] is True
    assert published["fields"]["sequence_limits"] == {
        "max_trajectories": 1,
        "num_seqs": 1,
        "max_mpnn_sequences": 1,
    }
    assert published["fields"]["sequence_limits_source"] == "verified_input"


def test_a_request_only_edit_keeps_the_approved_plan_revision() -> None:
    plans = GenerationPlanService(None)
    service = DesignJourneyService(plans)
    created = service.open_or_get("request_objective", 1, "Design a PDL1 binder for review.")
    status, approved = service.record_approval(
        created["journey_id"], _approval(created, _digest(created))
    )
    assert status == 200
    status, revised = _complete(
        service,
        "request_objective",
        [{"name": "objective", "value": "binder_design", "provenance": "user"}],
    )
    assert status == 200
    assert revised["plan_revision"] == approved["plan_revision"]
    assert revised["approved"] is True
    assert revised["approval"]["digest"] == approved["approval"]["digest"]
    assert revised["request_fields"]["objective"]["value"] == "binder_design"
    assert revised["approval_history"] == []


def _complete(
    service: DesignJourneyService,
    request_id: str,
    fields: list[dict[str, object]],
    acknowledgements: list[str] | None = None,
) -> tuple[int, dict[str, object]]:
    payload: dict[str, object] = {
        "request_id": request_id,
        "request_revision": 1,
        "request_text": "Design a PDL1 binder for review."
        if "PDL1" in request_id or request_id != "request_fields"
        else "Design a binder for review.",
        "fields": fields,
    }
    if acknowledgements is not None:
        payload["acknowledgements"] = acknowledgements
    if request_id == "request_fields":
        payload["request_text"] = "Design a binder for review."
    status, body = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys",
        json.dumps(payload).encode(),
    )
    return status, body


def _refuse_draft(*_args: object, **_kwargs: object) -> dict[str, object]:
    raise AssertionError("reopening drafted a plan")


def test_linking_the_saved_plan_is_idempotent_and_preserves_it(tmp_path: Path) -> None:
    plan_path = tmp_path / "plans.sqlite"
    plans = GenerationPlanService(None, store=SQLiteGenerationPlanStore(plan_path))
    created = plans.draft(
        "design-request:request_link:revision:2",
        {"request_text": "#5 design an new binder to PDL1"},
    )
    original = plans.published(created["plan_id"])
    assert original is not None
    before = _plan_rows(plan_path)
    service = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(tmp_path / "journeys.sqlite"))

    def refuse_draft(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("linking drafted a plan")

    plans.draft = refuse_draft  # type: ignore[method-assign]
    absent, missing = handle_design_journey(
        service,
        "GET",
        "/api/design-journeys/by-request/request_link",
        b"",
    )
    assert absent == 404
    assert missing == {"status": "journey_absent"}
    assert _plan_rows(plan_path) == before
    body = {
        "request_id": "request_link",
        "request_revision": 2,
        "plan_id": original["plan_id"],
        "revision": original["revision"],
        "digest": original["digest"],
    }
    mismatched, mismatch = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys/link",
        json.dumps({**body, "request_revision": 1}).encode(),
    )
    assert mismatched == 409
    assert mismatch["approved"] is False
    assert _plan_rows(plan_path) == before
    status, linked = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys/link",
        json.dumps(body).encode(),
    )
    assert status == 200
    assert linked["plan_id"] == original["plan_id"]
    assert linked["plan_revision"] == original["revision"]
    assert linked["request_id"] == "request_link"
    assert linked["request_revision"] == 2
    assert linked["approved"] is False
    assert linked["gpu_execution"] == "disabled"
    assert linked["launches_compute"] is False
    assert _plan_rows(plan_path) == before
    again_status, again = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys/link",
        json.dumps(body).encode(),
    )
    assert again_status == 200
    assert again["journey_id"] == linked["journey_id"]
    assert again["plan_id"] == original["plan_id"]
    assert sqlite3.connect(plan_path).execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
    stale_digest, stale = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys/link",
        json.dumps({**body, "digest": "0" * 64}).encode(),
    )
    assert stale_digest == 409
    assert "Review the current data" in stale["detail"]
    stale_revision, _ = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys/link",
        json.dumps({**body, "revision": original["revision"] + 1}).encode(),
    )
    assert stale_revision == 409
    found_status, found = handle_design_journey(
        service,
        "GET",
        "/api/design-journeys/by-request/request_link",
        b"",
    )
    assert found_status == 200
    assert found["journey_id"] == linked["journey_id"]
    assert found["plan_id"] == original["plan_id"]
    assert plans.published(original["plan_id"])["digest"] == original["digest"]
    assert _plan_rows(plan_path) == before
    approval = {
        "plan_id": original["plan_id"],
        "revision": original["revision"],
        "digest": original["digest"],
        "reviewer_id": "professor.ada",
        "reason": "Reviewed this revision.",
        "idempotency_key": "approval-link",
    }
    refused, refusal = handle_design_journey(
        service,
        "POST",
        f"/api/design-journeys/{linked['journey_id']}/approval",
        json.dumps({**approval, "digest": "0" * 64}).encode(),
    )
    assert refused == 409
    assert plans.published(original["plan_id"])["approved"] is False
    assert _plan_rows(plan_path) == before
    approved_status, approved = handle_design_journey(
        service,
        "POST",
        f"/api/design-journeys/{linked['journey_id']}/approval",
        json.dumps(approval).encode(),
    )
    assert approved_status == 200
    assert approved["approved"] is True
    assert approved["plan_id"] == original["plan_id"]
    assert approved["plan_revision"] == original["revision"]
    assert approved["gpu_execution"] == "disabled"
    assert approved["launches_compute"] is False
    assert approved.get("execution_authorization") is None
    recorded = plans.published(original["plan_id"])
    assert recorded["approved"] is True
    assert recorded["revision"] == original["revision"]
    assert recorded["digest"] == original["digest"]
    assert recorded["execution_authorized"] is False
    assert recorded["scientific_execution"] == "disabled"
    assert recorded["authorization_id"] is None
    repeated_status, repeated = handle_design_journey(
        service,
        "POST",
        f"/api/design-journeys/{linked['journey_id']}/approval",
        json.dumps(approval).encode(),
    )
    assert repeated_status == 200
    assert repeated["journey_id"] == linked["journey_id"]
    assert repeated["approval"]["digest"] == original["digest"]
    assert sqlite3.connect(plan_path).execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
    service.close()
    plans.close()


def _plan_rows(path: Path) -> list[tuple[str, str]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT plan_id, document FROM plans ORDER BY plan_id").fetchall()
    finally:
        connection.close()


def test_a_running_attempt_exposes_its_log_without_starting_a_process(tmp_path: Path) -> None:
    from cellertz.application.design_journey import _live_output

    root = tmp_path / "attempts"
    attempt = root / "attempt.19d10373"
    attempt.mkdir(parents=True)
    (attempt / "executor.stdout").write_text("Stage 1: Test Logits\nStage 2: Softmax\n", encoding="utf-8")
    (attempt / "executor.stderr").write_text("", encoding="utf-8")
    execution = {
        "source": "run_design",
        "status": "running",
        "attempt_directory": str(attempt),
    }
    assert _live_output(root, execution) == "Stage 1: Test Logits\nStage 2: Softmax"
    assert _live_output(root, {**execution, "status": "launch_failed"}) is None
    assert _live_output(root, {**execution, "attempt_directory": str(tmp_path / "elsewhere")}) is None
    assert (attempt / "executor.stdout").read_text(encoding="utf-8") == "Stage 1: Test Logits\nStage 2: Softmax\n"


def test_a_terminal_launch_failure_stays_recorded_and_does_not_block_a_later_attempt() -> None:
    from cellertz.application.design_journey import _stored_run

    parsed = {"plan_id": "plan_shown", "digest": "a" * 64, "revision": 3}
    failed = {
        "source": "run_design",
        "plan_id": "plan_shown",
        "digest": "a" * 64,
        "revision": 3,
        "attempt_id": "attempt.42ef617c",
        "status": "launch_failed",
        "launched": True,
        "terminal": True,
    }
    assert _stored_run({"execution": failed}, parsed) is None
    kept = {**failed, "launched": False, "status": "stub_diagnosis"}
    assert _stored_run({"execution": kept}, parsed)["attempt_id"] == "attempt.42ef617c"


def test_run_design_uses_the_displayed_revision_and_does_not_invent_an_approval(tmp_path: Path) -> None:
    catalog, _hashes = _input_files(tmp_path)
    plan_path = tmp_path / "plans.sqlite"
    plans = GenerationPlanService(None, store=SQLiteGenerationPlanStore(plan_path), inputs=catalog)
    service = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(tmp_path / "journeys.sqlite"))
    created, drafts = _ready_plan(plans, tmp_path)
    original = plans.published(created["plan_id"])
    assert original is not None
    assert original["approval"] is None
    before = _plan_rows(plan_path)
    body = {
        "request_id": "request_run",
        "request_revision": 1,
        "plan_id": original["plan_id"],
        "revision": original["revision"],
        "digest": original["digest"],
        "idempotency_key": "run-displayed",
    }
    refused, refusal = handle_design_journey(
        service, "POST", "/api/design-journeys/run", json.dumps(body).encode()
    )
    assert refused == 409
    assert refusal["status"] == "execution_not_permitted"
    assert refusal["launched"] is False
    assert refusal["automatic_retry"] is False
    assert "reviewer_id" not in refusal
    absent, missing = handle_design_journey(
        service, "GET", "/api/design-journeys/by-request/request_run", b""
    )
    assert absent == 404
    assert missing == {"status": "journey_absent"}
    stale, stale_body = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys/run",
        json.dumps({**body, "digest": "0" * 64}).encode(),
    )
    assert stale == 409
    assert stale_body["status"] == "plan_revision_conflict"
    assert "Review the current data" in stale_body["detail"]
    assert _plan_rows(plan_path) == before
    plans.close()
    service.close()

    class Stub:
        def __init__(self) -> None:
            self.calls = 0

        def launch(self, _request: object) -> dict[str, object]:
            self.calls += 1
            return {"status": "stub_diagnosis", "launched": False, "diagnosis": "generation was not started"}

    stub = Stub()
    plans = GenerationPlanService(
        None,
        store=SQLiteGenerationPlanStore(plan_path),
        inputs=catalog,
        demonstration=stub,
    )
    service = DesignJourneyService(
        plans,
        store=SQLiteDesignJourneyStore(tmp_path / "journeys-run.sqlite"),
        attempt_root=tmp_path / "attempts",
        drafter=drafts,
    )
    status, started = handle_design_journey(
        service, "POST", "/api/design-journeys/run", json.dumps(body).encode()
    )
    assert status == 200
    assert started["run"]["launched"] is False
    assert started["run"]["automatic_retry"] is False
    assert started["run"]["diagnosis"] == "generation was not started"
    assert started["run"]["plan_id"] == original["plan_id"]
    assert started["run"]["digest"] == original["digest"]
    assert started["approved"] is False
    assert "reviewer_id" not in json.dumps(started["run"])
    assert stub.calls == 1
    recorded = plans.published(original["plan_id"])
    assert recorded["approval"] is None
    assert recorded["revision"] == original["revision"]
    assert recorded["digest"] == original["digest"]
    assert recorded["execution_authorized"] is False
    again, repeated = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys/run",
        json.dumps({**body, "idempotency_key": "run-again"}).encode(),
    )
    assert again == 200
    assert repeated["run"]["attempt_id"] == started["run"]["attempt_id"]
    assert repeated["run"]["diagnosis"] == "generation was not started"
    assert stub.calls == 1
    service.close()
    plans.close()


def _input_files(tmp_path: Path) -> tuple[RegisteredInputCatalog, dict[str, str]]:
    payloads = {
        "target_structure": b"TARGET\n",
        "settings": b'{"chain": "A"}\n',
        "advanced_profile": b'{"k": 1}\n',
    }
    files = {}
    for role, payload in payloads.items():
        path = tmp_path / role
        path.write_bytes(payload)
        files[role] = path
    return RegisteredInputCatalog(files), {}


def _ready_plan(plans: GenerationPlanService, tmp_path: Path) -> tuple[dict[str, Any], DesignDrafting]:
    session = pdl1_prototype_session()
    drafts = DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        request_ids=lambda: "request_run",
    )
    opened = drafts.revise(DesignDraftCommand(session.session_id, "design a new binder to PDL1\n1"))
    assert opened.draft is not None
    configuration = SimpleNamespace(read=lambda: SimpleNamespace(tool_name="bindcraft", target_scope="PDL1"))
    created = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_run","revision":1}',
        configuration=configuration,
    )
    plan = created[1]["plan"]
    revised = handle_design_request_plan_revision(
        plans,
        drafts,
        body=json.dumps(
            {
                "request_id": "request_run",
                "request_revision": 1,
                "plan_id": plan["plan_id"],
                "expected_plan_revision": plan["revision"],
                "scientific": {
                    "binder_length": 65,
                    "hotspot": {"mode": "none"},
                    "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
                },
                "technical": {
                    "seed": 65,
                    "gpu": "0",
                    "cpu_count": 4,
                    "memory_mib": 32768,
                    "execution_deadline_seconds": 7200,
                },
            }
        ).encode(),
        configuration=configuration,
    )
    assert revised[0] == 200, revised[1]
    assert revised[1]["plans"][0]["questions"] == []
    return revised[1]["plans"][0], drafts


def test_http_routes_read_the_saved_journey_and_block_approval() -> None:
    service = DesignJourneyService(_CountingPlans())
    status, created = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys",
        b'{"request_id":"request_http","request_revision":1,"request_text":"Design a PDL1 binder."}',
    )
    assert status == 200
    journey_id = created["journey_id"]
    read_status, read = handle_design_journey(service, "GET", f"/api/design-journeys/{journey_id}", b"")
    assert read_status == 200
    assert read["plan_revision"] == 1
    blocked, body = handle_design_journey(
        service, "POST", f"/api/design-journeys/{journey_id}/approval", b"{}"
    )
    assert blocked == 400
    assert body["gpu_execution"] == "disabled"
    assert body["approved"] is False
    missing, _ = handle_design_journey(service, "GET", "/api/design-journeys/journey_missing", b"")
    assert missing == 404


def test_saved_plan_approval_rejects_stale_duplicates_and_does_not_launch() -> None:
    demonstration = _Demonstration()
    runs = _Runs()
    plans = GenerationPlanService(runs, demonstration=demonstration)
    service = DesignJourneyService(plans)
    document = service.open_or_get("request_approve", 1, _TEXT)
    digest = _digest(document)
    payload = _approval(document, digest)
    ordinary = plans.approve(
        plan_id=document["plan_id"],
        revision=document["plan_revision"],
        reviewer_id=payload["reviewer_id"],
        reason=payload["reason"],
        idempotency_key="ordinary-approval",
    )
    assert ordinary["approved"] is False
    assert ordinary["status"] == "approval_rejected"
    raw = json.dumps(payload).encode()
    status, approved = handle_design_journey(
        service,
        "POST",
        f"/api/design-journeys/{document['journey_id']}/approval",
        raw,
    )
    assert status == 200
    assert approved["approved"] is True
    assert approved["gpu_execution"] == "disabled"
    assert approved["launches_compute"] is False
    assert approved["scientific_validation_established"] is False
    assert approved["approval"]["reviewer_id"] == "professor.ada"
    assert approved["approval"]["digest"] == digest
    assert approved["approval"]["recorded_at"]
    assert _status(approved, "approval") == "completed"
    assert "does not launch generation" in _stage(approved, "generation")["limitations"][0]
    assert _status(approved, "generation") == "blocked"
    repeated = dict(payload, idempotency_key="approval-again")
    again_status, again = service.record_approval(document["journey_id"], repeated)
    assert again_status == 200
    assert again["approval"]["recorded_at"] == approved["approval"]["recorded_at"]
    stored = plans.published(document["plan_id"])
    assert stored is not None
    assert [event["kind"] for event in stored["events"]].count("approval_recorded") == 1
    conflict_status, conflict = service.record_approval(
        document["journey_id"],
        dict(payload, reason="A different review.", idempotency_key="approval-other"),
    )
    assert conflict_status == 409
    assert conflict["status"] == "idempotency_conflict"
    assert plans.published(document["plan_id"])["approval"]["reason"] == payload["reason"]
    stale_status, stale = service.record_approval(
        document["journey_id"],
        dict(payload, revision=document["plan_revision"] + 1, idempotency_key="approval-stale"),
    )
    assert stale_status == 409
    assert stale["status"] == "approval_rejected"
    assert service.get(document["journey_id"])["approved"] is True
    launched = plans.launch_apptainer_demonstration(
        plan_id=document["plan_id"],
        revision=document["plan_revision"],
        reviewer_id=payload["reviewer_id"],
        reason=payload["reason"],
        idempotency_key="launch-1",
        gpu_index=0,
        attempt_id="attempt.notlaunched",
        attempt_directory="/tmp/cellertz-not-used",
    )
    assert launched["launched"] is False
    assert launched["status"] == "journey_execution_not_connected"
    assert demonstration.calls == 0
    dispatched = plans.dispatch(
        plan_id=document["plan_id"],
        revision=document["plan_revision"],
        idempotency_key="dispatch-1",
    )
    assert dispatched["status"] == "dispatch_blocked"
    assert dispatched["blockers"] == ["journey_execution_not_connected"]
    assert dispatched["execution_connected"] is False
    assert runs.creates == 0
    revised = service.open_or_get("request_approve", 2, _TEXT + " Revision two.")
    assert revised["approved"] is False
    assert revised["plan_revision"] == 2
    assert _status(revised, "approval") == "pending"
    assert plans.published(revised["plan_id"])["approved"] is False


def test_approval_remains_with_the_plan_after_restart(tmp_path: Path) -> None:
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    plans = GenerationPlanService(None, store=SQLiteGenerationPlanStore(plan_path))
    service = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(journey_path))
    document = service.open_or_get("request_keep_approval", 1, _TEXT)
    status, approved = service.record_approval(document["journey_id"], _approval(document, _digest(document)))
    assert status == 200
    service.close()
    plans.close()
    reopened_plans = GenerationPlanService(None, store=SQLiteGenerationPlanStore(plan_path))
    reopened = DesignJourneyService(_Replay(), store=SQLiteDesignJourneyStore(journey_path))
    again = reopened.open_or_get("request_keep_approval", 1, _TEXT)
    stored = reopened_plans.published(again["plan_id"])
    assert again["journey_id"] == approved["journey_id"]
    assert again["approved"] is True
    assert again["plan_revision"] == approved["plan_revision"]
    assert again["approval"]["digest"] == stored["approval"]["digest"]
    assert again["approval"]["reviewer_id"] == stored["approval"]["reviewer_id"]
    assert again["approval"]["recorded_at"] == stored["approval"]["recorded_at"]
    assert stored["execution_connected"] is False
    assert _status(again, "generation") == "blocked"
    reopened.close()
    reopened_plans.close()


def test_explicit_authorization_replaces_recorded_plan_scope_without_launch(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True, "accepted_candidate": True})
    plans, service, document = _ready_journey(tmp_path, launcher)
    status, approved = service.record_approval(document["journey_id"], _approval(document, _digest(document)))
    assert status == 200
    review = approved["execution_review"]
    assert approved["approval"]["scope"] == "recorded_plan"
    assert review["recorded_plan_approval"] is True
    assert review["authorization_available"] is True
    assert review["server_execution"] == "apptainer-demonstration"
    assert review["journey_gpu_execution"] == "disabled"
    assert approved["gpu_execution"] == "disabled"
    assert review["configuration_blockers"] == []
    assert review["unresolved_blockers"] == []
    assert review["execution_authorized"] is False
    assert review["launches_compute"] is False
    granted_status, granted = service.authorize_execution(document["journey_id"], _authorization(approved))
    assert granted_status == 200, granted
    assert granted["execution_authorization"]["execution_authorized"] is True
    assert granted["execution_authorization"]["launches_compute"] is False
    assert granted["execution_review"]["execution_authorized"] is True
    assert granted["execution_review"]["authorization_available"] is False
    assert granted["execution_review"]["launches_compute"] is False
    assert granted.get("execution") is None
    assert launcher.calls == 0
    stored = plans.published(document["plan_id"])
    assert stored["revision"] == document["plan_revision"]
    assert "scope" not in stored["approval"]
    assert stored["approval_history"][-1]["scope"] == "recorded_plan"
    assert _status(granted, "generation") == "blocked"
    assert _status(granted, "approval") == "completed"
    assert granted["rounds"][-1]["recorded_review_status"] == "recorded_plan"
    assert any("This recorded review stays completed." in item for item in _stage(granted, "approval")["limitations"])


def test_saved_recorded_plan_reopened_under_demonstration_offers_authorization(
    tmp_path: Path,
) -> None:
    launcher = _Outcome({"status": "verified", "launched": True, "accepted_candidate": True})
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    plans, service, document = _ready_journey(
        tmp_path,
        launcher,
        plan_store=SQLiteGenerationPlanStore(plan_path),
        journey_store=SQLiteDesignJourneyStore(journey_path),
    )
    status, approved = service.record_approval(document["journey_id"], _approval(document, _digest(document)))
    assert status == 200
    service.close()
    plans.close()
    catalog = _input_catalog(tmp_path / "inputs")

    disabled_plans = GenerationPlanService(
        None, inputs=catalog, store=SQLiteGenerationPlanStore(plan_path)
    )
    disabled = DesignJourneyService(disabled_plans, store=SQLiteDesignJourneyStore(journey_path))
    plan_rows = _sqlite_documents(plan_path, "plans", "plan_id")
    journey_rows = _sqlite_documents(journey_path, "design_journeys", "journey_id")
    hidden_status, hidden = handle_design_journey(
        disabled, "GET", f"/api/design-journeys/{document['journey_id']}", b""
    )
    assert hidden_status == 200
    assert hidden["execution_review"]["authorization_available"] is False
    assert hidden["execution_review"]["configuration_blockers"] == ["demonstration_not_configured"]
    assert hidden["execution_review"]["server_execution"] == "disabled"
    assert hidden["execution_review"]["journey_gpu_execution"] == "disabled"
    assert hidden["gpu_execution"] == "disabled"
    assert hidden["approval"]["scope"] == "recorded_plan"
    assert hidden["execution_review"]["execution_authorized"] is False
    refused, body = disabled.authorize_execution(document["journey_id"], _authorization(hidden))
    assert refused == 409
    assert body["status"] == "demonstration_not_configured"
    assert body["unresolved_blockers"] == ["demonstration_not_configured"]
    assert _sqlite_documents(plan_path, "plans", "plan_id") == plan_rows
    assert _sqlite_documents(journey_path, "design_journeys", "journey_id") == journey_rows
    assert launcher.calls == 0
    disabled.close()
    disabled_plans.close()

    enabled_plans = GenerationPlanService(
        None,
        inputs=catalog,
        demonstration=launcher,
        store=SQLiteGenerationPlanStore(plan_path),
    )
    enabled = DesignJourneyService(
        enabled_plans,
        store=SQLiteDesignJourneyStore(journey_path),
        attempt_root=tmp_path / "attempts",
    )
    shown_status, shown = handle_design_journey(
        enabled, "GET", f"/api/design-journeys/{document['journey_id']}", b""
    )
    assert shown_status == 200
    assert _sqlite_documents(plan_path, "plans", "plan_id") == plan_rows
    assert _sqlite_documents(journey_path, "design_journeys", "journey_id") == journey_rows
    assert shown["execution_review"]["authorization_available"] is True
    assert shown["execution_review"]["server_execution"] == "apptainer-demonstration"
    assert shown["execution_review"]["journey_gpu_execution"] == "disabled"
    assert shown["execution_review"]["configuration_blockers"] == []
    assert shown["execution_review"]["recorded_plan_approval"] is True
    assert shown["execution_review"]["unresolved_blockers"] == []
    assert shown["execution_review"]["execution_authorized"] is False
    assert shown["execution_review"]["launches_compute"] is False
    assert shown.get("execution") is None
    assert shown["approval"]["scope"] == "recorded_plan"
    assert shown["plan_revision"] == approved["plan_revision"]
    assert shown["execution_review"]["digest"] == approved["execution_review"]["digest"]
    assert _status(shown, "execution_authorization") == "pending"
    stored_journey = json.loads(journey_rows[0][1])
    assert _status(stored_journey, "execution_authorization") == "blocked"
    assert launcher.calls == 0
    enabled.close()
    enabled_plans.close()


def test_unready_plan_keeps_its_revision_and_does_not_launch(tmp_path: Path) -> None:
    launcher = _Outcome({"launched": True})
    plans = GenerationPlanService(None, demonstration=launcher)
    service = DesignJourneyService(plans, attempt_root=tmp_path / "attempts")
    document = service.open_or_get("request_blocked", 1, _TEXT)
    assert "open_questions" in document["execution_review"]["unresolved_blockers"]
    status, body = service.authorize_execution(document["journey_id"], _authorization(document))
    assert status == 409
    assert body["status"] == "execution_not_ready"
    assert launcher.calls == 0
    assert plans.published(document["plan_id"])["revision"] == document["plan_revision"]
    assert service.get(document["journey_id"])["approved"] is False


def test_launch_requires_authorization_and_rechecks_identity(tmp_path: Path) -> None:
    launcher = _Outcome(
        {
            "status": "verified",
            "launched": True,
            "accepted_candidate": True,
            "successful_candidate_generation": True,
            "candidate_id": "PDL1_l65_s7_mpnn1",
            "progress": [{"status": "started"}, {"status": "verified"}],
        }
    )
    plans, service, document = _ready_journey(tmp_path, launcher)
    missing, missing_body = service.launch_execution(document["journey_id"], _launch(document))
    assert missing == 409
    assert missing_body["status"] == "execution_authorization_required"
    assert launcher.calls == 0
    authorized_status, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    assert authorized_status == 200, authorized
    assert authorized["execution_authorization"]["execution_authorized"] is True
    assert authorized["execution_authorization"]["launches_compute"] is False
    assert authorized["approved"] is False
    assert launcher.calls == 0
    assert _status(authorized, "generation") == "blocked"
    stale, _stale_body = service.launch_execution(
        document["journey_id"],
        dict(_launch(authorized), revision=authorized["plan_revision"] + 1, idempotency_key="launch-stale"),
    )
    assert stale == 409
    assert launcher.calls == 0
    target = tmp_path / "inputs" / "target_structure"
    target.write_bytes(b"TARGET-CHANGED\n")
    changed, changed_body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert changed == 409
    assert "input_identity_rejected" in changed_body["unresolved_blockers"] or changed_body["status"] == "execution_not_ready"
    assert launcher.calls == 0
    target.write_bytes(b"TARGET\n")
    failed = _Outcome(
        {
            "status": "launch_failed",
            "launched": True,
            "progress": [{"status": "started"}, {"status": "failed"}],
        }
    )
    plans._demonstration = failed
    failure_status, failure = service.launch_execution(
        document["journey_id"],
        dict(_launch(authorized), idempotency_key="launch-failed"),
    )
    assert failure_status == 200
    assert failed.calls == 1
    assert failure["execution"]["terminal"] is True
    assert failure["execution"]["successful_candidate_generation"] is False
    assert _status(failure, "generation") == "failed"
    assert _status(failure, "scoring") == "blocked"
    assert _status(failure, "ranking") == "blocked"
    again, again_body = service.launch_execution(
        document["journey_id"],
        dict(_launch(authorized), idempotency_key="launch-again"),
    )
    assert again == 409
    assert again_body["status"] == "duplicate_launch"
    assert failed.calls == 1


def test_budget_exhaustion_links_the_attempt_and_is_not_a_candidate(tmp_path: Path) -> None:
    launcher = _Outcome(
        {
            "status": "budget_exhausted",
            "launched": True,
            "accepted_candidate": False,
            "successful_candidate_generation": False,
            "termination_reason": "attempted_trajectory_budget_exhausted",
            "progress": [{"status": "started"}, {"status": "budget_exhausted"}],
        }
    )
    _plans, service, document = _ready_journey(tmp_path, launcher)
    _status_code, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    assert launcher.calls == 1
    execution = launched["execution"]
    assert execution["terminal"] is True
    assert execution["successful_candidate_generation"] is False
    assert execution["accepted_candidate"] is False
    assert execution["result_link"]["journey_id"] == document["journey_id"]
    assert execution["result_link"]["attempt_id"] == execution["attempt_id"]
    assert execution["result_link"]["candidate_id"] is None
    generation = _stage(launched, "generation")
    assert generation["status"] == "completed"
    assert generation["output"]["successful_candidate_generation"] is False
    assert generation["limitations"][0] == "Budget exhausted without an accepted candidate."
    assert _status(launched, "scoring") == "blocked"
    assert _status(launched, "critique") == "blocked"
    assert _status(launched, "prioritization") == "blocked"
    repeated, _repeated_body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert repeated == 200
    assert launcher.calls == 1


def test_reconcile_records_budget_exhaustion_without_replaying_or_rewriting(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True})
    plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    attempt_id = "attempt.0fb068b6"
    attempt_directory = tmp_path / "attempts" / attempt_id
    _write_observed_attempt(attempt_directory, attempt_id)
    before = _file_bytes(attempt_directory)
    stored = service._store.get(document["journey_id"])
    assert stored is not None
    stored["execution"] = _running_execution(document, authorized, attempt_id, attempt_directory)
    service._store.put(stored)
    status, body = handle_design_journey(
        service,
        "POST",
        f"/api/design-journeys/{document['journey_id']}/execution-reconciliation",
        b"{}",
    )
    assert status == 200, body
    assert launcher.calls == 0
    execution = body["execution"]
    assert execution["status"] == "budget_exhausted"
    assert execution["terminal"] is True
    assert execution["consumed"] is True
    assert execution["launched"] is True
    assert execution["accepted_candidate"] is False
    assert execution["successful_candidate_generation"] is False
    assert execution["budget_compliance_established"] is True
    assert execution["scientific_validation_established"] is False
    assert execution["termination_reason"] == "attempted_trajectory_budget_exhausted"
    assert execution["failure_counters"] == {
        "Trajectory_one-hot_pLDDT": "1",
        "Trajectory_final_pLDDT": "1",
    }
    assert "metrics" not in execution
    assert execution["raw_progress"]["status"] == "rejected"
    assert execution["raw_progress"]["reasons"] == ["final_design_row_missing"]
    assert execution["designs_directory"].endswith(f"{attempt_id}/designs")
    assert execution["attempt_id"] == attempt_id
    generation = _stage(body, "generation")
    assert generation["limitations"][0] == "Budget exhausted without an accepted candidate."
    assert generation["output"]["scientific_validation_established"] is False
    assert generation["output"]["budget_compliance_established"] is True
    assert generation["output"]["failure_counters"]["Trajectory_one-hot_pLDDT"] == "1"
    assert "metrics" not in generation["output"]
    assert _file_bytes(attempt_directory) == before
    assert plans.published(document["plan_id"])["revision"] == document["plan_revision"]
    again, again_body = handle_design_journey(
        service,
        "POST",
        f"/api/design-journeys/{document['journey_id']}/execution-reconciliation",
        b"",
    )
    assert again == 200
    assert again_body["execution"]["attempt_id"] == attempt_id
    assert again_body["execution"]["status"] == "budget_exhausted"
    assert launcher.calls == 0
    assert _file_bytes(attempt_directory) == before


def test_reconciliation_ignores_a_directory_that_is_not_the_known_layout(tmp_path: Path) -> None:
    launcher = _Outcome({"launched": True})
    _plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    attempt_id = "attempt.0fb068b6"
    attempt_directory = tmp_path / "attempts" / attempt_id
    decoy = attempt_directory / "unexpected" / "designs"
    _write_observed_attempt(attempt_directory, "unexpected")
    decoy_bytes = _file_bytes(decoy)
    stored = service._store.get(document["journey_id"])
    assert stored is not None
    stored["execution"] = _running_execution(document, authorized, attempt_id, attempt_directory)
    service._store.put(stored)
    status, body = service.reconcile_execution(document["journey_id"])
    assert status == 200
    assert body["execution"]["status"] == "execution_unresolved"
    assert body["execution"]["terminal"] is False
    assert body["execution"]["consumed"] is True
    assert body["execution"]["budget_compliance_established"] is False
    assert body["execution"]["evidence_checks"]["known_designs_directory"] is False
    assert launcher.calls == 0
    assert _file_bytes(decoy) == decoy_bytes
    repeated, _repeated_body = service.launch_execution(
        document["journey_id"], dict(_launch(authorized), idempotency_key="launch-again")
    )
    assert repeated == 409
    assert launcher.calls == 0


def test_a_launcher_exception_finalizes_saved_evidence_without_replay(tmp_path: Path) -> None:
    launcher = _EvidenceThenError()
    plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    status, body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert status == 200, body
    assert launcher.calls == 1
    assert body["execution"]["status"] == "budget_exhausted"
    assert body["execution"]["terminal"] is True
    assert body["execution"]["accepted_candidate"] is False
    assert body["execution"]["budget_compliance_established"] is True
    assert body["execution"]["scientific_validation_established"] is False
    assert plans.published(document["plan_id"])["revision"] == document["plan_revision"]
    again, _again_body = service.launch_execution(
        document["journey_id"], dict(_launch(authorized), idempotency_key="launch-again")
    )
    assert again == 409
    assert launcher.calls == 1


def test_a_launcher_exception_without_evidence_is_unresolved_and_not_replayed(
    tmp_path: Path,
) -> None:
    launcher = _Raises(RuntimeError("reply was not saved"))
    _plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    status, body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert status == 200, body
    assert launcher.calls == 1
    assert body["execution"]["status"] == "execution_unresolved"
    assert body["execution"]["consumed"] is True
    assert body["execution"]["terminal"] is False
    assert body["execution"]["launched"] is False
    assert body["execution"]["exception_type"] == "RuntimeError"
    assert _status(body, "generation") == "unresolved"
    again, again_body = service.launch_execution(
        document["journey_id"], dict(_launch(authorized), idempotency_key="launch-again")
    )
    assert again == 409
    assert again_body["status"] == "duplicate_launch"
    assert launcher.calls == 1


def test_gpu_index_mismatch_is_rejected_before_a_launch_is_consumed(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True})
    plans, service, document = _ready_journey(tmp_path, launcher, gpu="1")
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    refused, body = service.launch_execution(
        document["journey_id"], dict(_launch(authorized), gpu_index=0)
    )
    assert refused == 409
    assert body["status"] == "gpu_index_mismatch"
    assert launcher.calls == 0
    stored = service.get(document["journey_id"])
    assert stored is not None
    assert stored.get("execution") is None
    assert list((tmp_path / "attempts").glob("attempt.*")) == []
    assert plans.published(str(document["plan_id"]))["revision"] == document["plan_revision"]


def test_an_index_absent_from_inventory_is_rejected_before_consume(tmp_path: Path) -> None:
    launcher = _Inventory([0])
    _plans, service, document = _ready_journey(tmp_path, launcher, gpu="1")
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    refused, body = service.launch_execution(
        document["journey_id"], dict(_launch(authorized), gpu_index=1)
    )
    assert refused == 409
    assert body["status"] == "selected_gpu_missing"
    assert launcher.calls == 0
    stored = service.get(document["journey_id"])
    assert stored is not None
    assert stored.get("execution") is None


def test_explicit_gpu_index_zero_matches_the_approved_plan(tmp_path: Path) -> None:
    launcher = _Inventory([0])
    plans, service, document = _ready_journey(tmp_path, launcher, gpu="0")
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    status, body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert status == 200, body
    assert launcher.calls == 1
    assert launcher.requests[0].gpu_index == 0
    assert body["execution"]["gpu_index"] == 0
    assert body["execution"]["consumed"] is True
    published = plans.published(str(document["plan_id"]))
    assert published is not None
    assert published["fields"]["gpu"] == "0"
    assert published["fields"]["seed"] == 7
    assert published["fields"]["memory_mib"] == 16384
    assert published["revision"] == document["plan_revision"]


def test_an_occupied_gpu_does_not_block_the_explicit_selection(tmp_path: Path) -> None:
    class _Shared:
        def __init__(self) -> None:
            self.calls = 0
            self.inert_outputs = False

        def gpu_readiness(self, gpu_index: int) -> dict[str, object]:
            return {
                "status": "launch_ready",
                "launch_ready": True,
                "gpu_index": gpu_index,
                "occupancy": "shared",
                "compute_apps": [{"pid": "1475", "process_name": "oncobrain"}],
                "memory": {"used": "1188 MiB", "free": "30000 MiB"},
            }

        def launch(self, request: object) -> dict[str, object]:
            self.calls += 1
            return {"status": "verified", "launched": True, "attempt_id": getattr(request, "attempt_id", None)}

    launcher = _Shared()
    plans, service, document = _ready_journey(tmp_path, launcher, gpu="0")
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    status, body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert status == 200, body
    assert launcher.calls == 1
    assert body["execution"]["gpu_observation"]["occupancy"] == "shared"
    assert body["execution"]["gpu_observation"]["memory"]["free"] == "30000 MiB"
    published = plans.published(str(document["plan_id"]))
    assert published is not None
    assert published["fields"]["gpu"] == "0"
    assert published["revision"] == document["plan_revision"]


def test_a_missing_index_zero_is_not_replaced_with_another_gpu(tmp_path: Path) -> None:
    launcher = _Inventory([1])
    plans, service, document = _ready_journey(tmp_path, launcher, gpu="0")
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    refused, body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert refused == 409
    assert body["status"] == "selected_gpu_missing"
    assert launcher.calls == 0
    stored = service.get(document["journey_id"])
    assert stored is not None
    assert stored.get("execution") is None
    assert list((tmp_path / "attempts").glob("attempt.*")) == []
    published = plans.published(str(document["plan_id"]))
    assert published is not None
    assert published["fields"]["gpu"] == "0"
    assert published["revision"] == document["plan_revision"]


def test_vram_failure_is_recorded_once_and_is_not_relaunched(tmp_path: Path) -> None:
    launcher = _Outcome(
        {
            "status": "vram_insufficient",
            "launched": True,
            "automatic_retry": False,
            "other_services_signaled": False,
            "diagnostics": {
                "process_output": "CUDA out of memory",
                "gpu_memory": {"free": "100 MiB"},
            },
        }
    )
    _plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized_status, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    status, body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert status == 200, body
    assert launcher.calls == 1
    assert body["execution"]["status"] == "vram_insufficient"
    assert body["execution"]["automatic_retry"] is False
    assert body["execution"]["other_services_signaled"] is False
    assert body["execution"]["diagnostics"]["process_output"] == "CUDA out of memory"
    assert body["execution"]["terminal"] is True
    again, replay = service.launch_execution(document["journey_id"], _launch(authorized, key="launch-again"))
    assert again == 409
    assert replay["status"] == "duplicate_launch"
    assert launcher.calls == 1


def test_a_linked_candidate_survives_restart_and_a_running_launch_stays_unresolved(tmp_path: Path) -> None:
    launcher = _Outcome(
        {
            "status": "verified",
            "launched": True,
            "accepted_candidate": True,
            "successful_candidate_generation": True,
            "candidate_id": "PDL1_l65_s7_mpnn1",
            "progress": [{"status": "started"}, {"status": "verified"}],
        }
    )
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    _plans, service, document = _ready_journey(
        tmp_path,
        launcher,
        plan_store=SQLiteGenerationPlanStore(plan_path),
        journey_store=SQLiteDesignJourneyStore(journey_path),
    )
    _authorized_status, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    link = launched["execution"]["result_link"]
    assert launched["execution"]["accepted_candidate"] is False
    assert link["candidate_id"] is None
    assert link["journey_id"] == document["journey_id"]
    request = launcher.requests[0]
    attempt_id = str(launched["execution"]["attempt_id"])
    directory = Path(str(launched["execution"]["attempt_directory"]))
    assert attempt_id == request.attempt_id == link["attempt_id"]
    assert directory.name == attempt_id
    assert _safe_attempt_directory(attempt_id, directory) is None
    stale = tmp_path / "attempt.7d818cba2a1f"
    assert _safe_attempt_directory("attempt.7d818cba2a1f", stale) == "attempt_id_rejected"
    service.close()
    _plans.close()
    reopened_launcher = _Outcome({"launched": True, "status": "verified"})
    reopened_plans = GenerationPlanService(
        None,
        store=SQLiteGenerationPlanStore(plan_path),
        inputs=_input_catalog(tmp_path / "inputs"),
        demonstration=reopened_launcher,
    )
    reopened = DesignJourneyService(
        reopened_plans,
        store=SQLiteDesignJourneyStore(journey_path),
        attempt_root=tmp_path / "attempts",
    )
    again = reopened.get(document["journey_id"])
    assert again is not None
    assert again["execution"]["attempt_id"] == attempt_id
    assert again["execution"]["attempt_directory"] == str(directory)
    assert again["execution"]["result_link"] == link
    assert again["execution"]["result_link"]["attempt_id"] == attempt_id
    assert again["execution"]["terminal"] is True
    assert _status(again, "generation") == "completed"
    assert reopened_launcher.calls == 0
    running_launcher = _Outcome({"status": "running", "launched": True, "progress": [{"status": "started"}]})
    _running_plans, running_service, running_document = _ready_journey(tmp_path / "running", running_launcher)
    _running_authorized_status, running_authorized = running_service.authorize_execution(
        running_document["journey_id"],
        _authorization(running_document),
    )
    _running_launch_status, running = running_service.launch_execution(
        running_document["journey_id"],
        _launch(running_authorized),
    )
    assert running["execution"]["terminal"] is False
    assert _status(running, "generation") == "running"
    assert _status(running, "scoring") == "not_applicable"
    stored = running_service.get(running_document["journey_id"])
    assert stored is not None
    assert stored["execution"]["status"] == "running"
    assert stored["execution"]["successful_candidate_generation"] is False
    reopened.close()
    reopened_plans.close()


def test_a_child_decision_does_not_replace_the_parent_round_evidence(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True})
    plans, service, document = _ready_journey(tmp_path, launcher)
    journey_id = document["journey_id"]
    saved = service._store.get(journey_id)
    assert isinstance(saved, dict)
    saved["plan_id"] = "plan_child"
    saved["plan_revision"] = 2
    saved["approval"] = {
        "plan_id": "plan_child",
        "revision": 2,
        "digest": "child-digest",
        "scope": "recorded_plan",
    }
    saved["execution_authorization"] = {
        "plan_id": "plan_child",
        "revision": 2,
        "digest": "child-digest",
        "execution_authorized": True,
    }
    parent_execution = {
        "attempt_id": "attempt.parent",
        "plan_id": "plan_parent",
        "revision": 4,
        "digest": "parent-digest",
        "consumed": True,
        "terminal": True,
        "status": "verified",
        "gpu_index": 0,
    }
    child_execution = {
        "attempt_id": "attempt.child",
        "plan_id": "plan_child",
        "revision": 2,
        "digest": "child-digest",
        "consumed": True,
        "terminal": True,
        "status": "verified",
        "gpu_index": 0,
    }
    saved["execution"] = child_execution
    saved["rounds"] = [
        {
            "round_id": "round_parent",
            "ordinal": 1,
            "plan_id": "plan_parent",
            "plan_revision": 4,
            "digest": "parent-digest",
            "approval": None,
            "execution_authorization": None,
            "execution": parent_execution,
        },
        {
            "round_id": "round_child",
            "ordinal": 2,
            "plan_id": "plan_child",
            "plan_revision": 2,
            "digest": "child-digest",
            "approval": saved["approval"],
            "execution_authorization": saved["execution_authorization"],
            "execution": child_execution,
        },
    ]
    saved["events"] = [
        {"kind": "approval_recorded", "digest": "parent-digest"},
        {"kind": "execution_authorized", "digest": "parent-digest"},
        {"kind": "approval_recorded", "digest": "child-digest"},
        {"kind": "execution_authorized", "digest": "child-digest"},
    ]
    service._save(saved)
    before = json.dumps(service._store.get(journey_id), sort_keys=True)
    presented = service.get(journey_id)
    after = json.dumps(service._store.get(journey_id), sort_keys=True)
    assert before == after
    assert presented is not None
    parent = presented["rounds"][0]
    child = presented["rounds"][1]
    assert parent["recorded_review_status"] == "recorded_plan"
    assert parent["recorded_review_evidence"] == "journey_event"
    assert parent["execution_authorization_status"] == "authorized"
    assert parent["execution_authorization_evidence"] == "journey_event"
    assert parent["approval"] is None
    assert parent["plan_id"] == "plan_parent"
    assert parent["plan_revision"] == 4
    assert parent["digest"] == "parent-digest"
    assert parent["attempt_id"] == "attempt.parent"
    assert child["recorded_review_status"] == "recorded_plan"
    assert child["approval"]["digest"] == "child-digest"
    assert child["plan_id"] == "plan_child"
    assert child["attempt_id"] == "attempt.child"
    assert parent["approved_gpu"] is None
    assert child["approved_gpu"] is None
    plans.close()
    service.close()


def test_approved_gpu_comes_from_the_matching_plan_revision(tmp_path: Path) -> None:
    plans, service, document = _ready_journey(tmp_path, _Outcome({"status": "verified"}), gpu="0")
    status, approved = service.record_approval(document["journey_id"], _approval(document, _digest(document)))
    assert status == 200
    digest = approved["approval"]["digest"]
    plan_id = approved["plan_id"]
    revision = approved["plan_revision"]
    saved = service._store.get(document["journey_id"])
    assert isinstance(saved, dict)
    round_id = "round_live"
    saved["rounds"] = [
        {
            "round_id": round_id,
            "ordinal": 11,
            "plan_id": plan_id,
            "plan_revision": revision,
            "digest": digest,
            "approval": None,
            "approved_gpu": None,
            "parameters": [
                {
                    "name": "gpu",
                    "value": "0",
                    "source": "previous_round",
                    "provenance": "preserved",
                }
            ],
            "execution": {
                "attempt_id": "attempt.live",
                "gpu_index": 0,
                "plan_id": plan_id,
                "revision": revision,
                "digest": digest,
                "consumed": True,
                "terminal": True,
                "status": "verified",
            },
        }
    ]
    saved["active_round_id"] = round_id
    service._store.put(saved)
    before = json.dumps(service._store.get(document["journey_id"]), sort_keys=True)
    presented = service.get(document["journey_id"])
    after = json.dumps(service._store.get(document["journey_id"]), sort_keys=True)
    assert before == after
    assert presented is not None
    card = presented["rounds"][0]
    assert card["approved_gpu"] == "0"
    assert card["approval"]["plan_id"] == plan_id
    assert card["approval"]["revision"] == revision
    assert card["approval"]["digest"] == digest
    assert card["gpu_proposal_source"] == "previous_round"
    assert card["launch_gpu_index"] == 0
    assert service._store.get(document["journey_id"])["rounds"][0]["approved_gpu"] is None
    plans.close()
    service.close()


def test_a_different_plan_identity_does_not_supply_the_approved_gpu(tmp_path: Path) -> None:
    plans, service, document = _ready_journey(tmp_path, _Outcome({"status": "verified"}), gpu="0")
    status, approved = service.record_approval(document["journey_id"], _approval(document, _digest(document)))
    assert status == 200
    other = "ab" * 32
    saved = service._store.get(document["journey_id"])
    assert isinstance(saved, dict)
    saved["approval"]["digest"] = other
    saved["rounds"] = [
        {
            "round_id": "round_divergent",
            "ordinal": 11,
            "plan_id": approved["plan_id"],
            "plan_revision": approved["plan_revision"],
            "digest": other,
            "approval": None,
            "approved_gpu": None,
            "parameters": [
                {"name": "gpu", "value": "0", "source": "previous_round", "provenance": "preserved"}
            ],
            "execution": {
                "attempt_id": "attempt.divergent",
                "gpu_index": 0,
                "plan_id": approved["plan_id"],
                "revision": approved["plan_revision"],
                "digest": other,
                "consumed": True,
                "terminal": True,
                "status": "verified",
            },
        }
    ]
    saved["active_round_id"] = "round_divergent"
    service._store.put(saved)
    before = json.dumps(service._store.get(document["journey_id"]), sort_keys=True)
    presented = service.get(document["journey_id"])
    after = json.dumps(service._store.get(document["journey_id"]), sort_keys=True)
    assert before == after
    assert presented is not None
    card = presented["rounds"][0]
    assert card["approved_gpu"] is None
    assert card["launch_gpu_index"] == 0
    assert card["gpu_proposal_source"] == "previous_round"
    plans.close()
    service.close()


def test_a_journey_event_does_not_invent_an_approved_gpu(tmp_path: Path) -> None:
    plans, service, document = _ready_journey(tmp_path, _Outcome({"status": "verified"}), gpu="0")
    saved = service._store.get(document["journey_id"])
    assert isinstance(saved, dict)
    saved.pop("approval", None)
    saved["events"] = [{"kind": "approval_recorded", "digest": "parent-digest"}]
    saved["rounds"] = [
        {
            "round_id": "round_parent",
            "ordinal": 10,
            "plan_id": "plan_parent",
            "plan_revision": 4,
            "digest": "parent-digest",
            "approval": None,
            "approved_gpu": None,
            "parameters": [
                {"name": "gpu", "value": "0", "source": "previous_round", "provenance": "preserved"}
            ],
            "execution": {
                "attempt_id": "attempt.parent",
                "gpu_index": 0,
                "consumed": True,
                "terminal": True,
                "status": "verified",
            },
        }
    ]
    saved["active_round_id"] = "round_parent"
    service._store.put(saved)
    before = json.dumps(service._store.get(document["journey_id"]), sort_keys=True)
    presented = service.get(document["journey_id"])
    after = json.dumps(service._store.get(document["journey_id"]), sort_keys=True)
    assert before == after
    assert presented is not None
    assert presented["rounds"][0]["recorded_review_status"] == "recorded_plan"
    assert presented["rounds"][0]["approved_gpu"] is None
    plans.close()
    service.close()


def test_saved_specialist_timestamps_keep_their_overlap(tmp_path: Path) -> None:
    plans, service, document = _ready_journey(tmp_path, _Outcome({"status": "verified"}), gpu="0")
    stages = [
        {
            "stage_id": "generation",
            "started_at": "2026-10-25T16:54:42.359488+00:00",
            "completed_at": "2026-10-25T16:54:42.376214+00:00",
            "status": "completed",
        },
        {
            "stage_id": "scoring",
            "started_at": "2026-10-25T16:54:42.289138+00:00",
            "completed_at": "2026-10-25T16:54:42.359529+00:00",
            "status": "completed",
        },
        {
            "stage_id": "ranking",
            "started_at": "2026-10-25T16:54:42.359552+00:00",
            "completed_at": "2026-10-25T16:54:42.359576+00:00",
            "status": "completed",
        },
        {
            "stage_id": "critique",
            "started_at": "2026-10-25T16:54:42.359602+00:00",
            "completed_at": "2026-10-25T16:54:42.359628+00:00",
            "status": "completed",
        },
        {
            "stage_id": "prioritization",
            "started_at": "2026-10-25T16:54:42.359651+00:00",
            "completed_at": "2026-10-25T16:54:42.359691+00:00",
            "status": "completed",
        },
    ]
    saved = service._store.get(document["journey_id"])
    assert isinstance(saved, dict)
    saved["rounds"] = [
        {
            "round_id": "round_historical",
            "ordinal": 11,
            "plan_id": saved.get("plan_id"),
            "plan_revision": saved.get("plan_revision"),
            "digest": "historical-digest",
            "specialist_stages": stages,
        }
    ]
    saved["active_round_id"] = "round_historical"
    service._store.put(saved)
    before = json.dumps(service._store.get(document["journey_id"])["rounds"][0]["specialist_stages"])
    presented = service.get(document["journey_id"])
    after = json.dumps(service._store.get(document["journey_id"])["rounds"][0]["specialist_stages"])
    assert before == after
    assert presented is not None
    card = presented["rounds"][0]
    assert card["specialist_stages"][0]["started_at"] == "2026-10-25T16:54:42.359488+00:00"
    assert card["specialist_stages"][1]["started_at"] == "2026-10-25T16:54:42.289138+00:00"
    assert "Scoring starts before the recorded normalization start." in card["specialist_timestamp_observation"]
    assert "These saved timestamps were not rewritten." in card["specialist_timestamp_observation"]
    plans.close()
    service.close()


def test_specialist_intervals_follow_the_real_calls(monkeypatch: Any) -> None:
    from cellertz.application.journey_specialist_progress import apply_specialist_progress
    from cellertz.domains.protein_design.agents.generation import NormalizedCandidate

    order: list[str] = []
    ticks = {"value": 0}

    def clock() -> str:
        ticks["value"] += 1
        return f"2026-10-25T17:00:00.{ticks['value']:06d}+00:00"

    def generation(_execution: object) -> dict[str, object]:
        order.append("generation")
        return {
            "candidate": NormalizedCandidate(
                candidate_id="candidate.1",
                sequence="AAAA",
                target_id="PDL1",
                generator="bindcraft",
                generator_metadata={"design": "design.1"},
            ),
            "error": None,
            "invoked": True,
        }

    scorer = _IpSAEScore(_ipsae_result("candidate.1"))

    def scoring(execution: object) -> ProteinScoreResultV2:
        order.append("scoring")
        return scorer(execution)

    monkeypatch.setattr("cellertz.application.journey_specialist_progress._now", clock)
    monkeypatch.setattr(
        "cellertz.application.journey_specialist_progress._saved_generation",
        generation,
    )
    monkeypatch.setattr(
        "cellertz.application.journey_specialist_progress._evidence_block",
        lambda _execution: (None, []),
    )
    identity = {"plan_id": "plan_ordered", "revision": 2, "digest": "cd" * 32}
    execution = {
        "attempt_id": "attempt.ordered",
        "terminal": True,
        "accepted_candidate": True,
        "status": "verified",
        "candidate_id": "candidate.1",
        "candidate_identity": {
            "candidate_id": "candidate.1",
            "design": "design.1",
            "execution_id": "attempt.ordered",
            "sequence": "AAAA",
            "target_id": "PDL1",
        },
        **identity,
    }
    document = {
        "journey_id": "journey_ordered",
        "active_round_id": "round_ordered",
        "stages": [],
        "events": [],
        "execution": execution,
        "rounds": [
            {
                "round_id": "round_ordered",
                "plan_id": identity["plan_id"],
                "plan_revision": identity["revision"],
                "digest": identity["digest"],
                "specialist_stages": [],
                "specialist_events": [],
            }
        ],
    }
    assert apply_specialist_progress(document, "round_ordered", scorer=scoring, catalog_root=None) is True
    stages = {item["stage_id"]: item for item in document["rounds"][0]["specialist_stages"]}
    assert order == ["generation", "scoring"]
    assert stages["generation"]["completed_at"] <= stages["scoring"]["started_at"]
    assert stages["scoring"]["started_at"] < stages["scoring"]["completed_at"]
    assert stages["scoring"]["completed_at"] <= stages["ranking"]["started_at"]
    assert stages["ranking"]["completed_at"] <= stages["critique"]["started_at"]
    assert stages["critique"]["completed_at"] <= stages["prioritization"]["started_at"]
    assert stages["prioritization"]["started_at"] <= stages["prioritization"]["completed_at"]
    from cellertz.application.design_journey import _specialist_timestamp_observation

    assert _specialist_timestamp_observation(document["rounds"][0]["specialist_stages"]) is None


def test_next_round_keeps_the_exhausted_attempt_and_does_not_launch(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True})
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    plans, service, document = _ready_journey(
        tmp_path,
        launcher,
        plan_store=SQLiteGenerationPlanStore(plan_path),
        journey_store=SQLiteDesignJourneyStore(journey_path),
        gpu="1",
    )
    status, approved = service.record_approval(
        document["journey_id"], _approval(document, _digest(document))
    )
    assert status == 200
    attempt_id = "attempt.0fb068b6"
    attempt_directory = tmp_path / "attempt.0fb068b6"
    _write_observed_attempt(attempt_directory, attempt_id)
    saved = service._store.get(document["journey_id"])
    assert isinstance(saved, dict)
    saved["execution"] = _running_execution(approved, approved, attempt_id, attempt_directory)
    service._store.put(saved)
    reconciled_status, reconciled = service.reconcile_execution(document["journey_id"])
    assert reconciled_status == 200
    assert launcher.calls == 0
    stored = service._store.get(document["journey_id"])
    assert isinstance(stored, dict)
    assert "rounds" not in stored
    parent = reconciled["rounds"][0]
    assert parent["attempt_id"] == attempt_id
    assert parent["outcome_label"] == "Budget exhausted without an accepted candidate."
    assert parent["budget_usage"] == {
        "attempted_trajectories": 1,
        "low_confidence_trajectories": 1,
        "relaxed_trajectories": 0,
    }
    assert parent["candidate_count"] == 0
    assert parent["execution_completed"] is True
    assert parent["successful_candidate_generation"] is False
    assert parent["budget_compliance_established"] is True
    assert parent["scientific_validation_established"] is False
    assert parent["approved_gpu"] == "1"
    assert parent["launch_gpu_index"] == 0
    assert parent["recorded_review_status"] == "recorded_plan"
    assert parent["execution_authorization_status"] == "not_authorized"
    stored_execution = service._store.get(document["journey_id"])
    assert isinstance(stored_execution, dict)
    stored_execution["execution"].pop("budget_usage", None)
    service._store.put(stored_execution)
    presented = service.get(document["journey_id"])
    assert presented is not None
    assert presented["rounds"][0]["budget_usage"] == parent["budget_usage"]
    reread = service._store.get(document["journey_id"])
    assert isinstance(reread, dict)
    assert "budget_usage" not in reread["execution"]
    assert "rounds" not in reread
    parent = presented["rounds"][0]
    parent_plan = parent["plan_id"]
    parent_revision = parent["plan_revision"]

    route = f"/api/design-journeys/{document['journey_id']}/rounds"
    body = json.dumps({"parent_round_id": parent["round_id"]}).encode()
    prepared_status, prepared = handle_design_journey(service, "POST", route, body)
    assert prepared_status == 200
    assert launcher.calls == 0
    rounds = prepared["rounds"]
    assert len(rounds) == 2
    assert rounds[0]["plan_id"] == parent_plan
    assert rounds[0]["plan_revision"] == parent_revision
    assert rounds[0]["attempt_id"] == attempt_id
    assert rounds[0]["execution"]["status"] == "budget_exhausted"
    assert rounds[0]["recorded_review_status"] == "recorded_plan"
    assert rounds[0]["execution_authorization_status"] == "not_authorized"
    child = rounds[1]
    assert child["plan_id"] != parent_plan
    assert child["parent_round_id"] == parent["round_id"]
    assert rounds[0]["child_round_id"] == child["round_id"]
    assert child["approval"] is None
    assert child["execution"] is None
    assert child["execution_authorization"] is None
    assert child["recorded_review_status"] == "not_recorded"
    assert child["execution_authorization_status"] == "not_authorized"
    assert child["outcome_label"] == "No launch recorded."
    assert child["gpu_proposal"] == "1"
    assert child["profile_gpu"] is None
    assert child["profile_accepts_gpu_proposal"] is True
    assert child["sequence_limits"] == parent["sequence_limits"]
    assert "does not assign a host GPU" in child["profile_restriction"]
    assert "not accepted" not in child["profile_restriction"]
    copied = {item["name"]: item for item in child["parameters"]}
    recorded = {item["name"]: item for item in rounds[0]["parameters"]}
    assert copied["gpu"]["value"] == "1"
    assert copied["gpu"]["source"] == "previous_round"
    assert copied["gpu"]["provenance"] == "preserved"
    for name, item in recorded.items():
        assert copied[name]["value"] == item["value"]
    assert plans.published(parent_plan)["revision"] == parent_revision
    assert plans.published(parent_plan)["fields"]["gpu"] == "1"

    duplicate_status, duplicate = handle_design_journey(service, "POST", route, body)
    assert duplicate_status == 200
    assert [item["round_id"] for item in duplicate["rounds"]] == [
        item["round_id"] for item in rounds
    ]
    assert duplicate["rounds"][1]["plan_id"] == child["plan_id"]
    assert plans.published(child["plan_id"])["revision"] == child["plan_revision"]
    assert launcher.calls == 0

    text = (
        "Generate another PDL1 binder. "
        "technical-smoke-test no_filters. This filter policy is for the technical "
        "demonstration and does not establish scientific quality."
    )
    corrected = service.open_or_get(
        "request_ready",
        1,
        text,
        [{"name": "gpu", "value": "0", "provenance": "user", "source": "professor"}],
    )
    assert corrected["rounds"][0]["plan_revision"] == parent_revision
    assert corrected["rounds"][0]["attempt_id"] == attempt_id
    assert corrected["rounds"][0]["approved_gpu"] == "1"
    assert corrected["rounds"][0]["launch_gpu_index"] == 0
    assert corrected["rounds"][1]["gpu_proposal"] == "0"
    assert corrected["rounds"][1]["profile_accepts_gpu_proposal"] is True
    assert corrected["rounds"][1]["profile_gpu"] is None
    assert "does not assign a host GPU" in corrected["rounds"][1]["profile_restriction"]
    assert plans.published(parent_plan)["fields"]["gpu"] == "1"
    assert plans.published(corrected["plan_id"])["fields"]["gpu"] == "0"
    assert (
        plans.published(corrected["plan_id"])["fields"].get("sequence_limits")
        == plans.published(parent_plan)["fields"].get("sequence_limits")
    )
    assert launcher.calls == 0

    service.close()
    plans.close()
    reopened_plans = GenerationPlanService(
        None,
        inputs=_input_catalog(tmp_path / "inputs"),
        demonstration=launcher,
        store=SQLiteGenerationPlanStore(plan_path),
    )
    reopened = DesignJourneyService(
        reopened_plans,
        store=SQLiteDesignJourneyStore(journey_path),
        attempt_root=tmp_path / "attempts",
    )
    restored = reopened.get(document["journey_id"])
    assert restored is not None
    assert [item["round_id"] for item in restored["rounds"]] == [
        item["round_id"] for item in rounds
    ]
    assert restored["rounds"][0]["attempt_id"] == attempt_id
    assert restored["rounds"][0]["outcome_label"] == parent["outcome_label"]
    assert restored["rounds"][1]["gpu_proposal"] == "0"
    assert launcher.calls == 0
    reopened.close()
    reopened_plans.close()


def test_cancelling_a_prepared_round_keeps_history_and_allows_one_replacement(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True})
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    plans, service, document = _ready_journey(
        tmp_path,
        launcher,
        plan_store=SQLiteGenerationPlanStore(plan_path),
        journey_store=SQLiteDesignJourneyStore(journey_path),
        gpu="1",
    )
    status, approved = service.record_approval(
        document["journey_id"], _approval(document, _digest(document))
    )
    assert status == 200
    attempt_id = "attempt.0fb068b6"
    attempt_directory = tmp_path / "attempt.0fb068b6"
    _write_observed_attempt(attempt_directory, attempt_id)
    saved = service._store.get(document["journey_id"])
    assert isinstance(saved, dict)
    saved["execution"] = _running_execution(approved, approved, attempt_id, attempt_directory)
    service._store.put(saved)
    reconciled_status, reconciled = service.reconcile_execution(document["journey_id"])
    assert reconciled_status == 200
    parent_round = reconciled["rounds"][0]
    prepared_status, prepared = handle_design_journey(
        service,
        "POST",
        f"/api/design-journeys/{document['journey_id']}/rounds",
        json.dumps({"parent_round_id": parent_round["round_id"]}).encode(),
    )
    assert prepared_status == 200
    parent = prepared["rounds"][0]
    child = prepared["rounds"][1]
    parent_history = {
        "plan_id": parent["plan_id"],
        "plan_revision": parent["plan_revision"],
        "digest": parent["digest"],
        "attempt_id": parent["attempt_id"],
        "execution": parent["execution"],
        "approval": parent["approval"],
        "parameters": parent["parameters"],
    }
    route = (
        f"/api/design-journeys/{document['journey_id']}/rounds/{child['round_id']}/cancellation"
    )
    cancelled_status, cancelled = handle_design_journey(service, "POST", route, b"{}")
    assert cancelled_status == 200
    assert launcher.calls == 0
    kept = cancelled["rounds"][0]
    stopped = cancelled["rounds"][1]
    assert [item["plan_id"] for item in cancelled["rounds"]] == [parent["plan_id"], child["plan_id"]]
    assert stopped["status"] == "cancelled"
    assert isinstance(stopped["cancelled_at"], str)
    assert stopped["parent_round_id"] == parent["round_id"]
    assert stopped["plan_id"] == child["plan_id"]
    assert stopped["plan_revision"] == child["plan_revision"]
    assert kept["plan_id"] == parent_history["plan_id"]
    assert kept["execution"] == parent_history["execution"]
    assert kept["approval"] == parent_history["approval"]
    assert kept["parameters"] == parent_history["parameters"]
    assert kept["child_round_id"] == child["round_id"]
    repeated_status, repeated = handle_design_journey(service, "POST", route, b"{}")
    assert repeated_status == 200
    assert repeated["rounds"][1]["cancelled_at"] == stopped["cancelled_at"]
    stored = service._store.get(document["journey_id"])
    assert isinstance(stored, dict)
    assert sum(item.get("kind") == "round_cancelled" for item in stored["events"]) == 1

    refused_status, refused = handle_design_journey(
        service,
        "POST",
        f"/api/design-journeys/{document['journey_id']}/rounds/{parent['round_id']}/cancellation",
        b"{}",
    )
    assert refused_status == 409
    assert refused["status"] == "round_launch_consumed"
    assert "does not stop" in refused["detail"]
    assert service.get(document["journey_id"])["rounds"][0]["status"] != "cancelled"
    assert service.get(document["journey_id"])["rounds"][1]["cancelled_at"] == stopped["cancelled_at"]

    blocked, approval_body = service.record_approval(
        document["journey_id"], _approval(cancelled, _digest(cancelled))
    )
    assert blocked == 409
    assert approval_body["detail"] == (
        "This round is cancelled. It cannot be approved, authorized, or launched."
    )
    assert plans.published(child["plan_id"]).get("approved") is not True
    blocked, authorization_body = service.authorize_execution(
        document["journey_id"], _authorization(cancelled)
    )
    assert blocked == 409
    assert authorization_body["status"] == "round_cancelled"
    blocked, launch_body = service.launch_execution(document["journey_id"], _launch(cancelled))
    assert blocked == 409
    assert launch_body["status"] == "round_cancelled"
    assert launcher.calls == 0

    replacement_route = f"/api/design-journeys/{document['journey_id']}/rounds"
    replacement_body = json.dumps({"parent_round_id": parent["round_id"]}).encode()
    replacement_status, replacement = handle_design_journey(
        service, "POST", replacement_route, replacement_body
    )
    assert replacement_status == 200
    assert len(replacement["rounds"]) == 3
    assert replacement["rounds"][1]["status"] == "cancelled"
    assert replacement["rounds"][1]["cancelled_at"] == stopped["cancelled_at"]
    assert replacement["rounds"][1]["plan_id"] == child["plan_id"]
    opened = replacement["rounds"][2]
    assert opened["status"] == "open"
    assert opened["parent_round_id"] == parent["round_id"]
    assert opened["plan_id"] != child["plan_id"]
    assert opened["execution"] is None
    assert opened["approval"] is None
    assert replacement["rounds"][0]["execution"] == parent_history["execution"]
    assert replacement["rounds"][0]["approval"] == parent_history["approval"]
    assert replacement["rounds"][0]["plan_revision"] == parent_history["plan_revision"]
    assert child["round_id"] in replacement["rounds"][0]["child_round_ids"]
    assert opened["round_id"] in replacement["rounds"][0]["child_round_ids"]
    duplicate_status, duplicate = handle_design_journey(
        service, "POST", replacement_route, replacement_body
    )
    assert duplicate_status == 200
    assert [item["round_id"] for item in duplicate["rounds"]] == [
        item["round_id"] for item in replacement["rounds"]
    ]
    assert duplicate["rounds"][2]["plan_id"] == opened["plan_id"]
    assert plans.published(opened["plan_id"])["revision"] == opened["plan_revision"]
    assert launcher.calls == 0

    service.close()
    plans.close()
    reopened_plans = GenerationPlanService(
        None,
        inputs=_input_catalog(tmp_path / "inputs"),
        demonstration=launcher,
        store=SQLiteGenerationPlanStore(plan_path),
    )
    reopened = DesignJourneyService(
        reopened_plans,
        store=SQLiteDesignJourneyStore(journey_path),
        attempt_root=tmp_path / "attempts",
    )
    restored = reopened.get(document["journey_id"])
    assert restored is not None
    assert [item["round_id"] for item in restored["rounds"]] == [
        parent["round_id"],
        child["round_id"],
        opened["round_id"],
    ]
    assert restored["rounds"][1]["status"] == "cancelled"
    assert restored["rounds"][1]["cancelled_at"] == stopped["cancelled_at"]
    assert restored["rounds"][0]["attempt_id"] == attempt_id
    assert restored["rounds"][0]["execution"]["status"] == "budget_exhausted"
    assert launcher.calls == 0
    reopened.close()
    reopened_plans.close()


def _input_catalog(root: Path) -> RegisteredInputCatalog:
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "target_structure": b"TARGET\n",
        "settings": b'{"chain": "A"}\n',
        "advanced_profile": b'{"k": 1}\n',
    }
    files = {}
    for role, payload in payloads.items():
        path = root / role
        if not path.exists():
            path.write_bytes(payload)
        files[role] = path
    return RegisteredInputCatalog(files)


def _write_observed_attempt(attempt_directory: Path, attempt_id: str) -> None:
    designs = attempt_directory / attempt_id / "designs"
    rejected = designs / "Trajectory" / "LowConfidence"
    rejected.mkdir(parents=True)
    (rejected / "rejected.pdb").write_bytes(b"REJECTED TRAJECTORY\n")
    (attempt_directory / "progress.json").write_text(
        json.dumps({"status": "rejected", "reasons": ["final_design_row_missing"]}) + "\n",
        encoding="utf-8",
    )
    ledger = {
        "adapter_version": "bindcraft_attempted_trajectory_budget_v2",
        "requested_attempted_trajectories": 1,
        "observed_attempted_trajectories": 1,
        "observed_low_confidence_trajectories": 1,
        "observed_relaxed_trajectories": 0,
        "compliance_established": True,
        "termination_reason": "attempted_trajectory_budget_exhausted",
        "reservations": [{"ordinal": 1, "state": "finished", "scheduler_reserved": True}],
    }
    (designs / "cellertz_trajectory_budget.json").write_text(
        json.dumps(ledger) + "\n", encoding="utf-8"
    )
    for name in ("final_design_stats.csv", "mpnn_design_stats.csv", "trajectory_stats.csv"):
        (designs / name).write_text("Design,Sequence\n", encoding="utf-8")
    (designs / "failure_csv.csv").write_text(
        "Trajectory_one-hot_pLDDT,Trajectory_final_pLDDT\n1,1\n",
        encoding="utf-8",
    )


def _file_bytes(root: Path) -> dict[str, str]:
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _running_execution(
    document: dict[str, object],
    authorized: dict[str, object],
    attempt_id: str,
    attempt_directory: Path,
) -> dict[str, object]:
    review = authorized["execution_review"]
    assert isinstance(review, dict)
    return {
        "launch_id": "launch_saved",
        "idempotency_key": "launch-once",
        "plan_id": review["plan_id"],
        "revision": review["revision"],
        "digest": review["digest"],
        "attempt_id": attempt_id,
        "attempt_directory": str(attempt_directory),
        "gpu_index": 0,
        "status": "running",
        "launched": False,
        "consumed": True,
        "terminal": False,
        "progress": [{"status": "running"}],
        "accepted_candidate": None,
        "successful_candidate_generation": False,
        "candidate_id": None,
        "termination_reason": None,
        "scientific_validation_established": False,
        "result_link": {
            "journey_id": document["journey_id"],
            "attempt_id": attempt_id,
            "candidate_id": None,
        },
    }


class _EvidenceThenError:
    def __init__(self) -> None:
        self.calls = 0

    def launch(self, request: Any) -> dict[str, object]:
        self.calls += 1
        _write_observed_attempt(Path(request.attempt_directory), str(request.attempt_id))
        raise RuntimeError("reply was not saved")


class _Raises:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def launch(self, request: object) -> dict[str, object]:
        self.calls += 1
        raise self.error


class _Inventory:
    def __init__(self, indexes: list[int]) -> None:
        self.indexes = set(indexes)
        self.calls = 0
        self.inert_outputs = False
        self.requests: list[object] = []

    def gpu_readiness(self, gpu_index: int) -> dict[str, object]:
        if gpu_index not in self.indexes:
            return {"status": "selected_gpu_missing", "launch_ready": False, "gpu_index": gpu_index}
        return {"status": "launch_ready", "launch_ready": True, "gpu_index": gpu_index}

    def launch(self, request: object) -> dict[str, object]:
        self.calls += 1
        self.requests.append(request)
        return {"status": "verified", "launched": True}


def test_budget_exhaustion_records_blocked_specialists_without_launching_again(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The saved no-candidate outcome matches Round 8: specialists stay uninvoked."""

    launcher = _Outcome(
        {
            "status": "budget_exhausted",
            "launched": True,
            "accepted_candidate": False,
            "successful_candidate_generation": False,
            "termination_reason": "attempted_trajectory_budget_exhausted",
            "budget_compliance_established": True,
            "budget_usage": {
                "attempted_trajectories": 1,
                "low_confidence_trajectories": 1,
                "relaxed_trajectories": 0,
            },
            "progress": [{"status": "budget_exhausted"}],
        }
    )
    scorer = _IpSAEScore(_ipsae_result("candidate.1"))
    catalog = tmp_path / "results"
    plans, service, document = _ready_journey(
        tmp_path, launcher, specialist_scorer=scorer, results_catalog=catalog
    )

    def _refuse_generation(execution: dict) -> dict:
        del execution
        raise AssertionError("generation specialist invoked")

    monkeypatch.setattr(
        "cellertz.application.journey_specialist_progress._saved_generation",
        _refuse_generation,
    )
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    round_id = launched["rounds"][0]["round_id"]
    status, recorded = service.record_specialist_progress(document["journey_id"], round_id)
    assert status == 200, recorded
    assert launcher.calls == 1
    assert scorer.calls == 0
    assert recorded["specialist_agents_executed"] is False
    assert recorded["hermes"]["status"] == "not_applicable"
    generation = _stage(recorded, "generation")
    assert generation["status"] == "completed"
    assert generation["agent_executed"] is False
    assert generation["implementation"] == "GenerationPlanService.launch_apptainer_demonstration"
    assert generation["output"]["protein_generation_agent_executed"] is False
    for name in ("scoring", "ranking", "critique", "prioritization"):
        stage = _stage(recorded, name)
        assert stage["status"] == "blocked"
        assert stage["agent_executed"] is False
        assert "No accepted candidate" in stage["blocking_reason"]
        assert "were not invoked" in stage["blocking_reason"]
    card = next(item for item in recorded["rounds"] if item["round_id"] == round_id)
    assert card["specialist_progress_available"] is False
    assert any(item["status"] == "blocked" and item["stage_id"] == "scoring" for item in card["specialist_events"])
    assert "attempt.c5c1f13e" not in json.dumps(recorded)
    assert card["results_publication"] is None
    assert list(catalog.rglob("review.json")) == []
    read_status, reread = handle_design_journey(
        service, "GET", f"/api/design-journeys/{document['journey_id']}", b""
    )
    assert read_status == 200
    assert launcher.calls == 1
    assert scorer.calls == 0
    assert _status(reread, "scoring") == "blocked"
    again_status, again = service.record_specialist_progress(document["journey_id"], round_id)
    assert again_status == 200
    assert scorer.calls == 0
    assert len(again["specialist_events"]) == len(recorded["specialist_events"])
    plans.close()
    service.close()


_PREPARED_DIGEST = "0c13573d8ef08391cddf7c62ea60031ef2c30d2f52ef29f64355c0860ae2f0d7"
_EXECUTED_DIGEST = "c9b4a395b93fa1ea2ac59616bbdeba70b362d29b20a067e93c9059626b90025a"
_REQUEST_TEXT = (
    "Generate another PDL1 binder. "
    "technical-smoke-test no_filters. This filter policy is for the technical "
    "demonstration and does not establish scientific quality."
)


def test_child_round_revised_before_launch_records_the_executed_plan(tmp_path: Path) -> None:
    """A revision made before authorization is the identity specialists record."""

    launcher = _Outcome(
        {
            "status": "budget_exhausted",
            "launched": True,
            "accepted_candidate": False,
            "successful_candidate_generation": False,
            "termination_reason": "attempted_trajectory_budget_exhausted",
            "budget_compliance_established": True,
            "budget_usage": {
                "attempted_trajectories": 1,
                "low_confidence_trajectories": 1,
                "relaxed_trajectories": 0,
            },
            "progress": [{"status": "budget_exhausted"}],
        }
    )
    scorer = _IpSAEScore(_ipsae_result("candidate.1"))
    plans, service, document = _ready_journey(tmp_path, launcher, specialist_scorer=scorer)
    prepared_status, prepared = service.prepare_next_round(document["journey_id"])
    assert prepared_status == 200, prepared
    parent = next(item for item in prepared["rounds"] if not item.get("parent_round_id"))
    child = next(item for item in prepared["rounds"] if item.get("parent_round_id") == parent["round_id"])
    assert child["plan_revision"] == 1
    prepared_digest = child["digest"]
    parent_revision = parent["plan_revision"]
    parent_digest = parent["digest"]
    revised = service.open_or_get(
        "request_ready",
        1,
        _REQUEST_TEXT,
        [{"name": "seed", "value": 2, "provenance": "user", "source": "professor"}],
    )
    assert revised["plan_revision"] == 2
    assert revised.get("execution_authorization") is None
    child_revised = next(item for item in revised["rounds"] if item["round_id"] == child["round_id"])
    parent_revised = next(item for item in revised["rounds"] if item["round_id"] == parent["round_id"])
    assert child_revised["plan_revision"] == 2
    assert child_revised["digest"] != prepared_digest
    assert parent_revised["plan_revision"] == parent_revision
    assert parent_revised["digest"] == parent_digest
    authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(revised)
    )
    assert authorized_status == 200, authorized
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200, launched
    execution = launched["execution"]
    assert execution["revision"] == 2
    assert execution["digest"] == child_revised["digest"]
    assert execution["digest"] != prepared_digest
    child_launched = next(item for item in launched["rounds"] if item["round_id"] == child["round_id"])
    assert child_launched["digest"] == execution["digest"]
    status, recorded = service.record_specialist_progress(document["journey_id"], child["round_id"])
    assert status == 200, recorded
    assert launcher.calls == 1
    assert scorer.calls == 0
    reference = _stage(recorded, "generation")["evidence_references"][0]
    assert reference["plan_id"] == execution["plan_id"]
    assert reference["revision"] == 2
    assert reference["digest"] == execution["digest"]
    event = next(item for item in recorded["specialist_events"] if item["stage_id"] == "generation")
    assert event["plan_revision"] == 2
    assert event["digest"] == execution["digest"]
    parent_card = next(item for item in recorded["rounds"] if item["round_id"] == parent["round_id"])
    assert parent_card["plan_revision"] == parent_revision
    assert parent_card["digest"] == parent_digest
    assert parent_card["specialist_events"] == []
    again_status, again = service.record_specialist_progress(document["journey_id"], child["round_id"])
    assert again_status == 200
    assert again["specialist_events"] == recorded["specialist_events"]
    assert scorer.calls == 0
    plans.close()
    service.close()


def test_plan_identity_mismatch_is_rejected_before_a_specialist_runs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    launcher = _Outcome(
        {
            "status": "budget_exhausted",
            "launched": True,
            "accepted_candidate": False,
            "successful_candidate_generation": False,
            "termination_reason": "attempted_trajectory_budget_exhausted",
            "budget_compliance_established": True,
        }
    )
    scorer = _IpSAEScore(_ipsae_result("candidate.1"))
    plans, service, document = _ready_journey(tmp_path, launcher, specialist_scorer=scorer)

    def _refuse_generation(execution: dict) -> dict:
        del execution
        raise AssertionError("generation specialist invoked")

    monkeypatch.setattr(
        "cellertz.application.journey_specialist_progress._saved_generation",
        _refuse_generation,
    )
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200, launched
    round_id = launched["rounds"][0]["round_id"]
    saved = service._store.get(document["journey_id"])
    assert isinstance(saved, dict)
    execution = saved["execution"]
    assert isinstance(execution, dict)
    execution["revision"] = 2
    execution["digest"] = _EXECUTED_DIGEST
    saved["plan_revision"] = 2
    saved["rounds"] = [
        {
            "round_id": round_id,
            "ordinal": 1,
            "plan_id": execution["plan_id"],
            "plan_revision": 1,
            "digest": _PREPARED_DIGEST,
        }
    ]
    saved["active_round_id"] = round_id
    service._store.put(saved)
    status, refused = service.record_specialist_progress(document["journey_id"], round_id)
    assert status == 409
    assert refused["status"] == "plan_identity_mismatch"
    assert scorer.calls == 0
    assert launcher.calls == 1
    stored = service._store.get(document["journey_id"])
    assert isinstance(stored, dict)
    assert stored["rounds"][0]["plan_revision"] == 1
    assert stored["rounds"][0]["digest"] == _PREPARED_DIGEST
    assert stored["rounds"][0].get("specialist_events") is None
    assert stored["execution"]["revision"] == 2
    assert stored["execution"]["digest"] == _EXECUTED_DIGEST
    plans.close()
    service.close()


def test_recorded_specialist_evidence_keeps_the_prepared_plan_identity(tmp_path: Path) -> None:
    launcher = _Outcome(
        {
            "status": "budget_exhausted",
            "launched": True,
            "accepted_candidate": False,
            "successful_candidate_generation": False,
            "termination_reason": "attempted_trajectory_budget_exhausted",
            "budget_compliance_established": True,
        }
    )
    scorer = _IpSAEScore(_ipsae_result("candidate.1"))
    plans, service, document = _ready_journey(tmp_path, launcher, specialist_scorer=scorer)
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200, launched
    round_id = launched["rounds"][0]["round_id"]
    saved = service._store.get(document["journey_id"])
    assert isinstance(saved, dict)
    execution = saved["execution"]
    assert isinstance(execution, dict)
    execution["revision"] = 2
    execution["digest"] = _EXECUTED_DIGEST
    event = {
        "at": "2026-10-25T00:00:00+00:00",
        "kind": "stage_blocked",
        "stage_id": "scoring",
        "status": "blocked",
        "journey_id": document["journey_id"],
        "round_id": round_id,
        "plan_id": execution["plan_id"],
        "plan_revision": 1,
        "digest": _PREPARED_DIGEST,
        "attempt_id": execution["attempt_id"],
        "candidate_id": None,
        "model_id": None,
        "implementation": "BindCraftIpSAEEvidenceProvider",
        "implementation_kind": "structural_scoring",
        "agent_executed": False,
        "reason": "No accepted candidate was saved. Scoring was not invoked.",
    }
    stage = {
        "stage_id": "scoring",
        "status": "blocked",
        "evidence_references": [
            {
                "kind": "journey_round",
                "plan_id": execution["plan_id"],
                "revision": 1,
                "digest": _PREPARED_DIGEST,
                "attempt_id": execution["attempt_id"],
            }
        ],
    }
    saved["plan_revision"] = 2
    saved["rounds"] = [
        {
            "round_id": round_id,
            "ordinal": 1,
            "plan_id": execution["plan_id"],
            "plan_revision": 1,
            "digest": _PREPARED_DIGEST,
            "specialist_events": [event],
            "specialist_stages": [stage],
            "specialist_progress": {
                "attempt_id": execution["attempt_id"],
                "digest": _PREPARED_DIGEST,
                "status": "recorded",
            },
        }
    ]
    saved["active_round_id"] = round_id
    saved["specialist_events"] = [event]
    planted = json.dumps(
        {"rounds": saved["rounds"], "specialist_events": saved["specialist_events"]},
        sort_keys=True,
    )
    service._store.put(saved)
    status, recorded = service.record_specialist_progress(document["journey_id"], round_id)
    assert status == 200, recorded
    assert scorer.calls == 0
    assert launcher.calls == 1
    stored = service._store.get(document["journey_id"])
    assert isinstance(stored, dict)
    assert (
        json.dumps(
            {"rounds": stored["rounds"], "specialist_events": stored["specialist_events"]},
            sort_keys=True,
        )
        == planted
    )
    card = next(item for item in recorded["rounds"] if item["round_id"] == round_id)
    assert card["plan_revision"] == 2
    assert stored["rounds"][0]["digest"] == _PREPARED_DIGEST
    assert card["specialist_events"][0]["plan_revision"] == 1
    assert card["specialist_events"][0]["digest"] == _PREPARED_DIGEST
    assert card["specialist_stages"][0]["evidence_references"][0]["revision"] == 1
    assert card["specialist_stages"][0]["evidence_references"][0]["digest"] == _PREPARED_DIGEST
    assert card["execution"]["revision"] == 2
    assert card["execution"]["digest"] == _EXECUTED_DIGEST
    plans.close()
    service.close()


def test_missing_scoring_artifacts_block_before_the_scorer_runs(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True, "accepted_candidate": True})
    scorer = _IpSAEScore(_ipsae_result("candidate.1"))
    plans, service, document = _ready_journey(tmp_path, launcher, specialist_scorer=scorer)
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    _claim_candidate(service, document["journey_id"], launched["execution"], design="PDL1_l65_s7")
    round_id = launched["rounds"][0]["round_id"]
    status, recorded = service.record_specialist_progress(document["journey_id"], round_id)
    assert status == 200, recorded
    assert scorer.calls == 0
    assert launcher.calls == 1
    generation = _stage(recorded, "generation")
    assert generation["agent_executed"] is False
    assert "ProteinGenerationAgent was not run" in generation["contribution"]
    assert "not called again" in generation["contribution"]
    scoring = _stage(recorded, "scoring")
    assert scoring["status"] == "blocked"
    assert scoring["agent_executed"] is False
    assert _status(recorded, "ranking") == "blocked"
    assert recorded["specialist_agents_executed"] is False
    plans.close()
    service.close()


def test_a_scoring_double_hands_off_to_real_ranking_critique_and_prioritization(tmp_path: Path) -> None:
    launcher = _Outcome(
        {
            "status": "verified",
            "launched": True,
            "accepted_candidate": True,
            "candidate_id": "candidate.1",
            "successful_candidate_generation": True,
        }
    )
    scorer = _IpSAEScore(_ipsae_result("candidate.1"))
    plans, service, document = _ready_journey(tmp_path, launcher, specialist_scorer=scorer)
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    execution = launched["execution"]
    _claim_candidate(service, document["journey_id"], execution, design="PDL1_l65_s7")
    _write_bindcraft_artifacts(Path(execution["attempt_directory"]), "PDL1_l65_s7")
    round_id = launched["rounds"][0]["round_id"]
    status, recorded = service.record_specialist_progress(document["journey_id"], round_id)
    assert status == 200, recorded
    assert scorer.calls == 1
    assert launcher.calls == 1
    generation = _stage(recorded, "generation")
    assert generation["agent_executed"] is True
    assert generation["implementation"] == "ProteinGenerationAgent"
    assert generation["output"]["fixture_generation_used"] is False
    assert generation["output"]["producer"] == "GenerationPlanService.launch_apptainer_demonstration"
    assert "Sequence" in generation["contribution"]
    scoring = _stage(recorded, "scoring")
    assert scoring["status"] == "completed"
    assert scoring["agent_executed"] is True
    assert scoring["output"]["ranking_signal"] == "ipsae"
    assert scoring["output"]["ranking_score"] == 0.42
    assert scoring["output"]["fixture_metrics"] is False
    assert scoring["output"]["score_source"] == "configured_scorer"
    assert scoring["output"]["scientific_validation_established"] is False
    assert _stage(recorded, "ranking")["implementation"] == "ProteinRankingAgentV1"
    assert _stage(recorded, "ranking")["implementation_kind"] == "deterministic"
    assert _stage(recorded, "critique")["agent_executed"] is True
    assert "Hermes was not called" in _stage(recorded, "critique")["contribution"]
    assert _stage(recorded, "prioritization")["agent_executed"] is True
    assert recorded["specialist_agents_executed"] is True
    assert recorded["hermes"]["status"] == "not_applicable"
    read_status, _reread = handle_design_journey(
        service, "GET", f"/api/design-journeys/{document['journey_id']}", b""
    )
    assert read_status == 200
    assert scorer.calls == 1
    assert launcher.calls == 1
    plans.close()
    service.close()


def test_accepted_candidate_publishes_exact_models_into_results(tmp_path: Path) -> None:
    """The 0.42 value comes from a scoring double. It is not a calculated ipSAE."""

    launcher = _Outcome(
        {
            "status": "verified",
            "launched": True,
            "accepted_candidate": True,
            "candidate_id": "candidate.1",
            "successful_candidate_generation": True,
        }
    )
    scorer = _IpSAEScore(_ipsae_result("candidate.1"))
    catalog = tmp_path / "results"
    plans, service, document = _ready_journey(
        tmp_path, launcher, specialist_scorer=scorer, results_catalog=catalog
    )
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    execution = launched["execution"]
    attempt_id = execution["attempt_id"]
    attempt_directory = Path(execution["attempt_directory"])
    design = "PDL1_l65_s7"
    _claim_candidate(service, document["journey_id"], execution, design=design)
    raw_before = _publishable_artifacts(attempt_directory, design)
    round_id = launched["rounds"][0]["round_id"]
    read_status, _before = handle_design_journey(
        service, "GET", f"/api/design-journeys/{document['journey_id']}", b""
    )
    assert read_status == 200
    assert scorer.calls == 0
    assert launcher.calls == 1
    assert list(catalog.rglob("review.json")) == []
    status, recorded = service.record_specialist_progress(document["journey_id"], round_id)
    assert status == 200, recorded
    assert scorer.calls == 1
    assert launcher.calls == 1
    assert _stage(recorded, "generation")["agent_executed"] is True
    assert _stage(recorded, "scoring")["output"]["score_source"] == "configured_scorer"
    assert _stage(recorded, "scoring")["output"]["ranking_score"] == 0.42
    assert _stage(recorded, "scoring")["output"]["scientific_validation_established"] is False
    card = next(item for item in recorded["rounds"] if item["round_id"] == round_id)
    publication = card["results_publication"]
    assert publication["status"] == "verified"
    assert publication["attempt_id"] == attempt_id
    assert publication["candidate"] == design
    assert publication["scientific_validation_established"] is False
    assert publication["raw_artifacts_preserved"] is True
    assert set(publication["models"]) == {"model1", "model2"}
    assert publication["representative"]["rule"] == (
        "Accepted and Ranked copies match this model's SHA-256"
    )
    assert publication["representative"]["scientific_validation"] is False
    assert "unmet" in publication["execution_constraints"]
    review_path = catalog / attempt_id / "review.json"
    review_text = review_path.read_text(encoding="utf-8")
    assert "0.42" not in review_text
    review = json.loads(review_text)
    assert review["experimental_validation"] is False
    assert review["ranking_score_computed"] is False
    assert review["execution"] != "manual"
    assert _tree_hashes(attempt_directory / "designs") == raw_before
    catalog_bytes = {
        path.relative_to(catalog).as_posix(): path.read_bytes()
        for path in sorted(catalog.rglob("*"))
        if path.is_file()
    }
    again_status, again = service.record_specialist_progress(document["journey_id"], round_id)
    assert again_status == 200
    assert scorer.calls == 1
    assert launcher.calls == 1
    assert again["rounds"][0]["results_publication"] == publication
    reread = {
        path.relative_to(catalog).as_posix(): path.read_bytes()
        for path in sorted(catalog.rglob("*"))
        if path.is_file()
    }
    assert reread == catalog_bytes
    repeat = publish_verified_attempt_to_catalog(attempt_directory, attempt_id, catalog)
    assert repeat["status"] == "verified"
    assert repeat["rewritten"] is False
    assert repeat["models"] == publication["models"]
    assert repeat["scientific_validation_established"] is False
    loaded = load_catalog(
        default_attempt_files()
        + (AttemptFiles(review_json=review_path, pdb_dir=review_path.parent),)
    )
    inspected = handle_attempt_get(
        f"/api/attempts/{attempt_id}/inspection?candidate={design}&model=model1",
        loaded,
    )
    assert isinstance(inspected, tuple)
    body = inspected[1]
    assert body["attempt_id"] == attempt_id
    assert body["origin"]["kind"] == "recorded"
    assert body["origin"]["execution"] != "manual"
    assert set(body["models"]) == {"model1", "model2"}
    for model_id in ("model1", "model2"):
        assert body["models"][model_id]["pdb_url"] == f"/api/attempts/{attempt_id}/structures/{model_id}.pdb"
        artifact = handle_attempt_get(
            f"/api/attempts/{attempt_id}/structures/{model_id}.pdb",
            loaded,
        )
        assert isinstance(artifact, StructureArtifact)
        assert artifact.status == 200
        assert hashlib.sha256(artifact.body).hexdigest() == body["models"][model_id]["sha256"]
        assert artifact.body == (catalog / attempt_id / f"{model_id}.pdb").read_bytes()
    manual = handle_attempt_get(
        "/api/attempts/attempt.2LUTUOX4/structures/model1.pdb",
        loaded,
    )
    assert isinstance(manual, StructureArtifact)
    assert manual.body != (catalog / attempt_id / "model1.pdb").read_bytes()
    conflict = catalog / "attempt.conflicted"
    refused = publish_verified_attempt_to_catalog(
        attempt_directory,
        "attempt.c5c1f13e",
        catalog,
    )
    assert refused["status"] == "outputs_rejected"
    assert "recovered_attempt_not_published" in refused["reasons"]
    assert not (catalog / "attempt.c5c1f13e").exists()
    plans.close()
    service.close()
    del conflict


def test_catalog_publication_refuses_a_conflicting_destination(tmp_path: Path) -> None:
    attempt_id = "attempt.1234abcd"
    attempt_directory = tmp_path / "attempts" / attempt_id
    attempt_directory.mkdir(parents=True)
    design = "PDL1_l65_s7"
    raw = _publishable_artifacts(attempt_directory, design)
    catalog = tmp_path / "results"
    destination = catalog / attempt_id
    destination.mkdir(parents=True)
    existing = destination / "model1.pdb"
    existing.write_text("different structure\n", encoding="utf-8")
    before = existing.read_bytes()
    refused = publish_verified_attempt_to_catalog(attempt_directory, attempt_id, catalog)
    assert refused["status"] == "outputs_rejected"
    assert "catalog_pdb_conflicts" in refused["reasons"]
    assert existing.read_bytes() == before
    assert not (destination / "review.json").exists()
    assert _tree_hashes(attempt_directory / "designs") == raw


def test_scoring_failure_is_not_replayed(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True, "accepted_candidate": True})
    scorer = _IpSAEScore(error=RuntimeError("score failed"))
    plans, service, document = _ready_journey(tmp_path, launcher, specialist_scorer=scorer)
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    execution = launched["execution"]
    _claim_candidate(service, document["journey_id"], execution, design="PDL1_l65_s7")
    _write_bindcraft_artifacts(Path(execution["attempt_directory"]), "PDL1_l65_s7")
    round_id = launched["rounds"][0]["round_id"]
    status, recorded = service.record_specialist_progress(document["journey_id"], round_id)
    assert status == 200, recorded
    assert scorer.calls == 1
    assert _status(recorded, "scoring") == "failed"
    assert "not replayed" in _stage(recorded, "scoring")["failure_reason"]
    assert _status(recorded, "ranking") == "blocked"
    again_status, _again = service.record_specialist_progress(document["journey_id"], round_id)
    assert again_status == 200
    assert scorer.calls == 1
    plans.close()
    service.close()


def test_interrupted_scoring_is_not_replayed(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True, "accepted_candidate": True})
    scorer = _IpSAEScore(_ipsae_result("candidate.1"))
    plans, service, document = _ready_journey(tmp_path, launcher, specialist_scorer=scorer)
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    execution = launched["execution"]
    journey_id = document["journey_id"]
    round_id = launched["rounds"][0]["round_id"]
    stored = service._store.get(journey_id)
    stored["rounds"] = [
        {
            "round_id": round_id,
            "ordinal": 1,
            "plan_id": stored["plan_id"],
            "plan_revision": stored["plan_revision"],
            "digest": launched["execution_review"]["digest"],
            "execution": execution,
            "status": "open",
            "parameters": [],
            "specialist_stages": [{"stage_id": "scoring", "status": "running", "agent_executed": False}],
            "specialist_progress": {
                "status": "running",
                "attempt_id": execution["attempt_id"],
                "digest": launched["execution_review"]["digest"],
            },
        }
    ]
    service._store.put(stored)
    status, recorded = service.record_specialist_progress(journey_id, round_id)
    assert status == 200, recorded
    assert scorer.calls == 0
    assert launcher.calls == 1
    assert _status(recorded, "scoring") == "failed"
    assert "not replayed" in _stage(recorded, "scoring")["failure_reason"]
    plans.close()
    service.close()


def test_specialist_progress_survives_sqlite_reopen_without_duplication(tmp_path: Path) -> None:
    launcher = _Outcome(
        {
            "status": "budget_exhausted",
            "launched": True,
            "accepted_candidate": False,
            "termination_reason": "attempted_trajectory_budget_exhausted",
            "budget_compliance_established": True,
        }
    )
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    plans, service, document = _ready_journey(
        tmp_path,
        launcher,
        plan_store=SQLiteGenerationPlanStore(plan_path),
        journey_store=SQLiteDesignJourneyStore(journey_path),
    )
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    round_id = launched["rounds"][0]["round_id"]
    status, recorded = service.record_specialist_progress(document["journey_id"], round_id)
    assert status == 200
    service.close()
    plans.close()
    reopened_plans = GenerationPlanService(
        None,
        inputs=_input_catalog(tmp_path / "inputs"),
        demonstration=launcher,
        store=SQLiteGenerationPlanStore(plan_path),
    )
    reopened = DesignJourneyService(
        reopened_plans,
        store=SQLiteDesignJourneyStore(journey_path),
        attempt_root=tmp_path / "attempts",
    )
    read_status, body = handle_design_journey(
        reopened, "GET", f"/api/design-journeys/{document['journey_id']}", b""
    )
    assert read_status == 200
    assert launcher.calls == 1
    assert _status(body, "scoring") == "blocked"
    assert len(body["specialist_events"]) == len(recorded["specialist_events"])
    again_status, again = reopened.record_specialist_progress(document["journey_id"], round_id)
    assert again_status == 200
    assert len(again["specialist_events"]) == len(recorded["specialist_events"])
    assert launcher.calls == 1
    reopened.close()
    reopened_plans.close()


def test_specialist_progress_stays_on_its_round(tmp_path: Path) -> None:
    launcher = _Outcome(
        {
            "status": "budget_exhausted",
            "launched": True,
            "accepted_candidate": False,
            "termination_reason": "attempted_trajectory_budget_exhausted",
            "budget_compliance_established": True,
        }
    )
    plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    parent_id = launched["rounds"][0]["round_id"]
    status, _recorded = service.record_specialist_progress(document["journey_id"], parent_id)
    assert status == 200
    prepared_status, prepared = service.prepare_next_round(document["journey_id"], parent_id)
    assert prepared_status == 200
    assert launcher.calls == 1
    parent = next(item for item in prepared["rounds"] if item["round_id"] == parent_id)
    child = next(item for item in prepared["rounds"] if item["parent_round_id"] == parent_id)
    assert any("No accepted candidate" in str(item.get("contribution")) for item in parent["specialist_stages"])
    assert child["specialist_stages"] == []
    assert "No accepted candidate" not in str(_stage(prepared, "scoring")["limitations"])
    assert child["approval"] is None
    assert child["execution"] is None
    plans.close()
    service.close()


def test_a_changed_seed_invalidates_authorization_and_does_not_launch(tmp_path: Path) -> None:
    launcher = _Outcome({"status": "verified", "launched": True})
    plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    text = (
        "Generate another PDL1 binder. "
        "technical-smoke-test no_filters. This filter policy is for the technical "
        "demonstration and does not establish scientific quality."
    )
    revised = service.open_or_get(
        "request_ready",
        1,
        text,
        [{"name": "seed", "value": 2, "provenance": "user", "source": "professor"}],
    )
    assert revised["plan_revision"] != authorized["plan_revision"]
    assert revised.get("execution_authorization") is None
    assert revised["execution_review"]["execution_authorized"] is False
    published = plans.published(str(document["plan_id"]))
    assert published is not None
    assert published["fields"]["seed"] == 2
    refused, body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert refused == 409
    assert body["status"] == "execution_authorization_required"
    assert launcher.calls == 0
    assert list((tmp_path / "attempts").glob("attempt.*")) == []
    plans.close()
    service.close()


def test_changed_registered_inputs_invalidate_authorization_without_a_launch(
    tmp_path: Path,
) -> None:
    launcher = _Outcome({"status": "verified", "launched": True})
    _plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized_status, authorized = service.authorize_execution(
        document["journey_id"], _authorization(document)
    )
    (tmp_path / "inputs" / "advanced_profile").write_bytes(b'{"k": 2}\n')
    shown = service.get(document["journey_id"])
    assert shown is not None
    assert shown["execution_review"]["execution_authorized"] is False
    assert launcher.calls == 0
    refused, body = service.launch_execution(document["journey_id"], _launch(authorized))
    assert refused == 409
    assert body["status"] == "execution_not_ready"
    assert "input_identity_rejected" in body["unresolved_blockers"]
    assert launcher.calls == 0
    stored = service.get(document["journey_id"])
    assert stored is not None
    assert stored["execution_review"]["execution_authorized"] is False
    assert stored.get("execution") is None
    again, _again_body = service.launch_execution(
        document["journey_id"], dict(_launch(authorized), idempotency_key="launch-again")
    )
    assert again == 409
    assert launcher.calls == 0
    service.close()


def test_a_verified_budget_above_four_is_not_registered(tmp_path: Path) -> None:
    root = tmp_path / "inputs"
    root.mkdir()
    files = {
        "target_structure": root / "target.pdb",
        "settings": root / "settings.json",
        "advanced_profile": root / "advanced.json",
    }
    files["target_structure"].write_bytes(b"TARGET\n")
    files["settings"].write_bytes(b'{"chain": "A"}\n')
    files["advanced_profile"].write_bytes(
        b'{"max_trajectories": 5, "num_seqs": 1, "max_mpnn_sequences": 1}\n'
    )
    identities = RegisteredInputCatalog(files).identities()
    assert identities is not None
    assert "sequence_limits" not in identities
    files["advanced_profile"].write_bytes(
        b'{"max_trajectories": 4, "num_seqs": 1, "max_mpnn_sequences": 1, "weights_plddt": 0.1}\n'
    )
    bounded = RegisteredInputCatalog(files).identities()
    assert bounded is not None
    assert bounded["sequence_limits"] == {
        "max_trajectories": 4,
        "num_seqs": 1,
        "max_mpnn_sequences": 1,
    }


def test_a_consumed_round_cannot_be_revised_in_place(tmp_path: Path) -> None:
    launcher = _Outcome(
        {
            "status": "budget_exhausted",
            "launched": True,
            "accepted_candidate": False,
            "termination_reason": "attempted_trajectory_budget_exhausted",
        }
    )
    plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    launched_status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert launched_status == 200
    revision = launched["plan_revision"]
    status, body = handle_design_journey(
        service,
        "POST",
        "/api/design-journeys",
        json.dumps(
            {
                "request_id": "request_ready",
                "request_revision": 1,
                "request_text": "Generate another PDL1 binder.",
                "fields": [{"name": "seed", "value": 9, "provenance": "user", "source": "professor"}],
            }
        ).encode(),
    )
    assert status == 409
    assert body["status"] == "round_launch_consumed"
    assert body["detail"] == "This round already consumed a launch. Plan changes belong to a new round."
    stored = service.get(document["journey_id"])
    assert stored is not None
    assert stored["plan_revision"] == revision
    assert launcher.calls == 1
    plans.close()
    service.close()


_ROUND10_DIGEST = "bb3c0f1a507b4a38d2a5a9cb9770012830124b5425ec0aa8b4097e6a84d9877e"
_ROUND10_PLAN = "plan_e48d2b2dde43412c8666c06c0f9be2c8"
_ROUND10_ATTEMPT = "attempt.96138b26"
_ROUND10_DESIGN = "PDL1_l65_s875688_mpnn1"


def _write_verified_attempt(attempt_directory: Path, design: str) -> dict[str, str]:
    attempt_directory.mkdir(parents=True, exist_ok=True)
    _publishable_artifacts(attempt_directory, design)
    ledger = {
        "adapter_version": ADAPTER_VERSION,
        "requested_attempted_trajectories": 4,
        "observed_attempted_trajectories": 1,
        "observed_low_confidence_trajectories": 0,
        "observed_relaxed_trajectories": 1,
        "compliance_established": True,
        "termination_reason": "successful_early_completion",
        "resume_supported": False,
        "reservations": [{"scheduler_reserved": True, "state": "finished"}],
    }
    designs = attempt_directory / "designs"
    (designs / "cellertz_trajectory_budget.json").write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    raw = _tree_hashes(designs)
    ingested = ingest_bindcraft_output(
        designs,
        attempt_id=attempt_directory.name,
        execution="apptainer_demonstration_launch",
    )
    assert ingested["status"] == "verified", ingested
    published = publish_ingested_review(attempt_directory, ingested)
    assert published["status"] == "verified", published
    assert _tree_hashes(designs) == raw
    return raw


class _VerifiedReply:
    def __init__(self, *, write_artifacts: bool) -> None:
        self.calls = 0
        self.write_artifacts = write_artifacts

    def launch(self, request: Any) -> dict[str, object]:
        self.calls += 1
        attempt_id = str(request.attempt_id)
        if self.write_artifacts:
            _write_verified_attempt(Path(request.attempt_directory), _ROUND10_DESIGN)
        return {
            "status": "verified",
            "launched": True,
            "attempt_id": attempt_id,
            "scientific_validation": False,
        }


def test_verified_reply_saves_a_candidate_only_after_artifact_validation(tmp_path: Path) -> None:
    launcher = _VerifiedReply(write_artifacts=True)
    plans, service, document = _ready_journey(tmp_path, launcher)
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert status == 200, launched
    execution = launched["execution"]
    assert launcher.calls == 1
    assert execution["status"] == "verified"
    assert execution["accepted_candidate"] is True
    assert execution["candidate_id"] == f"bindcraft_{_ROUND10_DESIGN}"
    assert execution["candidate_identity"]["design"] == _ROUND10_DESIGN
    assert execution["candidate_identity"]["execution_id"] == execution["attempt_id"]
    assert execution["budget_compliance_established"] is True
    assert execution["budget_usage"]["requested_attempted_trajectories"] == 4
    assert execution["budget_usage"]["attempted_trajectories"] == 1
    assert execution["termination_reason"] == "successful_early_completion"
    assert execution["scientific_validation_established"] is False
    plans.close()
    service.close()


def test_verified_status_without_artifacts_does_not_invent_a_candidate(tmp_path: Path) -> None:
    launcher = _VerifiedReply(write_artifacts=False)
    scorer = _IpSAEScore(_ipsae_result("candidate.1"))
    plans, service, document = _ready_journey(tmp_path, launcher, specialist_scorer=scorer)
    _authorized, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    status, launched = service.launch_execution(document["journey_id"], _launch(authorized))
    assert status == 200, launched
    execution = launched["execution"]
    assert execution["status"] == "verified"
    assert execution["accepted_candidate"] is False
    assert execution["candidate_id"] is None
    assert execution["budget_compliance_established"] is False
    assert "budget_usage" not in execution
    round_id = launched["rounds"][0]["round_id"]
    recorded_status, recorded = service.record_specialist_progress(document["journey_id"], round_id)
    assert recorded_status == 200, recorded
    assert scorer.calls == 0
    assert launcher.calls == 1
    assert "No accepted candidate" in _stage(recorded, "scoring")["blocking_reason"]
    plans.close()
    service.close()


def test_verified_missing_candidate_recovery_survives_restart_and_duplicate_requests(
    tmp_path: Path, monkeypatch: Any
) -> None:
    launcher = _Outcome({"status": "verified", "launched": True})
    catalog = tmp_path / "results"
    plan_path = tmp_path / "plans.sqlite"
    journey_path = tmp_path / "journeys.sqlite"
    plans, service, document = _ready_journey(
        tmp_path,
        launcher,
        plan_store=SQLiteGenerationPlanStore(plan_path),
        journey_store=SQLiteDesignJourneyStore(journey_path),
        results_catalog=catalog,
    )
    attempt_directory = tmp_path / _ROUND10_ATTEMPT
    raw = _write_verified_attempt(attempt_directory, _ROUND10_DESIGN)
    review_bytes = (attempt_directory / "review.json").read_bytes()
    saved = service._store.get(document["journey_id"])
    assert isinstance(saved, dict)
    journey_id = document["journey_id"]
    round_id = f"round_{journey_id}_10"
    saved["execution"] = {
        "launch_id": "launch_round10",
        "idempotency_key": "launch-round-10",
        "plan_id": _ROUND10_PLAN,
        "revision": 4,
        "digest": _ROUND10_DIGEST,
        "attempt_id": _ROUND10_ATTEMPT,
        "attempt_directory": str(attempt_directory),
        "gpu_index": 0,
        "status": "verified",
        "launched": True,
        "consumed": True,
        "terminal": True,
        "progress": [{"status": "verified"}],
        "accepted_candidate": False,
        "successful_candidate_generation": False,
        "candidate_id": None,
        "termination_reason": None,
        "budget_compliance_established": False,
        "scientific_validation_established": False,
        "result_link": {
            "journey_id": journey_id,
            "attempt_id": _ROUND10_ATTEMPT,
            "candidate_id": None,
        },
    }
    saved["rounds"] = [
        {
            "round_id": round_id,
            "ordinal": 10,
            "plan_id": _ROUND10_PLAN,
            "plan_revision": 4,
            "digest": _ROUND10_DIGEST,
            "status": "open",
        }
    ]
    saved["active_round_id"] = round_id
    service._store.put(saved)
    blocked_status, blocked = service.record_specialist_progress(journey_id, round_id)
    assert blocked_status == 200, blocked
    assert launcher.calls == 0
    assert "No accepted candidate" in _stage(blocked, "scoring")["blocking_reason"]
    calls = {"n": 0}
    original = ProteinIpSAECalculator.calculate

    def _count(self: ProteinIpSAECalculator, **kwargs: object) -> object:
        calls["n"] += 1
        return original(self, **kwargs)

    monkeypatch.setattr(ProteinIpSAECalculator, "calculate", _count)
    body = json.dumps({"attempt_id": _ROUND10_ATTEMPT}).encode()
    recovered_status, recovered = handle_design_journey(
        service,
        "POST",
        f"/api/design-journeys/{journey_id}/accepted-launch-recovery",
        body,
    )
    assert recovered_status == 200, recovered
    assert recovered["accepted_launch_recovery"]["idempotent"] is False
    assert recovered["accepted_launch_recovery"]["round_id"] == round_id
    execution = recovered["execution"]
    assert execution["launch_id"] == "launch_round10"
    assert execution["consumed"] is True
    assert execution["attempt_id"] == _ROUND10_ATTEMPT
    assert execution["plan_id"] == _ROUND10_PLAN
    assert execution["revision"] == 4
    assert execution["digest"] == _ROUND10_DIGEST
    assert execution["accepted_candidate"] is True
    assert execution["candidate_id"] == f"bindcraft_{_ROUND10_DESIGN}"
    assert execution["budget_compliance_established"] is True
    assert execution["budget_usage"]["requested_attempted_trajectories"] == 4
    assert execution["scientific_validation_established"] is False
    card = next(item for item in recovered["rounds"] if item["round_id"] == round_id)
    correction = card["outcome_corrections"][0]
    assert correction["kind"] == "accepted_launch_outcome_correction"
    assert correction["plan_revision"] == 4
    assert correction["digest"] == _ROUND10_DIGEST
    assert correction["superseded_execution"]["accepted_candidate"] is False
    assert correction["superseded_execution"]["candidate_id"] is None
    assert correction["superseded_execution"]["budget_compliance_established"] is False
    assert "budget_usage" not in correction["superseded_execution"]
    assert any(
        "No accepted candidate" in str(item.get("blocking_reason") or item.get("contribution") or "")
        for item in correction["superseded_specialist_stages"]
    )
    assert card["specialist_events"] == []
    plans.close()
    service.close()
    plans, service = _reopen_journey(tmp_path, plan_path, journey_path, catalog, launcher)
    duplicate_status, duplicate = service.recover_accepted_launch(journey_id, _ROUND10_ATTEMPT)
    assert duplicate_status == 200, duplicate
    assert duplicate["accepted_launch_recovery"]["idempotent"] is True
    assert duplicate["execution"]["candidate_id"] == execution["candidate_id"]
    stored = service._store.get(journey_id)
    assert isinstance(stored, dict)
    assert len(stored["rounds"][0]["outcome_corrections"]) == 1
    progressed_status, progressed = service.record_specialist_progress(journey_id, round_id)
    assert progressed_status == 200, progressed
    assert calls["n"] == 2
    assert launcher.calls == 0
    assert _stage(progressed, "generation")["agent_executed"] is True
    assert _stage(progressed, "generation")["implementation"] == "ProteinGenerationAgent"
    scoring = _stage(progressed, "scoring")
    assert scoring["agent_executed"] is True
    assert scoring["output"]["score_source"] == "ProteinIpSAECalculator"
    assert scoring["output"]["scientific_validation_established"] is False
    assert isinstance(scoring["output"]["ranking_score"], float)
    assert _stage(progressed, "ranking")["agent_executed"] is True
    assert _stage(progressed, "critique")["agent_executed"] is True
    assert _stage(progressed, "prioritization")["agent_executed"] is True
    reference = scoring["evidence_references"][0]
    assert reference["revision"] == 4
    assert reference["digest"] == _ROUND10_DIGEST
    assert reference["plan_id"] == _ROUND10_PLAN
    progressed_card = next(item for item in progressed["rounds"] if item["round_id"] == round_id)
    assert len(progressed_card["outcome_corrections"]) == 1
    assert progressed_card["results_publication"]["status"] == "verified"
    assert progressed_card["results_publication"]["scientific_validation_established"] is False
    assert progressed_card["results_publication"]["representative"]["rule"] == (
        "Accepted and Ranked copies match this model's SHA-256"
    )
    assert _tree_hashes(attempt_directory / "designs") == raw
    assert (attempt_directory / "review.json").read_bytes() == review_bytes
    again_status, again = service.record_specialist_progress(journey_id, round_id)
    assert again_status == 200
    assert calls["n"] == 2
    assert again["specialist_events"] == progressed["specialist_events"]
    plans.close()
    service.close()
    plans, service = _reopen_journey(tmp_path, plan_path, journey_path, catalog, launcher)
    restarted_status, restarted = service.record_specialist_progress(journey_id, round_id)
    assert restarted_status == 200
    assert restarted["specialist_events"] == progressed["specialist_events"]
    assert len(restarted["rounds"][0]["outcome_corrections"]) == 1
    assert _tree_hashes(attempt_directory / "designs") == raw
    plans.close()
    service.close()


def _reopen_journey(
    tmp_path: Path,
    plan_path: Path,
    journey_path: Path,
    catalog: Path,
    launcher: object,
) -> tuple[GenerationPlanService, DesignJourneyService]:
    plans = GenerationPlanService(
        None,
        inputs=_input_catalog(tmp_path / "inputs"),
        demonstration=launcher,
        store=SQLiteGenerationPlanStore(plan_path),
    )
    service = DesignJourneyService(
        plans,
        store=SQLiteDesignJourneyStore(journey_path),
        attempt_root=tmp_path / "attempts",
        results_catalog=catalog,
    )
    return plans, service


def _ready_journey(
    tmp_path: Path,
    launcher: object,
    *,
    plan_store: object | None = None,
    journey_store: object | None = None,
    gpu: str = "0",
    specialist_scorer: object | None = None,
    results_catalog: Path | None = None,
) -> tuple[GenerationPlanService, DesignJourneyService, dict]:
    text = (
        "Generate another PDL1 binder. "
        "technical-smoke-test no_filters. This filter policy is for the technical "
        "demonstration and does not establish scientific quality."
    )
    plans = GenerationPlanService(
        None,
        inputs=_input_catalog(tmp_path / "inputs"),
        demonstration=launcher,
        store=plan_store,
    )
    proposal = {
        "request_text": text,
        "target_identity": "PDL1",
        "binder_length": 65,
        "hotspot_assumption": "Hotspot 56 is assumed and still needs confirmation.",
        "filter_policy": "no_filters",
        "seed": 7,
        "cpu_count": 4,
        "memory_mib": 16384,
        "gpu": gpu,
        "execution_deadline_seconds": 14400,
    }
    plans.draft("design-journey:request_ready", proposal)
    service = DesignJourneyService(
        plans,
        store=journey_store,
        attempt_root=tmp_path / "attempts",
        specialist_scorer=specialist_scorer,
        results_catalog=results_catalog,
    )
    document = service.open_or_get("request_ready", 1, text)
    return plans, service, document


def _authorization(document: dict) -> dict[str, object]:
    review = document["execution_review"]
    return {
        "plan_id": review["plan_id"],
        "revision": review["revision"],
        "digest": review["digest"],
        "reviewer_id": "professor.ada",
        "reason": "I authorize this execution-ready revision.",
        "idempotency_key": "authorize-once",
    }


def _launch(document: dict, key: str = "launch-once") -> dict[str, object]:
    review = document["execution_review"]
    return {
        "plan_id": review["plan_id"],
        "revision": review["revision"],
        "digest": review["digest"],
        "reviewer_id": "professor.ada",
        "reason": "Launch the authorized revision on the CPU double.",
        "idempotency_key": key,
        "gpu_index": 0,
    }


class _Outcome:
    def __init__(self, reply: dict[str, object]) -> None:
        self.calls = 0
        self.reply = reply
        self.requests: list[object] = []

    def launch(self, request: object) -> dict[str, object]:
        self.calls += 1
        self.requests.append(request)
        body = dict(self.reply)
        body.setdefault("attempt_id", getattr(request, "attempt_id", None))
        return body


class _VerifiedPdl1Inputs:
    def __init__(
        self,
        hotspot: str | None = None,
        sequence_limits: dict[str, int] | None = None,
    ) -> None:
        self._hotspot = hotspot
        self._sequence_limits = sequence_limits

    def registration_status(self) -> str:
        return "verified"

    def identities(self) -> dict[str, object]:
        recorded: dict[str, object] = {
            "artifacts": [
                {"role": "target_structure", "sha256": "a" * 64, "artifact_id": "registered_target_structure", "verified": True},
                {"role": "settings", "sha256": "b" * 64, "artifact_id": "registered_settings", "verified": True},
                {"role": "advanced_profile", "sha256": "c" * 64, "artifact_id": "registered_advanced_profile", "verified": True},
            ],
            "input_selection": {"schema": "cellertz.pdl1_input_selection.v1"},
        }
        if self._hotspot is not None:
            recorded["hotspot_assumption"] = self._hotspot
        if self._sequence_limits is not None:
            recorded["sequence_limits"] = dict(self._sequence_limits)
        return recorded


class _Knowledge:
    def __init__(self, items: list[SimpleNamespace]) -> None:
        self._items = items

    def list(self, status: str | None = None) -> tuple[SimpleNamespace, ...]:
        return tuple(item for item in self._items if status is None or item.status == status)


class _BrokenPlans:
    def draft(self, conversation_id: str, proposal: dict) -> dict:
        raise RuntimeError("planning failed")


class _CountingPlans:
    def __init__(self) -> None:
        self.calls = 0
        self._revision = 0
        self._plan_id = "plan_test"

    def draft(self, conversation_id: str, proposal: dict) -> dict:
        self.calls += 1
        self._revision += 1
        return {
            "plan_id": self._plan_id,
            "revision": self._revision,
            "digest": "abc",
            "approved": False,
            "execution_connected": False,
            "scientific_execution": "disabled",
            "questions": [{"field": "seed", "question": "Which integer seed should the plan record?"}],
            "fields": {"target_identity": "PDL1"},
        }


class _Demonstration:
    def __init__(self) -> None:
        self.calls = 0

    def launch(self, request: object) -> dict[str, bool]:
        self.calls += 1
        return {"launched": True}


class _Runs:
    def __init__(self) -> None:
        self.creates = 0

    def dispatch_gates(self) -> tuple[str, ...]:
        return ()

    def create(self, **_kwargs: object) -> None:
        self.creates += 1
        raise AssertionError("compute")


class _Replay:
    def draft(self, conversation_id: str, proposal: dict) -> dict:
        raise AssertionError("planning replayed")

    def approve(self, **_kwargs: object) -> dict:
        raise AssertionError("approval replayed")


def _approval(document: dict, digest: str) -> dict[str, object]:
    return {
        "plan_id": document["plan_id"],
        "revision": document["plan_revision"],
        "digest": digest,
        "reviewer_id": "professor.ada",
        "reason": "I reviewed this saved revision.",
        "idempotency_key": "approval-1",
    }


def _sqlite_documents(path: Path, table: str, key: str) -> list[tuple[str, str]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = connection.execute(f"SELECT {key}, document FROM {table} ORDER BY {key}").fetchall()
    connection.close()
    return [(str(row[0]), str(row[1])) for row in rows]


def _digest(document: dict) -> str:
    return str(_stage(document, "generation_planning")["output"]["digest"])


def _stage(document: dict, stage_id: str) -> dict:
    return next(stage for stage in document["stages"] if stage["stage_id"] == stage_id)


def _status(document: dict, stage_id: str) -> str:
    return str(_stage(document, stage_id)["status"])


def _stage_text(document: dict) -> str:
    return str(document["stages"])


class _IpSAEScore:
    """Test double. A returned number is not a calculated scientific ipSAE."""

    def __init__(self, result: ProteinScoreResultV2 | None = None, error: Exception | None = None) -> None:
        self.calls = 0
        self.result = result
        self.error = error

    def __call__(self, _execution: object) -> ProteinScoreResultV2:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("The scoring double was called without a result.")
        return self.result


def _ipsae_result(candidate_id: str) -> ProteinScoreResultV2:
    return ProteinScoreResultV2(
        candidate_id=candidate_id,
        baseline_score=0.42,
        structural_evidence={"ipsae": 0.42},
    )


def _publishable_artifacts(attempt_directory: Path, design: str) -> dict[str, str]:
    import numpy as np

    designs = attempt_directory / "designs"
    relaxed = designs / "MPNN" / "Relaxed"
    accepted = designs / "Accepted"
    ranked = designs / "Accepted" / "Ranked"
    pae = designs / "CellertzEvidence" / "PAE"
    for directory in (relaxed, accepted, ranked, pae):
        directory.mkdir(parents=True, exist_ok=True)
    model1 = _two_chain_pdb(0.0)
    model2 = _two_chain_pdb(7.0)
    (relaxed / f"{design}_model1.pdb").write_text(model1, encoding="utf-8")
    (relaxed / f"{design}_model2.pdb").write_text(model2, encoding="utf-8")
    (accepted / f"{design}_model1.pdb").write_text(model1, encoding="utf-8")
    (ranked / f"1_{design}_model1.pdb").write_text(model1, encoding="utf-8")
    (designs / "final_design_stats.csv").write_text(
        f"Design,Sequence\n{design},A\n",
        encoding="utf-8",
    )
    for number, text in ((1, model1), (2, model2)):
        pdb_path = relaxed / f"{design}_model{number}.pdb"
        pae_path = pae / f"{design}_model{number}_pae.npz"
        np.savez_compressed(
            pae_path,
            pae_angstrom=np.zeros((2, 2), dtype=np.float32),
            candidate_id=np.asarray(design),
            model_number=np.asarray(number, dtype=np.int32),
        )
        order = {
            "candidate_id": design,
            "contract": "pae_index_equals_capture_time_residue_order",
            "established": True,
            "model_number": number,
            "pae_sha256": hashlib.sha256(pae_path.read_bytes()).hexdigest(),
            "pdb_sha256": hashlib.sha256(pdb_path.read_bytes()).hexdigest(),
            "residues": [
                {
                    "chain": "A",
                    "index": 0,
                    "insertion_code": "",
                    "model_residue_index": 1,
                    "residue_name": "GLY",
                    "residue_number": 1,
                },
                {
                    "chain": "B",
                    "index": 1,
                    "insertion_code": "",
                    "model_residue_index": 51,
                    "residue_name": "ALA",
                    "residue_number": 1,
                },
            ],
            "source": "prediction_model.aux",
        }
        (pae / f"{design}_model{number}_residue_order.json").write_text(
            json.dumps(order) + "\n",
            encoding="utf-8",
        )
    return _tree_hashes(designs)


def _two_chain_pdb(shift: float) -> str:
    lines = [
        _ca_line(1, "GLY", "A", 1, shift),
        _ca_line(2, "ALA", "B", 1, shift + 3.0),
        "END",
    ]
    return "\n".join(lines) + "\n"


def _ca_line(serial: int, resname: str, chain: str, resseq: int, x: float) -> str:
    return (
        f"ATOM  {serial:5d}  CA  {resname:<3} {chain}{resseq:4d}    "
        f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
    )


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _claim_candidate(service: DesignJourneyService, journey_id: str, execution: dict, *, design: str) -> None:
    stored = service._store.get(journey_id)
    stored["execution"]["accepted_candidate"] = True
    stored["execution"]["candidate_id"] = "candidate.1"
    stored["execution"]["candidate_identity"] = {
        "candidate_id": "candidate.1",
        "sequence": "A" * 65,
        "target_id": "PDL1",
        "design": design,
        "execution_id": execution["attempt_id"],
    }
    service._store.put(stored)


def _write_bindcraft_artifacts(attempt_directory: Path, design: str) -> None:
    import numpy as np

    relaxed = attempt_directory / "designs" / "MPNN" / "Relaxed"
    pae = attempt_directory / "designs" / "CellertzEvidence" / "PAE"
    relaxed.mkdir(parents=True)
    pae.mkdir(parents=True)
    pdb_path = relaxed / f"{design}_model1.pdb"
    pdb_path.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
        "ATOM      2  CA  ALA B   1       1.000   0.000   0.000  1.00  0.00           C\n",
        encoding="utf-8",
    )
    pae_path = pae / f"{design}_model1_pae.npz"
    np.savez_compressed(
        pae_path,
        pae_angstrom=np.zeros((2, 2), dtype=np.float32),
        candidate_id=np.asarray(design),
        model_number=np.asarray(1, dtype=np.int32),
    )
    (attempt_directory / "designs" / "final_design_stats.csv").write_text(
        f"Design,Sequence\n{design},{'A' * 65}\n",
        encoding="utf-8",
    )
    order = {
        "candidate_id": design,
        "contract": "pae_index_equals_capture_time_residue_order",
        "established": True,
        "model_number": 1,
        "pae_sha256": hashlib.sha256(pae_path.read_bytes()).hexdigest(),
        "pdb_sha256": hashlib.sha256(pdb_path.read_bytes()).hexdigest(),
        "residues": [
            {
                "chain": "A",
                "index": 0,
                "insertion_code": "",
                "model_residue_index": 1,
                "residue_name": "ALA",
                "residue_number": 1,
            },
            {
                "chain": "B",
                "index": 1,
                "insertion_code": "",
                "model_residue_index": 51,
                "residue_name": "ALA",
                "residue_number": 1,
            },
        ],
        "source": "prediction_model.aux",
    }
    (pae / f"{design}_model1_residue_order.json").write_text(
        json.dumps(order) + "\n",
        encoding="utf-8",
    )


def test_results_follow_the_selected_request_and_leave_other_attempts_unread(tmp_path: Path, monkeypatch) -> None:
    from cellertz.application.design_journey import MemoryDesignJourneyStore, _request_results

    root = tmp_path / "attempts"
    linked = root / "attempt.linked01"
    other = root / "attempt.historical"
    linked.mkdir(parents=True)
    other.mkdir()
    sequence = "A" * 65
    model1 = b"ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
    model2 = b"ATOM      1  CA  ALA A   1       1.000   0.000   0.000  1.00  0.00           C\n"
    (linked / "model1.pdb").write_bytes(model1)
    (linked / "model2.pdb").write_bytes(model2)
    (linked / "executor.stdout").write_text(
        "Finished all designs. Script execution for 2 trajectories took: 0 hours, 7 minutes, 25 seconds\n",
        encoding="utf-8",
    )
    review = {
        "attempt_id": "attempt.linked01",
        "design": "PDL1_l65_s597806_mpnn1",
        "filter_policy": "no_filters",
        "scientific_evaluation": "pending",
        "platform_ipsae_ranking": False,
        "ranking_score_computed": False,
        "experimental_validation": False,
        "representative": {"model": "model1"},
        "chains": {"target": "A", "binder": "B"},
        "models": {
            "model1": {"sequence": sequence, "sha256": hashlib.sha256(model1).hexdigest(), "representative": True},
            "model2": {"sequence": sequence, "sha256": hashlib.sha256(model2).hexdigest(), "representative": False},
        },
        "metrics": [{
            "metric": "i_pTM",
            "model1": {"kind": "recorded", "text": "0.79"},
            "model2": {"kind": "recorded", "text": "0.76"},
        }],
        "provenance": {"recorded_rank": "1", "omitted": ["ipSAE", "ranking_score"], "stats_csv_sha256": "abc"},
    }
    (linked / "review.json").write_text(json.dumps(review), encoding="utf-8")
    (other / "review.json").write_text(
        json.dumps({**review, "attempt_id": "attempt.historical", "design": "historical"}),
        encoding="utf-8",
    )
    owned = {
        "journey_id": "journey_owned",
        "request_id": "request_owned",
        "request_revision": 2,
        "plan_id": "plan_owned",
        "plan_revision": 3,
        "execution": {
            "source": "run_design",
            "attempt_id": "attempt.linked01",
            "attempt_directory": str(linked),
            "status": "verified",
            "launched": True,
            "terminal": True,
            "plan_id": "plan_owned",
            "revision": 3,
            "digest": "d" * 64,
        },
        "execution_history": [{
            "source": "run_design",
            "attempt_id": "attempt.failed01",
            "attempt_directory": str(root / "attempt.failed01"),
            "status": "launch_failed",
            "launched": True,
            "terminal": True,
            "diagnosis": "launch_failed",
            "revision": 3,
            "digest": "d" * 64,
        }],
    }
    empty = {
        "journey_id": "journey_empty",
        "request_id": "request_empty",
        "request_revision": 1,
        "execution_history": [],
    }
    shown = _request_results(owned, root)
    assert [item["attempt_id"] for item in shown["results"]] == ["attempt.linked01", "attempt.failed01"]
    recorded = shown["results"][0]["review"]
    assert recorded["candidate"] == "PDL1_l65_s597806_mpnn1"
    assert recorded["metrics"][0]["model1"]["text"] == "0.79"
    assert recorded["metrics"][0]["model2"]["text"] == "0.76"
    assert "ipSAE" in recorded["omitted"]
    assert recorded["platform_ipsae_ranking"] is False
    assert recorded["bindcraft_rank"] == "1"
    assert shown["results"][0]["duration"] == (
        "Finished all designs. Script execution for 2 trajectories took: 0 hours, 7 minutes, 25 seconds"
    )
    assert shown["results"][1]["review"] is None
    assert _request_results(empty, root)["results"] == []
    assert "historical" not in json.dumps(shown)
    store = MemoryDesignJourneyStore()
    store.put(owned)
    store.put(empty)
    service = DesignJourneyService(SimpleNamespace(), store=store, attempt_root=root)
    status, body = handle_design_journey(
        service, "GET", "/api/design-journeys/by-request/request_owned/results", b""
    )
    assert status == 200
    assert body["results"][0]["attempt_id"] == "attempt.linked01"
    status, body = handle_design_journey(
        service, "GET", "/api/design-journeys/by-request/request_empty/results", b""
    )
    assert status == 200 and body["results"] == []
    monkeypatch.setenv("CELLERTZ_ATTEMPT_CATALOG", str(tmp_path / "unused-catalog"))
    monkeypatch.setenv("CELLERTZ_JOURNEY_ATTEMPT_ROOT", str(root))
    served = handle_attempt_get("/api/attempts/attempt.linked01/structures/model1.pdb")
    assert isinstance(served, StructureArtifact)
    assert served.body == model1
    refused = handle_attempt_get("/api/attempts/attempt.historical/structures/model1.pdb")
    assert isinstance(refused, tuple) and refused[0] == 404
