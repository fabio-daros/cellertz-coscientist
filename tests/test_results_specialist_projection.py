"""Results reads saved specialist evidence without scoring or rewriting the review."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cellertz.application.attempt_evidence import load_catalog
from cellertz.application.attempt_inspection import inspect_attempt
from cellertz.application.conversation_transport import configured_handler
from cellertz.application.design_journey import DesignJourneyService, SQLiteDesignJourneyStore
from cellertz.domains.protein_design.adapters.bindcraft import candidate_id_for_design
from tests.test_attempt_inspection import _invoke, _publish
from tests.test_bindcraft_output_ingestion import _ATTEMPT, _DESIGN

_SCORE = 0.424242
_OTHER_SCORE = 0.9
_PLAN = "plan_test"
_REVISION = 4
_DIGEST = "digest-executed"


def test_exact_attempt_links_a_candidate_score_and_leaves_the_review(tmp_path: Path, monkeypatch) -> None:
    catalog, review, hashes = _catalog(tmp_path, monkeypatch)
    before = review.read_bytes()
    other = _document("attempt.other", "OTHER", candidate_id_for_design("OTHER"), hashes, score=_OTHER_SCORE)
    other["journey_id"] = "journey_other"
    other["request_id"] = "request_other"
    documents = [_document(_ATTEMPT, _DESIGN, candidate_id_for_design(_DESIGN), hashes), other]
    status, payload = inspect_attempt(
        catalog, _ATTEMPT, {"candidate": _DESIGN, "model": "model1"}, journey_documents=documents
    )
    assert status == 200
    saved = payload["saved_specialist_processing"]
    assert saved["status"] == "linked"
    assert saved["journey_id"] == "journey_recovered"
    assert saved["ranking_score"] == _SCORE
    assert saved["applies_to_each_model"] is False
    assert saved["per_model_ranking_scores"] == "not_recorded"
    assert saved["critique_agreement"] == "unknown"
    assert saved["prioritization_bucket"] == "core"
    assert saved["rank"] == 1
    assert "comparative superiority" in saved["rank_note"]
    assert saved["aggregation"]["model_aggregation"] == "not_recorded"
    assert saved["attempted_trajectory_budget"]["compliance_established"] is True
    assert saved["attempted_trajectory_budget"]["establishes_execution_compliance"] is False
    assert payload["ranking_score"] == "not_recorded"
    assert payload["execution_compliance_established"] is False
    assert payload["scientific_validation_established"] is False
    assert "i_pTM" in {row["metric"] for row in payload["metrics"]}
    assert _SCORE not in _numbers(payload["models"])
    assert _OTHER_SCORE not in _numbers(saved)
    assert review.read_bytes() == before


def test_candidate_model_and_plan_mismatches_stay_explicit(tmp_path: Path, monkeypatch) -> None:
    catalog, _review, hashes = _catalog(tmp_path, monkeypatch)
    candidate = candidate_id_for_design(_DESIGN)
    bare = _document(_ATTEMPT, _DESIGN, _DESIGN, hashes)
    bare_status, bare_payload = inspect_attempt(
        catalog, _ATTEMPT, {"candidate": _DESIGN, "model": "model1"}, journey_documents=[bare]
    )
    assert bare_status == 200
    assert bare_payload["saved_specialist_processing"]["status"] == "mismatched"
    assert bare_payload["saved_specialist_processing"]["ranking_score"] == "not_recorded"
    changed = dict(hashes)
    changed["model2"] = "0" * 64
    hashed = _document(_ATTEMPT, _DESIGN, candidate, changed)
    hash_status, hash_payload = inspect_attempt(
        catalog, _ATTEMPT, {"candidate": _DESIGN, "model": "model1"}, journey_documents=[hashed]
    )
    assert hash_status == 200
    assert hash_payload["saved_specialist_processing"]["status"] == "mismatched"
    assert hash_payload["saved_specialist_processing"]["ranking_score"] == "not_recorded"
    stale = _document(_ATTEMPT, _DESIGN, candidate, hashes, digest="digest-stale")
    stale["execution"]["digest"] = _DIGEST
    stale_status, stale_payload = inspect_attempt(
        catalog, _ATTEMPT, {"candidate": _DESIGN, "model": "model1"}, journey_documents=[stale]
    )
    assert stale_status == 200
    assert stale_payload["saved_specialist_processing"]["status"] == "mismatched"
    assert stale_payload["saved_specialist_processing"]["ranking_score"] == "not_recorded"


def test_missing_ambiguous_and_superseded_records_do_not_invent_a_score(
    tmp_path: Path, monkeypatch
) -> None:
    catalog, _review, hashes = _catalog(tmp_path, monkeypatch)
    query = {"candidate": _DESIGN, "model": "model2"}
    missing, missing_payload = inspect_attempt(catalog, _ATTEMPT, query, journey_documents=None)
    assert missing == 200
    assert missing_payload["saved_specialist_processing"]["status"] == "not_recorded"
    assert missing_payload["ranking_score"] == "not_recorded"
    empty, empty_payload = inspect_attempt(catalog, _ATTEMPT, query, journey_documents=[])
    assert empty == 200
    assert empty_payload["saved_specialist_processing"]["detail"].startswith("No saved journey")
    first = _document(_ATTEMPT, _DESIGN, candidate_id_for_design(_DESIGN), hashes)
    second = _document(_ATTEMPT, _DESIGN, candidate_id_for_design(_DESIGN), hashes, score=_OTHER_SCORE)
    second["journey_id"] = "journey_second"
    second["request_id"] = "request_second"
    ambiguous, ambiguous_payload = inspect_attempt(
        catalog, _ATTEMPT, query, journey_documents=[first, second]
    )
    assert ambiguous == 200
    assert ambiguous_payload["saved_specialist_processing"]["status"] == "ambiguous"
    assert ambiguous_payload["saved_specialist_processing"]["ranking_score"] == "not_recorded"
    current = _document(_ATTEMPT, _DESIGN, candidate_id_for_design(_DESIGN), hashes)
    current["rounds"][0]["outcome_corrections"] = [
        {
            "kind": "accepted_launch_outcome_correction",
            "attempt_id": _ATTEMPT,
            "superseded_specialist_stages": [
                {"stage_id": "scoring", "output": {"ranking_score": 0.11}}
            ],
        }
    ]
    linked, linked_payload = inspect_attempt(catalog, _ATTEMPT, query, journey_documents=[current])
    saved = linked_payload["saved_specialist_processing"]
    assert linked == 200
    assert saved["ranking_score"] == _SCORE
    assert saved["superseded"][0]["ranking_score"] == 0.11
    assert saved["superseded"][0]["used_as_current_score"] is False


def test_sqlite_reopen_and_get_do_not_mutate_or_score(tmp_path: Path, monkeypatch) -> None:
    catalog, review, hashes = _catalog(tmp_path, monkeypatch)
    before_review = review.read_bytes()
    document = _document(_ATTEMPT, _DESIGN, candidate_id_for_design(_DESIGN), hashes)
    database = tmp_path / "journeys.sqlite"
    store = SQLiteDesignJourneyStore(database)
    store.put(document)
    store.close()
    file_before = hashlib.sha256(database.read_bytes()).hexdigest()
    reopened = SQLiteDesignJourneyStore(database)
    calls: list[str] = []
    service = DesignJourneyService(object(), store=reopened, specialist_scorer=lambda *_a, **_k: calls.append("score"))
    stored = service.saved_documents()
    status, payload = inspect_attempt(
        catalog,
        _ATTEMPT,
        {"candidate": _DESIGN, "model": "model1"},
        journey_documents=stored,
    )
    assert status == 200
    assert payload["saved_specialist_processing"]["ranking_score"] == _SCORE
    handler = configured_handler(object(), journeys=service)
    code, body = _invoke(
        handler,
        f"/api/attempts/{_ATTEMPT}/inspection?candidate={_DESIGN}&model=model2",
    )
    assert code == 200
    assert json.loads(body)["saved_specialist_processing"]["ranking_score"] == _SCORE
    assert calls == []
    assert reopened.get(document["journey_id"]) == document
    assert hashlib.sha256(database.read_bytes()).hexdigest() == file_before
    assert review.read_bytes() == before_review
    manual = catalog["attempt.2LUTUOX4"]
    manual_status, manual_payload = inspect_attempt(
        catalog,
        "attempt.2LUTUOX4",
        {"candidate": manual["record"]["design"], "model": "model1"},
        journey_documents=stored,
    )
    assert manual_status == 200
    assert manual_payload["ranking_score"] == "not_recorded"
    assert manual_payload["saved_specialist_processing"]["status"] == "not_recorded"
    assert manual_payload["saved_specialist_processing"]["ranking_score"] == "not_recorded"
    reopened.close()


def test_a_trace_without_a_question_is_not_a_coordinator_trace(tmp_path: Path, monkeypatch) -> None:
    catalog, _review, _hashes = _catalog(tmp_path, monkeypatch)

    def traces(_attempt_id: str) -> list[dict[str, object]]:
        return [
            {"trace_id": "blank", "source_query": "  ", "attempt_id": _ATTEMPT, "steps": []},
            {
                "trace_id": "asked",
                "source_query": "What is recorded?",
                "attempt_id": _ATTEMPT,
                "steps": [],
            },
        ]

    _status, payload = inspect_attempt(
        catalog,
        _ATTEMPT,
        {"candidate": _DESIGN, "model": "model1"},
        traces_for=traces,
        journey_documents=[],
    )
    assert [item["trace_id"] for item in payload["coordinator_traces"]] == ["asked"]
    assert payload["coordinator_trace_note"] == (
        "Records without a question are not coordinator traces."
    )
    _blank_status, blank = inspect_attempt(
        catalog,
        _ATTEMPT,
        {"candidate": _DESIGN, "model": "model1"},
        traces_for=lambda _attempt: [traces(_attempt)[0]],
        journey_documents=[],
    )
    assert blank["coordinator_traces"] == []
    assert "without a question" in blank["coordinator_trace_note"]


def test_blocked_scoring_does_not_report_a_calculated_score(tmp_path: Path, monkeypatch) -> None:
    catalog, _review, hashes = _catalog(tmp_path, monkeypatch)
    document = _document(_ATTEMPT, _DESIGN, candidate_id_for_design(_DESIGN), hashes)
    scoring = document["rounds"][0]["specialist_stages"][0]
    scoring["status"] = "blocked"
    scoring["agent_executed"] = False
    scoring["output"] = {"ranking_score": _SCORE}
    _status, payload = inspect_attempt(
        catalog,
        _ATTEMPT,
        {"candidate": _DESIGN, "model": "model1"},
        journey_documents=[document],
    )
    saved = payload["saved_specialist_processing"]
    assert saved["status"] == "recorded_without_score"
    assert saved["ranking_score"] == "not_recorded"
    assert saved["detail"] == "Scoring did not run. No ranking_score was calculated."


def _catalog(tmp_path: Path, monkeypatch) -> tuple[dict, Path, dict[str, str]]:
    _publish(tmp_path, monkeypatch)
    catalog = load_catalog()
    review = tmp_path / _ATTEMPT / "review.json"
    hashes = {
        model_id: model["expected_sha256"]
        for model_id, model in catalog[_ATTEMPT]["models"].items()
    }
    return catalog, review, hashes


def _document(
    attempt_id: str,
    design: str,
    candidate_id: str,
    hashes: dict[str, str],
    *,
    score: float = _SCORE,
    digest: str = _DIGEST,
) -> dict:
    identity = {"design": design, "candidate_id": candidate_id, "execution_id": attempt_id}
    execution = {
        "attempt_id": attempt_id,
        "candidate_id": candidate_id,
        "candidate_identity": identity,
        "plan_id": _PLAN,
        "revision": _REVISION,
        "digest": digest,
        "budget_compliance_established": True,
        "budget_usage": {"requested_attempted_trajectories": 4, "attempted_trajectories": 1},
        "termination_reason": "successful_early_completion",
        "scientific_validation_established": False,
    }

    def stage(stage_id: str, output: dict, kind: str = "deterministic") -> dict:
        return {
            "stage_id": stage_id,
            "status": "completed",
            "agent_executed": True,
            "implementation": stage_id,
            "implementation_kind": kind,
            "evidence_references": [
                {
                    "attempt_id": attempt_id,
                    "candidate_id": candidate_id,
                    "plan_id": _PLAN,
                    "revision": _REVISION,
                    "digest": digest,
                }
            ],
            "output": output,
        }

    models = [{"model_id": model_id, "pdb_sha256": digest} for model_id, digest in hashes.items()]
    return {
        "journey_id": "journey_recovered",
        "request_id": "request_recovered",
        "active_round_id": "round_recovered",
        "execution": execution,
        "rounds": [
            {
                "round_id": "round_recovered",
                "specialist_stages": [
                    stage(
                        "scoring",
                        {
                            "ranking_score": score,
                            "ranking_signal": "ipsae",
                            "score_source": "ProteinIpSAECalculator",
                            "model_ids": ["1", "2"],
                            "models": models,
                        },
                        "structural_scoring",
                    ),
                    stage("ranking", {"rank": 1, "candidate_id": candidate_id}),
                    stage("critique", {"agreement": None, "candidate_id": candidate_id}),
                    stage(
                        "prioritization",
                        {
                            "selection_bucket": "core",
                            "selection_reason": "The candidate is in the core set.",
                        },
                    ),
                ],
                "outcome_corrections": [],
            }
        ],
    }


def _numbers(value: object) -> list[float]:
    found: list[float] = []

    def walk(item: object) -> None:
        if isinstance(item, bool):
            return
        if isinstance(item, (int, float)):
            found.append(float(item))
            return
        if isinstance(item, dict):
            for child in item.values():
                walk(child)
            return
        if isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found
