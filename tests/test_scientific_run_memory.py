from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cellertz.domains.protein_design.execution.bindcraft_scientific import (
    HANDOFF_FILENAME as EXECUTION_HANDOFF_FILENAME,
)
from cellertz.second_brain.episodic.models import (
    EPISTEMIC_STATUS_OBSERVED,
    MEMORY_HOUSE_EPISODIC,
    ScientificRunMemoryConflict,
    ScientificRunMemoryError,
    ScientificRunMemoryProjectionError,
    attach_memory_digest,
    scientific_run_memory_from_document,
)
from cellertz.second_brain.episodic.protein_design import (
    HANDOFF_FILENAME,
    project_protein_design_run,
)
from cellertz.second_brain.episodic.store import (
    FileScientificRunMemoryStore,
    default_scientific_memory_root,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEQUENCE_A = "ACDEFGHIKLMNPQRSTVWYAAAAACDEFGHIKLMNPQRSTVWY"
SEQUENCE_B = "YWVTSRQPNMLKIHGFEDCAYYYYYWVTSRQPNMLKIHGFEDCA"
RANKING_SCORE_A = 0.125
RANKING_SCORE_B = 0.03125
MODEL1_IPSAE = 0.25
MODEL2_IPSAE = 0.0
PDB_SHA_A1 = "a" * 64
PAE_SHA_A1 = "b" * 64
PDB_SHA_A2 = "c" * 64
PAE_SHA_A2 = "d" * 64
RECORDED_AT_A = "2026-01-01T00:00:00+00:00"
RECORDED_AT_B = "2026-09-19T12:00:00+00:00"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _complete_stage() -> dict[str, object]:
    return {"status": "complete", "artifact": "x.json"}


def _run_document(run_id: str, target: str = "PDL1") -> dict[str, object]:
    return {
        "run_id": run_id,
        "round": 1,
        "target": target,
        "stage": "complete",
        "generation": _complete_stage(),
        "scoring": _complete_stage(),
        "ranking": _complete_stage(),
        "critique": _complete_stage(),
        "prioritization": _complete_stage(),
    }


def _candidate(
    candidate_id: str,
    sequence: str,
    *,
    target_id: str = "PDL1",
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "sequence": sequence,
        "target_id": target_id,
        "generator": "bindcraft",
        "generator_metadata": {},
    }


def _generation(
    candidates: list[dict[str, object]],
    *,
    target_id: str = "PDL1",
) -> dict[str, object]:
    return {
        "status": "complete",
        "adapter_name": "bindcraft",
        "adapter_version": "test",
        "input_hash": "e" * 64,
        "error": None,
        "request": {
            "target_id": target_id,
            "objective": "binder_design",
            "generator": "bindcraft",
            "n_candidates": len(candidates),
            "constraints": {"length_min": 65, "length_max": 65},
        },
        "candidates": candidates,
    }


def _ipsae_evidence(
    candidate_id: str,
    *,
    mean: float,
    models: list[dict[str, object]],
    execution_id: str = "exec_test",
) -> dict[str, object]:
    values = [float(model["ipsae"]) for model in models]
    return {
        "candidate_id": candidate_id,
        "design": candidate_id,
        "execution_id": execution_id,
        "protocol_id": "CARPNN_2026_REFERENCE",
        "model_aggregation": "mean",
        "n_models": len(models),
        "ipsae_mean": mean,
        "ipsae_min": min(values),
        "ipsae_max": max(values),
        "ranking_value": mean,
        "is_probability": False,
        "recommended_as_hard_filter": False,
        "protocol_parameters": {
            "pae_cutoff": 10.0,
            "distance_cutoff": 10.0,
            "chain_a": "A",
            "chain_b": "B",
            "within_model_aggregation": "max",
        },
        "models": models,
    }


def _score(
    candidate_id: str,
    ranking_score: float,
    evidence: dict[str, object],
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "ranking_score": ranking_score,
        "ranking_method": "ipsae",
        "is_probability": False,
        "recommended_as_hard_filter": False,
        "structural_evidence": {"ipsae": ranking_score},
        "additional_evidence": {},
        "provenance": {"ipsae_evidence": evidence},
    }


def _rank(
    candidate_id: str,
    rank: int,
    ranking_score: float,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "rank": rank,
        "ranking_signal": "ipsae",
        "ranking_score": ranking_score,
        "is_probability": False,
        "recommended_as_hard_filter": False,
        "benchmark_version": "protein_scoring_v2",
        "structural_evidence": {"ipsae": ranking_score},
    }


def _critique(
    candidate_id: str,
    rank: int,
    ranking_score: float,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "rank": rank,
        "ranking_signal": "ipsae",
        "ranking_score": ranking_score,
        "is_probability": False,
        "recommended_as_hard_filter": False,
        "structural_evidence": {"ipsae": ranking_score},
        "critic_assessment": {
            "agreement": "high",
            "ensemble_consistency": "consistent",
            "structural_conflict": False,
            "missing_evidence": False,
            "confidence_note": "ok",
        },
    }


def _portfolio(
    candidate_id: str,
    rank: int,
    ranking_score: float,
    *,
    selected: bool,
    bucket: str | None,
    order: int | None,
    reason: str,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "rank": rank,
        "ranking_score": ranking_score,
        "ranking_signal": "ipsae",
        "selected": selected,
        "selection_bucket": bucket,
        "selection_order": order,
        "selection_reason": reason,
        "critic_agreement": "high",
        "structural_conflict": False,
        "review_flag": False,
        "is_probability": False,
    }


def _handoff(run_id: str, execution_id: str) -> dict[str, object]:
    return {
        "execution_id": execution_id,
        "run_id": run_id,
        "manifest_sha256": "1" * 64,
        "request_hash": "2" * 64,
        "scientific_request_sha256": "3" * 64,
        "stats_csv": "final_design_stats.csv",
        "bindcraft_version": "test",
        "returncode": 0,
        "command": ["apptainer", "exec"],
        "execution_provenance": {
            "execution_id": execution_id,
            "run_id": run_id,
            "manifest_sha256": "1" * 64,
            "container_digest": "4" * 64,
            "tool_version": "bindcraft-test",
            "seed": 7,
            "advanced_profile_sha256": "5" * 64,
            "container_image": "/tmp/not-an-identity.sif",
        },
        "scientific_request": {
            "target_id": "PDL1",
            "objective": "binder_design",
            "generator": "bindcraft",
            "n_candidates": 2,
            "constraints": {"length_min": 65, "length_max": 65},
        },
    }


def write_complete_run(
    tmp_path: Path,
    *,
    run_id: str = "protein_run_test",
    execution_id: str = "exec_test",
) -> tuple[Path, Path]:
    artifact_dir = tmp_path / "artifacts" / run_id
    output_dir = tmp_path / "execution" / execution_id
    candidates = [
        _candidate("cand_a", SEQUENCE_A),
        _candidate("cand_b", SEQUENCE_B),
    ]
    models_a = [
        {
            "model_number": 1,
            "ipsae": MODEL1_IPSAE,
            "ipsae_ab": 0.2,
            "ipsae_ba": 0.3,
            "pdb_sha256": PDB_SHA_A1,
            "pae_sha256": PAE_SHA_A1,
        },
        {
            "model_number": 2,
            "ipsae": MODEL2_IPSAE,
            "ipsae_ab": 0.0,
            "ipsae_ba": 0.0,
            "pdb_sha256": PDB_SHA_A2,
            "pae_sha256": PAE_SHA_A2,
        },
    ]
    models_b = [
        {
            "model_number": 1,
            "ipsae": RANKING_SCORE_B,
            "ipsae_ab": RANKING_SCORE_B,
            "ipsae_ba": RANKING_SCORE_B,
            "pdb_sha256": "e" * 64,
            "pae_sha256": "f" * 64,
        }
    ]
    _write_json(artifact_dir / "run.json", _run_document(run_id))
    _write_json(artifact_dir / "generation.json", _generation(candidates))
    _write_json(
        artifact_dir / "scoring.json",
        [
            _score(
                "cand_a",
                RANKING_SCORE_A,
                _ipsae_evidence(
                    "cand_a",
                    mean=RANKING_SCORE_A,
                    models=models_a,
                    execution_id=execution_id,
                ),
            ),
            _score(
                "cand_b",
                RANKING_SCORE_B,
                _ipsae_evidence(
                    "cand_b",
                    mean=RANKING_SCORE_B,
                    models=models_b,
                    execution_id=execution_id,
                ),
            ),
        ],
    )
    _write_json(
        artifact_dir / "ranking.json",
        [
            _rank("cand_a", 1, RANKING_SCORE_A),
            _rank("cand_b", 2, RANKING_SCORE_B),
        ],
    )
    _write_json(
        artifact_dir / "critique.json",
        [
            _critique("cand_a", 1, RANKING_SCORE_A),
            _critique("cand_b", 2, RANKING_SCORE_B),
        ],
    )
    _write_json(
        artifact_dir / "prioritization.json",
        [
            _portfolio(
                "cand_a",
                1,
                RANKING_SCORE_A,
                selected=False,
                bucket=None,
                order=None,
                reason="Not selected under the experimental portfolio policy.",
            ),
            _portfolio(
                "cand_b",
                2,
                RANKING_SCORE_B,
                selected=True,
                bucket="core",
                order=1,
                reason="High-ranked representative selected under portfolio policy.",
            ),
        ],
    )
    _write_json(output_dir / HANDOFF_FILENAME, _handoff(run_id, execution_id))
    return artifact_dir, output_dir


def project_fixture(
    tmp_path: Path,
    recorded_at: str | None = RECORDED_AT_A,
):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    return project_protein_design_run(
        run_artifact_dir=artifact_dir,
        execution_output_dir=output_dir,
        recorded_at=recorded_at,
    )


def test_handoff_filename_matches_execution_constant():
    assert HANDOFF_FILENAME == EXECUTION_HANDOFF_FILENAME


def test_project_complete_run_into_scientific_run_memory(tmp_path):
    memory = project_fixture(tmp_path)
    assert memory.domain == "protein_design"
    assert memory.run_id == "protein_run_test"
    assert memory.execution_id == "exec_test"
    assert memory.target_id == "PDL1"
    assert memory.objective == "binder_design"
    assert memory.generator == "bindcraft"
    assert memory.scientific_request_sha256 == "3" * 64
    assert memory.memory_house == MEMORY_HOUSE_EPISODIC
    assert memory.epistemic_status == EPISTEMIC_STATUS_OBSERVED
    assert len(memory.candidates) == 2
    assert memory.memory_digest
    assert memory.recorded_at == RECORDED_AT_A


def test_exact_sequence_preserved(tmp_path):
    memory = project_fixture(tmp_path)
    by_id = {item.candidate_id: item for item in memory.candidates}
    assert by_id["cand_a"].sequence == SEQUENCE_A
    assert by_id["cand_b"].sequence == SEQUENCE_B


def test_ranking_score_preserved_exactly(tmp_path):
    memory = project_fixture(tmp_path)
    by_id = {item.candidate_id: item for item in memory.candidates}
    assert by_id["cand_a"].ranking_score == RANKING_SCORE_A
    assert by_id["cand_b"].ranking_score == RANKING_SCORE_B
    assert by_id["cand_a"].rank == 1
    assert by_id["cand_b"].rank == 2


def test_per_model_ipsae_preserved_exactly(tmp_path):
    memory = project_fixture(tmp_path)
    evidence = memory.candidates[0].evidence
    assert evidence is not None
    assert evidence.ipsae_mean == RANKING_SCORE_A
    assert evidence.models[0].ipsae == MODEL1_IPSAE
    assert evidence.models[1].ipsae == MODEL2_IPSAE
    assert evidence.models[0].pdb_sha256 == PDB_SHA_A1
    assert evidence.models[0].pae_sha256 == PAE_SHA_A1


def test_is_probability_remains_false(tmp_path):
    memory = project_fixture(tmp_path)
    for candidate in memory.candidates:
        assert candidate.is_probability is False
        assert candidate.evidence is not None
        assert candidate.evidence.is_probability is False


def test_recommended_as_hard_filter_remains_false(tmp_path):
    memory = project_fixture(tmp_path)
    for candidate in memory.candidates:
        assert candidate.recommended_as_hard_filter is False
        assert candidate.evidence is not None
        assert candidate.evidence.recommended_as_hard_filter is False


def test_prioritization_selected_not_inferred_from_rank(tmp_path):
    memory = project_fixture(tmp_path)
    by_id = {item.candidate_id: item for item in memory.candidates}
    assert by_id["cand_a"].rank == 1
    assert by_id["cand_a"].decision is not None
    assert by_id["cand_a"].decision.selected is False
    assert by_id["cand_b"].rank == 2
    assert by_id["cand_b"].decision is not None
    assert by_id["cand_b"].decision.selected is True
    assert by_id["cand_b"].decision.selection_bucket == "core"
    assert by_id["cand_b"].decision.selection_order == 1


def test_execution_provenance_distinct_from_evidence(tmp_path):
    memory = project_fixture(tmp_path)
    assert memory.execution.tool_version == "bindcraft-test"
    assert memory.execution.seed == 7
    assert memory.execution.container_digest == "4" * 64
    assert "container_image" not in memory.execution.extra
    assert "ipsae_mean" not in memory.execution.to_document()
    evidence = memory.candidates[0].evidence
    assert evidence is not None
    assert evidence.protocol_id == "CARPNN_2026_REFERENCE"
    assert evidence.ipsae_mean == RANKING_SCORE_A
    document = memory.to_document()
    assert "execution" in document
    assert document["candidates"][0]["evidence"]["ipsae_mean"] == RANKING_SCORE_A
    assert "ipsae_mean" not in document["execution"]


def test_same_completed_artifacts_produce_same_digest(tmp_path):
    first = project_fixture(tmp_path, recorded_at=RECORDED_AT_A)
    second_root = tmp_path / "copy"
    artifact_dir, output_dir = write_complete_run(second_root)
    second = project_protein_design_run(
        run_artifact_dir=artifact_dir,
        execution_output_dir=output_dir,
        recorded_at=RECORDED_AT_A,
    )
    assert first.memory_digest == second.memory_digest


def test_recorded_at_does_not_change_memory_digest(tmp_path):
    first = project_fixture(tmp_path, recorded_at=RECORDED_AT_A)
    artifact_dir = tmp_path / "artifacts" / "protein_run_test"
    output_dir = tmp_path / "execution" / "exec_test"
    second = project_protein_design_run(
        run_artifact_dir=artifact_dir,
        execution_output_dir=output_dir,
        recorded_at=RECORDED_AT_B,
    )
    assert first.recorded_at != second.recorded_at
    assert first.memory_digest == second.memory_digest
    mutated = replace(first, recorded_at=RECORDED_AT_B)
    assert attach_memory_digest(mutated).memory_digest == first.memory_digest


def test_same_run_same_digest_is_idempotent(tmp_path):
    memory = project_fixture(tmp_path)
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    first = store.put(memory)
    path = store.memory_path(memory.run_id)
    assert path is not None
    original = path.read_text(encoding="utf-8")
    second = store.put(replace(memory, recorded_at=RECORDED_AT_B))
    assert second.memory_digest == first.memory_digest
    assert second.recorded_at == first.recorded_at
    assert path.read_text(encoding="utf-8") == original


def test_same_run_different_digest_raises_conflict(tmp_path):
    memory = project_fixture(tmp_path)
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    store.put(memory)
    changed = memory.candidates[0]
    changed = replace(changed, sequence="GGGG")
    conflicting = attach_memory_digest(
        replace(memory, candidates=(changed, *memory.candidates[1:]))
    )
    assert conflicting.memory_digest != memory.memory_digest
    with pytest.raises(ScientificRunMemoryConflict):
        store.put(conflicting)


def test_mismatched_run_id_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    run_path = artifact_dir / "run.json"
    document = json.loads(run_path.read_text())
    document["run_id"] = "protein_run_other"
    _write_json(run_path, document)
    with pytest.raises(ScientificRunMemoryProjectionError, match="run_id_mismatch"):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_unknown_candidate_in_ranking_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    ranking_path = artifact_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text())
    ranking.append(_rank("cand_unknown", 3, 0.01))
    _write_json(ranking_path, ranking)
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="candidate_set_mismatch:ranking",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_duplicate_candidate_ids_fail_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    generation_path = artifact_dir / "generation.json"
    generation = json.loads(generation_path.read_text())
    generation["candidates"].append(_candidate("cand_a", SEQUENCE_A))
    _write_json(generation_path, generation)
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="duplicate_candidate_id",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_malformed_handoff_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    (output_dir / HANDOFF_FILENAME).write_text("{")
    with pytest.raises(ScientificRunMemoryProjectionError, match="handoff_malformed"):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_missing_required_source_artifact_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    (artifact_dir / "scoring.json").unlink()
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="missing_artifact:scoring.json",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_find_runs_by_target(tmp_path):
    first = project_fixture(tmp_path)
    other_dir = tmp_path / "other"
    artifact_dir, output_dir = write_complete_run(
        other_dir,
        run_id="protein_run_other",
        execution_id="exec_other",
    )
    generation = json.loads((artifact_dir / "generation.json").read_text())
    generation["request"]["target_id"] = "BCMA"
    for candidate in generation["candidates"]:
        candidate["target_id"] = "BCMA"
    _write_json(artifact_dir / "generation.json", generation)
    run_document = json.loads((artifact_dir / "run.json").read_text())
    run_document["target"] = "BCMA"
    _write_json(artifact_dir / "run.json", run_document)
    handoff = json.loads((output_dir / HANDOFF_FILENAME).read_text())
    handoff["scientific_request"]["target_id"] = "BCMA"
    _write_json(output_dir / HANDOFF_FILENAME, handoff)
    second = project_protein_design_run(
        run_artifact_dir=artifact_dir,
        execution_output_dir=output_dir,
        recorded_at=RECORDED_AT_A,
    )
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    store.put(first)
    store.put(second)
    pdl1 = store.find_runs_by_target("PDL1")
    bcma = store.find_runs_by_target("BCMA")
    assert [item.run_id for item in pdl1] == ["protein_run_test"]
    assert [item.run_id for item in bcma] == ["protein_run_other"]


