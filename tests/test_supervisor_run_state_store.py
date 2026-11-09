from cellertz.domains.protein_design.agents.generation import GenerationRequest
from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1
from cellertz.mcp.run_state import protein_get_run_state, set_run_state_reader
from cellertz.state.run_state import FileProteinRunStateStore


def test_supervisor_write_is_readable_without_calling_run_from_mcp(tmp_path):
    store = FileProteinRunStateStore(tmp_path / "runs")
    supervisor = ProteinSupervisorV1(
        request=GenerationRequest(
            target_id="BCMA",
            objective="binder_design",
            generator="fixture",
            n_candidates=3,
            constraints={"length_min": 50, "length_max": 100},
        ),
        artifact_dir=tmp_path / "artifacts",
        run_id="protein_run_001",
        run_state_store=store,
    )
    supervisor.run()
    set_run_state_reader(store)

    payload = protein_get_run_state("protein_run_001")

    assert payload["found"] is True
    assert payload["generation"]["status"] == "complete"
    assert payload["scoring"]["status"] == "complete"
    assert payload["ranking"]["status"] == "complete"
    assert payload["critique"]["status"] == "complete"
    assert payload["critique"]["grounded_context"] == {
        "status": "unavailable",
        "package_id": None,
    }
    assert payload["critique"]["explanation"]["status"] == "skipped"
    assert payload["prioritization"]["status"] == "complete"
    assert "missing_ipsae" in payload["scoring"]
