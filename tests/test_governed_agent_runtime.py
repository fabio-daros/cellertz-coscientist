import hashlib
import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.governed_agent_runtime import (
    CONTRACT_ORDER,
    CONTRACT_VERSIONS,
    SUMMARY,
    GovernedProteinAgentRuntime,
    StagePlan,
    default_specialists,
    stage_ids,
    versions,
    workflow_bytes_match,
    workflow_observed_at,
)
from cellertz.application.scientific_authorization import binding_hash_for
from cellertz.application.scientific_run import (
    FAILED_TEXT,
    ExecutorArtifact,
    ScientificExecutionResult,
    ScientificRun,
    ScientificRunEvent,
    ScientificRunStoreError,
    _canonical,
)
from cellertz.application.scientific_run_config import (
    GOVERNED_FIXTURE,
    resolve_scientific_run_execution,
)
from cellertz.application.scientific_run_store import SQLiteScientificRunStore
from cellertz.application.scientific_run_transport import (
    RUNS_PATH,
    ScientificArtifactResponse,
    handle_scientific_run,
)
from cellertz.conversation.__main__ import main
from cellertz.domains.protein_design.agents import supervisor as supervisor_contract
from cellertz.domains.protein_design.agents.supervisor import (
    CRITIQUE_VERSION,
    GENERATION_VERSION,
    PRIORITIZATION_VERSION,
    RANKING_VERSION,
    SCORING_VERSION,
    STAGE_ORDER,
    ProteinSupervisorV1,
)
from tests.test_scientific_authorization import SESSION, WHEN, _ready_readiness
from tests.test_scientific_run import _service

ROOT = Path(__file__).parents[1]
_LEAKS = ("Traceback", "/tmp/", "/Users/", "token=", "SELECT", "sqlite", "subprocess", "argv")


def test_adapter_matches_the_supervisor_contract() -> None:
    runtime = GovernedProteinAgentRuntime()
    assert stage_ids() == STAGE_ORDER == CONTRACT_ORDER
    assert (
        versions()
        == CONTRACT_VERSIONS
        == {
            "generation": GENERATION_VERSION,
            "scoring": SCORING_VERSION,
            "ranking": RANKING_VERSION,
            "critique": CRITIQUE_VERSION,
            "prioritization": PRIORITIZATION_VERSION,
        }
    )
    names = [type(runtime.specialists[stage]).__name__ for stage in CONTRACT_ORDER]
    assert names == [
        "GenerationPlanningSpecialist",
        "ScoringPlanningSpecialist",
        "RankingPlanningSpecialist",
        "CritiquePlanningSpecialist",
        "PrioritizationPlanningSpecialist",
    ]
    assert len(set(names)) == 5
    assert not hasattr(runtime, "calls")
    assert not hasattr(runtime.specialists["generation"], "calls")
    assert not hasattr(runtime.specialists["generation"], "handoffs")
    assert not isinstance(runtime, ProteinSupervisorV1)
    source = (ROOT / "src/cellertz/application/governed_agent_runtime.py").read_text()
    for name in (
        "ProteinSupervisorV1",
        "LocalProteinRunStarter",
        "BindCraft",
        "mBER",
        "RFdiffusion",
        "Apptainer",
        "Docker",
        "subprocess",
        "ScientificRunMemory",
        "SecondBrain",
        "Databricks",
    ):
        assert name not in source


def test_stages_run_once_and_digests_are_stable(tmp_path: Path) -> None:
    runtime, seen, spies = _watched()
    _service_unused, store, _authorizations, _granted, created = _start(tmp_path, runtime)
    assert seen == []
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    first = runtime.execute(running)
    second = GovernedProteinAgentRuntime().execute(running)
    later = "2026-10-12T16:00:01+00:00"
    object.__setattr__(running, "updated_at", later)
    object.__setattr__(running.events[-1], "recorded_at", later)
    third = GovernedProteinAgentRuntime().execute(running)
    assert seen == list(CONTRACT_ORDER)
    assert [item.kind for item in first.artifacts] == ["execution_record", "workflow_trace"]
    assert _digests(first) == _digests(second) == _digests(third)
    parsed = json.loads(first.artifacts[1].body)
    assert parsed["scientific_candidate_generated"] is False
    assert parsed["scientific_validation"] is False
    assert parsed["ranked"] is False
    assert parsed["accepted"] is False
    assert [stage["stage_id"] for stage in parsed["stages"]] == list(CONTRACT_ORDER)
    upstream = running.binding_hash
    for sequence, stage in enumerate(parsed["stages"], start=1):
        assert stage["sequence"] == sequence
        assert stage["upstream_digest"] == upstream
        assert stage["summary"] == SUMMARY
        assert stage["attempt"] == running.attempt
        upstream = stage["output_digest"]
    for stage_id in CONTRACT_ORDER:
        assert spies[stage_id].calls == 1
        assert spies[stage_id].handoffs[0].stage_id == stage_id
    assert spies["scoring"].handoffs[0].upstream_digest == parsed["stages"][0]["output_digest"]
    encoded = first.artifacts[1].body.decode()
    for needle in _LEAKS:
        assert needle not in encoded
    assert created.run.run_id == running.run_id
    store.close()