def test_find_candidate(tmp_path):
    memory = project_fixture(tmp_path)
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    store.put(memory)
    hits = store.find_candidate("cand_b")
    assert len(hits) == 1
    assert hits[0].run_id == "protein_run_test"
    assert hits[0].candidate.sequence == SEQUENCE_B
    assert hits[0].candidate.decision is not None
    assert hits[0].candidate.decision.selected is True
    assert store.find_candidate("missing") == ()


def test_local_uri_does_not_change_memory_digest(tmp_path):
    first = project_fixture(tmp_path)
    other = tmp_path / "relocated"
    artifact_dir, output_dir = write_complete_run(other)
    second = project_protein_design_run(
        run_artifact_dir=artifact_dir,
        execution_output_dir=output_dir,
        recorded_at=RECORDED_AT_A,
    )
    first_run = next(
        item for item in first.source_artifacts if item.artifact_type == "run"
    )
    second_run = next(
        item for item in second.source_artifacts if item.artifact_type == "run"
    )
    assert first_run.uri != second_run.uri
    assert first_run.sha256 == second_run.sha256
    assert first.memory_digest == second.memory_digest


def test_projector_does_not_depend_on_file_store():
    source = Path(
        "src/cellertz/second_brain/episodic/protein_design.py"
    ).read_text()
    assert "FileScientificRunMemoryStore" not in source
    assert "cellertz.mcp" not in source
    assert "LocalProteinRunStarter" not in source


