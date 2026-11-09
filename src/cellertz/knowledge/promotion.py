"""Explicit, developer-operated promotion into semantic knowledge.

A human supplies an approved promotion bundle. This module checks
that bundle against an observed ScientificRunMemory and the Second
Brain contract, then materializes it only when --apply is requested.

It does not invent claims, call a model, or treat an episodic run
as validated science.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Protocol

from cellertz.knowledge.health import inspect_vault
from cellertz.knowledge.models import DuplicateKnowledgeId
from cellertz.knowledge.reader import (
    FileKnowledgeReader,
    default_second_brain_root,
    parse_frontmatter,
)
from cellertz.second_brain.episodic.models import ScientificRunMemoryError

SCHEMA = "scientific_knowledge_promotion_v1"
RECEIPT_SCHEMA = "scientific_knowledge_promotion_receipt_v1"
_LOCK_NAME = ".cellertz-semantic-promotion.lock"
_RECEIPT_KEYS = frozenset(
    {
        "applied_at",
        "documents",
        "promotion_digest",
        "promotion_id",
        "result",
        "review_decision",
        "reviewed_at",
        "reviewed_by",
        "schema",
        "source_memory_digest",
        "source_run_id",
    }
)
DOCUMENT_ROOTS = frozenset({"packages", "claims", "evidence", "datasets"})
DIGEST_RE = r"^[0-9a-f]{64}$"
_SAFE_ID = r"^[A-Za-z0-9._-]+$"
_LINK_HEALTH = frozenset(
    {
        "broken_link",
        "missing_claim",
        "missing_evidence",
        "missing_dataset",
        "claim_without_evidence",
        "evidence_without_dataset",
        "package_without_claims",
    }
)
_SAFE_MEMORY_ERRORS = frozenset(
    {
        "databricks_config_missing",
        "databricks_sql_connect_failed",
        "databricks_sql_failed",
        "databricks_sql_optional_dependency_missing",
        "scientific_memory_digest_mismatch",
        "scientific_memory_duplicate_run_id",
        "scientific_memory_index_mismatch",
        "scientific_memory_malformed",
        "scientific_memory_unavailable",
        "scientific_memory_unreadable",
        "unknown_scientific_memory_backend",
    }
)


class _MemoryReader(Protocol):
    def get_run(self, run_id: str) -> Any: ...


class _HashMismatch(Exception):
    """Copied promotion bytes do not match the reviewed digest."""


@dataclass(frozen=True)
class _Document:
    path: str
    sha256: str


@dataclass(frozen=True)
class _Bundle:
    promotion_id: str
    run_id: str
    memory_digest: str
    reviewed_by: str
    reviewed_at: str
    reason: str
    documents: tuple[_Document, ...]
    promotion_digest: str
    root: Path
    manifest_bytes: bytes


def promote_scientific_knowledge(
    bundle: Path,
    *,
    apply: bool = False,
    vault_root: Path | None = None,
    memory_reader: _MemoryReader | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Inspect a promotion bundle. Write only when apply is true."""
    vault = (
        Path(vault_root) if vault_root is not None else default_second_brain_root()
    )
    loaded = _load_bundle(Path(bundle))
    if isinstance(loaded, str):
        return _report(error=loaded)
    source_error = _verify_source(loaded, memory_reader)
    if source_error is not None:
        return _report(error=source_error, bundle=loaded)
    preflight_error = _preflight(vault, loaded)
    if preflight_error is not None:
        return _report(error=preflight_error, bundle=loaded)
    destination = _relative_destination(loaded.promotion_id)
    if not apply:
        return _report(
            bundle=loaded,
            applied=False,
            destination=destination,
        )
    try:
        lock = _acquire_promotion_lock(vault)
    except Exception:  # noqa: BLE001 — lock errors stay a stable code
        return _report(
            error="promotion_lock_failed",
            bundle=loaded,
            destination=destination,
        )
    try:
        return _apply_locked(vault, loaded, destination, clock or _now)
    finally:
        _release_promotion_lock(lock)