def test_worker_runs_the_trace_outside_the_request(tmp_path: Path) -> None:
    runtime, seen, _spies = _watched()
    service, store, _authorizations, granted, created = _start(tmp_path, runtime)
    _status, payload = handle_scientific_run(
        service,
        "POST",
        RUNS_PATH,
        json.dumps(
            {
                "authorization_id": granted.authorization.authorization_id,
                "idempotency_key": "again",
            }
        ).encode(),
        SESSION,
    )
    assert payload["run"]["run_id"] == created.run.run_id
    assert seen == []
    finished = service.process_next()
    assert finished.status == "completed"
    assert finished.scientific_candidate_generated is False
    assert seen == list(CONTRACT_ORDER)
    before = list(seen)
    assert service.read(session_id=SESSION, run_id=finished.run_id).run.status == "completed"
    assert service.list_runs(session_id=SESSION).runs[0].run_id == finished.run_id
    artifact = next(item for item in finished.artifacts if item.kind == "workflow_trace")
    loaded = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    )
    assert loaded.media_type == "application/json"
    assert loaded.filename == "scientific-agent-workflow.json"
    assert seen == before
    again = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="other",
    )
    assert again.run.run_id == finished.run_id
    assert seen == before
    store.close()


def test_executor_runs_outside_the_store_transaction(tmp_path: Path) -> None:
    service, store, _authorizations, granted, _created = _start(tmp_path, None, backend="sqlite")

    class Watch(GovernedProteinAgentRuntime):
        def execute(self, run):
            self.inside = store._conn.in_transaction
            self.seen = run.status
            return super().execute(run)

    runtime = Watch()
    service._executor = runtime
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    finished = service.process_next()
    assert finished.status == "completed"
    assert runtime.inside is False
    assert runtime.seen == "running"
    store.close()