def test_projected_memory_is_episodic(tmp_path):
    memory = project_fixture(tmp_path)
    assert memory.memory_house == "episodic"
    assert memory.to_document()["memory_house"] == "episodic"


def test_completed_run_has_epistemic_status_observed(tmp_path):
    memory = project_fixture(tmp_path)
    assert memory.epistemic_status == "observed"
    assert memory.to_document()["epistemic_status"] == "observed"


def test_execution_complete_does_not_produce_validated_status(tmp_path):
    artifact_dir, _output_dir = write_complete_run(tmp_path)
    run_document = json.loads((artifact_dir / "run.json").read_text())
    assert run_document["stage"] == "complete"
    for name in (
        "generation",
        "scoring",
        "ranking",
        "critique",
        "prioritization",
    ):
        assert run_document[name]["status"] == "complete"
    memory = project_fixture(tmp_path)
    assert memory.epistemic_status == "observed"
    for forbidden in (
        "validated",
        "approved",
        "confirmed",
        "experimentally validated",
        "experimentally_validated",
    ):
        with pytest.raises(
            ScientificRunMemoryError,
            match="epistemic_status_must_be_observed",
        ):
            replace(memory, epistemic_status=forbidden)


def test_store_read_preserves_episodic_observed_status(tmp_path):
    memory = project_fixture(tmp_path)
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    stored = store.put(memory)
    loaded = store.get_run(memory.run_id)
    assert stored.memory_house == "episodic"
    assert stored.epistemic_status == "observed"
    assert loaded is not None
    assert loaded.memory_house == "episodic"
    assert loaded.epistemic_status == "observed"


