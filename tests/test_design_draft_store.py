"""Draft revisions survive a new process without becoming a journey."""

from __future__ import annotations

import json
import sqlite3

from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.request_draft import DesignDraftCommand, DesignDrafting, DeterministicDraftExtractor
from cellertz.application.request_draft_store import SQLiteDesignDraftStore, open_design_draft_store
from cellertz.application.request_transport import handle_design_draft, handle_list_design_drafts, handle_read_design_draft

SESSION = "session_pdl1_prototype"
FIRST = "Design a binder with length 80 120 for an unnamed target."
SECOND = "Design a binder with length 90 140 for an unnamed target."
STALE = "Design a binder with length 70 100 for an unnamed target."


def _service(store: SQLiteDesignDraftStore) -> DesignDrafting:
    session = pdl1_prototype_session()
    return DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        store=store,
        request_ids=lambda: "request_saved",
    )


def _post(service: DesignDrafting, message: str, request_id: str | None, revision: int | None) -> tuple[int, dict]:
    return handle_design_draft(
        service,
        json.dumps(
            {
                "session_id": SESSION,
                "message": message,
                "request_id": request_id,
                "revision": revision,
            }
        ).encode(),
    )


def test_incomplete_draft_survives_restart_and_rejects_a_stale_revision(tmp_path) -> None:
    database = tmp_path / "design-drafts.sqlite"
    store = SQLiteDesignDraftStore(database)
    service = _service(store)
    status, created = _post(service, FIRST, None, None)
    assert status == 200
    assert created["status"] == "draft_created"
    draft = created["draft"]
    assert draft["request_id"] == "request_saved"
    assert draft["revision"] == 1
    assert draft["completeness"] == "incomplete"
    assert draft["execution_authorized"] is False
    assert draft["executable"] is False
    fields = {item["name"]: item for item in draft["fields"]}
    assert fields["target_id"] == {"name": "target_id", "value": None, "provenance": "absent"}
    assert "PDL1" not in FIRST
    status, updated = _post(service, SECOND, "request_saved", 1)
    assert status == 200
    assert updated["draft"]["revision"] == 2
    assert updated["draft"]["completeness"] == "incomplete"
    store.close()

    reopened = SQLiteDesignDraftStore(database)
    restarted = _service(reopened)
    read_status, body = handle_read_design_draft(
        restarted, "/api/design-draft?request_id=request_saved"
    )
    assert read_status == 200
    assert body["status"] == "draft_recorded"
    assert body["message"] == SECOND
    assert body["draft"]["request_id"] == "request_saved"
    assert body["draft"]["revision"] == 2
    assert body["draft"]["completeness"] == "incomplete"
    read_fields = {item["name"]: item for item in body["draft"]["fields"]}
    assert read_fields["target_id"]["value"] is None
    assert read_fields["target_id"]["provenance"] == "absent"
    assert read_fields["length_min"]["value"] == 90
    assert read_fields["length_max"]["value"] == 140

    for _ in range(2):
        conflict_status, conflict = _post(restarted, STALE, "request_saved", 1)
        assert conflict_status == 409
        assert conflict["status"] == "revision_conflict"
        assert conflict["draft"]["revision"] == 2
        assert conflict["changed_fields"] == []
    again_status, again = handle_read_design_draft(
        restarted, "/api/design-draft?request_id=request_saved"
    )
    assert again_status == 200
    assert again["message"] == SECOND
    assert again["draft"]["revision"] == 2
    assert [item.revision for item in restarted.revisions("request_saved")] == [1, 2]

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        count = connection.execute("SELECT COUNT(*) FROM design_request_drafts").fetchone()[0]
    assert tables == {"schema_meta", "design_request_drafts"}
    assert count == 2
    reopened.close()


def test_editing_the_same_request_saves_a_new_revision_and_lists_one_card(tmp_path) -> None:
    database = tmp_path / "design-drafts.sqlite"
    store = SQLiteDesignDraftStore(database)
    identifiers = iter(["request_alpha", "request_beta"])
    session = pdl1_prototype_session()
    service = DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        store=store,
        request_ids=lambda: next(identifiers),
    )
    wording = "Design a binder with length 80 120 for an unnamed target, kept as a draft."
    status, created = _post(service, FIRST, None, None)
    assert status == 200
    assert created["status"] == "draft_created"
    assert created["draft"]["request_id"] == "request_alpha"
    assert created["draft"]["revision"] == 1

    status, edited = _post(service, wording, "request_alpha", 1)
    assert status == 200
    assert edited["status"] == "draft_updated"
    assert edited["draft"]["request_id"] == "request_alpha"
    assert edited["draft"]["revision"] == 2
    assert edited["changed_fields"] == []
    assert edited["execution_authorized"] is False
    assert edited["executable"] is False
    fields = {item["name"]: item for item in edited["draft"]["fields"]}
    assert fields["length_min"]["value"] == 80
    assert fields["length_max"]["value"] == 120
    assert fields["target_id"]["value"] is None

    status, other = _post(service, SECOND, None, None)
    assert status == 200
    assert other["draft"]["request_id"] == "request_beta"
    assert other["draft"]["revision"] == 1

    listed_status, listed = handle_list_design_drafts(service)
    assert listed_status == 200
    assert listed["status"] == "drafts_recorded"
    assert listed["execution_authorized"] is False
    assert listed["executable"] is False
    assert [item["draft"]["request_id"] for item in listed["drafts"]] == ["request_beta", "request_alpha"]
    alpha = listed["drafts"][1]
    assert alpha["message"] == wording
    assert alpha["draft"]["revision"] == 2
    assert "journey" not in listed
    assert "plan" not in listed

    store.close()
    restarted = DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        store=SQLiteDesignDraftStore(database),
    )
    listed_status, listed = handle_list_design_drafts(restarted)
    assert listed_status == 200
    assert [(item["draft"]["request_id"], item["draft"]["revision"], item["message"]) for item in listed["drafts"]] == [
        ("request_beta", 1, SECOND),
        ("request_alpha", 2, wording),
    ]
    conflict_status, conflict = _post(restarted, STALE, "request_alpha", 1)
    assert conflict_status == 409
    assert conflict["status"] == "revision_conflict"
    assert conflict["draft"]["revision"] == 2
    read_status, body = handle_read_design_draft(restarted, "/api/design-draft?request_id=request_alpha")
    assert read_status == 200
    assert body["message"] == wording
    with sqlite3.connect(database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM design_request_drafts").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert count == 3
    assert tables == {"schema_meta", "design_request_drafts"}


def test_memory_store_is_the_default_and_creates_no_database(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert open_design_draft_store({}) is None
    assert list(tmp_path.iterdir()) == []