def test_concurrent_workers_run_one_attempt(tmp_path: Path) -> None:
    runtime, seen, _spies = _watched()
    service, store, _authorizations, granted, _created = _start(tmp_path, runtime, backend="sqlite")
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    found: list = []

    def once() -> None:
        found.append(service.process_next())

    threads = [threading.Thread(target=once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    completed = [item for item in found if item is not None and item.status == "completed"]
    assert len(completed) == 1
    assert seen == list(CONTRACT_ORDER)
    store.close()


def test_stage_failure_and_malformed_output_stop_the_workflow(tmp_path: Path) -> None:
    class Stop:
        stage_id = "scoring"

        def plan(self, handoff):
            raise RuntimeError("secret /tmp/traceback token= SELECT")

    runtime, seen, spies = _watched({"scoring": Stop()})
    service, store, _authorizations, _granted, _created = _start(tmp_path, runtime)
    failed = service.process_next()
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    assert failed.artifacts == ()
    assert seen == ["generation"]
    assert spies["ranking"].calls == 0

    class Bad:
        stage_id = "ranking"

        def plan(self, handoff):
            forged = StagePlan(status="completed", summary=SUMMARY)
            object.__setattr__(forged, "summary", "/tmp/traceback token= SELECT")
            return forged

    other, other_seen, other_spies = _watched({"ranking": Bad()})
    service._executor = other
    refused = service.retry(session_id=SESSION, run_id=failed.run_id, idempotency_key="retry-bad")
    assert refused.run.attempt == 2
    second = service.process_next()
    assert second.status == "failed"
    assert second.artifacts == ()
    assert other_seen == ["generation", "scoring"]
    assert other_spies["critique"].calls == 0
    body = json.dumps(service.read(session_id=SESSION, run_id=failed.run_id).to_dict())
    for needle in ("/tmp/", "token=", "SELECT", "Traceback"):
        assert needle not in body
    store.close()


def test_retry_keeps_one_run_and_isolates_the_trace(tmp_path: Path) -> None:
    class Flaky:
        stage_id = "scoring"

        def __init__(self) -> None:
            self.calls = 0

        def plan(self, handoff):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("secret /tmp/token=")
            return StagePlan(status="completed", summary=SUMMARY)

    flaky = Flaky()
    runtime, _seen, spies = _watched({"scoring": flaky})
    service, store, _authorizations, _granted, created = _start(tmp_path, runtime, backend="sqlite")
    first = service.process_next()
    queued = service.retry(session_id=SESSION, run_id=first.run_id, idempotency_key="retry-a")
    second = service.process_next()
    later = service.retry(session_id=SESSION, run_id=first.run_id, idempotency_key="retry-b")
    third = service.process_next()
    replay = service.retry(session_id=SESSION, run_id=first.run_id, idempotency_key="retry-a")
    assert queued.run.attempt == 2
    assert second.status == "failed" and second.attempt == 2
    assert later.run.attempt == 3
    assert third.status == "completed" and third.attempt == 3
    assert third.run_id == created.run.run_id
    assert replay.run.attempt == 2 and replay.run.status == "failed"
    assert replay.run.artifacts == ()
    trace = next(item for item in third.artifacts if item.kind == "workflow_trace")
    assert trace.attempt == 3
    loaded = service.read_artifact(
        session_id=SESSION, run_id=third.run_id, artifact_id=trace.artifact_id
    )
    assert json.loads(loaded.content)["attempt"] == 3
    missing = service.read_artifact(
        session_id=SESSION,
        run_id=third.run_id,
        artifact_id=f"artifact_{third.run_id}_2_workflow_trace",
    )
    assert missing.status == "artifact_absent"
    assert flaky.calls == 3
    assert spies["generation"].calls == 3
    assert spies["prioritization"].calls == 1
    store.close()


def test_binding_changes_stop_before_the_first_stage(tmp_path: Path) -> None:
    runtime, seen, _spies = _watched()
    service, store, authorizations, granted, created = _start(tmp_path, runtime)
    queued = service.read(session_id=SESSION, run_id=created.run.run_id).run
    record = authorizations._store.read(SESSION, granted.authorization.authorization_id)
    original = record
    for field, value in (
        ("approval_id", "approval_other"),
        ("image_digest", "sha256:" + "12" * 32),
        ("manifest_digest", "12" * 32),
        ("readiness_observation_id", "observation_" + "12" * 32),
        ("readiness_observed_at", "2026-10-12T18:00:00+00:00"),
        ("request_digest", "12" * 32),
        ("request_id", "request_other"),
        ("request_revision", 9),
        ("runtime_name", "other-runtime"),
        ("runtime_version", "other-runtime-1"),
        ("tool_version", "other-tool-1"),
        ("profile_id", "other-profile"),
        ("tool_name", "other-tool"),
        ("session_id", "session_other"),
        ("target_scope", "CD22"),
    ):
        try:
            shifted = _shift(original, **{field: value})
        except ApplicationContractError:
            shifted = None
        if shifted is None:
            continue
        authorizations._store._records[original.authorization_id] = shifted
        assert service._bound(queued) is False
        authorizations._store._records[original.authorization_id] = original
    budget = dict(original.budget)
    for field, value in _budget_values(budget):
        changed = dict(original.budget)
        changed[field] = value
        try:
            shifted = _shift(original, budget=changed)
        except ApplicationContractError:
            shifted = None
        if shifted is None:
            continue
        authorizations._store._records[original.authorization_id] = shifted
        assert service._bound(queued) is False
        authorizations._store._records[original.authorization_id] = original
    assert service._bound(queued) is True
    authorizations._readiness = _ready_readiness(
        tmp_path / "shift", when=datetime(2026, 10, 12, 18, 0, tzinfo=WHEN.tzinfo)
    )[0]
    failed = service.process_next()
    assert failed.status == "failed"
    assert seen == []
    store.close()


def test_changed_budget_rejects_a_pending_result(tmp_path: Path) -> None:
    class Shift(GovernedProteinAgentRuntime):
        def __init__(self, store, authorization_id: str, spies) -> None:
            super().__init__(spies)
            self.store = store
            self.authorization_id = authorization_id

        def execute(self, run):
            result = super().execute(run)
            record = self.store._records[self.authorization_id]
            budget = dict(record.budget)
            budget["token_ceiling"] = 9 if budget["token_ceiling"] != 9 else 4
            self.store._records[self.authorization_id] = _shift(record, budget=budget)
            return result

    service, store, authorizations, granted, _created = _start(tmp_path, None)
    _runtime, seen, spies = _watched()
    runtime = Shift(authorizations._store, granted.authorization.authorization_id, spies)
    service._executor = runtime
    finished = service.process_next()
    assert finished.status == "failed"
    assert finished.artifacts == ()
    assert seen == list(CONTRACT_ORDER)
    assert service.read(session_id=SESSION, run_id=finished.run_id).run.text == FAILED_TEXT
    store.close()


def test_blocked_targets_do_not_enter_the_runtime(tmp_path: Path) -> None:
    runtime, seen, _spies = _watched()
    _service_unused, store, _authorizations, _granted, _created = _start(tmp_path, runtime)
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    for target in ("CD22", "TACI"):
        object.__setattr__(running, "target_scope", target)
        object.__setattr__(running, "binding_hash", binding_hash_for(running))
        assert runtime.execute(running).status == "failed"
        assert seen == []
    object.__setattr__(running, "target_scope", "PDL1")
    object.__setattr__(running, "profile_id", "other-profile")
    object.__setattr__(running, "binding_hash", binding_hash_for(running))
    assert runtime.execute(running).status == "failed"
    assert seen == []
    budget = dict(running.budget)
    budget["authorizes_execution"] = True
    object.__setattr__(running, "budget", budget)
    object.__setattr__(running, "profile_id", "pdl1-bindcraft-historical-v1")
    object.__setattr__(running, "binding_hash", binding_hash_for(running))
    assert runtime.execute(running).status == "failed"
    assert seen == []
    store.close()


def test_trace_bytes_fail_closed(tmp_path: Path) -> None:
    runtime = GovernedProteinAgentRuntime()
    service, store, _authorizations, _granted, _created = _start(
        tmp_path, runtime, backend="sqlite"
    )
    finished = service.process_next()
    artifact = next(item for item in finished.artifacts if item.kind == "workflow_trace")
    content = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    ).content
    assert workflow_bytes_match(content, finished) is True
    tampered = bytearray(content)
    tampered[20] = tampered[20] ^ 1
    assert workflow_bytes_match(bytes(tampered), finished) is False
    spaced = content.replace(b":", b": ", 1)
    assert workflow_bytes_match(spaced, finished) is False
    assert workflow_bytes_match(b"x" * 65537, finished) is False

    def rejected(statement: str, values: tuple) -> str:
        store._conn.execute(statement, values)
        reply = service.read_artifact(
            session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
        )
        encoded = json.dumps(reply.to_dict())
        for needle in (*_LEAKS, "SECRET"):
            assert needle not in encoded
        return reply.status

    assert (
        rejected(
            "UPDATE artifacts SET content = ? WHERE artifact_id = ?",
            ("SECRET-TEXT", artifact.artifact_id),
        )
        == "artifact_invalid"
    )
    assert (
        rejected(
            "UPDATE artifacts SET content = ?, size = ?, sha256 = ? WHERE artifact_id = ?",
            (b"Q" * 65537, 65537, "ab" * 32, artifact.artifact_id),
        )
        == "artifact_too_large"
    )
    store._conn.execute(
        "UPDATE artifacts SET content = ?, size = ?, sha256 = ? WHERE artifact_id = ?",
        (content, len(content), artifact.sha256, artifact.artifact_id),
    )
    assert (
        rejected(
            "UPDATE artifacts SET attempt = ? WHERE artifact_id = ?",
            (finished.attempt + 1, artifact.artifact_id),
        )
        == "artifact_attempt_unavailable"
    )
    store.close()
    reopened = SQLiteScientificRunStore(tmp_path / "scientific-runs.sqlite3")
    service._store = reopened
    again = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    )
    assert again.status == "artifact_attempt_unavailable"
    reopened.close()


