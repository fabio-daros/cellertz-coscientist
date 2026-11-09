import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cellertz.mcp.capabilities import cellertz_capabilities
from cellertz.mcp.server import (
    cellertz_get_capabilities,
    protein_get_run_state as mcp_protein_get_run_state,
    protein_start_fixture_run as mcp_protein_start_fixture_run,
)
from cellertz.mcp.start_run import protein_start_fixture_run, set_run_starter
from cellertz.runs.authorization import reject_requested_generator
from cellertz.runs.idempotency import (
    FileIdempotencyStore,
    hash_idempotency_key,
)
from cellertz.runs.models import ProteinFixtureRunRequest, ProteinRunStartResult
from cellertz.runs.starter import LocalProteinRunStarter
from cellertz.state.run_state import FileProteinRunStateStore
from cellertz.tools.protein_scoring.scoring import RANKING_METHOD


MCP_ROOT = Path("src/cellertz/mcp")


class CountingStarter:
    def __init__(self) -> None:
        self.calls = 0

    def start_fixture_run(self, request, idempotency_key):
        self.calls += 1
        return ProteinRunStartResult(
            run_id="protein_run_counted",
            mode="fixture",
            created=True,
            idempotent_replay=False,
            status="complete",
            idempotency_key=idempotency_key,
        )


def _request(n_candidates: int = 10) -> ProteinFixtureRunRequest:
    return ProteinFixtureRunRequest(
        target_id="BCMA",
        objective="binder_design",
        n_candidates=n_candidates,
    )


def _starter(tmp_path: Path, **kwargs) -> LocalProteinRunStarter:
    return LocalProteinRunStarter(
        idempotency_store=FileIdempotencyStore(tmp_path / "idempotency"),
        run_state_store=FileProteinRunStateStore(tmp_path / "runs"),
        artifact_root=tmp_path / "artifacts",
        **kwargs,
    )


def test_mutation_disabled_refuses_the_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "disabled")
    set_run_starter(_starter(tmp_path))

    payload = protein_start_fixture_run(
        target_id="BCMA",
        objective="binder_design",
        n_candidates=10,
        idempotency_key="k-disabled",
    )

    assert payload["status"] == "rejected"
    assert payload["error"] == "mutation_disabled"
    assert payload["created"] is False
    assert payload.get("run_id") is None
    assert not list((tmp_path / "runs").glob("**/state.json"))
    assert not list((tmp_path / "idempotency").glob("*.json"))


def test_unknown_mutation_mode_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "open")
    set_run_starter(_starter(tmp_path))

    payload = protein_start_fixture_run(
        target_id="BCMA",
        objective="binder_design",
        n_candidates=10,
        idempotency_key="k-open",
    )

    assert payload["error"] == "mutation_disabled"
    assert cellertz_capabilities()["mutation"] == {
        "enabled": False,
        "mode": "disabled",
        "real_generators_allowed": False,
    }