def _load_bundle(root: Path) -> _Bundle | str:
    if not root.is_dir() or root.is_symlink():
        return "invalid_promotion_bundle"
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return "invalid_promotion_bundle"
    try:
        manifest_bytes = manifest_path.read_bytes()
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "invalid_promotion_bundle"
    if not isinstance(payload, dict):
        return "invalid_promotion_bundle"
    if payload.get("schema") != SCHEMA:
        return "invalid_promotion_schema"
    promotion_id = payload.get("promotion_id")
    if not _safe_id(promotion_id):
        return "invalid_promotion_id"
    source = payload.get("source")
    if not isinstance(source, dict):
        return "missing_source_identity"
    run_id = source.get("run_id")
    memory_digest = source.get("memory_digest")
    if not _safe_id(run_id) or not isinstance(memory_digest, str):
        return "missing_source_identity"
    if not _digest(memory_digest):
        return "invalid_memory_digest"
    review = payload.get("review")
    if not isinstance(review, dict) or review.get("decision") != "approved":
        return "invalid_review_decision"
    reviewed_by = review.get("reviewed_by")
    reason = review.get("reason")
    reviewed_at = review.get("reviewed_at")
    if not isinstance(reviewed_by, str) or not reviewed_by.strip():
        return "missing_reviewer"
    if reviewed_by != reviewed_by.strip():
        return "invalid_reviewer"
    if not isinstance(reason, str) or not reason.strip():
        return "missing_review_reason"
    if reason != reason.strip():
        return "invalid_review_reason"
    if not _aware_timestamp(reviewed_at):
        return "invalid_reviewed_at"
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        return "invalid_promotion_documents"
    parsed: list[_Document] = []
    seen_paths: set[str] = set()
    for entry in documents:
        if not isinstance(entry, dict):
            return "invalid_promotion_documents"
        relative = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str):
            return "invalid_promotion_documents"
        if relative in seen_paths:
            return "duplicate_document_path"
        seen_paths.add(relative)
        safe = _safe_document_path(relative)
        if safe is None:
            return "unsafe_document_path"
        if not _digest(digest):
            return "document_hash_mismatch"
        target = root / safe
        if target.is_symlink() or _has_symlink_parent(root, target):
            return "symlink_rejected"
        if not target.is_file() or not _contained(root, target):
            return "document_outside_bundle"
        try:
            actual = _sha256(target)
        except OSError:
            return "document_hash_mismatch"
        if actual != digest:
            return "document_hash_mismatch"
        parsed.append(_Document(path=safe.as_posix(), sha256=digest))
    extra = _undeclared_markdown(root, seen_paths)
    if extra:
        return "undeclared_document"
    ordered = tuple(sorted(parsed, key=lambda item: item.path))
    review_error = _review_documents(root, promotion_id, run_id, memory_digest, review, ordered)
    if review_error is not None:
        return review_error
    return _Bundle(
        promotion_id=promotion_id,
        run_id=run_id,
        memory_digest=memory_digest,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        reason=reason,
        documents=ordered,
        promotion_digest=_promotion_digest(
            promotion_id=promotion_id,
            run_id=run_id,
            memory_digest=memory_digest,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            reason=reason,
            documents=ordered,
        ),
        root=root.resolve(),
        manifest_bytes=manifest_bytes,
    )


