from pathlib import Path

from cellertz.knowledge.health import inspect_vault
from cellertz.knowledge.reader import FileKnowledgeReader
from cellertz.mcp.capabilities import cellertz_capabilities


VAULT = Path("second_brain")
PACKAGE_ID = "protein_scoring_bcma_boltz2_v2"


def test_health_cli_passes_on_current_vault():
    from cellertz.knowledge.health import main

    assert main(["--root", str(VAULT)]) == 0


def test_current_vault_is_healthy():
    health = inspect_vault(VAULT)
    reader = FileKnowledgeReader(VAULT)
    relations = sum(len(item.relations) for item in reader.list())

    assert health.ok is True
    assert health.nodes == 10
    assert health.relations == relations
    assert health.broken_links == 0
    assert health.validated_packages == 1
    assert health.invalid_packages == 0
    assert health.issues == ()
    assert "PASS" in health.summary()


def test_front_door_matches_live_retrieval():
    text = (VAULT / "index.md").read_text()
    assert "retrieve            → not built" not in text
    assert "Hermes              → does not read this vault yet" not in text
    assert "FileKnowledgeReader" in text
    assert "knowledge_ground_package" in text
    assert PACKAGE_ID in text
    assert "Automatic agent writeback is forbidden." in text
    assert "Working is not semantic" in text


def test_health_is_not_an_mcp_tool():
    source = Path("src/cellertz/mcp/server.py").read_text()
    assert "knowledge_health" not in source
    assert "inspect_vault" not in source
    assert "knowledge_health" not in str(cellertz_capabilities())


def test_broken_link_fails_health(tmp_path):
    vault = tmp_path / "second_brain"
    (vault / "claims").mkdir(parents=True)
    (vault / "claims" / "orphan.md").write_text(
        "---\n"
        "id: orphan_claim\n"
        "type: claim\n"
        "domain: protein_design\n"
        "status: validated\n"
        "version: 1\n"
        "statement: Orphan statement.\n"
        "definition: not_provided\n"
        "---\n\n"
        "# Orphan\n\n"
        "Supported by:\n\n"
        "- [[missing_evidence]]\n"
    )
    health = inspect_vault(vault)
    codes = {issue.code for issue in health.issues}
    assert health.ok is False
    assert "broken_link" in codes
    assert "claim_without_evidence" in codes or "missing_evidence" in codes
    assert "FAIL" in health.summary()


def test_validated_package_requires_provenance(tmp_path):
    vault = tmp_path / "second_brain"
    (vault / "packages").mkdir(parents=True)
    (vault / "packages" / "bare.md").write_text(
        "---\n"
        "id: bare_package\n"
        "type: validated_evidence\n"
        "domain: protein_design\n"
        "status: validated\n"
        "version: 1\n"
        "---\n\n"
        "# Bare package\n"
    )
    health = inspect_vault(vault)
    codes = {issue.code for issue in health.issues}
    assert health.ok is False
    assert "missing_provenance" in codes
    assert "package_without_claims" in codes


def test_package_freshness_is_recorded_not_expired():
    item = FileKnowledgeReader(VAULT).get(PACKAGE_ID)
    source = Path("src/cellertz/knowledge/health.py").read_text()
    assert item is not None
    assert item.status == "validated"
    assert item.validated_at == "2026-09-19"
    assert item.reviewed_at == "2026-09-19"
    assert item.superseded_by is None
    assert "expires_at" not in source
    assert "expires_at" not in item.to_dict()
