"""The seam that lets a host own the literature cache.

These tests stay in `tests/`, which runs without Django, because the point of
the seam is that `cellertz` does not need the host to be installed.
"""

from __future__ import annotations

from typing import Any

import pytest

from cellertz.application.literature_search import (
    EuropePMCLiteratureSearch,
    MemoryLiteratureCache,
    build_query,
    open_literature_cache,
    set_literature_cache_factory,
)


class RecordingCache:
    """A host's cache. It is not a subclass of anything in cellertz."""

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.writes: list[str] = []

    def get(self, query: str) -> dict[str, Any] | None:
        self.reads.append(query)
        return None

    def put(self, query: str, payload: Any) -> None:
        self.writes.append(query)


@pytest.fixture(autouse=True)
def _clear_registration():
    yield
    set_literature_cache_factory(None)


def test_without_registration_the_environment_still_decides():
    assert isinstance(open_literature_cache({}), MemoryLiteratureCache)


def test_a_registered_factory_is_preferred_over_the_environment():
    set_literature_cache_factory(RecordingCache)
    opened = open_literature_cache({"CELLERTZ_DESIGN_DRAFT_STORE": "sqlite"})
    assert isinstance(opened, RecordingCache)


def test_clearing_the_registration_restores_the_environment_choice():
    set_literature_cache_factory(RecordingCache)
    set_literature_cache_factory(None)
    assert isinstance(open_literature_cache({}), MemoryLiteratureCache)


def test_the_search_reads_through_the_registered_cache():
    recording = RecordingCache()
    set_literature_cache_factory(lambda: recording)
    search = EuropePMCLiteratureSearch(transport=lambda url, timeout: (500, b""))
    search.search(target="PDL1", message="binder")
    assert recording.reads == [build_query("PDL1", "binder")]
    # Europe PMC answered 500, and a failure must not be cached as a reply.
    assert recording.writes == []