def test_queries_return_observed_episodic_memory(tmp_path):
    memory = project_fixture(tmp_path)
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    store.put(memory)
    runs = store.find_runs_by_target("PDL1")
    hits = store.find_candidate("cand_a")
    assert len(runs) == 1
    assert runs[0].memory_house == "episodic"
    assert runs[0].epistemic_status == "observed"
    assert runs[0].epistemic_status != "validated"
    assert hits[0].run_id == "protein_run_test"
    assert hits[0].candidate.sequence == memory.candidates[0].sequence


def test_projection_does_not_write_semantic_knowledge_packages(tmp_path):
    vault = Path("second_brain")
    before = sorted(
        path.relative_to(vault).as_posix() for path in vault.rglob("*")
    )
    project_fixture(tmp_path)
    after = sorted(
        path.relative_to(vault).as_posix() for path in vault.rglob("*")
    )
    assert before == after
    source = Path(
        "src/cellertz/second_brain/episodic/protein_design.py"
    ).read_text()
    assert "KnowledgePackage" not in source
    assert "from cellertz.knowledge" not in source
    assert "import cellertz.knowledge" not in source
    assert "FileKnowledge" not in source


def test_default_storage_does_not_collide_with_semantic_vault(monkeypatch):
    monkeypatch.delenv("CELLERTZ_SCIENTIFIC_MEMORY_ROOT", raising=False)
    monkeypatch.delenv("CELLERTZ_SECOND_BRAIN_ROOT", raising=False)
    root = default_scientific_memory_root()
    assert root == Path("outputs/scientific_memory/episodic")
    assert "second_brain" not in root.parts
    from cellertz.knowledge.reader import default_second_brain_root

    assert root != default_second_brain_root()
    assert default_second_brain_root() == Path("second_brain")


