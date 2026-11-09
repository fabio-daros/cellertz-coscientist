from pathlib import Path

import pytest

from cellertz.knowledge.models import (
    DuplicateKnowledgeId,
    InvalidKnowledgeDocument,
    KnowledgePathError,
)
from cellertz.knowledge.reader import FileKnowledgeReader, extract_wikilink_ids

VAULT = Path("second_brain")
PACKAGE_ID = "protein_scoring_bcma_boltz2_v2"
PACKAGE_GRAPH = {
    "chow_bcma",
    "ipsae_primary_ranking",
    "boltz2_auxiliary_evidence",
    "no_validated_composite_score",
    "boltz2_v2_global_benchmark",
    "boltz2_v2_bindcraft1_holdout",
    "boltz2_v2_rfd5_holdout",
}
PDL1_GRAPH = {
    "pdb_4zqk",
    "pdl1_pd1_interface_4zqk",
}


def _note(
    root: Path,
    folder: str,
    name: str,
    *,
    item_id: str,
    item_type: str,
    status: str = "validated",
    body: str,
) -> Path:
    path = root / folder / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"id: {item_id}",
        f"type: {item_type}",
        "domain: protein_design",
        f"status: {status}",
        "version: 1",
    ]
    if item_type == "claim":
        lines.append(f"statement: {item_id} statement.")
        lines.append("definition: not_provided")
    lines.extend(["---", "", body])
    path.write_text("\n".join(lines) + "\n")
    return path


def _vault_payloads(root: Path) -> dict[str, tuple[int, bytes]]:
    payloads = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            payloads[path.as_posix()] = (path.stat().st_mtime_ns, path.read_bytes())
    return payloads


def test_get_package_by_id():
    item = FileKnowledgeReader(VAULT).get(PACKAGE_ID)

    assert item is not None
    assert item.id == PACKAGE_ID
    assert item.type == "validated_evidence"
    assert item.domain == "protein_design"
    assert item.status == "validated"
    assert item.version == 1
    assert item.title == "Protein Scoring BCMA — Boltz2 v2"
    assert item.source_path == "packages/protein_scoring_bcma_boltz2_v2.md"
    assert item.packet == {
        "local_boltz_gain": "BindCraft1 only",
        "generalizes_to_rfd5": False,
        "ranking_decision": "raw ipSAE remains primary",
    }


def test_get_claim_by_id():
    claim = FileKnowledgeReader(VAULT).get_claim("ipsae_primary_ranking")

    assert claim is not None
    assert claim.type == "claim"
    assert claim.statement == (
        "Raw ipSAE remains the primary structural ranking signal."
    )
    assert claim.definition == "not_provided"
    assert claim.undefined_terms == ("ipSAE",)
    assert claim.title.startswith("Raw ipSAE remains the primary")
    assert FileKnowledgeReader(VAULT).get_claim(PACKAGE_ID) is None


def test_list_by_domain_type_and_status():
    reader = FileKnowledgeReader(VAULT)

    by_domain = reader.list(domain="protein_design")
    by_type = reader.list(type="claim")
    by_status = reader.list(status="validated")
    reference = reader.list(status="reference")

    assert {item.id for item in by_domain} == {PACKAGE_ID, *PACKAGE_GRAPH, *PDL1_GRAPH}
    assert {item.id for item in by_type} == {
        "ipsae_primary_ranking",
        "boltz2_auxiliary_evidence",
        "no_validated_composite_score",
    }
    assert "chow_bcma" not in {item.id for item in by_status}
    assert {item.id for item in reference} == {"chow_bcma", "pdb_4zqk"}
    assert reader.list(domain="missing") == ()


def test_parse_frontmatter_and_wikilinks():
    item = FileKnowledgeReader(VAULT).get("ipsae_primary_ranking")
    assert item is not None
    assert extract_wikilink_ids(item.body) == (
        "boltz2_v2_global_benchmark",
        "boltz2_v2_bindcraft1_holdout",
        "boltz2_v2_rfd5_holdout",
        PACKAGE_ID,
    )
    assert item.to_dict()["links"] == [
        "boltz2_v2_global_benchmark",
        "boltz2_v2_bindcraft1_holdout",
        "boltz2_v2_rfd5_holdout",
        PACKAGE_ID,
    ]
    assert item.to_dict()["unresolved_links"] == []


