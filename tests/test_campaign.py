import json
import sqlite3
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from cellertz.application.campaign import (
    SCHEMA_VERSION,
    CampaignError,
    CampaignService,
    CampaignStoreError,
    MemoryCampaignStore,
    SQLiteCampaignStore,
    handle_campaign,
    open_campaign_store,
)
from cellertz.application.conversation_transport import configured_handler


def test_a_campaign_link_is_optional_and_does_not_create_a_request() -> None:
    service = CampaignService()
    created = service.create("Binder study", "Design a binder.", "")
    missing, _ = handle_campaign(
        service,
        "POST",
        f"/api/campaigns/{created['campaign_id']}/design-requests",
        b'{"request_id":"request_one"}',
    )
    assert missing == 200
    linked = service.get(created["campaign_id"])
    assert linked is not None
    assert linked["design_request_ids"] == ["request_one"]
    again, body = handle_campaign(
        service,
        "POST",
        f"/api/campaigns/{created['campaign_id']}/design-requests",
        b'{"request_id":"request_one"}',
    )
    assert again == 200
    assert body["design_request_ids"] == ["request_one"]
    absent, payload = handle_campaign(
        service,
        "POST",
        "/api/campaigns/campaign_missing/design-requests",
        b'{"request_id":"request_one"}',
    )
    assert absent == 404
    assert payload["status"] == "campaign_absent"
    rejected, _ = handle_campaign(
        service,
        "POST",
        f"/api/campaigns/{created['campaign_id']}/design-requests",
        b'{"request_id":"request one","extra":1}',
    )
    assert rejected == 400
    assert service.list()[0]["name"] == "Binder study"


def test_create_records_only_the_supplied_campaign() -> None:
    service = CampaignService()
    created = service.create("  Binder study  ", "  Design a binder.  ", "  lab note  ")
    assert created["name"] == "Binder study"
    assert created["scientific_objective"] == "Design a binder."
    assert created["notes"] == "lab note"
    assert created["design_request_ids"] == []
    assert created["campaign_id"].startswith("campaign_")
    assert created["created_at"] == created["updated_at"]
    assert set(created) == {
        "campaign_id",
        "name",
        "scientific_objective",
        "notes",
        "created_at",
        "updated_at",
        "design_request_ids",
    }
    assert service.get(created["campaign_id"]) == created
    assert service.list() == [created]


@pytest.mark.parametrize(
    ("name", "objective", "notes"),
    [
        ("", "Design a binder.", ""),
        ("   ", "Design a binder.", None),
        ("Binder study", "", ""),
        ("Binder study", "   ", ""),
        (None, "Design a binder.", ""),
        ("Binder study", None, ""),
        ("Binder study", "Design a binder.", 12),
    ],
)
def test_create_rejects_missing_text(name: object, objective: object, notes: object) -> None:
    service = CampaignService()
    with pytest.raises(CampaignError):
        service.create(name, objective, notes)
    assert service.list() == []


def test_sqlite_reopen_returns_the_same_campaign(tmp_path: Path) -> None:
    path = tmp_path / "campaigns.sqlite"
    service = CampaignService(SQLiteCampaignStore(path))
    created = service.create("Binder study", "Design a binder.", "")
    service.close()
    again = CampaignService(SQLiteCampaignStore(path))
    try:
        assert again.get(created["campaign_id"]) == created
        assert again.list() == [created]
        with sqlite3.connect(path) as conn:
            version = conn.execute("SELECT version FROM schema_meta").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        again.close()


def test_journey_database_is_refused_and_left_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "design-journeys.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            CREATE TABLE design_journeys (
                journey_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                document TEXT NOT NULL
            );
            INSERT INTO schema_meta (version) VALUES (1);
            INSERT INTO design_journeys (journey_id, request_id, document)
            VALUES (
                'journey_historical',
                'request_historical',
                '{"journey_id":"journey_historical"}'
            );
            """
        )

    def snapshot() -> tuple[list[str], list[tuple[str, str, str]], int]:
        with sqlite3.connect(path) as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                )
            ]
            rows = conn.execute(
                "SELECT journey_id, request_id, document FROM design_journeys"
            ).fetchall()
            version = conn.execute("SELECT version FROM schema_meta").fetchone()[0]
        return tables, rows, version

    before = snapshot()
    with pytest.raises(CampaignStoreError) as caught:
        open_campaign_store(
            {
                "CELLERTZ_CAMPAIGN_STORE": "sqlite",
                "CELLERTZ_CAMPAIGN_DB": str(path),
            }
        )
    assert caught.value.code == "campaign_schema_inconsistent"
    assert snapshot() == before
    assert "campaigns" not in before[0]


def test_memory_store_is_the_default_and_sqlite_needs_a_path(tmp_path: Path) -> None:
    assert isinstance(open_campaign_store({}), MemoryCampaignStore)
    with pytest.raises(CampaignStoreError) as missing:
        open_campaign_store({"CELLERTZ_CAMPAIGN_STORE": "sqlite"})
    assert missing.value.code == "campaign_db_required"
    with pytest.raises(CampaignStoreError) as invalid:
        open_campaign_store({"CELLERTZ_CAMPAIGN_STORE": "postgres"})
    assert invalid.value.code == "invalid_campaign_store"
    opened = open_campaign_store(
        {
            "CELLERTZ_CAMPAIGN_STORE": "sqlite",
            "CELLERTZ_CAMPAIGN_DB": str(tmp_path / "fresh.sqlite"),
        }
    )
    opened.close()


def test_http_create_and_read_do_not_call_another_service(tmp_path: Path) -> None:
    class Unused:
        def __getattr__(self, name: str) -> None:
            raise AssertionError(name)

    service = CampaignService(SQLiteCampaignStore(tmp_path / "campaigns.sqlite"))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        configured_handler(Unused(), journeys=Unused(), campaigns=service),  # type: ignore[arg-type]
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    assert isinstance(port, int)
    try:
        created = _request(
            host,
            port,
            "POST",
            "/api/campaigns",
            {"name": "Binder study", "scientific_objective": "Design a binder.", "notes": ""},
        )
        assert created.status == 200
        document = json.loads(created.body)
        assert document["design_request_ids"] == []
        listed = _request(host, port, "GET", "/api/campaigns")
        assert json.loads(listed.body) == {"campaigns": [document]}
        one = _request(host, port, "GET", f"/api/campaigns/{document['campaign_id']}")
        assert json.loads(one.body) == document
        missing = _request(host, port, "GET", "/api/campaigns/campaign_missing")
        assert missing.status == 404
        assert json.loads(missing.body) == {"status": "campaign_absent"}
        rejected = _request(host, port, "POST", "/api/campaigns", {"name": " ", "scientific_objective": "x"})
        assert rejected.status == 400
    finally:
        server.shutdown()
        service.close()


def test_handler_rejects_a_campaign_write_without_a_store() -> None:
    assert handle_campaign(CampaignService(), "POST", "/api/campaigns", b"[]") == (
        400,
        {"status": "rejected"},
    )


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body


def _request(
    host: str,
    port: int,
    method: str,
    path: str,
    payload: dict[str, str] | None = None,
) -> _Response:
    body = b"" if payload is None else json.dumps(payload).encode()
    headers = {"Content-Length": str(len(body))}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    connection = HTTPConnection(host, port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return _Response(response.status, response.read().decode())
    finally:
        connection.close()