def test_digest_includes_episodic_contract_not_filesystem_placement(tmp_path):
    memory = project_fixture(tmp_path)
    canonical = memory.to_canonical_document()
    assert canonical["memory_house"] == "episodic"
    assert canonical["epistemic_status"] == "observed"
    assert "recorded_at" not in canonical
    uris = [item.get("uri") for item in canonical["source_artifacts"]]
    assert uris == [None] * len(uris)
    other = tmp_path / "relocated"
    artifact_dir, output_dir = write_complete_run(other)
    relocated = project_protein_design_run(
        run_artifact_dir=artifact_dir,
        execution_output_dir=output_dir,
        recorded_at=RECORDED_AT_A,
    )
    assert relocated.memory_digest == memory.memory_digest


def _drop_candidate(path: Path, candidate_id: str) -> None:
    records = json.loads(path.read_text())
    _write_json(
        path,
        [item for item in records if item["candidate_id"] != candidate_id],
    )


def test_tampered_sequence_unchanged_digest_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    store.put(memory)
    path = store.memory_path(memory.run_id)
    assert path is not None
    document = json.loads(path.read_text())
    original_digest = document["memory_digest"]
    document["candidates"][0]["sequence"] = "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW"
    _write_json(path, document)
    assert json.loads(path.read_text())["memory_digest"] == original_digest
    with pytest.raises(
        ScientificRunMemoryError,
        match="scientific_memory_digest_mismatch",
    ):
        store.get_run(memory.run_id)


