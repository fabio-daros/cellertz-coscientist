"""Read-only KnowledgeReader over the Second Brain Markdown vault.

Humans use Obsidian. Agents use this reader. Both share second_brain/.
This module does not write, validate, supersede, or search semantically.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol

from cellertz.knowledge.models import (
    ALLOWED_STATUSES,
    ALLOWED_TYPES,
    DuplicateKnowledgeId,
    InvalidKnowledgeDocument,
    KnowledgeItem,
    KnowledgeLink,
    KnowledgePathError,
    KnowledgeRelation,
)


SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]")
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
SECTION = re.compile(
    r"^(?:#{1,6}\s+)?("
    r"validated conclusions|claims|dataset|evidence|"
    r"supported by|part of|used by|supports"
    r")\s*:?\s*$",
    re.IGNORECASE,
)
INLINE_SECTION = re.compile(
    r"^(dataset|evidence)\s*:\s*(.+)$",
    re.IGNORECASE,
)
PACKET_FLOATS = frozenset(
    {
        "ipsae_ap",
        "boltz2_mean_ap",
        "boltz2_std_inverted_ap",
    }
)
PACKET_BOOLS = frozenset({"generalizes_to_rfd5"})
PACKET_STRINGS = frozenset(
    {
        "packet_key",
        "local_boltz_gain",
        "ranking_decision",
    }
)


def extract_packet_fields(fields: dict[str, str]) -> dict[str, object]:
    packet: dict[str, object] = {}
    for key in PACKET_FLOATS:
        raw = fields.get(key)
        if raw:
            packet[key] = float(raw)
    for key in PACKET_BOOLS:
        raw = fields.get(key)
        if raw is not None and raw != "":
            packet[key] = raw.strip().lower() == "true"
    for key in PACKET_STRINGS:
        raw = fields.get(key)
        if raw:
            packet[key] = raw
    return packet


HEADING_RELATIONS = {
    "validated conclusions": "has_claim",
    "claims": "has_claim",
    "dataset": "uses_dataset",
    "evidence": "has_evidence",
    "supported by": "supported_by",
    "part of": "part_of",
    "used by": "used_by",
    "supports": "supports",
}


def default_second_brain_root() -> Path:
    return Path(os.environ.get("CELLERTZ_SECOND_BRAIN_ROOT", "second_brain"))


def is_safe_knowledge_id(item_id: str) -> bool:
    return bool(item_id) and SAFE_ID.fullmatch(item_id) is not None


def parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    match = FRONTMATTER.match(text)
    if match is None:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields, match.group(2)


def extract_wikilink_ids(text: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in WIKILINK.finditer(text))


def extract_title(body: str) -> str:
    for line in body.splitlines():
        heading = HEADING.match(line)
        if heading is not None:
            return heading.group(1).strip()
    return ""


def extract_relations(item_id: str, body: str) -> tuple[KnowledgeRelation, ...]:
    relations: list[KnowledgeRelation] = []
    current: str | None = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        section = SECTION.match(line)
        if section is not None:
            current = HEADING_RELATIONS[section.group(1).lower()]
            continue
        inline = INLINE_SECTION.match(line)
        if inline is not None:
            relation = HEADING_RELATIONS[inline.group(1).lower()]
            for target in extract_wikilink_ids(inline.group(2)):
                relations.append(
                    KnowledgeRelation(
                        source=item_id,
                        relation=relation,
                        target=target,
                    )
                )
            continue
        if current is None:
            continue
        for target in extract_wikilink_ids(line):
            relations.append(
                KnowledgeRelation(
                    source=item_id,
                    relation=current,
                    target=target,
                )
            )
    return tuple(relations)


class KnowledgeReader(Protocol):
    def get(self, item_id: str) -> KnowledgeItem | None:
        ...

    def get_claim(self, item_id: str) -> KnowledgeItem | None:
        ...

    def list(
        self,
        *,
        domain: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> tuple[KnowledgeItem, ...]:
        ...

    def neighbors(self, item_id: str) -> tuple[KnowledgeRelation, ...]:
        ...


class FileKnowledgeReader:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            Path(root) if root is not None else default_second_brain_root()
        ).resolve()
        self._items: dict[str, KnowledgeItem] = {}
        self._index()

    def _assert_in_vault(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise KnowledgePathError(
                f"Path is outside the Second Brain vault: {path}"
            )
        return resolved

    def _require_safe_id(self, item_id: str) -> str:
        if not is_safe_knowledge_id(item_id):
            raise KnowledgePathError(
                f"Knowledge id is outside the vault namespace: {item_id!r}"
            )
        return item_id

    def _index(self) -> None:
        if not self.root.is_dir():
            return
        for path in sorted(self.root.rglob("*.md")):
            self._assert_in_vault(path)
            parsed = parse_frontmatter(path.read_text(encoding="utf-8"))
            if parsed is None:
                continue
            fields, body = parsed
            item = self._item_from_document(path, fields, body)
            if item.id in self._items:
                raise DuplicateKnowledgeId(
                    f"Duplicate knowledge id {item.id!r} in "
                    f"{self._items[item.id].source_path} and {item.source_path}"
                )
            self._items[item.id] = item
        self._resolve_links()

    def _item_from_document(
        self,
        path: Path,
        fields: dict[str, str],
        body: str,
    ) -> KnowledgeItem:
        missing = [
            key
            for key in ("id", "type", "domain", "status", "version")
            if not fields.get(key)
        ]
        if missing:
            raise InvalidKnowledgeDocument(
                f"Missing fields {missing} in {path}"
            )
        item_id = fields["id"]
        if not is_safe_knowledge_id(item_id):
            raise KnowledgePathError(
                f"Unsafe knowledge id {item_id!r} in {path}"
            )
        item_type = fields["type"]
        status = fields["status"]
        if item_type not in ALLOWED_TYPES:
            raise InvalidKnowledgeDocument(
                f"Invalid type {item_type!r} in {path}"
            )
        if status not in ALLOWED_STATUSES:
            raise InvalidKnowledgeDocument(
                f"Invalid status {status!r} in {path}"
            )
        try:
            version = int(fields["version"])
        except ValueError as error:
            raise InvalidKnowledgeDocument(
                f"Invalid version {fields['version']!r} in {path}"
            ) from error
        statement = fields.get("statement") or None
        if item_type == "claim" and statement is None:
            raise InvalidKnowledgeDocument(
                f"Claim {item_id!r} is missing statement in {path}"
            )
        definition = fields.get("definition") or None
        if item_type == "claim" and definition is None:
            definition = "not_provided"
        undefined_terms = tuple(
            term
            for term in (
                part.strip()
                for part in fields.get("undefined_terms", "").split(",")
            )
            if term
        )
        relations = extract_relations(item_id, body)
        relation_targets = {
            (relation.relation, relation.target) for relation in relations
        }
        links: list[KnowledgeLink] = []
        seen: set[str] = set()
        for target in extract_wikilink_ids(body):
            if target in seen:
                continue
            seen.add(target)
            matched = next(
                (
                    relation
                    for relation, candidate in relation_targets
                    if candidate == target
                ),
                None,
            )
            links.append(
                KnowledgeLink(
                    target_id=target,
                    resolved=False,
                    relation=matched,
                )
            )
        source_path = path.resolve().relative_to(self.root).as_posix()
        return KnowledgeItem(
            id=item_id,
            type=item_type,
            domain=fields["domain"],
            status=status,
            version=version,
            title=extract_title(body),
            body=body.strip() + "\n",
            links=tuple(links),
            source_path=source_path,
            relations=relations,
            statement=statement,
            definition=definition,
            undefined_terms=undefined_terms,
            packet=extract_packet_fields(fields) or None,
            validated_at=fields.get("validated_at") or None,
            reviewed_at=fields.get("reviewed_at") or None,
            superseded_by=fields.get("superseded_by") or None,
            review_reason=fields.get("review_reason") or None,
        )

    def _resolve_links(self) -> None:
        known = set(self._items)
        resolved_items: dict[str, KnowledgeItem] = {}
        for item in self._items.values():
            links = tuple(
                KnowledgeLink(
                    target_id=link.target_id,
                    resolved=link.target_id in known,
                    relation=link.relation,
                )
                for link in item.links
            )
            resolved_items[item.id] = KnowledgeItem(
                id=item.id,
                type=item.type,
                domain=item.domain,
                status=item.status,
                version=item.version,
                title=item.title,
                body=item.body,
                links=links,
                source_path=item.source_path,
                relations=item.relations,
                statement=item.statement,
                definition=item.definition,
                undefined_terms=item.undefined_terms,
                packet=item.packet,
                validated_at=item.validated_at,
                reviewed_at=item.reviewed_at,
                superseded_by=item.superseded_by,
                review_reason=item.review_reason,
            )
        self._items = resolved_items

    def get(self, item_id: str) -> KnowledgeItem | None:
        self._require_safe_id(item_id)
        return self._items.get(item_id)

    def get_claim(self, item_id: str) -> KnowledgeItem | None:
        item = self.get(item_id)
        if item is None or item.type != "claim":
            return None
        return item

    def list(
        self,
        *,
        domain: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> tuple[KnowledgeItem, ...]:
        items = self._items.values()
        if domain is not None:
            items = (item for item in items if item.domain == domain)
        if type is not None:
            items = (item for item in items if item.type == type)
        if status is not None:
            items = (item for item in items if item.status == status)
        return tuple(sorted(items, key=lambda item: item.id))

    def neighbors(self, item_id: str) -> tuple[KnowledgeRelation, ...]:
        self._require_safe_id(item_id)
        if item_id not in self._items:
            return ()
        found: list[KnowledgeRelation] = []
        seen: set[tuple[str, str, str]] = set()
        for item in self._items.values():
            for relation in item.relations:
                if relation.source != item_id and relation.target != item_id:
                    continue
                key = (relation.source, relation.relation, relation.target)
                if key in seen:
                    continue
                seen.add(key)
                found.append(relation)
        return tuple(
            sorted(
                found,
                key=lambda relation: (
                    relation.source,
                    relation.relation,
                    relation.target,
                ),
            )
        )