def test_resolve_package_links_to_claims_and_evidence():
    item = FileKnowledgeReader(VAULT).get(PACKAGE_ID)
    assert item is not None
    assert {link.target_id for link in item.links} == PACKAGE_GRAPH
    assert all(link.resolved for link in item.links)


def test_real_package_graph_matches_obsidian_vault():
    reader = FileKnowledgeReader(VAULT)
    related = set()
    for relation in reader.neighbors(PACKAGE_ID):
        other = (
            relation.target if relation.source == PACKAGE_ID else relation.source
        )
        if other != PACKAGE_ID:
            related.add(other)

    assert related == PACKAGE_GRAPH
    assert (
        "has_claim",
        "ipsae_primary_ranking",
    ) in {(item.relation, item.target) for item in reader.neighbors(PACKAGE_ID)}
    assert (
        "supported_by",
        "boltz2_v2_global_benchmark",
    ) in {
        (item.relation, item.target)
        for item in reader.neighbors("ipsae_primary_ranking")
    }
    assert (
        "uses_dataset",
        "chow_bcma",
    ) in {
        (item.relation, item.target)
        for item in reader.neighbors("boltz2_v2_global_benchmark")
    }
    assert (
        "part_of",
        PACKAGE_ID,
    ) in {
        (item.relation, item.target)
        for item in reader.neighbors("no_validated_composite_score")
    }


def test_missing_item_is_not_found():
    assert FileKnowledgeReader(VAULT).get("something_that_does_not_exist") is None


def test_duplicate_id_is_an_error(tmp_path):
    _note(
        tmp_path,
        "claims",
        "one",
        item_id="same_id",
        item_type="claim",
        body="# One\n",
    )
    _note(
        tmp_path,
        "claims",
        "two",
        item_id="same_id",
        item_type="claim",
        body="# Two\n",
    )

    with pytest.raises(DuplicateKnowledgeId):
        FileKnowledgeReader(tmp_path)


def test_invalid_status_and_type_are_errors(tmp_path):
    _note(
        tmp_path,
        "claims",
        "bad_type",
        item_id="bad_type",
        item_type="banana",
        body="# Bad type\n",
    )
    with pytest.raises(InvalidKnowledgeDocument, match="Invalid type"):
        FileKnowledgeReader(tmp_path)

    root = tmp_path / "status"
    _note(
        root,
        "claims",
        "bad_status",
        item_id="bad_status",
        item_type="claim",
        status="awesome",
        body="# Bad status\n",
    )
    with pytest.raises(InvalidKnowledgeDocument, match="Invalid status"):
        FileKnowledgeReader(root)


def test_path_traversal_is_refused():
    reader = FileKnowledgeReader(VAULT)

    with pytest.raises(KnowledgePathError):
        reader.get("../../.env")
    with pytest.raises(KnowledgePathError):
        reader.get("/etc/passwd")
    with pytest.raises(KnowledgePathError):
        reader.get("outputs/secret")
    assert reader.get("ipsae_primary_ranking") is not None


def test_broken_wikilink_is_not_invented(tmp_path):
    _note(
        tmp_path,
        "claims",
        "orphan",
        item_id="orphan_claim",
        item_type="claim",
        body="# Orphan\n\nSupported by:\n\n- [[something_that_does_not_exist]]\n",
    )
    reader = FileKnowledgeReader(tmp_path)
    item = reader.get("orphan_claim")

    assert item is not None
    assert item.links[0].target_id == "something_that_does_not_exist"
    assert item.links[0].resolved is False
    assert reader.get("something_that_does_not_exist") is None


def test_reader_does_not_modify_any_file():
    before = _vault_payloads(VAULT)
    reader = FileKnowledgeReader(VAULT)
    reader.get(PACKAGE_ID)
    reader.get_claim("ipsae_primary_ranking")
    reader.list(domain="protein_design", type="claim", status="validated")
    reader.neighbors(PACKAGE_ID)

    assert _vault_payloads(VAULT) == before


def test_reader_source_is_read_only():
    joined = "\n".join(
        path.read_text()
        for path in Path("src/cellertz/knowledge").glob("*.py")
        if path.name != "promotion.py"
    )
    assert "write_text" not in joined
    assert "unlink" not in joined
    assert "os.replace" not in joined
    assert "search_semantically" not in joined
