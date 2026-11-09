"""The Django cache must answer exactly as the SQLite cache answers.

A migration that changes behaviour is not a migration. Each test drives both
implementations with one clock and compares their replies.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.test import TestCase

from cellertz.application.literature_search import SQLiteLiteratureCache
from cellertz_web.literature.cache import DjangoLiteratureCache
from cellertz_web.literature.models import LiteratureSearchCacheEntry

TTL = timedelta(days=7)
START = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
PAYLOAD = {"results": [{"id": "MED:1", "title": "A retrieved abstract"}], "available": True}


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class LiteratureCacheEquivalenceTests(TestCase):
    def setUp(self) -> None:
        self.clock = _Clock(START)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.sqlite = SQLiteLiteratureCache(
            Path(self.directory.name) / "literature.sqlite", ttl=TTL, clock=self.clock
        )
        self.django = DjangoLiteratureCache(ttl=TTL, clock=self.clock)

    def both(self, query: str):
        return self.sqlite.get(query), self.django.get(query)

    def test_an_absent_query_is_absent_in_both(self) -> None:
        sqlite, django = self.both("never stored")
        self.assertIsNone(sqlite)
        self.assertIsNone(django)

    def test_a_stored_reply_comes_back_from_both(self) -> None:
        self.sqlite.put("pdl1 binding site", PAYLOAD)
        self.django.put("pdl1 binding site", PAYLOAD)
        sqlite, django = self.both("pdl1 binding site")
        self.assertEqual(sqlite, PAYLOAD)
        self.assertEqual(django, sqlite)

    def test_an_entry_older_than_the_ttl_is_absent_in_both(self) -> None:
        self.sqlite.put("pdl1", PAYLOAD)
        self.django.put("pdl1", PAYLOAD)
        self.clock.now = START + TTL + timedelta(seconds=1)
        sqlite, django = self.both("pdl1")
        self.assertIsNone(sqlite)
        self.assertIsNone(django)

    def test_an_entry_at_the_ttl_boundary_is_still_fresh_in_both(self) -> None:
        self.sqlite.put("pdl1", PAYLOAD)
        self.django.put("pdl1", PAYLOAD)
        self.clock.now = START + TTL
        sqlite, django = self.both("pdl1")
        self.assertEqual(sqlite, PAYLOAD)
        self.assertEqual(django, sqlite)

    def test_a_stale_entry_is_kept_rather_than_deleted(self) -> None:
        self.django.put("pdl1", PAYLOAD)
        self.clock.now = START + TTL + timedelta(days=1)
        self.assertIsNone(self.django.get("pdl1"))
        self.assertEqual(LiteratureSearchCacheEntry.objects.count(), 1)

    def test_storing_the_same_query_again_replaces_the_reply_in_both(self) -> None:
        self.sqlite.put("pdl1", PAYLOAD)
        self.django.put("pdl1", PAYLOAD)
        self.clock.now = START + timedelta(hours=1)
        replaced = {"results": [], "available": False}
        self.sqlite.put("pdl1", replaced)
        self.django.put("pdl1", replaced)
        sqlite, django = self.both("pdl1")
        self.assertEqual(sqlite, replaced)
        self.assertEqual(django, sqlite)
        self.assertEqual(LiteratureSearchCacheEntry.objects.count(), 1)

    def test_a_stored_reply_does_not_keep_the_caller_object(self) -> None:
        payload = {"results": [{"id": "MED:1"}]}
        self.django.put("pdl1", payload)
        payload["results"].append({"id": "MED:2"})
        self.assertEqual(self.django.get("pdl1"), {"results": [{"id": "MED:1"}]})

    def test_queries_do_not_collide_in_both(self) -> None:
        self.sqlite.put("pdl1", PAYLOAD)
        self.django.put("pdl1", PAYLOAD)
        sqlite, django = self.both("pdl1 ")
        self.assertIsNone(sqlite)
        self.assertIsNone(django)
