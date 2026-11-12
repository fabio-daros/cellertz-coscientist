"""Read-only Europe PMC search for the literature.search tool.

The Articles API is documented at https://europepmc.org/RestfulWebService.
``resultType=core`` returns the abstract. This client does not call
``/{pmcid}/fullTextXML``, so a record is never labeled as full text.
Retrieved text is stored as data. A failure or an empty result does not
invent a reference.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
SOURCE_NAME = "Europe PMC Articles API"
_MAX_RESULTS = 3
_TIMEOUT_SECONDS = 15.0
_CACHE_TTL = timedelta(hours=24)
_EXCERPT_LIMIT = 1200
_MAX_BODY_BYTES = 1_000_000
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
_SAFE_SOURCE = re.compile(r"^[A-Z]{2,8}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

Transport = Callable[[str, float], tuple[int, bytes]]


class MemoryLiteratureCache:
    """Process cache. A hit keeps the original retrieval time."""

    def __init__(self, *, ttl: timedelta = _CACHE_TTL, clock: Callable[[], datetime] | None = None) -> None:
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._items: dict[str, tuple[datetime, dict[str, Any]]] = {}

    def get(self, query: str) -> dict[str, Any] | None:
        found = self._items.get(query)
        if found is None:
            return None
        stored_at, payload = found
        if self._clock() - stored_at > self._ttl:
            del self._items[query]
            return None
        return json.loads(json.dumps(payload))

    def put(self, query: str, payload: Mapping[str, Any]) -> None:
        self._items[query] = (self._clock(), json.loads(json.dumps(payload)))


class SQLiteLiteratureCache:
    """Cache beside the draft database. It does not open a plan or an attempt."""

    def __init__(self, path: Path, *, ttl: timedelta = _CACHE_TTL, clock: Callable[[], datetime] | None = None) -> None:
        self._path = Path(path)
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS literature_search_cache (
                query TEXT PRIMARY KEY,
                stored_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, query: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT stored_at, payload FROM literature_search_cache WHERE query = ?",
                (query,),
            ).fetchone()
        if row is None:
            return None
        stored_at = datetime.fromisoformat(row[0])
        if self._clock() - stored_at > self._ttl:
            return None
        payload = json.loads(row[1])
        return payload if isinstance(payload, dict) else None

    def put(self, query: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO literature_search_cache (query, stored_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(query) DO UPDATE SET stored_at = excluded.stored_at, payload = excluded.payload
                """,
                (query, self._clock().isoformat(), json.dumps(payload)),
            )
            self._conn.commit()


class LiteratureCache(Protocol):
    """What this module needs from a cache. Structural, so a cache can live in
    another package without this one importing it."""

    def get(self, query: str) -> dict[str, Any] | None: ...

    def put(self, query: str, payload: Mapping[str, Any]) -> None: ...


# A host that owns persistence registers its cache here before the service
# starts. The registration inverts the dependency: this module never imports
# the host, so `cellertz` stays free of any framework.
_cache_factory: Callable[[], LiteratureCache] | None = None


def set_literature_cache_factory(factory: Callable[[], LiteratureCache] | None) -> None:
    """Install the cache the host owns, or pass None to fall back to sqlite."""

    global _cache_factory
    _cache_factory = factory


def open_literature_cache(environ: Mapping[str, str] | None = None) -> LiteratureCache:
    if _cache_factory is not None:
        return _cache_factory()
    env = os.environ if environ is None else environ
    if env.get("CELLERTZ_DESIGN_DRAFT_STORE", "memory").strip().lower() != "sqlite":
        return MemoryLiteratureCache()
    raw = env.get("CELLERTZ_DESIGN_DRAFT_DB", "").strip()
    if not raw or any(char in raw for char in "\x00\n\r"):
        return MemoryLiteratureCache()
    return SQLiteLiteratureCache(Path(raw).with_name("literature-search-cache.sqlite"))