def test_tampered_ranking_score_unchanged_digest_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    store.put(memory)
    path = store.memory_path(memory.run_id)
    assert path is not None
    document = json.loads(path.read_text())
    document["candidates"][0]["ranking_score"] = 0.999
    _write_json(path, document)
    with pytest.raises(
        ScientificRunMemoryError,
        match="scientific_memory_digest_mismatch",
    ):
        store.get_run(memory.run_id)


def test_invalid_memory_digest_put_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    bad = replace(memory, memory_digest="0" * 64)
    with pytest.raises(
        ScientificRunMemoryError,
        match="scientific_memory_digest_mismatch",
    ):
        store.put(bad)
    assert store.get_run(memory.run_id) is None


def test_malformed_required_string_type_fails_closed(tmp_path):
    document = project_fixture(tmp_path).to_document()
    document["run_id"] = 123
    with pytest.raises(ScientificRunMemoryError, match="run_id_malformed"):
        scientific_run_memory_from_document(document)


def test_bool_used_as_numeric_field_fails_closed(tmp_path):
    document = project_fixture(tmp_path).to_document()
    document["candidates"][0]["rank"] = True
    with pytest.raises(ScientificRunMemoryError, match="optional_int_malformed"):
        scientific_run_memory_from_document(document)


def test_top_level_execution_id_mismatch_fails_closed(tmp_path):
    document = project_fixture(tmp_path).to_document()
    document["execution_id"] = "exec_other"
    with pytest.raises(ScientificRunMemoryError, match="execution_id_mismatch"):
        scientific_run_memory_from_document(document)


