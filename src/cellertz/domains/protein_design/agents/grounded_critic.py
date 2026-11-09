"""Grounded critic context, separate from Critic Agent v1.

ProteinCriticAgentV1 stays a pure deterministic function.
This module attaches already-validated Second Brain context
after that assessment exists. It does not change rank,
ranking_score, agreement, or structural_conflict.

Only status=validated knowledge is normative. reference and
observed do not enter critic constraints. superseded is never
used as a current rule.

3F.1 does not call Hermes and does not enter the Supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from cellertz.domains.protein_design.agents.critic import ProteinCriticRecord
from cellertz.knowledge.grounding import EvidencePacket, ground_package
from cellertz.knowledge.models import KnowledgePathError
from cellertz.knowledge.reader import KnowledgeReader


GroundedStatus = Literal["available", "unavailable"]

STRUCTURAL_SCORING_PACKAGE_ID = "protein_scoring_bcma_boltz2_v2"
DOMAIN_PURPOSE_PACKAGES = {
    ("protein_design", "structural_scoring"): STRUCTURAL_SCORING_PACKAGE_ID,
}
PRIMARY_RANKING_WARNING = (
    "Boltz2 evidence should not be used to override the ipSAE ranking."
)


@dataclass(frozen=True)
class GroundedCriticContext:
    status: GroundedStatus
    package_id: str | None
    validated_constraints: tuple[str, ...]
    contextual_warnings: tuple[str, ...]
    knowledge_used: tuple[str, ...]
    unsupported_terms: tuple[str, ...]
    knowledge_context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "package_id": self.package_id,
            "validated_constraints": list(self.validated_constraints),
            "contextual_warnings": list(self.contextual_warnings),
            "knowledge_used": list(self.knowledge_used),
            "unsupported_terms": list(self.unsupported_terms),
        }
        if self.contextual_warnings:
            payload["contextual_warning"] = self.contextual_warnings[0]
        if self.knowledge_context is not None:
            payload["knowledge_context"] = dict(self.knowledge_context)
        return payload


@dataclass(frozen=True)
class GroundedCriticResult:
    critic: ProteinCriticRecord
    grounded_context: GroundedCriticContext

    def to_dict(self) -> dict[str, object]:
        payload = self.critic.to_dict()
        payload["grounded_context"] = self.grounded_context.to_dict()
        return payload


class GroundedCriticContextProvider(Protocol):
    def for_domain(
        self,
        *,
        domain: str,
        purpose: str,
    ) -> GroundedCriticContext:
        ...


def unavailable_context(package_id: str | None = None) -> GroundedCriticContext:
    return GroundedCriticContext(
        status="unavailable",
        package_id=package_id,
        validated_constraints=(),
        contextual_warnings=(),
        knowledge_used=(),
        unsupported_terms=(),
    )


def grounded_context_from_packet(packet: EvidencePacket) -> GroundedCriticContext:
    """Render a critic context from one EvidencePacket. No new science."""
    if not packet.found:
        return unavailable_context(packet.package_id)

    status_by_id = {
        item["id"]: item["status"] for item in packet.knowledge_used
    }
    if status_by_id.get(packet.package_id) != "validated":
        return unavailable_context(packet.package_id)

    validated_ids = {
        item_id
        for item_id, status in status_by_id.items()
        if status == "validated"
    }
    constraints: list[str] = []
    used: list[str] = []
    for claim in packet.claims:
        claim_id = claim.get("id")
        statement = claim.get("statement")
        if claim_id not in validated_ids or not statement:
            continue
        used.append(claim_id)
        constraints.append(statement)

    warnings: tuple[str, ...] = ()
    interpretation = packet.validated_interpretation or {}
    if interpretation.get("ranking_decision") == "raw ipSAE remains primary":
        warnings = (PRIMARY_RANKING_WARNING,)

    package_status = status_by_id.get(packet.package_id)
    return GroundedCriticContext(
        status="available",
        package_id=packet.package_id,
        validated_constraints=tuple(constraints),
        contextual_warnings=warnings,
        knowledge_used=tuple(used),
        unsupported_terms=tuple(packet.unsupported_expansions),
        knowledge_context=packet.knowledge_context(package_status),
    )


class EvidencePacketCriticContextProvider:
    """Read-only provider. CriticAgentV1 does not own this object."""

    def __init__(self, reader: KnowledgeReader) -> None:
        self._reader = reader

    def for_domain(
        self,
        *,
        domain: str,
        purpose: str,
    ) -> GroundedCriticContext:
        package_id = DOMAIN_PURPOSE_PACKAGES.get((domain, purpose))
        if package_id is None:
            return unavailable_context()
        try:
            packet = ground_package(self._reader, package_id)
        except KnowledgePathError:
            return unavailable_context(package_id)
        return grounded_context_from_packet(packet)


def attach_grounded_context(
    reviewed: list[ProteinCriticRecord],
    context: GroundedCriticContext,
) -> list[GroundedCriticResult]:
    """Attach one scientific context to already-computed critic records."""
    return [
        GroundedCriticResult(critic=record, grounded_context=context)
        for record in reviewed
    ]