def test_reopened_store_returns_the_trace_without_rerunning(tmp_path: Path) -> None:
    runtime = GovernedProteinAgentRuntime()
    service, store, authorizations, _granted, _created = _start(tmp_path, runtime, backend="sqlite")
    finished = service.process_next()
    artifact = next(item for item in finished.artifacts if item.kind == "workflow_trace")
    body = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    ).content
    path = tmp_path / "scientific-runs.sqlite3"
    store.close()
    reopened = SQLiteScientificRunStore(path)
    restored = type(service)(
        authorizations,
        store=reopened,
        executor=GovernedProteinAgentRuntime(),
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    loaded = restored.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    )
    assert loaded.content == body
    assert restored.process_next() is None
    assert not hasattr(restored._executor, "calls")
    reopened.close()


def test_governed_fixture_is_opt_in_and_enabled_still_refuses(monkeypatch) -> None:
    assert resolve_scientific_run_execution({}) == "disabled"
    assert (
        resolve_scientific_run_execution({"CELLERTZ_SCIENTIFIC_RUN_EXECUTION": GOVERNED_FIXTURE})
        == GOVERNED_FIXTURE
    )
    seen = {}

    def started(*_args, **kwargs):
        seen["name"] = type(kwargs["scientific_runs"]._executor).__name__
        seen["enabled"] = kwargs["scientific_runs"]._execution_enabled

    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_EXECUTION", GOVERNED_FIXTURE)
    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    main()
    assert seen == {"name": "GovernedProteinAgentRuntime", "enabled": True}
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_EXECUTION", "enabled")
    with pytest.raises(Exception) as blocked:
        main()
    assert blocked.value.code == "scientific_run_executor_unconfigured"


