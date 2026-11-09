import pytest

from cellertz.mcp.knowledge import set_knowledge_reader
from cellertz.mcp.run_state import set_run_state_reader
from cellertz.mcp.start_run import set_run_starter


@pytest.fixture(autouse=True)
def isolate_run_state_root(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("run_state_root")
    idem = tmp_path_factory.mktemp("idempotency_root")
    artifacts = tmp_path_factory.mktemp("artifact_root")
    monkeypatch.setenv("CELLERTZ_RUN_STATE_ROOT", str(root))
    monkeypatch.setenv("CELLERTZ_IDEMPOTENCY_ROOT", str(idem))
    monkeypatch.setenv("CELLERTZ_RUN_ARTIFACT_ROOT", str(artifacts))
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "disabled")
    set_run_state_reader(None)
    set_run_starter(None)
    set_knowledge_reader(None)
    yield
    set_run_state_reader(None)
    set_run_starter(None)
    set_knowledge_reader(None)
