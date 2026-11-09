"""Django-backed literature cache.

Implements the get/put interface that `cellertz.application.literature_search`
already expects, so the search code is not changed and does not learn about
Django. Freshness is decided here, on read, exactly as the SQLite cache
decides it: an entry older than the TTL is reported as absent and the caller
asks Europe PMC again.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from cellertz_web.literature.models import LiteratureSearchCacheEntry

DEFAULT_TTL = timedelta(days=7)


class DjangoLiteratureCache:
    """One row per query. A stale row is kept, not deleted."""

    def __init__(
        self,
        *,
        ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    def get(self, query: str) -> dict[str, Any] | None:
        entry = LiteratureSearchCacheEntry.objects.filter(query=query).first()
        if entry is None:
            return None
        if self._clock() - entry.stored_at > self._ttl:
            return None
        payload = entry.payload
        return payload if isinstance(payload, dict) else None

    def put(self, query: str, payload: Mapping[str, Any]) -> None:
        LiteratureSearchCacheEntry.objects.update_or_create(
            query=query,
            defaults={
                "stored_at": self._clock(),
                # Round-trip through JSON so a stored reply cannot hold a live
                # object, which is what the in-memory and SQLite caches do.
                "payload": json.loads(json.dumps(payload)),
            },
        )
