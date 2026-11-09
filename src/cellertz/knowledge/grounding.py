"""Compact EvidencePacket over retrieved Second Brain items.

ground_package assembles validated statements, numeric evidence, and
the already-validated interpretation. It does not invent conclusions
or expand undefined terms. It does not write to the vault.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from cellertz.knowledge.models import KnowledgeItem
from cellertz.knowledge.reader import KnowledgeReader


EVIDENCE_KEYS = {
    "global": "global",
    "bindcraft1": "bindcraft1",
    "rfd5": "rfd5",
}


@dataclass(frozen=True)
class EvidencePacket:
    found: bool
    package_id: str
    claims: tuple[dict[str, Any], ...]
    evidence: dict[str, dict[str, float]]
    validated_interpretation: dict[str, Any] | None
    knowledge_used: tuple[dict[str, str], ...]
    unsupported_expansions: tuple[str, ...]
    unavailable: tuple[str, ...]
    operational: dict[str, int]

    def canonical_bytes(self) -> bytes:
        payload: dict[str, Any] = {
            "found": self.found,
            "package_id": self.package_id,
            "claims": list(self.claims),
            "evidence": self.evidence,
            "validated_interpretation": self.validated_interpretation,
            "knowledge_used": list(self.knowledge_used),
            "unsupported_expansions": list(self.unsupported_expansions),
            "unavailable": list(self.unavailable),
        }
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def digest(self) -> tuple[str, int]:
        encoded = self.canonical_bytes()
        return hashlib.sha256(encoded).hexdigest(), len(encoded)

    def to_dict(self) -> dict[str, Any]:
        encoded = self.canonical_bytes()
        payload = json.loads(encoded)
        payload["operational"] = {
            **self.operational,
            "payload_bytes": len(encoded),
        }
        return payload

    def knowledge_context(self, package_status: str | None) -> dict[str, Any]:
        digest, payload_bytes = self.digest()
        return {
            "package_id": self.package_id,
            "package_status": package_status,
            "knowledge_used": [item["id"] for item in self.knowledge_used],
            "evidence_packet_sha256": digest,
            "retrieval_contract_version": "ground_package_v1",
            "payload_bytes": payload_bytes,
        }


def _used(item: KnowledgeItem) -> dict[str, str]:
    return {"id": item.id, "status": item.status}


def _follow(
    reader: KnowledgeReader,
    item: KnowledgeItem,
    relation: str,
) -> tuple[KnowledgeItem, ...]:
    found: list[KnowledgeItem] = []
    for edge in item.relations:
        if edge.relation != relation:
            continue
        target = reader.get(edge.target)
        if target is not None:
            found.append(target)
    return tuple(found)


def _claim_record(claim: KnowledgeItem) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": claim.id,
        "statement": claim.statement,
    }
    if claim.definition is not None:
        record["definition"] = claim.definition
    if claim.undefined_terms:
        record["undefined_terms"] = list(claim.undefined_terms)
    return record


def _evidence_metrics(item: KnowledgeItem) -> dict[str, float]:
    packet = item.packet or {}
    return {
        key: value
        for key, value in packet.items()
        if key in {"ipsae_ap", "boltz2_mean_ap", "boltz2_std_inverted_ap"}
        and isinstance(value, float)
    }


def _interpretation(package: KnowledgeItem) -> dict[str, Any] | None:
    packet = package.packet or {}
    interpretation: dict[str, Any] = {}
    if "local_boltz_gain" in packet:
        interpretation["local_boltz_gain"] = packet["local_boltz_gain"]
    if "generalizes_to_rfd5" in packet:
        interpretation["generalizes_to_rfd5"] = packet["generalizes_to_rfd5"]
    if "ranking_decision" in packet:
        interpretation["ranking_decision"] = packet["ranking_decision"]
    return interpretation or None


def ground_package(reader: KnowledgeReader, package_id: str) -> EvidencePacket:
    """Assemble the compact EvidencePacket for one validated package."""
    package = reader.get(package_id)
    if package is None:
        return EvidencePacket(
            found=False,
            package_id=package_id,
            claims=(),
            evidence={},
            validated_interpretation=None,
            knowledge_used=(),
            unsupported_expansions=(),
            unavailable=(f"{package_id} not found",),
            operational={
                "knowledge_items_used": 0,
                "number_of_knowledge_tool_calls": 1,
            },
        )

    claims = _follow(reader, package, "has_claim")
    evidence_items: list[KnowledgeItem] = []
    for claim in claims:
        evidence_items.extend(_follow(reader, claim, "supported_by"))
    for item in _follow(reader, package, "has_evidence"):
        if item.id not in {entry.id for entry in evidence_items}:
            evidence_items.append(item)
    unique_evidence = tuple({item.id: item for item in evidence_items}.values())

    datasets: list[KnowledgeItem] = []
    for item in unique_evidence:
        datasets.extend(_follow(reader, item, "uses_dataset"))
    datasets.extend(_follow(reader, package, "uses_dataset"))
    unique_datasets = tuple({item.id: item for item in datasets}.values())

    evidence: dict[str, dict[str, float]] = {}
    for item in unique_evidence:
        key = (item.packet or {}).get("packet_key")
        if not isinstance(key, str) or key not in EVIDENCE_KEYS:
            continue
        metrics = _evidence_metrics(item)
        if metrics:
            evidence[key] = metrics

    unsupported: list[str] = []
    unavailable: list[str] = []
    for claim in claims:
        if claim.definition in {None, "not_provided", "unknown"}:
            if claim.undefined_terms:
                for term in claim.undefined_terms:
                    unavailable.append(
                        "The retrieved Cellertz knowledge does not define "
                        f'the expansion of the acronym "{term}".'
                    )
                    unsupported.append(term)
            else:
                unavailable.append(
                    f"Claim {claim.id} provides no definition of its terms."
                )

    used = [_used(package)]
    used.extend(_used(claim) for claim in claims)
    used.extend(_used(item) for item in unique_evidence)
    used.extend(_used(item) for item in unique_datasets)
    seen: set[str] = set()
    knowledge_used: list[dict[str, str]] = []
    for entry in used:
        if entry["id"] in seen:
            continue
        seen.add(entry["id"])
        knowledge_used.append(entry)

    return EvidencePacket(
        found=True,
        package_id=package.id,
        claims=tuple(_claim_record(claim) for claim in claims if claim.statement),
        evidence=evidence,
        validated_interpretation=_interpretation(package),
        knowledge_used=tuple(knowledge_used),
        unsupported_expansions=tuple(dict.fromkeys(unsupported)),
        unavailable=tuple(unavailable),
        operational={
            "knowledge_items_used": len(knowledge_used),
            "number_of_knowledge_tool_calls": 1,
        },
    )
