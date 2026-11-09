"""Phase 6J explicit scientific-run authorization. Permission only."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from cellertz.application.contracts import ExecutionBudget
from cellertz.application.conversation_transport import configured_handler
from cellertz.application.request_draft import DesignDraftCommand
from cellertz.application.scientific_authorization import (
    AUTHORIZED_TEXT,
    CONSUMED_TEXT,
    REVOKED_TEXT,
    SINGLE_USE_TEXT,
    VALIDATION_TEXT,
    InMemoryScientificAuthorizationStore,
    ScientificAuthorizationListReply,
    ScientificAuthorizationReply,
    ScientificAuthorizationService,
    ScientificAuthorizationStoreError,
)
from cellertz.application.scientific_authorization_config import (
    ScientificAuthorizationConfigurationError,
    open_scientific_authorization_store,
    resolve_scientific_authorization_backend,
)
from cellertz.application.scientific_authorization_store import (
    SCHEMA_VERSION,
    SQLiteScientificAuthorizationStore,
)
from cellertz.application.scientific_authorization_transport import (
    AUTHORIZATIONS_PATH,
    handle_scientific_authorization,
)
from cellertz.application.scientific_runner_readiness import (
    CD22_PROFILE_ID,
    PDL1_PROFILE_ID,
    TACI_PROFILE_ID,
    ProbeObservation,
    load_scientific_runner_readiness,
)
from cellertz.conversation.__main__ import main
from tests.test_approved_run import _created

ROOT = Path(__file__).resolve().parents[1]
SESSION = "session_pdl1_prototype"
WHEN = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
_MODULES = (
    ROOT / "src/cellertz/application/scientific_authorization.py",
    ROOT / "src/cellertz/application/scientific_authorization_store.py",
    ROOT / "src/cellertz/application/scientific_authorization_config.py",
    ROOT / "src/cellertz/application/scientific_authorization_transport.py",
)


class _Probe:
    def __init__(self, observation: ProbeObservation) -> None:
        self.calls = 0
        self.observation = observation

    def probe(self) -> ProbeObservation:
        self.calls += 1
        return self.observation


def _manifest(
    profile: str = PDL1_PROFILE_ID, tool: str = "bindcraft", target: str = "PDL1"
) -> dict:
    return {
        "argv_template": ["--recorded-template"],
        "entrypoint": "recorded-entry",
        "expected_inputs": [
            {"media_type": "application/json", "name": "binder_specification"},
            {"media_type": "chemical/x-pdb", "name": "target_structure"},
        ],
        "expected_outputs": [{"media_type": "text/csv", "name": "design_records"}],
        "image_digest": "sha256:" + ("ab" * 32),
        "max_output_bytes": 1048576,
        "profile_id": profile,
        "resource_envelope": {
            "cpu_count": 2,
            "gpu_count": 0,
            "max_output_bytes": 1048576,
            "memory_mib": 4096,
            "timeout_seconds": 120,
        },
        "runtime_name": "recorded-runtime",
        "runtime_version": "recorded-runtime-1",
        "schema_version": "scientific-runner-manifest-v1",
        "target_scope": target,
        "timeout_seconds": 120,
        "tool_name": tool,
        "tool_version": "recorded-tool-1",
    }


def _write_manifest(path: Path, document: dict) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ready_readiness(tmp_path: Path, *, when: datetime = WHEN, document: dict | None = None):
    payload = _manifest() if document is None else document
    digest = _write_manifest(tmp_path / "manifest.json", payload)
    observation = ProbeObservation(
        "passed",
        True,
        digest,
        payload["profile_id"],
        payload["tool_name"],
        payload["tool_version"],
        payload["runtime_name"],
        payload["runtime_version"],
        payload["image_digest"],
        payload["target_scope"],
    )
    probe = _Probe(observation)
    service = load_scientific_runner_readiness(
        session_id=SESSION,
        environ={
            "CELLERTZ_SCIENTIFIC_RUNNER_READINESS": "manifest",
            "CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST": str(tmp_path / "manifest.json"),
        },
        probe=probe,
        clock=lambda: when,
    )
    return service, probe


def _world(tmp_path: Path, message: str | None = None, store=None, when: datetime = WHEN):
    if message is None:
        finished, runs, runner, gate, drafter, approval = _created()
    else:
        from tests.test_human_approval import _decide, _stack

        gate, drafter, _store, _dispatcher, reply = _stack(message)
        recorded = _decide(gate, reply)
        assert recorded.approval is not None
        dispatched = gate.dispatch(
            session_id=SESSION,
            approval_id=recorded.approval.approval_id,
            idempotency_key="dispatch-1",
        )
        assert dispatched.status == "demonstration_completed"
        from cellertz.application.approved_run import ApprovedRunService

        runs = ApprovedRunService(gate, clock=lambda: datetime(2026, 10, 8, 12, 0, tzinfo=UTC))
        created = runs.create(
            session_id=SESSION,
            approval_id=recorded.approval.approval_id,
            idempotency_key="run-1",
        )
        assert runs.process_next() is not None
        finished = runs.read(session_id=SESSION, run_id=created.run.run_id)
        runner = runs._runner
        approval = recorded.approval
    readiness, probe = _ready_readiness(tmp_path, when=when)
    ids = {"n": 0}

    def authorization_ids() -> str:
        ids["n"] += 1
        return f"authorization_{ids['n']:032x}"

    service = ScientificAuthorizationService(
        gate._sessions,
        drafter,
        gate,
        runs,
        readiness,
        store=store,
        clock=lambda: when,
        authorization_ids=authorization_ids,
    )
    return service, runs, runner, gate, drafter, approval, finished, probe


def _authorize(service: ScientificAuthorizationService, approval_id: str, key: str = "auth-1"):
    return service.authorize(session_id=SESSION, approval_id=approval_id, idempotency_key=key)


def test_authorization_requires_the_full_prerequisite_chain(tmp_path: Path) -> None:
    service, runs, runner, gate, drafter, approval, finished, probe = _world(tmp_path)
    before = runner.calls
    missing = _authorize(service, "approval_missing")
    assert missing.status == "approval_absent"
    assert missing.authorization is None
    assert missing.execution_authorized is False
    bare = ScientificAuthorizationService(
        {},
        drafter,
        gate,
        runs,
        service._readiness,
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{1:032x}",
    )
    assert (
        bare.authorize(
            session_id=SESSION, approval_id=approval.approval_id, idempotency_key="auth-1"
        ).status
        == "session_absent"
    )
    from tests.test_human_approval import _decide, _stack

    reject_gate, reject_drafter, _store, _dispatcher, reject_reply = _stack(
        "design a binder for pdl1 using fixture with 3 candidates"
    )
    rejected = _decide(reject_gate, reject_reply, decision="rejected")
    assert rejected.approval is not None
    denied = ScientificAuthorizationService(
        reject_gate._sessions,
        reject_drafter,
        reject_gate,
        runs,
        service._readiness,
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{2:032x}",
    ).authorize(
        session_id=SESSION,
        approval_id=rejected.approval.approval_id,
        idempotency_key="auth-rejected",
    )
    assert denied.status == "approval_not_approved"
    no_demo_gate, no_demo_drafter, _store, _dispatcher, reply = __import__(
        "tests.test_human_approval", fromlist=["_stack"]
    )._stack()
    recorded = __import__("tests.test_human_approval", fromlist=["_decide"])._decide(
        no_demo_gate, reply
    )
    assert recorded.approval is not None
    without_demo = ScientificAuthorizationService(
        no_demo_gate._sessions,
        no_demo_drafter,
        no_demo_gate,
        runs,
        service._readiness,
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{3:032x}",
    )
    assert (
        without_demo.authorize(
            session_id=SESSION,
            approval_id=recorded.approval.approval_id,
            idempotency_key="auth-1",
        ).status
        == "demonstration_absent"
    )
    queued_runs, _queued_runner, queued_gate, queued_drafter, _store, _dispatcher, recorded = (
        __import__("tests.test_approved_run", fromlist=["_demonstrated"])._demonstrated()
    )
    queued_approval = recorded.approval
    assert queued_approval is not None
    created = queued_runs.create(
        session_id=SESSION,
        approval_id=queued_approval.approval_id,
        idempotency_key="run-queued",
    )
    assert created.run is not None and created.run.status == "queued"
    del _store, _dispatcher
    waiting = ScientificAuthorizationService(
        queued_gate._sessions,
        queued_drafter,
        queued_gate,
        queued_runs,
        service._readiness,
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{4:032x}",
    )
    assert _authorize(waiting, queued_approval.approval_id).status == "fixture_run_required"
    draft = drafter.revisions(approval.request_id)[-1]
    drafter._history[approval.request_id][-1] = replace(draft, completeness="incomplete")
    assert _authorize(service, approval.approval_id, "incomplete").status == "request_incomplete"
    drafter._history[approval.request_id][-1] = draft
    updated = drafter.revise(
        DesignDraftCommand(
            SESSION,
            "design a binder for pdl1 using fixture with 2 candidates",
            approval.request_id,
            approval.request_revision,
        )
    )
    assert updated.draft is not None and updated.draft.revision == approval.request_revision + 1
    assert _authorize(service, approval.approval_id, "stale").status == "revision_conflict"
    drafter._history[approval.request_id].pop()
    session = gate._sessions[SESSION]
    gate._sessions[SESSION] = replace(
        session,
        budget=ExecutionBudget(
            token_ceiling=5,
            model_cost_ceiling=None,
            scientific_tool_cost_ceiling=None,
            warning_threshold=None,
            action_on_limit="stop",
            human_override_required=True,
        ),
    )
    assert _authorize(service, approval.approval_id, "digest").status == "digest_mismatch"

    class _NoBudget:
        budget = None

    gate._sessions[SESSION] = _NoBudget()
    assert _authorize(service, approval.approval_id, "budget").status == "budget_invalid"
    gate._sessions[SESSION] = session
    granted = _authorize(service, approval.approval_id)
    assert granted.status == "authorization_created"
    assert granted.authorization is not None
    assert granted.authorization.execution_authorized is True
    assert type(granted.authorization.execution_authorized) is bool
    assert granted.authorization.scientific_execution is False
    assert finished.run.status == "completed"
    assert runner.calls == before
    assert probe.calls == 1
    record = granted.authorization
    assert record.request_digest == approval.revision_digest
    assert record.profile_id == PDL1_PROFILE_ID
    assert record.tool_name == "bindcraft"
    assert record.target_scope == "PDL1"
    assert record.tool_version == "recorded-tool-1"
    assert record.image_digest.startswith("sha256:")
    assert record.manifest_digest
    assert record.status == "authorized"
    body = granted.to_dict()
    assert body["authorization"]["single_use"] == SINGLE_USE_TEXT
    assert body["authorization"]["text"] == AUTHORIZED_TEXT
    assert body["authorization"]["validation_text"] == VALIDATION_TEXT
    assert body["scientific_candidate_generated"] is False
    assert body["authorization"]["budget"]["authorizes_execution"] is False
    assert "argv" not in json.dumps(body)
    assert runner.calls == before


def test_readiness_and_blocked_targets_do_not_authorize(tmp_path: Path) -> None:
    service, _runs, runner, _gate, _drafter, approval, _finished, _probe = _world(tmp_path)
    calls = runner.calls
    service._readiness = load_scientific_runner_readiness(
        session_id=SESSION, environ={}, clock=lambda: WHEN
    )
    assert _authorize(service, approval.approval_id, "disabled").status == "readiness_not_ready"
    blocked, _probe = _ready_readiness(
        tmp_path / "blocked",
        document=_manifest(CD22_PROFILE_ID, "mber", "CD22"),
    )
    service._readiness = blocked
    assert _authorize(service, approval.approval_id, "cd22").status == "target_blocked"
    taci, _probe = _ready_readiness(
        tmp_path / "taci",
        document=_manifest(TACI_PROFILE_ID, "mber", "TACI"),
    )
    service._readiness = taci
    assert _authorize(service, approval.approval_id, "taci").status == "target_blocked"
    failed = tmp_path / "bad.json"
    failed.write_text("{", encoding="utf-8")
    service._readiness = load_scientific_runner_readiness(
        session_id=SESSION,
        environ={
            "CELLERTZ_SCIENTIFIC_RUNNER_READINESS": "manifest",
            "CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST": str(failed),
        },
        clock=lambda: WHEN,
    )
    assert _authorize(service, approval.approval_id, "config").status == "readiness_not_ready"

    class _Broken:
        def read(self):
            raise RuntimeError("traceback /tmp/secret SELECT")

    service._readiness = _Broken()
    assert _authorize(service, approval.approval_id, "adapter").status == "readiness_invalid"
    ready, _probe = _ready_readiness(tmp_path / "ready")
    leaked = ready.read()
    object.__setattr__(leaked, "execution_authorized", True)
    service._readiness = ready
    ready.read = lambda: leaked  # type: ignore[method-assign]
    assert _authorize(service, approval.approval_id, "flag").status == "readiness_invalid"
    bcma, _runs_b, _runner_b, _gate_b, _drafter_b, approval_b, _finished_b, _probe_b = _world(
        tmp_path / "bcma",
        message="design a binder for bcma using fixture with 1 candidates",
    )
    assert _authorize(bcma, approval_b.approval_id, "bcma").status == "target_blocked"
    assert runner.calls == calls


def test_idempotency_sessions_revocation_and_single_consume(tmp_path: Path) -> None:
    service, runs, runner, gate, _drafter, approval, _finished, _probe = _world(tmp_path)
    calls = runner.calls
    first = _authorize(service, approval.approval_id, "key-1")
    assert first.status == "authorization_created"
    replay = _authorize(service, approval.approval_id, "key-1")
    assert replay.status == "authorization_replayed"
    assert replay.authorization.authorization_id == first.authorization.authorization_id
    second_key = _authorize(service, approval.approval_id, "key-2")
    assert second_key.status == "authorization_replayed"
    assert second_key.authorization.authorization_id == first.authorization.authorization_id
    later = datetime(2026, 10, 12, 17, 0, tzinfo=UTC)
    shifted, _probe = _ready_readiness(tmp_path / "later", when=later)
    service._readiness = shifted
    conflict = _authorize(service, approval.approval_id, "key-1")
    assert conflict.status == "idempotency_conflict"
    assert (
        service.read(
            session_id=SESSION, authorization_id=first.authorization.authorization_id
        ).authorization.readiness_observed_at
        == WHEN.isoformat()
    )
    assert (
        service.consumable(
            session_id=SESSION, authorization_id=first.authorization.authorization_id
        )
        is False
    )
    assert (
        service.claim_once(
            session_id=SESSION, authorization_id=first.authorization.authorization_id
        )
        == "unavailable"
    )
    service._readiness = _ready_readiness(tmp_path / "back", when=WHEN)[0]
    assert (
        service.claim_once(
            session_id=SESSION, authorization_id=first.authorization.authorization_id
        )
        == "consumed"
    )
    assert (
        service.claim_once(
            session_id=SESSION, authorization_id=first.authorization.authorization_id
        )
        == "unavailable"
    )
    observed = service.read(
        session_id=SESSION, authorization_id=first.authorization.authorization_id
    )
    assert observed.authorization.status == "authorized"
    assert observed.authorization.execution_authorized is False
    assert observed.text == CONSUMED_TEXT
    replayed = _authorize(service, approval.approval_id, "key-1")
    assert replayed.status == "authorization_replayed"
    assert replayed.text == CONSUMED_TEXT
    listed = service.list_authorizations(session_id=SESSION)
    assert listed.authorizations[0].execution_authorized is False
    assert listed.to_dict()["authorizations"][0]["text"] == CONSUMED_TEXT
    refused = service.revoke(
        session_id=SESSION,
        authorization_id=first.authorization.authorization_id,
        idempotency_key="revoke-consumed",
    )
    assert refused.status == "authorization_rejected"
    assert refused.authorization is None
    assert "No scientific execution has started" not in refused.text
    still = service.read(session_id=SESSION, authorization_id=first.authorization.authorization_id)
    assert still.authorization.status == "authorized"
    assert len(still.authorization.events) == 1
    other = service.read(
        session_id="session_other", authorization_id=first.authorization.authorization_id
    )
    assert other.status == "authorization_absent"
    assert runner.calls == calls
    del runs, gate


def test_revocation_is_idempotent_before_consumption(tmp_path: Path) -> None:
    service, runs, runner, gate, _drafter, approval, _finished, _probe = _world(tmp_path)
    calls = runner.calls
    first = _authorize(service, approval.approval_id, "key-1")
    later = datetime(2026, 10, 12, 16, 5, tzinfo=UTC)
    service._clock = lambda: later
    revoked = service.revoke(
        session_id=SESSION,
        authorization_id=first.authorization.authorization_id,
        idempotency_key="revoke-1",
    )
    assert revoked.status == "authorization_revoked"
    assert revoked.authorization.execution_authorized is False
    assert len(revoked.authorization.events) == 2
    assert revoked.authorization.events[1].recorded_at == later.isoformat()
    again = service.revoke(
        session_id=SESSION,
        authorization_id=first.authorization.authorization_id,
        idempotency_key="revoke-1",
    )
    assert again.status == "authorization_revoked"
    assert len(again.authorization.events) == 2
    other_key = service.revoke(
        session_id=SESSION,
        authorization_id=first.authorization.authorization_id,
        idempotency_key="revoke-2",
    )
    assert other_key.status == "authorization_revoked"
    assert len(other_key.authorization.events) == 2
    restored = _authorize(service, approval.approval_id, "key-3")
    assert restored.status == "authorization_revoked"
    assert restored.authorization.authorization_id == first.authorization.authorization_id
    assert runner.calls == calls
    listed = service.list_authorizations(session_id=SESSION)
    assert listed.authorizations[0].authorization_id == first.authorization.authorization_id
    del runs, gate


def test_sqlite_restart_locks_and_closed_failures(tmp_path: Path) -> None:
    path = tmp_path / "scientific-authorizations.sqlite3"
    store = SQLiteScientificAuthorizationStore(path)
    service, _runs, runner, _gate, _drafter, approval, _finished, _probe = _world(
        tmp_path, store=store
    )
    calls = runner.calls
    granted = _authorize(service, approval.approval_id)
    assert granted.status == "authorization_created"
    service.close()
    reopened = SQLiteScientificAuthorizationStore(path)
    restored = reopened.read(SESSION, granted.authorization.authorization_id)
    assert restored is not None
    assert restored.binding_hash == granted.authorization.binding_hash
    assert restored.budget == granted.authorization.budget
    assert len(restored.events) == 1
    assert reopened._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 1
    reopened._fail_on = "key"
    with pytest.raises(ScientificAuthorizationStoreError):
        reopened.claim(
            scope=(SESSION, "partial"),
            content_hash=restored.binding_hash,
            binding_hash=restored.binding_hash,
            authorization=restored,
        )
    assert reopened._conn.execute("SELECT COUNT(*) FROM authorizations").fetchone()[0] == 1
    assert reopened._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert reopened._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 1
    document = reopened._conn.execute("SELECT document FROM authorizations").fetchone()[0]
    reopened._conn.execute(
        "UPDATE authorizations SET document = ? WHERE authorization_id = ?",
        (document.replace("{", "{ ", 1), granted.authorization.authorization_id),
    )
    with pytest.raises(Exception) as bad:
        reopened.read(SESSION, granted.authorization.authorization_id)
    assert bad.value.code == "authorization_record_invalid"
    assert "sqlite" not in str(bad.value)
    reopened.close()
    versioned = sqlite3.connect(path)
    versioned.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    versioned.execute("UPDATE schema_meta SET version = 9")
    versioned.commit()
    versioned.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    versioned.close()
    for suffix in ("-wal", "-shm"):
        leftover = Path(str(path) + suffix)
        if leftover.exists():
            leftover.unlink()
    unchanged = path.read_bytes()
    with pytest.raises(Exception) as newer:
        SQLiteScientificAuthorizationStore(path)
    assert newer.value.code == "unsupported_scientific_authorization_schema"
    assert path.read_bytes() == unchanged
    garbage = tmp_path / "garbage.sqlite3"
    garbage.write_bytes(b"\xff not a database")
    original = garbage.read_bytes()
    with pytest.raises(Exception) as unreadable:
        SQLiteScientificAuthorizationStore(garbage)
    assert unreadable.value.code == "scientific_authorization_database_unreadable"
    assert garbage.read_bytes() == original
    assert runner.calls == calls
    assert SCHEMA_VERSION == 1


def test_missing_approval_is_http_409(tmp_path: Path) -> None:
    service, _runs, runner, _gate, _drafter, _approval, _finished, _probe = _world(tmp_path)
    calls = runner.calls
    status, payload = handle_scientific_authorization(
        service,
        "POST",
        AUTHORIZATIONS_PATH,
        json.dumps({"approval_id": "approval_missing", "idempotency_key": "missing-1"}).encode(),
        SESSION,
    )
    assert status == 409
    assert payload["status"] == "approval_absent"
    assert payload["authorization"] is None
    assert payload["text"] == "The approval is not available. No scientific action was taken."
    assert runner.calls == calls


def test_consumed_authorization_points_at_the_scientific_run(tmp_path: Path) -> None:
    service, _runs, runner, _gate, _drafter, approval, _finished, _probe = _world(tmp_path)
    calls = runner.calls
    body = json.dumps({"approval_id": approval.approval_id, "idempotency_key": "edge-1"}).encode()
    created, payload = handle_scientific_authorization(
        service, "POST", AUTHORIZATIONS_PATH, body, SESSION
    )
    assert created == 201
    assert payload["text"] == AUTHORIZED_TEXT
    authorization_id = payload["authorization"]["authorization_id"]
    assert service.claim_once(session_id=SESSION, authorization_id=authorization_id) == "consumed"
    replay_status, replay = handle_scientific_authorization(
        service, "POST", AUTHORIZATIONS_PATH, body, SESSION
    )
    assert replay_status == 200
    assert replay["status"] == "authorization_replayed"
    assert replay["text"] == CONSUMED_TEXT
    assert replay["execution_authorized"] is False
    assert replay["authorization"]["status"] == "authorized"
    assert replay["authorization"]["text"] == CONSUMED_TEXT
    assert replay["authorization"]["execution_authorized"] is False
    read_status, observed = handle_scientific_authorization(
        service, "GET", f"{AUTHORIZATIONS_PATH}/{authorization_id}", b"", SESSION
    )
    assert read_status == 200
    assert observed["status"] == "authorization_observed"
    assert observed["text"] == CONSUMED_TEXT
    assert observed["authorization"]["execution_authorized"] is False
    listed_status, listed = handle_scientific_authorization(
        service, "GET", AUTHORIZATIONS_PATH, b"", SESSION
    )
    assert listed_status == 200
    assert listed["authorizations"][0]["text"] == CONSUMED_TEXT
    assert listed["authorizations"][0]["execution_authorized"] is False
    revoked_status, revoked = handle_scientific_authorization(
        service,
        "POST",
        f"{AUTHORIZATIONS_PATH}/{authorization_id}/revoke",
        json.dumps({"idempotency_key": "revoke-consumed"}).encode(),
        SESSION,
    )
    assert revoked_status == 409
    assert revoked["status"] == "authorization_rejected"
    assert revoked["authorization"] is None
    assert "No scientific execution has started" not in revoked["text"]
    assert runner.calls == calls


def test_transport_rejects_client_fields_and_stays_read_only(tmp_path: Path) -> None:
    service, _runs, runner, _gate, _drafter, approval, _finished, probe = _world(tmp_path)
    calls = runner.calls
    probes = probe.calls
    body = json.dumps({"approval_id": approval.approval_id, "idempotency_key": "edge-1"}).encode()
    status, payload = handle_scientific_authorization(
        service, "POST", AUTHORIZATIONS_PATH, body, SESSION
    )
    assert status == 201
    assert payload["status"] == "authorization_created"
    assert payload["execution_authorized"] is True
    replay, replay_body = handle_scientific_authorization(
        service, "POST", AUTHORIZATIONS_PATH, body, SESSION
    )
    assert replay == 200
    assert (
        replay_body["authorization"]["authorization_id"]
        == payload["authorization"]["authorization_id"]
    )
    for extra in (
        {"digest": "ab" * 32},
        {"revision": 1},
        {"budget": {"token_ceiling": 1}},
        {"argv": ["bindcraft"]},
        {"session_id": "session_other"},
        {"execution_authorized": True},
        {"readiness_status": "ready"},
        {"path": "/tmp/secret"},
    ):
        rejected, rejected_body = handle_scientific_authorization(
            service,
            "POST",
            AUTHORIZATIONS_PATH,
            json.dumps(
                {
                    "approval_id": approval.approval_id,
                    "idempotency_key": "edge-1",
                    **extra,
                }
            ).encode(),
            SESSION,
        )
        assert rejected == 400
        assert rejected_body["status"] == "invalid_payload"
        assert rejected_body["authorization"] is None
        encoded = json.dumps(rejected_body)
        assert "/tmp" not in encoded
        assert "secret" not in encoded
    invalid, invalid_body = handle_scientific_authorization(
        service, "POST", AUTHORIZATIONS_PATH, b"\xff", SESSION
    )
    assert invalid == 400
    assert invalid_body["status"] == "invalid_payload"
    listed, history = handle_scientific_authorization(
        service, "GET", AUTHORIZATIONS_PATH, b"", SESSION
    )
    assert listed == 200
    assert history["authorizations"]
    _status, again_body = handle_scientific_authorization(
        service, "GET", AUTHORIZATIONS_PATH, b"", SESSION
    )
    assert again_body == history
    queried, query_body = handle_scientific_authorization(
        service, "GET", f"{AUTHORIZATIONS_PATH}?session_id=other", b"", SESSION
    )
    assert queried == 400
    assert "other" not in json.dumps(query_body)
    missing, missing_body = handle_scientific_authorization(
        service,
        "GET",
        f"{AUTHORIZATIONS_PATH}/authorization_{'ab' * 16}",
        b"",
        SESSION,
    )
    assert missing == 404
    assert missing_body["status"] == "authorization_absent"
    posted, _payload = handle_scientific_authorization(
        service,
        "POST",
        f"{AUTHORIZATIONS_PATH}/{payload['authorization']['authorization_id']}/consume",
        b"{}",
        SESSION,
    )
    assert posted == 404
    handler = configured_handler(object(), session_id=SESSION, authorizations=service)
    status_code, round_trip = _invoke(handler, "GET", AUTHORIZATIONS_PATH, b"")
    assert status_code == 200
    assert (
        round_trip["authorizations"][0]["authorization_id"]
        == payload["authorization"]["authorization_id"]
    )
    assert runner.calls == calls
    assert probe.calls == probes


class _Leak(ScientificAuthorizationReply):
    def to_dict(self) -> dict:
        return {"text": "/tmp/secret Traceback SELECT token=abc", "argv": ["run"]}


def test_exact_dto_hides_untrusted_replies(tmp_path: Path) -> None:
    service, _runs, _runner, _gate, _drafter, approval, _finished, _probe = _world(tmp_path)

    class _Service:
        def authorize(self, **_kwargs):
            return _Leak("authorization_created", "/tmp/secret", SESSION)

        def list_authorizations(self, **_kwargs):
            return _Leak("authorizations_observed", "SELECT secret", SESSION)

        def read(self, **_kwargs):
            return object()

        def revoke(self, **_kwargs):
            return object()

    status, body = handle_scientific_authorization(
        _Service(),
        "POST",
        AUTHORIZATIONS_PATH,
        json.dumps({"approval_id": approval.approval_id, "idempotency_key": "edge"}).encode(),
        SESSION,
    )
    encoded = json.dumps(body)
    assert status == 200
    assert body["status"] == "adapter_failure"
    assert body["authorization"] is None
    assert body["execution_authorized"] is False
    for leak in ("/tmp", "Traceback", "SELECT", "token=", "argv", "secret"):
        assert leak not in encoded
    del service


def test_configuration_does_not_create_files_or_start_the_server(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "scientific-authorizations.sqlite3"
    with pytest.raises(ScientificAuthorizationConfigurationError) as unknown:
        resolve_scientific_authorization_backend(
            {"CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE": "postgres"}
        )
    assert unknown.value.code == "invalid_scientific_authorization_store"
    assert str(unknown.value) == unknown.value.code
    with pytest.raises(ScientificAuthorizationConfigurationError) as missing:
        open_scientific_authorization_store({"CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE": "sqlite"})
    assert missing.value.code == "scientific_authorization_db_required"
    assert not database.exists()
    assert open_scientific_authorization_store({}).__class__ is InMemoryScientificAuthorizationStore
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE", "redis")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_DB", str(database))

    def started(*_args, **_kwargs):
        raise AssertionError("server started")

    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    with pytest.raises(ScientificAuthorizationConfigurationError):
        main()
    assert not database.exists()
    assert list(tmp_path.iterdir()) == []


def test_modules_do_not_name_execution_machinery() -> None:
    forbidden = (
        "ProteinSupervisorV1",
        "LocalProteinRunStarter",
        "subprocess",
        "Popen",
        "ScientificRunMemory",
        "Apptainer",
        "aws_batch",
        "SecondBrain",
    )
    for path in _MODULES:
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in source
    transport = (ROOT / "src/cellertz/application/scientific_authorization_transport.py").read_text(
        encoding="utf-8"
    )
    assert "/consume" not in transport
    assert "process_next" not in transport


def test_symlink_fork_and_invalid_utf8_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "scientific-authorizations.sqlite3"
    store = SQLiteScientificAuthorizationStore(path)
    service, _runs, runner, _gate, _drafter, approval, _finished, _probe = _world(
        tmp_path, store=store
    )
    granted = _authorize(service, approval.approval_id)
    calls = runner.calls
    link = tmp_path / "alias.sqlite3"
    link.symlink_to(path)
    same = SQLiteScientificAuthorizationStore(link)
    same.close()
    busy = _lock_probe(link)
    assert busy.returncode == 0
    assert busy.stdout == "scientific_authorization_store_busy"
    assert "Traceback" not in busy.stderr
    assert "SELECT" not in busy.stderr
    store.close()
    assert _lock_probe(path).stdout == "opened"
    forked = _fork_probe(path, SESSION, granted.authorization.authorization_id)
    assert forked.returncode == 0
    assert forked.stdout == "scientific_authorization_store_busy"
    assert "Traceback" not in forked.stderr
    reopened = SQLiteScientificAuthorizationStore(path)
    rebound = ScientificAuthorizationService(
        service._sessions,
        service._drafter,
        service._gate,
        service._runs,
        service._readiness,
        store=reopened,
    )
    observed = rebound.read(
        session_id=SESSION, authorization_id=granted.authorization.authorization_id
    )
    assert observed.status == "authorization_observed"
    reopened._conn.execute(
        "UPDATE authorizations SET document = ? WHERE authorization_id = ?",
        (b"\xff", granted.authorization.authorization_id),
    )
    broken = rebound.read(
        session_id=SESSION, authorization_id=granted.authorization.authorization_id
    )
    assert broken.status == "authorization_record_invalid"
    assert broken.authorization is None
    encoded = json.dumps(broken.to_dict())
    for leak in ("Traceback", "sqlite", "SELECT", "/tmp", "\xff"):
        assert leak not in encoded
    with pytest.raises(ScientificAuthorizationStoreError) as caught:
        reopened.read(SESSION, granted.authorization.authorization_id)
    assert caught.value.code == "authorization_record_invalid"
    assert str(caught.value) == caught.value.code
    reopened.close()
    assert runner.calls == calls


def _lock_probe(path: Path) -> subprocess.CompletedProcess[str]:
    script = """