def test_executor_exception_stays_off_the_public_record(tmp_path: Path) -> None:
    class Boom(GovernedProteinAgentRuntime):
        def execute(self, run):
            self.entered = True
            raise RuntimeError("secret /tmp/traceback token= SELECT sqlite")

    runtime = Boom()
    service, store, _authorizations, _granted, _created = _start(tmp_path, runtime)
    failed = service.process_next()
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    encoded = json.dumps(service.read(session_id=SESSION, run_id=failed.run_id).to_dict())
    for needle in ("secret", "/tmp/", "token=", "SELECT", "sqlite", "Traceback"):
        assert needle not in encoded
    assert service.process_next() is None
    assert runtime.entered is True
    store.close()


def test_supervisor_drift_fails_closed(monkeypatch, tmp_path: Path) -> None:
    runtime, seen, _spies = _watched()
    _service_unused, store, _authorizations, _granted, _created = _start(tmp_path, runtime)
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    held = runtime.execute(running)
    body = held.artifacts[1].body
    assert seen == list(CONTRACT_ORDER)
    assert workflow_bytes_match(body, running) is True
    monkeypatch.setattr(
        supervisor_contract,
        "STAGE_ORDER",
        ("scoring", "generation", "ranking", "critique", "prioritization"),
    )
    drifted, drifted_seen, drifted_spies = _watched()
    assert drifted.execute(running).status == "failed"
    assert drifted_seen == []
    assert all(spy.calls == 0 for spy in drifted_spies.values())
    assert workflow_bytes_match(body, running) is True
    assert body == held.artifacts[1].body
    monkeypatch.setattr(supervisor_contract, "STAGE_ORDER", STAGE_ORDER)
    monkeypatch.setattr(supervisor_contract, "RANKING_VERSION", "protein_ranking_agent_v2")
    version_drift, version_seen, version_spies = _watched()
    assert version_drift.execute(running).status == "failed"
    assert version_seen == []
    assert all(spy.calls == 0 for spy in version_spies.values())
    assert workflow_bytes_match(body, running) is True
    store.close()


def test_historical_v1_trace_survives_a_supervisor_upgrade(monkeypatch, tmp_path: Path) -> None:
    runtime, seen, spies = _watched()
    service, store, authorizations, _granted, _created = _start(tmp_path, runtime, backend="sqlite")
    finished = service.process_next()
    artifact = next(item for item in finished.artifacts if item.kind == "workflow_trace")
    original = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    ).content
    parsed = json.loads(original)
    assert parsed["schema"] == "cellertz.governed_agent_workflow.v1"
    assert [stage["stage_id"] for stage in parsed["stages"]] == list(CONTRACT_ORDER)
    assert [stage["implementation_version"] for stage in parsed["stages"]] == [
        CONTRACT_VERSIONS[stage_id] for stage_id in CONTRACT_ORDER
    ]
    digest = hashlib.sha256(original).hexdigest()

    def stored_bytes() -> tuple[str, list[tuple[str, bytes]]]:
        document = store._conn.execute(
            "SELECT document FROM runs WHERE run_id = ?", (finished.run_id,)
        ).fetchone()["document"]
        rows = store._conn.execute(
            "SELECT artifact_id, content FROM artifacts WHERE run_id = ? ORDER BY artifact_id",
            (finished.run_id,),
        ).fetchall()
        return document, [(row["artifact_id"], bytes(row["content"])) for row in rows]

    before = stored_bytes()
    monkeypatch.setattr(
        supervisor_contract,
        "STAGE_ORDER",
        ("scoring", "generation", "ranking", "critique", "prioritization"),
    )
    monkeypatch.setattr(supervisor_contract, "RANKING_VERSION", "protein_ranking_agent_v2")
    listed = handle_scientific_run(service, "GET", RUNS_PATH, b"", SESSION)
    read = handle_scientific_run(service, "GET", f"{RUNS_PATH}/{finished.run_id}", b"", SESSION)
    downloaded = handle_scientific_run(
        service,
        "GET",
        f"{RUNS_PATH}/{finished.run_id}/artifacts/{artifact.artifact_id}",
        b"",
        SESSION,
    )
    assert listed[0] == 200 and listed[1]["runs"][0]["run_id"] == finished.run_id
    assert read[0] == 200 and read[1]["run"]["status"] == "completed"
    assert isinstance(downloaded, ScientificArtifactResponse)
    assert downloaded.body == original
    assert hashlib.sha256(downloaded.body).hexdigest() == digest
    assert stored_bytes() == before
    assert seen == list(CONTRACT_ORDER)
    for stage_id in CONTRACT_ORDER:
        assert spies[stage_id].calls == 1
    assert workflow_bytes_match(original, finished) is True
    reordered = json.loads(original)
    reordered["stages"][0], reordered["stages"][1] = reordered["stages"][1], reordered["stages"][0]
    assert workflow_bytes_match(_canonical(reordered).encode(), finished) is False
    retitled = json.loads(original)
    retitled["stages"][2]["implementation_version"] = "protein_ranking_agent_v2"
    assert workflow_bytes_match(_canonical(retitled).encode(), finished) is False
    future = json.loads(original)
    future["schema"] = "cellertz.governed_agent_workflow.v2"
    assert workflow_bytes_match(_canonical(future).encode(), finished) is False
    path = tmp_path / "scientific-runs.sqlite3"
    store.close()
    reopened = SQLiteScientificRunStore(path)
    fresh, fresh_seen, _fresh_spies = _watched()
    restored = type(service)(
        authorizations,
        store=reopened,
        executor=fresh,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    loaded = restored.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    )
    assert loaded.content == original
    assert restored.process_next() is None
    assert fresh_seen == []
    reopened.close()


