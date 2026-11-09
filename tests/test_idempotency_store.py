import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cellertz.runs.idempotency import FileIdempotencyStore, IdempotencyRecord


def _record(
    tmp_path: Path,
    *,
    status: str,
    run_id: str = "protein_run_original",
    execution_id: str | None = "exec_original",
    output_dir: str | None = None,
    execution_status: str | None = None,
    scientific_status: str | None = None,
) -> IdempotencyRecord:
    return IdempotencyRecord(
        idempotency_key="cas-key",
        request_hash="hash-1",
        run_id=run_id,
        mode="real",
        created_at="2026-01-01T00:00:00+00:00",
        status=status,
        execution_id=execution_id,
        output_dir=output_dir or str(tmp_path / "out"),
        manifest_sha256="a" * 64,
        execution_status=execution_status,
        scientific_status=scientific_status,
    )


def test_old_records_without_optional_fields_still_load(tmp_path):
    store = FileIdempotencyStore(tmp_path / "idempotency")
    path = store._path("legacy-key")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "created_at": "2026-01-01T00:00:00+00:00",
                "idempotency_key": "legacy-key",
                "mode": "real",
                "request_hash": "legacy-hash",
                "run_id": "protein_run_legacy",
                "status": "rejected",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = store.get("legacy-key")
    assert loaded is not None
    assert loaded.status == "rejected"
    assert loaded.execution_id is None
    assert loaded.output_dir is None
    assert loaded.manifest_sha256 is None
    assert loaded.execution_status is None
    assert loaded.scientific_status is None
    assert loaded.scientific_request_sha256 is None


def test_try_transition_is_atomic_one_winner(tmp_path):
    store = FileIdempotencyStore(tmp_path / "idempotency")
    current = _record(
        tmp_path,
        status="execution_complete",
        execution_status="complete",
    )
    store.try_claim(current)
    barrier = threading.Barrier(2)

    def go(suffix: str):
        barrier.wait()
        return store.try_transition(
            current.idempotency_key,
            expected_status="execution_complete",
            record=current.evolve(
                status="scientific_pending",
                scientific_status="pending",
                run_id=f"protein_run_{suffix}",
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=5)
            for future in (
                pool.submit(go, "a"),
                pool.submit(go, "b"),
            )
        ]

    winners = [record for won, record in results if won]
    losers = [record for won, record in results if not won]
    assert len(winners) == 1
    assert len(losers) == 1
    assert winners[0].status == "scientific_pending"
    assert losers[0].status == "scientific_pending"
    assert store.get(current.idempotency_key).status == "scientific_pending"
    assert store.get(current.idempotency_key).scientific_status == "pending"


def test_try_transition_rejects_wrong_expected_status(tmp_path):
    store = FileIdempotencyStore(tmp_path / "idempotency")
    current = _record(
        tmp_path,
        status="scientific_pending",
        execution_status="complete",
        scientific_status="pending",
    )
    store.try_claim(current)
    won, loaded = store.try_transition(
        current.idempotency_key,
        expected_status="execution_complete",
        record=current.evolve(status="complete", scientific_status="complete"),
    )
    assert won is False
    assert loaded.status == "scientific_pending"
    assert store.get(current.idempotency_key).status == "scientific_pending"


def test_wait_until_settled_returns_on_execution_complete(tmp_path):
    store = FileIdempotencyStore(
        tmp_path / "idempotency",
        settle_timeout_s=30,
    )
    pending = _record(tmp_path, status="pending")
    store.try_claim(pending)
    entered = threading.Event()

    def wait():
        entered.set()
        return store.wait_until_settled(pending.idempotency_key)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(wait)
        assert entered.wait(timeout=5)
        store.put(
            pending.evolve(
                status="execution_complete",
                execution_status="complete",
            )
        )
        settled = future.result(timeout=2)

    assert settled is not None
    assert settled.status == "execution_complete"
    assert store.get(pending.idempotency_key).status == "execution_complete"
