from pathlib import Path

from cellertz.knowledge.reader import FileKnowledgeReader
from cellertz.mcp.capabilities import cellertz_capabilities
from cellertz.mcp.knowledge import (
    knowledge_get,
    knowledge_ground_package,
    knowledge_neighbors,
    set_knowledge_reader,
)
from cellertz.mcp.server import cellertz_get_capabilities
from cellertz.mcp.server import knowledge_get as mcp_knowledge_get
from cellertz.mcp.server import knowledge_ground_package as mcp_knowledge_ground_package
from cellertz.mcp.server import knowledge_neighbors as mcp_knowledge_neighbors
from cellertz.mcp.server import protein_get_run_state as mcp_protein_get_run_state


VAULT = Path("second_brain")
PACKAGE_ID = "protein_scoring_bcma_boltz2_v2"
MCP_ROOT = Path("src/cellertz/mcp")


def _payloads(root: Path) -> dict[str, bytes]:
    return {
        path.as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_knowledge_get_finds_real_package():
    payload = knowledge_get(PACKAGE_ID)

    assert payload["found"] is True
    assert payload["item"] == {
        "id": PACKAGE_ID,
        "type": "validated_evidence",
        "domain": "protein_design",
        "status": "validated",
        "version": 1,
        "title": "Protein Scoring BCMA — Boltz2 v2",
    }
    relations = {(item["relation"], item["target"]) for item in payload["relations"]}
    assert ("has_claim", "ipsae_primary_ranking") in relations
    assert ("has_claim", "boltz2_auxiliary_evidence") in relations
    assert ("has_claim", "no_validated_composite_score") in relations
    assert all(item["resolved"] is True for item in payload["relations"])
    assert mcp_knowledge_get(PACKAGE_ID) == payload


def test_knowledge_get_finds_real_claim():
    payload = knowledge_get("ipsae_primary_ranking")

    assert payload["found"] is True
    assert payload["item"]["id"] == "ipsae_primary_ranking"
    assert payload["item"]["type"] == "claim"
    assert payload["item"]["statement"] == (
        "Raw ipSAE remains the primary structural ranking signal."
    )
    assert payload["item"]["definition"] == "not_provided"
    assert payload["item"]["undefined_terms"] == ["ipSAE"]
    targets = {item["target"] for item in payload["relations"]}
    assert "boltz2_v2_global_benchmark" in targets
    assert PACKAGE_ID in targets


def test_missing_id_is_not_found():
    payload = knowledge_get("something_that_does_not_exist")

    assert payload == {"found": False, "id": "something_that_does_not_exist"}


def test_path_traversal_is_refused():
    payload = knowledge_get("../../.env")

    assert payload["found"] is False
    assert payload["error"] == "path_outside_vault"
    assert knowledge_neighbors("/etc/passwd")["error"] == "path_outside_vault"


def test_broken_wikilink_stays_unresolved(tmp_path):
    path = tmp_path / "claims" / "orphan.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\n"
        "id: orphan_claim\n"
        "type: claim\n"
        "domain: protein_design\n"
        "status: validated\n"
        "version: 1\n"
        "statement: Orphan claim.\n"
        "definition: not_provided\n"
        "---\n\n"
        "# Orphan\n\n"
        "Supported by:\n\n"
        "- [[something_that_does_not_exist]]\n"
    )
    set_knowledge_reader(FileKnowledgeReader(tmp_path))

    payload = knowledge_get("orphan_claim")

    assert payload["found"] is True
    assert payload["relations"] == [
        {
            "relation": "supported_by",
            "target": "something_that_does_not_exist",
            "resolved": False,
        }
    ]


def test_retrieval_does_not_modify_markdown():
    before = _payloads(VAULT)
    knowledge_get(PACKAGE_ID)
    knowledge_get("ipsae_primary_ranking")
    knowledge_neighbors(PACKAGE_ID)
    knowledge_ground_package(PACKAGE_ID)
    knowledge_get("missing")
    knowledge_get("../../.env")

    assert _payloads(VAULT) == before


def test_mcp_does_not_parse_markdown():
    joined = "\n".join(path.read_text() for path in MCP_ROOT.glob("*.py"))
    assert "parse_frontmatter" not in joined
    assert "FRONTMATTER" not in joined
    assert "WIKILINK" not in joined
    assert "extract_wikilink" not in joined
    assert "[[ " not in joined
    assert "FileKnowledgeReader" in joined


def test_mutation_mode_does_not_affect_knowledge(monkeypatch):
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "disabled")
    disabled = knowledge_get(PACKAGE_ID)
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    enabled = knowledge_get(PACKAGE_ID)

    assert disabled == enabled
    assert disabled["found"] is True
    assert cellertz_capabilities()["knowledge"] == {
        "get": True,
        "neighbors": True,
        "ground_package": True,
        "list": False,
        "semantic_search": False,
        "health": False,
    }


def test_previous_tools_still_work():
    capabilities = cellertz_get_capabilities()

    assert capabilities == cellertz_capabilities()
    assert capabilities["protein_design"]["ranking"] is True
    assert capabilities["mutation"]["real_generators_allowed"] is False
    assert mcp_protein_get_run_state("protein_run_missing") == {
        "found": False,
        "run_id": "protein_run_missing",
    }
    neighbors = mcp_knowledge_neighbors(PACKAGE_ID)
    assert neighbors["found"] is True
    assert any(
        item["target"] == "ipsae_primary_ranking"
        or item["source"] == "ipsae_primary_ranking"
        for item in neighbors["relations"]
    )


def test_knowledge_ground_package_returns_compact_packet():
    payload = knowledge_ground_package(PACKAGE_ID)

    assert payload["found"] is True
    assert payload["package_id"] == PACKAGE_ID
    assert payload["evidence"]["bindcraft1"] == {
        "ipsae_ap": 0.3082,
        "boltz2_std_inverted_ap": 0.3553,
    }
    assert payload["evidence"]["rfd5"] == {
        "ipsae_ap": 0.2501,
        "boltz2_std_inverted_ap": 0.0962,
    }
    assert payload["validated_interpretation"]["ranking_decision"] == (
        "raw ipSAE remains primary"
    )
    assert payload["unsupported_expansions"] == ["ipSAE"]
    assert payload["operational"]["knowledge_items_used"] == 8
    assert payload["operational"]["number_of_knowledge_tool_calls"] == 1
    assert "body" not in payload
    assert mcp_knowledge_ground_package(PACKAGE_ID) == payload


def test_knowledge_ground_package_missing_and_unsafe_ids():
    missing = knowledge_ground_package("missing_package")
    unsafe = knowledge_ground_package("../../.env")

    assert missing["found"] is False
    assert missing["unavailable"] == ["missing_package not found"]
    assert unsafe["found"] is False
    assert unsafe["error"] == "path_outside_vault"
