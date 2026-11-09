"""Deterministic Second Brain structural health.

This is a CI / local check. It is not an MCP tool and Hermes
does not call it. Health is structure. Canaries are meaning.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from cellertz.knowledge.models import (
    DuplicateKnowledgeId,
    InvalidKnowledgeDocument,
    KnowledgeItem,
    KnowledgePathError,
)
from cellertz.knowledge.reader import (
    FileKnowledgeReader,
    default_second_brain_root,
    parse_frontmatter,
)

INDEX_NOTES = frozenset({"index.md"})
ARCHITECTURE_PREFIX = "architecture/"
SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
EMPTY_DEFINITIONS = frozenset({None, "not_provided", "unknown"})


@dataclass(frozen=True)
class HealthIssue:
    code: str
    message: str
    item_id: str | None = None


@dataclass(frozen=True)
class SecondBrainHealth:
    ok: bool
    nodes: int
    relations: int
    broken_links: int
    validated_packages: int
    invalid_packages: int
    issues: tuple[HealthIssue, ...]

    def summary(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        lines = [
            f"Second Brain health: {status}",
            f"{self.nodes} nodes",
            f"{self.relations} relations",
            f"{self.broken_links} broken links",
            f"{self.validated_packages} validated package"
            f"{'' if self.validated_packages == 1 else 's'}",
            f"{self.invalid_packages} invalid package"
            f"{'' if self.invalid_packages == 1 else 's'}",
        ]
        if self.issues:
            lines.append("")
            for issue in self.issues:
                where = f" [{issue.item_id}]" if issue.item_id else ""
                lines.append(f"{issue.code}{where}: {issue.message}")
        return "\n".join(lines)


def _issue(issues: list[HealthIssue], code: str, message: str, item_id: str | None = None) -> None:
    issues.append(HealthIssue(code=code, message=message, item_id=item_id))


def _has_relation(item: KnowledgeItem, relation: str) -> tuple[str, ...]:
    return tuple(
        edge.target for edge in item.relations if edge.relation == relation
    )


def inspect_vault(root: Path | None = None) -> SecondBrainHealth:
    vault = Path(root) if root is not None else default_second_brain_root()
    issues: list[HealthIssue] = []

    if vault.is_dir():
        for path in sorted(vault.rglob("*.md")):
            relative = path.resolve().relative_to(vault.resolve()).as_posix()
            if relative in INDEX_NOTES or relative.startswith(ARCHITECTURE_PREFIX):
                continue
            parsed = parse_frontmatter(path.read_text(encoding="utf-8"))
            if parsed is None:
                _issue(
                    issues,
                    "invalid_frontmatter",
                    f"{relative} is missing valid frontmatter",
                )

    try:
        reader = FileKnowledgeReader(vault)
    except (DuplicateKnowledgeId, InvalidKnowledgeDocument, KnowledgePathError) as error:
        _issue(issues, "unreadable_vault", str(error))
        return SecondBrainHealth(
            ok=False,
            nodes=0,
            relations=0,
            broken_links=0,
            validated_packages=0,
            invalid_packages=1,
            issues=tuple(issues),
        )

    items = reader.list()
    known = {item.id for item in items}
    incoming: dict[str, dict[str, list[str]]] = {}
    for item in items:
        for edge in item.relations:
            incoming.setdefault(edge.target, {}).setdefault(edge.relation, []).append(
                item.id
            )
    relations = 0
    broken = 0
    validated_packages = 0
    invalid_packages = 0

    for item in items:
        relations += len(item.relations)
        for link in item.links:
            if not link.resolved:
                broken += 1
                _issue(
                    issues,
                    "broken_link",
                    f"{item.id} links to missing {link.target_id}",
                    item.id,
                )
        if item.superseded_by and item.superseded_by not in known:
            _issue(
                issues,
                "unresolved_successor",
                f"{item.id} superseded_by {item.superseded_by} does not resolve",
                item.id,
            )
        if (
            item.type == "claim"
            and item.definition not in EMPTY_DEFINITIONS
            and not item.undefined_terms
            and not any(
                heading in item.body
                for heading in ("Provenance", "Source", "Supported by")
            )
        ):
            _issue(
                issues,
                "unsupported_definition",
                f"{item.id} defines a term without a source",
                item.id,
            )

        if item.type == "validated_evidence":
            claims = _has_relation(item, "has_claim")
            if not claims:
                invalid_packages += 1
                _issue(
                    issues,
                    "package_without_claims",
                    f"{item.id} has no claims",
                    item.id,
                )
            for claim_id in claims:
                if claim_id not in known:
                    _issue(
                        issues,
                        "missing_claim",
                        f"{item.id} points at missing claim {claim_id}",
                        item.id,
                    )
            if item.status == "validated":
                validated_packages += 1
                if "Provenance" not in item.body or SHA256_RE.search(item.body) is None:
                    invalid_packages += 1
                    _issue(
                        issues,
                        "missing_provenance",
                        f"{item.id} is validated without provenance hashes",
                        item.id,
                    )
                if not item.validated_at:
                    _issue(
                        issues,
                        "missing_validated_at",
                        f"{item.id} is validated without validated_at",
                        item.id,
                    )

        if item.type == "claim":
            evidence = _has_relation(item, "supported_by") or tuple(
                incoming.get(item.id, {}).get("supports", ())
            )
            if not evidence:
                _issue(
                    issues,
                    "claim_without_evidence",
                    f"{item.id} has no supporting evidence",
                    item.id,
                )
            for evidence_id in evidence:
                if evidence_id not in known:
                    _issue(
                        issues,
                        "missing_evidence",
                        f"{item.id} points at missing evidence {evidence_id}",
                        item.id,
                    )

        if item.type == "evidence":
            datasets = _has_relation(item, "uses_dataset")
            if not datasets:
                _issue(
                    issues,
                    "evidence_without_dataset",
                    f"{item.id} has no dataset",
                    item.id,
                )
            for dataset_id in datasets:
                if dataset_id not in known:
                    _issue(
                        issues,
                        "missing_dataset",
                        f"{item.id} points at missing dataset {dataset_id}",
                        item.id,
                    )

    return SecondBrainHealth(
        ok=not issues,
        nodes=len(items),
        relations=relations,
        broken_links=broken,
        validated_packages=validated_packages,
        invalid_packages=invalid_packages,
        issues=tuple(issues),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Second Brain structural health")
    parser.add_argument(
        "--root",
        default=None,
        help="Vault path. Defaults to CELLERTZ_SECOND_BRAIN_ROOT or second_brain/",
    )
    args = parser.parse_args(argv)
    health = inspect_vault(Path(args.root) if args.root else None)
    print(health.summary())
    return 0 if health.ok else 1


if __name__ == "__main__":
    sys.exit(main())