def test_terminal_store_failure_does_not_rerun_specialists(tmp_path: Path) -> None:
    runtime, seen, spies = _watched()
    service, store, _authorizations, _granted, _created = _start(tmp_path, runtime)
    real = store.commit_transition
    state = {"count": 0}

    def flaky(*args, **kwargs):
        state["count"] += 1
        if state["count"] <= 2:
            raise ScientificRunStoreError("scientific_run_store_unavailable")
        return real(*args, **kwargs)

    store.commit_transition = flaky
    assert service.process_next() is None
    assert seen == list(CONTRACT_ORDER)
    assert service._pending_failure is not None
    failed = service.process_next()
    assert failed is not None
    assert failed.status == "failed"
    assert failed.artifacts == ()
    assert seen == list(CONTRACT_ORDER)
    for stage_id in CONTRACT_ORDER:
        assert spies[stage_id].calls == 1
    store.close()


def test_trace_mutations_and_duplicate_keys_fail_closed(tmp_path: Path) -> None:
    runtime = GovernedProteinAgentRuntime()
    _service_unused, store, _authorizations, _granted, _created = _start(tmp_path, runtime)
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    body = runtime.execute(running).artifacts[1].body
    parsed = json.loads(body)
    stamped = json.loads(body)
    stamped["stages"][0]["observed_at"] = "2026-10-12T16:00:01+00:00"
    assert stamped["stages"][0]["output_digest"] == parsed["stages"][0]["output_digest"]
    assert hashlib.sha256(_canonical(stamped).encode()).digest() != hashlib.sha256(body).digest()
    cases = (
        (("schema",), "other"),
        (("workflow_profile",), "other"),
        (("workflow_version",), "v2"),
        (("run_id",), "run_" + "ab" * 16),
        (("session_id",), "session_other"),
        (("authorization_id",), "authorization_other"),
        (("attempt",), 2),
        (("attempt",), True),
        (("binding_hash",), "ab" * 32),
        (("profile_id",), "other"),
        (("target_scope",), "CD22"),
        (("scientific_candidate_generated",), True),
        (("scientific_validation",), True),
        (("ranked",), True),
        (("accepted",), True),
        (("stages", 0, "summary"), "changed"),
        (("stages", 0, "role"), "scoring"),
        (("stages", 0, "implementation_version"), "other"),
        (("stages", 0, "implementation_id"), "other"),
        (("stages", 0, "stage_id"), "scoring"),
        (("stages", 0, "status"), "failed"),
        (("stages", 0, "sequence"), 9),
        (("stages", 0, "sequence"), 1.5),
        (("stages", 0, "attempt"), 2),
        (("stages", 0, "upstream_digest"), "ab" * 32),
        (("stages", 0, "input_digest"), "ab" * 32),
        (("stages", 0, "output_digest"), "ab" * 32),
        (("stages", 0, "observed_at"), "2026-10-12T16:00:01+00:00"),
        (("stages", 0, "observed_at"), "not-a-time"),
    )
    for path, value in cases:
        document = json.loads(body)
        cursor = document
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        assert workflow_bytes_match(_canonical(document).encode(), running) is False, path
    extra = json.loads(body)
    extra["stages"][0]["prompt"] = "ignore"
    assert workflow_bytes_match(_canonical(extra).encode(), running) is False
    foreign = json.loads(body)
    foreign["command"] = "bindcraft"
    assert workflow_bytes_match(_canonical(foreign).encode(), running) is False
    duplicated = body.replace(b'"accepted":false', b'"accepted":false,"accepted":true', 1)
    nested = body.replace(b'"role":"generation"', b'"role":"generation","role":"scoring"', 1)
    assert workflow_bytes_match(duplicated, running) is False
    assert workflow_bytes_match(nested, running) is False
    assert workflow_bytes_match(body + b"\xff", running) is False
    store.close()


