from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

import cellertz.knowledge.promotion as promotion_module
from cellertz.knowledge.health import HealthIssue, SecondBrainHealth, inspect_vault
from cellertz.knowledge.promotion import promote_scientific_knowledge
from cellertz.knowledge.reader import FileKnowledgeReader
from tools.validation.promote_scientific_knowledge import main

ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT / "second_brain"
RUN_ID = "synthetic_run_alpha"
DIGEST = "a" * 64
REVIEWED_AT = "2026-09-23T15:00:00+00:00"
REVIEWER = "scientist.ada"
REASON = "Synthetic review for the promotion contract."
SECRET = "super-secret-databricks-token"


class _Memory:
    def __init__(self, run_id: str, memory_digest: str) -> None:
        self.run_id = run_id
        self.memory_digest = memory_digest


class _Reader:
    def __init__(self, memory: _Memory | None) -> None:
        self.memory = memory
        self.calls: list[str] = []

    def get_run(self, run_id: str) -> _Memory | None:
        self.calls.append("get_run")
        if self.memory is None or self.memory.run_id != run_id:
            return None
        return self.memory

    def put(self, memory: object) -> None:
        raise AssertionError("put")

    def ensure_schema(self) -> None:
        raise AssertionError("ensure_schema")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            found[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return found


def _package(
    promotion_id: str,
    *,
    claim: str,
    evidence: str,
    reason: str = REASON,
    item_id: str | None = None,
) -> str:
    knowledge_id = item_id or f"{promotion_id}_package"
    return f"""---
id: {knowledge_id}
type: validated_evidence
domain: synthetic_domain
status: validated
version: 1
promotion_id: {promotion_id}
source_run_id: {RUN_ID}
source_memory_digest: {DIGEST}
validated_at: {REVIEWED_AT}
reviewed_at: {REVIEWED_AT}
reviewed_by: {REVIEWER}
review_reason: {reason}
---

# Synthetic validated package

## Validated conclusions

- [[{claim}]]

## Evidence

- [[{evidence}]]

## Provenance

- Bundle SHA256: {"b" * 64}
"""


def _claim(claim: str, evidence: str) -> str:
    return f"""---
id: {claim}
type: claim
domain: synthetic_domain
status: validated
version: 1
statement: A synthetic reviewed statement.
definition: not_provided
---

# Synthetic claim

Supported by:

- [[{evidence}]]
"""


def _evidence(evidence: str, dataset: str) -> str:
    return f"""---
id: {evidence}
type: evidence
domain: synthetic_domain
status: validated
version: 1
---

# Synthetic evidence

## Dataset

- [[{dataset}]]
"""


def _dataset(dataset: str) -> str:
    return f"""---
id: {dataset}
type: dataset
domain: synthetic_domain
status: reference
version: 1
---

# Synthetic dataset
"""


def _write_bundle(
    root: Path,
    *,
    promotion_id: str = "promo_alpha",
    decision: str = "approved",
    reviewed_by: str = REVIEWER,
    reviewed_at: str = REVIEWED_AT,
    reason: str = REASON,
    memory_digest: str = DIGEST,
    run_id: str = RUN_ID,
    package: str | None = None,
    item_id: str | None = None,
    extra: dict[str, str] | None = None,
    omit: set[str] | None = None,
    manifest_documents: list[dict[str, str]] | None = None,
) -> Path:
    claim = f"{promotion_id}_claim"
    evidence = f"{promotion_id}_evidence"
    dataset = f"{promotion_id}_dataset"
    files = {
        "packages/package.md": package
        if package is not None
        else _package(
            promotion_id,
            claim=claim,
            evidence=evidence,
            reason=reason,
            item_id=item_id,
        ),
        "claims/claim.md": _claim(claim, evidence),
        "evidence/evidence.md": _evidence(evidence, dataset),
        "datasets/dataset.md": _dataset(dataset),
    }
    if extra:
        files.update(extra)
    if omit:
        for path in omit:
            files.pop(path, None)
    documents = []
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        documents.append({"path": relative, "sha256": _sha(text)})
    manifest = {
        "schema": "scientific_knowledge_promotion_v1",
        "promotion_id": promotion_id,
        "source": {"run_id": run_id, "memory_digest": memory_digest},
        "review": {
            "decision": decision,
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
            "reason": reason,
        },
        "documents": manifest_documents
        if manifest_documents is not None
        else documents,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def _promote(bundle: Path, vault: Path, reader: _Reader, **kwargs):
    return promote_scientific_knowledge(
        bundle,
        vault_root=vault,
        memory_reader=reader,
        **kwargs,
    )


def test_inspect_only_leaves_the_vault_unchanged(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "kept.txt").write_text("same\n", encoding="utf-8")
    before = _hashes(vault)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)))
    assert report["ok"] is True
    assert report["applied"] is False
    assert report["idempotent"] is False
    assert report["document_count"] == 4
    assert report["promotion_digest"]
    assert _hashes(vault) == before
    assert not (vault / "promotions").exists()
    assert not (vault.parent / ".cellertz-semantic-promotion.lock").exists()