def test_memory_id_inconsistent_with_run_id_fails_closed(tmp_path):
    document = project_fixture(tmp_path).to_document()
    document["memory_id"] = "protein_design:other_run"
    with pytest.raises(ScientificRunMemoryError, match="memory_id_mismatch"):
        scientific_run_memory_from_document(document)


def test_missing_candidate_from_scoring_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    _drop_candidate(artifact_dir / "scoring.json", "cand_b")
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="candidate_set_mismatch:scoring",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_missing_candidate_from_ranking_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    _drop_candidate(artifact_dir / "ranking.json", "cand_a")
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="candidate_set_mismatch:ranking",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_missing_candidate_from_critique_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    _drop_candidate(artifact_dir / "critique.json", "cand_b")
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="candidate_set_mismatch:critique",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_missing_candidate_from_prioritization_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    _drop_candidate(artifact_dir / "prioritization.json", "cand_a")
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="candidate_set_mismatch:prioritization",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_ipsae_evidence_candidate_id_mismatch_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    scoring_path = artifact_dir / "scoring.json"
    scoring = json.loads(scoring_path.read_text())
    scoring[0]["provenance"]["ipsae_evidence"]["candidate_id"] = "cand_b"
    _write_json(scoring_path, scoring)
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="ipsae_evidence_candidate_mismatch",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_ipsae_evidence_execution_id_mismatch_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    scoring_path = artifact_dir / "scoring.json"
    scoring = json.loads(scoring_path.read_text())
    scoring[0]["provenance"]["ipsae_evidence"]["execution_id"] = "exec_other"
    _write_json(scoring_path, scoring)
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="ipsae_evidence_execution_mismatch",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_scoring_vs_ranking_score_mismatch_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    ranking_path = artifact_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text())
    ranking[0]["ranking_score"] = 0.999
    _write_json(ranking_path, ranking)
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="ranking_score_mismatch",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_scoring_vs_ipsae_mean_mismatch_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    scoring_path = artifact_dir / "scoring.json"
    scoring = json.loads(scoring_path.read_text())
    scoring[0]["provenance"]["ipsae_evidence"]["ipsae_mean"] = 0.5
    scoring[0]["provenance"]["ipsae_evidence"]["ranking_value"] = 0.5
    _write_json(scoring_path, scoring)
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="ipsae_score_mismatch",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_probability_flag_mismatch_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    ranking_path = artifact_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text())
    ranking[0]["is_probability"] = True
    _write_json(ranking_path, ranking)
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="is_probability_mismatch",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_hard_filter_flag_mismatch_fails_closed(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    ranking_path = artifact_dir / "ranking.json"
    ranking = json.loads(ranking_path.read_text())
    ranking[0]["recommended_as_hard_filter"] = True
    _write_json(ranking_path, ranking)
    with pytest.raises(
        ScientificRunMemoryProjectionError,
        match="recommended_as_hard_filter_mismatch",
    ):
        project_protein_design_run(
            run_artifact_dir=artifact_dir,
            execution_output_dir=output_dir,
        )


def test_valid_fixture_still_projects_identically(tmp_path):
    first = project_fixture(tmp_path)
    artifact_dir = tmp_path / "artifacts" / "protein_run_test"
    output_dir = tmp_path / "execution" / "exec_test"
    second = project_protein_design_run(
        run_artifact_dir=artifact_dir,
        execution_output_dir=output_dir,
        recorded_at=RECORDED_AT_A,
    )
    assert first.memory_digest == second.memory_digest
    assert first.candidates[0].sequence == SEQUENCE_A
    assert first.candidates[0].ranking_score == RANKING_SCORE_A
    assert first.candidates[0].evidence is not None
    assert first.candidates[0].evidence.models[0].ipsae == MODEL1_IPSAE
    assert first.memory_house == "episodic"
    assert first.epistemic_status == "observed"


def test_same_digest_idempotency_remains_unchanged(tmp_path):
    memory = project_fixture(tmp_path)
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    first = store.put(memory)
    second = store.put(memory)
    assert second.memory_digest == first.memory_digest
    assert second.recorded_at == first.recorded_at
    assert store.get_run(memory.run_id) is not None


def test_uri_independent_digest_remains_unchanged(tmp_path):
    first = project_fixture(tmp_path)
    other = tmp_path / "relocated"
    artifact_dir, output_dir = write_complete_run(other)
    second = project_protein_design_run(
        run_artifact_dir=artifact_dir,
        execution_output_dir=output_dir,
        recorded_at=RECORDED_AT_A,
    )
    first_run = next(
        item for item in first.source_artifacts if item.artifact_type == "run"
    )
    second_run = next(
        item for item in second.source_artifacts if item.artifact_type == "run"
    )
    assert first_run.uri != second_run.uri
    assert first_run.sha256 == second_run.sha256
    assert first.memory_digest == second.memory_digest