def urllib_transport(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "CellertzLiteratureSearch/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(_MAX_BODY_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(_MAX_BODY_BYTES)


class EuropePMCLiteratureSearch:
    """One search request. It is not retried."""

    def __init__(
        self,
        transport: Transport | None = None,
        cache: LiteratureCache | None = None,
        clock: Callable[[], datetime] | None = None,
        *,
        timeout: float = _TIMEOUT_SECONDS,
        max_results: int = _MAX_RESULTS,
    ) -> None:
        self._transport = transport or urllib_transport
        self._cache = cache if cache is not None else open_literature_cache()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timeout = timeout
        self._max_results = max_results

    def search(self, *, target: str, message: str) -> dict[str, Any]:
        query = build_query(target, message)
        retrieved_at = self._clock().isoformat()
        if not query:
            return _document(query, retrieved_at, [], available=False, failure="no_target", limitation=_NO_TARGET)
        cached = self._cache.get(query)
        if cached is not None:
            cached["cache_hit"] = True
            return cached
        url = _search_url(query, self._max_results)
        try:
            status, body = self._transport(url, self._timeout)
        except Exception:  # noqa: BLE001 — a transport failure is recorded, not retried
            return _document(query, retrieved_at, [], available=False, failure="request_failed", limitation=_FAILED)
        if status != 200:
            return _document(query, retrieved_at, [], available=False, failure=f"http_{status}", limitation=_FAILED)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _document(query, retrieved_at, [], available=False, failure="unreadable", limitation=_UNREADABLE)
        results = _records(payload, retrieved_at, self._max_results)
        limitation = _EMPTY if not results else _ABSTRACTS_ONLY
        document = _document(query, retrieved_at, results, available=True, failure=None, limitation=limitation)
        self._cache.put(query, document)
        document["cache_hit"] = False
        return document


def build_query(target: str, message: str) -> str:
    """Search the requested target and the binder interaction. The raw message is not sent."""

    target_clause = _target_clause(target)
    if not target_clause:
        return ""
    interaction = _interaction_clause(message)
    return f"{target_clause} AND {interaction} AND HAS_ABSTRACT:Y"


def _target_clause(target: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", target.casefold())
    if compact in {"pdl1", "cd274", "b7h1"}:
        terms = ('"PD-L1"', "PDL1", "CD274", '"B7-H1"')
    else:
        cleaned = re.sub(r"[^\w\s.-]", "", target).strip()
        if not cleaned:
            return ""
        terms = (f'"{cleaned}"',)
    parts = [f"TITLE:{term} OR ABSTRACT:{term}" for term in terms]
    return "(" + " OR ".join(parts) + ")"


def _interaction_clause(message: str) -> str:
    """Protein-interface terms only. The raw message and a bare binder word are not the query."""

    del message
    terms = ('"binding interface"', "hotspot", '"interface residue"', '"protein-protein"')
    return "(" + " OR ".join(f"ABSTRACT:{term}" for term in terms) + ")"


def _search_url(query: str, max_results: int) -> str:
    return ENDPOINT + "?" + urllib.parse.urlencode(
        {
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": str(max_results),
            "cursorMark": "*",
        }
    )


def _records(payload: Mapping[str, Any], retrieved_at: str, max_results: int) -> list[dict[str, Any]]:
    result_list = payload.get("resultList") if isinstance(payload.get("resultList"), dict) else {}
    rows = result_list.get("result") if isinstance(result_list.get("result"), list) else []
    records: list[dict[str, Any]] = []
    for row in rows:
        if len(records) >= max_results or not isinstance(row, dict):
            continue
        record = _record(row, retrieved_at)
        if record is not None:
            records.append(record)
    return records


def _record(row: Mapping[str, Any], retrieved_at: str) -> dict[str, Any] | None:
    source = row.get("source") if isinstance(row.get("source"), str) else ""
    identifier = row.get("id") if isinstance(row.get("id"), str) else ""
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    if not _SAFE_SOURCE.fullmatch(source) or not _SAFE_ID.fullmatch(identifier) or not title.strip():
        return None
    abstract = row.get("abstractText") if isinstance(row.get("abstractText"), str) else ""
    excerpt, truncated = _excerpt(abstract)
    title = _CONTROL.sub(" ", title).strip()
    labels = classify_literature(title, excerpt, source)
    return {
        "id": f"{source}:{identifier}",
        "title": title,
        "url": f"https://europepmc.org/article/{source}/{identifier}",
        "excerpt": excerpt,
        "excerpt_truncated": truncated,
        "retrieved_at": retrieved_at,
        "relevance": labels["relevance"],
        "publication_kind": labels["publication_kind"],
        "text_level": "abstract" if excerpt else "unavailable",
        "full_text_retrieved": False,
    }


def _excerpt(abstract: str) -> tuple[str, bool]:
    text = _CONTROL.sub(" ", abstract).strip()
    if len(text) <= _EXCERPT_LIMIT:
        return text, False
    return text[:_EXCERPT_LIMIT].rstrip(), True


def _document(
    query: str,
    retrieved_at: str,
    results: list[dict[str, Any]],
    *,
    available: bool,
    failure: str | None,
    limitation: str,
) -> dict[str, Any]:
    return {
        "available": available,
        "source": SOURCE_NAME,
        "endpoint": ENDPOINT,
        "query": query,
        "retrieved_at": retrieved_at,
        "results": results,
        "failure": failure,
        "cache_hit": False,
        "full_text_retrieved": False,
        "limitation": limitation,
    }


_DIRECT_TERMS = ("binding interface", "hotspot", "interface residue", "protein-protein")
_CONTEXT_TERMS = ("small molecule", "drug pocket", "rna-binding", "post-transcriptional")


def classify_literature(title: str, excerpt: str, source: str) -> dict[str, str]:
    """Separate protein-interface evidence from general target context. PPR is a preprint."""

    blob = f"{title}\n{excerpt}".casefold()
    direct = any(term in blob for term in _DIRECT_TERMS) and not any(term in blob for term in _CONTEXT_TERMS)
    return {
        "relevance": "direct" if direct else "context",
        "publication_kind": "preprint" if source == "PPR" else "article",
    }


_NO_TARGET = "The literature search had no target. No reference was invented."
_FAILED = "The literature search failed. No reference was invented."
_UNREADABLE = "The literature response could not be read. No reference was invented."
_EMPTY = "The literature search returned no results. No reference was invented."
_ABSTRACTS_ONLY = (
    "The literature search retrieved abstracts only. Full text was not retrieved. "
    "These records are not a verified residue map for the selected structure."
)