def _review_documents(
    root: Path,
    promotion_id: str,
    run_id: str,
    memory_digest: str,
    review: Mapping[str, Any],
    documents: tuple[_Document, ...],
) -> str | None:
    found = False
    for document in documents:
        try:
            text = (root / document.path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "invalid_promotion_documents"
        parsed = parse_frontmatter(text)
        if parsed is None:
            return "invalid_promotion_documents"
        fields, _body = parsed
        if fields.get("type") == "validated_evidence" and fields.get("status") == "validated":
            found = True
            expected = {
                "promotion_id": promotion_id,
                "source_run_id": run_id,
                "source_memory_digest": memory_digest,
                "validated_at": review["reviewed_at"],
                "reviewed_at": review["reviewed_at"],
                "reviewed_by": review["reviewed_by"],
                "review_reason": review["reason"],
            }
            for key, value in expected.items():
                if fields.get(key) != value:
                    return "promotion_frontmatter_mismatch"
    if not found:
        return "missing_validated_evidence"
    return None


def _verify_source(bundle: _Bundle, memory_reader: _MemoryReader | None) -> str | None:
    try:
        reader = memory_reader if memory_reader is not None else _default_reader()
        memory = reader.get_run(bundle.run_id)
    except ScientificRunMemoryError as exc:
        return _public_memory_error(exc)
    except Exception:  # noqa: BLE001 — hide backend text at this boundary
        return "scientific_memory_unavailable"
    if memory is None:
        return "source_run_not_found"
    digest = getattr(memory, "memory_digest", None)
    if digest != bundle.memory_digest:
        return "source_memory_digest_mismatch"
    return None


def _default_reader() -> _MemoryReader:
    from cellertz.second_brain.episodic.factory import (
        create_scientific_run_memory_store,
    )

    return create_scientific_run_memory_store()


def _preflight(vault: Path, bundle: _Bundle) -> str | None:
    if _contains_symlink(vault):
        return "semantic_vault_symlink_rejected"
    existing_ids = _existing_ids(vault)
    with tempfile.TemporaryDirectory(prefix="cellertz-promotion-") as temporary:
        staged = Path(temporary) / "vault"
        copy_error = _copy_vault(vault, staged)
        if copy_error is not None:
            return copy_error
        destination = staged / "promotions" / bundle.promotion_id
        if destination.exists():
            receipt = _read_receipt(destination)
            if (
                receipt is not None
                and receipt.get("promotion_digest") == bundle.promotion_digest
            ):
                return _installed_integrity(staged, bundle, destination)
            return "promotion_conflict"
        for document in bundle.documents:
            target = destination / document.path
            if target.exists():
                return "existing_semantic_file"
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bundle.root / document.path, target, follow_symlinks=False)
            except OSError:
                return "document_hash_mismatch"
        if not _copied_hashes_match(destination, bundle.documents):
            return "document_hash_mismatch"
        try:
            reader = FileKnowledgeReader(staged)
        except DuplicateKnowledgeId:
            return "duplicate_knowledge_id"
        except Exception:  # noqa: BLE001 — vault errors become a stable code
            return "semantic_health_failed"
        promoted = _promoted_packages(reader, bundle.promotion_id)
        if not promoted:
            return "missing_validated_evidence"
        for item in reader.list():
            if item.id in existing_ids and item.source_path.startswith(
                f"promotions/{bundle.promotion_id}/"
            ):
                return "duplicate_knowledge_id"
        for item in promoted:
            relations = {edge.relation for edge in item.relations}
            if "has_claim" not in relations or "has_evidence" not in relations:
                return "promotion_link_missing"
        health = inspect_vault(staged)
        if not health.ok:
            codes = {issue.code for issue in health.issues}
            if codes & _LINK_HEALTH:
                return "broken_knowledge_link"
            return "semantic_health_failed"
    return None


