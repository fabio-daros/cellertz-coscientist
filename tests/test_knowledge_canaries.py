import inspect
from pathlib import Path

from cellertz.domains.protein_design.agents.critic import ProteinCriticAgentV1
from cellertz.domains.protein_design.agents.grounded_critic import (
    EvidencePacketCriticContextProvider,
    attach_grounded_context,
)
from cellertz.domains.protein_design.agents.prioritization import (
    ProteinPrioritizationAgentV1,
)
from cellertz.domains.protein_design.agents.ranking import (
    ProteinRankingAgentV1,
    ProteinRankingRecord,
)
from cellertz.knowledge.grounding import ground_package
from cellertz.knowledge.reader import FileKnowledgeReader
from cellertz.tools.protein_scoring import scoring_v2


VAULT = Path("second_brain")
PACKAGE_ID = "protein_scoring_bcma_boltz2_v2"
FORBIDDEN_GLOSS = (
    "inverse protein",
    "Surface Area Elasticity",
    "surface area elasticity",
)


def _packet():
    return ground_package(FileKnowledgeReader(VAULT), PACKAGE_ID)


def _statements():
    return {item["statement"] for item in _packet().claims}


def test_canary_1_raw_ipsae_remains_primary():
    packet = _packet()
    assert (
        "Raw ipSAE remains the primary structural ranking signal."
        in _statements()
    )
    assert packet.validated_interpretation["ranking_decision"] == (
        "raw ipSAE remains primary"
    )


def test_canary_2_boltz2_remains_auxiliary():
    assert (
        "Boltz2 ensemble metrics are auxiliary structural evidence."
        in _statements()
    )


def test_canary_3_no_validated_composite_exists():
    packet = _packet()
    assert (
        "No ipSAE + Boltz2 composite score is currently validated."
        in _statements()
    )
    joined = str(packet.to_dict())
    assert "composite score" in joined
    assert "ranking_score =" not in joined
    assert "0.5 * ipsae" not in joined.lower()


def test_canary_4_ysd_is_not_an_input_to_candidate_ranking():
    ranking_source = inspect.getsource(ProteinRankingAgentV1)
    scoring_source = inspect.getsource(scoring_v2)
    packet = str(_packet().to_dict()).lower()

    assert "ysd" not in ranking_source.lower()
    assert "ysd" not in scoring_source.lower()
    assert "ysd" not in packet
    ranked = ProteinRankingAgentV1().rank(
        [
            scoring_v2.ProteinScoreResultV2(
                candidate_id="a",
                baseline_score=0.2,
                structural_evidence={"ipsae": 0.2, "boltz2_iptm_mean": 0.99},
            ),
            scoring_v2.ProteinScoreResultV2(
                candidate_id="b",
                baseline_score=0.9,
                structural_evidence={"ipsae": 0.9, "boltz2_iptm_mean": 0.10},
            ),
        ]
    )
    assert [item.candidate_id for item in ranked] == ["b", "a"]


def test_canary_5_grounded_explanation_cannot_change_selection():
    prioritization_source = Path(
        "src/cellertz/domains/protein_design/agents/prioritization.py"
    ).read_text()
    assert "GroundedCriticExplanation" not in prioritization_source
    assert "hermes" not in prioritization_source.lower()

    ranked = [
        ProteinRankingRecord(
            candidate_id="high",
            rank=1,
            ranking_signal="ipsae",
            ranking_score=0.90,
            structural_evidence={"ipsae": 0.90, "boltz2_iptm_mean": 0.20},
        ),
        ProteinRankingRecord(
            candidate_id="low",
            rank=2,
            ranking_signal="ipsae",
            ranking_score=0.70,
            structural_evidence={"ipsae": 0.70, "boltz2_iptm_mean": 0.99},
        ),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)
    context = EvidencePacketCriticContextProvider(
        FileKnowledgeReader(VAULT)
    ).for_domain(domain="protein_design", purpose="structural_scoring")
    attach_grounded_context(reviewed, context)
    selected = ProteinPrioritizationAgentV1().select(reviewed)
    selected_again = ProteinPrioritizationAgentV1().select(reviewed)
    assert [item.candidate_id for item in selected if item.selected] == [
        item.candidate_id for item in selected_again if item.selected
    ]


def test_canary_6_undefined_terms_remain_undefined():
    packet = _packet()
    payload = packet.to_dict()
    assert packet.unsupported_expansions == ("ipSAE",)
    assert any("does not define" in message for message in packet.unavailable)
    for gloss in FORBIDDEN_GLOSS:
        assert gloss not in str(payload)
    claim = FileKnowledgeReader(VAULT).get("ipsae_primary_ranking")
    assert claim is not None
    assert claim.definition in {None, "not_provided", "unknown"}
