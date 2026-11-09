"""Verbalize an already-resolved GroundedCriticResult.

Hermes may phrase the explanation. Python keeps provenance and
invariants. This module does not call Hermes, open the vault,
or change rank / ranking_score / agreement.

Rules the prompt freezes:

1. Explain the supplied assessment.
2. Do not recalculate any scientific field.
3. Do not recommend rank changes.
4. Do not add scientific claims absent from grounded_context.

This module does not import the Supervisor or call Hermes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from cellertz.domains.protein_design.agents.grounded_critic import GroundedCriticResult


EXPLANATION_RULES = (
    "Explain the supplied assessment.",
    "Do not recalculate any scientific field.",
    "Do not recommend rank changes.",
    "Do not add scientific claims absent from grounded_context.",
)


@dataclass(frozen=True)
class CriticExplanationRequest:
    candidate_id: str
    rank: int
    ranking_score: float | None
    agreement: str
    structural_conflict: bool | str
    grounded_status: str
    validated_constraints: tuple[str, ...]
    contextual_warning: str | None
    knowledge_used: tuple[str, ...]
    unsupported_terms: tuple[str, ...]

    @classmethod
    def from_result(cls, result: GroundedCriticResult) -> CriticExplanationRequest:
        assessment = result.critic.critic_assessment
        if assessment is None:
            raise ValueError("GroundedCriticResult has no critic_assessment.")
        if assessment.structural_conflict is None:
            conflict: bool | str = "unknown"
        else:
            conflict = assessment.structural_conflict
        warning = None
        if result.grounded_context.contextual_warnings:
            warning = result.grounded_context.contextual_warnings[0]
        return cls(
            candidate_id=result.critic.candidate_id,
            rank=result.critic.rank,
            ranking_score=result.critic.ranking_score,
            agreement=assessment.agreement,
            structural_conflict=conflict,
            grounded_status=result.grounded_context.status,
            validated_constraints=result.grounded_context.validated_constraints,
            contextual_warning=warning,
            knowledge_used=result.grounded_context.knowledge_used,
            unsupported_terms=result.grounded_context.unsupported_terms,
        )

    def to_dict(self) -> dict[str, Any]:
        context: dict[str, Any] = {
            "status": self.grounded_status,
            "validated_constraints": list(self.validated_constraints),
            "knowledge_used": list(self.knowledge_used),
            "unsupported_terms": list(self.unsupported_terms),
        }
        if self.contextual_warning is not None:
            context["contextual_warning"] = self.contextual_warning
        return {
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "ranking_score": self.ranking_score,
            "critic_assessment": {
                "agreement": self.agreement,
                "structural_conflict": self.structural_conflict,
            },
            "grounded_context": context,
        }


@dataclass(frozen=True)
class GroundedCriticExplanation:
    candidate_id: str
    explanation: str
    knowledge_used: tuple[str, ...]
    rank_preserved: bool
    ranking_score_preserved: bool
    hermes_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "explanation": self.explanation,
            "knowledge_used": list(self.knowledge_used),
            "rank_preserved": self.rank_preserved,
            "ranking_score_preserved": self.ranking_score_preserved,
        }
        if self.hermes_run_id is not None:
            payload["hermes_run_id"] = self.hermes_run_id
        return payload


class CriticExplainer(Protocol):
    def explain(self, result: GroundedCriticResult) -> GroundedCriticExplanation:
        ...


class FixtureCriticExplainer:
    """Deterministic prose for CI. Does not call Hermes."""

    def explain(self, result: GroundedCriticResult) -> GroundedCriticExplanation:
        return explain_grounded_critic(
            result,
            lambda request: (
                f"Candidate {request.candidate_id} has {request.agreement} "
                "structural evidence. Rank remains based on raw ipSAE."
            ),
        )


class FailingCriticExplainer:
    """Explainer that fails. The scientific pipeline must still finish."""

    def explain(self, result: GroundedCriticResult) -> GroundedCriticExplanation:
        raise RuntimeError("critic explanation failed")


class HermesCriticExplainer:
    """Inject a verbalize callable. The Supervisor never sees HTTP."""

    def __init__(
        self,
        verbalize: Callable[[CriticExplanationRequest], str],
        hermes_run_id: str | None = None,
    ) -> None:
        self._verbalize = verbalize
        self._hermes_run_id = hermes_run_id

    def explain(self, result: GroundedCriticResult) -> GroundedCriticExplanation:
        explained = explain_grounded_critic(result, self._verbalize)
        if self._hermes_run_id is None:
            return explained
        return GroundedCriticExplanation(
            candidate_id=explained.candidate_id,
            explanation=explained.explanation,
            knowledge_used=explained.knowledge_used,
            rank_preserved=explained.rank_preserved,
            ranking_score_preserved=explained.ranking_score_preserved,
            hermes_run_id=self._hermes_run_id,
        )


def explanation_prompt(request: CriticExplanationRequest) -> str:
    """Build the Hermes input. The JSON is the only scientific state."""
    rules = "\n".join(f"{index}. {rule}" for index, rule in enumerate(EXPLANATION_RULES, start=1))
    unavailable = ""
    if request.grounded_status == "unavailable":
        unavailable = (
            "grounded_context.status is unavailable. Explain only the "
            "critic assessment and say that no additional validated "
            "scientific context is available.\n"
        )
    terms = ""
    if request.unsupported_terms:
        listed = ", ".join(request.unsupported_terms)
        terms = (
            f"You may write {listed} as given. Do not expand those "
            "acronyms or invent definitions.\n"
        )
    return (
        "Verbalize the supplied GroundedCriticResult. Do not call tools. "
        "The scientific state is already resolved. Write the explanation "
        "in English.\n\n"
        f"{rules}\n"
        "Do not recommend promotion, demotion, or deprioritization. "
        "Do not invent a composite score, a probability, or experimental "
        "efficacy. Cite knowledge_used when grounded_context.status is "
        "available.\n"
        f"{unavailable}{terms}\n"
        f"{json.dumps(request.to_dict(), indent=2)}\n"
    )


def assemble_explanation(
    request: CriticExplanationRequest,
    result: GroundedCriticResult,
    explanation: str,
) -> GroundedCriticExplanation:
    """Attach Hermes prose. Invariants come from the original result."""
    return GroundedCriticExplanation(
        candidate_id=result.critic.candidate_id,
        explanation=explanation,
        knowledge_used=result.grounded_context.knowledge_used,
        rank_preserved=result.critic.rank == request.rank,
        ranking_score_preserved=result.critic.ranking_score == request.ranking_score,
    )


def explain_grounded_critic(
    result: GroundedCriticResult,
    verbalize: Callable[[CriticExplanationRequest], str],
) -> GroundedCriticExplanation:
    """Hermes phrases. Python preserves scientific state."""
    request = CriticExplanationRequest.from_result(result)
    return assemble_explanation(request, result, verbalize(request))
