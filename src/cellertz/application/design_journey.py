"""Durable professor journey from a design request through a separate launch.

Recorded-plan approval and execution authorization are different human
actions. Neither one starts compute by itself. Launch is a third action
and rechecks the saved plan before it calls the existing launcher.
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

from cellertz.application.generation_plan import _design_request_binding
from cellertz.application.apptainer_demonstration import (
    BOOTSTRAP_SEED_MAXIMUM,
    BOOTSTRAP_SEED_MINIMUM,
    explicit_gpu_index,
    gpu_launch_readiness,
    read_accepted_launch_evidence,
    read_terminal_attempt_evidence,
)
from cellertz.application.journey_specialist_progress import (
    SpecialistProgressError,
    apply_specialist_progress,
)
from cellertz.domains.protein_design.agents.supervisor import supported_professor_boundary
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    ATTEMPTED_TRAJECTORY_BUDGET_UPPER_BOUND,
    bounded_sequence_limits,
)
from cellertz.knowledge.models import (
    DuplicateKnowledgeId,
    InvalidKnowledgeDocument,
    KnowledgePathError,
)

SCHEMA_VERSION = 1
_REQUIRED_TABLES = frozenset({"schema_meta", "design_journeys"})
_LATER = ("scoring", "ranking", "critique", "prioritization")
_APPROVAL_KEYS = frozenset(
    {"plan_id", "revision", "digest", "reviewer_id", "reason", "idempotency_key"}
)
_LINK_KEYS = frozenset({"request_id", "request_revision", "plan_id", "revision", "digest"})
_RUN_KEYS = _LINK_KEYS | {"idempotency_key"}
_LAUNCH_KEYS = _APPROVAL_KEYS | {"gpu_index"}
_TRAJECTORY_BLOCK = (
    "Recorded plan approval does not launch generation. "
    "Execution authorization and launch are separate actions."
)
_RECORDED_PLAN_BLOCKER = "recorded_plan_approval_is_not_execution_authorization"
_SMOKE_ACK = (
    "technical-smoke-test no_filters. This filter policy is for the technical "
    "demonstration and does not establish scientific quality."
)
_ACK_CONFLICT = (
    "no_filters was not recorded. The technical demonstration "
    "acknowledgement is required. no_filters does not establish "
    "scientific quality."
)
_FILTER_ACK_BLOCKER = "filter_policy_acknowledgement_required"
_PRE_LAUNCH_REFUSALS = frozenset(
    {
        "demonstration_not_configured",
        "approval_required",
        "journey_execution_not_connected",
        "input_identity_unverified",
        "idempotency_conflict",
        "selected_gpu_missing",
        "nvidia_smi_unavailable",
        "gpu_not_ready",
    }
)


class DesignJourneyError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MemoryDesignJourneyStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._by_request: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, document: dict[str, Any]) -> None:
        saved = json.loads(json.dumps(document))
        with self._lock:
            self._items[saved["journey_id"]] = saved
            self._by_request[saved["request_id"]] = saved["journey_id"]

    def get(self, journey_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._items.get(journey_id)
        return None if found is None else json.loads(json.dumps(found))

    def for_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            journey_id = self._by_request.get(request_id)
            found = None if journey_id is None else self._items.get(journey_id)
        return None if found is None else json.loads(json.dumps(found))

    def documents(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._items.values())
        return [json.loads(json.dumps(item)) for item in items]

    def claim_where(
        self,
        predicate: Any,
        mutate: Any,
    ) -> dict[str, Any] | None:
        """Apply one mutation while the store lock is held. A second caller sees the result."""

        with self._lock:
            for item in self._items.values():
                if not predicate(item):
                    continue
                mutate(item)
                return json.loads(json.dumps(item))
        return None


class SQLiteDesignJourneyStore:
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
            raise DesignJourneyError("design_journey_schema_inconsistent")

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            CREATE TABLE design_journeys (
                journey_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                document TEXT NOT NULL
            );
            INSERT INTO schema_meta (version) VALUES (1);
            """
        )
        self._conn.commit()

    def put(self, document: dict[str, Any]) -> None:
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT INTO design_journeys (journey_id, request_id, document)
                VALUES (?, ?, ?)
                ON CONFLICT(journey_id) DO UPDATE SET
                    request_id = excluded.request_id,
                    document = excluded.document
                """,
                (str(document["journey_id"]), str(document["request_id"]), payload),
            )
            self._conn.commit()

    def get(self, journey_id: str) -> dict[str, Any] | None:
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT document FROM design_journeys WHERE journey_id = ?",
                (journey_id,),
            ).fetchone()
        return None if row is None else json.loads(row["document"])

    def for_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT document FROM design_journeys WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return None if row is None else json.loads(row["document"])

    def documents(self) -> list[dict[str, Any]]:
        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute(
                "SELECT document FROM design_journeys ORDER BY journey_id"
            ).fetchall()
        return [json.loads(row["document"]) for row in rows]

    def claim_where(self, predicate: Any, mutate: Any) -> dict[str, Any] | None:
        """Claim one document in a SQLite transaction. The in-process lock is not the guarantee."""

        with self._lock:
            assert self._conn is not None
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT journey_id, document FROM design_journeys ORDER BY journey_id"
                ).fetchall()
                for row in rows:
                    document = json.loads(row["document"])
                    if not predicate(document):
                        continue
                    mutate(document)
                    payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
                    self._conn.execute(
                        "UPDATE design_journeys SET document = ? WHERE journey_id = ?",
                        (payload, row["journey_id"]),
                    )
                    self._conn.commit()
                    return document
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def open_design_journey_store(
    environ: Mapping[str, str] | None = None,
) -> MemoryDesignJourneyStore | SQLiteDesignJourneyStore:
    env = os.environ if environ is None else environ
    backend = env.get("CELLERTZ_DESIGN_JOURNEY_STORE", "memory").strip().lower()
    if backend == "memory":
        return MemoryDesignJourneyStore()
    if backend != "sqlite":
        raise DesignJourneyError("invalid_design_journey_store")
    raw = env.get("CELLERTZ_DESIGN_JOURNEY_DB", "").strip()
    if not raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        raise DesignJourneyError("design_journey_db_required")
    return SQLiteDesignJourneyStore(Path(raw))


class DesignJourneyService:
    """One journey per design request. A repeated revision is not replayed."""

    def __init__(
        self,
        plans: Any,
        knowledge: Any | None = None,
        store: Any | None = None,
        attempt_root: Path | None = None,
        specialist_scorer: Any | None = None,
        results_catalog: Path | None = None,
        drafter: Any | None = None,
    ) -> None:
        self._plans = plans
        self._knowledge = knowledge
        self._store = store if store is not None else MemoryDesignJourneyStore()
        self._attempt_root = None if attempt_root is None else Path(attempt_root)
        self._specialist_scorer = specialist_scorer
        self._drafter = drafter
        self._results_catalog = None if results_catalog is None else Path(results_catalog)
        self._lock = threading.Lock()
        self._orchestrator: Any | None = None
        self._defer_generation = False
        self._orchestration_owner = f"journey-worker-{uuid4().hex}"

    def saved_documents(self) -> list[dict[str, Any]]:
        """Read stored journeys. This does not score, publish, or write."""

        reader = getattr(self._store, "documents", None)
        if not callable(reader):
            return []
        loaded = reader()
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    def open_or_get(
        self,
        request_id: str,
        request_revision: int,
        request_text: str,
        structured: object = None,
        acknowledgements: object = None,
    ) -> dict[str, Any]:
        _identity(request_id, request_revision, request_text)
        completion = _parse_completion(structured, acknowledgements)
        self._screen_sequence_limits(completion)
        with self._lock:
            existing = self._store.for_request(request_id)
            if isinstance(existing, dict) and existing.get("request_revision") == request_revision:
                return self._apply_completion(existing, request_text, completion)
            document = self._build(
                request_id,
                request_revision,
                request_text,
                existing,
                completion["plan_fields"],
                completion["acknowledgement"],
            )
            _store_completion(document, existing, completion)
            try:
                self._save(document)
            except sqlite3.IntegrityError:
                saved = self._store.for_request(request_id)
                if not isinstance(saved, dict):
                    raise
                return self._present(saved)
            return self._present(document)

    def list_requests(self) -> list[dict[str, Any]]:
        """Saved requests for a new browser. This does not approve or launch."""

        summaries = [
            _request_summary(item)
            for item in self._store.documents()
            if isinstance(item, dict)
        ]
        return sorted(
            summaries,
            key=lambda item: (str(item["updated_at"]), str(item["journey_id"])),
            reverse=True,
        )

    def link_saved_plan(self, payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        """Point one journey at an existing plan. This does not draft or approve."""

        parsed = _link_request(payload)
        if parsed is None:
            return 400, _link_rejected("rejected")
        published = _published_plan(self._plans, parsed["plan_id"])
        if not isinstance(published, dict) or published.get("plan_id") != parsed["plan_id"]:
            return 404, _link_rejected("plan_absent")
        if published.get("execution_connected") is True:
            return 409, _link_rejected("plan_identity_rejected")
        binding = _design_request_binding(str(published.get("conversation_id") or ""))
        if binding != (parsed["request_id"], parsed["request_revision"]):
            return 409, _link_rejected("plan_identity_rejected")
        if published.get("revision") != parsed["revision"] or published.get("digest") != parsed["digest"]:
            return 409, _link_rejected("plan_revision_conflict")
        fields = published.get("fields")
        request_text = fields.get("request_text") if isinstance(fields, dict) else None
        if not isinstance(request_text, str) or not request_text.strip():
            return 409, _link_rejected("plan_identity_rejected")
        with self._lock:
            existing = self._store.for_request(parsed["request_id"])
            if isinstance(existing, dict):
                if existing.get("request_revision") != parsed["request_revision"]:
                    return 409, _link_rejected("plan_identity_rejected")
                current_plan = existing.get("plan_id")
                if current_plan not in {None, parsed["plan_id"]}:
                    return 409, _link_rejected("plan_identity_rejected")
                if current_plan == parsed["plan_id"] and existing.get("plan_revision") == parsed["revision"]:
                    return 200, self._present(existing)
                updated = json.loads(json.dumps(existing))
                updated.pop("execution_review", None)
                updated.pop("completion", None)
                updated["plan_id"] = parsed["plan_id"]
                updated["plan_revision"] = parsed["revision"]
                updated["conversation_id"] = published.get("conversation_id")
                updated["gpu_execution"] = "disabled"
                updated["launches_compute"] = False
                updated["updated_at"] = datetime.now(UTC).isoformat()
                self._save(updated)
                return 200, self._present(updated)
            try:
                _identity(parsed["request_id"], parsed["request_revision"], request_text)
            except DesignJourneyError:
                return 409, _link_rejected("plan_identity_rejected")
            document = self._build(
                parsed["request_id"],
                parsed["request_revision"],
                request_text,
                None,
                saved_plan=published,
            )
            document["launches_compute"] = False
            try:
                self._save(document)
            except sqlite3.IntegrityError:
                saved = self._store.for_request(parsed["request_id"])
                if not isinstance(saved, dict):
                    raise
                return 200, self._present(saved)
            return 200, self._present(document)

    def run_design(self, payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        """Record one explicit run and launch that revision when the server permits it."""

        parsed = _run_request(payload)
        if parsed is None:
            return 400, _run_refused("rejected")
        published = _published_plan(self._plans, parsed["plan_id"])
        if not isinstance(published, dict) or published.get("plan_id") != parsed["plan_id"]:
            return 404, _run_refused("plan_absent")
        binding = _design_request_binding(str(published.get("conversation_id") or ""))
        if binding != (parsed["request_id"], parsed["request_revision"]):
            return 409, _run_refused("plan_identity_rejected")
        if published.get("revision") != parsed["revision"] or published.get("digest") != parsed["digest"]:
            return 409, _run_refused("plan_revision_conflict")
        configuration = _run_configuration(published, self._plans)
        if configuration is not None:
            return 409, _run_refused(configuration)
        if _server_capability(self._plans)["demonstration_configured"] is not True:
            return 409, _run_refused("execution_not_permitted")
        if self._attempt_root is None:
            return 409, _run_refused("attempt_directory_unconfigured")
        design_count = _saved_design_count(
            self._drafter, parsed["request_id"], parsed["request_revision"]
        )
        if design_count is None:
            return 409, _run_refused("configuration_incomplete")
        gpu = explicit_gpu_index((published.get("fields") or {}).get("gpu"))
        if gpu is None:
            return 409, _run_refused("gpu_not_selected")
        status, linked = self.link_saved_plan(
            {key: parsed[key] for key in ("request_id", "request_revision", "plan_id", "revision", "digest")}
        )
        if status != 200:
            return status, linked
        with self._lock:
            current = self._store.get(str(linked.get("journey_id")))
            if not isinstance(current, dict):
                return 404, _run_refused("journey_absent")
            stored = _stored_run(current, parsed)
            if stored is not None:
                presented = self._present(current)
                presented["run"] = _public_run(stored)
                return 200, presented
            attempt_id = f"attempt.{uuid4().hex[:8]}"
            attempt_directory = self._attempt_root / attempt_id
            running = _run_record(parsed, attempt_id, str(attempt_directory), gpu, "running", None, False)
            updated = json.loads(json.dumps(current))
            previous = updated.get("execution")
            if isinstance(previous, dict) and previous.get("source") == "run_design":
                history = updated.get("execution_history")
                if not isinstance(history, list):
                    history = []
                history.append(json.loads(json.dumps(previous)))
                updated["execution_history"] = history
            updated["execution"] = running
            self._save(updated)
        launch = getattr(self._plans, "launch_explicit_run", None)
        if not callable(launch):
            failed = _run_record(parsed, attempt_id, str(attempt_directory), gpu, "launch_unavailable", "launch_unavailable", False)
            updated["execution"] = failed
            self._save(updated)
            presented = self._present(updated)
            presented["run"] = _public_run(failed)
            return 409, presented
        try:
            reply = launch(
                plan_id=parsed["plan_id"],
                revision=parsed["revision"],
                digest=parsed["digest"],
                idempotency_key=parsed["idempotency_key"],
                gpu_index=gpu,
                attempt_id=attempt_id,
                attempt_directory=str(attempt_directory),
                n_designs=design_count,
            )
        except Exception as exc:  # noqa: BLE001 - the failed attempt is kept and is not launched again
            reply = {"status": "launcher_exception", "launched": False, "diagnosis": type(exc).__name__}
        if not isinstance(reply, dict):
            reply = {"status": "launch_failed", "launched": False}
        finished = _run_record(
            parsed,
            attempt_id,
            str(attempt_directory),
            gpu,
            str(reply.get("status") or "launch_failed"),
            str(reply.get("diagnosis") or reply.get("status") or "launch_failed"),
            reply.get("launched") is True,
        )
        log = reply.get("executor_log")
        if isinstance(log, str) and log.strip():
            finished["executor_log"] = log[:65536]
        updated["execution"] = finished
        self._save(updated)
        presented = self._present(updated)
        presented["run"] = _public_run(finished)
        if finished["launched"] is not True:
            presented["status"] = finished["status"]
            return 200, presented
        return 200, presented

    def results_for_request(self, request_id: str) -> dict[str, Any] | None:
        """Read attempts already linked to one request. This does not launch or score."""

        if not isinstance(request_id, str) or not request_id or "/" in request_id:
            return None
        found = self._store.for_request(request_id)
        if not isinstance(found, dict):
            return None
        return _request_results(found, self._attempt_root)

    def get_by_request(self, request_id: str) -> dict[str, Any] | None:
        if not isinstance(request_id, str) or not request_id or "/" in request_id:
            return None
        found = self._store.for_request(request_id)
        return None if not isinstance(found, dict) else self._present(found)

    def get(self, journey_id: str) -> dict[str, Any] | None:
        found = self._store.get(journey_id)
        return None if not isinstance(found, dict) else self._present(found)

    def close(self) -> None:
        close = getattr(self._store, "close", None)
        if callable(close):
            close()

    def record_approval(self, journey_id: str, payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        found = self._store.get(journey_id)
        if not isinstance(found, dict):
            return 404, {"status": "journey_absent"}
        parsed = _approval_request(payload)
        if parsed is None:
            return 400, _approval_rejected("rejected")
        if _active_round_cancelled(found):
            return 409, _round_action_refused()
        if parsed["plan_id"] != found.get("plan_id") or parsed["revision"] != found.get("plan_revision"):
            return 409, _approval_rejected("approval_rejected")
        if isinstance(found.get("execution_authorization"), dict):
            return 409, _approval_rejected("execution_authorization_is_separate")
        published = _published_plan(self._plans, str(found.get("plan_id")))
        if (
            published is None
            or published.get("digest") != parsed["digest"]
            or published.get("revision") != parsed["revision"]
            or published.get("execution_connected") is True
        ):
            return 409, _approval_rejected("approval_rejected")
        approve = getattr(self._plans, "approve", None)
        if not callable(approve):
            return 409, _approval_rejected("approval_rejected")
        result = approve(
            plan_id=parsed["plan_id"],
            revision=parsed["revision"],
            reviewer_id=parsed["reviewer_id"],
            reason=parsed["reason"],
            idempotency_key=(
                f"journey-approval:{parsed['plan_id']}:{parsed['revision']}:{parsed['digest']}"
            ),
            expected_digest=parsed["digest"],
            require_execution_readiness=False,
        )
        if isinstance(result, dict) and result.get("status") == "idempotency_conflict":
            return 409, _approval_rejected("idempotency_conflict")
        if not isinstance(result, dict) or result.get("approved") is not True:
            return 409, _approval_rejected("approval_rejected")
        if result.get("execution_connected") is True or result.get("run_id"):
            return 409, _approval_rejected("approval_rejected")
        recorded = result.get("approval")
        stored = found.get("approval")
        if (
            isinstance(recorded, dict)
            and isinstance(stored, dict)
            and stored.get("digest") == recorded.get("digest")
            and stored.get("reviewer_id") == recorded.get("reviewer_id")
            and stored.get("reason") == recorded.get("reason")
        ):
            return 200, self._present(found)
        updated = _with_approval(found, result)
        self._save(updated)
        return 200, self._present(updated)

    def authorize_execution(
        self, journey_id: str, payload: Mapping[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        found = self._store.get(journey_id)
        if not isinstance(found, dict):
            return 404, {"status": "journey_absent"}
        parsed = _approval_request(payload)
        if parsed is None:
            return 400, _approval_rejected("rejected")
        if _active_round_cancelled(found):
            return 409, _round_action_refused()
        if parsed["plan_id"] != found.get("plan_id") or parsed["revision"] != found.get("plan_revision"):
            return 409, _approval_rejected("revision_mismatch")
        published = _published_plan(self._plans, str(found.get("plan_id")))
        if published is None or published.get("digest") != parsed["digest"]:
            return 409, _approval_rejected("revision_mismatch")
        if published.get("revision") != parsed["revision"]:
            return 409, _approval_rejected("revision_mismatch")
        acknowledgement = evaluate_filter_acknowledgement(published)
        if acknowledgement["blocker"]:
            return 409, _execution_refused("execution_not_ready", [acknowledgement["blocker"]])
        existing = found.get("execution_authorization")
        if isinstance(existing, dict) and existing.get("digest") == parsed["digest"]:
            same = (
                existing.get("reviewer_id") == parsed["reviewer_id"]
                and existing.get("reason") == parsed["reason"]
            )
            if same:
                return 200, self._present(found)
            return 409, _approval_rejected("idempotency_conflict")
        if not _server_capability(self._plans)["demonstration_configured"]:
            return 409, _execution_refused(
                "demonstration_not_configured",
                ["demonstration_not_configured"],
            )
        approve = getattr(self._plans, "approve", None)
        if not callable(approve):
            return 409, _execution_refused("approval_rejected", ["execution_authorization_unavailable"])
        result = approve(
            plan_id=parsed["plan_id"],
            revision=parsed["revision"],
            reviewer_id=parsed["reviewer_id"],
            reason=parsed["reason"],
            idempotency_key=(
                f"journey-execution-authorization:{parsed['plan_id']}:"
                f"{parsed['revision']}:{parsed['digest']}"
            ),
            expected_digest=parsed["digest"],
            require_execution_readiness=True,
        )
        if not isinstance(result, dict) or result.get("approved") is not True:
            review = self._execution_review(found)
            return 409, _execution_refused("execution_not_ready", review["unresolved_blockers"])
        granted = result.get("approval")
        if not isinstance(granted, dict) or granted.get("scope") == "recorded_plan":
            return 409, _execution_refused("recorded_plan_not_execution_authorization", [_RECORDED_PLAN_BLOCKER])
        updated = _with_execution_authorization(found, result)
        self._save(updated)
        return 200, self._present(updated)

    def launch_execution(self, journey_id: str, payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        found = self._store.get(journey_id)
        if not isinstance(found, dict):
            return 404, {"status": "journey_absent"}
        parsed = _launch_request(payload)
        if parsed is None:
            return 400, _approval_rejected("rejected")
        if _active_round_cancelled(found):
            return 409, _round_action_refused()
        authorization = found.get("execution_authorization")
        if not isinstance(authorization, dict):
            return 409, _execution_refused("execution_authorization_required", ["execution_authorization_required"])
        if (
            parsed["plan_id"] != found.get("plan_id")
            or parsed["revision"] != authorization.get("revision")
            or parsed["digest"] != authorization.get("digest")
            or parsed["reviewer_id"] != authorization.get("reviewer_id")
        ):
            return 409, _execution_refused("revision_mismatch", ["revision_mismatch"])
        published = _published_plan(self._plans, str(found.get("plan_id")))
        if (
            published is None
            or published.get("revision") != parsed["revision"]
            or published.get("digest") != parsed["digest"]
            or published.get("input_registration") != "verified"
            or published.get("questions")
        ):
            blockers = ["input_identity_rejected"]
            if published is not None and published.get("questions"):
                blockers = ["open_questions"]
            if published is not None and published.get("digest") != parsed["digest"]:
                blockers = ["revision_mismatch"]
            return 409, _execution_refused("execution_not_ready", blockers)
        acknowledgement = evaluate_filter_acknowledgement(published)
        if acknowledgement["blocker"]:
            return 409, _execution_refused("execution_not_ready", [acknowledgement["blocker"]])
        approval = published.get("approval")
        if isinstance(approval, dict) and approval.get("scope") == "recorded_plan":
            return 409, _execution_refused("recorded_plan_not_execution_authorization", [_RECORDED_PLAN_BLOCKER])
        current = found.get("execution")
        if isinstance(current, dict) and current.get("consumed") is True:
            if current.get("idempotency_key") == parsed["idempotency_key"]:
                return 200, self._present(found)
            return 409, _execution_refused("duplicate_launch", ["duplicate_launch"])
        if self._attempt_root is None:
            return 409, _execution_refused("attempt_directory_unconfigured", ["attempt_directory_unconfigured"])
        launch = getattr(self._plans, "launch_apptainer_demonstration", None)
        if not callable(launch):
            return 409, _execution_refused("demonstration_not_configured", ["demonstration_not_configured"])
        assigned = _gpu_assignment_index(_plan_gpu(published))
        if assigned is None or assigned != parsed["gpu_index"]:
            return 409, _execution_refused("gpu_index_mismatch", ["gpu_index_mismatch"])
        blocker = _reviewed_input_blocker(published, self._plans)
        if blocker is not None:
            updated = _without_execution_authorization(found)
            self._save(updated)
            presented = self._present(updated)
            presented["status"] = blocker
            presented["unresolved_blockers"] = [blocker]
            return 409, presented
        inventory, gpu_report = self._gpu_inventory(parsed["gpu_index"])
        if inventory is not None:
            return 409, _execution_refused(inventory, [inventory])
        with self._lock:
            latest = self._store.get(journey_id)
            if not isinstance(latest, dict):
                return 404, {"status": "journey_absent"}
            raced = latest.get("execution")
            if isinstance(raced, dict) and raced.get("consumed") is True:
                if raced.get("idempotency_key") == parsed["idempotency_key"]:
                    return 200, self._present(latest)
                return 409, _execution_refused("duplicate_launch", ["duplicate_launch"])
            attempt_id = f"attempt.{uuid4().hex[:8]}"
            attempt_directory = self._attempt_root / attempt_id
            self._attempt_root.mkdir(parents=True, exist_ok=True)
            running = _execution_record(
                latest,
                parsed,
                attempt_id=attempt_id,
                attempt_directory=str(attempt_directory),
                status="running",
                launched=False,
                terminal=False,
                progress=[{"status": "running"}],
            )
            running["consumed"] = True
            if isinstance(gpu_report, dict):
                running["gpu_observation"] = {
                    "occupancy": gpu_report.get("occupancy"),
                    "compute_apps": gpu_report.get("compute_apps"),
                    "memory": gpu_report.get("memory"),
                }
            prepared = json.loads(json.dumps(latest))
            prepared["execution"] = running
            if self._defer_generation:
                from cellertz.application.journey_orchestration import new_dispatch

                prepared["post_generation"] = new_dispatch(
                    parsed,
                    attempt_id=attempt_id,
                    attempt_directory=str(attempt_directory),
                )
                if not isinstance(prepared.get("rounds"), list) or not prepared.get("rounds"):
                    prepared["rounds"] = _stored_rounds(prepared, self._plans)
                if not isinstance(prepared.get("active_round_id"), str):
                    prepared["active_round_id"] = prepared["rounds"][-1]["round_id"]
                _copy_dispatch(prepared)
            # One commit stores the attempt identity. The GPU call is outside this lock.
            self._save(prepared)
        if self._defer_generation:
            wake = getattr(self._orchestrator, "wake", None)
            if callable(wake):
                wake()
            return 200, self._present(prepared)
        try:
            reply = launch(
                plan_id=parsed["plan_id"],
                revision=parsed["revision"],
                reviewer_id=parsed["reviewer_id"],
                reason=parsed["reason"],
                idempotency_key=parsed["idempotency_key"],
                gpu_index=parsed["gpu_index"],
                attempt_id=attempt_id,
                attempt_directory=str(attempt_directory),
            )
        except Exception as exc:  # noqa: BLE001 - a launcher failure must be recorded, not left running
            reply = {"status": "launcher_exception", "launched": False, "exception_type": type(exc).__name__}
        if not isinstance(reply, dict):
            reply = {"status": "launch_failed", "launched": False}
        reply = _with_saved_evidence(reply, attempt_directory, attempt_id)
        finished = _execution_from_reply(prepared, running, reply)
        if finished["consumed"] is True:
            updated = _with_launch(prepared, finished)
        else:
            updated = prepared
            updated["execution"] = finished
        self._save(updated)
        if finished["consumed"] is not True:
            presented = self._present(updated)
            presented["status"] = finished["status"]
            presented["unresolved_blockers"] = [str(finished["status"])]
            return 409, presented
        return 200, self._present(updated)

    def attach_orchestrator(self, orchestrator: Any) -> None:
        """Return new launches before GPU completion and let the worker continue them."""

        self._orchestrator = orchestrator
        self._defer_generation = True

    def continue_post_generation(self) -> bool:
        """Claim one queued automatic launch and run it. Historical rounds are skipped."""

        from cellertz.application.journey_orchestration import claim_dispatch, dispatch_is_queued

        claim = getattr(self._store, "claim_where", None)
        if not callable(claim):
            return False
        claimed = claim(dispatch_is_queued, lambda document: claim_dispatch(document, self._orchestration_owner))
        if not isinstance(claimed, dict):
            return False
        self._run_saved_launch(claimed)
        return True

    def recover_automatic_dispatches(self) -> None:
        """Reconcile saved dispatches. This does not call the launcher."""

        from cellertz.application.journey_orchestration import (
            needs_specialist_follow_up,
            recover_document,
        )

        for document in self.saved_documents():
            journey_id = document.get("journey_id")
            if not isinstance(journey_id, str):
                continue
            updated = recover_document(json.loads(json.dumps(document)))
            if updated is not None:
                self._save(updated)
                continue
            if needs_specialist_follow_up(document):
                self._follow_automatic(journey_id)

    def _run_saved_launch(self, document: dict[str, Any]) -> None:
        from cellertz.application.journey_orchestration import follow_up_decision

        execution = document.get("execution")
        dispatch = document.get("post_generation")
        request = dispatch.get("launch_request") if isinstance(dispatch, dict) else None
        if not isinstance(execution, dict) or not isinstance(request, dict):
            return
        if execution.get("terminal") is True:
            self._follow_automatic(str(document.get("journey_id")))
            return
        launch = getattr(self._plans, "launch_apptainer_demonstration", None)
        attempt_id = execution.get("attempt_id")
        directory = execution.get("attempt_directory")
        if not callable(launch) or not isinstance(attempt_id, str) or not isinstance(directory, str):
            return
        attempt_directory = Path(directory)
        try:
            reply = launch(
                plan_id=request["plan_id"],
                revision=request["revision"],
                reviewer_id=request["reviewer_id"],
                reason=request["reason"],
                idempotency_key=request["idempotency_key"],
                gpu_index=request["gpu_index"],
                attempt_id=attempt_id,
                attempt_directory=directory,
            )
        except Exception as exc:  # noqa: BLE001 - the saved attempt is not launched again
            reply = {"status": "launcher_exception", "launched": False, "exception_type": type(exc).__name__}
        if not isinstance(reply, dict):
            reply = {"status": "launch_failed", "launched": False}
        reply = _with_saved_evidence(reply, attempt_directory, attempt_id)
        current = self._store.get(str(document.get("journey_id")))
        if not isinstance(current, dict):
            return
        running = current.get("execution")
        if not isinstance(running, dict):
            running = execution
        finished = _execution_from_reply(current, running, reply)
        if finished["consumed"] is True:
            updated = _with_launch(current, finished)
        else:
            updated = current
            updated["execution"] = finished
            from cellertz.application.journey_orchestration import finish_dispatch_record

            finish_dispatch_record(
                updated,
                "refused",
                "generation",
                "The launcher refused before consumption. Generation was not retried automatically.",
            )
        if isinstance(updated.get("post_generation"), dict) and finished["consumed"] is True:
            decision = follow_up_decision(finished)
            if decision == "running":
                updated["post_generation"]["status"] = "running"
            _copy_dispatch(updated)
        self._save(updated)
        if finished["consumed"] is True and follow_up_decision(finished) != "running":
            self._follow_automatic(str(document.get("journey_id")))

    def _follow_automatic(self, journey_id: str) -> None:
        from cellertz.application.journey_orchestration import (
            finish_dispatch_record,
            follow_up_decision,
            specialist_dispatch_status,
        )

        document = self._store.get(journey_id)
        if not isinstance(document, dict):
            return
        dispatch = document.get("post_generation")
        execution = document.get("execution")
        if not isinstance(dispatch, dict) or dispatch.get("automatic") is not True:
            return
        if dispatch.get("status") in {"completed", "blocked", "failed", "unresolved", "refused"}:
            return
        if not isinstance(execution, dict):
            return
        decision = follow_up_decision(execution)
        if decision == "specialists":
            round_id = document.get("active_round_id")
            if isinstance(round_id, str):
                self.record_specialist_progress(journey_id, round_id)
            document = self._store.get(journey_id)
            if not isinstance(document, dict):
                return
            status, phase = specialist_dispatch_status(document)
            finish_dispatch_record(document, status, phase, _dispatch_detail(status, phase))
            self._save(document)
            return
        if decision in {"unresolved", "failed"}:
            finish_dispatch_record(document, decision, "generation", _dispatch_detail(decision, "generation"))
            self._save(document)

    def reconcile_execution(self, journey_id: str) -> tuple[int, dict[str, Any]]:
        """Record the saved attempt evidence. This does not launch or edit the plan."""

        found = self._store.get(journey_id)
        if not isinstance(found, dict):
            return 404, {"status": "journey_absent"}
        execution = found.get("execution")
        if not isinstance(execution, dict) or execution.get("consumed") is not True:
            return 409, _execution_refused("execution_not_consumed", ["execution_not_consumed"])
        if execution.get("terminal") is True and execution.get("status") == "budget_exhausted":
            return 200, self._present(found)
        attempt_id = execution.get("attempt_id")
        attempt_directory = execution.get("attempt_directory")
        if not isinstance(attempt_id, str) or not isinstance(attempt_directory, str):
            return 409, _execution_refused("attempt_directory_unconfigured", ["attempt_directory_unconfigured"])
        evidence = read_terminal_attempt_evidence(Path(attempt_directory), attempt_id)
        reply = _with_saved_evidence(
            {"status": "execution_unresolved", "launched": False},
            Path(attempt_directory),
            attempt_id,
            evidence,
        )
        finished = _execution_from_reply(found, execution, reply)
        updated = _with_launch(found, finished)
        self._save(updated)
        return 200, self._present(updated)

    def prepare_next_round(
        self, journey_id: str, parent_round_id: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Open one child round. Repeated calls for the same parent do not draft again."""

        with self._lock:
            found = self._store.get(journey_id)
            if not isinstance(found, dict):
                return 404, {"status": "journey_absent"}
            updated = json.loads(json.dumps(found))
            updated.pop("execution_review", None)
            updated.pop("completion", None)
            rounds = _stored_rounds(updated, self._plans)
            parent = _find_round(rounds, parent_round_id) if parent_round_id else rounds[-1]
            if parent is None:
                return 404, {"status": "round_absent", "detail": "The round was not found."}
            if parent.get("status") == "cancelled":
                return 409, _round_action_refused()
            active_child = _active_child(rounds, parent)
            if active_child is not None:
                return 200, self._present(found)
            ordinals = [
                int(item["ordinal"])
                for item in rounds
                if isinstance(item.get("ordinal"), int) and not isinstance(item.get("ordinal"), bool)
            ]
            child_ordinal = (max(ordinals) if ordinals else int(parent["ordinal"])) + 1
            child_id = f"round_{journey_id}_{child_ordinal}"
            published = _published_plan(self._plans, str(parent.get("plan_id") or ""))
            fields = published.get("fields") if isinstance(published, dict) else {}
            if not isinstance(fields, dict):
                fields = {}
            proposals = _copied_proposals(fields, str(parent["round_id"]), parent.get("plan_revision"))
            request_text = fields.get("request_text") if isinstance(fields.get("request_text"), str) else ""
            if not request_text:
                request_text = str(updated.get("request_id") or "")
            conversation_id = f"design-journey:{updated['request_id']}:round:{child_id}"
            explicit = _proposal_draft(proposals, fields)
            plan, error = _draft(self._plans, conversation_id, request_text, explicit, None)
            child_ids = [
                item for item in parent.get("child_round_ids") or [] if isinstance(item, str)
            ]
            previous_child = parent.get("child_round_id")
            if isinstance(previous_child, str) and previous_child not in child_ids:
                child_ids.append(previous_child)
            child_ids.append(child_id)
            parent["child_round_ids"] = child_ids
            parent["child_round_id"] = child_id
            _keep_parent_decision(parent, updated.get("approval"), "approval")
            _keep_parent_decision(parent, updated.get("execution_authorization"), "execution_authorization")
            _keep_parent_gpu(parent, self._plans)
            child = {
                "round_id": child_id,
                "parent_round_id": parent["round_id"],
                "child_round_id": None,
                "ordinal": child_ordinal,
                "plan_id": None if plan is None else plan.get("plan_id"),
                "plan_revision": None if plan is None else plan.get("revision"),
                "digest": None if plan is None else plan.get("digest"),
                "approval": None,
                "approval_history": [],
                "execution_authorization": None,
                "execution": None,
                "status": "open",
                "cancelled_at": None,
                "parameters": proposals,
                "target": fields.get("target_identity") if isinstance(fields.get("target_identity"), str) else None,
                "approved_gpu": None,
                "sequence_limits": _same_sequence_limits(fields.get("sequence_limits")),
            }
            for item in rounds:
                if item.get("round_id") == parent["round_id"]:
                    item.update(parent)
            rounds.append(child)
            updated["rounds"] = rounds
            updated["active_round_id"] = child_id
            updated["conversation_id"] = conversation_id
            updated["plan_id"] = child["plan_id"]
            updated["plan_revision"] = child["plan_revision"]
            updated["approved"] = False
            updated["approval_history"] = []
            updated.pop("approval", None)
            updated.pop("execution_authorization", None)
            updated.pop("execution", None)
            updated["gpu_execution"] = "disabled"
            updated["launches_compute"] = False
            updated["scientific_validation_established"] = False
            updated["stages"] = _stages_for_new_round(updated, plan, error)
            events = list(updated.get("events") or [])
            events.append(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "kind": "round_prepared",
                    "parent_round_id": parent["round_id"],
                    "round_id": child_id,
                    "detail": (
                        "A new round was prepared from the previous round. "
                        "The previous round was not changed and generation was not launched."
                    ),
                }
            )
            updated["events"] = events
            updated["updated_at"] = datetime.now(UTC).isoformat()
            self._save(updated)
            return 200, self._present(updated)

    def cancel_round(self, journey_id: str, round_id: str) -> tuple[int, dict[str, Any]]:
        """Cancel one unlaunched round. A consumed launch is left running."""

        with self._lock:
            found = self._store.get(journey_id)
            if not isinstance(found, dict):
                return 404, {"status": "journey_absent", "detail": "The design journey was not recorded."}
            updated = json.loads(json.dumps(found))
            updated.pop("execution_review", None)
            updated.pop("completion", None)
            rounds = _stored_rounds(updated, self._plans)
            target = _find_round(rounds, round_id)
            if target is None:
                return 404, {"status": "round_absent", "detail": "The round was not found."}
            if target.get("status") == "cancelled" and isinstance(target.get("cancelled_at"), str):
                return 200, self._present(found)
            execution = _execution_for_round(updated, target)
            if isinstance(execution, dict) and execution.get("consumed") is True:
                return 409, _round_cancel_refused()
            cancelled_at = datetime.now(UTC).isoformat()
            target["status"] = "cancelled"
            target["cancelled_at"] = cancelled_at
            updated["rounds"] = rounds
            if not updated.get("active_round_id"):
                updated["active_round_id"] = rounds[-1]["round_id"]
            events = list(updated.get("events") or [])
            events.append(
                {
                    "at": cancelled_at,
                    "kind": "round_cancelled",
                    "round_id": round_id,
                    "parent_round_id": target.get("parent_round_id"),
                    "detail": (
                        "This round was cancelled before launch. The round, its plan revisions, "
                        "and its parent were kept. Generation was not launched."
                    ),
                }
            )
            updated["events"] = events
            updated["updated_at"] = cancelled_at
            self._save(updated)
            return 200, self._present(updated)

    def record_specialist_progress(
        self, journey_id: str, round_id: str
    ) -> tuple[int, dict[str, Any]]:
        """Persist specialist stages from the saved execution. This does not launch."""

        with self._lock:
            found = self._store.get(journey_id)
            if not isinstance(found, dict):
                return 404, {"status": "journey_absent"}
            updated = json.loads(json.dumps(found))
            updated.pop("execution_review", None)
            updated.pop("completion", None)
            if not isinstance(updated.get("rounds"), list) or not updated.get("rounds"):
                updated["rounds"] = _stored_rounds(updated, self._plans)
            target = _find_round(updated["rounds"], round_id)
            if target is None:
                return 404, {"status": "round_absent", "detail": "The round was not found."}
            try:
                changed = apply_specialist_progress(
                    updated,
                    round_id,
                    scorer=self._specialist_scorer,
                    save=self._save,
                    catalog_root=self._results_catalog,
                )
            except SpecialistProgressError as exc:
                return exc.status, exc.body
            if changed:
                updated["updated_at"] = datetime.now(UTC).isoformat()
                self._save(updated)
            return 200, self._present(updated)

    def record_scoring_audit(
        self,
        journey_id: str,
        attempt_id: str,
        *,
        resume_interrupted: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """Recalculate one saved attempt. This does not launch or replace specialist stages."""

        from cellertz.application.scoring_audit import apply_scoring_audit

        if not isinstance(attempt_id, str) or not attempt_id or "/" in attempt_id:
            return 400, {"status": "attempt_identity_rejected"}
        with self._lock:
            found = self._store.get(journey_id)
            if not isinstance(found, dict):
                return 404, {"status": "journey_absent"}
            updated = json.loads(json.dumps(found))
            before = json.dumps(updated, sort_keys=True)
            status, payload = apply_scoring_audit(
                updated,
                attempt_id,
                resume_interrupted=resume_interrupted,
                save=self._save,
            )
            if json.dumps(updated, sort_keys=True) != before:
                updated["updated_at"] = datetime.now(UTC).isoformat()
                self._save(updated)
            return status, payload

    def recover_accepted_launch(self, journey_id: str, attempt_id: str) -> tuple[int, dict[str, Any]]:
        """Correct one verified launch whose candidate was not saved.

        The original execution projection and blocked specialist evidence stay
        in an explicit correction record. This does not launch or score.
        """

        if not isinstance(attempt_id, str) or not attempt_id or "/" in attempt_id:
            return 400, {"status": "attempt_identity_rejected"}
        with self._lock:
            found = self._store.get(journey_id)
            if not isinstance(found, dict):
                return 404, {"status": "journey_absent"}
            updated = json.loads(json.dumps(found))
            updated.pop("execution_review", None)
            updated.pop("completion", None)
            if not isinstance(updated.get("rounds"), list) or not updated.get("rounds"):
                updated["rounds"] = _stored_rounds(updated, self._plans)
            target = _round_for_attempt(updated, attempt_id)
            if target is None:
                return 404, {"status": "attempt_absent", "detail": "That attempt is not on this journey."}
            execution = _execution_for_round(updated, target)
            if not isinstance(execution, dict) or execution.get("consumed") is not True:
                return 409, _execution_refused("execution_not_consumed", ["execution_not_consumed"])
            if execution.get("attempt_id") != attempt_id:
                return 404, {"status": "attempt_absent"}
            directory = execution.get("attempt_directory")
            if not isinstance(directory, str):
                return 409, _execution_refused("attempt_directory_unconfigured", ["attempt_directory_unconfigured"])
            if _correction_recorded(target, execution):
                presented = self._present(found)
                presented["accepted_launch_recovery"] = {
                    "round_id": target.get("round_id"),
                    "attempt_id": attempt_id,
                    "idempotent": True,
                }
                return 200, presented
            if execution.get("status") != "verified" or execution.get("accepted_candidate") is True:
                return 409, {
                    "status": "accepted_launch_recovery_refused",
                    "detail": "Recovery applies only to a verified launch saved without a candidate.",
                }
            progress = target.get("specialist_progress")
            if isinstance(progress, dict) and progress.get("status") == "running":
                return 409, {
                    "status": "specialist_progress_running",
                    "detail": "Specialist processing is still recorded as running. It was not replayed.",
                }
            evidence = read_accepted_launch_evidence(Path(directory), attempt_id)
            if evidence.get("accepted") is not True:
                return 409, {
                    "status": "accepted_launch_not_validated",
                    "reasons": evidence.get("reasons") or [],
                    "detail": "The saved review, candidate, structures, and ledger did not validate. Nothing was rewritten.",
                }
            _archive_blocked_specialists(updated, target, execution)
            corrected = _corrected_execution(execution, evidence)
            if updated.get("active_round_id") in {None, target.get("round_id")}:
                updated["execution"] = corrected
            target["execution"] = json.loads(json.dumps(corrected))
            target["plan_id"] = corrected["plan_id"]
            target["plan_revision"] = corrected["revision"]
            target["digest"] = corrected["digest"]
            updated["scientific_validation_established"] = False
            updated["updated_at"] = datetime.now(UTC).isoformat()
            self._save(updated)
            presented = self._present(updated)
            presented["accepted_launch_recovery"] = {
                "round_id": target.get("round_id"),
                "attempt_id": attempt_id,
                "idempotent": False,
            }
            return 200, presented

    def _gpu_inventory(self, gpu_index: int) -> tuple[str | None, dict[str, Any] | None]:
        """Return a blocker and the informative GPU reading.

        A shared GPU is not a blocker. A missing or unreadable index is.
        """

        demonstration = getattr(self._plans, "_demonstration", None)
        if demonstration is None or not hasattr(demonstration, "inert_outputs"):
            return None, None
        if getattr(demonstration, "inert_outputs", False) is True:
            return None, None
        readiness = getattr(demonstration, "gpu_readiness", None) or gpu_launch_readiness
        if not callable(readiness):
            return None, None
        report = readiness(gpu_index)
        if not isinstance(report, dict):
            return "gpu_not_ready", None
        status = report.get("status")
        if status in {"launch_ready", "selected_gpu_busy"} or report.get("launch_ready") is True:
            return None, report
        if status in {"selected_gpu_missing", "nvidia_smi_unavailable", "gpu_not_ready"}:
            return str(status), report
        return "gpu_not_ready", report

    def _save(self, document: dict[str, Any]) -> None:
        stored = json.loads(json.dumps(document))
        stored.pop("execution_review", None)
        stored.pop("completion", None)
        stored.pop("live_output", None)
        self._store.put(stored)

    def _present(self, document: dict[str, Any]) -> dict[str, Any]:
        presented = json.loads(json.dumps(document))
        presented.pop("execution_review", None)
        presented.pop("completion", None)
        published = _published_plan(self._plans, str(presented.get("plan_id") or ""))
        evidence = evaluate_filter_acknowledgement(published)
        current_conflicts, historical_conflicts = _split_filter_conflicts(
            _journey_conflict_items(presented), evidence
        )
        presented["conflicts"] = current_conflicts
        presented["historical_conflicts"] = historical_conflicts
        presented["acknowledgement_evidence"] = evidence
        _apply_request_evidence(presented, current_conflicts, historical_conflicts, evidence)
        presented["execution_review"] = self._execution_review(presented)
        presented["rounds"] = _round_cards(presented, self._plans)
        _present_authorization_stage(presented)
        facts = {}
        recorded = getattr(self._plans, "recorded_input_facts", None)
        if callable(recorded):
            found = recorded()
            if isinstance(found, dict):
                facts = found
        presented["completion"] = {
            "request_fields": presented.get("request_fields") or {},
            "conflicts": current_conflicts,
            "historical_conflicts": historical_conflicts,
            "acknowledgement_evidence": evidence,
            "approval_history": presented.get("approval_history") or [],
            "technical_demonstration": _technical_demonstration(facts),
        }
        if "approval_history" not in presented:
            presented["approval_history"] = []
        presented.pop("live_output", None)
        live = _live_output(self._attempt_root, presented.get("execution"))
        if live:
            presented["live_output"] = live
        return presented

    def _execution_review(self, document: Mapping[str, Any]) -> dict[str, Any]:
        published = _published_plan(self._plans, str(document.get("plan_id") or ""))
        authorization = document.get("execution_authorization")
        authorized = bool(
            isinstance(authorization, dict)
            and authorization.get("revision") == document.get("plan_revision")
            and authorization.get("digest")
        )
        capability = _server_capability(self._plans)
        if published is None:
            return _review_body(document, None, ["plan_unavailable"], authorized, capability, False)
        blockers: list[str] = []
        questions = published.get("questions")
        if isinstance(questions, list) and questions:
            blockers.append("open_questions")
        acknowledgement = evaluate_filter_acknowledgement(published)
        if acknowledgement["blocker"]:
            blockers.append(acknowledgement["blocker"])
        registration = published.get("input_registration")
        if registration != "verified":
            blockers.append(str(registration or "input_identity_unverified"))
        approval = published.get("approval")
        if authorized and _reviewed_input_blocker(published, self._plans) is not None:
            authorized = False
        recorded_plan = (
            isinstance(approval, dict)
            and approval.get("scope") == "recorded_plan"
            and approval.get("revision") == published.get("revision")
            and approval.get("digest") == published.get("digest")
        )
        return _review_body(document, published, blockers, authorized, capability, recorded_plan)

    def _apply_completion(
        self,
        existing: dict[str, Any],
        request_text: str,
        completion: dict[str, Any],
    ) -> dict[str, Any]:
        explicit = completion["plan_fields"]
        acknowledgement = completion["acknowledgement"]
        published = _published_plan(self._plans, str(existing.get("plan_id") or ""))
        if evaluate_filter_acknowledgement(published)["recorded"] is True:
            retained = [item for item in completion["conflicts"] if not _is_missing_ack_conflict(item)]
            removed = len(retained) != len(completion["conflicts"])
            completion["conflicts"] = retained
            if removed and completion["plan_fields"].get("filter_policy") in (None, "no_filters"):
                completion["touched"].discard("filter_policy")
        plan_same = not explicit or _explicit_already_recorded(self._plans, existing, explicit)
        if plan_same and acknowledgement and not _acknowledgement_recorded(published, acknowledgement):
            plan_same = False
        request_fields = _merge_maps(existing.get("request_fields"), completion["request_fields"])
        conflicts = _merge_conflicts(
            existing.get("conflicts"), completion["conflicts"], completion["touched"]
        )
        request_same = request_fields == (existing.get("request_fields") or {})
        conflicts_same = conflicts == (existing.get("conflicts") or [])
        execution = existing.get("execution")
        if (
            isinstance(execution, dict)
            and execution.get("consumed") is True
            and not (plan_same and request_same and conflicts_same)
        ):
            raise DesignJourneyError("round_plan_locked")
        if plan_same and request_same and conflicts_same:
            return self._present(existing)
        if plan_same:
            updated = json.loads(json.dumps(existing))
            updated.pop("execution_review", None)
            updated.pop("completion", None)
            _store_completion(updated, existing, {**completion, "request_fields": request_fields, "conflicts": conflicts})
            updated["updated_at"] = datetime.now(UTC).isoformat()
            self._save(updated)
            return self._present(updated)
        return self._revise_saved_plan(
            existing,
            request_text,
            explicit,
            acknowledgement,
            {**completion, "request_fields": request_fields, "conflicts": conflicts},
        )

    def _screen_sequence_limits(self, completion: dict[str, Any]) -> None:
        stated = completion["plan_fields"].get("sequence_limits")
        if not isinstance(stated, dict):
            return
        facts = {}
        recorded = getattr(self._plans, "recorded_input_facts", None)
        if callable(recorded):
            found = recorded()
            if isinstance(found, dict):
                facts = found
        verified = facts.get("sequence_limits") if facts.get("verified") is True else None
        if bounded_sequence_limits(stated) != stated:
            for key in ("sequence_limits", "sequence_limits_provenance", "sequence_limits_source"):
                completion["plan_fields"].pop(key, None)
            completion["conflicts"].append(
                {
                    "field": "sequence_limits",
                    "conflict": (
                        "The attempted-trajectory budget is outside 1 through "
                        f"{ATTEMPTED_TRAJECTORY_BUDGET_UPPER_BOUND}, or num_seqs or "
                        "max_mpnn_sequences is not 1. The limits were not recorded."
                    ),
                }
            )
            completion["touched"].add("sequence_limits")
            return
        if isinstance(verified, dict) and stated == verified:
            return
        for key in ("sequence_limits", "sequence_limits_provenance", "sequence_limits_source"):
            completion["plan_fields"].pop(key, None)
        completion["conflicts"].append(
            {
                "field": "sequence_limits",
                "conflict": (
                    "The stated sequence limits do not match the verified advanced profile. "
                    "They were not recorded."
                ),
            }
        )
        completion["touched"].add("sequence_limits")

    def _revise_saved_plan(
        self,
        existing: dict[str, Any],
        request_text: str,
        explicit: dict[str, Any],
        acknowledgement: str | None = None,
        completion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversation_id = existing.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            conversation_id = f"design-journey:{existing['request_id']}"
        published = _published_plan(self._plans, str(existing.get("plan_id") or ""))
        previous_revision = existing.get("plan_revision")
        previous_digest = None
        if isinstance(published, dict):
            previous_revision = published.get("revision")
            previous_digest = published.get("digest")
        plan, error = _draft(self._plans, conversation_id, request_text, explicit, acknowledgement)
        unchanged = plan is None or (
            plan.get("revision") == previous_revision and plan.get("digest") == previous_digest
        )
        if unchanged and completion is None:
            return self._present(existing)
        updated = json.loads(json.dumps(existing))
        updated.pop("execution_review", None)
        updated.pop("completion", None)
        if not unchanged and plan is not None:
            updated["plan_id"] = plan.get("plan_id")
            updated["plan_revision"] = plan.get("revision")
            updated["approved"] = False
            history = [
                item for item in updated.get("approval_history") or [] if isinstance(item, dict)
            ]
            previous_approval = updated.get("approval")
            if isinstance(previous_approval, dict):
                history.append(previous_approval)
            updated["approval_history"] = history
            updated.pop("approval", None)
            updated.pop("execution_authorization", None)
            stages = []
            for stage in updated.get("stages") or []:
                if not isinstance(stage, dict):
                    continue
                if stage.get("stage_id") == "generation_planning":
                    stages.append(
                        _planning_stage(
                            plan, error, str(existing["request_id"]), int(existing["request_revision"])
                        )
                    )
                elif stage.get("stage_id") == "approval":
                    stages.append(_approval_stage(True))
                elif stage.get("stage_id") == "execution_authorization":
                    stages.append(_execution_authorization_stage(True))
                else:
                    stages.append(stage)
            updated["stages"] = stages
            events = list(updated.get("events") or [])
            events.append(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "kind": "plan_revised",
                    "previous_revision": previous_revision,
                    "previous_digest": previous_digest,
                    "revision": plan.get("revision"),
                    "digest": plan.get("digest"),
                    "detail": (
                        "Explicit structured fields were applied through a new plan revision. "
                        "The previous revision was not edited in place."
                    ),
                }
            )
            updated["events"] = events
            _bind_unrecorded_round_identity(
                updated, plan.get("plan_id"), plan.get("revision"), plan.get("digest")
            )
        if completion is not None:
            _store_completion(updated, existing, completion)
        if unchanged and completion is not None:
            request_same = updated.get("request_fields") == existing.get("request_fields")
            conflicts_same = updated.get("conflicts") == existing.get("conflicts")
            if request_same and conflicts_same:
                return self._present(existing)
        updated["updated_at"] = datetime.now(UTC).isoformat()
        self._save(updated)
        return self._present(updated)

    def _build(
        self,
        request_id: str,
        request_revision: int,
        request_text: str,
        existing: dict[str, Any] | None,
        explicit: dict[str, Any] | None = None,
        acknowledgement: str | None = None,
        *,
        saved_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        boundary = supported_professor_boundary()
        journey_id = (
            str(existing["journey_id"])
            if isinstance(existing, dict) and existing.get("journey_id")
            else f"journey_{uuid4().hex}"
        )
        if saved_plan is None:
            conversation_id = f"design-journey:{request_id}"
            plan, plan_error = _draft(
                self._plans, conversation_id, request_text, explicit, acknowledgement
            )
            recorded_detail = (
                "Generation planning was recorded."
                if plan is not None
                else "Generation planning failed. Later stages were not run."
            )
        else:
            conversation_id = saved_plan.get("conversation_id")
            if not isinstance(conversation_id, str) or not conversation_id:
                raise DesignJourneyError("invalid_request")
            plan, plan_error = saved_plan, None
            recorded_detail = "The existing generation plan was linked. No new plan was recorded."
        knowledge = _knowledge(self._knowledge, request_text)
        now = datetime.now(UTC).isoformat()
        planning = _planning_stage(plan, plan_error, request_id, request_revision)
        stages = [
            _request_stage(request_id, request_revision, request_text),
            planning,
            _approval_stage(planning["status"] == "completed"),
            _execution_authorization_stage(planning["status"] == "completed"),
            _blocked_generation(boundary),
        ]
        stage_map = boundary["stages"] if isinstance(boundary.get("stages"), dict) else {}
        for name in _LATER:
            info = stage_map.get(name) if isinstance(stage_map, dict) else None
            reason = info.get("reason") if isinstance(info, dict) else "Required evidence does not exist."
            kind = info.get("implementation_kind") if isinstance(info, dict) else "not_executed"
            stages.append(_not_applicable(name, str(reason), str(kind)))
        events = list(existing.get("events") or []) if isinstance(existing, dict) else []
        events.append(
            {
                "at": now,
                "kind": "recorded",
                "detail": recorded_detail,
            }
        )
        return {
            "journey_id": journey_id,
            "request_id": request_id,
            "request_revision": request_revision,
            "plan_id": None if plan is None else plan.get("plan_id"),
            "plan_revision": None if plan is None else plan.get("revision"),
            "conversation_id": conversation_id,
            "gpu_execution": "disabled",
            "approved": False,
            "specialist_agents_executed": False,
            "supervisor": {
                "name": boundary["supervisor"],
                "invoked": boundary["invoked"],
                "run_called": False,
            },
            "knowledge": knowledge,
            "hermes": {
                "status": "not_applicable",
                "detail": (
                    "Hermes explains a completed GroundedCriticResult. "
                    "No critic result exists, so Hermes was not called."
                ),
            },
            "stages": stages,
            "events": events,
            "recorded_at": existing.get("recorded_at") if isinstance(existing, dict) else now,
            "updated_at": now,
        }


def handle_design_journey(
    service: DesignJourneyService, method: str, path: str, body: bytes
) -> tuple[int, dict[str, Any]] | None:
    route = path.split("?", 1)[0]
    if route == "/api/design-journeys" and method == "GET":
        return 200, {"requests": service.list_requests()}
    if route == "/api/design-journeys" and method == "POST":
        payload = _json_object(body)
        if payload is None:
            return 400, {"status": "rejected"}
        try:
            revision = payload.get("request_revision")
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise DesignJourneyError("invalid_request")
            structured = payload.get("fields", [])
            acknowledgements = payload.get("acknowledgements", [])
            if not isinstance(structured, list) or not isinstance(acknowledgements, list):
                raise DesignJourneyError("invalid_request")
            document = service.open_or_get(
                str(payload.get("request_id") or ""),
                revision,
                str(payload.get("request_text") or ""),
                structured,
                acknowledgements,
            )
        except DesignJourneyError as exc:
            if exc.code == "round_plan_locked":
                return 409, {
                    "status": "round_launch_consumed",
                    "detail": (
                        "This round already consumed a launch. Plan changes belong to a new round."
                    ),
                }
            return 400, {"status": "rejected"}
        return 200, document
    prefix = "/api/design-journeys/"
    if not route.startswith(prefix):
        return None
    rest = route.removeprefix(prefix)
    if method == "POST" and rest == "link":
        payload = _json_object(body)
        if payload is None or set(payload) != _LINK_KEYS:
            return 400, _link_rejected("rejected")
        return service.link_saved_plan(payload)
    if method == "POST" and rest == "run":
        payload = _json_object(body)
        if payload is None or set(payload) != _RUN_KEYS:
            return 400, _run_refused("rejected")
        return service.run_design(payload)
    if method == "GET" and rest.startswith("by-request/") and rest.endswith("/results"):
        request_id = rest.removeprefix("by-request/").removesuffix("/results")
        if not request_id or "/" in request_id:
            return 404, {"status": "journey_absent"}
        found = service.results_for_request(request_id)
        if found is None:
            return 404, {"status": "journey_absent"}
        return 200, found
    if method == "GET" and rest.startswith("by-request/"):
        request_id = rest.removeprefix("by-request/")
        if not request_id or "/" in request_id:
            return 404, {"status": "journey_absent"}
        found = service.get_by_request(request_id)
        if found is None:
            return 404, {"status": "journey_absent"}
        return 200, found
    if method == "GET" and "/" not in rest and rest:
        found = service.get(rest)
        if found is None:
            return 404, {"status": "journey_absent"}
        return 200, found
    if method == "POST" and rest.endswith("/approval"):
        journey_id = rest[: -len("/approval")]
        if not journey_id or "/" in journey_id:
            return 404, {"status": "journey_absent"}
        payload = _json_object(body)
        if payload is None or set(payload) - _APPROVAL_KEYS:
            return 400, _approval_rejected("rejected")
        return service.record_approval(journey_id, payload)
    if method == "POST" and rest.endswith("/execution-authorization"):
        journey_id = rest[: -len("/execution-authorization")]
        if not journey_id or "/" in journey_id:
            return 404, {"status": "journey_absent"}
        payload = _json_object(body)
        if payload is None or set(payload) - _APPROVAL_KEYS:
            return 400, _approval_rejected("rejected")
        return service.authorize_execution(journey_id, payload)
    if method == "POST" and rest.endswith("/rounds"):
        journey_id = rest[: -len("/rounds")]
        if not journey_id or "/" in journey_id:
            return 404, {"status": "journey_absent"}
        payload = {} if body in {b"", b"{}"} else _json_object(body)
        if payload is None or not isinstance(payload, dict):
            return 400, _approval_rejected("rejected")
        parent = payload.get("parent_round_id")
        if parent is not None and not isinstance(parent, str):
            return 400, _approval_rejected("rejected")
        if set(payload) - {"parent_round_id"}:
            return 400, _approval_rejected("rejected")
        return service.prepare_next_round(journey_id, parent)
    if method == "POST" and "/rounds/" in rest and rest.endswith("/cancellation"):
        journey_id, _, tail = rest.partition("/rounds/")
        round_id = tail[: -len("/cancellation")]
        if not journey_id or "/" in journey_id or not round_id or "/" in round_id:
            return 404, {"status": "round_absent", "detail": "The round was not found."}
        if body not in {b"", b"{}"}:
            payload = _json_object(body)
            if payload is None or payload:
                return 400, _approval_rejected("rejected")
        return service.cancel_round(journey_id, round_id)
    if method == "POST" and rest.endswith("/accepted-launch-recovery"):
        journey_id = rest[: -len("/accepted-launch-recovery")]
        if not journey_id or "/" in journey_id:
            return 404, {"status": "journey_absent"}
        payload = _json_object(body)
        attempt_id = None if not isinstance(payload, dict) else payload.get("attempt_id")
        if payload is None or set(payload) != {"attempt_id"} or not isinstance(attempt_id, str):
            return 400, {"status": "attempt_identity_rejected"}
        return service.recover_accepted_launch(journey_id, attempt_id)
    if method == "POST" and rest.endswith("/scoring-audit"):
        journey_id = rest[: -len("/scoring-audit")]
        if not journey_id or "/" in journey_id:
            return 404, {"status": "journey_absent"}
        payload = _json_object(body)
        attempt_id = None if not isinstance(payload, dict) else payload.get("attempt_id")
        if payload is None or not isinstance(attempt_id, str):
            return 400, {"status": "attempt_identity_rejected"}
        allowed = {"attempt_id"}
        resume = False
        if "resume_interrupted" in payload:
            if payload.get("resume_interrupted") is not True:
                return 400, {"status": "attempt_identity_rejected"}
            allowed.add("resume_interrupted")
            resume = True
        if set(payload) != allowed:
            return 400, {"status": "attempt_identity_rejected"}
        return service.record_scoring_audit(
            journey_id, attempt_id, resume_interrupted=resume
        )
    if method == "POST" and "/rounds/" in rest and rest.endswith("/specialist-progress"):
        journey_id, _, tail = rest.partition("/rounds/")
        round_id = tail[: -len("/specialist-progress")]
        if not journey_id or "/" in journey_id or not round_id or "/" in round_id:
            return 404, {"status": "round_absent", "detail": "The round was not found."}
        if body not in {b"", b"{}"}:
            payload = _json_object(body)
            if payload is None or payload:
                return 400, _approval_rejected("rejected")
        return service.record_specialist_progress(journey_id, round_id)
    if method == "POST" and rest.endswith("/execution-reconciliation"):
        journey_id = rest[: -len("/execution-reconciliation")]
        if not journey_id or "/" in journey_id:
            return 404, {"status": "journey_absent"}
        if body not in {b"", b"{}"}:
            payload = _json_object(body)
            if payload is None or payload:
                return 400, _approval_rejected("rejected")
        return service.reconcile_execution(journey_id)
    if method == "POST" and rest.endswith("/launch"):
        journey_id = rest[: -len("/launch")]
        if not journey_id or "/" in journey_id:
            return 404, {"status": "journey_absent"}
        payload = _json_object(body)
        if payload is None or set(payload) - _LAUNCH_KEYS:
            return 400, _approval_rejected("rejected")
        return service.launch_execution(journey_id, payload)
    return None


def _draft(
    plans: Any,
    conversation_id: str,
    request_text: str,
    explicit: dict[str, Any] | None = None,
    acknowledgement: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    draft = getattr(plans, "draft", None)
    if not callable(draft):
        return None, "Generation planning is not available."
    proposal = {"request_text": request_text}
    if acknowledgement:
        proposal["request_text"] = f"{request_text.rstrip()}\n{acknowledgement}"
    if explicit:
        proposal.update(explicit)
    try:
        published = draft(conversation_id, proposal)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return None, str(exc) or "Generation planning failed."
    if not isinstance(published, dict) or not isinstance(published.get("plan_id"), str):
        return None, "Generation planning did not return a plan."
    if published.get("approved") is True or published.get("execution_connected") is True:
        return None, "Generation planning returned an executable plan. That result was not accepted."
    return published, None


def _planning_stage(
    plan: dict[str, Any] | None,
    error: str | None,
    request_id: str,
    request_revision: int,
) -> dict[str, Any]:
    if plan is None:
        return _stage(
            "generation_planning",
            "failed",
            "deterministic",
            ["generation_plan_draft"],
            [],
            None,
            [error or "Generation planning failed."],
            "GenerationPlanService.draft",
        )
    fields = plan.get("fields") if isinstance(plan.get("fields"), dict) else {}
    output = {
        "plan_id": plan.get("plan_id"),
        "plan_revision": plan.get("revision"),
        "digest": plan.get("digest"),
        "approved": False,
        "execution_connected": False,
        "scientific_execution": plan.get("scientific_execution"),
        "questions": plan.get("questions") if isinstance(plan.get("questions"), list) else [],
        "inputs": _review_values(
            fields,
            ("input_artifacts", "stated_input_artifacts", "input_hashes_verified_against_files", "input_selection"),
        ),
        "parameters": _review_values(
            fields,
            ("target_identity", "hotspot_assumption", "filter_policy", "binder_length", "seed", "sequence_limits"),
        ),
        "resource_limits": _review_values(
            fields,
            ("gpu", "cpu_count", "memory_mib", "execution_deadline_seconds"),
        ),
        "settings": {key: fields.get(key) for key in (
            "target_identity",
            "target_identity_provenance",
            "target_identity_conflict",
            "hotspot_assumption",
            "gpu",
            "filter_policy",
            "binder_length",
            "seed",
        ) if key in fields},
    }
    for key, value in fields.items():
        if key.endswith(("_provenance", "_source", "_conflict")):
            output["parameters"][key] = value
    return _stage(
        "generation_planning",
        "completed",
        "deterministic",
        ["generation_plan_draft"],
        [
            {"kind": "design_request", "request_id": request_id, "revision": request_revision},
            {
                "kind": "generation_plan",
                "plan_id": plan.get("plan_id"),
                "revision": plan.get("revision"),
                "digest": plan.get("digest"),
            },
        ],
        output,
        [
            "This is an unapproved plan. It does not generate a sequence.",
            "GPU execution is disabled.",
            "ranking_score is raw ipSAE and is not recorded. BindCraft i_pTM is not a substitute.",
        ],
        "GenerationPlanService.draft",
    )


def _request_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    text = ""
    for stage in document.get("stages") or []:
        if not isinstance(stage, dict) or stage.get("stage_id") != "design_request":
            continue
        output = stage.get("output")
        if isinstance(output, dict) and isinstance(output.get("request_text"), str):
            text = output["request_text"]
        break
    return {
        "request_id": document.get("request_id"),
        "request_revision": document.get("request_revision"),
        "request_text": text,
        "journey_id": document.get("journey_id"),
        "updated_at": document.get("updated_at") or "",
        "plan_id": document.get("plan_id"),
        "plan_revision": document.get("plan_revision"),
    }


def _request_stage(request_id: str, revision: int, text: str) -> dict[str, Any]:
    return _stage(
        "design_request",
        "completed",
        "recorded_request",
        [],
        [{"kind": "design_request", "request_id": request_id, "revision": revision}],
        {"request_text": text},
        ["A design request is not authorization and not a run."],
        "DesignDrafting",
    )


def _execution_authorization_stage(plan_ready: bool) -> dict[str, Any]:
    if not plan_ready:
        return _stage(
            "execution_authorization",
            "blocked",
            "not_executed",
            [],
            [],
            None,
            ["No reviewable plan exists, so execution cannot be authorized."],
            "GenerationPlanService.approve",
        )
    return _stage(
        "execution_authorization",
        "pending",
        "not_executed",
        [],
        [],
        {"execution_authorized": False, "launches_compute": False},
        [
            "Recorded plan approval does not authorize execution.",
            "Execution authorization does not launch generation.",
        ],
        "GenerationPlanService.approve",
    )


def _approval_stage(plan_ready: bool) -> dict[str, Any]:
    if not plan_ready:
        return _stage(
            "approval",
            "blocked",
            "not_executed",
            [],
            [],
            None,
            ["No reviewable plan exists, so approval cannot start."],
            "human_review",
        )
    return _stage(
        "approval",
        "pending",
        "not_executed",
        [],
        [],
        {"approved": False, "gpu_execution": "disabled"},
        ["Explicit human approval is still required. This journey does not grant it."],
        "human_review",
    )


def _blocked_generation(boundary: dict[str, object]) -> dict[str, Any]:
    stages = boundary.get("stages") if isinstance(boundary.get("stages"), dict) else {}
    info = stages.get("generation") if isinstance(stages, dict) else None
    reason = info.get("reason") if isinstance(info, dict) else "GPU execution is disabled."
    return _stage(
        "generation",
        "blocked",
        "adapter",
        [],
        [],
        None,
        [str(reason), "ProteinGenerationAgent was not called."],
        "ProteinGenerationAgent",
    )


def _not_applicable(name: str, reason: str, kind: str) -> dict[str, Any]:
    return _stage(name, "not_applicable", kind, [], [], None, [reason], "ProteinSupervisorV1")


def _stage(
    stage_id: str,
    status: str,
    kind: str,
    tools: list[str],
    evidence: list[dict[str, Any]],
    output: dict[str, Any] | None,
    limitations: list[str],
    implementation: str,
) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "status": status,
        "implementation_kind": kind,
        "implementation": implementation,
        "tools": tools,
        "agent_executed": False,
        "evidence_references": evidence,
        "output": output,
        "limitations": limitations,
    }


def _knowledge(reader: Any, request_text: str) -> dict[str, Any]:
    if reader is None or not callable(getattr(reader, "list", None)):
        return {
            "status": "unavailable",
            "sources": [],
            "detail": "No Second Brain reader is configured. The plan was not changed.",
        }
    try:
        items = reader.list(status="validated")
    except (DuplicateKnowledgeId, InvalidKnowledgeDocument, KnowledgePathError, OSError, TypeError, ValueError):
        return {
            "status": "unavailable",
            "sources": [],
            "detail": "Second Brain retrieval failed. The plan was not changed.",
        }
    if "pdl1" not in request_text.lower():
        return {
            "status": "irrelevant",
            "sources": [],
            "detail": "The request does not identify PDL1. No Second Brain source was applied.",
        }
    sources = []
    for item in items or ():
        item_id = getattr(item, "id", None)
        title = getattr(item, "title", "")
        if not isinstance(item_id, str) or "pdl1" not in f"{item_id} {title}".lower():
            continue
        sources.append(
            {
                "id": item_id,
                "version": getattr(item, "version", None),
                "type": getattr(item, "type", None),
                "domain": getattr(item, "domain", None),
                "status": getattr(item, "status", None),
                "title": title,
            }
        )
    if not sources:
        return {
            "status": "irrelevant",
            "sources": [],
            "detail": (
                "No validated Second Brain source matched this PDL1 design request. "
                "The plan was not changed."
            ),
        }
    return {
        "status": "retrieved",
        "sources": sources,
        "detail": "Read-only retrieval. These sources did not change the plan, rank, or ipSAE.",
    }


def _review_values(fields: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: fields.get(name) for name in names}


def _published_plan(plans: Any, plan_id: str) -> dict[str, Any] | None:
    published = getattr(plans, "published", None)
    if not callable(published):
        return None
    found = published(plan_id)
    return found if isinstance(found, dict) else None


def _execution_refused(status: str, blockers: list[str]) -> dict[str, Any]:
    return {
        "status": status,
        "approved": False,
        "execution_authorized": False,
        "launches_compute": False,
        "gpu_execution": "disabled",
        "scientific_validation_established": False,
        "unresolved_blockers": blockers,
        "detail": "Execution was not authorized and generation was not launched.",
    }


def _server_capability(plans: Any) -> dict[str, Any]:
    report = getattr(plans, "server_execution", None)
    if callable(report):
        found = report()
        if isinstance(found, dict):
            mode = found.get("mode")
            configured = found.get("demonstration_configured") is True
            if mode in {"disabled", "apptainer-demonstration"} and configured == (mode == "apptainer-demonstration"):
                return {"mode": mode, "demonstration_configured": configured}
    return {"mode": "disabled", "demonstration_configured": False}


def _review_body(
    document: Mapping[str, Any],
    published: Mapping[str, Any] | None,
    blockers: list[str],
    execution_authorized: bool,
    capability: Mapping[str, Any],
    recorded_plan: bool,
) -> dict[str, Any]:
    fields = published.get("fields") if isinstance(published, Mapping) and isinstance(published.get("fields"), dict) else {}
    limits = fields.get("sequence_limits") if isinstance(fields.get("sequence_limits"), dict) else {}
    identities = fields.get("input_artifacts") if "input_identity_unverified" not in blockers else None
    if published is None or published.get("input_registration") != "verified":
        identities = None
    configured = capability.get("demonstration_configured") is True
    configuration = [] if configured or execution_authorized else ["demonstration_not_configured"]
    journey_gpu = document.get("gpu_execution")
    return {
        "plan_id": None if published is None else published.get("plan_id"),
        "revision": None if published is None else published.get("revision"),
        "digest": None if published is None else published.get("digest"),
        "input_identities": identities,
        "input_registration": None if published is None else published.get("input_registration"),
        "resource_budget": {
            "gpu": fields.get("gpu"),
            "cpu_count": fields.get("cpu_count"),
            "memory_mib": fields.get("memory_mib"),
            "execution_deadline_seconds": fields.get("execution_deadline_seconds"),
            "max_trajectories": limits.get("max_trajectories"),
            "num_seqs": limits.get("num_seqs"),
            "max_mpnn_sequences": limits.get("max_mpnn_sequences"),
        },
        "questions": [] if published is None or not isinstance(published.get("questions"), list) else published.get("questions"),
        "unresolved_blockers": blockers,
        "configuration_blockers": configuration,
        "recorded_plan_approval": recorded_plan,
        "authorization_available": not blockers and configured and not execution_authorized,
        "server_execution": capability.get("mode") if isinstance(capability.get("mode"), str) else "disabled",
        "journey_gpu_execution": journey_gpu if isinstance(journey_gpu, str) else None,
        "execution_authorized": execution_authorized,
        "launches_compute": False,
    }


def _present_authorization_stage(presented: dict[str, Any]) -> None:
    review = presented.get("execution_review")
    if not isinstance(review, dict) or review.get("authorization_available") is not True:
        return
    stages = presented.get("stages")
    if not isinstance(stages, list):
        return
    for stage in stages:
        if not isinstance(stage, dict) or stage.get("stage_id") != "execution_authorization":
            continue
        if stage.get("status") != "blocked":
            return
        stage["status"] = "pending"
        stage["limitations"] = [
            "Recorded plan approval does not authorize execution.",
            "Authorize execution is a separate action and does not launch generation.",
        ]
        return


def _launch_request(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    parsed = _approval_request(payload)
    gpu_index = payload.get("gpu_index")
    if parsed is None or isinstance(gpu_index, bool) or not isinstance(gpu_index, int) or not 0 <= gpu_index <= 7:
        return None
    return {**parsed, "gpu_index": gpu_index}


def _with_execution_authorization(document: dict[str, Any], published: Mapping[str, Any]) -> dict[str, Any]:
    approval = published.get("approval")
    if not isinstance(approval, dict):
        raise DesignJourneyError("approval_absent")
    updated = json.loads(json.dumps(document))
    updated.pop("execution_review", None)
    recorded = {
        "plan_id": published.get("plan_id"),
        "revision": approval.get("revision"),
        "digest": approval.get("digest"),
        "reviewer_id": approval.get("reviewer_id"),
        "reason": approval.get("reason"),
        "recorded_at": approval.get("recorded_at"),
        "source": approval.get("source"),
        "scope": approval.get("scope"),
        "execution_authorized": True,
        "launches_compute": False,
        "scientific_validation_established": False,
        "gpu_execution": "disabled",
    }
    updated["execution_authorization"] = recorded
    updated["gpu_execution"] = "disabled"
    updated["launches_compute"] = False
    updated["scientific_validation_established"] = False
    for stage in updated["stages"]:
        if not isinstance(stage, dict) or stage.get("stage_id") != "execution_authorization":
            continue
        stage["status"] = "completed"
        stage["implementation"] = "GenerationPlanService.approve"
        stage["implementation_kind"] = "human_review"
        stage["tools"] = ["generation_plan_decision"]
        stage["agent_executed"] = False
        stage["output"] = recorded
        stage["evidence_references"] = [
            {
                "kind": "generation_plan",
                "plan_id": recorded["plan_id"],
                "revision": recorded["revision"],
                "digest": recorded["digest"],
            }
        ]
        stage["limitations"] = [
            "Execution authorization does not launch generation.",
            "Launch is a separate action and rechecks this revision.",
        ]
    for stage in updated["stages"]:
        if not isinstance(stage, dict):
            continue
        if stage.get("stage_id") == "approval" and stage.get("status") == "completed":
            limitations = [
                item for item in stage.get("limitations") or [] if isinstance(item, str)
            ]
            note = "Execution authorization is separate. This recorded review stays completed."
            if note not in limitations:
                limitations.append(note)
            stage["limitations"] = limitations
        if stage.get("stage_id") == "generation":
            stage["status"] = "blocked"
            stage["limitations"] = [
                "Execution is authorized. Launch has not been requested.",
                "ProteinGenerationAgent was not called.",
            ]
    events = list(updated.get("events") or [])
    if not any(
        isinstance(item, dict) and item.get("kind") == "execution_authorized" and item.get("digest") == recorded["digest"]
        for item in events
    ):
        events.append(
            {
                "at": recorded["recorded_at"] or datetime.now(UTC).isoformat(),
                "kind": "execution_authorized",
                "digest": recorded["digest"],
                "detail": "A person authorized this revision for execution. Launch was not requested.",
            }
        )
    updated["events"] = events
    updated["updated_at"] = datetime.now(UTC).isoformat()
    _remember_round_decision(updated, "execution_authorization", recorded)
    return updated


def _remember_round_decision(document: dict[str, Any], key: str, recorded: Mapping[str, Any]) -> None:
    """Store this decision on the active round when the plan identity matches.

    Other rounds are left unchanged.
    """

    rounds = document.get("rounds")
    active = document.get("active_round_id")
    if not isinstance(rounds, list) or not isinstance(active, str):
        return
    for item in rounds:
        if isinstance(item, dict) and item.get("round_id") == active:
            _keep_parent_decision(item, recorded, key)
            return


def _keep_parent_decision(snapshot: dict[str, Any], recorded: object, key: str) -> None:
    """Copy one already saved decision onto a round that does not have it.

    A different digest is not copied, and an existing decision is not replaced.
    """

    if isinstance(snapshot.get(key), dict) or not isinstance(recorded, dict):
        return
    execution = snapshot.get("execution") if isinstance(snapshot.get("execution"), dict) else None
    digest = _round_digest(snapshot, execution)
    if isinstance(digest, str) and digest and recorded.get("digest") != digest:
        return
    if digest is None and snapshot.get("plan_id") not in {None, recorded.get("plan_id")}:
        return
    snapshot[key] = json.loads(json.dumps(recorded))


def _keep_parent_gpu(snapshot: dict[str, Any], plans: Any) -> None:
    """Copy the approved plan's GPU onto a round that already stores that approval.

    An inherited proposal and a launch index are not this value. A round that
    already stores an index is left unchanged.
    """

    if explicit_gpu_index(snapshot.get("approved_gpu")) is not None:
        return
    approval = snapshot.get("approval")
    if not isinstance(approval, dict):
        return
    plan_id = approval.get("plan_id") or snapshot.get("plan_id")
    revision = approval.get("revision")
    if revision is None:
        revision = snapshot.get("plan_revision")
    digest = approval.get("digest") or snapshot.get("digest")
    gpu = _gpu_on_exact_plan(plans, plan_id, revision, digest)
    if gpu is not None:
        snapshot["approved_gpu"] = gpu


def _gpu_on_exact_plan(plans: Any, plan_id: object, revision: object, digest: object) -> str | None:
    if not isinstance(plan_id, str) or not plan_id:
        return None
    if type(revision) is not int or isinstance(revision, bool):
        return None
    if not isinstance(digest, str) or not digest:
        return None
    published = _published_plan(plans, plan_id)
    if not isinstance(published, dict):
        return None
    if (
        published.get("plan_id") != plan_id
        or published.get("revision") != revision
        or published.get("digest") != digest
    ):
        return None
    fields = published.get("fields")
    if not isinstance(fields, dict):
        return None
    gpu = fields.get("gpu")
    if isinstance(gpu, str) and explicit_gpu_index(gpu) is not None:
        return gpu
    return None


def _stored_gpu_for_approval(
    snapshot: Mapping[str, Any], plan_id: str, revision: int, digest: str
) -> str | None:
    stored = snapshot.get("approved_gpu")
    recorded = snapshot.get("approval")
    if (
        isinstance(stored, str)
        and explicit_gpu_index(stored) is not None
        and isinstance(recorded, dict)
        and recorded.get("plan_id") == plan_id
        and recorded.get("revision") == revision
        and recorded.get("digest") == digest
    ):
        return stored
    return None


def _bound_approved_gpu(
    card: Mapping[str, Any], snapshot: Mapping[str, Any], plans: Any
) -> str | None:
    """GPU from the plan revision named by this round's approval.

    The published revision must match that plan, revision, and digest. A
    historical snapshot index is used only when it was stored with that same
    approval. An inherited proposal and the launch index are not substitutes.
    """

    approval = card.get("approval")
    if not isinstance(approval, dict):
        return None
    plan_id = card.get("plan_id")
    revision = card.get("plan_revision")
    digest = card.get("digest")
    if (
        not isinstance(plan_id, str)
        or not plan_id
        or type(revision) is not int
        or isinstance(revision, bool)
        or not isinstance(digest, str)
        or not digest
        or approval.get("plan_id") != plan_id
        or approval.get("revision") != revision
        or approval.get("digest") != digest
    ):
        return None
    published = _gpu_on_exact_plan(plans, plan_id, revision, digest)
    if published is not None:
        return published
    return _stored_gpu_for_approval(snapshot, plan_id, revision, digest)


def _specialist_timestamp_observation(stages: object) -> str | None:
    """Describe a saved timestamp overlap. This does not rewrite the stages."""

    if not isinstance(stages, list):
        return None
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in stages:
        if isinstance(item, dict) and isinstance(item.get("stage_id"), str):
            by_id[item["stage_id"]] = item
    generation = by_id.get("generation")
    scoring = by_id.get("scoring")
    if generation is None or scoring is None:
        return None
    generated_start = generation.get("started_at")
    generated_end = generation.get("completed_at")
    scored_start = scoring.get("started_at")
    if not all(isinstance(item, str) and item for item in (generated_start, generated_end, scored_start)):
        return None
    later_ends = []
    for name in ("ranking", "critique", "prioritization"):
        stage = by_id.get(name)
        if isinstance(stage, dict) and isinstance(stage.get("completed_at"), str) and stage.get("completed_at"):
            later_ends.append(stage["completed_at"])
    parts = []
    if generated_start > scored_start:
        parts.append("Scoring starts before the recorded normalization start.")
    elif generated_end > scored_start:
        parts.append("The recorded normalization end is after scoring starts.")
    if any(item < generated_end for item in later_ends):
        parts.append(
            "Ranking, critique, or prioritization finish before the recorded normalization end."
        )
    if not parts:
        return None
    return (
        "Saved timestamps overlap for this round. "
        + " ".join(parts)
        + " These saved timestamps were not rewritten."
    )


_ROUND_FIELDS = (
    "target_identity",
    "hotspot_assumption",
    "filter_policy",
    "binder_length",
    "seed",
    "cpu_count",
    "memory_mib",
    "gpu",
    "execution_deadline_seconds",
)


def _round_for_attempt(document: Mapping[str, Any], attempt_id: str) -> dict[str, Any] | None:
    matches = []
    rounds = document.get("rounds")
    if not isinstance(rounds, list):
        return None
    for item in rounds:
        if not isinstance(item, dict):
            continue
        execution = _execution_for_round(document, item)
        if isinstance(execution, dict) and execution.get("attempt_id") == attempt_id:
            matches.append(item)
    if len(matches) != 1:
        return None
    return matches[0]


def _correction_recorded(snapshot: Mapping[str, Any], execution: Mapping[str, Any]) -> bool:
    corrections = snapshot.get("outcome_corrections")
    if not isinstance(corrections, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("kind") == "accepted_launch_outcome_correction"
        and item.get("attempt_id") == execution.get("attempt_id")
        and item.get("plan_revision") == execution.get("revision")
        and item.get("digest") == execution.get("digest")
        for item in corrections
    )


def _archive_blocked_specialists(
    document: dict[str, Any], target: dict[str, Any], execution: Mapping[str, Any]
) -> None:
    """Keep the no-candidate pass. The next pass writes beside this record."""

    attempt_id = execution.get("attempt_id")
    round_id = target.get("round_id")
    stages = [item for item in target.get("specialist_stages") or [] if isinstance(item, dict)]
    events = [item for item in target.get("specialist_events") or [] if isinstance(item, dict)]
    progress = target.get("specialist_progress") if isinstance(target.get("specialist_progress"), dict) else None
    document_events = [
        item
        for item in document.get("specialist_events") or []
        if isinstance(item, dict) and item.get("round_id") == round_id and item.get("attempt_id") == attempt_id
    ]
    corrections = [item for item in target.get("outcome_corrections") or [] if isinstance(item, dict)]
    corrections.append(
        {
            "kind": "accepted_launch_outcome_correction",
            "at": datetime.now(UTC).isoformat(),
            "attempt_id": attempt_id,
            "plan_id": execution.get("plan_id"),
            "plan_revision": execution.get("revision"),
            "digest": execution.get("digest"),
            "superseded_execution": json.loads(json.dumps(execution)),
            "superseded_specialist_stages": json.loads(json.dumps(stages)),
            "superseded_specialist_events": json.loads(json.dumps(events)),
            "superseded_document_events": json.loads(json.dumps(document_events)),
            "superseded_specialist_progress": json.loads(json.dumps(progress)) if progress else None,
        }
    )
    target["outcome_corrections"] = corrections
    target.pop("specialist_stages", None)
    target.pop("specialist_events", None)
    target.pop("specialist_progress", None)
    document["specialist_events"] = [
        item
        for item in document.get("specialist_events") or []
        if not (
            isinstance(item, dict) and item.get("round_id") == round_id and item.get("attempt_id") == attempt_id
        )
    ]


def _corrected_execution(execution: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    corrected = json.loads(json.dumps(execution))
    corrected["accepted_candidate"] = True
    corrected["successful_candidate_generation"] = True
    corrected["candidate_id"] = evidence["candidate_id"]
    corrected["candidate_identity"] = evidence["candidate_identity"]
    corrected["budget_compliance_established"] = True
    corrected["budget_usage"] = evidence["budget_usage"]
    corrected["termination_reason"] = evidence["termination_reason"]
    corrected["acceptance_evidence"] = evidence["summary"]
    corrected["scientific_validation_established"] = False
    corrected["consumed"] = True
    link = corrected.get("result_link")
    if not isinstance(link, dict):
        link = {}
    link["attempt_id"] = corrected.get("attempt_id")
    link["candidate_id"] = evidence["candidate_id"]
    corrected["result_link"] = link
    return corrected


def _specialist_evidence_recorded(snapshot: Mapping[str, Any]) -> bool:
    events = snapshot.get("specialist_events")
    stages = snapshot.get("specialist_stages")
    progress = snapshot.get("specialist_progress")
    return (
        (isinstance(events, list) and any(isinstance(item, dict) for item in events))
        or (isinstance(stages, list) and any(isinstance(item, dict) for item in stages))
        or isinstance(progress, dict)
    )


def _bind_unrecorded_round_identity(
    document: dict[str, Any], plan_id: object, revision: object, digest: object
) -> None:
    """Point the active round at the plan being revised or launched.

    A round that already has specialist evidence keeps that evidence.
    """

    if (
        not isinstance(plan_id, str)
        or type(revision) is not int
        or isinstance(revision, bool)
        or revision < 1
        or not isinstance(digest, str)
        or not digest
    ):
        return
    rounds = document.get("rounds")
    active = document.get("active_round_id")
    if not isinstance(rounds, list) or not isinstance(active, str):
        return
    for item in rounds:
        if not isinstance(item, dict) or item.get("round_id") != active:
            continue
        if _specialist_evidence_recorded(item):
            return
        item["plan_id"] = plan_id
        item["plan_revision"] = revision
        item["digest"] = digest
        return


def _stored_rounds(document: dict[str, Any], plans: Any) -> list[dict[str, Any]]:
    stored = document.get("rounds")
    if isinstance(stored, list) and any(isinstance(item, dict) and item.get("round_id") for item in stored):
        return [item for item in json.loads(json.dumps(stored)) if isinstance(item, dict)]
    return [_snapshot_round(document, plans, 1, None)]


def _find_round(rounds: list[dict[str, Any]], round_id: str) -> dict[str, Any] | None:
    for item in rounds:
        if item.get("round_id") == round_id:
            return item
    return None


def _active_child(rounds: list[dict[str, Any]], parent: Mapping[str, Any]) -> dict[str, Any] | None:
    """The open child, if this parent already has one. A cancelled child does not count."""

    pointed = parent.get("child_round_id")
    if isinstance(pointed, str):
        child = _find_round(rounds, pointed)
        if child is not None and child.get("status") != "cancelled":
            return child
    parent_id = parent.get("round_id")
    matches = [
        item
        for item in rounds
        if item.get("parent_round_id") == parent_id and item.get("status") != "cancelled"
    ]
    return matches[-1] if matches else None


def _active_round_cancelled(document: Mapping[str, Any]) -> bool:
    rounds = document.get("rounds")
    active_id = document.get("active_round_id")
    if not isinstance(rounds, list) or not isinstance(active_id, str):
        return False
    for item in rounds:
        if isinstance(item, dict) and item.get("round_id") == active_id:
            return item.get("status") == "cancelled"
    return False


def _execution_for_round(document: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    if document.get("active_round_id") == snapshot.get("round_id"):
        current = document.get("execution")
        if isinstance(current, dict):
            return current
    stored = snapshot.get("execution")
    return stored if isinstance(stored, dict) else None


def _round_action_refused() -> dict[str, Any]:
    return {
        "status": "round_cancelled",
        "approved": False,
        "execution_authorized": False,
        "launches_compute": False,
        "gpu_execution": "disabled",
        "scientific_validation_established": False,
        "detail": "This round is cancelled. It cannot be approved, authorized, or launched.",
    }


def _round_cancel_refused() -> dict[str, Any]:
    return {
        "status": "round_launch_consumed",
        "approved": False,
        "execution_authorized": False,
        "launches_compute": False,
        "gpu_execution": "disabled",
        "scientific_validation_established": False,
        "detail": "This round already consumed a launch. Cancellation does not stop that execution.",
    }


def _snapshot_round(
    document: Mapping[str, Any],
    plans: Any,
    ordinal: int,
    parent_round_id: str | None,
) -> dict[str, Any]:
    published = _published_plan(plans, str(document.get("plan_id") or ""))
    fields = published.get("fields") if isinstance(published, dict) and isinstance(published.get("fields"), dict) else {}
    journey_id = str(document.get("journey_id") or "journey")
    round_id = f"round_{journey_id}_{ordinal}"
    approval = document.get("approval") if isinstance(document.get("approval"), dict) else None
    execution = document.get("execution") if isinstance(document.get("execution"), dict) else None
    authorization = document.get("execution_authorization")
    gpu = fields.get("gpu") if isinstance(fields.get("gpu"), str) else None
    return {
        "round_id": round_id,
        "parent_round_id": parent_round_id,
        "child_round_id": None,
        "ordinal": ordinal,
        "plan_id": document.get("plan_id"),
        "plan_revision": document.get("plan_revision"),
        "digest": None if not isinstance(published, dict) else published.get("digest"),
        "approval": json.loads(json.dumps(approval)) if approval else None,
        "approval_history": json.loads(json.dumps(document.get("approval_history") or [])),
        "execution_authorization": json.loads(json.dumps(authorization)) if isinstance(authorization, dict) else None,
        "execution": json.loads(json.dumps(execution)) if execution else None,
        "status": "cancelled" if document.get("status") == "cancelled" else "open",
        "cancelled_at": document.get("cancelled_at") if document.get("status") == "cancelled" else None,
        "parameters": _copied_proposals(
            fields, round_id, document.get("plan_revision"), "recorded_round"
        ),
        "target": fields.get("target_identity") if isinstance(fields.get("target_identity"), str) else None,
        "approved_gpu": gpu if approval else None,
        "sequence_limits": _same_sequence_limits(fields.get("sequence_limits")),
    }


def _copied_proposals(
    fields: Mapping[str, Any],
    parent_round_id: str,
    revision: object,
    source: str = "previous_round",
) -> list[dict[str, Any]]:
    proposals = []
    for name in _ROUND_FIELDS:
        if name not in fields:
            continue
        proposals.append(
            {
                "name": name,
                "value": fields[name],
                "provenance": "preserved",
                "source": source,
                "parent_round_id": parent_round_id,
                "parent_plan_revision": revision,
            }
        )
    limits = _same_sequence_limits(fields.get("sequence_limits"))
    if limits is not None:
        proposals.append(
            {
                "name": "sequence_limits",
                "value": limits,
                "provenance": "preserved",
                "source": source,
                "parent_round_id": parent_round_id,
                "parent_plan_revision": revision,
            }
        )
    return proposals


def _proposal_draft(proposals: list[dict[str, Any]], fields: Mapping[str, Any]) -> dict[str, Any]:
    explicit: dict[str, Any] = {}
    for item in proposals:
        name = item["name"]
        explicit[name] = item["value"]
        explicit[f"{name}_provenance"] = "preserved"
        source = fields.get(f"{name}_source")
        if source in _FIELD_SOURCES or source == "verified_advanced_profile":
            explicit[f"{name}_source"] = source
    return explicit


def _same_sequence_limits(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    copied: dict[str, int] = {}
    for key in ("max_trajectories", "num_seqs", "max_mpnn_sequences"):
        item = value.get(key)
        if type(item) is not int or isinstance(item, bool):
            return None
        copied[key] = item
    return copied


def _stages_for_new_round(
    document: Mapping[str, Any], plan: Mapping[str, Any] | None, error: str | None
) -> list[dict[str, Any]]:
    planning = _planning_stage(
        plan if isinstance(plan, dict) else None,
        error,
        str(document["request_id"]),
        int(document["request_revision"]),
    )
    stages = []
    for stage in document.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        stage_id = stage.get("stage_id")
        if stage_id == "generation_planning":
            stages.append(planning)
        elif stage_id == "approval":
            stages.append(_approval_stage(plan is not None))
        elif stage_id == "execution_authorization":
            stages.append(_execution_authorization_stage(plan is not None))
        elif stage_id == "generation":
            stages.append(_blocked_generation(supported_professor_boundary()))
        elif stage_id in _LATER:
            boundary_stages = supported_professor_boundary()["stages"]
            info = boundary_stages.get(stage_id) if isinstance(boundary_stages, dict) else None
            reason = info.get("reason") if isinstance(info, dict) else "Required evidence does not exist."
            kind = info.get("implementation_kind") if isinstance(info, dict) else "not_executed"
            stages.append(_not_applicable(stage_id, str(reason), str(kind)))
        else:
            stages.append(json.loads(json.dumps(stage)))
    return stages


def _round_digest(snapshot: Mapping[str, Any], execution: Mapping[str, Any] | None) -> str | None:
    approval = snapshot.get("approval") if isinstance(snapshot.get("approval"), dict) else None
    for value in (
        snapshot.get("digest"),
        None if execution is None else execution.get("digest"),
        None if approval is None else approval.get("digest"),
    ):
        if isinstance(value, str) and value:
            return value
    return None


def _record_binds(
    record: object, snapshot: Mapping[str, Any], execution: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Keep a saved decision only when its plan identity is this round's."""

    if not isinstance(record, dict):
        return None
    digest = _round_digest(snapshot, execution)
    if not isinstance(record.get("digest"), str) or record.get("digest") != digest:
        return None
    plan_id = snapshot.get("plan_id") or (None if execution is None else execution.get("plan_id"))
    if isinstance(plan_id, str) and plan_id and record.get("plan_id") not in {None, plan_id}:
        return None
    revision = snapshot.get("plan_revision")
    if revision is None and execution is not None:
        revision = execution.get("revision")
    if revision is not None and record.get("revision") not in {None, revision}:
        return None
    return record


def _event_for_digest(document: Mapping[str, Any], kind: str, digest: str | None) -> bool:
    if not isinstance(digest, str) or not digest:
        return False
    return any(
        isinstance(item, dict) and item.get("kind") == kind and item.get("digest") == digest
        for item in document.get("events") or []
    )


def _round_cards(document: Mapping[str, Any], plans: Any) -> list[dict[str, Any]]:
    stored = document.get("rounds")
    snapshots = [item for item in stored if isinstance(item, dict)] if isinstance(stored, list) else []
    if not snapshots:
        snapshots = [_snapshot_round(document, plans, 1, None)]
    cards = []
    for index, snapshot in enumerate(snapshots):
        live = index == len(snapshots) - 1
        card = _round_card(document, snapshot, live)
        if live:
            published = _published_plan(plans, str(document.get("plan_id") or ""))
            if isinstance(published, dict):
                card["digest"] = published.get("digest")
                fields = published.get("fields") if isinstance(published.get("fields"), dict) else {}
                gpu = fields.get("gpu") if isinstance(fields.get("gpu"), str) else None
                if gpu is not None:
                    card["gpu_proposal"] = gpu
                    card["profile_accepts_gpu_proposal"] = explicit_gpu_index(gpu) is not None
        card["approved_gpu"] = _bound_approved_gpu(card, snapshot, plans)
        card["specialist_timestamp_observation"] = _specialist_timestamp_observation(
            card.get("specialist_stages")
        )
        cards.append(card)
    return cards


def _round_card(
    document: Mapping[str, Any], snapshot: Mapping[str, Any], live: bool
) -> dict[str, Any]:
    execution = snapshot.get("execution") if isinstance(snapshot.get("execution"), dict) else None
    if execution is None and live and isinstance(document.get("execution"), dict):
        execution = document.get("execution")
    approval = _record_binds(snapshot.get("approval"), snapshot, execution)
    if approval is None:
        approval = _record_binds(document.get("approval"), snapshot, execution)
    authorization = _record_binds(snapshot.get("execution_authorization"), snapshot, execution)
    if authorization is None:
        authorization = _record_binds(document.get("execution_authorization"), snapshot, execution)
    digest = _round_digest(snapshot, execution)
    review_evidence = "round_record" if isinstance(snapshot.get("approval"), dict) else None
    if approval is not None and review_evidence is None:
        review_evidence = "matching_plan"
    authorization_evidence = (
        "round_record" if isinstance(snapshot.get("execution_authorization"), dict) else None
    )
    if authorization is not None and authorization_evidence is None:
        authorization_evidence = "matching_plan"
    parameters = snapshot.get("parameters") if isinstance(snapshot.get("parameters"), list) else []
    gpu_proposal = _proposal_value(parameters, "gpu")
    gpu_source = _proposal_source(parameters, "gpu")
    if live and gpu_proposal is None:
        gpu_proposal = snapshot.get("approved_gpu") if isinstance(snapshot.get("approved_gpu"), str) else None
        gpu_source = "approved" if gpu_proposal else None
    accepted = bool(execution and execution.get("accepted_candidate") is True)
    usage = _presented_budget_usage(execution)
    cancelled = snapshot.get("status") == "cancelled"
    consumed = bool(execution and execution.get("consumed") is True)
    terminal = bool(execution and execution.get("terminal") is True)
    specialist_stages = snapshot.get("specialist_stages") if isinstance(snapshot.get("specialist_stages"), list) else []
    specialist_events = snapshot.get("specialist_events") if isinstance(snapshot.get("specialist_events"), list) else []
    progress = snapshot.get("specialist_progress") if isinstance(snapshot.get("specialist_progress"), dict) else None
    progress_closed = (
        isinstance(progress, dict)
        and progress.get("attempt_id") == (execution.get("attempt_id") if execution else None)
        and progress.get("status") in {"recorded", "failed", "interrupted"}
    )
    inherited = gpu_source == "previous_round"
    if terminal:
        restriction = (
            "The technical demonstration profile does not assign a host GPU. "
            "This round is complete, so it does not ask for another GPU index."
        )
    elif inherited:
        restriction = (
            "The technical demonstration profile does not assign a host GPU. "
            f"Inherited GPU {gpu_proposal}. This is a historical value from an earlier round. "
            "It is not this round's approved GPU. Choose an explicit index on this plan. "
            "The approved plan, the launch request, and the runtime device must use that same index."
        )
    else:
        restriction = (
            "The technical demonstration profile does not assign a host GPU. "
            "Choose an explicit GPU index on this plan. "
            "The approved plan, the launch request, and the runtime device must use that same index. "
            "An unselected assignment is not a choice."
        )
    recorded_review = "not_recorded"
    if approval and approval.get("scope") == "recorded_plan":
        recorded_review = "recorded_plan"
    elif approval:
        recorded_review = "recorded"
    elif _event_for_digest(document, "approval_recorded", digest):
        recorded_review = "recorded_plan"
        review_evidence = "journey_event"
    authorization_status = "not_authorized"
    if authorization:
        authorization_status = "authorized"
    elif _event_for_digest(document, "execution_authorized", digest):
        authorization_status = "authorized"
        authorization_evidence = "journey_event"
    return {
        "round_id": snapshot.get("round_id"),
        "parent_round_id": snapshot.get("parent_round_id"),
        "child_round_id": snapshot.get("child_round_id"),
        "child_round_ids": snapshot.get("child_round_ids") if isinstance(snapshot.get("child_round_ids"), list) else [],
        "ordinal": snapshot.get("ordinal"),
        "active": live,
        "plan_id": document.get("plan_id") if live else snapshot.get("plan_id"),
        "plan_revision": document.get("plan_revision") if live else snapshot.get("plan_revision"),
        "digest": snapshot.get("digest"),
        "target": snapshot.get("target"),
        "status": "cancelled" if cancelled else "open",
        "cancelled_at": snapshot.get("cancelled_at") if cancelled else None,
        "cancellable": (not cancelled) and not consumed and bool(snapshot.get("parent_round_id")),
        "outcome": "cancelled" if cancelled else None if execution is None else execution.get("status"),
        "outcome_label": "Cancelled." if cancelled else _outcome_label(execution),
        "execution_completed": bool(execution and execution.get("terminal") is True),
        "successful_candidate_generation": bool(
            execution and execution.get("successful_candidate_generation") is True
        ),
        "candidate_count": 1 if accepted else 0,
        "budget_usage": usage,
        "budget_compliance_established": bool(
            execution and execution.get("budget_compliance_established") is True
        ),
        "scientific_validation_established": False,
        "recorded_review_status": recorded_review,
        "recorded_review_evidence": review_evidence if recorded_review != "not_recorded" else "absent",
        "execution_authorization_status": authorization_status,
        "execution_authorization_evidence": authorization_evidence
        if authorization_status == "authorized"
        else "absent",
        "approved_gpu": None,
        "launch_gpu_index": execution.get("gpu_index") if execution and "gpu_index" in execution else None,
        "gpu_proposal": gpu_proposal,
        "gpu_proposal_source": gpu_source,
        "gpu_proposal_historical": inherited,
        "profile_gpu": None,
        "profile_accepts_gpu_proposal": explicit_gpu_index(gpu_proposal) is not None,
        "host_gpu_index": None,
        "profile_restriction": restriction,
        "specialist_stages": specialist_stages,
        "specialist_events": specialist_events,
        "specialist_progress_available": terminal and not cancelled and not progress_closed,
        "results_publication": snapshot.get("results_publication")
        if isinstance(snapshot.get("results_publication"), dict)
        else None,
        "outcome_corrections": snapshot.get("outcome_corrections")
        if isinstance(snapshot.get("outcome_corrections"), list)
        else [],
        "scoring_audits": snapshot.get("scoring_audits")
        if isinstance(snapshot.get("scoring_audits"), list)
        else [],
        "post_generation": _saved_dispatch(snapshot, document if live else None),
        "parameters": parameters,
        "sequence_limits": snapshot.get("sequence_limits"),
        "attempt_id": execution.get("attempt_id") if execution else None,
        "approval": approval,
        "execution_authorization": authorization,
        "execution": execution,
    }


def _presented_budget_usage(execution: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Show ledger counts on the card. A read does not rewrite the saved execution."""

    if not isinstance(execution, dict):
        return None
    usage = execution.get("budget_usage")
    if isinstance(usage, dict):
        return usage
    directory = execution.get("attempt_directory")
    attempt_id = execution.get("attempt_id")
    if not isinstance(directory, str) or not isinstance(attempt_id, str):
        return None
    evidence = read_terminal_attempt_evidence(Path(directory), attempt_id)
    found = evidence.get("budget_usage")
    return found if isinstance(found, dict) else None


def _proposal_source(parameters: list[object], name: str) -> str | None:
    for item in parameters:
        if isinstance(item, dict) and item.get("name") == name and isinstance(item.get("source"), str):
            return item["source"]
    return None


def _proposal_value(parameters: list[object], name: str) -> str | None:
    for item in parameters:
        if isinstance(item, dict) and item.get("name") == name and isinstance(item.get("value"), str):
            return item["value"]
    return None


def _outcome_label(execution: Mapping[str, Any] | None) -> str:
    if not isinstance(execution, dict):
        return "No launch recorded."
    if (
        execution.get("status") == "budget_exhausted"
        or execution.get("termination_reason") == "attempted_trajectory_budget_exhausted"
    ):
        return "Budget exhausted without an accepted candidate."
    if execution.get("successful_candidate_generation") is True:
        return "Successful candidate generation."
    if execution.get("terminal") is True:
        return "Execution completed without a successful candidate."
    if execution.get("status") == "execution_unresolved":
        return "Execution unresolved. It was not replayed."
    if execution.get("consumed") is True:
        return "Execution is still recorded as running."
    return "No launch recorded."


def _plan_gpu(published: Mapping[str, Any]) -> object:
    fields = published.get("fields")
    if not isinstance(fields, dict):
        return None
    return fields.get("gpu")


def _gpu_assignment_index(value: object) -> int | None:
    """The approved plan gpu is the nvidia-smi index, not a GPU count.

    ``"0"`` is index 0 and ``"1"`` is index 1. ``CUDA_VISIBLE_DEVICES`` and
    the launch request use that same integer. A missing value is not filled.
    """

    return explicit_gpu_index(value)


def _with_saved_evidence(
    reply: Mapping[str, Any],
    attempt_directory: Path,
    attempt_id: str,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    found = evidence if evidence is not None else read_terminal_attempt_evidence(attempt_directory, attempt_id)
    merged = dict(reply)
    merged["evidence_checks"] = found.get("evidence_checks")
    merged["raw_progress"] = found.get("raw_progress")
    merged["designs_directory"] = found.get("designs_directory")
    merged["failure_counters"] = found.get("failure_counters")
    merged["scientific_validation_established"] = False
    if found.get("outcome") == "budget_exhausted":
        merged.update(
            {
                "status": "budget_exhausted",
                "launched": True,
                "accepted_candidate": False,
                "successful_candidate_generation": False,
                "termination_reason": "attempted_trajectory_budget_exhausted",
                "budget_compliance_established": True,
                "budget_usage": found.get("budget_usage"),
                "progress": [{"status": "rejected"}, {"status": "budget_exhausted"}],
            }
        )
        return merged
    if reply.get("status") == "verified":
        accepted = read_accepted_launch_evidence(attempt_directory, attempt_id)
        merged["acceptance_evidence"] = accepted.get("summary")
        merged["accepted_candidate"] = accepted.get("accepted") is True
        merged["successful_candidate_generation"] = accepted.get("accepted") is True
        merged["candidate_id"] = accepted.get("candidate_id") if accepted.get("accepted") is True else None
        merged["candidate_identity"] = (
            accepted.get("candidate_identity") if accepted.get("accepted") is True else None
        )
        merged["budget_compliance_established"] = accepted.get("accepted") is True
        if accepted.get("accepted") is True:
            merged["budget_usage"] = accepted.get("budget_usage")
            merged["termination_reason"] = accepted.get("termination_reason")
        else:
            merged.pop("budget_usage", None)
        merged["scientific_validation_established"] = False
        return merged
    if reply.get("status") == "launcher_exception" or evidence is not None:
        progress = found.get("raw_progress")
        recorded = progress.get("status") if isinstance(progress, dict) else None
        merged.update(
            {
                "status": "execution_unresolved",
                "launched": False,
                "terminal": False,
                "accepted_candidate": False,
                "successful_candidate_generation": False,
                "budget_compliance_established": False,
                "termination_reason": None,
                "progress": [{"status": recorded or "execution_unresolved"}],
            }
        )
    return merged


def _execution_record(
    document: Mapping[str, Any],
    parsed: Mapping[str, Any],
    *,
    attempt_id: str,
    attempt_directory: str,
    status: str,
    launched: bool,
    terminal: bool,
    progress: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "launch_id": f"launch_{uuid4().hex}",
        "idempotency_key": parsed["idempotency_key"],
        "plan_id": parsed["plan_id"],
        "revision": parsed["revision"],
        "digest": parsed["digest"],
        "attempt_id": attempt_id,
        "attempt_directory": attempt_directory,
        "gpu_index": parsed["gpu_index"],
        "status": status,
        "launched": launched,
        "consumed": False,
        "terminal": terminal,
        "progress": progress,
        "accepted_candidate": None,
        "successful_candidate_generation": False,
        "candidate_id": None,
        "termination_reason": None,
        "scientific_validation_established": False,
        "result_link": {
            "journey_id": document.get("journey_id"),
            "attempt_id": attempt_id,
            "candidate_id": None,
        },
    }


def _execution_from_reply(
    document: Mapping[str, Any],
    running: dict[str, Any],
    reply: Mapping[str, Any],
) -> dict[str, Any]:
    finished = json.loads(json.dumps(running))
    status = str(reply.get("status") or "launch_failed")
    launched = reply.get("launched") is True
    pre_launch = status in _PRE_LAUNCH_REFUSALS and not launched
    progress = reply.get("progress")
    if not isinstance(progress, list):
        progress = [{"status": status}]
    accepted = reply.get("accepted_candidate") is True
    successful = accepted and reply.get("successful_candidate_generation") is True and status == "verified"
    candidate = reply.get("candidate_id")
    candidate_id = candidate if isinstance(candidate, str) and candidate else None
    reported_attempt = reply.get("attempt_id")
    attempt_id = reported_attempt if reported_attempt == finished["attempt_id"] else finished["attempt_id"]
    unresolved = status == "execution_unresolved"
    terminal = (not pre_launch) and not unresolved and status not in {"running", "started", "planned"}
    if pre_launch:
        terminal = False
    finished.update(
        {
            "status": status,
            "launched": launched,
            "consumed": True if unresolved else not pre_launch,
            "terminal": terminal,
            "progress": progress,
            "accepted_candidate": False if status == "budget_exhausted" else accepted if launched or terminal else None,
            "successful_candidate_generation": successful,
            "candidate_id": candidate_id,
            "termination_reason": reply.get("termination_reason"),
            "attempt_id": attempt_id,
            "budget_compliance_established": reply.get("budget_compliance_established") is True,
            "scientific_validation_established": False,
            "result_link": {
                "journey_id": document.get("journey_id"),
                "attempt_id": attempt_id,
                "candidate_id": candidate_id,
            },
        }
    )
    for name in (
        "failure_counters",
        "raw_progress",
        "designs_directory",
        "evidence_checks",
        "exception_type",
        "budget_usage",
        "candidate_identity",
        "acceptance_evidence",
        "diagnostics",
        "automatic_retry",
        "other_services_signaled",
    ):
        if name in reply:
            finished[name] = reply[name]
    return finished


def _saved_dispatch(snapshot: Mapping[str, Any], source: Mapping[str, Any] | None) -> dict[str, Any] | None:
    dispatch = snapshot.get("post_generation")
    if not isinstance(dispatch, dict) and source is not None:
        dispatch = source.get("post_generation")
    if not isinstance(dispatch, dict):
        return None
    return json.loads(json.dumps(dispatch))


def _copy_dispatch(document: dict[str, Any]) -> None:
    dispatch = document.get("post_generation")
    rounds = document.get("rounds")
    active = document.get("active_round_id")
    if not isinstance(rounds, list):
        return
    for item in rounds:
        if isinstance(item, dict) and item.get("round_id") == active:
            item["post_generation"] = dispatch
            if isinstance(document.get("execution"), dict):
                item["execution"] = document["execution"]


def _dispatch_detail(status: str, phase: str) -> str:
    if status == "unresolved" and phase == "generation":
        return (
            "The launcher reported verified, but the saved artifacts did not accept a candidate. "
            "Specialists were not run and generation was not launched again."
        )
    if status == "failed" and phase == "publication":
        return "Publication failed. Completed specialist scores were kept."
    if status == "failed":
        return "Post-generation failed. Generation was not launched again."
    if status == "blocked":
        return "Downstream specialists are blocked. No accepted candidate was scored."
    if status == "completed":
        return "Specialist processing finished. Scientific validation is not established."
    return f"Post-generation status is {status}."


def _with_launch(document: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(document))
    updated.pop("execution_review", None)
    updated["execution"] = execution
    updated["gpu_execution"] = "disabled"
    updated["launches_compute"] = False
    updated["scientific_validation_established"] = False
    terminal = execution.get("terminal") is True
    successful = execution.get("successful_candidate_generation") is True
    for stage in updated["stages"]:
        if not isinstance(stage, dict):
            continue
        if stage.get("stage_id") == "generation":
            if execution.get("status") == "execution_unresolved":
                stage["status"] = "unresolved"
            elif not terminal and execution.get("consumed") is True:
                stage["status"] = "running"
            elif terminal and execution.get("status") in {
                "launch_failed",
                "failed",
                "outputs_rejected",
                "vram_insufficient",
            }:
                stage["status"] = "failed"
            elif terminal:
                stage["status"] = "completed"
            stage["implementation"] = "GenerationPlanService.launch_apptainer_demonstration"
            stage["tools"] = ["apptainer_demonstration_launch"]
            stage["agent_executed"] = False
            stage["output"] = {
                "launch_id": execution.get("launch_id"),
                "attempt_id": execution.get("attempt_id"),
                "status": execution.get("status"),
                "terminal": terminal,
                "accepted_candidate": execution.get("accepted_candidate"),
                "successful_candidate_generation": successful,
                "candidate_id": execution.get("candidate_id"),
                "termination_reason": execution.get("termination_reason"),
                "result_link": execution.get("result_link"),
                "progress": execution.get("progress"),
                "scientific_validation_established": False,
                "budget_compliance_established": execution.get("budget_compliance_established") is True,
                "failure_counters": execution.get("failure_counters"),
            }
            stage["evidence_references"] = [
                {
                    "kind": "journey_attempt",
                    "journey_id": updated.get("journey_id"),
                    "attempt_id": execution.get("attempt_id"),
                    "candidate_id": execution.get("candidate_id"),
                }
            ]
            if execution.get("status") == "budget_exhausted":
                stage["limitations"] = [
                    "Budget exhausted without an accepted candidate.",
                    "Budget compliance is separate from scientific validation.",
                ]
            elif execution.get("status") == "execution_unresolved":
                stage["limitations"] = [
                    "The launcher reply was not recorded. Execution was not replayed.",
                ]
            elif not terminal:
                stage["limitations"] = [
                    "Execution is unresolved. It is not reported as completed.",
                ]
            elif not successful:
                stage["limitations"] = [
                    "Generation finished without an accepted candidate.",
                ]
            else:
                stage["limitations"] = [
                    "An accepted candidate was linked. Scientific validation is not established.",
                    "BindCraft i_pTM is not ipSAE.",
                ]
        if terminal and stage.get("stage_id") in _LATER:
            stage["status"] = "blocked"
            stage["agent_executed"] = False
            stage["limitations"] = [
                "Required evidence for this stage was not recorded.",
                "BindCraft i_pTM is not ipSAE.",
            ]
    events = list(updated.get("events") or [])
    launch_id = execution.get("launch_id")
    if not any(isinstance(item, dict) and item.get("kind") == "launch_recorded" and item.get("launch_id") == launch_id for item in events):
        events.append(
            {
                "at": datetime.now(UTC).isoformat(),
                "kind": "launch_recorded",
                "launch_id": launch_id,
                "detail": f"Launch status {execution.get('status')} was recorded from the launcher reply.",
            }
        )
    updated["events"] = events
    updated["updated_at"] = datetime.now(UTC).isoformat()
    _bind_unrecorded_round_identity(
        updated, execution.get("plan_id"), execution.get("revision"), execution.get("digest")
    )
    return updated


def _run_configuration(published: Mapping[str, Any], plans: Any) -> str | None:
    if published.get("questions"):
        return "configuration_incomplete"
    fields = published.get("fields") if isinstance(published.get("fields"), dict) else {}
    if fields.get("input_hashes_verified_against_files") is not True:
        return "inputs_unverified"
    if explicit_gpu_index(fields.get("gpu")) is None:
        return "gpu_not_selected"
    reader = getattr(plans, "verified_input_paths", None)
    found = reader() if callable(reader) else None
    if not isinstance(found, dict):
        return "inputs_unreadable"
    for path in found.values():
        if not isinstance(path, Path) or not path.is_file():
            return "inputs_unreadable"
    return None


def _saved_design_count(drafter: Any, request_id: str, revision: int) -> int | None:
    """Quantity saved on this request revision. An absent quantity is not guessed."""

    from cellertz.application.bindcraft_input_preparation import _count
    from cellertz.application.pdl1_container_entrypoint import DESIGN_COUNT_MAX, DESIGN_COUNT_MIN

    recorded = None
    reader = getattr(drafter, "recorded_at", None)
    if callable(reader):
        recorded = reader(request_id, revision)
    if recorded is None:
        recorded = _readonly_design_record(request_id, revision)
    if not isinstance(recorded, tuple) or len(recorded) != 2:
        return None
    draft = recorded[0]
    fields = {field.name: field for field in getattr(draft, "fields", ())}
    field = fields.get("n_candidates")
    if field is None or getattr(field, "provenance", None) != "user":
        return None
    count, blocker = _count(getattr(field, "value", None))
    if blocker is not None or type(count) is not int or isinstance(count, bool):
        return None
    if not DESIGN_COUNT_MIN <= count <= DESIGN_COUNT_MAX:
        return None
    return count


def _readonly_design_record(request_id: str, revision: int) -> tuple[Any, str] | None:
    """Read one saved draft without opening a writable connection."""

    backend = os.environ.get("CELLERTZ_DESIGN_DRAFT_STORE", "memory").strip().lower()
    if backend != "sqlite":
        return None
    raw = os.environ.get("CELLERTZ_DESIGN_DRAFT_DB", "").strip()
    if not raw or not os.path.isabs(raw) or any(char in raw for char in "\x00\n\r"):
        return None
    path = Path(raw)
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            """
            SELECT document FROM design_request_drafts
            WHERE request_id = ? AND revision = ?
            """,
            (request_id, revision),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None or not isinstance(row[0], str):
        return None
    try:
        document = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    draft_document = document.get("draft") if isinstance(document, dict) else None
    message = document.get("message") if isinstance(document, dict) else None
    if not isinstance(draft_document, dict) or not isinstance(message, str):
        return None
    from cellertz.application.request_draft import _stored_draft

    try:
        draft, message = _stored_draft(document)
    except Exception:
        return None
    if getattr(draft, "request_id", None) != request_id or getattr(draft, "revision", None) != revision:
        return None
    return draft, message


def _stored_run(document: Mapping[str, Any], parsed: Mapping[str, Any]) -> dict[str, Any] | None:
    execution = document.get("execution")
    if not isinstance(execution, dict) or execution.get("source") != "run_design":
        return None
    if execution.get("plan_id") != parsed["plan_id"] or execution.get("digest") != parsed["digest"]:
        return None
    if execution.get("revision") != parsed["revision"]:
        return None
    if (
        execution.get("terminal") is True
        and execution.get("launched") is True
        and execution.get("status") == "launch_failed"
    ):
        return None
    return execution


def _run_record(
    parsed: Mapping[str, Any],
    attempt_id: str,
    attempt_directory: str,
    gpu_index: int,
    status: str,
    diagnosis: str | None,
    launched: bool,
) -> dict[str, Any]:
    return {
        "source": "run_design",
        "request_id": parsed["request_id"],
        "plan_id": parsed["plan_id"],
        "revision": parsed["revision"],
        "digest": parsed["digest"],
        "idempotency_key": parsed["idempotency_key"],
        "attempt_id": attempt_id,
        "attempt_directory": attempt_directory,
        "gpu_index": gpu_index,
        "status": status,
        "diagnosis": diagnosis,
        "launched": launched,
        "automatic_retry": False,
        "terminal": status != "running",
        "recorded_at": datetime.now(UTC).isoformat(),
    }


def _tail_text(path: Path, limit: int = 6000) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            data = handle.read(limit)
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    if size > limit:
        text = text.split("\n", 1)[-1]
    lines = text.splitlines()
    return "\n".join(lines[-30:])


def _live_output(root: Path | None, execution: object) -> str | None:
    """Read the running attempt log. This does not start or stop a process."""

    if root is None or not isinstance(execution, dict) or execution.get("status") != "running":
        return None
    if execution.get("source") != "run_design":
        return None
    raw = execution.get("attempt_directory")
    if not isinstance(raw, str) or not raw:
        return None
    directory = Path(raw)
    try:
        directory.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    parts = [text for name in ("executor.stdout", "executor.stderr") if (text := _tail_text(directory / name).strip())]
    return "\n".join(parts) or None


def _request_results(document: Mapping[str, Any], root: Path | None) -> dict[str, Any]:
    """One request's own run records. Another request's attempts are not included."""

    current = document.get("execution")
    history = document.get("execution_history")
    linked = []
    if isinstance(current, dict) and current.get("source") == "run_design":
        linked.append(current)
    if isinstance(history, list):
        linked.extend(item for item in history if isinstance(item, dict) and item.get("source") == "run_design")
    return {
        "request_id": document.get("request_id"),
        "request_revision": document.get("request_revision"),
        "plan_id": document.get("plan_id"),
        "plan_revision": document.get("plan_revision"),
        "results": [_attempt_result(item, root) for item in linked],
    }


def _attempt_result(execution: Mapping[str, Any], root: Path | None) -> dict[str, Any]:
    result = {
        "attempt_id": execution.get("attempt_id"),
        "status": execution.get("status"),
        "diagnosis": execution.get("diagnosis"),
        "launched": execution.get("launched") is True,
        "terminal": execution.get("terminal") is True,
        "automatic_retry": False,
        "plan_id": execution.get("plan_id"),
        "revision": execution.get("revision"),
        "digest": execution.get("digest"),
        "recorded_at": execution.get("recorded_at"),
        "review": None,
        "duration": None,
        "executor_log": None,
    }
    directory = _linked_attempt_directory(root, execution.get("attempt_directory"))
    if directory is None:
        log = execution.get("executor_log")
        if isinstance(log, str) and log.strip():
            result["executor_log"] = log[-8000:]
        return result
    result["duration"] = _recorded_duration(directory)
    log = _tail_text(directory / "executor.stdout", 8000).strip()
    error = _tail_text(directory / "executor.stderr", 8000).strip()
    parts = [part for part in (log, error) if part]
    result["executor_log"] = "\n".join(parts) or None
    review_path = directory / "review.json"
    if review_path.is_file():
        result["review"] = _recorded_review(directory)
    return result


def _linked_attempt_directory(root: Path | None, raw: object) -> Path | None:
    if root is None or not isinstance(raw, str) or not raw:
        return None
    directory = Path(raw)
    try:
        directory.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return directory if directory.is_dir() else None


def _recorded_duration(directory: Path) -> str | None:
    for line in reversed(_tail_text(directory / "executor.stdout", 8000).splitlines()):
        if "took:" in line:
            return line.strip()
    return None


def _recorded_review(directory: Path) -> dict[str, Any] | None:
    from cellertz.application.attempt_evidence import AttemptFiles, load_catalog
    from cellertz.application.attempt_inspection import _ca_counts, structure_url

    loaded = load_catalog((AttemptFiles(review_json=directory / "review.json", pdb_dir=directory),))
    if len(loaded) != 1:
        return None
    attempt_id, item = next(iter(loaded.items()))
    record = item["record"]
    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
    models = {}
    for model_id, model in item["models"].items():
        recorded = model["record"]
        models[model_id] = {
            "id": model_id,
            "representative": recorded.get("representative") is True,
            "sequence": recorded.get("sequence"),
            "sha256": model["expected_sha256"],
            "hash_matches": model["actual_sha256"] == model["expected_sha256"],
            "pdb_url": structure_url(attempt_id, model_id),
            "ca_counts": _ca_counts(model["pdb_path"]) if model["actual_sha256"] else {},
        }
    return {
        "attempt_id": attempt_id,
        "candidate": record.get("design"),
        "filter_policy": record.get("filter_policy"),
        "scientific_evaluation": record.get("scientific_evaluation"),
        "platform_ipsae_ranking": record.get("platform_ipsae_ranking") is True,
        "ranking_score_computed": record.get("ranking_score_computed") is True,
        "experimental_validation": record.get("experimental_validation") is True,
        "representative": record.get("representative"),
        "chains": record.get("chains") if isinstance(record.get("chains"), dict) else {},
        "metrics": record.get("metrics") if isinstance(record.get("metrics"), list) else [],
        "omitted": provenance.get("omitted") if isinstance(provenance.get("omitted"), list) else [],
        "bindcraft_rank": provenance.get("recorded_rank") if isinstance(provenance.get("recorded_rank"), str) else None,
        "stats_csv_sha256": provenance.get("stats_csv_sha256") if isinstance(provenance.get("stats_csv_sha256"), str) else None,
        "pdb_verified": item["pdb_verified"] is True,
        "models": models,
    }


def _public_run(execution: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "attempt_id": execution.get("attempt_id"),
        "status": execution.get("status"),
        "diagnosis": execution.get("diagnosis"),
        "launched": execution.get("launched") is True,
        "automatic_retry": False,
        "plan_id": execution.get("plan_id"),
        "revision": execution.get("revision"),
        "digest": execution.get("digest"),
        "gpu_index": execution.get("gpu_index"),
        **(
            {"executor_log": execution["executor_log"]}
            if isinstance(execution.get("executor_log"), str) and execution["executor_log"].strip()
            else {}
        ),
    }


def _run_refused(status: str) -> dict[str, Any]:
    detail = "The displayed plan is no longer current. Review the current data before running."
    if status == "rejected":
        detail = "The run payload was rejected. Generation was not started."
    elif status == "plan_absent":
        detail = "The displayed plan was not found. Generation was not started."
    elif status == "plan_identity_rejected":
        detail = "The displayed plan does not belong to this request. Generation was not started."
    elif status == "configuration_incomplete":
        detail = "The configuration is not complete. Generation was not started."
    elif status == "inputs_unverified" or status == "inputs_unreadable":
        detail = "The input files are not verified. Generation was not started."
    elif status == "gpu_not_selected":
        detail = "A GPU is not selected. Generation was not started."
    elif status == "execution_not_permitted":
        detail = "The server does not allow execution. Generation was not started."
    elif status == "attempt_directory_unconfigured":
        detail = "The attempt directory is not configured. Generation was not started."
    return {
        "status": status,
        "launched": False,
        "automatic_retry": False,
        "approved": False,
        "execution_authorized": False,
        "gpu_execution": "disabled",
        "detail": detail,
    }


def _run_request(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    linked = _link_request(payload)
    key = payload.get("idempotency_key")
    if (
        linked is None
        or not isinstance(key, str)
        or not key.strip()
        or len(key.strip()) > 200
        or any(char in key for char in "\n\r\x00")
    ):
        return None
    return {**linked, "idempotency_key": key.strip()}


def _link_rejected(status: str) -> dict[str, Any]:
    detail = "The displayed plan is no longer current. Review the current data before approving."
    if status == "rejected":
        detail = "The link payload was rejected. No plan was created."
    elif status == "plan_absent":
        detail = "The displayed plan was not found. No plan was created."
    elif status == "plan_identity_rejected":
        detail = "The displayed plan does not belong to this request. No plan was copied."
    return {
        "status": status,
        "approved": False,
        "gpu_execution": "disabled",
        "launches_compute": False,
        "detail": detail,
    }


def _link_request(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    request_id = payload.get("request_id")
    request_revision = payload.get("request_revision")
    plan_id = payload.get("plan_id")
    revision = payload.get("revision")
    digest = payload.get("digest")
    if (
        not isinstance(request_id, str)
        or not request_id.strip()
        or request_id != request_id.strip()
        or any(char in request_id for char in "/\\\n\r\x00")
        or isinstance(request_revision, bool)
        or not isinstance(request_revision, int)
        or request_revision < 1
        or not isinstance(plan_id, str)
        or not plan_id.strip()
        or any(char in plan_id for char in "/\\\n\r\x00")
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        return None
    return {
        "request_id": request_id,
        "request_revision": request_revision,
        "plan_id": plan_id.strip(),
        "revision": revision,
        "digest": digest,
    }


def _approval_rejected(status: str) -> dict[str, Any]:
    detail = "The approval does not match the saved plan revision and digest."
    if status == "rejected":
        detail = "The approval payload was rejected. GPU execution stays disabled."
    elif status == "idempotency_conflict":
        detail = "This plan revision already has a different approval record."
    return {
        "status": status,
        "approved": False,
        "gpu_execution": "disabled",
        "launches_compute": False,
        "scientific_validation_established": False,
        "detail": detail,
    }


def _approval_request(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    plan_id = payload.get("plan_id")
    revision = payload.get("revision")
    digest = payload.get("digest")
    reviewer = payload.get("reviewer_id")
    reason = payload.get("reason")
    key = payload.get("idempotency_key")
    if (
        not isinstance(plan_id, str)
        or not plan_id.strip()
        or any(char in plan_id for char in "/\\\n\r\x00")
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or not isinstance(reviewer, str)
        or not reviewer.strip()
        or len(reviewer.strip()) > 200
        or any(char in reviewer for char in "\n\r\x00")
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason.strip()) > 500
        or any(char in reason for char in "\n\r\x00")
        or not isinstance(key, str)
        or not key.strip()
        or len(key.strip()) > 200
        or any(char in key for char in "\n\r\x00")
    ):
        return None
    return {
        "plan_id": plan_id.strip(),
        "revision": revision,
        "digest": digest,
        "reviewer_id": reviewer.strip(),
        "reason": reason.strip(),
        "idempotency_key": key.strip(),
    }


def _with_approval(document: dict[str, Any], published: Mapping[str, Any]) -> dict[str, Any]:
    approval = published.get("approval")
    if not isinstance(approval, dict):
        raise DesignJourneyError("approval_absent")
    updated = json.loads(json.dumps(document))
    recorded = {
        "plan_id": published.get("plan_id"),
        "revision": approval.get("revision"),
        "digest": approval.get("digest"),
        "reviewer_id": approval.get("reviewer_id"),
        "reason": approval.get("reason"),
        "recorded_at": approval.get("recorded_at"),
        "source": approval.get("source"),
        "scope": approval.get("scope"),
        "execution_authorized": False,
        "scientific_validation_established": False,
        "launches_compute": False,
        "gpu_execution": "disabled",
    }
    updated["approved"] = True
    updated["gpu_execution"] = "disabled"
    updated["scientific_validation_established"] = False
    updated["launches_compute"] = False
    updated["approval"] = recorded
    for stage in updated["stages"]:
        if not isinstance(stage, dict):
            continue
        if stage.get("stage_id") == "generation_planning" and isinstance(stage.get("output"), dict):
            stage["output"]["approved"] = True
            stage["output"]["execution_connected"] = False
            limitations = [
                item
                for item in stage.get("limitations") or []
                if "unapproved plan" not in str(item)
            ]
            note = "A person approved this saved revision. Approval does not generate a sequence."
            if note not in limitations:
                limitations.insert(0, note)
            stage["limitations"] = limitations
        if stage.get("stage_id") == "execution_authorization":
            stage["status"] = "blocked"
            stage["limitations"] = [
                "Recorded plan approval does not authorize execution.",
                "This revision was not modified.",
            ]
        if stage.get("stage_id") == "approval":
            stage["status"] = "completed"
            stage["implementation"] = "GenerationPlanService.approve"
            stage["implementation_kind"] = "human_review"
            stage["tools"] = ["generation_plan_decision"]
            stage["agent_executed"] = False
            stage["evidence_references"] = [
                {
                    "kind": "generation_plan",
                    "plan_id": recorded["plan_id"],
                    "revision": recorded["revision"],
                    "digest": recorded["digest"],
                }
            ]
            stage["output"] = recorded
            stage["limitations"] = [
                "Approval records this plan revision. It does not launch compute.",
                "Approval does not establish scientific validation.",
                _TRAJECTORY_BLOCK,
            ]
        if stage.get("stage_id") == "generation":
            stage["status"] = "blocked"
            stage["output"] = {"gpu_execution": "disabled", "launches_compute": False}
            stage["limitations"] = [_TRAJECTORY_BLOCK, "ProteinGenerationAgent was not called."]
    events = list(updated.get("events") or [])
    digest = recorded["digest"]
    if not any(
        isinstance(item, dict) and item.get("kind") == "approval_recorded" and item.get("digest") == digest
        for item in events
    ):
        events.append(
            {
                "at": recorded["recorded_at"] or datetime.now(UTC).isoformat(),
                "kind": "approval_recorded",
                "digest": digest,
                "detail": "A person approved this saved plan revision. GPU execution stays disabled.",
            }
        )
    updated["events"] = events
    updated["updated_at"] = datetime.now(UTC).isoformat()
    _remember_round_decision(updated, "approval", recorded)
    return updated


_EXPLICIT_PLAN_FIELDS = {
    "target_id": "target_identity",
    "target_identity": "target_identity",
    "hotspot_assumption": "hotspot_assumption",
    "filter_policy": "filter_policy",
    "gpu": "gpu",
    "binder_length": "binder_length",
    "seed": "seed",
    "cpu_count": "cpu_count",
    "memory_mib": "memory_mib",
    "execution_deadline_seconds": "execution_deadline_seconds",
}
_EXPLICIT_TEXT = frozenset({"target_identity", "hotspot_assumption", "filter_policy", "gpu"})
_EXPLICIT_INTEGERS = frozenset(
    {"binder_length", "seed", "cpu_count", "memory_mib", "execution_deadline_seconds"}
)
_EXPLICIT_PROVENANCE = frozenset({"user", "preserved"})
_FIELD_SOURCES = frozenset({"technical_demonstration_profile", "verified_input", "professor"})
_REQUEST_TEXT = frozenset({"objective", "generator"})
_REQUEST_INTEGERS = frozenset({"n_candidates", "length_min", "length_max"})


def _parse_completion(structured: object, acknowledgements: object) -> dict[str, Any]:
    """Split one save into plan fields, request fields, and visible conflicts."""

    plan_fields, request_fields, conflicts, touched = _walk_completion(structured)
    _apply_cross_field_conflicts(plan_fields, request_fields, conflicts, touched)
    acknowledgement = None
    acks = acknowledgements if isinstance(acknowledgements, list) else []
    acknowledged = any(item == "technical-smoke-test" for item in acks if isinstance(item, str))
    if plan_fields.get("filter_policy") == "no_filters":
        if acknowledged:
            acknowledgement = _SMOKE_ACK
        else:
            _drop_plan_field(plan_fields, "filter_policy")
            conflicts.append({"field": "filter_policy", "conflict": _ACK_CONFLICT})
            touched.add("filter_policy")
    return {
        "plan_fields": plan_fields,
        "request_fields": request_fields,
        "conflicts": conflicts,
        "touched": touched,
        "acknowledgement": acknowledgement,
    }


def _walk_completion(
    structured: object,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]], set[str]]:
    plan_fields: dict[str, Any] = {}
    request_fields: dict[str, Any] = {}
    conflicts: list[dict[str, str]] = []
    touched: set[str] = set()
    if not isinstance(structured, list):
        return plan_fields, request_fields, conflicts, touched
    seen_plan: dict[str, Any] = {}
    seen_request: dict[str, Any] = {}
    for item in structured:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        provenance = item.get("provenance")
        if not isinstance(name, str) or provenance not in _EXPLICIT_PROVENANCE:
            continue
        source = item.get("source") if item.get("source") in _FIELD_SOURCES else None
        canonical = _EXPLICIT_PLAN_FIELDS.get(name)
        if canonical is not None:
            value = _explicit_value(canonical, item.get("value"))
            if value is None:
                continue
            touched.add(canonical)
            if canonical in seen_plan and seen_plan[canonical] != value:
                conflicts.append(
                    {
                        "field": canonical,
                        "conflict": "The save states two different values. Neither value was recorded.",
                    }
                )
                _drop_plan_field(plan_fields, canonical)
                if canonical == "target_identity":
                    request_fields.pop("target_id", None)
                continue
            if canonical == "seed" and not BOOTSTRAP_SEED_MINIMUM <= value <= BOOTSTRAP_SEED_MAXIMUM:
                conflicts.append(
                    {
                        "field": "seed",
                        "conflict": (
                            "The seed is outside 1 through 4294967295. It was not recorded. "
                            "This value is the NumPy bootstrap seed, not BindCraft's trajectory seed."
                        ),
                    }
                )
                touched.add("seed")
                continue
            seen_plan[canonical] = value
            plan_fields[canonical] = value
            plan_fields[f"{canonical}_provenance"] = provenance
            record = {"value": value, "provenance": provenance}
            if isinstance(source, str):
                plan_fields[f"{canonical}_source"] = source
                record["source"] = source
            if canonical == "target_identity":
                request_fields["target_id"] = record
            continue
        if name == "sequence_limits":
            value = _explicit_sequence_limits(item.get("value"))
            if value is None:
                continue
            touched.add("sequence_limits")
            plan_fields["sequence_limits"] = value
            plan_fields["sequence_limits_provenance"] = provenance
            if isinstance(source, str):
                plan_fields["sequence_limits_source"] = source
            continue
        if name not in _REQUEST_TEXT and name not in _REQUEST_INTEGERS and name != "target_id":
            continue
        value = _request_value(name, item.get("value"))
        touched.add(name)
        if value is None:
            conflicts.append(
                {
                    "field": name,
                    "conflict": "The stated request value is not one of the supported values. It was not recorded.",
                }
            )
            request_fields.pop(name, None)
            continue
        if name in seen_request and seen_request[name] != value:
            conflicts.append(
                {
                    "field": name,
                    "conflict": "The save states two different values. Neither value was recorded.",
                }
            )
            request_fields.pop(name, None)
            continue
        seen_request[name] = value
        record = {"value": value, "provenance": provenance}
        if isinstance(source, str):
            record["source"] = source
        request_fields[name] = record
    if "target_id" in request_fields and "target_identity" not in seen_plan:
        target = request_fields["target_id"]
        plan_fields["target_identity"] = target["value"]
        plan_fields["target_identity_provenance"] = target["provenance"]
        if "source" in target:
            plan_fields["target_identity_source"] = target["source"]
        seen_plan["target_identity"] = target["value"]
        touched.add("target_identity")
    return plan_fields, request_fields, conflicts, touched


def _apply_cross_field_conflicts(
    plan_fields: dict[str, Any],
    request_fields: dict[str, Any],
    conflicts: list[dict[str, str]],
    touched: set[str],
) -> None:
    target = request_fields.get("target_id")
    stated = plan_fields.get("target_identity")
    if isinstance(target, dict) and stated not in (None, "") and target.get("value") != stated:
        conflicts.append(
            {
                "field": "target_identity",
                "conflict": (
                    "The request target and the plan target differ. "
                    "Neither value was recorded."
                ),
            }
        )
        request_fields.pop("target_id", None)
        _drop_plan_field(plan_fields, "target_identity")
        touched.add("target_identity")
    low = request_fields.get("length_min")
    high = request_fields.get("length_max")
    binder = plan_fields.get("binder_length")
    if (
        isinstance(low, dict)
        and isinstance(high, dict)
        and type(binder) is int
        and not low["value"] <= binder <= high["value"]
    ):
        conflicts.append(
            {
                "field": "binder_length",
                "conflict": (
                    "The binder length is outside the stated request length range. "
                    "The length values were not recorded."
                ),
            }
        )
        request_fields.pop("length_min", None)
        request_fields.pop("length_max", None)
        _drop_plan_field(plan_fields, "binder_length")
        touched.update({"binder_length", "length_min", "length_max"})


def _drop_plan_field(plan_fields: dict[str, Any], name: str) -> None:
    plan_fields.pop(name, None)
    plan_fields.pop(f"{name}_provenance", None)
    plan_fields.pop(f"{name}_source", None)


def _request_value(name: str, value: object) -> str | int | None:
    from cellertz.application.request_draft import GENERATORS, OBJECTIVES

    if name == "objective" and value in OBJECTIVES:
        return str(value)
    if name == "generator" and value in GENERATORS:
        return str(value)
    if name == "target_id" and isinstance(value, str) and value.strip():
        return value.strip()
    if name in _REQUEST_INTEGERS and type(value) is int and value >= 1:
        return value
    return None


def _explicit_sequence_limits(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    expected = {"max_trajectories", "num_seqs", "max_mpnn_sequences"}
    if set(value) != expected:
        return None
    limits: dict[str, int] = {}
    for name in expected:
        item = value[name]
        if type(item) is not int or isinstance(item, bool) or item < 1:
            return None
        limits[name] = item
    return limits


def _store_completion(
    document: dict[str, Any],
    _existing: dict[str, Any] | None,
    completion: Mapping[str, Any],
) -> None:
    document["request_fields"] = completion.get("request_fields") or {}
    document["conflicts"] = list(completion.get("conflicts") or [])
    if "approval_history" not in document:
        document["approval_history"] = []
    for stage in document.get("stages") or []:
        if isinstance(stage, dict) and stage.get("stage_id") == "design_request":
            output = stage.get("output") if isinstance(stage.get("output"), dict) else {}
            output = dict(output)
            output["request_fields"] = document["request_fields"]
            output["conflicts"] = document["conflicts"]
            stage["output"] = output


def _merge_maps(existing: object, incoming: object) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(existing, dict):
        merged.update(existing)
    if isinstance(incoming, dict):
        merged.update(incoming)
    return merged


def _merge_conflicts(existing: object, incoming: object, touched: object) -> list[dict[str, str]]:
    names = touched if isinstance(touched, set) else set()
    kept = []
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, dict) and item.get("field") not in names:
                kept.append(item)
    if isinstance(incoming, list):
        kept.extend(item for item in incoming if isinstance(item, dict))
    return kept


def _is_missing_ack_conflict(item: object) -> bool:
    return isinstance(item, dict) and item.get("field") == "filter_policy" and item.get("conflict") == _ACK_CONFLICT


def evaluate_filter_acknowledgement(published: Mapping[str, Any] | None) -> dict[str, Any]:
    """Judge the saved plan request text. Filter policy and provenance are not consent."""

    fields: Mapping[str, Any] = {}
    if isinstance(published, Mapping) and isinstance(published.get("fields"), Mapping):
        fields = published["fields"]
    policy = fields.get("filter_policy")
    request_text = fields.get("request_text") if isinstance(fields.get("request_text"), str) else ""
    texts = [request_text]
    requests = fields.get("user_requests")
    if isinstance(requests, list):
        texts.extend(item for item in requests if isinstance(item, str))
    recorded = any(_SMOKE_ACK in text for text in texts)
    blocker = _FILTER_ACK_BLOCKER if policy == "no_filters" and not recorded else None
    return {
        "filter_policy": policy if isinstance(policy, str) else None,
        "recorded": recorded,
        "text": _SMOKE_ACK if recorded else None,
        "plan_request_text": request_text,
        "plan_revision": None if not isinstance(published, Mapping) else published.get("revision"),
        "digest": None if not isinstance(published, Mapping) else published.get("digest"),
        "establishes_scientific_quality": False,
        "blocker": blocker,
    }


def _journey_conflict_items(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[object] = []
    stored = document.get("conflicts")
    if isinstance(stored, list):
        candidates.extend(stored)
    for stage in document.get("stages") or []:
        if not isinstance(stage, dict) or stage.get("stage_id") != "design_request":
            continue
        output = stage.get("output")
        if isinstance(output, dict) and isinstance(output.get("conflicts"), list):
            candidates.extend(output["conflicts"])
    items: list[dict[str, Any]] = []
    seen: set[tuple[object, object]] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        key = (item.get("field"), item.get("conflict"))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def _split_filter_conflicts(
    items: list[dict[str, Any]], evidence: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current: list[dict[str, Any]] = []
    historical: list[dict[str, Any]] = []
    recorded = evidence.get("text") if evidence.get("recorded") is True else None
    published_revision = evidence.get("plan_revision")
    published_digest = evidence.get("digest")
    revision = published_revision
    digest = published_digest
    for item in items:
        if _is_missing_ack_conflict(item) and recorded is not None:
            historical.append(
                {
                    "field": "filter_policy",
                    "conflict": _ACK_CONFLICT,
                    "status": "historical",
                    "plan_revision": revision,
                    "digest": digest,
                    "detail": (
                        "This conflict was stored on the journey. The current plan revision "
                        "records the technical demonstration acknowledgement, so this conflict "
                        "is not current evidence."
                    ),
                }
            )
            continue
        current.append(dict(item))
    return current, historical


def _apply_request_evidence(
    document: dict[str, Any],
    current: list[dict[str, Any]],
    historical: list[dict[str, Any]],
    evidence: Mapping[str, Any],
) -> None:
    for stage in document.get("stages") or []:
        if not isinstance(stage, dict) or stage.get("stage_id") != "design_request":
            continue
        output = stage.get("output") if isinstance(stage.get("output"), dict) else {}
        output = dict(output)
        output["conflicts"] = current
        output["historical_conflicts"] = historical
        output["acknowledgement_evidence"] = dict(evidence)
        stage["output"] = output


def _acknowledgement_recorded(published: dict[str, Any] | None, acknowledgement: str) -> bool:
    evidence = evaluate_filter_acknowledgement(published)
    return evidence["recorded"] is True and evidence["text"] == acknowledgement


def _technical_demonstration(facts: Mapping[str, Any]) -> dict[str, Any]:
    from cellertz.application.apptainer_demonstration import demonstration_contract

    contract = demonstration_contract()
    profile = "technical_demonstration_profile"
    verified = "verified_input"
    values = [
        _offer(
            "filter_policy",
            contract["filter_policy"],
            profile,
            True,
            "no_filters is for the technical demonstration and does not establish scientific quality.",
        ),
        _offer(
            "seed",
            contract["seed"],
            profile,
            True,
            "NumPy bootstrap seed passed as --cellertz-seed. BindCraft then draws each "
            "trajectory seed with np.random.randint(0, high=999999). Those values are not "
            "the same. A reviewed next round may record another seed from 1 through 4294967295.",
        ),
        _offer("binder_length", contract["binder_length"], profile, True, ""),
        _offer("cpu_count", contract["cpu_count"], profile, True, ""),
        _offer(
            "memory_mib",
            contract["memory_mib"],
            profile,
            True,
            "Host RAM in MiB. This is not GPU VRAM.",
        ),
        _offer(
            "execution_deadline_seconds",
            contract["timeout_seconds"],
            profile,
            True,
            "",
        ),
        _offer(
            "gpu",
            None,
            "host_gpu_assignment",
            False,
            "Host GPU index. The technical demonstration profile does not assign one. "
            "This is not GPU VRAM. An unselected assignment stays unselected until an explicit index is chosen.",
        ),
        _offer("n_candidates", contract["n_candidates"], profile, True, ""),
    ]
    conflicts: list[dict[str, str]] = []
    profile_limits = contract["advanced_limits"]
    verified_limits = facts.get("sequence_limits") if facts.get("verified") is True else None
    if not isinstance(verified_limits, dict):
        values.append(
            _offer(
                "sequence_limits",
                profile_limits,
                profile,
                False,
                "The verified advanced profile does not record these sequence limits. "
                "They are shown from the technical demonstration profile and are not saved.",
            )
        )
    elif bounded_sequence_limits(verified_limits) == verified_limits and verified_limits != profile_limits:
        values.append(
            _offer(
                "sequence_limits",
                verified_limits,
                verified,
                True,
                "Reviewed attempted-trajectory budget from the verified advanced profile. "
                f"max_trajectories may be 1 through {ATTEMPTED_TRAJECTORY_BUDGET_UPPER_BOUND}. "
                "num_seqs and max_mpnn_sequences stay 1. "
                "This does not change confidence gates and does not launch.",
            )
        )
    elif verified_limits == profile_limits:
        values.append(_offer("sequence_limits", verified_limits, verified, True, ""))
    else:
        conflicts.append(
            {
                "field": "sequence_limits",
                "conflict": (
                    "The technical demonstration profile and the verified advanced profile "
                    "record different sequence limits. Neither value was filled."
                ),
            }
        )
    hotspot = facts.get("hotspot_assumption")
    if facts.get("verified") is True and isinstance(hotspot, str) and hotspot.strip():
        values.append(_offer("hotspot_assumption", hotspot, verified, True, ""))
    if facts.get("verified") is True:
        values.append(_offer("target_identity", "PDL1", verified, True, ""))
        values.append(_offer("target_id", "PDL1", verified, True, ""))
    return {
        "name": "Technical demonstration",
        "profile_id": contract["profile_id"],
        "saves_on_review": False,
        "authorizes_execution": False,
        "launches_compute": False,
        "filter_policy_establishes_scientific_quality": False,
        "acknowledgement": _SMOKE_ACK,
        "values": values,
        "conflicts": conflicts,
    }


def _without_execution_authorization(document: Mapping[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(document))
    updated.pop("execution_authorization", None)
    updated.pop("execution_review", None)
    stages = []
    for stage in updated.get("stages") or []:
        if isinstance(stage, dict) and stage.get("stage_id") == "execution_authorization":
            stages.append(_execution_authorization_stage(True))
        else:
            stages.append(stage)
    updated["stages"] = stages
    return updated


def _reviewed_input_blocker(published: Mapping[str, Any], plans: Any) -> str | None:
    """Refuse a launch whose live registration no longer matches the authorized plan."""

    fields = published.get("fields") if isinstance(published.get("fields"), dict) else {}
    live = getattr(plans, "_live_inputs", None)
    identities = live() if callable(live) else None
    if isinstance(identities, dict) and fields.get("input_selection") != identities.get("input_selection"):
        return "input_identity_rejected"
    inputs = getattr(plans, "_inputs", None)
    verified_paths = getattr(inputs, "verified_paths", None)
    paths = verified_paths() if callable(verified_paths) else None
    profile = paths.get("advanced_profile") if isinstance(paths, dict) else None
    if not isinstance(profile, Path):
        return None
    seed = fields.get("seed")
    if type(seed) is not int or isinstance(seed, bool) or not BOOTSTRAP_SEED_MINIMUM <= seed <= BOOTSTRAP_SEED_MAXIMUM:
        return "reviewed_seed_rejected"
    try:
        document = json.loads(profile.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "reviewed_budget_rejected"
    if not isinstance(document, dict):
        return "reviewed_budget_rejected"
    limits = bounded_sequence_limits(
        {
            "max_trajectories": document.get("max_trajectories"),
            "num_seqs": document.get("num_seqs"),
            "max_mpnn_sequences": document.get("max_mpnn_sequences"),
        }
    )
    stated = fields.get("sequence_limits")
    if limits is None:
        return "reviewed_budget_rejected" if isinstance(stated, dict) else None
    if isinstance(stated, dict) and stated != limits:
        return "reviewed_budget_rejected"
    if stated is None and limits["max_trajectories"] != 1:
        return "reviewed_budget_rejected"
    return None


def _offer(name: str, value: object, source: str, recordable: bool, note: str) -> dict[str, Any]:
    return {"name": name, "value": value, "source": source, "recordable": recordable, "note": note}


def _explicit_value(name: str, value: object) -> str | int | None:
    if name in _EXPLICIT_TEXT and isinstance(value, str) and value.strip():
        return value.strip()
    if name in _EXPLICIT_INTEGERS and type(value) is int and value >= 1:
        return value
    return None


def _explicit_already_recorded(plans: Any, existing: Mapping[str, Any], explicit: Mapping[str, Any]) -> bool:
    published = _published_plan(plans, str(existing.get("plan_id") or ""))
    fields = published.get("fields") if isinstance(published, dict) and isinstance(published.get("fields"), dict) else {}
    return all(fields.get(key) == value for key, value in explicit.items())


def _identity(request_id: str, revision: int, text: str) -> None:
    if (
        not isinstance(request_id, str)
        or not request_id
        or request_id != request_id.strip()
        or any(char in request_id for char in "/\\\n\r\x00")
        or not isinstance(text, str)
        or not text.strip()
        or revision < 1
    ):
        raise DesignJourneyError("invalid_request")


def _json_object(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
