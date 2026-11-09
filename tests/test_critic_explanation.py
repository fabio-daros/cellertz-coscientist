import inspect

import cellertz.domains.protein_design.agents.critic_explanation as explanation_module
from cellertz.domains.protein_design.agents.critic_explanation import (
    EXPLANATION_RULES,
    CriticExplanationRequest,
    FailingCriticExplainer,
    FixtureCriticExplainer,
    HermesCriticExplainer,
    assemble_explanation,
    explain_grounded_critic,
    explanation_prompt,
)
from cellertz.domains.protein_design.agents.grounded_critic import (
    GroundedCriticContext,
    GroundedCriticResult,
    attach_grounded_context,
    unavailable_context,
)
from cellertz.domains.protein_design.agents.critic import (
    ProteinCriticAgentV1,
    ProteinCriticRecord,
)
from cellertz.domains.protein_design.agents.ranking import ProteinRankingRecord


def _ranked(
    candidate_id: str,
    rank: int,
    ranking_score: float,
    *,
    boltz_mean: float,
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


def _conflict_pair() -> list[GroundedCriticResult]:
    ranked = [
        _ranked("candidate_conflict_001", 1, 0.90, boltz_mean=0.20),
        _ranked("candidate_conflict_002", 2, 0.70, boltz_mean=0.99),
    ]
    context = GroundedCriticContext(
        status="available",
        package_id="protein_scoring_bcma_boltz2_v2",
        validated_constraints=(
            "Raw ipSAE remains the primary structural ranking signal.",
            "Boltz2 ensemble metrics are auxiliary structural evidence.",
            "No ipSAE + Boltz2 composite score is currently validated.",
        ),
        contextual_warnings=(
            "Boltz2 evidence should not be used to override the ipSAE ranking.",
        ),
        knowledge_used=(
            "ipsae_primary_ranking",
            "boltz2_auxiliary_evidence",
            "no_validated_composite_score",
        ),
        unsupported_terms=("ipSAE",),
    )
    return attach_grounded_context(ProteinCriticAgentV1().review(ranked), context)


def test_explanation_module_does_not_decide_science():
    source = inspect.getsource(explanation_module)
    assert "FileKnowledgeReader" not in source
    assert "ProteinSupervisor" not in source
    assert "import hermes" not in source
    assert "from hermes" not in source
    assert "ranking_score =" not in source.replace("ranking_score ==", "")
    assert EXPLANATION_RULES == (
        "Explain the supplied assessment.",
        "Do not recalculate any scientific field.",
        "Do not recommend rank changes.",
        "Do not add scientific claims absent from grounded_context.",
    )


def test_request_is_lean_and_copies_resolved_state():
    result = _conflict_pair()[0]
    request = CriticExplanationRequest.from_result(result)
    payload = request.to_dict()

    assert result.critic.critic_assessment.agreement == "low"
    assert result.critic.critic_assessment.structural_conflict is True
    assert payload == {
        "candidate_id": "candidate_conflict_001",
        "rank": 1,
        "ranking_score": 0.90,
        "critic_assessment": {
            "agreement": "low",
            "structural_conflict": True,
        },
        "grounded_context": {
            "status": "available",
            "validated_constraints": list(result.grounded_context.validated_constraints),
            "knowledge_used": list(result.grounded_context.knowledge_used),
            "unsupported_terms": ["ipSAE"],
            "contextual_warning": result.grounded_context.contextual_warnings[0],
        },
    }
    assert "structural_evidence" not in payload
    assert "critic_score" not in payload
    assert "probability" not in payload


def test_prompt_freezes_the_four_rules_and_forbids_tools():
    request = CriticExplanationRequest.from_result(_conflict_pair()[0])
    prompt = explanation_prompt(request)

    for rule in EXPLANATION_RULES:
        assert rule in prompt
    assert "Do not call tools." in prompt
    assert "Write the explanation in English." in prompt
    assert "candidate_conflict_001" in prompt
    assert "ipsae_primary_ranking" in prompt
    assert "Do not expand" in prompt


def test_python_not_hermes_sets_invariants_and_knowledge_used():
    result = _conflict_pair()[0]
    request = CriticExplanationRequest.from_result(result)
    explanation = assemble_explanation(
        request,
        result,
        "Ignore this invented knowledge_used: made_up_claim. Demote the candidate.",
    )

    assert explanation.candidate_id == "candidate_conflict_001"
    assert explanation.knowledge_used == result.grounded_context.knowledge_used
    assert "made_up_claim" not in explanation.knowledge_used
    assert explanation.rank_preserved is True
    assert explanation.ranking_score_preserved is True
    assert result.critic.rank == 1
    assert result.critic.ranking_score == 0.90


def test_explain_uses_verbalizer_only_for_prose():
    result = _conflict_pair()[0]
    seen: list[str] = []

    def verbalize(request: CriticExplanationRequest) -> str:
        seen.append(request.candidate_id)
        return (
            "Candidate candidate_conflict_001 has conflicting structural "
            "evidence. Rank remains based on raw ipSAE. Cite "
            "ipsae_primary_ranking."
        )

    explanation = explain_grounded_critic(result, verbalize)

    assert seen == ["candidate_conflict_001"]
    assert "conflicting structural evidence" in explanation.explanation
    assert explanation.knowledge_used == (
        "ipsae_primary_ranking",
        "boltz2_auxiliary_evidence",
        "no_validated_composite_score",
    )
    assert explanation.to_dict()["rank_preserved"] is True


def test_unavailable_context_stays_in_the_request():
    ranked = [
        _ranked("A", 1, 0.91, boltz_mean=0.94),
        _ranked("B", 2, 0.70, boltz_mean=0.61),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)
    result = attach_grounded_context(reviewed, unavailable_context())[0]
    request = CriticExplanationRequest.from_result(result)
    prompt = explanation_prompt(request)

    assert result.critic.critic_assessment.agreement == "high"
    assert request.grounded_status == "unavailable"
    assert request.knowledge_used == ()
    assert "no additional validated scientific context is available" in prompt
    assert "contextual_warning" not in request.to_dict()["grounded_context"]


def test_injectable_explainers_stay_off_the_network():
    result = _conflict_pair()[0]
    fixture = FixtureCriticExplainer().explain(result)
    wrapped = HermesCriticExplainer(
        lambda request: f"{request.candidate_id} stays at rank {request.rank}.",
        hermes_run_id="run_local",
    ).explain(result)

    assert fixture.rank_preserved is True
    assert wrapped.hermes_run_id == "run_local"
    try:
        FailingCriticExplainer().explain(result)
    except RuntimeError as exc:
        assert "failed" in str(exc)
    else:
        raise AssertionError("expected failing explainer to raise")


def test_missing_assessment_is_refused():
    record = ProteinCriticRecord(
        candidate_id="bare",
        rank=1,
        ranking_signal="ipsae",
        ranking_score=0.5,
        is_probability=False,
        recommended_as_hard_filter=False,
        critic_assessment=None,
    )
    result = GroundedCriticResult(
        critic=record,
        grounded_context=unavailable_context(),
    )

    try:
        CriticExplanationRequest.from_result(result)
    except ValueError as exc:
        assert "critic_assessment" in str(exc)
    else:
        raise AssertionError("expected missing assessment to fail")