def _apply_locked(
    vault: Path,
    bundle: _Bundle,
    destination: str,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    preflight_error = _preflight(vault, bundle)
    if preflight_error is not None:
        return _report(error=preflight_error, bundle=bundle, destination=destination)
    target = vault / destination
    if target.exists():
        receipt = _read_receipt(target)
        if receipt is not None and receipt.get("promotion_digest") == bundle.promotion_digest:
            if _installed_integrity(vault, bundle, target) is not None:
                return _report(
                    error="promotion_integrity_failed",
                    bundle=bundle,
                    destination=destination,
                )
            return _report(
                bundle=bundle,
                applied=False,
                idempotent=True,
                destination=destination,
            )
        return _report(error="promotion_conflict", bundle=bundle, destination=destination)
    applied_at = _format_timestamp(clock())
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{bundle.promotion_id}.", dir=parent))
    except Exception:  # noqa: BLE001 — do not leak filesystem text
        return _report(error="promotion_apply_failed", bundle=bundle, destination=destination)
    try:
        for document in bundle.documents:
            output = temporary / document.path
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(bundle.root / document.path, output, follow_symlinks=False)
            except OSError as exc:
                raise _HashMismatch() from exc
        receipt_body = _receipt(bundle, applied_at)
        (temporary / "manifest.json").write_bytes(bundle.manifest_bytes)
        (temporary / "promotion_receipt.json").write_text(
            json.dumps(receipt_body, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        if not _copied_hashes_match(temporary, bundle.documents):
            raise _HashMismatch()
        os.rename(temporary, target)
    except _HashMismatch:
        shutil.rmtree(temporary, ignore_errors=True)
        return _report(
            error="document_hash_mismatch",
            bundle=bundle,
            destination=destination,
        )
    except Exception:  # noqa: BLE001 — do not leak filesystem or backend text
        shutil.rmtree(temporary, ignore_errors=True)
        return _report(error="promotion_apply_failed", bundle=bundle, destination=destination)
    if not _final_vault_ok(vault):
        final_error = "semantic_health_failed"
    elif _installed_integrity(vault, bundle, target) is not None:
        final_error = "promotion_integrity_failed"
    else:
        return _report(bundle=bundle, applied=True, destination=destination)
    if _rollback_installed(target) is not None or target.exists():
        return _report(
            error="promotion_rollback_failed",
            bundle=bundle,
            applied=target.exists(),
            destination=destination,
        )
    return _report(
        error=final_error,
        bundle=bundle,
        applied=False,
        destination=destination,
    )


def _final_vault_ok(vault: Path) -> bool:
    try:
        FileKnowledgeReader(vault)
        return inspect_vault(vault).ok
    except Exception:  # noqa: BLE001 — post-apply vault errors stay coded
        return False


def _rollback_installed(target: Path) -> str | None:
    """Remove one promotion directory installed by this apply."""
    if not target.exists() and not target.is_symlink():
        return None
    vault_root = target.parent.parent
    withdrawn: Path | None = None
    try:
        withdrawn = Path(
            tempfile.mkdtemp(
                prefix=f".{target.name}.withdrawn.",
                dir=vault_root.parent,
            )
        )
        shutil.rmtree(withdrawn)
        os.rename(target, withdrawn)
        shutil.rmtree(withdrawn)
    except Exception:  # noqa: BLE001 — rollback failure is a stable code
        return "promotion_rollback_failed"
    if target.exists() or target.is_symlink() or withdrawn.exists():
        return "promotion_rollback_failed"
    return None


def _installed_integrity(vault: Path, bundle: _Bundle, target: Path) -> str | None:
    manifest_path = target / "manifest.json"
    receipt_path = target / "promotion_receipt.json"
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or not receipt_path.is_file()
        or receipt_path.is_symlink()
    ):
        return "promotion_integrity_failed"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "promotion_integrity_failed"
    if not isinstance(manifest, dict) or not isinstance(receipt, dict):
        return "promotion_integrity_failed"
    if _canonical_json(manifest) != _canonical_json(json.loads(bundle.manifest_bytes)):
        return "promotion_integrity_failed"
    if set(receipt) != _RECEIPT_KEYS:
        return "promotion_integrity_failed"
    if receipt.get("result") != "applied":
        return "promotion_integrity_failed"
    if not _aware_timestamp(receipt.get("applied_at")):
        return "promotion_integrity_failed"
    if manifest.get("schema") != SCHEMA:
        return "promotion_integrity_failed"
    if manifest.get("promotion_id") != bundle.promotion_id:
        return "promotion_integrity_failed"
    source = manifest.get("source")
    review = manifest.get("review")
    if not isinstance(source, dict) or not isinstance(review, dict):
        return "promotion_integrity_failed"
    if source.get("run_id") != bundle.run_id:
        return "promotion_integrity_failed"
    if source.get("memory_digest") != bundle.memory_digest:
        return "promotion_integrity_failed"
    if review.get("decision") != "approved":
        return "promotion_integrity_failed"
    if review.get("reviewed_by") != bundle.reviewed_by:
        return "promotion_integrity_failed"
    if review.get("reviewed_at") != bundle.reviewed_at:
        return "promotion_integrity_failed"
    if review.get("reason") != bundle.reason:
        return "promotion_integrity_failed"
    if receipt.get("promotion_id") != bundle.promotion_id:
        return "promotion_integrity_failed"
    if receipt.get("promotion_digest") != bundle.promotion_digest:
        return "promotion_integrity_failed"
    if receipt.get("schema") != RECEIPT_SCHEMA:
        return "promotion_integrity_failed"
    if receipt.get("reviewed_by") != bundle.reviewed_by:
        return "promotion_integrity_failed"
    if receipt.get("reviewed_at") != bundle.reviewed_at:
        return "promotion_integrity_failed"
    if receipt.get("review_decision") != "approved":
        return "promotion_integrity_failed"
    if receipt.get("source_run_id") != bundle.run_id:
        return "promotion_integrity_failed"
    if receipt.get("source_memory_digest") != bundle.memory_digest:
        return "promotion_integrity_failed"
    expected = {document.path: document.sha256 for document in bundle.documents}
    if _document_map(manifest.get("documents")) != expected:
        return "promotion_integrity_failed"
    if _document_map(receipt.get("documents")) != expected:
        return "promotion_integrity_failed"
    for relative, digest in expected.items():
        path = target / relative
        if not path.is_file() or path.is_symlink():
            return "promotion_integrity_failed"
        try:
            actual = _sha256(path)
        except OSError:
            return "promotion_integrity_failed"
        if actual != digest:
            return "promotion_integrity_failed"
    if _undeclared_markdown(target, set(expected)):
        return "promotion_integrity_failed"
    try:
        FileKnowledgeReader(vault)
        healthy = inspect_vault(vault).ok
    except Exception:  # noqa: BLE001 — replay integrity stays a stable code
        return "promotion_integrity_failed"
    if not healthy:
        return "promotion_integrity_failed"
    return None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _copied_hashes_match(root: Path, documents: tuple[_Document, ...]) -> bool:
    for document in documents:
        path = root / document.path
        if not path.is_file() or path.is_symlink():
            return False
        try:
            actual = _sha256(path)
        except OSError:
            return False
        if actual != document.sha256:
            return False
    return True


def _document_map(value: object) -> dict[str, str] | None:
    if not isinstance(value, list):
        return None
    found: dict[str, str] = {}
    for entry in value:
        if not isinstance(entry, dict):
            return None
        relative = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str):
            return None
        if relative in found:
            return None
        found[relative] = digest
    return found


def _acquire_promotion_lock(vault: Path) -> IO[str]:
    path = _lock_path(vault)
    handle: IO[str] | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+", encoding="utf-8")  # noqa: SIM115 — held until unlock
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except Exception:
        if handle is not None:
            handle.close()
        raise
    return handle


def _release_promotion_lock(handle: IO[str]) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:  # noqa: BLE001 — release must not replace the apply result
        handle.close()
        return
    handle.close()


def _lock_path(vault: Path) -> Path:
    return vault.resolve().parent / _LOCK_NAME


def _receipt(bundle: _Bundle, applied_at: str) -> dict[str, Any]:
    return {
        "applied_at": applied_at,
        "documents": [
            {"path": document.path, "sha256": document.sha256}
            for document in bundle.documents
        ],
        "promotion_digest": bundle.promotion_digest,
        "promotion_id": bundle.promotion_id,
        "result": "applied",
        "review_decision": "approved",
        "reviewed_at": bundle.reviewed_at,
        "reviewed_by": bundle.reviewed_by,
        "schema": RECEIPT_SCHEMA,
        "source_memory_digest": bundle.memory_digest,
        "source_run_id": bundle.run_id,
    }


def _promotion_digest(
    *,
    promotion_id: str,
    run_id: str,
    memory_digest: str,
    reviewed_by: str,
    reviewed_at: str,
    reason: str,
    documents: tuple[_Document, ...],
) -> str:
    payload = {
        "documents": [
            {"path": document.path, "sha256": document.sha256}
            for document in sorted(documents, key=lambda item: item.path)
        ],
        "promotion_id": promotion_id,
        "review_decision": "approved",
        "review_reason": reason,
        "reviewed_at": reviewed_at,
        "reviewed_by": reviewed_by,
        "schema": SCHEMA,
        "source_memory_digest": memory_digest,
        "source_run_id": run_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _report(
    *,
    bundle: _Bundle | None = None,
    applied: bool = False,
    idempotent: bool = False,
    destination: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": error is None,
        "applied": applied,
        "idempotent": idempotent,
        "promotion_id": None if bundle is None else bundle.promotion_id,
        "source_run_id": None if bundle is None else bundle.run_id,
        "source_memory_digest": None if bundle is None else bundle.memory_digest,
        "promotion_digest": None if bundle is None else bundle.promotion_digest,
        "document_count": 0 if bundle is None else len(bundle.documents),
        "destination": destination,
        "error": error,
    }


def _existing_ids(vault: Path) -> set[str]:
    if not vault.is_dir():
        return set()
    try:
        reader = FileKnowledgeReader(vault)
    except Exception:  # noqa: BLE001 — an unreadable vault fails closed later
        return set()
    return {item.id for item in reader.list()}


def _promoted_packages(reader: FileKnowledgeReader, promotion_id: str) -> tuple[Any, ...]:
    prefix = f"promotions/{promotion_id}/"
    return tuple(
        item
        for item in reader.list()
        if item.source_path.startswith(prefix)
        and item.type == "validated_evidence"
        and item.status == "validated"
    )


def _contains_symlink(root: Path) -> bool:
    if not root.exists():
        return False
    if root.is_symlink():
        return True
    if not root.is_dir():
        return False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        for name in (*dirnames, *filenames):
            if (current / name).is_symlink():
                return True
    return False


def _copy_vault(source: Path, destination: Path) -> str | None:
    if _contains_symlink(source):
        return "semantic_vault_symlink_rejected"
    destination.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return None
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        current = Path(dirpath)
        relative_dir = current.relative_to(source)
        (destination / relative_dir).mkdir(parents=True, exist_ok=True)
        for name in dirnames:
            if (current / name).is_symlink():
                return "semantic_vault_symlink_rejected"
        for name in filenames:
            path = current / name
            if path.is_symlink():
                return "semantic_vault_symlink_rejected"
            output = destination / relative_dir / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output, follow_symlinks=False)
    return None


def _relative_destination(promotion_id: str) -> str:
    return f"promotions/{promotion_id}"


def _read_receipt(directory: Path) -> dict[str, Any] | None:
    path = directory / "promotion_receipt.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _undeclared_markdown(root: Path, declared: set[str]) -> bool:
    for path in root.rglob("*.md"):
        if path.is_symlink() or not path.is_file():
            return True
        if not _contained(root, path):
            return True
        relative = path.relative_to(root).as_posix()
        if relative not in declared:
            return True
    return False


def _safe_document_path(value: str) -> Path | None:
    if not value or value.startswith(("/", "\\")) or "\\" in value:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.parts[0] not in DOCUMENT_ROOTS:
        return None
    if path.suffix != ".md" or len(path.parts) < 2:
        return None
    return path


def _has_symlink_parent(root: Path, target: Path) -> bool:
    current = target.parent
    root_resolved = root.resolve()
    while current != root_resolved and current != current.parent:
        if current.is_symlink():
            return True
        if current == root:
            break
        current = current.parent
    return False


def _contained(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _safe_id(value: object) -> bool:
    if not isinstance(value, str) or value in {".", ".."}:
        return False
    return re.fullmatch(_SAFE_ID, value) is not None


def _digest(value: str) -> bool:
    return re.fullmatch(DIGEST_RE, value) is not None


def _aware_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _public_memory_error(exc: ScientificRunMemoryError) -> str:
    head = str(exc).split(":", 1)[0].strip()
    if head in _SAFE_MEMORY_ERRORS:
        return head
    return "scientific_memory_unavailable"


def _now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()
