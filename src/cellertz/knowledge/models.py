"""Structured knowledge items loaded from the Second Brain vault.

The reader returns these models, not raw Markdown. Obsidian remains
the human explorer over the same files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_TYPES = frozenset(
    {
        "validated_evidence",
        "claim",
        "evidence",
        "dataset",
        "reference",
        "run_context",
    }
)
ALLOWED_STATUSES = frozenset(
    {
        "reference",
        "observed",
        "validated",
        "superseded",
    }
)


@dataclass(frozen=True)
class KnowledgeLink:
    target_id: str
    resolved: bool
    relation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target_id": self.target_id,
            "resolved": self.resolved,
        }
        if self.relation is not None:
            payload["relation"] = self.relation
        return payload


@dataclass(frozen=True)
class KnowledgeRelation:
    source: str
    relation: str
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "relation": self.relation,
            "target": self.target,
        }


@dataclass(frozen=True)
class KnowledgeItem:
    id: str
    type: str
    domain: str
    status: str
    version: int
    title: str
    body: str
    links: tuple[KnowledgeLink, ...]
    source_path: str
    relations: tuple[KnowledgeRelation, ...] = ()
    statement: str | None = None
    definition: str | None = None
    undefined_terms: tuple[str, ...] = ()
    packet: dict[str, Any] | None = None
    validated_at: str | None = None
    reviewed_at: str | None = None
    superseded_by: str | None = None
    review_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "domain": self.domain,
            "status": self.status,
            "version": self.version,
            "title": self.title,
            "body": self.body,
            "links": [link.target_id for link in self.links],
            "unresolved_links": [
                link.target_id for link in self.links if not link.resolved
            ],
            "relations": [relation.to_dict() for relation in self.relations],
            "source_path": self.source_path,
        }
        if self.validated_at is not None:
            payload["validated_at"] = self.validated_at
        if self.reviewed_at is not None:
            payload["reviewed_at"] = self.reviewed_at
        if self.superseded_by is not None:
            payload["superseded_by"] = self.superseded_by
        if self.review_reason is not None:
            payload["review_reason"] = self.review_reason
        if self.statement is not None:
            payload["statement"] = self.statement
        if self.definition is not None:
            payload["definition"] = self.definition
        if self.undefined_terms:
            payload["undefined_terms"] = list(self.undefined_terms)
        if self.packet:
            payload["packet"] = dict(self.packet)
        return payload


class KnowledgeNotFound(LookupError):
    pass


class DuplicateKnowledgeId(ValueError):
    pass


class InvalidKnowledgeDocument(ValueError):
    pass


class KnowledgePathError(ValueError):
    pass
