"""Which stored versions a recorded result names.

A version named by an analysis or a suggestion is one that cannot be removed
without making that result unexplainable.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cellertz.application.agent_prompt_references import referenced_versions


def _store(path: Path, table: str, documents: list[dict]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, document TEXT NOT NULL)")
    connection.executemany(
        f"INSERT INTO {table} (document) VALUES (?)", [(json.dumps(d),) for d in documents]
    )
    connection.commit()
    connection.close()


@pytest.fixture
def state(tmp_path: Path) -> dict[str, str]:
    return {
        "CELLERTZ_DESIGN_DRAFT_STORE": "sqlite",
        "CELLERTZ_DESIGN_DRAFT_DB": str(tmp_path / "design-drafts.sqlite"),
    }


def test_nothing_is_referenced_without_a_sqlite_store():
    assert referenced_versions({}) == set()


def test_an_absent_store_file_is_not_an_error(state):
    assert referenced_versions(state) == set()


def test_an_analysis_names_the_version_that_produced_it(state, tmp_path):
    _store(
        tmp_path / "design-supervisor-analyses.sqlite",
        "design_supervisor_analyses",
        [{"prompt": {"agent_key": "a.task", "origin": "stored", "version": 2}}],
    )
    assert referenced_versions(state) == {("a.task", 2)}


def test_a_suggestion_names_both_of_its_prompts(state, tmp_path):
    _store(
        tmp_path / "design-plan-suggestions.sqlite",
        "design_plan_suggestions",
        [
            {
                "prompts": [
                    {"agent_key": "sup.delegation", "origin": "stored", "version": 1},
                    {"agent_key": "spec.suggestion", "origin": "stored", "version": 3},
                ]
            }
        ],
    )
    assert referenced_versions(state) == {("sup.delegation", 1), ("spec.suggestion", 3)}


def test_a_builtin_origin_names_no_version(state, tmp_path):
    _store(
        tmp_path / "design-supervisor-analyses.sqlite",
        "design_supervisor_analyses",
        [{"prompt": {"agent_key": "a.task", "origin": "builtin", "version": None}}],
    )
    assert referenced_versions(state) == set()


def test_both_stores_are_read(state, tmp_path):
    _store(
        tmp_path / "design-supervisor-analyses.sqlite",
        "design_supervisor_analyses",
        [{"prompt": {"agent_key": "a.task", "origin": "stored", "version": 1}}],
    )
    _store(
        tmp_path / "design-plan-suggestions.sqlite",
        "design_plan_suggestions",
        [{"prompts": [{"agent_key": "b.task", "origin": "stored", "version": 9}]}],
    )
    assert referenced_versions(state) == {("a.task", 1), ("b.task", 9)}


def test_a_document_that_is_not_json_is_skipped(state, tmp_path):
    path = tmp_path / "design-supervisor-analyses.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE design_supervisor_analyses (id INTEGER PRIMARY KEY, document TEXT)")
    connection.execute("INSERT INTO design_supervisor_analyses (document) VALUES ('not json')")
    connection.commit()
    connection.close()
    assert referenced_versions(state) == set()
