"""ipSAE provenance uses the existing calculator and leaves incomplete records unchanged."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from cellertz.application.design_journey import (
    DesignJourneyService,
    SQLiteDesignJourneyStore,
    handle_design_journey,
)
from cellertz.application.journey_specialist_progress import apply_specialist_progress
from cellertz.application.results_specialist_projection import project_saved_specialist_evidence
from cellertz.application.scoring_audit import AUDIT_ABSOLUTE_TOLERANCE, main
from cellertz.domains.protein_design.adapters.bindcraft import candidate_id_for_design
from cellertz.domains.protein_design.execution.bindcraft_scientific import BINDCRAFT_IPSAE_CONFIG
from cellertz.tools.protein_scoring.ipsae import IpSAEConfig, ProteinIpSAECalculator

_ATTEMPT = "attempt.provenance1"
_DESIGN = "ProvenanceTiny"
_PLAN = "plan_provenance"
_REVISION = 4
_DIGEST = "digest-provenance"
_JOURNEY = "journey_provenance"
_ROUND = "round_provenance"


def _directional(pae_value: float) -> float:
    """One inter-chain pair. Pair inclusion is strict less-than the PAE cutoff."""

    if pae_value >= BINDCRAFT_IPSAE_CONFIG.pae_cutoff:
        return 0.0
    length = max(1, 26.523)
    d0 = 1.24 * (length - 15.0) ** (1.0 / 3.0) - 1.8
    return 1.0 / (1.0 + (pae_value / d0) ** 2.0)


def _ca_line(serial: int, resname: str, chain: str, resseq: int, x: float) -> str:
    return (
        f"ATOM  {serial:5d}  CA  {resname:<3} {chain}{resseq:4d}    "
        f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
    )


def _pdb(shift: float) -> str:
    return "\n".join([_ca_line(1, "GLY", "A", 1, shift), _ca_line(2, "ALA", "B", 1, shift + 3.0), "END"]) + "\n"


def _write_model(pae_dir: Path, relaxed: Path, design: str, number: int, pae_ab: float, pae_ba: float) -> None:
    pdb_path = relaxed / f"{design}_model{number}.pdb"
    pdb_path.write_text(_pdb(0.0 if number == 1 else 7.0), encoding="utf-8")
    pae_path = pae_dir / f"{design}_model{number}_pae.npz"
    matrix = np.zeros((2, 2), dtype=np.float32)
    matrix[0, 1] = np.float32(pae_ab)
    matrix[1, 0] = np.float32(pae_ba)
    np.savez_compressed(
        pae_path,
        pae_angstrom=matrix,
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
            {"chain": "A", "index": 0, "insertion_code": "", "model_residue_index": 1, "residue_name": "GLY", "residue_number": 1},
            {"chain": "B", "index": 1, "insertion_code": "", "model_residue_index": 51, "residue_name": "ALA", "residue_number": 1},
        ],
        "source": "prediction_model.aux",
    }
    (pae_dir / f"{design}_model{number}_residue_order.json").write_text(json.dumps(order) + "\n", encoding="utf-8")


def _artifacts(root: Path, pairs: tuple[tuple[float, float], tuple[float, float]]) -> Path:
    attempt = root / _ATTEMPT
    designs = attempt / "designs"
    relaxed = designs / "MPNN" / "Relaxed"
    accepted = designs / "Accepted"
    ranked = accepted / "Ranked"
    pae = designs / "CellertzEvidence" / "PAE"
    for directory in (relaxed, accepted, ranked, pae):
        directory.mkdir(parents=True)
    model1 = _pdb(0.0)
    (relaxed / f"{_DESIGN}_model1.pdb").write_text(model1, encoding="utf-8")
    (accepted / f"{_DESIGN}_model1.pdb").write_text(model1, encoding="utf-8")
    (ranked / f"1_{_DESIGN}_model1.pdb").write_text(model1, encoding="utf-8")
    (designs / "final_design_stats.csv").write_text(f"Design,Sequence\n{_DESIGN},A\n", encoding="utf-8")
    _write_model(pae, relaxed, _DESIGN, 1, *pairs[0])
    _write_model(pae, relaxed, _DESIGN, 2, *pairs[1])
    accepted_copy = accepted / f"{_DESIGN}_model1.pdb"
    accepted_copy.write_text((relaxed / f"{_DESIGN}_model1.pdb").read_text(encoding="utf-8"), encoding="utf-8")
    (ranked / f"1_{_DESIGN}_model1.pdb").write_text(accepted_copy.read_text(encoding="utf-8"), encoding="utf-8")
    models = {}
    for number in (1, 2):
        pdb_path = relaxed / f"{_DESIGN}_model{number}.pdb"
        models[f"model{number}"] = {"sha256": hashlib.sha256(pdb_path.read_bytes()).hexdigest()}
    review = {
        "attempt_id": _ATTEMPT,
        "design": _DESIGN,
        "models": models,
        "scientific_validation": False,
        "experimental_validation": False,
    }
    (attempt / "review.json").write_text(json.dumps(review), encoding="utf-8")
    return attempt


def _execution(attempt: Path) -> dict:
    candidate_id = candidate_id_for_design(_DESIGN)
    return {
        "attempt_id": _ATTEMPT,
        "attempt_directory": str(attempt),
        "terminal": True,
        "accepted_candidate": True,
        "candidate_id": candidate_id,
        "candidate_identity": {
            "design": _DESIGN,
            "candidate_id": candidate_id,
            "execution_id": _ATTEMPT,
            "sequence": "A",
        },
        "plan_id": _PLAN,
        "revision": _REVISION,
        "digest": _DIGEST,
    }


def _document(attempt: Path, score: float) -> dict:
    execution = _execution(attempt)
    candidate_id = execution["candidate_id"]
    hashes = json.loads((attempt / "review.json").read_text(encoding="utf-8"))["models"]
    return {
        "journey_id": _JOURNEY,
        "request_id": "request_provenance",
        "active_round_id": _ROUND,
        "execution": execution,
        "rounds": [
            {
                "round_id": _ROUND,
                "execution": execution,
                "specialist_stages": [
                    {
                        "stage_id": "scoring",
                        "status": "completed",
                        "agent_executed": True,
                        "evidence_references": [
                            {
                                "attempt_id": _ATTEMPT,
                                "candidate_id": candidate_id,
                                "plan_id": _PLAN,
                                "revision": _REVISION,
                                "digest": _DIGEST,
                            }
                        ],
                        "output": {
                            "ranking_score": score,
                            "models": [
                                {"model_id": model_id, "pdb_sha256": item["sha256"]}
                                for model_id, item in hashes.items()
                            ],
                        },
                    }
                ],
                "results_publication": {"published": True},
                "scoring_audits": [],
            }
        ],
    }


class _Calls:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, candidate):
        self.calls += 1
        raise RuntimeError("stopped")


def test_controlled_inputs_follow_the_existing_directional_and_mean_rules(tmp_path: Path) -> None:
    attempt = _artifacts(tmp_path, ((5.0, 2.0), (4.0, 8.0)))
    designs = attempt / "designs"
    calculator = ProteinIpSAECalculator()
    first = calculator.calculate(
        pae_path=designs / "CellertzEvidence" / "PAE" / f"{_DESIGN}_model1_pae.npz",
        structure_path=designs / "MPNN" / "Relaxed" / f"{_DESIGN}_model1.pdb",
        config=BINDCRAFT_IPSAE_CONFIG,
    )
    expected_ab = _directional(5.0)
    expected_ba = _directional(2.0)
    assert first.ipsae_ab == pytest.approx(expected_ab)
    assert first.ipsae_ba == pytest.approx(expected_ba)
    assert first.score == pytest.approx(max(expected_ab, expected_ba))
    assert first.score != pytest.approx((expected_ab + expected_ba) / 2)
    cutoff = calculator.calculate(
        pae_path=_cutoff_pae(tmp_path),
        structure_path=designs / "MPNN" / "Relaxed" / f"{_DESIGN}_model1.pdb",
        config=BINDCRAFT_IPSAE_CONFIG,
    )
    assert cutoff.ipsae_ab == 0.0
    assert cutoff.ipsae_ba == 0.0
    wide = IpSAEConfig(
        pae_cutoff=BINDCRAFT_IPSAE_CONFIG.pae_cutoff,
        distance_cutoff=99.0,
        chain_a="A",
        chain_b="B",
        aggregation="max",
        protocol_id=BINDCRAFT_IPSAE_CONFIG.protocol_id,
    )
    changed_distance = calculator.calculate(
        pae_path=designs / "CellertzEvidence" / "PAE" / f"{_DESIGN}_model1_pae.npz",
        structure_path=designs / "MPNN" / "Relaxed" / f"{_DESIGN}_model1.pdb",
        config=wide,
    )
    assert changed_distance.ipsae_ab == first.ipsae_ab
    from cellertz.application.journey_specialist_progress import _candidate, _evidence_provider

    evidence = _evidence_provider(execution := _execution(attempt), calculator).evaluate(_candidate(execution))
    second_score = max(_directional(4.0), _directional(8.0))
    expected_mean = (first.score + second_score) / 2
    assert evidence.model_aggregation == "mean"
    assert evidence.ranking_value == pytest.approx(expected_mean)
    assert evidence.ranking_value != pytest.approx(max(first.score, second_score))
    assert evidence.models[0].ipsae == pytest.approx(first.score)
    assert evidence.models[0].residue_order_sha256 is not None
    assert evidence.ipsae_min == pytest.approx(min(first.score, second_score))
    assert evidence.ipsae_max == pytest.approx(max(first.score, second_score))


def _cutoff_pae(root: Path) -> Path:
    path = root / "cutoff_pae.npz"
    matrix = np.full((2, 2), np.float32(10.0), dtype=np.float32)
    np.savez_compressed(path, pae_angstrom=matrix)
    return path


def test_future_specialist_scoring_persists_the_calculator_provenance(tmp_path: Path) -> None:
    attempt = _artifacts(tmp_path, ((5.0, 2.0), (4.0, 8.0)))
    document = _document(attempt, 0.11)
    document["rounds"][0]["specialist_stages"] = []
    document["rounds"][0]["specialist_events"] = []
    review_before = (attempt / "review.json").read_bytes()
    apply_specialist_progress(document, _ROUND, save=lambda _document: None, catalog_root=None)
    scoring = next(item for item in document["rounds"][0]["specialist_stages"] if item["stage_id"] == "scoring")
    recorded = scoring["output"]["ipsae_provenance"]
    assert recorded["model_aggregation"] == "mean"
    assert recorded["protocol_parameters"]["within_model_aggregation"] == "max"
    assert recorded["protocol_parameters"]["pae_cutoff"] == 10.0
    assert recorded["protocol_parameters"]["distance_cutoff"] == 10.0
    assert recorded["distance_cutoff_used_by_primary_ipsae"] is False
    assert recorded["calculator"]["implementation"] == "ProteinIpSAECalculator"
    assert recorded["plan_id"] == _PLAN
    assert recorded["plan_revision"] == _REVISION
    assert recorded["digest"] == _DIGEST
    assert recorded["calculation_started_at"]
    assert recorded["calculation_completed_at"]
    assert scoring["output"]["ranking_score"] == pytest.approx(recorded["ranking_value"])
    assert {item["model_number"] for item in recorded["models"]} == {1, 2}
    assert all(item["residue_order_sha256"] for item in recorded["models"])
    assert recorded["scientific_validation_established"] is False
    assert recorded["experimental_validation_established"] is False
    assert (attempt / "review.json").read_bytes() == review_before
    projected = project_saved_specialist_evidence(
        attempt_id=_ATTEMPT,
        design=_DESIGN,
        review_models={
            model_id: item["sha256"]
            for model_id, item in json.loads(review_before)["models"].items()
        },
        documents=[document],
    )
    assert projected["ranking_score"] == pytest.approx(recorded["ranking_value"])
    assert projected["aggregation"]["model_aggregation"] == "mean"
    assert projected["per_model_ranking_scores"][0]["ipsae_ab"] == pytest.approx(_directional(5.0))
    assert projected["applies_to_each_model"] is False
    assert projected["scientific_validation_established"] is False


def test_audit_records_agreement_disagreement_and_does_not_replace_the_specialist(tmp_path: Path) -> None:
    attempt = _artifacts(tmp_path, ((5.0, 2.0), (4.0, 8.0)))
    expected = (max(_directional(5.0), _directional(2.0)) + max(_directional(4.0), _directional(8.0))) / 2
    document = _document(attempt, expected)
    review_before = (attempt / "review.json").read_bytes()
    store = SQLiteDesignJourneyStore(tmp_path / "journeys.sqlite")
    store.put(document)
    planted = store.get(_JOURNEY)
    stages_before = json.dumps(planted["rounds"][0]["specialist_stages"], sort_keys=True)
    publication_before = json.dumps(planted["rounds"][0]["results_publication"], sort_keys=True)
    service = DesignJourneyService(object(), store=store)
    status, payload = service.record_scoring_audit(_JOURNEY, _ATTEMPT)
    audit = payload["scoring_audit"]
    assert status == 200
    assert payload["status"] == "completed"
    assert audit["agrees"] is True
    assert audit["agreement"] == "agrees"
    assert audit["absolute_tolerance"] == AUDIT_ABSOLUTE_TOLERANCE
    assert audit["original_ranking_score"] == pytest.approx(expected)
    assert audit["recalculated_ranking_score"] == pytest.approx(expected)
    assert audit["model_aggregation"] == "mean"
    assert audit["replaces_specialist_record"] is False
    assert audit["scientific_validation_established"] is False
    assert {item["model_number"] for item in audit["calculated_models"]} == {1, 2}
    stored = store.get(_JOURNEY)
    assert stored["rounds"][0]["specialist_stages"][0]["output"]["ranking_score"] == pytest.approx(expected)
    assert json.dumps(stored["rounds"][0]["results_publication"], sort_keys=True) == publication_before
    assert json.dumps(stored["rounds"][0]["specialist_stages"], sort_keys=True) == stages_before
    assert (attempt / "review.json").read_bytes() == review_before
    repeated, repeated_payload = service.record_scoring_audit(_JOURNEY, _ATTEMPT)
    assert repeated == 200
    assert repeated_payload["status"] == "idempotent"
    assert repeated_payload["scoring_audit"]["audit_id"] == audit["audit_id"]
    assert len(store.get(_JOURNEY)["rounds"][0]["scoring_audits"]) == 1
    service.close()

    disagreed = _document(attempt, 0.11)
    disagreed["journey_id"] = "journey_disagree"
    disagreed["request_id"] = "request_disagree"
    memory = SQLiteDesignJourneyStore(tmp_path / "disagree.sqlite")
    memory.put(disagreed)
    other = DesignJourneyService(object(), store=memory)
    bad, bad_payload = other.record_scoring_audit("journey_disagree", _ATTEMPT)
    assert bad == 200
    assert bad_payload["scoring_audit"]["agrees"] is False
    assert bad_payload["scoring_audit"]["agreement"] == "disagrees"
    assert bad_payload["scoring_audit"]["original_ranking_score"] == pytest.approx(0.11)
    kept = memory.get("journey_disagree")
    assert kept["rounds"][0]["specialist_stages"][0]["output"]["ranking_score"] == pytest.approx(0.11)
    other.close()


def test_altered_identity_and_an_interrupted_audit_do_not_score_again(tmp_path: Path) -> None:
    attempt = _artifacts(tmp_path, ((5.0, 2.0), (4.0, 8.0)))
    document = _document(attempt, 0.11)
    review = json.loads((attempt / "review.json").read_text(encoding="utf-8"))
    review["models"]["model1"]["sha256"] = "0" * 64
    (attempt / "review.json").write_text(json.dumps(review), encoding="utf-8")
    store = SQLiteDesignJourneyStore(tmp_path / "journeys.sqlite")
    store.put(document)
    service = DesignJourneyService(object(), store=store, specialist_scorer=lambda *_args, **_kwargs: None)
    calls = _Calls()
    from cellertz.application import scoring_audit

    original_calculate = scoring_audit._calculate

    def _blocked(execution, calculator):
        calls.evaluate(None)
        return original_calculate(execution, calculator)

    scoring_audit._calculate = _blocked
    try:
        failed, failed_payload = service.record_scoring_audit(_JOURNEY, _ATTEMPT)
        assert failed == 409
        assert failed_payload["status"] == "verification_failed"
        assert calls.calls == 0
        again, again_payload = service.record_scoring_audit(_JOURNEY, _ATTEMPT)
        assert again == 200
        assert again_payload["status"] == "idempotent"
        assert calls.calls == 0
        review["models"]["model1"]["sha256"] = hashlib.sha256(
            (attempt / "designs" / "MPNN" / "Relaxed" / f"{_DESIGN}_model1.pdb").read_bytes()
        ).hexdigest()
        (attempt / "review.json").write_text(json.dumps(review), encoding="utf-8")
        pae_path = attempt / "designs" / "CellertzEvidence" / "PAE" / f"{_DESIGN}_model1_pae.npz"
        sidecar = pae_path.with_name(pae_path.name.replace("_pae.npz", "_residue_order.json"))
        with np.load(pae_path, allow_pickle=False) as payload:
            stored = {key: payload[key] for key in payload.files}
        stored["model_number"] = np.asarray(2, dtype=np.int32)
        np.savez_compressed(pae_path, **stored)
        order = json.loads(sidecar.read_text(encoding="utf-8"))
        order["pae_sha256"] = hashlib.sha256(pae_path.read_bytes()).hexdigest()
        sidecar.write_text(json.dumps(order) + "\n", encoding="utf-8")
        associated, associated_payload = service.record_scoring_audit(_JOURNEY, _ATTEMPT)
        assert associated == 409
        assert associated_payload["status"] == "verification_failed"
        assert calls.calls == 0
        _write_model(
            pae_path.parent,
            attempt / "designs" / "MPNN" / "Relaxed",
            _DESIGN,
            1,
            5.0,
            2.0,
        )
        stopped, stopped_payload = service.record_scoring_audit(_JOURNEY, _ATTEMPT)
    finally:
        scoring_audit._calculate = original_calculate
    assert stopped == 409
    assert stopped_payload["status"] == "audit_interrupted"
    assert calls.calls == 1
    quiet, quiet_payload = service.record_scoring_audit(_JOURNEY, _ATTEMPT)
    assert quiet == 409
    assert quiet_payload["status"] == "audit_interrupted"
    assert calls.calls == 1
    scoring_audit._calculate = original_calculate
    resumed, resumed_payload = service.record_scoring_audit(_JOURNEY, _ATTEMPT, resume_interrupted=True)
    assert resumed == 200
    assert resumed_payload["status"] == "completed"
    assert resumed_payload["scoring_audit"]["audit_id"] == stopped_payload["scoring_audit"]["audit_id"]
    assert resumed_payload["scoring_audit"]["agreement"] == "disagrees"
    stored = store.get(_JOURNEY)
    assert stored["rounds"][0]["specialist_stages"][0]["output"]["ranking_score"] == pytest.approx(0.11)
    service.close()
    reopened = SQLiteDesignJourneyStore(tmp_path / "journeys.sqlite")
    kept = reopened.get(_JOURNEY)
    assert kept["rounds"][0]["scoring_audits"][-1]["status"] == "completed"
    file_before = hashlib.sha256((tmp_path / "journeys.sqlite").read_bytes()).hexdigest()
    reader = DesignJourneyService(object(), store=reopened)

    def _refuse_get(*_args, **_kwargs):
        raise AssertionError("GET calculated ipSAE")

    original = ProteinIpSAECalculator.calculate
    ProteinIpSAECalculator.calculate = _refuse_get
    try:
        got = handle_design_journey(reader, "GET", f"/api/design-journeys/{_JOURNEY}", b"")
        projected = project_saved_specialist_evidence(
            attempt_id=_ATTEMPT,
            design=_DESIGN,
            review_models={
                model_id: item["sha256"]
                for model_id, item in json.loads((attempt / "review.json").read_text(encoding="utf-8"))["models"].items()
            },
            documents=reader.saved_documents(),
        )
        assert got is not None and got[0] == 200
        assert got[1]["rounds"][0]["scoring_audits"]
        assert projected["ranking_score"] == pytest.approx(0.11)
        assert projected["original_ranking_score"] == pytest.approx(0.11)
        assert projected["scoring_audit"]["records"][-1]["recalculated_ranking_score"] != pytest.approx(0.11)
        assert projected["ipsae_provenance"] == "not_recorded"
    finally:
        ProteinIpSAECalculator.calculate = original
        reader.close()
    assert hashlib.sha256((tmp_path / "journeys.sqlite").read_bytes()).hexdigest() == file_before


def test_audit_cli_is_idempotent_and_get_does_not_invoke_it(tmp_path: Path, monkeypatch) -> None:
    attempt = _artifacts(tmp_path, ((5.0, 5.0), (5.0, 5.0)))
    expected = _directional(5.0)
    document = _document(attempt, expected)
    database = tmp_path / "journeys.sqlite"
    store = SQLiteDesignJourneyStore(database)
    store.put(document)
    store.close()
    monkeypatch.setenv("CELLERTZ_DESIGN_JOURNEY_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_DESIGN_JOURNEY_DB", str(database))
    assert main(["--journey-id", _JOURNEY, "--attempt-id", _ATTEMPT]) == 0
    assert main(["--journey-id", _JOURNEY, "--attempt-id", _ATTEMPT]) == 0
    reopened = SQLiteDesignJourneyStore(database)
    saved = reopened.get(_JOURNEY)
    assert len(saved["rounds"][0]["scoring_audits"]) == 1
    assert saved["rounds"][0]["specialist_stages"][0]["output"]["ranking_score"] == pytest.approx(expected)
    reopened.close()
    extra_service = DesignJourneyService(object(), store=SQLiteDesignJourneyStore(database))
    try:
        extra = handle_design_journey(
            extra_service,
            "POST",
            f"/api/design-journeys/{_JOURNEY}/scoring-audit",
            b'{"attempt_id":"attempt.provenance1","unexpected":true}',
        )
    finally:
        extra_service.close()
    assert extra is not None and extra[0] == 400
