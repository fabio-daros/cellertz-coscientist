"""Thin MCP adapter over FileKnowledgeReader.

This module must not parse Markdown. Retrieval stays read-only and
is independent of CELLERTZ_MCP_MUTATION_MODE.
"""

from __future__ import annotations

from typing import Any

from cellertz.knowledge.grounding import EvidencePacket, ground_package
from cellertz.knowledge.models import KnowledgePathError
from cellertz.knowledge.reader import (
    FileKnowledgeReader,
    KnowledgeReader,
    default_second_brain_root,
)


_reader: KnowledgeReader | None = None


def get_knowledge_reader() -> KnowledgeReader:
    global _reader
    if _reader is None:
        _reader = FileKnowledgeReader(default_second_brain_root())
    return _reader


def set_knowledge_reader(reader: KnowledgeReader | None) -> None:
    global _reader
    _reader = reader


def _resolved(reader: KnowledgeReader, target_id: str) -> bool:
    try:
        return reader.get(target_id) is not None
    except KnowledgePathError:
        return False


def knowledge_get(item_id: str) -> dict[str, Any]:
    """Return one knowledge item and its outgoing relations. Read-only."""
    reader = get_knowledge_reader()
    try:
        item = reader.get(item_id)
    except KnowledgePathError:
        return {
            "found": False,
            "id": item_id,
            "error": "path_outside_vault",
        }
    if item is None:
        return {"found": False, "id": item_id}
    summary: dict[str, object] = {
        "id": item.id,
        "type": item.type,
        "domain": item.domain,
        "status": item.status,
        "version": item.version,
        "title": item.title,
    }
    if item.statement is not None:
        summary["statement"] = item.statement
    if item.definition is not None:
        summary["definition"] = item.definition
    if item.undefined_terms:
        summary["undefined_terms"] = list(item.undefined_terms)
    return {
        "found": True,
        "item": summary,
        "relations": [
            {
                "relation": relation.relation,
                "target": relation.target,
                "resolved": _resolved(reader, relation.target),
            }
            for relation in item.relations
        ],
    }


def knowledge_neighbors(item_id: str) -> dict[str, Any]:
    """Return graph neighbors for one knowledge item. Read-only."""
    reader = get_knowledge_reader()
    try:
        item = reader.get(item_id)
        relations = reader.neighbors(item_id)
    except KnowledgePathError:
        return {
            "found": False,
            "id": item_id,
            "error": "path_outside_vault",
        }
    if item is None:
        return {"found": False, "id": item_id}
    return {
        "found": True,
        "id": item.id,
        "relations": [
            {
                "source": relation.source,
                "relation": relation.relation,
                "target": relation.target,
                "resolved": _resolved(
                    reader,
                    relation.target
                    if relation.source == item_id
                    else relation.source,
                ),
            }
            for relation in relations
        ],
    }


def knowledge_ground_package(package_id: str) -> dict[str, Any]:
    """Return the compact EvidencePacket for one package. Read-only."""
    reader = get_knowledge_reader()
    try:
        return ground_package(reader, package_id).to_dict()
    except KnowledgePathError:
        return EvidencePacket(
            found=False,
            package_id=package_id,
            claims=(),
            evidence={},
            validated_interpretation=None,
            knowledge_used=(),
            unsupported_expansions=(),
            unavailable=(),
            operational={
                "knowledge_items_used": 0,
                "number_of_knowledge_tool_calls": 1,
            },
        ).to_dict() | {"error": "path_outside_vault"}