class StageSpy:
    def __init__(self, specialist: object, seen: list[str]) -> None:
        self.stage_id = specialist.stage_id
        self._plan = specialist.plan
        self.calls = 0
        self.handoffs: list = []
        self._seen = seen

    def plan(self, handoff):
        self.calls += 1
        self.handoffs.append(handoff)
        self._seen.append(self.stage_id)
        return self._plan(handoff)


def _watched(overrides: dict | None = None):
    seen: list[str] = []
    spies = {stage: StageSpy(spec, seen) for stage, spec in default_specialists().items()}
    chosen = dict(spies)
    if overrides:
        chosen.update(overrides)
    return GovernedProteinAgentRuntime(chosen), seen, spies


def test_completion_keeps_its_own_timestamp(tmp_path: Path) -> None:
    runtime, _seen, _spies = _watched()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "memory", enabled=True, executor=runtime
    )
    _advance(service)
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    finished = service.process_next()
    queued, running, completed = (event.recorded_at for event in finished.events)
    assert created.run.updated_at == queued
    assert queued < running < completed
    assert finished.events[-1].status == "completed"
    assert finished.updated_at == completed
    assert workflow_observed_at(finished) == running
    artifact = next(item for item in finished.artifacts if item.kind == "workflow_trace")
    body = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    ).content
    parsed = json.loads(body)
    assert {stage["observed_at"] for stage in parsed["stages"]} == {running}
    assert workflow_bytes_match(body, finished) is True
    for stamp in (finished.created_at, finished.updated_at, running[:-6] + ".500000+00:00"):
        shifted = json.loads(body)
        for stage in shifted["stages"]:
            stage["observed_at"] = stamp
        assert workflow_bytes_match(_canonical(shifted).encode(), finished) is False
    assert "if sealed" not in (ROOT / "src/cellertz/application/scientific_run.py").read_text()
    store.close()


def test_running_event_must_be_the_current_attempt(tmp_path: Path) -> None:
    runtime = GovernedProteinAgentRuntime()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "memory", enabled=True, executor=runtime
    )
    _advance(service)
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    finished = service.process_next()
    artifact = next(item for item in finished.artifacts if item.kind == "workflow_trace")
    body = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    ).content
    running = next(event for event in finished.events if event.status == "running")
    missing = _retarget(finished, tuple(event for event in finished.events if event is not running))
    assert workflow_observed_at(missing) is None
    assert workflow_bytes_match(body, missing) is False
    duplicate = _retarget(finished, (*finished.events, _event_like(running, sequence=9)))
    assert workflow_observed_at(duplicate) is None
    assert workflow_bytes_match(body, duplicate) is False
    foreign = _retarget(
        finished,
        tuple(
            _event_like(event, attempt=2) if event.status == "running" else event
            for event in finished.events
        ),
    )
    assert workflow_observed_at(foreign) is None
    assert workflow_bytes_match(body, foreign) is False
    malformed = _retarget(
        finished,
        tuple(
            _event_like(event, recorded_at="2026-10-12T16:00:02.000000+00:00")
            if event.status == "running"
            else event
            for event in finished.events
        ),
    )
    assert workflow_observed_at(malformed) is None
    assert workflow_bytes_match(body, malformed) is False
    store.close()


def test_current_attempt_chain_is_revalidated(tmp_path: Path) -> None:
    runtime = GovernedProteinAgentRuntime()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "memory", enabled=True, executor=runtime
    )
    _advance(service)
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    finished = service.process_next()
    artifact = next(item for item in finished.artifacts if item.kind == "workflow_trace")
    body = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    ).content
    queued, running, completed = finished.events
    assert workflow_observed_at(finished) == running.recorded_at

    def reject(events: tuple) -> None:
        altered = _retarget(finished, events)
        assert workflow_observed_at(altered) is None
        assert workflow_bytes_match(body, altered) is False

    def replace(target: ScientificRunEvent, **changes) -> tuple:
        return tuple(
            _event_like(event, **changes) if event is target else event for event in finished.events
        )

    reject(replace(queued, origin="client"))
    reject(replace(running, origin="operator"))
    reject(replace(completed, origin=""))
    reject(replace(running, sequence=queued.sequence + 2))
    reject(replace(completed, sequence=running.sequence + 2))
    reject(replace(running, sequence=True))
    reject(replace(queued, sequence=1.5))
    reject((running, queued, completed))
    store.close()


