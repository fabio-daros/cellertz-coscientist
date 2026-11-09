from cellertz.mcp.capabilities import cellertz_capabilities
from cellertz.mcp.server import cellertz_get_capabilities


def test_capabilities_payload_is_disabled_by_default():
    payload = cellertz_capabilities()

    assert payload["service"] == "cellertz-ai-coscientist"
    assert "mutating_operations" not in payload
    assert payload["mutation"] == {
        "enabled": False,
        "mode": "disabled",
        "real_generators_allowed": False,
    }
    assert payload["protein_design"] == {
        "generation": True,
        "scoring": True,
        "ranking": True,
        "critic": True,
        "prioritization": True,
    }
    assert payload["knowledge"] == {
        "get": True,
        "neighbors": True,
        "ground_package": True,
        "list": False,
        "semantic_search": False,
        "health": False,
    }
    assert payload["episodic_memory"] == {
        "memory_house": "episodic",
        "epistemic_status": "observed",
        "write": False,
        "get_run": True,
        "find_runs_by_target": True,
        "find_candidate": True,
    }


def test_fixture_only_capabilities_are_explicit(monkeypatch):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    payload = cellertz_capabilities()

    assert "mutating_operations" not in payload
    assert payload["mutation"] == {
        "enabled": True,
        "mode": "fixture_only",
        "real_generators_allowed": False,
    }


def test_mcp_tool_returns_the_same_payload():
    assert cellertz_get_capabilities() == cellertz_capabilities()
