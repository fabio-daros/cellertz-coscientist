import inspect
from pathlib import Path

import cellertz.domains.protein_design.agents.critic as critic_module
from cellertz.domains.protein_design.agents.grounded_critic import (
    PRIMARY_RANKING_WARNING,
    EvidencePacketCriticContextProvider,
    attach_grounded_context,
    grounded_context_from_packet,
    unavailable_context,
)
from cellertz.domains.protein_design.agents.critic import ProteinCriticAgentV1
from cellertz.domains.protein_design.agents.ranking import ProteinRankingRecord
from cellertz.knowledge.grounding import EvidencePacket
from cellertz.knowledge.reader import FileKnowledgeReader


VAULT = Path("second_brain")
PACKAGE_ID = "protein_scoring_bcma_boltz2_v2"


def _record(
    candidate_id: str,
    rank: int,
    ranking_score: float | None,
    *,
    boltz_mean: float | None = None,
) -> ProteinRankingRecord:
    return ProteinRankingRecord(
        candidate_id=candidate_id,
        rank=rank,
        ranking_signal="ipsae",
        ranking_score=ranking_score,
        is_probability=False,
        recommended_as_hard_filter=False,
        structural_evidence={
            "ipsae": ranking_score,
            "boltz2_iptm_mean": boltz_mean,
        },
    )


def _packet(
    *,
    found: bool = True,
    package_id: str = PACKAGE_ID,
    claims: tuple[dict, ...] = (),
    knowledge_used: tuple[dict[str, str], ...] = (),
    interpretation: dict | None = None,
    unsupported: tuple[str, ...] = (),
) -> EvidencePacket:
    return EvidencePacket(
        found=found,
        package_id=package_id,
        claims=claims,
        evidence={},
        validated_interpretation=interpretation,
        knowledge_used=knowledge_used,
        unsupported_expansions=unsupported,
        unavailable=() if found else (f"{package_id} not found",),
        operational={
            "knowledge_items_used": len(knowledge_used),
            "number_of_knowledge_tool_calls": 1,
        },
    )


def _mixed_ranked() -> list[ProteinRankingRecord]:
    return [
        _record("A", 1, 0.90, boltz_mean=0.99),
        _record("B", 2, 0.80, boltz_mean=0.10),
        _record("C", 3, 0.70, boltz_mean=0.50),
    ]


def test_critic_agent_does_not_open_second_brain():
    source = inspect.getsource(critic_module)
    assert "FileKnowledgeReader" not in source
    assert "ground_package" not in source
    assert "EvidencePacket" not in source
    assert "GroundedCritic" not in source


def test_agreement_is_identical_with_and_without_second_brain():
    ranked = _mixed_ranked()
    without = ProteinCriticAgentV1().review(ranked)
    provider = EvidencePacketCriticContextProvider(FileKnowledgeReader(VAULT))
    context = provider.for_domain(
        domain="protein_design",
        purpose="structural_scoring",
    )
    with_brain = attach_grounded_context(without, context)

    assert without[1].critic_assessment.agreement == "mixed"
    assert with_brain[1].critic.critic_assessment.agreement == "mixed"
    for original, grounded in zip(without, with_brain, strict=True):
        assert original.to_dict() == grounded.critic.to_dict()
        assert original.rank == grounded.critic.rank
        assert original.ranking_score == grounded.critic.ranking_score
        assert (
            original.critic_assessment.structural_conflict
            == grounded.critic.critic_assessment.structural_conflict
        )


def test_low_boltz_does_not_demote_high_ipsae_with_grounded_context():
    ranked = [
        _record("A", 1, 0.90, boltz_mean=0.20),
        _record("B", 2, 0.70, boltz_mean=0.99),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)
    context = EvidencePacketCriticContextProvider(
        FileKnowledgeReader(VAULT)
    ).for_domain(domain="protein_design", purpose="structural_scoring")
    grounded = attach_grounded_context(reviewed, context)

    assert [item.critic.candidate_id for item in grounded] == ["A", "B"]
    assert [item.critic.rank for item in grounded] == [1, 2]
    assert grounded[0].critic.ranking_score == 0.90
    assert context.status == "available"
    assert PRIMARY_RANKING_WARNING in context.contextual_warnings