def test_retry_trace_uses_the_new_running_event(tmp_path: Path) -> None:
    class Once:
        stage_id = "generation"

        def __init__(self) -> None:
            self.calls = 0

        def plan(self, handoff):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("secret")
            return StagePlan(status="completed", summary=SUMMARY)

    runtime, _seen, _spies = _watched({"generation": Once()})
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "sqlite", enabled=True, executor=runtime
    )
    _advance(service)
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    failed = service.process_next()
    previous = next(event.recorded_at for event in failed.events if event.status == "running")
    queued = service.retry(session_id=SESSION, run_id=created.run.run_id, idempotency_key="retry-a")
    finished = service.process_next()
    assert queued.run.attempt == 2
    assert finished.status == "completed" and finished.attempt == 2
    current = next(
        event.recorded_at
        for event in finished.events
        if event.status == "running" and event.attempt == 2
    )
    assert previous < current < finished.updated_at
    artifact = next(item for item in finished.artifacts if item.kind == "workflow_trace")
    body = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    ).content
    assert {stage["observed_at"] for stage in json.loads(body)["stages"]} == {current}
    replay = service.retry(session_id=SESSION, run_id=finished.run_id, idempotency_key="retry-a")
    assert replay.run.status == "completed" and replay.run.attempt == 2
    assert workflow_bytes_match(body, replay.run) is True
    assert workflow_observed_at(replay.run) == current
    moved = _retarget(
        replay.run,
        tuple(
            _event_like(event, recorded_at=previous) if event.recorded_at == current else event
            for event in replay.run.events
        ),
    )
    assert workflow_bytes_match(body, moved) is False
    store.close()


def test_plain_executor_keeps_the_terminal_stamp(tmp_path: Path) -> None:
    class Plain:
        def execute(self, run):
            body = _canonical(
                {
                    "attempt": run.attempt,
                    "authorization_id": run.authorization_id,
                    "run_id": run.run_id,
                    "schema": "cellertz.scientific_execution_record.v1",
                    "scientific_validation": False,
                    "session_id": run.session_id,
                    "status": "completed",
                }
            ).encode()
            return ScientificExecutionResult(
                "completed",
                (ExecutorArtifact("execution_record", "application/json", body),),
            )

    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "memory", enabled=True, executor=Plain()
    )
    _advance(service)
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    finished = service.process_next()
    queued, running, completed = (event.recorded_at for event in finished.events)
    assert queued < running < completed
    assert finished.updated_at == completed
    assert [item.kind for item in finished.artifacts] == ["execution_record"]
    store.close()


def _advance(service) -> None:
    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        return WHEN + timedelta(seconds=ticks["n"])

    service._clock = clock


def _retarget(run: ScientificRun, events: tuple) -> ScientificRun:
    clone = object.__new__(ScientificRun)
    for name, value in run.__dict__.items():
        object.__setattr__(clone, name, value)
    object.__setattr__(clone, "events", events)
    return clone


def _event_like(event: ScientificRunEvent, **changes) -> ScientificRunEvent:
    clone = object.__new__(ScientificRunEvent)
    for name, value in event.__dict__.items():
        object.__setattr__(clone, name, value)
    for name, value in changes.items():
        object.__setattr__(clone, name, value)
    return clone


def _start(tmp_path: Path, runtime, backend: str = "memory"):
    service, store, authorizations, granted, _runner = _service(
        tmp_path, backend, enabled=True, executor=runtime
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    return service, store, authorizations, granted, created


def _digests(result: ScientificExecutionResult) -> list[str]:
    parsed = json.loads(result.artifacts[1].body)
    return [stage["output_digest"] for stage in parsed["stages"]]


def _shift(record, **changes):
    source = {
        key: getattr(record, key)
        for key in (
            "approval_id",
            "budget",
            "image_digest",
            "manifest_digest",
            "profile_id",
            "readiness_observation_id",
            "readiness_observed_at",
            "request_digest",
            "request_id",
            "request_revision",
            "runtime_name",
            "runtime_version",
            "session_id",
            "target_scope",
            "tool_name",
            "tool_version",
        )
    }
    source.update(changes)
    if "budget" in changes:
        source["budget"] = dict(changes["budget"])
    return replace(record, binding_hash=binding_hash_for(source), **changes)


def _budget_values(budget: dict) -> tuple[tuple[str, object], ...]:
    action = "stop" if budget["action_on_limit"] == "warn" else "warn"
    currency = "USD" if budget["currency"] != "USD" else None
    warning = 0.25 if budget["warning_threshold"] != 0.25 else 0.5
    return (
        ("action_on_limit", action),
        ("authorizes_execution", True),
        ("currency", currency),
        ("human_override_required", not budget["human_override_required"]),
        ("model_cost_ceiling", None if budget["model_cost_ceiling"] is not None else 1),
        (
            "scientific_tool_cost_ceiling",
            None if budget["scientific_tool_cost_ceiling"] is not None else 1,
        ),
        ("token_ceiling", 9 if budget["token_ceiling"] != 9 else 4),
        ("warning_threshold", warning),
    )
