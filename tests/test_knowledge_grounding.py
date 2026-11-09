from pathlib import Path

from cellertz.knowledge.grounding import ground_package
from cellertz.knowledge.reader import FileKnowledgeReader


VAULT = Path("second_brain")
PACKAGE_ID = "protein_scoring_bcma_boltz2_v2"
FORBIDDEN_GLOSS = (
    "inverse protein",
    "Surface Area Elasticity",
    "surface area elasticity",
)


def _payloads(root: Path) -> dict[str, bytes]:
    return {
        path.as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_grounded_package_returns_compact_evidence_packet():
    packet = ground_package(FileKnowledgeReader(VAULT), PACKAGE_ID)
    payload = packet.to_dict()
    statements = {item["statement"] for item in packet.claims}
    used = {item["id"] for item in packet.knowledge_used}

    assert packet.found is True
    assert packet.package_id == PACKAGE_ID
    assert statements == {
        "Raw ipSAE remains the primary structural ranking signal.",
        "Boltz2 ensemble metrics are auxiliary structural evidence.",
        "No ipSAE + Boltz2 composite score is currently validated.",
    }
    assert {item["id"] for item in packet.claims} == {
        "ipsae_primary_ranking",
        "boltz2_auxiliary_evidence",
        "no_validated_composite_score",
    }
    assert packet.evidence == {
        "global": {"ipsae_ap": 0.0924, "boltz2_mean_ap": 0.0825},
        "bindcraft1": {"ipsae_ap": 0.3082, "boltz2_std_inverted_ap": 0.3553},
        "rfd5": {"ipsae_ap": 0.2501, "boltz2_std_inverted_ap": 0.0962},
    }
    assert packet.validated_interpretation == {
        "local_boltz_gain": "BindCraft1 only",
        "generalizes_to_rfd5": False,
        "ranking_decision": "raw ipSAE remains primary",
    }
    assert used == {
        PACKAGE_ID,
        "ipsae_primary_ranking",
        "boltz2_auxiliary_evidence",
        "no_validated_composite_score",
        "boltz2_v2_global_benchmark",
        "boltz2_v2_bindcraft1_holdout",
        "boltz2_v2_rfd5_holdout",
        "chow_bcma",
    }
    assert packet.unsupported_expansions == ("ipSAE",)
    assert any("ipSAE" in message for message in packet.unavailable)
    assert "body" not in payload
    assert "supporting_evidence" not in payload
    assert payload["operational"]["knowledge_items_used"] == 8
    assert payload["operational"]["number_of_knowledge_tool_calls"] == 1
    assert payload["operational"]["payload_bytes"] < 2500
    joined = str(payload)
    for gloss in FORBIDDEN_GLOSS:
        assert gloss not in joined


def test_grounded_answer_does_not_modify_vault():
    before = _payloads(VAULT)
    ground_package(FileKnowledgeReader(VAULT), PACKAGE_ID)
    assert _payloads(VAULT) == before


def test_missing_package_is_unavailable():
    packet = ground_package(FileKnowledgeReader(VAULT), "missing_package")
    assert packet.found is False
    assert packet.claims == ()
    assert packet.knowledge_used == ()
    assert packet.unavailable == ("missing_package not found",)
    assert packet.operational["knowledge_items_used"] == 0
