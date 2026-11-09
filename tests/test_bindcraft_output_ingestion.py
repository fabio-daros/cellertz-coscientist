"""Final BindCraft designs are ingested from the CSV identity, not from a PDB count."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

from cellertz.application.apptainer_demonstration import (
    ingest_bindcraft_output,
    recover_completed_attempt,
)
from cellertz.application.attempt_coordinator import AttemptCoordinator
from cellertz.application.attempt_evidence import (
    AttemptFiles,
    compare_metrics,
    list_attempts,
    load_catalog,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "bindcraft_attempt_c5c1f13e"
_DESIGN = "PDL1_l65_s400711_mpnn1"
_ATTEMPT = "attempt.c5c1f13e"
_TRAJECTORIES = {
    "Trajectory/Relaxed/PDL1_l65_s400711.pdb": (
        "SYKEMTRQYPEQMEEVMRAFPTLHTMTRAETVAFWQRLERQTGDIRVAVTMVMQVYKYVEDPSMS"
    ),
    "Trajectory/LowConfidence/PDL1_l65_s439581.pdb": (
        "STINWGPDRTYPINFFIQVFRHMMMHNPRQWPAEMDRAFRTQDPNTPDMRRLEKEMRKVQEEMRN"
    ),
    "Trajectory/LowConfidence/PDL1_l65_s128037.pdb": (
        "RPIRKLTPHEVERTREKIRSNVRELMQISRGRTWRMSPAQVYSTVHWTMRENIYLYEDIEASLAG"
    ),
}
_AA3 = {
    "A": "ALA",
    "R": "ARG",
    "N": "ASN",
    "D": "ASP",
    "C": "CYS",
    "Q": "GLN",
    "E": "GLU",
    "G": "GLY",
    "H": "HIS",
    "I": "ILE",
    "L": "LEU",
    "K": "LYS",
    "M": "MET",
    "F": "PHE",
    "P": "PRO",
    "S": "SER",
    "T": "THR",
    "W": "TRP",
    "Y": "TYR",
    "V": "VAL",
}


def test_real_layout_keeps_one_candidate_and_both_model_cells(tmp_path: Path) -> None:
    attempt = _build_attempt(tmp_path)
    progress = (attempt / "progress.json").read_bytes()
    raw = _hashes(attempt / "designs")
    outcome = recover_completed_attempt(attempt)
    assert outcome["status"] == "verified"
    assert outcome["apptainer_invoked"] is False
    assert outcome["raw_artifacts_preserved"] is True
    assert (attempt / "progress.json").read_bytes() == progress
    assert json.loads(progress)["status"] == "rejected"
    assert _hashes(attempt / "designs") == raw
    review = json.loads((attempt / "review.json").read_text(encoding="utf-8"))
    assert review["design"] == _DESIGN
    assert review["execution"] == "recovered_without_apptainer"
    assert review["filter_policy"] == "no_filters"
    assert review["platform_ipsae_ranking"] is False
    assert review["ranking_score_computed"] is False
    assert set(review["models"]) == {"model1", "model2"}
    sequence = _csv_sequence()
    assert review["models"]["model1"]["sequence"] == sequence
    assert review["models"]["model2"]["sequence"] == sequence
    assert review["models"]["model1"]["binder_sequence_matches_csv"] is True
    assert review["models"]["model1"]["representative"] is True
    assert review["models"]["model2"]["representative"] is False
    assert review["representative"]["model"] == "model1"
    assert review["representative"]["accepted_pdb"] == f"Accepted/{_DESIGN}_model1.pdb"
    assert review["representative"]["ranked_pdb"] == f"Accepted/Ranked/1_{_DESIGN}_model1.pdb"
    assert review["models"]["model1"]["sha256"] == _sha(attempt / "designs" / f"Accepted/{_DESIGN}_model1.pdb")
    ranked = attempt / "designs" / "Accepted" / "Ranked" / f"1_{_DESIGN}_model1.pdb"
    assert review["models"]["model1"]["sha256"] == _sha(ranked)
    assert review["models"]["model2"]["sha256"] != review["models"]["model1"]["sha256"]
    metrics = {row["metric"]: row for row in review["metrics"]}
    assert metrics["pLDDT"]["model1"] == {"kind": "recorded", "text": "0.91"}
    assert metrics["pLDDT"]["model2"] == {"kind": "recorded", "text": "0.9"}
    assert metrics["n_InterfaceResidues"]["model2"] == {"kind": "recorded_zero", "text": "0"}
    assert metrics["Relaxed_Clashes"]["model1"] == {"kind": "recorded_zero", "text": "0"}
    assert metrics["InterfaceHbondsPercentage"]["model2"] == {"kind": "empty_field"}
    assert "InterfaceHbondsPercentage" in review["blank_model2_metrics"]
    assert "n_InterfaceResidues" not in review["blank_model2_metrics"]
    assert review["interface_residues"] == {"kind": "empty_field"}
    assert "model3" not in review["models"]
    assert "ipSAE" in review["provenance"]["omitted"]
    assert all("ipsae" not in row["metric"].lower() for row in review["metrics"])
    constraints = review["provenance"]["execution_constraints"]
    assert constraints["unmet"] is True
    assert constraints["requested_max_trajectories"] == 1
    assert constraints["nonzero_failure_counts"]["Trajectory_one-hot_pLDDT"] == "2"
    assert constraints["nonzero_failure_counts"]["Trajectory_final_pLDDT"] == "2"
    assert len(constraints["low_confidence_trajectory_files"]) == 2
    assert set(review["provenance"]["excluded_trajectories"]) == set(_TRAJECTORIES)
    catalog = load_catalog((AttemptFiles(attempt / "review.json", attempt),))
    assert catalog[_ATTEMPT]["pdb_verified"] is True
    assert outcome["execution_constraints"]["unmet"] is True


def test_renamed_trajectory_pdb_fails_sequence_verification(tmp_path: Path) -> None:
    attempt = _build_attempt(tmp_path)
    canonical = attempt / "designs" / "MPNN" / "Relaxed" / f"{_DESIGN}_model1.pdb"
    trajectory = next(iter(_TRAJECTORIES.values()))
    canonical.write_text(_pdb(trajectory, 0.0), encoding="utf-8")
    progress = (attempt / "progress.json").read_bytes()
    outcome = recover_completed_attempt(attempt)
    assert outcome["status"] == "outputs_rejected"
    assert "binder_sequence_mismatch:model1" in outcome["reasons"]
    assert not (attempt / "review.json").exists()
    assert (attempt / "progress.json").read_bytes() == progress


def test_an_unscoped_pdb_is_rejected_with_the_design_name(tmp_path: Path) -> None:
    designs = tmp_path / "designs"
    designs.mkdir()
    shutil.copy(_FIXTURE / "final_design_stats.csv", designs / "final_design_stats.csv")
    (designs / "model1.pdb").write_text(_pdb(_csv_sequence(), 0.0), encoding="utf-8")
    for relative, sequence in _TRAJECTORIES.items():
        path = designs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_pdb(sequence, 9.0), encoding="utf-8")
    outcome = ingest_bindcraft_output(designs, attempt_id=_ATTEMPT, execution="check")
    assert outcome["status"] == "outputs_rejected"
    assert f"canonical_model_pdb_missing:{_DESIGN}" in outcome["reasons"]
    assert any(reason.startswith("unclassified_pdb:") for reason in outcome["reasons"])
    assert "review" not in outcome


def test_a_missing_model_column_stays_absent(tmp_path: Path) -> None:
    designs = tmp_path / "designs"
    _write_models(designs, "AAAAAA", include_model2=True)
    (designs / "final_design_stats.csv").write_text(
        "Design,Sequence,1_pLDDT,2_n_InterfaceResidues,Average_pLDDT\n"
        f"{_DESIGN},AAAAAA,0.91,0,0.91\n",
        encoding="utf-8",
    )
    outcome = ingest_bindcraft_output(designs, attempt_id=_ATTEMPT, execution="check")
    assert outcome["status"] == "verified", outcome["reasons"]
    metrics = {row["metric"]: row for row in outcome["review"]["metrics"]}
    assert metrics["pLDDT"]["model2"] == {"kind": "absent"}
    assert metrics["n_InterfaceResidues"]["model2"] == {"kind": "recorded_zero", "text": "0"}
    assert metrics["pLDDT"]["model1"] == {"kind": "recorded", "text": "0.91"}


def test_nested_recovery_is_readable_through_the_catalog(tmp_path: Path, monkeypatch) -> None:
    catalog = tmp_path / "cellertz-pdl1-demonstration"
    other = catalog / "attempt.keep1234"
    other.mkdir(parents=True)
    (other / "review.json").write_text(
        json.dumps(
            {
                "attempt_id": "attempt.keep1234",
                "design": "other-attempt",
                "filter_policy": "no_filters",
                "scientific_evaluation": "pending",
                "models": {},
                "metrics": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    inner = _build_attempt(catalog / _ATTEMPT)
    outer = catalog / _ATTEMPT
    progress = (inner / "progress.json").read_bytes()
    (inner / "progress.json").unlink()
    (outer / "progress.json").write_bytes(progress)
    already = recover_completed_attempt(inner)
    assert already["status"] == "verified"
    assert (inner / "review.json").is_file()
    assert not (outer / "review.json").exists()
    monkeypatch.setenv("CELLERTZ_ATTEMPT_CATALOG", str(catalog))
    hidden = load_catalog()
    assert _ATTEMPT not in hidden
    assert "attempt.keep1234" in hidden
    assert "attempt.2LUTUOX4" in hidden
    review_bytes = (inner / "review.json").read_bytes()
    recovery_bytes = (inner / "recovery.json").read_bytes()
    raw = _hashes(inner / "designs")
    runtime = _hashes(inner / ".cellertz_runtime")
    published = recover_completed_attempt(outer)
    assert published["status"] == "verified"
    assert published["apptainer_invoked"] is False
    assert published["raw_artifacts_preserved"] is True
    assert published["execution_constraints"]["unmet"] is True
    assert (outer / "review.json").read_bytes() == review_bytes
    assert (inner / "review.json").read_bytes() == review_bytes
    assert (inner / "recovery.json").read_bytes() == recovery_bytes
    assert (outer / "progress.json").read_bytes() == progress
    assert _hashes(inner / "designs") == raw
    assert _hashes(inner / ".cellertz_runtime") == runtime
    visible = load_catalog()
    assert _ATTEMPT in visible
    assert "attempt.keep1234" in visible
    assert "attempt.2LUTUOX4" in visible
    item = visible[_ATTEMPT]
    assert item["pdb_verified"] is True
    assert set(item["models"]) == {"model1", "model2"}
    assert item["models"]["model1"]["pdb_path"] == outer / "model1.pdb"
    assert item["models"]["model2"]["pdb_path"] == outer / "model2.pdb"
    compared = compare_metrics(
        item,
        ["pLDDT", "n_InterfaceResidues", "InterfaceHbondsPercentage"],
    )
    assert compared["status"] == "ok"
    assert compared["pdb_verified"] is True
    cells = {row["metric"]: row for row in compared["metrics"]}
    assert cells["pLDDT"]["model1"]["text"] == "0.91"
    assert cells["pLDDT"]["model2"]["text"] == "0.9"
    assert cells["n_InterfaceResidues"]["model2"]["kind"] == "recorded_zero"
    assert cells["InterfaceHbondsPercentage"]["model2"]["kind"] == "empty_field"
    assert item["record"]["provenance"]["execution_constraints"]["unmet"] is True
    listed = {entry["attempt_id"] for entry in list_attempts(visible)["attempts"]}
    assert {_ATTEMPT, "attempt.keep1234", "attempt.2LUTUOX4"} <= listed
    again = recover_completed_attempt(outer)
    assert again["status"] == "verified"
    assert again["rewritten"] is False
    assert (inner / "recovery.json").read_bytes() == recovery_bytes
    assert (outer / "progress.json").read_bytes() == progress
    assert (outer / "review.json").read_bytes() == review_bytes
    assert _hashes(inner / "designs") == raw


def test_recovered_attempt_synthesis_cites_recorded_constraints(tmp_path: Path, monkeypatch) -> None:
    catalog = tmp_path / "cellertz-pdl1-demonstration"
    inner = _build_attempt(catalog / _ATTEMPT)
    outer = catalog / _ATTEMPT
    progress = (inner / "progress.json").read_bytes()
    (inner / "progress.json").unlink()
    (outer / "progress.json").write_bytes(progress)
    recover_completed_attempt(inner)
    published = recover_completed_attempt(outer)
    assert published["execution_constraints"]["unmet"] is True
    monkeypatch.setenv("CELLERTZ_ATTEMPT_CATALOG", str(catalog))
    loaded = load_catalog()
    seen: dict[str, dict] = {}
    calls = {"n": 0}

    def complete(messages: list[dict], _tools: list[dict], tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            assert tool_choice == "required"
            return _tool("list_verified_attempts", {})
        if calls["n"] == 2:
            assert tool_choice == "required"
            return _tool("get_attempt_evidence", {"attempt_id": _ATTEMPT})
        if calls["n"] == 3:
            seen["evidence"] = json.loads(messages[-1]["content"])
            return _tool(
                "compare_model_metrics",
                {
                    "attempt_id": _ATTEMPT,
                    "metrics": [
                        "pLDDT",
                        "n_InterfaceResidues",
                        "InterfaceHbondsPercentage",
                        "Relaxed_Clashes",
                    ],
                },
            )
        if calls["n"] == 4:
            seen["metrics"] = json.loads(messages[-1]["content"])
            return _tool("get_geometric_contacts", {"attempt_id": _ATTEMPT})
        if calls["n"] == 5:
            seen["geometry"] = json.loads(messages[-1]["content"])
            return _tool("link_structural_review", {"attempt_id": _ATTEMPT})
        seen["review"] = json.loads(messages[-1]["content"])
        return {
            "content": (
                "No unmet trajectory execution constraints are reported. "
                "The evidence supports the comparison."
            ),
            "tool_calls": [],
        }

    reply = AttemptCoordinator(loaded, complete).ask(
        "Compare both models of attempt.c5c1f13e and report trajectory execution constraints."
    )
    evidence = seen["evidence"]
    assert evidence["execution_constraints"]["unmet"] is True
    assert evidence["execution_constraints"]["requested_max_trajectories"] == 1
    assert evidence["provenance"]["execution_constraints"]["unmet"] is True
    assert evidence["execution_constraints_reported"] is True
    assert evidence["execution_compliance_established"] is False
    assert evidence["scientific_validation_established"] is False
    assert evidence["artifact_verification"] == {"pdb_verified": True}
    cells = {row["metric"]: row for row in seen["metrics"]["metrics"]}
    assert cells["n_InterfaceResidues"]["model2"] == {"kind": "recorded_zero", "text": "0"}
    assert cells["InterfaceHbondsPercentage"]["model2"] == {"kind": "empty_field"}
    assert cells["Relaxed_Clashes"]["model1"] == {"kind": "recorded_zero", "text": "0"}
    assert cells["pLDDT"]["model1"]["text"] == "0.91"
    assert cells["pLDDT"]["model2"]["text"] == "0.9"
    assert seen["geometry"]["status"] == "calculated"
    assert seen["geometry"]["models"]["model1"]["calculated"]["residue_pairs"] == 1
    assert seen["geometry"]["models"]["model2"]["calculated"]["residue_pairs"] == 1
    assert seen["review"]["review_url"] == "/?area=manual"
    text = reply["text"]
    assert "no unmet" not in text.casefold()
    assert "unmet is True" in text
    assert "requested_max_trajectories is 1" in text
    assert "Trajectory/Relaxed/PDL1_l65_s400711.pdb" in text
    assert "Trajectory/LowConfidence/PDL1_l65_s439581.pdb" in text
    assert "Trajectory/LowConfidence/PDL1_l65_s128037.pdb" in text
    assert evidence["models"]["model1"]["sha256"] in text
    assert evidence["models"]["model2"]["sha256"] in text
    assert "model1.pdb" in text
    assert "model2.pdb" in text
    assert evidence["provenance"]["stats_csv_sha256"] in text
    assert "n_InterfaceResidues model2 recorded_zero 0" in text
    assert "InterfaceHbondsPercentage model2 empty_field" in text
    assert "Relaxed_Clashes model1 recorded_zero 0" in text
    assert "pLDDT model1 recorded 0.91" in text
    assert "pLDDT model2 recorded 0.9" in text
    assert "model1 geometric residue_pairs 1" in text
    assert "model2 geometric residue_pairs 1" in text
    assert "execution_compliance_established is False" in text
    assert "scientific_validation_established is False" in text
    assert "Structural review reference: /?area=manual." in text
    assert "separate from execution compliance and scientific validation" in text
    cited = next(
        reference
        for item in reply["evidence"]
        if item["tool"] == "get_attempt_evidence"
        for reference in item["references"]
        if reference["kind"] == "attempt_record"
    )
    assert cited["kind"] == "attempt_record"
    assert cited["execution_constraints"]["unmet"] is True
    assert cited["execution_constraints_reported"] is True


def _tool(name: str, arguments: dict) -> dict:
    return {
        "content": "",
        "tool_calls": [
            {
                "id": f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def test_nested_copy_recovers_without_rewriting_the_outer_rejection(tmp_path: Path) -> None:
    wrapper = tmp_path / "copied-results"
    inner = _build_attempt(wrapper)
    progress = (inner / "progress.json").read_bytes()
    (inner / "progress.json").unlink()
    (wrapper / "progress.json").write_bytes(progress)
    outcome = recover_completed_attempt(wrapper)
    assert outcome["status"] == "verified"
    assert outcome["attempt_id"] == _ATTEMPT
    assert (inner / "review.json").is_file()
    assert (wrapper / "progress.json").read_bytes() == progress
    assert json.loads((wrapper / "progress.json").read_text(encoding="utf-8"))["status"] == "rejected"


def _build_attempt(root: Path) -> Path:
    attempt = root / _ATTEMPT
    designs = attempt / "designs"
    sequence = _csv_sequence()
    _write_models(designs, sequence, include_model2=True)
    for relative, binder in _TRAJECTORIES.items():
        path = designs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_pdb(binder, 12.0), encoding="utf-8")
    shutil.copy(_FIXTURE / "final_design_stats.csv", designs / "final_design_stats.csv")
    shutil.copy(_FIXTURE / "failure_csv.csv", designs / "failure_csv.csv")
    runtime = attempt / ".cellertz_runtime"
    runtime.mkdir()
    shutil.copy(_FIXTURE / "advanced.json", runtime / "advanced.json")
    (attempt / "progress.json").write_text(
        json.dumps(
            {
                "at": "2026-10-24T13:15:57.890693+00:00",
                "profile_id": "pdl1-bindcraft-apptainer-demonstration-v1",
                "status": "rejected",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return attempt


def _write_models(designs: Path, sequence: str, *, include_model2: bool) -> None:
    model1 = _pdb(sequence, 0.0)
    copies = {
        f"MPNN/Relaxed/{_DESIGN}_model1.pdb": model1,
        f"Accepted/{_DESIGN}_model1.pdb": model1,
        f"Accepted/Ranked/1_{_DESIGN}_model1.pdb": model1,
    }
    if include_model2:
        copies[f"MPNN/Relaxed/{_DESIGN}_model2.pdb"] = _pdb(sequence, 7.0)
    for relative, text in copies.items():
        path = designs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _csv_sequence() -> str:
    with (_FIXTURE / "final_design_stats.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["Design"] == _DESIGN
    return row["Sequence"]


def _pdb(sequence: str, shift: float) -> str:
    lines = [_atom(1, "A", 1, shift, "GLY")]
    for index, residue in enumerate(sequence, start=1):
        lines.append(_atom(index + 1, "B", index, shift + (3.0 if index == 1 else 30.0), _AA3[residue]))
    lines.append("END")
    return "\n".join(lines) + "\n"


def _atom(serial: int, chain: str, resseq: int, x: float, resname: str) -> str:
    return (
        f"ATOM  {serial:5d}  CA  {resname:<3} {chain}{resseq:4d}    "
        f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
    )


def _hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