import sys
from pathlib import Path
from cellertz.application.scientific_authorization import ScientificAuthorizationStoreError
from cellertz.application.scientific_authorization_store import SQLiteScientificAuthorizationStore
try:
    opened = SQLiteScientificAuthorizationStore(Path(sys.argv[1]))
except ScientificAuthorizationStoreError as exc:
    sys.stdout.write(exc.code)
    raise SystemExit(0)
opened.close()
sys.stdout.write("opened")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _fork_probe(
    path: Path, session_id: str, authorization_id: str
) -> subprocess.CompletedProcess[str]:
    script = """
import os
import sys
from pathlib import Path
from cellertz.application.scientific_authorization import ScientificAuthorizationStoreError
from cellertz.application.scientific_authorization_store import SQLiteScientificAuthorizationStore

database, session_id, authorization_id = sys.argv[1:]
store = SQLiteScientificAuthorizationStore(Path(database))
current = store.read(session_id, authorization_id)
if current is None or current.status != "authorized":
    sys.stdout.write("missing")
    store.close()
    raise SystemExit(0)
pid = os.fork()
if pid == 0:
    try:
        SQLiteScientificAuthorizationStore(Path(database))
    except ScientificAuthorizationStoreError as exc:
        os.write(1, exc.code.encode())
    else:
        os.write(1, b"opened")
    os._exit(0)
_child, status = os.waitpid(pid, 0)
again = store.read(session_id, authorization_id)
store.close()
if status != 0 or again is None or again.status != "authorized":
    sys.stdout.write("changed")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", script, str(path), session_id, authorization_id],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_concurrent_authorization_creates_one_record(tmp_path: Path) -> None:
    service, _runs, runner, _gate, _drafter, approval, _finished, _probe = _world(tmp_path)
    calls = runner.calls
    barrier = threading.Barrier(2)
    results: list[str] = []

    def attempt(key: str) -> None:
        barrier.wait()
        reply = _authorize(service, approval.approval_id, key)
        results.append(reply.authorization.authorization_id)

    threads = [threading.Thread(target=attempt, args=(key,)) for key in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1
    assert runner.calls == calls


def test_claim_rechecks_the_live_binding(tmp_path: Path) -> None:
    service, _runs, runner, _gate, _drafter, approval, _finished, _probe = _world(tmp_path)
    granted = _authorize(service, approval.approval_id)
    calls = runner.calls
    entered = threading.Barrier(2)
    release = threading.Barrier(2)

    def before() -> None:
        entered.wait(timeout=2)
        release.wait(timeout=2)

    service._before_claim = before
    shifted, _probe = _ready_readiness(
        tmp_path / "shift", when=datetime(2026, 10, 12, 18, 0, tzinfo=UTC)
    )
    outcome: dict[str, str] = {}

    def consume() -> None:
        outcome["value"] = service.claim_once(
            session_id=SESSION, authorization_id=granted.authorization.authorization_id
        )

    thread = threading.Thread(target=consume)
    thread.start()
    entered.wait(timeout=2)
    service._readiness = shifted
    release.wait(timeout=2)
    thread.join(timeout=2)
    assert outcome["value"] == "unavailable"
    current = service.read(
        session_id=SESSION, authorization_id=granted.authorization.authorization_id
    )
    assert current.authorization.consumed_at is None
    assert current.authorization.execution_authorized is True
    assert runner.calls == calls


def test_mutated_exact_records_become_adapter_failures(tmp_path: Path) -> None:
    service, _runs, _runner, _gate, _drafter, approval, _finished, _probe = _world(tmp_path)
    granted = _authorize(service, approval.approval_id)
    record = granted.authorization
    mutations = {
        "runtime_version": "/tmp/secret",
        "authorization_id": "authorization_" + ("zz" * 16),
        "created_at": "2026-10-12T18:00:00+00:00",
        "updated_at": "2026-10-12T15:00:00+00:00",
        "request_digest": "ab",
        "image_digest": "sha256:/tmp/secret",
        "budget": {**record.budget, "currency": "/tmp/secret"},
        "events": (),
    }
    for field, value in mutations.items():
        broken = replace(record)
        object.__setattr__(broken, field, value)
        _expect_adapter_failure(broken)
    reply = ScientificAuthorizationReply("authorization_created", AUTHORIZED_TEXT, SESSION, record)
    object.__setattr__(reply, "scientific_execution", True)
    _expect_adapter_reply(reply)
    del service


def _expect_adapter_failure(record: object) -> None:
    reply = ScientificAuthorizationReply(
        "authorization_created",
        AUTHORIZED_TEXT,
        SESSION,
        record,  # type: ignore[arg-type]
    )
    _expect_adapter_reply(reply)
    listed = ScientificAuthorizationListReply(
        "authorizations_observed",
        "Scientific authorization history. Not scientific execution.",
        SESSION,
        (record,),  # type: ignore[arg-type]
    )
    status, body = handle_scientific_authorization(
        _Listing(listed), "GET", AUTHORIZATIONS_PATH, b"", SESSION
    )
    encoded = json.dumps(body)
    assert status == 200
    assert body["status"] == "adapter_failure"
    assert body["authorizations"] == []
    assert "/tmp" not in encoded
    assert "secret" not in encoded


def _expect_adapter_reply(reply: ScientificAuthorizationReply) -> None:
    status, body = handle_scientific_authorization(
        _Returning(reply),
        "POST",
        AUTHORIZATIONS_PATH,
        json.dumps({"approval_id": "approval_1", "idempotency_key": "edge"}).encode(),
        SESSION,
    )
    encoded = json.dumps(body)
    assert status == 200
    assert body["status"] == "adapter_failure"
    assert body["authorization"] is None
    assert "/tmp" not in encoded
    assert "secret" not in encoded


class _Returning:
    def __init__(self, reply: ScientificAuthorizationReply) -> None:
        self._reply = reply

    def authorize(self, **_kwargs: object) -> ScientificAuthorizationReply:
        return self._reply

    def list_authorizations(self, **_kwargs: object) -> ScientificAuthorizationReply:
        return self._reply

    def read(self, **_kwargs: object) -> object:
        return object()

    def revoke(self, **_kwargs: object) -> object:
        return object()


class _Listing:
    def __init__(self, reply: ScientificAuthorizationListReply) -> None:
        self._reply = reply

    def list_authorizations(self, **_kwargs: object) -> ScientificAuthorizationListReply:
        return self._reply


def test_corrupted_rows_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "scientific-authorizations.sqlite3"
    store = SQLiteScientificAuthorizationStore(path)
    service, _runs, _runner, _gate, _drafter, approval, _finished, _probe = _world(
        tmp_path, store=store
    )
    granted = _authorize(service, approval.approval_id)
    auth_id = granted.authorization.authorization_id
    original = store._conn.execute(
        "SELECT document, created_at, updated_at, binding_hash, status, consumed_at FROM authorizations"
    ).fetchone()

    def restore() -> None:
        store._conn.execute(
            """
            UPDATE authorizations
            SET document = ?, created_at = ?, updated_at = ?, binding_hash = ?,
                status = ?, consumed_at = ?
            WHERE authorization_id = ?
            """,
            (*original, auth_id),
        )

    def unreadable() -> None:
        with pytest.raises(ScientificAuthorizationStoreError) as caught:
            store.read(SESSION, auth_id)
        assert caught.value.code == "authorization_record_invalid"
        assert str(caught.value) == caught.value.code

    parsed = json.loads(original["document"])
    parsed["created_at"] = "2026-10-12T18:00:00+00:00"
    parsed["events"][0]["recorded_at"] = parsed["created_at"]
    backward = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    store._conn.execute(
        "UPDATE authorizations SET document = ?, created_at = ? WHERE authorization_id = ?",
        (backward, parsed["created_at"], auth_id),
    )
    unreadable()
    restore()
    parsed = json.loads(original["document"])
    parsed["events"][0]["status"] = "revoked"
    parsed["events"][0]["text"] = REVOKED_TEXT
    illegal = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    store._conn.execute(
        "UPDATE authorizations SET document = ? WHERE authorization_id = ?",
        (illegal, auth_id),
    )
    unreadable()
    restore()
    parsed = json.loads(original["document"])
    parsed["status"] = "revoked"
    parsed["text"] = REVOKED_TEXT
    mismatched = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    store._conn.execute(
        "UPDATE authorizations SET document = ?, status = 'revoked' WHERE authorization_id = ?",
        (mismatched, auth_id),
    )
    unreadable()
    restore()
    for statement in (
        "UPDATE authorizations SET binding_hash = 1 WHERE authorization_id = ?",
        "UPDATE authorizations SET document = x'ff' WHERE authorization_id = ?",
        "UPDATE authorizations SET consumed_at = 'yesterday' WHERE authorization_id = ?",
        "UPDATE authorizations SET consumed_at = '2026-10-12T15:00:00+00:00' WHERE authorization_id = ?",
        "UPDATE authorizations SET document = '{ }' WHERE authorization_id = ?",
        "UPDATE authorizations SET consumed_at = 1 WHERE authorization_id = ?",
        "UPDATE events SET sequence = x'ff' WHERE authorization_id = ?",
    ):
        store._conn.execute(statement, (auth_id,))
        unreadable()
        if statement.startswith("UPDATE events"):
            store._conn.execute(
                "UPDATE events SET sequence = 1 WHERE authorization_id = ?",
                (auth_id,),
            )
        restore()
    parsed = json.loads(original["document"])
    parsed["authorization_id"] = "not-an-authorization"
    renamed = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    store._conn.execute(
        "UPDATE authorizations SET document = ? WHERE authorization_id = ?",
        (renamed, auth_id),
    )
    unreadable()
    store._conn.execute("PRAGMA foreign_keys = OFF")
    store._conn.executescript(
        """
        CREATE TABLE authorizations_nullable (
            authorization_id TEXT PRIMARY KEY,
            session_id TEXT,
            binding_hash TEXT,
            status TEXT,
            consumed_at TEXT,
            document TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        INSERT INTO authorizations_nullable
            SELECT authorization_id, session_id, binding_hash, status, consumed_at,
                   document, created_at, updated_at
            FROM authorizations;
        DROP TABLE authorizations;
        ALTER TABLE authorizations_nullable RENAME TO authorizations;
        """
    )
    store._conn.execute(
        "UPDATE authorizations SET status = NULL WHERE authorization_id = ?",
        (auth_id,),
    )
    unreadable()
    store.close()


def test_failed_authorization_open_releases_the_run_lock(tmp_path: Path, monkeypatch) -> None:
    run_db = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_DB", str(run_db))
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE", "memory")

    def boom():
        raise ScientificAuthorizationConfigurationError("scientific_authorization_db_required")

    monkeypatch.setattr("cellertz.conversation.__main__.open_scientific_authorization_store", boom)
    monkeypatch.setattr(
        "cellertz.conversation.__main__.serve_conversation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("server started")),
    )
    with pytest.raises(ScientificAuthorizationConfigurationError):
        main()
    from cellertz.application.approved_run_store import SQLiteApprovedRunStore

    reopened = SQLiteApprovedRunStore(run_db)
    reopened.close()


def _invoke(handler: type, method: str, path: str, body: bytes) -> tuple[int, dict]:
    instance = handler.__new__(handler)
    instance.path = path
    instance.headers = {"Content-Length": str(len(body))}
    instance.rfile = BytesIO(body)
    instance.wfile = BytesIO()

    def send_response(code: int) -> None:
        instance.status_code = code

    instance.send_response = send_response
    instance.send_header = lambda *_args: None
    instance.end_headers = lambda: None
    if method == "GET":
        instance.do_GET()
    else:
        instance.do_POST()
    instance.wfile.seek(0)
    return instance.status_code, json.loads(instance.wfile.getvalue())
