"""Idempotency records for mutating protein-run starts.

Separate from ProteinRunStateStore. Keys are hashed before they become
paths so the raw key never appears in a filename.

Claiming a key is atomic (O_CREAT|O_EXCL). Status transitions use
compare-and-swap. A later DynamoDB/Postgres store can keep the same
contract with a conditional write.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

IN_PROGRESS_STATUSES = frozenset(
    {
        "pending",
        "scientific_pending",
    }
)


def default_idempotency_root() -> Path:
    return Path(os.environ.get("CELLERTZ_IDEMPOTENCY_ROOT", "outputs/idempotency"))


def hash_idempotency_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def canonical_request_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _optional_str(document: dict[str, Any], key: str) -> str | None:
    value = document.get(key)
    if value is None:
        return None
    return str(value)


@dataclass(frozen=True)
class IdempotencyRecord:
    idempotency_key: str
    request_hash: str
    run_id: str
    mode: str
    created_at: str
    status: str
    execution_id: str | None = None
    output_dir: str | None = None
    manifest_sha256: str | None = None
    execution_status: str | None = None
    scientific_status: str | None = None
    error: str | None = None
    scientific_request_sha256: str | None = None

    def evolve(self, **changes: Any) -> IdempotencyRecord:
        return replace(self, **changes)

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "idempotency_key": self.idempotency_key,
            "request_hash": self.request_hash,
            "run_id": self.run_id,
            "mode": self.mode,
            "created_at": self.created_at,
            "status": self.status,
        }
        if self.execution_id is not None:
            document["execution_id"] = self.execution_id
        if self.output_dir is not None:
            document["output_dir"] = self.output_dir
        if self.manifest_sha256 is not None:
            document["manifest_sha256"] = self.manifest_sha256
        if self.execution_status is not None:
            document["execution_status"] = self.execution_status
        if self.scientific_status is not None:
            document["scientific_status"] = self.scientific_status
        if self.error is not None:
            document["error"] = self.error
        if self.scientific_request_sha256 is not None:
            document["scientific_request_sha256"] = (
                self.scientific_request_sha256
            )
        return document

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> IdempotencyRecord:
        return cls(
            idempotency_key=str(document["idempotency_key"]),
            request_hash=str(document["request_hash"]),
            run_id=str(document["run_id"]),
            mode=str(document["mode"]),
            created_at=str(document["created_at"]),
            status=str(document["status"]),
            execution_id=_optional_str(document, "execution_id"),
            output_dir=_optional_str(document, "output_dir"),
            manifest_sha256=_optional_str(document, "manifest_sha256"),
            execution_status=_optional_str(document, "execution_status"),
            scientific_status=_optional_str(document, "scientific_status"),
            error=_optional_str(document, "error"),
            scientific_request_sha256=_optional_str(
                document,
                "scientific_request_sha256",
            ),
        )


class ProteinRunIdempotencyStore(Protocol):
    def get(self, idempotency_key: str) -> IdempotencyRecord | None:
        ...

    def try_claim(self, record: IdempotencyRecord) -> tuple[bool, IdempotencyRecord]:
        ...

    def try_transition(
        self,
        idempotency_key: str,
        *,
        expected_status: str,
        record: IdempotencyRecord,
    ) -> tuple[bool, IdempotencyRecord]:
        ...

    def wait_until_settled(self, idempotency_key: str) -> IdempotencyRecord | None:
        ...

    def put(self, record: IdempotencyRecord) -> None:
        ...


class FileIdempotencyStore:
    def __init__(
        self,
        root: Path | None = None,
        *,
        settle_timeout_s: float = 60.0,
        settle_interval_s: float = 0.02,
    ) -> None:
        self.root = Path(root) if root is not None else default_idempotency_root()
        self.settle_timeout_s = settle_timeout_s
        self.settle_interval_s = settle_interval_s

    def _path(self, idempotency_key: str) -> Path:
        return self.root / f"{hash_idempotency_key(idempotency_key)}.json"

    def _lock_path(self, idempotency_key: str) -> Path:
        return self.root / f"{hash_idempotency_key(idempotency_key)}.cas.lock"

    def _encode(self, record: IdempotencyRecord) -> str:
        return json.dumps(
            record.to_document(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ) + "\n"

    def _read(self, path: Path) -> IdempotencyRecord | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        if not raw.strip():
            return None
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(document, dict):
            return None
        try:
            return IdempotencyRecord.from_document(document)
        except (KeyError, TypeError, ValueError):
            return None

    def _write_atomic(self, path: Path, encoded: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix="idem.",
            suffix=".json.tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    def _acquire_cas_lock(self, idempotency_key: str) -> int:
        lock_path = self._lock_path(idempotency_key)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.settle_timeout_s
        while True:
            try:
                return os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "timed out acquiring idempotency CAS lock"
                    )
                time.sleep(self.settle_interval_s)

    def _release_cas_lock(self, idempotency_key: str, fd: int) -> None:
        try:
            os.close(fd)
        finally:
            try:
                self._lock_path(idempotency_key).unlink()
            except FileNotFoundError:
                pass

    def get(self, idempotency_key: str) -> IdempotencyRecord | None:
        return self._read(self._path(idempotency_key))

    def try_claim(self, record: IdempotencyRecord) -> tuple[bool, IdempotencyRecord]:
        path = self._path(record.idempotency_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = self._encode(record)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(path, flags, 0o644)
        except FileExistsError:
            existing = self._wait_readable(record.idempotency_key)
            if existing is None:
                existing = self.get(record.idempotency_key)
            if existing is None:
                raise
            return False, existing
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            return True, record
        except Exception:
            if path.exists():
                path.unlink()
            raise

    def try_transition(
        self,
        idempotency_key: str,
        *,
        expected_status: str,
        record: IdempotencyRecord,
    ) -> tuple[bool, IdempotencyRecord]:
        if record.idempotency_key != idempotency_key:
            raise ValueError("CAS record key does not match idempotency_key")
        fd = self._acquire_cas_lock(idempotency_key)
        try:
            current = self.get(idempotency_key)
            if (
                current is None
                or current.status != expected_status
                or current.request_hash != record.request_hash
            ):
                return False, current if current is not None else record
            self._write_atomic(self._path(idempotency_key), self._encode(record))
            return True, record
        finally:
            self._release_cas_lock(idempotency_key, fd)

    def wait_until_settled(self, idempotency_key: str) -> IdempotencyRecord | None:
        deadline = time.monotonic() + self.settle_timeout_s
        last = self.get(idempotency_key)
        while time.monotonic() < deadline:
            last = self.get(idempotency_key)
            if last is not None and last.status not in IN_PROGRESS_STATUSES:
                return last
            time.sleep(self.settle_interval_s)
        return last

    def _wait_readable(self, idempotency_key: str) -> IdempotencyRecord | None:
        deadline = time.monotonic() + self.settle_timeout_s
        while time.monotonic() < deadline:
            record = self.get(idempotency_key)
            if record is not None:
                return record
            time.sleep(self.settle_interval_s)
        return self.get(idempotency_key)

    def put(self, record: IdempotencyRecord) -> None:
        self._write_atomic(self._path(record.idempotency_key), self._encode(record))