def test_fixture_only_allows_a_fixture_run(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    set_run_starter(_starter(tmp_path))

    payload = protein_start_fixture_run(
        target_id="BCMA",
        objective="binder_design",
        n_candidates=10,
        idempotency_key="k-fixture",
    )

    assert payload["created"] is True
    assert payload["idempotent_replay"] is False
    assert payload["mode"] == "fixture"
    assert payload["status"] == "complete"
    assert payload["run_id"].startswith("protein_run_")
    state = FileProteinRunStateStore(tmp_path / "runs").get_run_state(
        payload["run_id"]
    )
    assert state.found is True
    assert state.generation.status == "complete"
    assert state.scoring.status == "complete"
    assert state.ranking.status == "complete"
    assert state.critique.status == "complete"
    assert state.prioritization.status == "complete"


def test_never_accepts_real_generators(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    spy = CountingStarter()
    set_run_starter(spy)

    for generator in ("bindcraft", "rfdiffusion", "mber", "RFdiffusion", "BindCraft"):
        payload = protein_start_fixture_run(
            target_id="BCMA",
            objective="binder_design",
            n_candidates=10,
            idempotency_key=f"k-{generator}",
            generator=generator,
        )
        assert payload["status"] == "rejected"
        assert payload["error"] == "generator_not_allowed"
        assert reject_requested_generator(generator) == "generator_not_allowed"

    assert spy.calls == 0
    signature = inspect.signature(mcp_protein_start_fixture_run)
    assert "generator" not in signature.parameters


def test_same_key_same_request_runs_once(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    calls = {"n": 0}

    class Factory:
        def __init__(self, **kwargs):
            from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1

            self.inner = ProteinSupervisorV1(**kwargs)

        def run(self):
            calls["n"] += 1
            return self.inner.run()

    starter = _starter(tmp_path, supervisor_factory=Factory)
    first = starter.start_fixture_run(_request(), "same-key")
    second = starter.start_fixture_run(_request(), "same-key")

    assert first.created is True
    assert second.created is False
    assert second.idempotent_replay is True
    assert first.run_id == second.run_id
    assert calls["n"] == 1


def test_concurrent_same_key_runs_supervisor_once(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    lock = threading.Lock()
    calls = {"n": 0}
    entered = threading.Event()
    hold = threading.Event()

    class Factory:
        def __init__(self, **kwargs):
            from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1

            self.inner = ProteinSupervisorV1(**kwargs)

        def run(self):
            with lock:
                calls["n"] += 1
            entered.set()
            hold.wait(timeout=5)
            return self.inner.run()

    starter = _starter(tmp_path, supervisor_factory=Factory)
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        return starter.start_fixture_run(_request(3), "concurrent-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(go), pool.submit(go)]
        assert entered.wait(timeout=5)
        hold.set()
        results = [future.result(timeout=30) for future in futures]

    run_ids = {item.run_id for item in results}
    assert len(run_ids) == 1
    assert None not in run_ids
    assert calls["n"] == 1
    assert {item.created for item in results} == {True, False}
    assert all(item.status == "complete" for item in results)
    assert sum(1 for item in results if item.idempotent_replay) == 1
    assert all(item.run_id == next(iter(run_ids)) for item in results)


def test_same_key_different_payload_conflicts(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    starter = _starter(tmp_path)
    first = starter.start_fixture_run(_request(10), "conflict-key")
    second = starter.start_fixture_run(_request(3), "conflict-key")

    assert first.created is True
    assert second.status == "conflict"
    assert second.error == "idempotency_key_conflict"
    assert second.run_id is None
    assert second.created is False


def test_replay_returns_the_same_run_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    set_run_starter(_starter(tmp_path))
    first = protein_start_fixture_run(
        "BCMA", "binder_design", 10, "replay-key"
    )
    second = protein_start_fixture_run(
        "BCMA", "binder_design", 10, "replay-key"
    )

    assert first["run_id"] == second["run_id"]
    assert second["idempotent_replay"] is True
    assert second["created"] is False


def test_idempotency_filename_is_hashed(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    key = "key/with/slashes and spaces"
    starter = _starter(tmp_path)
    result = starter.start_fixture_run(_request(), key)

    store_root = tmp_path / "idempotency"
    expected = store_root / f"{hash_idempotency_key(key)}.json"
    assert expected.is_file()
    assert list(store_root.rglob("*.json")) == [expected]
    document = json.loads(expected.read_text())
    assert document["idempotency_key"] == key
    assert document["run_id"] == result.run_id
    assert document["mode"] == "fixture"
    assert document["status"] == "complete"
    assert "request_hash" in document
    assert "created_at" in document


def test_final_state_persists_under_outputs_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    starter = _starter(tmp_path)
    result = starter.start_fixture_run(_request(), "persist-key")
    state_path = tmp_path / "runs" / result.run_id / "state.json"

    assert result.status == "complete"
    assert state_path.is_file()
    document = json.loads(state_path.read_text())
    assert document["run_id"] == result.run_id
    assert document["generation"]["status"] == "complete"
    assert document["ranking"]["status"] == "complete"
    generation = json.loads(
        (tmp_path / "artifacts" / result.run_id / "generation.json").read_text()
    )
    assert generation["adapter_name"] == "fixture"


def test_supervisor_failure_is_recorded_not_masked(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")

    class Boom:
        def __init__(self, **kwargs):
            from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1

            self.inner = ProteinSupervisorV1(
                **kwargs,
                score_fn=lambda _candidates: (_ for _ in ()).throw(
                    RuntimeError("fixture scoring exploded")
                ),
            )

        def run(self):
            return self.inner.run()

    starter = _starter(tmp_path, supervisor_factory=Boom)
    result = starter.start_fixture_run(_request(3), "fail-key")
    state = FileProteinRunStateStore(tmp_path / "runs").get_run_state(result.run_id)

    assert result.status == "failed"
    assert result.created is True
    assert result.error == "fixture scoring exploded"
    assert state.found is True
    assert state.generation.status == "complete"
    assert state.scoring.status == "failed"
    assert state.scoring.error == "fixture scoring exploded"
    assert state.ranking.status == "pending"
    replay = starter.start_fixture_run(_request(3), "fail-key")
    assert replay.idempotent_replay is True
    assert replay.run_id == result.run_id
    assert replay.status == "failed"


def test_unexpected_supervisor_exception_is_not_complete(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")

    class Crash:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run(self):
            raise RuntimeError("supervisor crashed")

    starter = _starter(tmp_path, supervisor_factory=Crash)
    result = starter.start_fixture_run(_request(2), "crash-key")

    assert result.status == "failed"
    assert result.error == "supervisor crashed"
    assert result.created is True
    record = json.loads(
        next((tmp_path / "idempotency").glob("*.json")).read_text()
    )
    assert record["status"] == "failed"
    assert record["run_id"] == result.run_id


def test_mcp_does_not_implement_science():
    sources = [path.read_text() for path in MCP_ROOT.glob("*.py")]
    joined = "\n".join(sources)
    assert "from cellertz.domains.protein_design.agents.supervisor" not in joined
    assert "import cellertz.domains.protein_design.agents.supervisor" not in joined
    assert "from cellertz.domains.protein_design.agents.ranking" not in joined
    assert "from cellertz.agents.protein_supervisor" not in joined
    assert "from cellertz.agents.protein_ranking" not in joined
    assert ".rank(" not in joined
    assert "ranking_score =" not in joined
    server_source = (MCP_ROOT / "server.py").read_text()
    assert "protein_start_fixture_run" in server_source
    assert "LocalProteinRunStarter" not in server_source
    assert inspect.getsource(mcp_protein_start_fixture_run).count("return") == 1


def test_ranking_contracts_hold_on_starter_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    starter = _starter(tmp_path)
    result = starter.start_fixture_run(_request(10), "rank-key")
    ranking = json.loads(
        (tmp_path / "artifacts" / result.run_id / "ranking.json").read_text()
    )
    scores = [
        item["ranking_score"]
        for item in ranking
        if item["ranking_score"] is not None
    ]

    assert result.status == "complete"
    assert scores == sorted(scores, reverse=True)
    for item in ranking:
        assert item["ranking_signal"] == RANKING_METHOD
        assert item["is_probability"] is False
        assert item["recommended_as_hard_filter"] is False
        assert item["ranking_score"] == item["structural_evidence"]["ipsae"]


def test_mcp_v12_capabilities_and_read_tools_still_work(monkeypatch):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    started = mcp_protein_start_fixture_run(
        target_id="BCMA",
        objective="binder_design",
        n_candidates=10,
        idempotency_key="cap-key",
    )
    capabilities = cellertz_get_capabilities()

    assert capabilities["mutation"] == {
        "enabled": True,
        "mode": "fixture_only",
        "real_generators_allowed": False,
    }
    assert capabilities["protein_design"] == {
        "generation": True,
        "scoring": True,
        "ranking": True,
        "critic": True,
        "prioritization": True,
    }
    payload = mcp_protein_get_run_state(started["run_id"])
    assert payload["found"] is True
    assert payload["generation"]["status"] == "complete"
    missing = mcp_protein_get_run_state("protein_run_missing")
    assert missing == {"found": False, "run_id": "protein_run_missing"}
