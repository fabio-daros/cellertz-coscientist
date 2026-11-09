"""CPU checks for the recovered-attempt ipSAE acceptance command.

Synthetic files exercise the gates. They are not the host score for
attempt.c5c1f13e.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from cellertz.application.recovered_ipsae_acceptance import (
    RECOVERED_ATTEMPT_ID,
    RECOVERED_DESIGN,
    RECOVERED_PAE_SHA256,
    RECOVERED_PDB_SHA256,
    analyze_recovered_artifacts,
    main,
)
from cellertz.tools.protein_scoring.ipsae import ProteinIpSAECalculator

DESIGN = "PDL1_l1_s1_mpnn1"
EXECUTION = "attempt.synthetic"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pdb(*chains: str) -> str:
    lines = []
    for index, chain in enumerate(chains, start=1):
        lines.append(
            f"ATOM  {index:5d}  CA  ALA {chain}{index:4d}"
            "       0.000   0.000   0.000  1.00 90.00           C"
        )
    return "\n".join(lines) + "\n"


def _write_pair(
    designs: Path,
    model: int,
    *,
    design: str = DESIGN,
    chains: tuple[str, ...] = ("A", "B"),
    candidate_id: str | None = None,
    model_number: int | None = None,
    pae: np.ndarray | None = None,
    dtype: np.dtype = np.float32,
) -> tuple[Path, Path]:
    pdb_dir = designs / "MPNN" / "Relaxed"
    pae_dir = designs / "CellertzEvidence" / "PAE"
    pdb_dir.mkdir(parents=True, exist_ok=True)
    pae_dir.mkdir(parents=True, exist_ok=True)
    pdb_path = pdb_dir / f"{design}_model{model}.pdb"
    pae_path = pae_dir / f"{design}_model{model}_pae.npz"
    pdb_path.write_text(_pdb(*chains), encoding="utf-8")
    size = len(chains)
    matrix = pae if pae is not None else np.zeros((size, size), dtype=dtype)
    np.savez_compressed(
        pae_path,
        pae_angstrom=np.asarray(matrix, dtype=dtype),
        candidate_id=np.asarray(design if candidate_id is None else candidate_id),
        model_number=np.asarray(
            model if model_number is None else model_number,
            dtype=np.int32,
        ),
    )
    return pdb_path, pae_path


def _layout(tmp_path: Path) -> Path:
    designs = tmp_path / EXECUTION / "designs"
    designs.mkdir(parents=True)
    return designs


def _hashes(paths: dict[int, tuple[Path, Path]]) -> dict[int, dict[str, str]]:
    return {
        model: {"pdb": _sha256(pdb), "pae": _sha256(pae)}
        for model, (pdb, pae) in paths.items()
    }


def _analyze(designs: Path, report: Path, **kwargs):
    return analyze_recovered_artifacts(
        designs,
        candidate=kwargs.pop("candidate", DESIGN),
        execution_id=kwargs.pop("execution_id", EXECUTION),
        report_path=report,
        **kwargs,
    )


def test_filename_and_shape_do_not_score_a_mismatched_candidate(tmp_path, monkeypatch):
    designs = _layout(tmp_path)
    pdb, pae = _write_pair(designs, 1, candidate_id="other_design")
    report = tmp_path / "report.json"

    def fail_calculate(self, **kwargs):
        raise AssertionError("scoring was invoked")

    monkeypatch.setattr(ProteinIpSAECalculator, "calculate", fail_calculate)
    document = _analyze(designs, report, expected_hashes=_hashes({1: (pdb, pae)}))

    assert document["status"] == "blocked"
    assert document["scoring_invoked"] is False
    assert "candidate_id" in document["blocker"]
    assert "Scoring was not invoked" in document["blocker"]
    assert "ranking_value" not in document
    assert document["scientific_validation_established"] is False
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "blocked"


def test_model_number_mismatch_blocks_before_scoring(tmp_path, monkeypatch):
    designs = _layout(tmp_path)
    pdb, pae = _write_pair(designs, 1, model_number=2)
    monkeypatch.setattr(
        ProteinIpSAECalculator,
        "calculate",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("scoring was invoked")),
    )

    document = _analyze(
        designs,
        tmp_path / "report.json",
        expected_hashes=_hashes({1: (pdb, pae)}),
    )

    assert document["scoring_invoked"] is False
    assert "model_number" in document["blocker"]


def test_interleaved_chains_block_without_a_score(tmp_path, monkeypatch):
    designs = _layout(tmp_path)
    pdb, pae = _write_pair(designs, 1, chains=("A", "B", "A"))
    monkeypatch.setattr(
        ProteinIpSAECalculator,
        "calculate",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("scoring was invoked")),
    )

    document = _analyze(
        designs,
        tmp_path / "report.json",
        expected_hashes=_hashes({1: (pdb, pae)}),
    )

    assert document["scoring_invoked"] is False
    assert "residue order" in document["blocker"]
    assert "ranking_value" not in document


def test_binder_length_mismatch_blocks_without_a_score(tmp_path, monkeypatch):
    designs = _layout(tmp_path)
    pdb, pae = _write_pair(designs, 1, chains=("A", "B", "B"))
    monkeypatch.setattr(
        ProteinIpSAECalculator,
        "calculate",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("scoring was invoked")),
    )

    document = _analyze(
        designs,
        tmp_path / "report.json",
        expected_hashes=_hashes({1: (pdb, pae)}),
    )

    assert document["scoring_invoked"] is False
    assert "binder length" in document["blocker"]


def test_non_float32_pae_blocks_without_a_score(tmp_path, monkeypatch):
    designs = _layout(tmp_path)
    pdb, pae = _write_pair(designs, 1, dtype=np.float64)
    monkeypatch.setattr(
        ProteinIpSAECalculator,
        "calculate",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("scoring was invoked")),
    )

    document = _analyze(
        designs,
        tmp_path / "report.json",
        expected_hashes=_hashes({1: (pdb, pae)}),
    )

    assert document["scoring_invoked"] is False
    assert "float32" in document["blocker"]


def test_unpickled_object_array_blocks_without_a_score(tmp_path, monkeypatch):
    designs = _layout(tmp_path)
    _pdb_path, pae_path = _write_pair(designs, 1)
    np.savez(
        pae_path,
        pae_angstrom=np.zeros((2, 2), dtype=np.float32),
        candidate_id=np.asarray(DESIGN, dtype=object),
        model_number=np.asarray(1, dtype=np.int32),
    )
    monkeypatch.setattr(
        ProteinIpSAECalculator,
        "calculate",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("scoring was invoked")),
    )

    document = _analyze(designs, tmp_path / "report.json")

    assert document["scoring_invoked"] is False
    assert "allow_pickle=False" in document["blocker"]


def test_eligible_models_use_the_mean_not_the_higher_model(tmp_path):
    designs = _layout(tmp_path)
    low = np.array([[0.0, 9.0], [10.0, 0.0]], dtype=np.float32)
    high = np.zeros((2, 2), dtype=np.float32)
    first = _write_pair(designs, 1, pae=low)
    second = _write_pair(designs, 2, pae=high)
    review = designs / "review.json"
    review.write_text('{"unmet": true}\n', encoding="utf-8")
    before = {
        path: path.read_bytes()
        for path in (*first, *second, review)
    }
    report = tmp_path / "outside" / "report.json"

    document = _analyze(
        designs,
        report,
        expected_hashes=_hashes({1: first, 2: second}),
    )

    scores = [item["ipsae"] for item in document["models"]]
    assert document["status"] == "scored"
    assert document["scoring_invoked"] is True
    assert document["implementation"] == "BindCraftIpSAEEvidenceProvider"
    assert document["model_aggregation"] == "mean"
    assert document["ranking_value"] == pytest.approx(sum(scores) / len(scores))
    assert document["ranking_value"] != pytest.approx(max(scores))
    assert "highest score is not selected" in document["selection_note"]
    assert document["protocol"]["protocol_id"] == "CARPNN_2026_REFERENCE"
    assert document["protocol"]["within_model_aggregation"] == "max"
    assert document["protocol"]["distance_cutoff_used_by_primary_ipsae"] is False
    assert document["host_identity_confirmed"] is False
    assert document["scientific_validation_established"] is False
    assert document["attached_to_round"] is None
    assert document["raw_artifacts_modified"] is False
    assert all(path.read_bytes() == before[path] for path in before)
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["ranking_value"] == document["ranking_value"]
    assert "i_pTM" in " ".join(saved["limitations"])


def test_recovered_hash_mismatch_blocks_before_scoring(tmp_path, monkeypatch):
    designs = tmp_path / RECOVERED_ATTEMPT_ID / "designs"
    designs.mkdir(parents=True)
    _write_pair(designs, 1, design=RECOVERED_DESIGN, candidate_id=RECOVERED_DESIGN)
    _write_pair(designs, 2, design=RECOVERED_DESIGN, candidate_id=RECOVERED_DESIGN)
    monkeypatch.setattr(
        ProteinIpSAECalculator,
        "calculate",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("scoring was invoked")),
    )

    document = _analyze(
        designs,
        tmp_path / "report.json",
        candidate=RECOVERED_DESIGN,
        execution_id=RECOVERED_ATTEMPT_ID,
        expected_hashes={
            1: {"pdb": RECOVERED_PDB_SHA256[1], "pae": RECOVERED_PAE_SHA256[1]},
            2: {"pdb": RECOVERED_PDB_SHA256[2], "pae": RECOVERED_PAE_SHA256[2]},
        },
    )

    assert document["scoring_invoked"] is False
    assert document["host_identity_confirmed"] is False
    assert "sha256" in document["blocker"]


def test_report_inside_the_attempt_is_not_written(tmp_path):
    designs = _layout(tmp_path)
    pdb, pae = _write_pair(designs, 1)
    review = designs / "review.json"
    review.write_text("{}\n", encoding="utf-8")
    before = review.read_bytes()
    inside = designs / "CellertzEvidence" / "acceptance.json"

    document = _analyze(
        designs,
        inside,
        expected_hashes=_hashes({1: (pdb, pae)}),
    )

    assert document["status"] == "blocked"
    assert document["scoring_invoked"] is False
    assert not inside.exists()
    assert review.read_bytes() == before


def test_existing_report_is_not_overwritten(tmp_path):
    designs = _layout(tmp_path)
    pdb, pae = _write_pair(designs, 1)
    report = tmp_path / "report.json"
    report.write_text('{"keep": true}\n', encoding="utf-8")

    document = _analyze(
        designs,
        report,
        expected_hashes=_hashes({1: (pdb, pae)}),
    )

    assert document["status"] == "blocked"
    assert report.read_text(encoding="utf-8") == '{"keep": true}\n'


def test_cli_refuses_a_different_attempt_and_does_not_score(tmp_path, monkeypatch):
    designs = _layout(tmp_path)
    _write_pair(designs, 1)
    report = tmp_path / "report.json"
    monkeypatch.setattr(
        ProteinIpSAECalculator,
        "calculate",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("scoring was invoked")),
    )

    status = main(["--designs-dir", str(designs), "--report", str(report)])

    assert status == 2
    document = json.loads(report.read_text(encoding="utf-8"))
    assert document["scoring_invoked"] is False
    assert document["attempt_id"] == RECOVERED_ATTEMPT_ID
    assert RECOVERED_DESIGN == "PDL1_l65_s400711_mpnn1"
    assert "attempt.c5c1f13e" in document["blocker"]
