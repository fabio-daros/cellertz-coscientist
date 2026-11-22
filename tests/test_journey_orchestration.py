"""Automatic post-generation runs only for a new saved dispatch."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from cellertz.application.design_journey import (
    DesignJourneyService,
    SQLiteDesignJourneyStore,
    handle_design_journey,
)
from cellertz.application.journey_orchestration import JourneyOrchestrationWorker
from tests.test_design_journey import (
    _ROUND10_DESIGN,
    _authorization,
    _launch,
    _ready_journey,
    _VerifiedReply,
)


class _RaiseScore:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _execution: object) -> object:
        self.calls += 1
        raise RuntimeError("scoring stopped")


def _deferred(tmp_path: Path, launcher: object, **kwargs: object) -> tuple[object, DesignJourneyService, dict]:
    plans, service, document = _ready_journey(tmp_path, launcher, **kwargs)  # type: ignore[arg-type]
    service.attach_orchestrator(JourneyOrchestrationWorker(service))
    _status, authorized = service.authorize_execution(document["journey_id"], _authorization(document))
    return plans, service, authorized


def test_deferred_launch_returns_before_the_launcher_and_claims_once(tmp_path: Path) -> None:
    launcher = _VerifiedReply(write_artifacts=False)
    plans, service, document = _deferred(tmp_path, launcher)
    status, body = service.launch_execution(document["journey_id"], _launch(document))
    assert status == 200
    assert launcher.calls == 0
    assert body["execution"]["attempt_id"].startswith("attempt.")
    assert body["execution"]["consumed"] is True
    assert body["execution"]["terminal"] is False
    assert body["rounds"][0]["post_generation"]["status"] == "queued"
    assert body["rounds"][0]["post_generation"]["generation_invoked"] is False
    repeated, repeated_body = service.launch_execution(document["journey_id"], _launch(document))
    assert repeated == 200
    assert repeated_body["execution"]["attempt_id"] == body["execution"]["attempt_id"]
    assert launcher.calls == 0
    assert service.continue_post_generation() is True
    assert launcher.calls == 1
    stored = service._store.get(document["journey_id"])
    assert stored["post_generation"]["status"] == "unresolved"
    assert stored["post_generation"]["generation_invoked"] is True
    assert stored["execution"]["accepted_candidate"] is False
    assert service.continue_post_generation() is False
    assert launcher.calls == 1
    plans.close()
    service.close()


def test_concurrent_launches_keep_one_attempt(tmp_path: Path) -> None:
    launcher = _VerifiedReply(write_artifacts=False)
    plans, service, document = _deferred(
        tmp_path, launcher, journey_store=SQLiteDesignJourneyStore(tmp_path / "journeys.sqlite")
    )
    barrier = threading.Barrier(2)
    results: list[tuple[int, dict]] = []

    def _once() -> None:
        barrier.wait()
        results.append(service.launch_execution(document["journey_id"], _launch(document)))

    threads = [threading.Thread(target=_once), threading.Thread(target=_once)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert launcher.calls == 0
    identities = {body["execution"]["attempt_id"] for status, body in results if status == 200}
    assert identities == {results[0][1]["execution"]["attempt_id"]}
    assert service.continue_post_generation() is True
    assert launcher.calls == 1
    assert service.continue_post_generation() is False
    plans.close()
    service.close()


def test_accepted_artifacts_use_the_real_calculator_and_keep_a_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _VerifiedReply(write_artifacts=True)
    catalog = tmp_path / "results"
    plans, service, document = _deferred(tmp_path, launcher, results_catalog=catalog)
    status, _body = service.launch_execution(document["journey_id"], _launch(document))
    assert status == 200

    def _reject(_execution: object, _catalog: object) -> dict[str, object]:
        return {"status": "outputs_rejected", "reasons": ["destination_conflict"]}

    monkeypatch.setattr(
        "cellertz.application.journey_specialist_progress._publish_catalog",
        _reject,
    )
    assert service.continue_post_generation() is True
    assert launcher.calls == 1
    stored = service._store.get(document["journey_id"])
    scoring = next(item for item in stored["rounds"][0]["specialist_stages"] if item["stage_id"] == "scoring")
    assert scoring["status"] == "completed"
    assert scoring["agent_executed"] is True
    assert scoring["output"]["score_source"] == "ProteinIpSAECalculator"
    assert scoring["output"]["ranking_score"] != pytest.approx(0.42)
    assert scoring["output"]["ipsae_provenance"]["model_aggregation"] == "mean"
    assert scoring["started_at"] != scoring["completed_at"]
    ranking = next(item for item in stored["rounds"][0]["specialist_stages"] if item["stage_id"] == "ranking")
    assert ranking["started_at"] >= scoring["completed_at"]
    sequences = [item["sequence"] for item in stored["rounds"][0]["specialist_events"]]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert stored["rounds"][0]["results_publication"]["status"] == "outputs_rejected"
    assert stored["post_generation"]["status"] == "failed"
    assert stored["post_generation"]["phase"] == "publication"
    assert stored["execution"]["scientific_validation_established"] is False
    plans.close()
    service.close()


def test_scoring_failure_does_not_hide_the_attempt_or_rank(tmp_path: Path) -> None:
    launcher = _VerifiedReply(write_artifacts=True)
    scorer = _RaiseScore()
    plans, service, document = _deferred(tmp_path, launcher, specialist_scorer=scorer)
    service.launch_execution(document["journey_id"], _launch(document))
    assert service.continue_post_generation() is True
    stored = service._store.get(document["journey_id"])
    scoring = next(item for item in stored["rounds"][0]["specialist_stages"] if item["stage_id"] == "scoring")
    ranking = next(item for item in stored["rounds"][0]["specialist_stages"] if item["stage_id"] == "ranking")
    assert scorer.calls == 1
    assert scoring["status"] == "failed"
    assert ranking["status"] == "blocked"
    assert stored["post_generation"]["status"] == "failed"
    assert stored["post_generation"]["phase"] == "specialist"
    assert service.continue_post_generation() is False
    assert scorer.calls == 1
    plans.close()
    service.close()


def test_restart_does_not_relaunch_and_leaves_history_alone(tmp_path: Path) -> None:
    launcher = _VerifiedReply(write_artifacts=False)
    database = tmp_path / "journeys.sqlite"
    plans, service, document = _deferred(
        tmp_path, launcher, journey_store=SQLiteDesignJourneyStore(database)
    )
    journey_id = document["journey_id"]
    service.launch_execution(journey_id, _launch(document))
    assert launcher.calls == 0
    service.recover_automatic_dispatches()
    assert launcher.calls == 0
    assert service._store.get(journey_id)["post_generation"]["status"] == "queued"
    claimed = service._store.claim_where(
        lambda item: item.get("post_generation", {}).get("status") == "queued",
        lambda item: item["post_generation"].update({"status": "running", "generation_invoked": True, "owner": "old"}),
    )
    assert claimed is not None
    restarted = DesignJourneyService(plans, store=SQLiteDesignJourneyStore(database), attempt_root=service._attempt_root)
    restarted.recover_automatic_dispatches()
    recovered = restarted._store.get(journey_id)
    assert launcher.calls == 0
    assert recovered["post_generation"]["status"] == "unresolved"
    historical = json.loads(json.dumps(recovered))
    historical["journey_id"] = "journey_historical"
    historical["request_id"] = "request_historical"
    historical.pop("post_generation")
    historical["rounds"][0].pop("post_generation", None)
    restarted._store.put(historical)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    status, _payload = handle_design_journey(restarted, "GET", f"/api/design-journeys/{journey_id}", b"")
    assert status == 200
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert restarted.continue_post_generation() is False
    assert launcher.calls == 0
    plans.close()
    service.close()
    restarted.close()


def test_interrupted_scoring_is_not_replayed(tmp_path: Path) -> None:
    launcher = _VerifiedReply(write_artifacts=True)
    scorer = _RaiseScore()
    plans, service, document = _deferred(tmp_path, launcher, specialist_scorer=scorer)
    journey_id = document["journey_id"]
    service.launch_execution(journey_id, _launch(document))
    saved = service._store.get(journey_id)
    saved["execution"]["status"] = "verified"
    saved["execution"]["terminal"] = True
    saved["execution"]["accepted_candidate"] = True
    saved["post_generation"]["generation_invoked"] = True
    saved["post_generation"]["status"] = "running"
    saved["rounds"][0]["specialist_stages"] = [
        {
            "stage_id": "scoring",
            "status": "running",
            "started_at": "2026-10-25T15:00:00+00:00",
            "agent_executed": True,
        }
    ]
    service._save(saved)
    service.recover_automatic_dispatches()
    stored = service._store.get(journey_id)
    scoring = stored["rounds"][0]["specialist_stages"][0]
    assert launcher.calls == 0
    assert scorer.calls == 0
    assert scoring["status"] == "unresolved"
    assert stored["post_generation"]["status"] == "unresolved"
    assert service.continue_post_generation() is False
    plans.close()
    service.close()


def test_budget_exhaustion_blocks_specialists_without_a_score(tmp_path: Path) -> None:
    class _Budget:
        calls = 0

        def launch(self, request: object) -> dict[str, object]:
            del request
            self.calls += 1
            return {
                "status": "budget_exhausted",
                "launched": True,
                "accepted_candidate": False,
                "successful_candidate_generation": False,
                "termination_reason": "attempted_trajectory_budget_exhausted",
            }

    launcher = _Budget()
    scorer = _RaiseScore()
    plans, service, document = _deferred(tmp_path, launcher, specialist_scorer=scorer)
    service.launch_execution(document["journey_id"], _launch(document))
    assert service.continue_post_generation() is True
    stored = service._store.get(document["journey_id"])
    assert launcher.calls == 1
    assert scorer.calls == 0
    assert stored["execution"]["accepted_candidate"] is False
    scoring = next(item for item in stored["rounds"][0]["specialist_stages"] if item["stage_id"] == "scoring")
    assert scoring["status"] == "blocked"
    assert stored["post_generation"]["status"] == "blocked"
    assert _ROUND10_DESIGN not in json.dumps(stored["execution"].get("candidate_id"))
    plans.close()
    service.close()


def _decide(status: str, **extra: object) -> str:
    from cellertz.application.journey_orchestration import follow_up_decision

    return follow_up_decision({"status": status, "terminal": True, **extra})


def test_a_ranked_launch_is_routed_to_the_specialists() -> None:
    """mBER's terminal status is "ranked"; only BindCraft's "verified" was routed.

    Everything else fell through to "failed", so a run that screened and
    ranked a real cohort on the card was recorded as a failed dispatch and no
    specialist was ever dispatched.
    """

    assert _decide("ranked", accepted_candidate=True) == "specialists"
    assert _decide("verified", accepted_candidate=True) == "specialists"


def test_a_terminal_status_that_accepted_nothing_is_unresolved_not_failed() -> None:
    """The launch finished its pipeline; what it produced is the open question."""

    assert _decide("ranked") == "unresolved"
    assert _decide("verified") == "unresolved"


def test_mber_accepting_no_design_is_a_clean_outcome() -> None:
    """Its own filter rejecting every binder is a result, not a failure.

    Specialists run and record the absence, which is what budget exhaustion
    already did.
    """

    assert _decide("no_accepted_design") == "specialists"
    assert _decide("budget_exhausted") == "specialists"


def test_a_real_failure_is_still_a_failure() -> None:
    for status in ("launch_failed", "failed", "outputs_rejected", "launcher_exception", "vram_insufficient"):
        assert _decide(status) == "failed", status
    assert _decide("ranked", terminal=False) == "running"


def test_a_ranked_execution_is_left_alone_after_a_restart() -> None:
    """The recovery guard listed only BindCraft's clean terminal statuses.

    A finished mBER launch would have been reported as "the GPU launch was not
    started again after restart" and its execution overwritten as unresolved.
    """

    from cellertz.application.journey_orchestration import recover_document

    def saved(status: str) -> dict:
        return {
            "journey_id": "journey_restart",
            "post_generation": {
                "dispatch_id": "dispatch_1",
                "automatic": True,
                "status": "running",
                "phase": "generation",
                "generation_invoked": True,
                "events": [],
            },
            "execution": {"status": status, "terminal": True, "accepted_candidate": True},
            "rounds": [],
            "stages": [],
            "events": [],
        }

    for status in ("ranked", "no_accepted_design", "verified", "budget_exhausted"):
        assert recover_document(saved(status)) is None, status
    recovered = recover_document(saved("some_other_status"))
    assert recovered is not None
    assert recovered["post_generation"]["status"] == "unresolved"
