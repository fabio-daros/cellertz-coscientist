"""The results route opens one recovered attempt and does not fall back."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

from cellertz.application.apptainer_demonstration import recover_completed_attempt
from cellertz.application.attempt_evidence import load_catalog
from cellertz.application.attempt_inspection import (
    handle_attempt_get,
    inspect_attempt,
    structure_url,
)
from cellertz.application.conversation_transport import configured_handler
from tests.test_bindcraft_output_ingestion import _ATTEMPT, _DESIGN, _build_attempt

_CANDIDATE = _DESIGN
_QUERY = {"candidate": _CANDIDATE, "model": "model1"}


def test_recovered_inspection_cites_constraints_and_both_structures(tmp_path: Path, monkeypatch) -> None:
    _publish(tmp_path, monkeypatch)
    catalog = load_catalog()
    status, payload = inspect_attempt(catalog, _ATTEMPT, _QUERY)
    assert status == 200
    assert payload["attempt_id"] == _ATTEMPT
    assert payload["candidate"] == _CANDIDATE
    assert payload["selected_model"] == "model1"
    assert payload["origin"]["kind"] == "recovered"
    assert payload["live_processing"] is False
    assert payload["specialist_activity"] == []
    assert payload["pdb_verified"] is True
    assert payload["execution_compliance_established"] is False
    assert payload["scientific_validation_established"] is False
    assert payload["ranking_score"] == "not_recorded"
    assert "i_pTM" in payload["ranking_note"]
    assert "ipSAE" in payload["ranking_note"]
    constraints = payload["execution_constraints"]
    assert constraints["unmet"] is True
    assert constraints["requested_max_trajectories"] == 1
    assert len(constraints["accepted_trajectory_files"]) == 1
    assert len(constraints["low_confidence_trajectory_files"]) == 2
    metrics = {row["metric"]: row for row in payload["metrics"]}
    assert metrics["n_InterfaceResidues"]["model2"]["kind"] == "recorded_zero"
    assert metrics["InterfaceHbondsPercentage"]["model2"]["kind"] == "empty_field"
    assert metrics["pLDDT"]["model1"]["kind"] == "recorded"
    model1 = payload["models"]["model1"]
    model2 = payload["models"]["model2"]
    assert model1["representative"] is True
    assert model2["representative"] is False
    assert model1["pdb_url"] == structure_url(_ATTEMPT, "model1")
    assert model1["calculated_contacts"]["residue_pairs"] == 1
    assert model2["calculated_contacts"]["residue_pairs"] == 1
    assert model1["recorded_contacts"]["residue_pairs"] is None
    assert model1["sha256"] in json.dumps(payload["evidence_references"])
    assert "attempt.2LUTUOX4" not in json.dumps(payload)
    artifact = handle_attempt_get(model1["pdb_url"], catalog)
    assert artifact.status == 200
    assert hashlib.sha256(artifact.body).hexdigest() == model1["sha256"]
    assert artifact.body.startswith(b"ATOM")


def test_a_wrong_candidate_does_not_open_the_historical_attempt(tmp_path: Path, monkeypatch) -> None:
    _publish(tmp_path, monkeypatch)
    catalog = load_catalog()
    status, payload = inspect_attempt(
        catalog,
        "attempt.2LUTUOX4",
        {"candidate": _CANDIDATE, "model": "model1"},
    )
    assert status == 404
    assert payload["status"] == "candidate_mismatch"
    assert "model1.pdb" not in json.dumps(payload)
    missing, rejected = inspect_attempt(catalog, _ATTEMPT, {"candidate": _CANDIDATE, "model": "model9"})
    assert missing == 404
    assert rejected["status"] == "unknown_model"
    escaped = handle_attempt_get(f"/api/attempts/{_ATTEMPT}/structures/../review.json", catalog)
    assert escaped[0] == 404
    assert escaped[1]["status"] == "unknown_attempt_route"


def test_hash_mismatch_is_not_served(tmp_path: Path, monkeypatch) -> None:
    attempt = _publish(tmp_path, monkeypatch)
    (attempt / "model1.pdb").write_bytes(b"ATOM  changed\n")
    catalog = load_catalog()
    artifact = handle_attempt_get(structure_url(_ATTEMPT, "model1"), catalog)
    assert artifact[0] == 409
    assert artifact[1]["status"] == "hash_mismatch"
    status, payload = inspect_attempt(catalog, _ATTEMPT, _QUERY)
    assert status == 200
    assert payload["pdb_verified"] is False
    assert payload["models"]["model1"]["hash_matches"] is False


def test_http_route_serves_the_recovered_structure(tmp_path: Path, monkeypatch) -> None:
    _publish(tmp_path, monkeypatch)
    handler = configured_handler(object())
    status, payload = _invoke(
        handler,
        f"/api/attempts/{_ATTEMPT}/inspection?candidate={_CANDIDATE}&model=model2",
    )
    assert status == 200
    body = json.loads(payload)
    assert body["selected_model"] == "model2"
    assert body["execution_constraints"]["unmet"] is True
    code, pdb = _invoke(handler, body["models"]["model2"]["pdb_url"])
    assert code == 200
    assert pdb.startswith(b"ATOM")


def _publish(tmp_path: Path, monkeypatch) -> Path:
    attempt = _build_attempt(tmp_path)
    outcome = recover_completed_attempt(attempt)
    assert outcome["status"] == "verified"
    monkeypatch.setenv("CELLERTZ_ATTEMPT_CATALOG", str(tmp_path))
    return attempt


def _invoke(handler: type, path: str) -> tuple[int, bytes]:
    instance = handler.__new__(handler)
    instance.path = path
    instance.headers = {}
    instance.rfile = BytesIO()
    instance.wfile = BytesIO()
    instance.status_code = None
    instance.send_response = lambda code: setattr(instance, "status_code", code)
    instance.send_header = lambda *_args: None
    instance.end_headers = lambda: None
    instance.do_GET()
    return instance.status_code, instance.wfile.getvalue()
