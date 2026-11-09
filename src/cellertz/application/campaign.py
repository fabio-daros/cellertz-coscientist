"""Saved research campaigns.

A campaign records a name, a scientific objective, and optional notes.
Creating one does not create a design request, a plan, a journey, an
approval, an authorization, or an execution. The store is a new database.
It does not open or rewrite the journey or generation-plan databases.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 1
_REQUIRED_TABLES = frozenset({"schema_meta", "campaigns"})
_NAME_LIMIT = 200
_TEXT_LIMIT = 4000
_BODY_LIMIT = 65536
_PUBLIC_KEYS = (
    "campaign_id",
    "name",
    "scientific_objective",
    "notes",
    "created_at",
    "updated_at",
    "design_request_ids",
)


class CampaignStoreError(Exception):
    """The campaign database cannot be used."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CampaignError(Exception):
    """The campaign request cannot be recorded."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _request_identity(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 200:
        raise CampaignError("request_id_rejected")
    if any(char in value for char in "/\\\n\r\x00 "):
        raise CampaignError("request_id_rejected")
    return value


def _text(value: object, *, required: bool, limit: int, code: str) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise CampaignError(code)
    cleaned = value.strip()
    if required and not cleaned:
        raise CampaignError(code)
    if len(cleaned) > limit:
        raise CampaignError(code)
    return cleaned


def _public(document: Mapping[str, Any]) -> dict[str, Any]:
    return {key: document[key] for key in _PUBLIC_KEYS}


class MemoryCampaignStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def put(self, document: Mapping[str, Any]) -> None:
        stored = json.loads(json.dumps(dict(document)))
        with self._lock:
            self._items[str(stored["campaign_id"])] = stored

    def get(self, campaign_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._items.get(campaign_id)
        return None if found is None else json.loads(json.dumps(found))

    def documents(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._items.values())
        copied = [json.loads(json.dumps(item)) for item in items]
        return sorted(copied, key=lambda item: (str(item["created_at"]), str(item["campaign_id"])))

    def close(self) -> None:
        return None


class SQLiteCampaignStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path).resolve()
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._conn = conn
        version = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if version is None:
            self._ensure_schema()
            return
        row = conn.execute("SELECT version FROM schema_meta").fetchone()
        tables = {
            item["name"]
            for item in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if row is None or int(row["version"]) != SCHEMA_VERSION or not _REQUIRED_TABLES <= tables:
            conn.close()
            self._conn = None
            raise CampaignStoreError("campaign_schema_inconsistent")

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            CREATE TABLE campaigns (
                campaign_id TEXT PRIMARY KEY,
                document TEXT NOT NULL
            );
            INSERT INTO schema_meta (version) VALUES (1);
            """
        )
        self._conn.commit()

    def put(self, document: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(document), sort_keys=True, separators=(",", ":"))
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT INTO campaigns (campaign_id, document)
                VALUES (?, ?)
                ON CONFLICT(campaign_id) DO UPDATE SET document = excluded.document
                """,
                (str(document["campaign_id"]), payload),
            )
            self._conn.commit()

    def get(self, campaign_id: str) -> dict[str, Any] | None:
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT document FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        if row is None:
            return None
        parsed = json.loads(row["document"])
        return parsed if isinstance(parsed, dict) else None

    def documents(self) -> list[dict[str, Any]]:
        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute("SELECT document FROM campaigns ORDER BY campaign_id").fetchall()
        loaded: list[dict[str, Any]] = []
        for row in rows:
            parsed = json.loads(row["document"])
            if isinstance(parsed, dict):
                loaded.append(parsed)
        return sorted(loaded, key=lambda item: (str(item["created_at"]), str(item["campaign_id"])))

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def open_campaign_store(
    environ: Mapping[str, str] | None = None,
) -> MemoryCampaignStore | SQLiteCampaignStore:
    env = os.environ if environ is None else environ
    backend = env.get("CELLERTZ_CAMPAIGN_STORE", "memory").strip().lower()
    if backend == "memory":
        return MemoryCampaignStore()
    if backend != "sqlite":
        raise CampaignStoreError("invalid_campaign_store")
    raw = env.get("CELLERTZ_CAMPAIGN_DB", "").strip()
    if not raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        raise CampaignStoreError("campaign_db_required")
    return SQLiteCampaignStore(Path(raw))


class CampaignService:
    def __init__(self, store: MemoryCampaignStore | SQLiteCampaignStore | None = None) -> None:
        self._store = MemoryCampaignStore() if store is None else store
        self._lock = threading.Lock()

    def create(self, name: object, scientific_objective: object, notes: object = None) -> dict[str, Any]:
        recorded_name = _text(name, required=True, limit=_NAME_LIMIT, code="name_required")
        recorded_objective = _text(
            scientific_objective,
            required=True,
            limit=_TEXT_LIMIT,
            code="scientific_objective_required",
        )
        recorded_notes = _text(notes, required=False, limit=_TEXT_LIMIT, code="notes_rejected")
        stamp = _now()
        document = {
            "campaign_id": f"campaign_{uuid4().hex}",
            "name": recorded_name,
            "scientific_objective": recorded_objective,
            "notes": recorded_notes,
            "created_at": stamp,
            "updated_at": stamp,
            "design_request_ids": [],
        }
        with self._lock:
            self._store.put(document)
        return _public(document)

    def get(self, campaign_id: str) -> dict[str, Any] | None:
        if not campaign_id or "/" in campaign_id:
            return None
        found = self._store.get(campaign_id)
        return None if found is None else _public(found)

    def list(self) -> list[dict[str, Any]]:
        return [_public(item) for item in self._store.documents() if isinstance(item, dict)]

    def link_design_request(self, campaign_id: str, request_id: object) -> dict[str, Any] | None:
        """Record an optional association. This does not create a request or a journey."""

        recorded = _request_identity(request_id)
        with self._lock:
            found = self._store.get(campaign_id)
            if found is None:
                return None
            current = found.get("design_request_ids")
            linked = [item for item in current if isinstance(item, str)] if isinstance(current, list) else []
            if recorded not in linked:
                linked.append(recorded)
            updated = dict(found)
            updated["design_request_ids"] = linked
            updated["updated_at"] = _now()
            self._store.put(updated)
        return _public(updated)

    def close(self) -> None:
        self._store.close()


def handle_campaign(
    service: CampaignService, method: str, path: str, body: bytes
) -> tuple[int, dict[str, Any]] | None:
    route = path.split("?", 1)[0]
    if route == "/api/campaigns" and method == "GET":
        return 200, {"campaigns": service.list()}
    if route == "/api/campaigns" and method == "POST":
        if len(body) > _BODY_LIMIT:
            return 400, {"status": "rejected"}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return 400, {"status": "rejected"}
        if not isinstance(payload, dict):
            return 400, {"status": "rejected"}
        try:
            document = service.create(
                payload.get("name"),
                payload.get("scientific_objective"),
                payload.get("notes"),
            )
        except CampaignError:
            return 400, {"status": "rejected"}
        return 200, document
    prefix = "/api/campaigns/"
    link_suffix = "/design-requests"
    if method == "POST" and route.startswith(prefix) and route.endswith(link_suffix):
        campaign_id = route[len(prefix) : -len(link_suffix)]
        if not campaign_id or "/" in campaign_id:
            return 404, {"status": "campaign_absent"}
        if len(body) > _BODY_LIMIT:
            return 400, {"status": "rejected"}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return 400, {"status": "rejected"}
        if not isinstance(payload, dict) or set(payload) != {"request_id"}:
            return 400, {"status": "rejected"}
        try:
            linked = service.link_design_request(campaign_id, payload.get("request_id"))
        except CampaignError:
            return 400, {"status": "rejected"}
        if linked is None:
            return 404, {"status": "campaign_absent"}
        return 200, linked
    if method == "GET" and route.startswith(prefix):
        campaign_id = route.removeprefix(prefix)
        if not campaign_id or "/" in campaign_id:
            return 404, {"status": "campaign_absent"}
        found = service.get(campaign_id)
        if found is None:
            return 404, {"status": "campaign_absent"}
        return 200, found
    if route == "/api/campaigns" or route.startswith(prefix):
        return 405, {"status": "rejected"}
    return None