def test_high_boltz_does_not_promote_low_ipsae_with_grounded_context():
    ranked = [
        _record("A", 1, 0.90, boltz_mean=0.20),
        _record("B", 2, 0.70, boltz_mean=0.99),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)
    grounded = attach_grounded_context(
        reviewed,
        EvidencePacketCriticContextProvider(
            FileKnowledgeReader(VAULT)
        ).for_domain(domain="protein_design", purpose="structural_scoring"),
    )

    assert grounded[1].critic.candidate_id == "B"
    assert grounded[1].critic.rank == 2
    assert grounded[1].critic.ranking_score == 0.70


def test_unavailable_packet_does_not_block_critic():
    ranked = _mixed_ranked()
    reviewed = ProteinCriticAgentV1().review(ranked)
    grounded = attach_grounded_context(reviewed, unavailable_context(PACKAGE_ID))

    assert reviewed[1].critic_assessment.agreement == "mixed"
    assert grounded[1].critic.critic_assessment.agreement == "mixed"
    assert grounded[1].grounded_context.status == "unavailable"
    assert grounded[1].grounded_context.validated_constraints == ()
    assert grounded[0].critic.rank == reviewed[0].rank


def test_missing_package_from_provider_is_unavailable():
    context = EvidencePacketCriticContextProvider(
        FileKnowledgeReader(VAULT)
    ).for_domain(domain="protein_design", purpose="expression")

    assert context.status == "unavailable"
    assert context.package_id is None


def test_real_package_becomes_validated_critic_context():
    context = EvidencePacketCriticContextProvider(
        FileKnowledgeReader(VAULT)
    ).for_domain(domain="protein_design", purpose="structural_scoring")

    assert context.status == "available"
    assert context.package_id == PACKAGE_ID
    assert context.validated_constraints == (
        "Raw ipSAE remains the primary structural ranking signal.",
        "Boltz2 ensemble metrics are auxiliary structural evidence.",
        "No ipSAE + Boltz2 composite score is currently validated.",
    )
    assert context.knowledge_used == (
        "ipsae_primary_ranking",
        "boltz2_auxiliary_evidence",
        "no_validated_composite_score",
    )
    assert context.unsupported_terms == ("ipSAE",)
    assert context.to_dict()["contextual_warning"] == PRIMARY_RANKING_WARNING


def test_only_validated_knowledge_enters_normative_context():
    packet = _packet(
        claims=(
            {"id": "ok", "statement": "validated statement"},
            {"id": "old", "statement": "superseded statement"},
            {"id": "obs", "statement": "observed statement"},
            {"id": "ref", "statement": "reference statement"},
        ),
        knowledge_used=(
            {"id": PACKAGE_ID, "status": "validated"},
            {"id": "ok", "status": "validated"},
            {"id": "old", "status": "superseded"},
            {"id": "obs", "status": "observed"},
            {"id": "ref", "status": "reference"},
        ),
        interpretation={"ranking_decision": "raw ipSAE remains primary"},
        unsupported=("ipSAE",),
    )
    context = grounded_context_from_packet(packet)

    assert context.status == "available"
    assert context.validated_constraints == ("validated statement",)
    assert context.knowledge_used == ("ok",)


def test_superseded_package_is_unavailable():
    packet = _packet(
        claims=({"id": "ok", "statement": "validated statement"},),
        knowledge_used=(
            {"id": PACKAGE_ID, "status": "superseded"},
            {"id": "ok", "status": "validated"},
        ),
        interpretation={"ranking_decision": "raw ipSAE remains primary"},
    )
    context = grounded_context_from_packet(packet)

    assert context.status == "unavailable"
    assert context.validated_constraints == ()
    assert context.contextual_warnings == ()


def test_grounded_result_keeps_assessment_separate_from_context():
    ranked = [_record("design_042", 3, 0.91, boltz_mean=0.50)]
    reviewed = ProteinCriticAgentV1().review(
        ranked + [_record("other", 4, 0.80, boltz_mean=0.40)]
    )
    context = EvidencePacketCriticContextProvider(
        FileKnowledgeReader(VAULT)
    ).for_domain(domain="protein_design", purpose="structural_scoring")
    payload = attach_grounded_context(reviewed, context)[0].to_dict()

    assert payload["candidate_id"] == "design_042"
    assert payload["rank"] == 3
    assert payload["ranking_score"] == 0.91
    assert "agreement" in payload["critic_assessment"]
    assert payload["grounded_context"]["package_id"] == PACKAGE_ID
    assert "critic_score" not in payload
    assert payload["grounded_context"]["status"] == "available"