def test_apply_creates_a_readable_healthy_promotion(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    report = _promote(
        bundle,
        vault,
        _Reader(_Memory(RUN_ID, DIGEST)),
        apply=True,
    )
    assert report["ok"] is True
    assert report["applied"] is True
    destination = vault / report["destination"]
    assert (destination / "manifest.json").is_file()
    assert (destination / "promotion_receipt.json").is_file()
    assert (destination / "packages" / "package.md").is_file()
    receipt = json.loads((destination / "promotion_receipt.json").read_text())
    assert receipt["promotion_digest"] == report["promotion_digest"]
    assert receipt["source_run_id"] == RUN_ID
    assert SECRET not in json.dumps(receipt)
    reader = FileKnowledgeReader(vault)
    assert reader.get("promo_alpha_package") is not None
    assert inspect_vault(vault).ok is True


def test_replay_is_idempotent_and_digest_conflict_fails(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    first = _promote(bundle, vault, reader, apply=True)
    again = _promote(bundle, vault, reader, apply=True)
    assert again["ok"] is True
    assert again["idempotent"] is True
    assert again["applied"] is False
    assert again["promotion_digest"] == first["promotion_digest"]
    other = _write_bundle(tmp_path / "other", reason="A different reviewed rationale.")
    conflict = _promote(other, vault, reader, apply=True)
    assert conflict["ok"] is False
    assert conflict["error"] == "promotion_conflict"
    stored = json.loads(
        (vault / "promotions" / "promo_alpha" / "promotion_receipt.json").read_text()
    )
    assert stored["promotion_digest"] == first["promotion_digest"]


def _fail_real_vault_health(monkeypatch, vault: Path) -> None:
    real = inspect_vault

    def wrapped(root=None):
        if root is not None and Path(root).resolve() == vault.resolve():
            return SecondBrainHealth(
                ok=False,
                nodes=0,
                relations=0,
                broken_links=0,
                validated_packages=0,
                invalid_packages=1,
                issues=(HealthIssue(code="invalid_frontmatter", message="forced"),),
            )
        return real(root)

    monkeypatch.setattr("cellertz.knowledge.promotion.inspect_vault", wrapped)


def test_final_validation_failure_rolls_back(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    kept = vault / "datasets"
    kept.mkdir(parents=True)
    original = "kept\n"
    (kept / "note.txt").write_text(original, encoding="utf-8")
    _fail_real_vault_health(monkeypatch, vault)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["ok"] is False
    assert report["applied"] is False
    assert report["error"] == "semantic_health_failed"
    assert "forced" not in json.dumps(report)
    assert not (vault / "promotions" / "promo_alpha").exists()
    assert not list((vault / "promotions").glob(".promo_alpha.withdrawn.*"))
    assert (kept / "note.txt").read_text(encoding="utf-8") == original


def test_rollback_failure_stays_visible(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    _fail_real_vault_health(monkeypatch, vault)
    real_rename = os.rename
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_rename(src, dst)
        raise OSError("hidden-rollback-detail")

    monkeypatch.setattr("cellertz.knowledge.promotion.os.rename", flaky)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    encoded = json.dumps(report)
    assert report["error"] == "promotion_rollback_failed"
    assert report["applied"] is True
    assert (vault / "promotions" / "promo_alpha").is_dir()
    assert "hidden-rollback-detail" not in encoded


def test_idempotent_replay_rejects_tampered_installations(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    applied = _promote(bundle, vault, reader, apply=True)
    assert applied["ok"] is True
    destination = vault / "promotions" / "promo_alpha"
    document = destination / "packages" / "package.md"
    original = document.read_bytes()

    document.write_bytes(original + b"\nchanged\n")
    altered = _promote(bundle, vault, reader, apply=True)
    assert altered["error"] == "promotion_integrity_failed"
    assert altered["applied"] is False
    assert altered["idempotent"] is False
    assert document.read_bytes() == original + b"\nchanged\n"
    document.write_bytes(original)

    document.unlink()
    removed = _promote(bundle, vault, reader, apply=True)
    assert removed["error"] == "promotion_integrity_failed"
    assert not document.exists()
    document.write_bytes(original)

    extra = destination / "claims" / "extra.md"
    extra.write_text("---\nid: extra_claim\n---\n", encoding="utf-8")
    added = _promote(bundle, vault, reader, apply=True)
    assert added["error"] == "promotion_integrity_failed"
    assert extra.is_file()
    extra.unlink()

    receipt_path = destination / "promotion_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reviewed_by"] = "someone.else"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    changed_receipt = _promote(bundle, vault, reader, apply=True)
    assert changed_receipt["error"] == "promotion_integrity_failed"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["reviewed_by"] == "someone.else"


def test_idempotent_replay_rejects_invalid_vault(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    assert _promote(bundle, vault, reader, apply=True)["ok"] is True
    broken = vault / "datasets" / "broken.md"
    broken.parent.mkdir()
    broken.write_text("---\nid: broken\n---\n", encoding="utf-8")
    before = _hashes(vault)
    report = _promote(bundle, vault, reader, apply=True)
    assert report["error"] == "promotion_integrity_failed"
    assert report["idempotent"] is False
    assert _hashes(vault) == before


def test_semantic_vault_symlink_is_rejected(tmp_path):
    vault = tmp_path / "vault"
    kept = vault / "datasets"
    kept.mkdir(parents=True)
    original = """---
id: existing_dataset
type: dataset
domain: synthetic_domain
status: reference
version: 1
---

# Existing dataset
"""
    target = kept / "existing.md"
    target.write_text(original, encoding="utf-8")
    (vault / "alias.md").symlink_to(target)
    before = target.read_bytes()
    bundle = _write_bundle(tmp_path / "bundle")
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["error"] == "semantic_vault_symlink_rejected"
    assert report["applied"] is False
    assert not (vault / "promotions").exists()
    assert target.read_bytes() == before
    assert (vault / "alias.md").is_symlink()


def test_concurrent_promotions_cannot_share_a_knowledge_id(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    left = _write_bundle(
        tmp_path / "left",
        promotion_id="promo_left",
        item_id="shared_conclusion",
    )
    right = _write_bundle(
        tmp_path / "right",
        promotion_id="promo_right",
        item_id="shared_conclusion",
    )
    runner = tmp_path / "run_promotion.py"
    runner.write_text(
        """import json, sys
from pathlib import Path
from cellertz.knowledge.promotion import promote_scientific_knowledge

class Memory:
    def __init__(self, run_id, memory_digest):
        self.run_id = run_id
        self.memory_digest = memory_digest

class Reader:
    def get_run(self, run_id):
        if run_id == sys.argv[4]:
            return Memory(sys.argv[4], sys.argv[5])
        return None

report = promote_scientific_knowledge(
    Path(sys.argv[1]),
    apply=True,
    vault_root=Path(sys.argv[2]),
    memory_reader=Reader(),
)
Path(sys.argv[3]).write_text(json.dumps(report), encoding="utf-8")
""",
        encoding="utf-8",
    )
    out_left = tmp_path / "left.json"
    out_right = tmp_path / "right.json"
    processes = [
        subprocess.Popen(
            [sys.executable, str(runner), str(bundle), str(vault), str(out), RUN_ID, DIGEST],
            cwd=ROOT,
        )
        for bundle, out in ((left, out_left), (right, out_right))
    ]
    assert [process.wait(timeout=60) for process in processes] == [0, 0]
    reports = [
        json.loads(path.read_text(encoding="utf-8")) for path in (out_left, out_right)
    ]
    assert [item["ok"] for item in reports].count(True) == 1
    assert any(item["error"] == "duplicate_knowledge_id" for item in reports)
    loaded = FileKnowledgeReader(vault)
    assert sum(1 for item in loaded.list() if item.id == "shared_conclusion") == 1
    assert not (vault / ".cellertz-semantic-promotion.lock").exists()


def test_lock_failure_does_not_write(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    before = _hashes(vault)

    def boom(*_args, **_kwargs):
        raise OSError("hidden-lock-detail")

    monkeypatch.setattr("cellertz.knowledge.promotion.fcntl.flock", boom)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["error"] == "promotion_lock_failed"
    assert report["applied"] is False
    assert "hidden-lock-detail" not in json.dumps(report)
    assert _hashes(vault) == before


def test_missing_run_and_digest_mismatch_fail_closed(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    missing = _promote(bundle, vault, _Reader(None), apply=True)
    assert missing["error"] == "source_run_not_found"
    assert not (vault / "promotions").exists()
    mismatched = _promote(
        bundle,
        vault,
        _Reader(_Memory(RUN_ID, "c" * 64)),
        apply=True,
    )
    assert mismatched["error"] == "source_memory_digest_mismatch"
    assert not (vault / "promotions").exists()


def test_databricks_failure_does_not_fall_back_to_file(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()

    def boom(_config: object) -> object:
        raise RuntimeError(f"rejected token={SECRET}")

    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_MEMORY_BACKEND", "databricks")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_MEMORY_ROOT", str(tmp_path / "file-memory"))
    monkeypatch.setenv("CELLERTZ_DATABRICKS_SERVER_HOSTNAME", "adb.example.invalid")
    monkeypatch.setenv("CELLERTZ_DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc")
    monkeypatch.setenv("CELLERTZ_DATABRICKS_CATALOG", "cellertz")
    monkeypatch.setenv("CELLERTZ_DATABRICKS_SCHEMA", "science")
    monkeypatch.setenv("CELLERTZ_DATABRICKS_ACCESS_TOKEN", SECRET)
    monkeypatch.setattr(
        "cellertz.second_brain.episodic.databricks.executor.connect_databricks_sql_executor",
        boom,
    )
    report = promote_scientific_knowledge(bundle, apply=True, vault_root=vault)
    encoded = json.dumps(report)
    assert report["ok"] is False
    assert report["error"] == "scientific_memory_unavailable"
    assert SECRET not in encoded
    assert "adb.example.invalid" not in encoded
    assert not (vault / "promotions").exists()


def test_review_fields_and_decisions_are_required(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    pending = _write_bundle(tmp_path / "pending", decision="pending")
    rejected = _write_bundle(tmp_path / "rejected", decision="rejected")
    assert _promote(pending, vault, reader, apply=True)["error"] == (
        "invalid_review_decision"
    )
    assert _promote(rejected, vault, reader, apply=True)["error"] == (
        "invalid_review_decision"
    )
    missing_reviewer = _write_bundle(tmp_path / "reviewer", reviewed_by="  ")
    missing_reason = _write_bundle(tmp_path / "reason", reason=" ")
    naive = _write_bundle(tmp_path / "naive", reviewed_at="2026-09-23T15:00:00")
    assert _promote(missing_reviewer, vault, reader)["error"] == "missing_reviewer"
    assert _promote(missing_reason, vault, reader)["error"] == "missing_review_reason"
    assert _promote(naive, vault, reader)["error"] == "invalid_reviewed_at"
    assert not (vault / "promotions").exists()


def test_unsafe_paths_symlinks_and_hashes_are_rejected(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    traversal = _write_bundle(tmp_path / "traversal")
    manifest = json.loads((traversal / "manifest.json").read_text())
    manifest["documents"][0]["path"] = "packages/../../outside.md"
    (traversal / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert _promote(traversal, vault, reader)["error"] == "unsafe_document_path"
    linked = _write_bundle(tmp_path / "linked")
    target = linked / "packages" / "package.md"
    outside = tmp_path / "outside.md"
    outside.write_text("nope\n", encoding="utf-8")
    target.unlink()
    target.symlink_to(outside)
    linked_manifest = json.loads((linked / "manifest.json").read_text())
    for entry in linked_manifest["documents"]:
        if entry["path"] == "packages/package.md":
            entry["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    (linked / "manifest.json").write_text(json.dumps(linked_manifest), encoding="utf-8")
    assert _promote(linked, vault, reader)["error"] == "symlink_rejected"
    mismatched = _write_bundle(tmp_path / "mismatch")
    body = json.loads((mismatched / "manifest.json").read_text())
    body["documents"][0]["sha256"] = "d" * 64
    (mismatched / "manifest.json").write_text(json.dumps(body), encoding="utf-8")
    assert _promote(mismatched, vault, reader)["error"] == "document_hash_mismatch"
    undeclared = _write_bundle(tmp_path / "extra")
    (undeclared / "claims" / "extra.md").write_text("---\nid: extra\n---\n", encoding="utf-8")
    assert _promote(undeclared, vault, reader)["error"] == "undeclared_document"


def test_duplicate_ids_and_broken_links_do_not_write(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    existing = vault / "packages"
    existing.mkdir()
    existing_text = """---
id: promo_alpha_package
type: dataset
domain: synthetic_domain
status: reference
version: 1
---

# Already here
"""
    (existing / "kept.md").write_text(existing_text, encoding="utf-8")
    before = _hashes(vault)
    bundle = _write_bundle(tmp_path / "bundle")
    duplicate = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert duplicate["error"] == "duplicate_knowledge_id"
    assert _hashes(vault) == before
    broken = _write_bundle(
        tmp_path / "broken",
        promotion_id="promo_beta",
        package=_package("promo_beta", claim="missing_claim", evidence="missing_evidence"),
    )
    broken_before = _hashes(vault)
    report = _promote(broken, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["error"] in {"broken_knowledge_link", "promotion_link_missing"}
    assert _hashes(vault) == broken_before


def test_existing_semantic_bytes_survive_a_distinct_promotion(tmp_path):
    vault = tmp_path / "vault"
    kept = vault / "datasets"
    kept.mkdir(parents=True)
    original = """---
id: existing_dataset
type: dataset
domain: synthetic_domain
status: reference
version: 1
---

# Existing dataset
"""
    (kept / "existing.md").write_text(original, encoding="utf-8")
    before = (kept / "existing.md").read_bytes()
    bundle = _write_bundle(tmp_path / "bundle")
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["ok"] is True
    assert (kept / "existing.md").read_bytes() == before


def test_digest_ignores_directory_and_applied_at(tmp_path):
    first_root = _write_bundle(tmp_path / "one")
    second_root = _write_bundle(tmp_path / "two")
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    early = datetime(2026, 9, 23, 12, tzinfo=UTC)
    later = datetime(2026, 9, 24, 12, tzinfo=UTC)
    first = _promote(first_root, vault, reader, clock=lambda: early)
    second = _promote(second_root, vault, reader, clock=lambda: later)
    assert first["promotion_digest"] == second["promotion_digest"]
    applied = _promote(first_root, vault, reader, apply=True, clock=lambda: early)
    replay = _promote(second_root, vault, reader, apply=True, clock=lambda: later)
    assert applied["promotion_digest"] == replay["promotion_digest"]


def test_reader_write_methods_and_import_do_not_connect(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    report = _promote(bundle, vault, reader, apply=True)
    assert report["ok"] is True
    assert reader.calls == ["get_run"]
    script = """
import builtins
import os
os.environ["CELLERTZ_SCIENTIFIC_MEMORY_BACKEND"] = "databricks"
os.environ["CELLERTZ_DATABRICKS_SERVER_HOSTNAME"] = "adb.example.invalid"
os.environ["CELLERTZ_DATABRICKS_HTTP_PATH"] = "/sql/1.0/warehouses/abc"
os.environ["CELLERTZ_DATABRICKS_CATALOG"] = "cellertz"
os.environ["CELLERTZ_DATABRICKS_SCHEMA"] = "science"
os.environ["CELLERTZ_DATABRICKS_ACCESS_TOKEN"] = "super-secret-databricks-token"
real_import = builtins.__import__

def guard(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "databricks" or name.startswith("databricks."):
        raise RuntimeError("databricks connector imported")
    module = real_import(name, globals, locals, fromlist, level)
    if name == "cellertz.second_brain.episodic.databricks.executor":
        def boom(_config):
            raise RuntimeError("databricks connected")
        module.connect_databricks_sql_executor = boom
    return module

builtins.__import__ = guard
import cellertz.knowledge.promotion  # noqa: F401
print("imported")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "imported"


def test_real_vault_and_capabilities_stay_unchanged(tmp_path):
    before = _hashes(VAULT)
    bundle = _write_bundle(tmp_path / "bundle", promotion_id="promo_real_guard")
    report = promote_scientific_knowledge(
        bundle,
        vault_root=VAULT,
        memory_reader=_Reader(_Memory(RUN_ID, DIGEST)),
        apply=False,
    )
    assert report["ok"] is True
    assert _hashes(VAULT) == before
    assert not (VAULT / "promotions").exists()
    assert not list(VAULT.rglob("*promo_real_guard*"))
    capabilities = (ROOT / "src/cellertz/mcp/capabilities.py").read_text(encoding="utf-8")
    hermes = (ROOT / "hermes/skills/cellertz-second-brain/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "promote_scientific_knowledge" not in capabilities
    assert "scientific_knowledge_promotion" not in hermes


def test_dot_promotion_ids_are_rejected(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    for promotion_id in (".", ".."):
        bundle = _write_bundle(tmp_path / f"bundle-{promotion_id}", promotion_id=promotion_id)
        report = _promote(bundle, vault, reader, apply=True)
        assert report["error"] == "invalid_promotion_id"
        assert report["applied"] is False
    assert not (vault / "promotions").exists()


def test_bundle_mutation_after_hash_is_not_promoted(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    source = bundle / "packages" / "package.md"
    vault = tmp_path / "vault"
    vault.mkdir()
    before = _hashes(vault)
    real_hash = hashlib.sha256

    def wrapped(path: Path) -> str:
        digest = real_hash(path.read_bytes()).hexdigest()
        if Path(path).resolve() == source.resolve():
            Path(path).write_bytes(Path(path).read_bytes() + b"\nmutated\n")
        return digest

    monkeypatch.setattr("cellertz.knowledge.promotion._sha256", wrapped)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["error"] == "document_hash_mismatch"
    assert report["applied"] is False
    assert _hashes(vault) == before
    assert not (vault / "promotions").exists()


def test_divergent_installed_manifest_fails_inspect_and_apply(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    assert _promote(bundle, vault, reader, apply=True)["ok"] is True
    manifest_path = vault / "promotions" / "promo_alpha" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema"] = "other_schema"
    manifest["source"] = {"run_id": "other_run", "memory_digest": "b" * 64}
    manifest["review"]["reason"] = "A different reason."
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    before = _hashes(vault)
    inspected = _promote(bundle, vault, reader)
    applied = _promote(bundle, vault, reader, apply=True)
    assert inspected["error"] == "promotion_integrity_failed"
    assert inspected["applied"] is False
    assert applied["error"] == "promotion_integrity_failed"
    assert applied["applied"] is False
    assert applied["idempotent"] is False
    assert _hashes(vault) == before


def test_inspect_only_rejects_corrupted_installation(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    assert _promote(bundle, vault, reader, apply=True)["ok"] is True
    document = vault / "promotions" / "promo_alpha" / "packages" / "package.md"
    document.write_bytes(document.read_bytes() + b"\ncorrupt\n")
    before = _hashes(vault)
    report = _promote(bundle, vault, reader)
    assert report["error"] == "promotion_integrity_failed"
    assert report["applied"] is False
    assert report["idempotent"] is False
    assert _hashes(vault) == before


def test_rollback_removal_failure_leaves_bytes_outside_the_vault(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    _fail_real_vault_health(monkeypatch, vault)
    real_rmtree = shutil.rmtree
    withdrawn_calls = {"n": 0}

    def boom(path, *args, **kwargs):
        if "withdrawn" in str(path):
            withdrawn_calls["n"] += 1
            if withdrawn_calls["n"] >= 2:
                raise OSError("hidden-rmtree-detail")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("cellertz.knowledge.promotion.shutil.rmtree", boom)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    encoded = json.dumps(report)
    destination = vault / "promotions" / "promo_alpha"
    assert report["error"] == "promotion_rollback_failed"
    assert report["applied"] is False
    assert not destination.exists()
    assert "hidden-rmtree-detail" not in encoded
    leftovers = list(vault.parent.glob(".promo_alpha.withdrawn.*"))
    assert leftovers
    assert all(vault.resolve() not in path.resolve().parents for path in leftovers)
    loaded = FileKnowledgeReader(vault)
    assert loaded.get("promo_alpha_package") is None


def test_manifest_mutation_after_second_preflight_is_not_installed(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    manifest_path = bundle / "manifest.json"
    original = manifest_path.read_bytes()
    vault = tmp_path / "vault"
    vault.mkdir()
    real_preflight = promotion_module._preflight
    calls = {"n": 0}

    def wrapped(vault_root, loaded):
        result = real_preflight(vault_root, loaded)
        calls["n"] += 1
        if calls["n"] == 2:
            tampered = json.loads(original)
            tampered["review"]["reason"] = "Tampered after preflight."
            tampered["source"]["run_id"] = "other_run"
            manifest_path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
        return result

    monkeypatch.setattr(promotion_module, "_preflight", wrapped)
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    report = _promote(bundle, vault, reader, apply=True)
    assert calls["n"] == 2
    assert report["ok"] is True
    installed = vault / "promotions" / "promo_alpha" / "manifest.json"
    assert installed.read_bytes() == original
    monkeypatch.setattr(promotion_module, "_preflight", real_preflight)
    manifest_path.write_bytes(original)
    replay = _promote(bundle, vault, reader, apply=True)
    assert replay["idempotent"] is True


def test_document_removed_before_copy_returns_stable_code(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    real_copy = shutil.copy2
    reader = _Reader(_Memory(RUN_ID, DIGEST))

    def vanish_first(src, dst, *args, **kwargs):
        Path(src).unlink(missing_ok=True)
        return real_copy(src, dst, *args, **kwargs)

    preflight_bundle = _write_bundle(tmp_path / "preflight")
    monkeypatch.setattr(promotion_module.shutil, "copy2", vanish_first)
    report = _promote(preflight_bundle, vault, reader, apply=True)
    assert report["error"] == "document_hash_mismatch"
    assert report["applied"] is False
    assert not (vault / "promotions").exists()

    apply_bundle = _write_bundle(tmp_path / "apply")

    def vanish_in_apply(src, dst, *args, **kwargs):
        if ".promo_alpha." in str(dst):
            Path(src).unlink(missing_ok=True)
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(promotion_module.shutil, "copy2", vanish_in_apply)
    applied = _promote(apply_bundle, vault, reader, apply=True)
    assert applied["error"] == "document_hash_mismatch"
    assert applied["applied"] is False
    assert not (vault / "promotions" / "promo_alpha").exists()
    assert not list((vault / "promotions").glob(".promo_alpha.*"))


def test_hash_read_error_returns_stable_code(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    real_sha = promotion_module._sha256
    bundle_root = bundle.resolve()

    def failing(path: Path) -> str:
        if bundle_root not in Path(path).resolve().parents:
            raise OSError("hidden-hash-detail")
        return real_sha(path)

    monkeypatch.setattr(promotion_module, "_sha256", failing)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["error"] == "document_hash_mismatch"
    assert report["applied"] is False
    assert "hidden-hash-detail" not in json.dumps(report)
    assert not (vault / "promotions").exists()

    load_bundle = _write_bundle(tmp_path / "load")

    def always(_path: Path) -> str:
        raise OSError("hidden-hash-detail")

    monkeypatch.setattr(promotion_module, "_sha256", always)
    loaded = _promote(load_bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert loaded["error"] == "document_hash_mismatch"
    assert "hidden-hash-detail" not in json.dumps(loaded)


def test_rollback_mkdtemp_failure_is_reported(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    _fail_real_vault_health(monkeypatch, vault)
    real_mkdtemp = tempfile.mkdtemp

    def failing(*args, **kwargs):
        if "withdrawn" in str(kwargs.get("prefix", "")):
            raise OSError("hidden-mkdtemp-detail")
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(promotion_module.tempfile, "mkdtemp", failing)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["error"] == "promotion_rollback_failed"
    assert report["applied"] is True
    assert (vault / "promotions" / "promo_alpha").is_dir()
    assert "hidden-mkdtemp-detail" not in json.dumps(report)


def test_review_whitespace_is_rejected_on_inspect_and_apply(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    padded_reviewer = _write_bundle(tmp_path / "reviewer", reviewed_by=f" {REVIEWER} ")
    padded_reason = _write_bundle(tmp_path / "reason", reason=f"{REASON} ")
    for apply in (False, True):
        first = _promote(padded_reviewer, vault, reader, apply=apply)
        second = _promote(padded_reason, vault, reader, apply=apply)
        assert first["error"] == "invalid_reviewer"
        assert second["error"] == "invalid_review_reason"
        assert first["applied"] is False
        assert second["applied"] is False
    assert not (vault / "promotions").exists()


def _applied_vault(tmp_path: Path) -> tuple[Path, Path, _Reader]:
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    reader = _Reader(_Memory(RUN_ID, DIGEST))
    assert _promote(bundle, vault, reader, apply=True)["ok"] is True
    return bundle, vault, reader


def _assert_integrity_failure(bundle: Path, vault: Path, reader: _Reader) -> None:
    before = _hashes(vault)
    inspected = _promote(bundle, vault, reader)
    applied = _promote(bundle, vault, reader, apply=True)
    for report in (inspected, applied):
        assert report["error"] == "promotion_integrity_failed"
        assert report["applied"] is False
        assert report["idempotent"] is False
    assert _hashes(vault) == before


def test_extra_manifest_field_fails_integrity(tmp_path):
    bundle, vault, reader = _applied_vault(tmp_path)
    manifest_path = vault / "promotions" / "promo_alpha" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["note"] = "added after apply"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _assert_integrity_failure(bundle, vault, reader)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("result", "pending"),
        ("applied_at", "2026-09-23T15:00:00"),
        ("applied_at", "not-a-timestamp"),
        ("unexpected", "extra"),
    ],
)
def test_tampered_receipt_fails_integrity(tmp_path, key, value):
    bundle, vault, reader = _applied_vault(tmp_path)
    receipt_path = vault / "promotions" / "promo_alpha" / "promotion_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[key] = value
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    _assert_integrity_failure(bundle, vault, reader)


def test_install_mkdtemp_failure_returns_stable_code(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    real_mkdtemp = tempfile.mkdtemp

    def failing(*args, **kwargs):
        if str(kwargs.get("prefix", "")) == ".promo_alpha.":
            raise OSError("hidden-install-mkdtemp-detail")
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(promotion_module.tempfile, "mkdtemp", failing)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["error"] == "promotion_apply_failed"
    assert report["applied"] is False
    assert "hidden-install-mkdtemp-detail" not in json.dumps(report)
    assert not (vault / "promotions" / "promo_alpha").exists()


def test_inspect_vault_exception_after_rename_rolls_back(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path / "bundle")
    vault = tmp_path / "vault"
    vault.mkdir()
    real = inspect_vault

    def raising(root=None):
        if root is not None and Path(root).resolve() == vault.resolve():
            raise RuntimeError("hidden-inspect-detail")
        return real(root)

    monkeypatch.setattr(promotion_module, "inspect_vault", raising)
    report = _promote(bundle, vault, _Reader(_Memory(RUN_ID, DIGEST)), apply=True)
    assert report["error"] == "semantic_health_failed"
    assert report["applied"] is False
    assert "hidden-inspect-detail" not in json.dumps(report)
    assert not (vault / "promotions" / "promo_alpha").exists()
    assert not list(vault.parent.glob(".promo_alpha.withdrawn.*"))


def test_cli_inspect_and_apply(tmp_path, capsys):
    bundle = _write_bundle(tmp_path / "bundle", promotion_id="promo_cli")
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CELLERTZ_SECOND_BRAIN_ROOT", str(vault))
    monkeypatch.setattr(
        "cellertz.knowledge.promotion._default_reader",
        lambda: _Reader(_Memory(RUN_ID, DIGEST)),
    )
    try:
        assert main(["--bundle", str(bundle)]) == 0
        inspected = json.loads(capsys.readouterr().out)
        assert inspected["applied"] is False
        assert main(["--bundle", str(bundle), "--apply"]) == 0
        applied = json.loads(capsys.readouterr().out)
        assert applied["applied"] is True
        assert (vault / "promotions" / "promo_cli" / "promotion_receipt.json").is_file()
    finally:
        monkeypatch.undo()
