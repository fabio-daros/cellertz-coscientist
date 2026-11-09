"""Generation plans stay unapproved until a person records the same revision."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from cellertz.application.attempt_coordinator import AttemptCoordinator
from cellertz.application.attempt_evidence import AttemptFiles, load_catalog, workspace_root
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.generation_plan import (
    GenerationPlanService,
    RegisteredInputCatalog,
    _execution_integration_pending,
    governed_host_readiness,
    handle_design_request_plan,
    handle_design_request_plan_revision,
    handle_generation_plan,
)
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DesignDrafting,
    DeterministicDraftExtractor,
)
from cellertz.application.generation_plan_store import SQLiteGenerationPlanStore
from cellertz.application.scientific_run import ScientificRunService

_HASH = "ab" * 32


def _proposal() -> dict:
    return {
        "request_text": (
            "Generate another PDL1 binder. technical-smoke-test. Filter policy no_filters."
        ),
        "target_identity": "PDL1",
        "input_artifacts": [{"role": "target_structure", "sha256": _HASH}],
        "binder_length": 65,
        "hotspot_assumption": "Hotspot 56 is assumed and still needs confirmation.",
        "filter_policy": "no_filters",
        "seed": 7,
        "cpu_count": 4,
        "memory_mib": 16384,
        "gpu": "not_assigned",
        "execution_deadline_seconds": 14400,
    }


def _inputs(tmp_path: Path) -> tuple[RegisteredInputCatalog, dict[str, str]]:
    payloads = {
        "target_structure": b"TARGET\n",
        "settings": b'{"chain": "A"}\n',
        "advanced_profile": b'{"k": 1}\n',
    }
    files: dict[str, Path] = {}
    for role, payload in payloads.items():
        path = tmp_path / role
        path.write_bytes(payload)
        files[role] = path
    catalog = RegisteredInputCatalog(files)
    identities = catalog.identities()
    assert identities is not None
    return catalog, {item["role"]: item["sha256"] for item in identities["artifacts"]}


def _ready(tmp_path: Path, runs: object | None = None, **kwargs: object) -> tuple[GenerationPlanService, dict]:
    catalog, hashes = _inputs(tmp_path)
    service = GenerationPlanService(runs, inputs=catalog, **kwargs)  # type: ignore[arg-type]
    proposal = _proposal()
    proposal["input_artifacts"] = [{"role": role, "sha256": digest} for role, digest in hashes.items()]
    return service, proposal


def _authorizations() -> SimpleNamespace:
    record = SimpleNamespace(
        status="authorized",
        session_id="session",
        authorization_id="authorization_test",
        binding_hash="b" * 64,
    )

    def read(*, session_id: str, authorization_id: str) -> SimpleNamespace:
        if session_id == "session" and authorization_id == "authorization_test":
            return SimpleNamespace(status="authorization_observed", authorization=record)
        return SimpleNamespace(status="authorization_absent", authorization=None)

    return SimpleNamespace(read=read)


def _catalog() -> dict:
    root = workspace_root()
    return load_catalog(
        (
            AttemptFiles(
                review_json=root / "src" / "manual-attempt-2LUTUOX4.json",
                pdb_dir=root / "public" / "manual-review" / "attempt.2LUTUOX4",
            ),
        )
    )


def test_missing_filter_policy_is_a_question_and_not_no_filters() -> None:
    proposal = _proposal()
    del proposal["filter_policy"]
    proposal["request_text"] = "Generate another PDL1 binder."
    plan = GenerationPlanService().draft("conversation", proposal)
    assert plan["approved"] is False
    assert plan["status"] == "draft"
    assert "filter_policy" not in plan["fields"]
    assert any(item["field"] == "filter_policy" for item in plan["questions"])
    assert "no_filters" not in json.dumps(plan["fields"])


def test_explicit_no_filters_does_not_establish_quality_and_approval_binds_the_digest(tmp_path: Path) -> None:
    service, proposal = _ready(tmp_path)
    plan = service.draft("conversation", proposal)
    assert plan["fields"]["filter_policy"] == "no_filters"
    assert plan["fields"]["filter_policy_establishes_scientific_quality"] is False
    rejected = service.approve(
        plan_id=plan["plan_id"],
        revision=plan["revision"],
        reviewer_id="",
        reason="looks fine",
        idempotency_key="approval-1",
    )
    assert rejected["approved"] is False
    approved = service.approve(
        plan_id=plan["plan_id"],
        revision=plan["revision"],
        reviewer_id="professor",
        reason="I reviewed this revision.",
        idempotency_key="approval-1",
    )
    assert approved["approved"] is True
    assert approved["approval"]["digest"] == plan["digest"]
    assert approved["llm_approval"] is False
    changed = dict(proposal)
    changed["seed"] = 8
    revised = service.draft("conversation", changed)
    assert revised["revision"] == 2
    assert revised["approved"] is False
    assert revised["digest"] != plan["digest"]


def test_dispatch_stays_blocked_and_the_inert_executor_is_not_called(tmp_path: Path) -> None:
    class Inert:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, _run: object) -> None:
            self.calls += 1

    inert = Inert()
    runs = ScientificRunService(object(), executor=inert, execution_enabled=False)
    service, proposal = _ready(tmp_path, runs)
    plan = service.draft("conversation", proposal)
    service.approve(
        plan_id=plan["plan_id"],
        revision=plan["revision"],
        reviewer_id="professor",
        reason="Reviewed.",
        idempotency_key="approval-1",
    )
    first = service.dispatch(plan_id=plan["plan_id"], revision=plan["revision"], idempotency_key="dispatch-1")
    second = service.dispatch(plan_id=plan["plan_id"], revision=plan["revision"], idempotency_key="dispatch-1")
    assert first["status"] == "dispatch_blocked"
    assert "scientific_execution_disabled" in first["blockers"]
    assert "pdl1_worker_loss_deadline_unresolved" in first["blockers"]
    assert "scientific_authorization_required" in first["blockers"]
    assert first["run_id"] is None
    assert second["status"] == "dispatch_blocked"
    assert [event["kind"] for event in second["events"]].count("dispatch_blocked") == 1
    assert inert.calls == 0


def test_enabled_execution_without_a_host_deadline_still_does_not_run(tmp_path: Path) -> None:
    class Inert:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, _run: object) -> None:
            self.calls += 1

    inert = Inert()
    runs = ScientificRunService(object(), executor=inert, execution_enabled=True)
    service, proposal = _ready(
        tmp_path, runs, authorizations=_authorizations(), session_id="session"
    )
    plan = service.draft("conversation", proposal)
    service.approve(
        plan_id=plan["plan_id"],
        revision=1,
        reviewer_id="professor",
        reason="Reviewed.",
        idempotency_key="approval-1",
    )
    bound = service.bind_authorization(
        plan_id=plan["plan_id"],
        revision=1,
        authorization_id="authorization_test",
        reviewer_id="professor",
        reason="Bind the existing authorization. This does not grant a new one.",
        idempotency_key="authorization-1",
    )
    assert bound["authorization_bound"] is True
    assert bound["plan_approval_grants_execution"] is False
    assert bound["execution_authorized"] is False
    blocked = service.dispatch(plan_id=plan["plan_id"], revision=1, idempotency_key="dispatch-1")
    assert "pdl1_worker_loss_deadline_unresolved" in blocked["blockers"]
    assert "scientific_execution_disabled" not in blocked["blockers"]
    assert "scientific_authorization_required" not in blocked["blockers"]
    assert inert.calls == 0


def test_a_clear_gate_refers_once_and_unverified_output_is_not_completion(tmp_path: Path) -> None:
    class Runs:
        def __init__(self) -> None:
            self.creates = 0

        def dispatch_gates(self) -> tuple[str, ...]:
            return ()

        def create(self, **_kwargs: object) -> SimpleNamespace:
            self.creates += 1
            return SimpleNamespace(status="run_queued", run=SimpleNamespace(run_id="run_test", status="queued"))

    runs = Runs()
    service, proposal = _ready(tmp_path, runs, authorizations=_authorizations(), session_id="session")
    plan = service.draft("conversation", proposal)
    service.approve(
        plan_id=plan["plan_id"],
        revision=1,
        reviewer_id="professor",
        reason="Reviewed.",
        idempotency_key="approval-1",
    )
    service.bind_authorization(
        plan_id=plan["plan_id"],
        revision=1,
        authorization_id="authorization_test",
        reviewer_id="professor",
        reason="Bind the existing authorization.",
        idempotency_key="authorization-1",
    )
    referred = service.dispatch(plan_id=plan["plan_id"], revision=1, idempotency_key="dispatch-1")
    service.dispatch(plan_id=plan["plan_id"], revision=1, idempotency_key="dispatch-1")
    assert referred["run_status"] == "queued"
    assert referred["execution_connected"] is False
    assert runs.creates == 1
    listed = service.note_verified_run(
        plan["plan_id"],
        {
            "status": "completed",
            "run_id": "run_test",
            "artifacts": [{"artifact_id": "artifact_1", "sha256": _HASH, "model_id": "model1"}],
        },
    )
    assert listed["status"] == "outputs_unverified"
    execution = json.dumps({"status": "completed"}).encode()
    candidate = json.dumps(
        {"models": [{"model_id": "model1", "sha256": _HASH, "contact_count": 0}, {"model_id": "model2", "sha256": "cd" * 32, "contact_count": None}]}
    ).encode()
    verified = service.note_verified_run(
        plan["plan_id"],
        {
            "status": "completed",
            "run_id": "run_test",
            "artifacts": [
                {
                    "kind": "execution_record",
                    "artifact_id": "artifact_execution",
                    "run_id": "run_test",
                    "sha256": hashlib.sha256(execution).hexdigest(),
                    "body": execution,
                },
                {
                    "kind": "candidate_record",
                    "artifact_id": "artifact_candidate",
                    "run_id": "run_test",
                    "sha256": hashlib.sha256(candidate).hexdigest(),
                    "body": candidate,
                },
            ],
        },
    )
    assert verified["evidence"]["models"][0]["model_id"] == "model1"
    assert verified["evidence"]["models"][0]["contact_count"] == 0
    assert verified["evidence"]["models"][1]["contact_count"] is None
    assert verified["evidence"]["interface_quality"] is None
    assert verified["historical_manual_attempt"] == "attempt.2LUTUOX4"
    refined = service.refine(plan["plan_id"], "Try a stated filter other than no_filters.")
    assert refined["approved"] is False
    assert refined["revision"] == 2
    assert refined["run_id"] is None


def test_the_model_cannot_approve_a_plan_by_putting_approved_in_the_tool_call() -> None:
    service = GenerationPlanService(ScientificRunService(object(), execution_enabled=False))

    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        if not any(item.get("role") == "tool" for item in messages):
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_plan",
                        "type": "function",
                        "function": {
                            "name": "propose_generation_plan",
                            "arguments": json.dumps({"request_text": "Generate another binder.", "approved": True}),
                        },
                    }
                ],
            }
        payload = json.loads(messages[-1]["content"])
        assert payload["approved"] is False
        assert payload["llm_approval"] is False
        assert payload["historical_manual_attempt"] == "attempt.2LUTUOX4"
        return {
            "content": "The draft is not approved. Use Record approval after the missing fields are answered.",
            "tool_calls": [],
        }

    reply = AttemptCoordinator(_catalog(), complete, service).ask("Generate another binder.")
    assert reply["plan"]["approved"] is False
    assert reply["execution_connected"] is False
    assert "Record approval" in reply["text"]


def test_no_filters_from_the_tool_is_ignored_unless_the_professor_stated_it() -> None:
    proposal = _proposal()
    proposal["request_text"] = "Generate another binder."
    plan = GenerationPlanService().draft("conversation", proposal)
    assert "filter_policy" not in plan["fields"]
    assert any(item["field"] == "filter_policy" for item in plan["questions"])
    assert "no_filters" not in json.dumps(plan["fields"])


def test_a_stated_hash_is_not_verification_and_a_mismatch_blocks_approval(tmp_path: Path) -> None:
    service = GenerationPlanService()
    stated = service.draft("conversation", _proposal())
    assert stated["fields"]["input_hashes_verified_against_files"] is False
    assert stated["approved"] is False
    rejected = service.approve(
        plan_id=stated["plan_id"],
        revision=1,
        reviewer_id="professor",
        reason="Reviewed.",
        idempotency_key="approval-1",
    )
    assert rejected["status"] == "approval_rejected"
    catalog, _hashes = _inputs(tmp_path)
    mismatched = GenerationPlanService(inputs=catalog)
    proposal = _proposal()
    proposal["input_artifacts"] = [{"role": "target_structure", "sha256": _HASH}]
    plan = mismatched.draft("conversation", proposal)
    assert plan["fields"]["input_identity_mismatch"] is True
    assert str(tmp_path) not in json.dumps(plan)


def test_changed_registered_bytes_are_rejected_before_dispatch(tmp_path: Path) -> None:
    class Runs:
        def __init__(self) -> None:
            self.creates = 0

        def dispatch_gates(self) -> tuple[str, ...]:
            return ()

        def create(self, **_kwargs: object) -> SimpleNamespace:
            self.creates += 1
            return SimpleNamespace(run=SimpleNamespace(run_id="run_test", status="queued"))

    runs = Runs()
    service, proposal = _ready(tmp_path, runs, authorizations=_authorizations(), session_id="session")
    plan = service.draft("conversation", proposal)
    service.approve(
        plan_id=plan["plan_id"],
        revision=1,
        reviewer_id="professor",
        reason="Reviewed.",
        idempotency_key="approval-1",
    )
    service.bind_authorization(
        plan_id=plan["plan_id"],
        revision=1,
        authorization_id="authorization_test",
        reviewer_id="professor",
        reason="Bind the existing authorization.",
        idempotency_key="authorization-1",
    )
    (tmp_path / "target_structure").write_bytes(b"CHANGED\n")
    blocked = service.dispatch(plan_id=plan["plan_id"], revision=1, idempotency_key="dispatch-1")
    assert blocked["status"] == "dispatch_blocked"
    assert "input_identity_rejected" in blocked["blockers"]
    assert runs.creates == 0
    stored = service._plans[plan["plan_id"]]
    assert stored.approval is not None
    assert stored.revision == 1


def test_the_same_dispatch_key_proceeds_once_after_blockers_clear(tmp_path: Path) -> None:
    class Runs:
        def __init__(self) -> None:
            self.creates = 0
            self.blocked = True

        def dispatch_gates(self) -> tuple[str, ...]:
            return ("scientific_execution_disabled",) if self.blocked else ()

        def create(self, **_kwargs: object) -> SimpleNamespace:
            self.creates += 1
            return SimpleNamespace(run=SimpleNamespace(run_id="run_once", status="queued"))

    runs = Runs()
    service, proposal = _ready(tmp_path, runs, authorizations=_authorizations(), session_id="session")
    plan = service.draft("conversation", proposal)
    service.approve(
        plan_id=plan["plan_id"],
        revision=1,
        reviewer_id="professor",
        reason="Reviewed.",
        idempotency_key="approval-1",
    )
    service.bind_authorization(
        plan_id=plan["plan_id"],
        revision=1,
        authorization_id="authorization_test",
        reviewer_id="professor",
        reason="Bind the existing authorization.",
        idempotency_key="authorization-1",
    )
    first = service.dispatch(plan_id=plan["plan_id"], revision=1, idempotency_key="dispatch-1")
    replay = service.dispatch(plan_id=plan["plan_id"], revision=1, idempotency_key="dispatch-1")
    assert first["status"] == "dispatch_blocked"
    assert [event["kind"] for event in replay["events"]].count("dispatch_blocked") == 1
    runs.blocked = False
    proceeded = service.dispatch(plan_id=plan["plan_id"], revision=1, idempotency_key="dispatch-1")
    again = service.dispatch(plan_id=plan["plan_id"], revision=1, idempotency_key="dispatch-again")
    assert proceeded["run_id"] == "run_once"
    assert again["run_id"] == "run_once"
    assert runs.creates == 1


def test_a_restart_reloads_the_approved_plan_and_its_authorization(tmp_path: Path) -> None:
    store = SQLiteGenerationPlanStore(tmp_path / "plans.sqlite")
    service, proposal = _ready(
        tmp_path, store=store, authorizations=_authorizations(), session_id="session"
    )
    plan = service.draft("conversation", proposal)
    service.approve(
        plan_id=plan["plan_id"],
        revision=1,
        reviewer_id="professor",
        reason="Reviewed.",
        idempotency_key="approval-1",
    )
    service.bind_authorization(
        plan_id=plan["plan_id"],
        revision=1,
        authorization_id="authorization_test",
        reviewer_id="professor",
        reason="Bind the existing authorization.",
        idempotency_key="authorization-1",
    )
    service.close()
    restored = GenerationPlanService(
        store=SQLiteGenerationPlanStore(tmp_path / "plans.sqlite"),
        inputs=RegisteredInputCatalog(
            {
                "target_structure": tmp_path / "target_structure",
                "settings": tmp_path / "settings",
                "advanced_profile": tmp_path / "advanced_profile",
            }
        ),
        authorizations=_authorizations(),
        session_id="session",
    )
    again = restored.draft("conversation", proposal)
    assert again["plan_id"] == plan["plan_id"]
    assert again["approved"] is True
    assert again["authorization_bound"] is True
    assert again["revision"] == 1
    assert any(event["kind"] == "authorization_bound" for event in again["events"])
    restored.close()


def _registered_root(tmp_path: Path) -> Path:
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "target.pdb").write_bytes(b"TARGET\n")
    (root / "settings.json").write_bytes(b'{"chain": "A"}\n')
    (root / "advanced.json").write_bytes(b'{"k": 1}\n')
    return root


def _file_hashes() -> dict[str, str]:
    canonical = (json.dumps({"k": 1}, indent=2, sort_keys=True) + "\n").encode()
    return {
        "target_structure": hashlib.sha256(b"TARGET\n").hexdigest(),
        "settings": hashlib.sha256(b'{"chain": "A"}\n').hexdigest(),
        "advanced_profile": hashlib.sha256(canonical).hexdigest(),
    }


def _startup_environment(monkeypatch, tmp_path: Path, *, input_root: str | None) -> None:
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_EXECUTION", "disabled")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE", "memory")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_STORE", "memory")
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_STORE", "memory")
    monkeypatch.setenv("CELLERTZ_GENERATION_PLAN_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_GENERATION_PLAN_DB", str(tmp_path / "plans.sqlite"))
    if input_root is None:
        monkeypatch.delenv("CELLERTZ_PDL1_INPUT_ROOT", raising=False)
    else:
        monkeypatch.setenv("CELLERTZ_PDL1_INPUT_ROOT", input_root)


def test_startup_without_an_input_root_does_not_verify_a_stated_hash(tmp_path: Path, monkeypatch) -> None:
    from cellertz.conversation.__main__ import main

    _startup_environment(monkeypatch, tmp_path, input_root=None)
    seen: dict[str, object] = {}

    def started(*_args, **kwargs):
        plans = kwargs["generation_plans"]
        runs = kwargs["scientific_runs"]
        seen["runs_enabled"] = runs._execution_enabled
        seen["executor"] = runs._executor
        seen["plan"] = plans.draft("conversation", _proposal())

    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    main()
    plan = seen["plan"]
    assert seen["runs_enabled"] is False
    assert seen["executor"] is None
    assert plan["input_registration"] == "input_root_unconfigured"
    assert plan["fields"]["input_hashes_verified_against_files"] is False
    assert plan["fields"]["stated_input_artifacts"][0]["sha256"] == _HASH
    assert "verified" not in plan["fields"]["stated_input_artifacts"][0]
    assert "CELLERTZ_PDL1_INPUT_ROOT" in json.dumps(plan["questions"])
    assert plan["approved"] is False


def test_startup_rejects_a_relative_input_root(tmp_path: Path, monkeypatch) -> None:
    from cellertz.conversation.__main__ import main

    _startup_environment(monkeypatch, tmp_path, input_root="inputs")
    seen: dict[str, object] = {}

    def started(*_args, **kwargs):
        seen["plan"] = kwargs["generation_plans"].draft("conversation", _proposal())

    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    main()
    plan = seen["plan"]
    assert plan["input_registration"] == "input_root_invalid"
    assert plan["fields"]["input_hashes_verified_against_files"] is False
    assert "verified" not in plan["fields"]["stated_input_artifacts"][0]
    assert "absolute directory" in json.dumps(plan["questions"])


def test_startup_registered_files_bind_approval_and_recheck_after_restart(
    tmp_path: Path, monkeypatch
) -> None:
    from cellertz.conversation.__main__ import main

    root = _registered_root(tmp_path)
    hashes = _file_hashes()
    _startup_environment(monkeypatch, tmp_path, input_root=str(root))
    state: dict[str, object] = {}

    def started(*_args, **kwargs):
        plans = kwargs["generation_plans"]
        runs = kwargs["scientific_runs"]
        assert runs._execution_enabled is False
        assert runs._executor is None
        if "plan_id" not in state:
            proposal = _proposal()
            proposal.pop("input_artifacts")
            drafted = plans.draft("startup-conversation", proposal)
            state["plan_id"] = drafted["plan_id"]
            state["revision"] = drafted["revision"]
            state["draft"] = drafted
            approved = plans.approve(
                plan_id=drafted["plan_id"],
                revision=drafted["revision"],
                reviewer_id="professor",
                reason="The registered files were reviewed.",
                idempotency_key="approval-startup",
            )
            state["approved"] = approved
            (root / "target.pdb").write_bytes(b"CHANGED\n")
            state["blocked"] = plans.dispatch(
                plan_id=drafted["plan_id"],
                revision=drafted["revision"],
                idempotency_key="dispatch-startup",
            )
            return
        state["restored"] = plans.dispatch(
            plan_id=state["plan_id"],
            revision=state["revision"],
            idempotency_key="dispatch-startup",
        )

    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    main()
    main()
    drafted = state["draft"]
    found = {item["role"]: item for item in drafted["fields"]["input_artifacts"]}
    assert drafted["input_registration"] == "verified"
    assert found["target_structure"]["sha256"] == hashes["target_structure"]
    assert found["settings"]["sha256"] == hashes["settings"]
    assert found["advanced_profile"]["sha256"] == hashes["advanced_profile"]
    assert found["target_structure"]["verified"] is True
    assert drafted["fields"]["input_selection"]["target_structure_sha256"] == hashes["target_structure"]
    approved = state["approved"]
    assert approved["approved"] is True
    assert approved["approval"]["digest"] == drafted["digest"]
    blocked = state["blocked"]
    assert blocked["run_id"] is None
    assert blocked["execution_connected"] is False
    assert "input_identity_rejected" in blocked["blockers"]
    assert "scientific_execution_disabled" in blocked["blockers"]
    assert blocked["input_registration"] == "input_files_changed"
    assert blocked["fields"]["input_selection"]["target_structure_sha256"] == hashes["target_structure"]
    restored = state["restored"]
    assert restored["plan_id"] == state["plan_id"]
    assert restored["revision"] == state["revision"]
    assert restored["approved"] is True
    assert "input_identity_rejected" in restored["blockers"]
    assert restored["fields"]["input_selection"]["target_structure_sha256"] == hashes["target_structure"]
    assert restored["run_id"] is None
    assert restored["execution_connected"] is False
    assert str(root) not in json.dumps(restored)


_TURN_ONE = "Generate another PDL1 binder. technical-smoke-test. Filter policy no_filters."
_TURN_TWO = (
    "I confirm the three sequence limits max_trajectories, num_seqs, and "
    "max_mpnn_sequences and the advanced profile."
)


def test_follow_up_keeps_no_filters_and_does_not_treat_confirmation_as_a_question(tmp_path: Path) -> None:
    catalog, _hashes = _inputs(tmp_path)
    (tmp_path / "advanced_profile").write_text(
        json.dumps({"max_trajectories": 1, "num_seqs": 1, "max_mpnn_sequences": 1}),
        encoding="utf-8",
    )
    verified = catalog.identities()
    assert verified is not None
    service = GenerationPlanService(inputs=catalog)
    calls = {"n": 0}

    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "draft",
                        "type": "function",
                        "function": {
                            "name": "propose_generation_plan",
                            "arguments": json.dumps(
                                {
                                    "request_text": "Summary: every question is resolved and no_filters applies.",
                                    "target_identity": "PDL1",
                                    "binder_length": 65,
                                    "filter_policy": "no_filters",
                                    "seed": 1,
                                    "cpu_count": 4,
                                    "memory_mib": 32768,
                                    "gpu": "1",
                                    "execution_deadline_seconds": 7200,
                                    "unresolved_questions": ["Model note that is not a user question."],
                                }
                            ),
                        },
                    }
                ],
            }
        if calls["n"] == 2:
            return {"content": "Revision 1 is recorded.", "tool_calls": []}
        if calls["n"] == 3:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "revise",
                        "type": "function",
                        "function": {
                            "name": "propose_generation_plan",
                            "arguments": json.dumps(
                                {
                                    "request_text": "All questions were resolved.",
                                    "unresolved_questions": [
                                        "Which hotspot assumption should be recorded?",
                                        _TURN_TWO,
                                    ],
                                }
                            ),
                        },
                    }
                ],
            }
        return {"content": "All questions were resolved.", "tool_calls": []}

    coordinator = AttemptCoordinator(_catalog(), complete, service)
    first = coordinator.ask(_TURN_ONE, "coord-plan-revision")
    assert first["plan"]["revision"] == 1
    assert first["plan"]["fields"]["filter_policy"] == "no_filters"
    assert first["plan"]["approved"] is False
    second = coordinator.ask(_TURN_TWO, first["conversation_id"])
    plan = second["plan"]
    assert plan["revision"] == 2
    assert plan["approved"] is False
    assert plan["execution_connected"] is False
    assert plan["plan_approval_grants_execution"] is False
    assert plan["fields"]["filter_policy"] == "no_filters"
    assert plan["fields"]["filter_policy_establishes_scientific_quality"] is False
    assert plan["fields"]["seed"] == 1
    assert plan["fields"]["binder_length"] == 65
    assert plan["fields"]["cpu_count"] == 4
    assert plan["fields"]["memory_mib"] == 32768
    assert plan["fields"]["gpu"] == "1"
    assert plan["fields"]["target_identity"] == "PDL1"
    assert plan["fields"]["user_requests"] == [_TURN_ONE, _TURN_TWO]
    assert "technical-smoke-test" in plan["fields"]["request_text"]
    assert plan["fields"]["sequence_limits"] == verified["sequence_limits"]
    roles = {item["role"]: item["sha256"] for item in plan["fields"]["input_artifacts"]}
    assert roles == {item["role"]: item["sha256"] for item in verified["artifacts"]}
    assert any(item["field"] == "hotspot_assumption" for item in plan["questions"])
    assert all(_TURN_TWO not in item["question"] for item in plan["questions"])
    assert "all questions were resolved" not in second["text"].casefold()
    assert "hotspot" in second["text"].casefold()
    assert [event["kind"] for event in plan["events"]] == ["drafted", "revised"]


def test_governed_runtime_stays_docker_and_separate_from_the_manual_sif() -> None:
    report = governed_host_readiness()
    assert report["governed_runtime"] == "docker"
    assert report["governed_profile_id"] == "pdl1-bindcraft-docker-v1"
    assert report["manual_attempt_runtime"] == "apptainer"
    assert report["docker_is_the_supported_governed_path"] is True
    assert report["manual_launcher_is_not_the_governed_executor"] is True
    assert report["scientific_execution"] == "disabled"
    assert report["unresolved_gate"] == "pdl1_worker_loss_deadline_unresolved"


def test_a_design_request_plan_stays_bound_to_its_revision(tmp_path: Path) -> None:
    session = pdl1_prototype_session()
    issued = iter(["request_one", "request_two"])
    drafts = DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        request_ids=lambda: next(issued),
    )
    first = drafts.revise(DesignDraftCommand(session.session_id, "design a new binder to PDL1"))
    second = drafts.revise(DesignDraftCommand(session.session_id, "design a new binder"))
    assert first.draft is not None and second.draft is not None
    assert first.draft.revision == 1 and second.draft.revision == 1
    configuration = SimpleNamespace(read=lambda: SimpleNamespace(tool_name="bindcraft", target_scope="PDL1"))
    store = SQLiteGenerationPlanStore(tmp_path / "plans.sqlite")
    plans = GenerationPlanService(store=store)
    before = handle_design_request_plan(
        plans, drafts, method="GET", path="/api/generation-plan?request_id=request_one", configuration=configuration
    )
    assert before[0] == 200
    assert before[1]["plans"] == []
    assert before[1]["summary"]["target"] == {"value": "PDL1", "source": "researcher", "pending": False}
    assert before[1]["summary"]["candidates_requested"]["pending"] is True
    assert before[1]["summary"]["generation_method"] == {
        "value": "BindCraft",
        "source": "server_configuration",
        "pending": False,
    }
    assert before[1]["model_decided"] is False and before[1]["agents_executed"] is False
    created = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_one","revision":1}',
        configuration=configuration,
    )
    assert created[0] == 200 and created[1]["created"] is True
    plan = created[1]["plan"]
    assert plan["status"] == "draft"
    assert plan["approved"] is False
    assert plan["execution_authorized"] is False
    assert plan["scientific_execution"] == "disabled"
    assert plan["design_request_id"] == "request_one"
    assert plan["design_request_revision"] == 1
    assert plan["fields"]["target_identity"] == "PDL1"
    assert "binder_length" not in plan["fields"]
    assert "n_candidates" not in plan["fields"]
    assert "generator" not in plan["fields"]
    assert any(item["field"] == "binder_length" for item in plan["questions"])
    reopened = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_one","revision":1}',
        configuration=configuration,
    )
    assert reopened[1]["created"] is False
    assert reopened[1]["plan"]["plan_id"] == plan["plan_id"]
    assert reopened[1]["plan"]["revision"] == 1
    other = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_two","revision":1}',
        configuration=configuration,
    )
    assert other[1]["plan"]["plan_id"] != plan["plan_id"]
    assert "target_identity" not in other[1]["plan"]["fields"]
    assert other[1]["summary"]["generation_method"]["pending"] is True
    listed = handle_design_request_plan(
        plans, drafts, method="GET", path="/api/generation-plan?request_id=request_one"
    )
    assert [item["plan_id"] for item in listed[1]["plans"]] == [plan["plan_id"]]
    revised = drafts.revise(
        DesignDraftCommand(
            session.session_id,
            "design a new binder to PDL1",
            request_id="request_one",
            revision=1,
            clarifications=(("n_candidates", 2),),
        )
    )
    assert revised.draft is not None and revised.draft.revision == 2
    stale = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_one","revision":1}',
        configuration=configuration,
    )
    assert stale[0] == 409 and stale[1]["status"] == "revision_conflict"
    assert plans.plans_for_design_request("request_one")[0]["plan_id"] == plan["plan_id"]
    current = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_one","revision":2}',
        configuration=configuration,
    )
    assert current[1]["created"] is True
    assert current[1]["summary"]["candidates_requested"] == {"value": 2, "source": "researcher", "pending": False}
    assert current[1]["plan"]["fields"]["target_identity"] == "PDL1"
    assert "n_candidates" not in current[1]["plan"]["fields"]
    both = plans.plans_for_design_request("request_one")
    assert [item["design_request_revision"] for item in both] == [2, 1]
    assert both[1]["plan_id"] == plan["plan_id"]
    plans.close()
    reloaded = GenerationPlanService(store=SQLiteGenerationPlanStore(tmp_path / "plans.sqlite"))
    kept = reloaded.plans_for_design_request("request_one")
    assert [item["plan_id"] for item in kept] == [item["plan_id"] for item in both]
    assert reloaded.plans_for_design_request("request_two")[0]["plan_id"] == other[1]["plan"]["plan_id"]
    reloaded.close()


def test_plan_parameter_revision_keeps_the_request_and_rejects_a_stale_revision(tmp_path: Path) -> None:
    session = pdl1_prototype_session()
    issued = iter(["request_one", "request_two"])
    drafts = DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        request_ids=lambda: next(issued),
    )
    message = "design a new binder to PDL1"
    first = drafts.revise(DesignDraftCommand(session.session_id, message))
    second = drafts.revise(DesignDraftCommand(session.session_id, "design a new binder to CD22"))
    assert first.draft is not None and second.draft is not None
    configuration = SimpleNamespace(read=lambda: SimpleNamespace(tool_name="bindcraft", target_scope="PDL1"))
    plans = GenerationPlanService(store=SQLiteGenerationPlanStore(tmp_path / "plans.sqlite"))
    created = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_one","revision":1}',
        configuration=configuration,
    )
    other = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_two","revision":1}',
        configuration=configuration,
    )
    plan = created[1]["plan"]
    original_digest = plan["digest"]
    original_text = plan["fields"]["request_text"]
    assert original_text == message
    approved = plans.approve(
        plan_id=plan["plan_id"],
        revision=1,
        reviewer_id="professor",
        reason="record this revision only",
        idempotency_key="approval-1",
        require_execution_readiness=False,
    )
    assert approved["approved"] is True
    assert approved["execution_authorized"] is False
    stored = plans._plans[plan["plan_id"]]
    stored.authorization_id = "auth_old"
    plans._persist(stored)

    unchanged = _revise(
        plans,
        drafts,
        plan,
        {"scientific": {}, "technical": {}},
        configuration,
    )
    assert unchanged[0] == 200
    assert unchanged[1]["plan_revision_changed"] is False
    assert unchanged[1]["plan"]["revision"] == 1
    assert unchanged[1]["plan"]["digest"] == plans.published(plan["plan_id"])["digest"]
    assert unchanged[1]["plan"]["approved"] is True

    rejected = _revise(
        plans,
        drafts,
        plan,
        {"scientific": {"binder_length": 0}, "technical": {}},
        configuration,
    )
    assert rejected[0] == 400
    assert rejected[1]["reason"] == "binder_length_rejected"
    assert rejected[1]["bindcraft_capacity_recorded"] is False
    assert plans.published(plan["plan_id"])["revision"] == 1

    for payload, reason in (
        ({"scientific": {"hotspot": {"mode": "residues", "value": " "}}}, "hotspot_rejected"),
        ({"scientific": {"hotspot": {"mode": "none", "value": ""}}}, "hotspot_rejected"),
        ({"scientific": {"filter_policy": {"mode": "scientific", "policy": "strict"}}}, "filter_policy_unsupported"),
        ({"technical": {"seed": -1}}, "seed_rejected"),
        ({"technical": {"seed": 2**32}}, "seed_rejected"),
        ({"technical": {"gpu": ""}}, "gpu_rejected"),
        ({"technical": {"gpu": "8"}}, "gpu_rejected"),
        ({"technical": {"cpu_count": 65}}, "cpu_count_rejected"),
        ({"technical": {"memory_mib": 262145}}, "memory_mib_rejected"),
        ({"technical": {"execution_deadline_seconds": 86401}}, "execution_deadline_rejected"),
        ({"request_text": "technical-smoke-test no_filters"}, "rejected"),
    ):
        response = _revise(plans, drafts, plan, payload, configuration)
        assert response[0] == 400, payload
        assert response[1]["reason"] == reason
        assert response[1]["scientific_execution"] == "disabled"
    assert plans.published(plan["plan_id"])["revision"] == 1
    assert plans._plans[plan["plan_id"]].fields["request_text"] == message

    smuggled = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_one","revision":1,"seed":1}',
        configuration=configuration,
    )
    assert smuggled[0] == 400

    saved = _revise(
        plans,
        drafts,
        plan,
        {
            "scientific": {
                "binder_length": 80,
                "hotspot": {"mode": "none"},
                "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
            },
            "technical": {
                "seed": 0,
                "gpu": "not_assigned",
                "cpu_count": 4,
                "memory_mib": 32768,
                "execution_deadline_seconds": 7200,
            },
            "adopted_origins": {
                "binder_length": {
                    "source": "hotspot_identification_agent",
                    "suggestion_id": "suggestion_one",
                },
            },
        },
        configuration,
    )
    assert saved[0] == 200
    assert saved[1]["plan_revision_changed"] is True
    revised = saved[1]["plan"]
    assert revised["plan_id"] == plan["plan_id"]
    assert revised["revision"] == 2
    assert revised["digest"] != original_digest
    assert revised["design_request_id"] == "request_one"
    assert revised["design_request_revision"] == 1
    assert revised["approved"] is False
    assert revised["execution_authorized"] is False
    assert revised["scientific_execution"] == "disabled"
    assert revised["status"] == "draft"
    assert revised["fields"]["request_text"] == message
    assert "technical-smoke-test" not in revised["fields"]["request_text"]
    assert revised["fields"]["binder_length"] == 80
    assert revised["fields"]["binder_length_provenance"] == "adopted_suggestion"
    assert revised["fields"]["binder_length_source"] == "hotspot_identification_agent"
    assert revised["fields"]["binder_length_suggestion_id"] == "suggestion_one"
    assert revised["fields"]["cpu_count_source"] == "professor"
    assert revised["approved"] is False
    assert revised["fields"]["binder_length_bindcraft_capacity_recorded"] is False
    assert revised["fields"]["binder_length_execution_integration"] == "pending"
    assert revised["fields"]["hotspot_strategy"] == "none"
    assert "hotspot_assumption" not in revised["fields"]
    assert revised["fields"]["hotspot_assumption_execution_support"] == "not_connected"
    assert revised["fields"]["filter_policy"] == "no_filters"
    assert revised["fields"]["filter_policy_mode"] == "technical_demonstration"
    assert revised["fields"]["filter_policy_establishes_scientific_quality"] is False
    assert revised["fields"]["filter_policy_is_technical_smoke_test"] is True
    assert revised["fields"]["seed"] == 0
    assert revised["fields"]["seed_plan_bootstrap_minimum"] == 1
    assert revised["fields"]["seed_reproduces_bindcraft_trajectory"] is False
    assert revised["fields"]["gpu"] == "not_assigned"
    assert revised["fields"]["gpu_execution_support"] == "unsupported"
    assert revised["approval"] is None
    assert revised["approval_history"][0]["revision"] == 1
    assert revised["revision_history"][0]["revision"] == 1
    assert revised["revision_history"][0]["approval"]["reason"] == "record this revision only"
    assert all(item["field"] != "binder_length" for item in revised["questions"])
    assert all(item["field"] != "hotspot_assumption" for item in revised["questions"])
    assert all(item["field"] != "filter_policy" for item in revised["questions"])

    blocked = plans.approve(
        plan_id=plan["plan_id"],
        revision=2,
        reviewer_id="professor",
        reason="fields are filled",
        idempotency_key="approval-2",
        expected_digest=revised["digest"],
    )
    assert blocked["status"] == "approval_rejected"
    assert blocked["approved"] is False

    repeat = _revise(
        plans,
        drafts,
        revised,
        {
            "scientific": {
                "binder_length": 80,
                "hotspot": {"mode": "none"},
                "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
            },
            "technical": {
                "seed": 0,
                "gpu": "not_assigned",
                "cpu_count": 4,
                "memory_mib": 32768,
                "execution_deadline_seconds": 7200,
            },
        },
        configuration,
    )
    assert repeat[0] == 200
    assert repeat[1]["plan_revision_changed"] is False
    assert repeat[1]["plan"]["revision"] == 2
    assert repeat[1]["plan"]["digest"] == revised["digest"]

    stale = _revise(
        plans,
        drafts,
        plan,
        {"scientific": {"binder_length": 90}},
        configuration,
    )
    assert stale[0] == 409
    assert stale[1]["status"] == "plan_revision_conflict"
    assert plans.published(plan["plan_id"])["revision"] == 2
    assert plans.published(plan["plan_id"])["digest"] == revised["digest"]
    assert plans.published(plan["plan_id"])["fields"]["binder_length"] == 80

    kept_request = drafts.recorded("request_one")
    assert kept_request is not None
    assert kept_request[0].revision == 1
    assert kept_request[1] == message
    other_plan = plans.published(other[1]["plan"]["plan_id"])
    assert other_plan is not None
    assert other_plan["revision"] == 1
    assert other_plan["fields"]["request_text"] == "design a new binder to CD22"
    assert "binder_length" not in other_plan["fields"]

    plans.close()
    reloaded = GenerationPlanService(store=SQLiteGenerationPlanStore(tmp_path / "plans.sqlite"))
    loaded = reloaded.published(plan["plan_id"])
    assert loaded is not None
    assert loaded["revision"] == 2
    assert loaded["digest"] == revised["digest"]
    assert loaded["fields"]["request_text"] == message
    assert loaded["fields"]["seed"] == 0
    assert loaded["fields"]["hotspot_strategy"] == "none"
    assert "hotspot_assumption" not in loaded["fields"]
    assert loaded["fields"]["hotspot_assumption_execution_support"] == "not_connected"
    assert all(item["field"] != "hotspot_assumption" for item in loaded["questions"])
    untouched = reloaded.published(other[1]["plan"]["plan_id"])
    assert untouched is not None
    assert "hotspot_strategy" not in untouched["fields"]
    assert any(item["field"] == "hotspot_assumption" for item in untouched["questions"])
    assert loaded["approval"] is None
    assert loaded["authorization_id"] is None
    assert loaded["execution_authorized"] is False
    raw = next(
        parsed
        for (document,) in __import__("sqlite3").connect(tmp_path / "plans.sqlite").execute(
            "SELECT document FROM plans"
        )
        if (parsed := json.loads(document))["plan_id"] == plan["plan_id"]
    )
    assert raw["authorization_id"] is None
    assert raw["revision_history"][0]["authorization_id"] == "auth_old"
    assert raw["approval_history"][0]["revision"] == 1
    reloaded.close()


def test_applied_values_and_reproducibility_limits_are_not_integration_failures() -> None:
    fields = {
        "binder_length_execution_integration": "pending",
        "binder_length_execution_support": "not_connected",
        "seed_execution_integration": "pending",
        "seed_execution_support": "numpy_bootstrap_only",
        "execution_deadline_seconds_execution_integration": "pending",
        "execution_deadline_seconds_execution_support": "unsupported",
        "gpu_execution_integration": "pending",
        "gpu_execution_support": "not_connected",
    }
    assert _execution_integration_pending(fields) is False
    fields["gpu_execution_support"] = "unsupported"
    assert _execution_integration_pending(fields) is True


def test_decision_binds_the_digest_and_does_not_authorize(tmp_path: Path) -> None:
    session = pdl1_prototype_session()
    drafts = DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        request_ids=lambda: "request_one",
    )
    opened = drafts.revise(DesignDraftCommand(session.session_id, "design a new binder to PDL1"))
    assert opened.draft is not None
    configuration = SimpleNamespace(read=lambda: SimpleNamespace(tool_name="bindcraft", target_scope="PDL1"))
    catalog, _hashes = _inputs(tmp_path)
    plans = GenerationPlanService(
        store=SQLiteGenerationPlanStore(tmp_path / "plans.sqlite"),
        inputs=catalog,
    )
    created = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=b'{"request_id":"request_one","revision":1}',
        configuration=configuration,
    )
    plan = created[1]["plan"]
    revised = _revise(
        plans,
        drafts,
        plan,
        {
            "scientific": {
                "binder_length": 65,
                "hotspot": {"mode": "none"},
                "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
            },
            "technical": {
                "seed": 65,
                "gpu": "0",
                "cpu_count": 4,
                "memory_mib": 32768,
                "execution_deadline_seconds": 7200,
            },
        },
        configuration,
    )
    assert revised[0] == 200
    current = revised[1]["plan"]
    assert current["status"] == "ready_for_review"
    mismatch = handle_generation_plan(
        plans,
        json.dumps(
            {
                "plan_id": current["plan_id"],
                "revision": current["revision"],
                "reviewer_id": "professor",
                "reason": "approve this digest",
                "idempotency_key": "approval-digest",
                "expected_digest": "0" * 64,
            }
        ).encode(),
        "decision",
    )
    assert mismatch[1]["status"] == "approval_rejected"
    assert mismatch[1]["approved"] is False
    recorded = handle_generation_plan(
        plans,
        json.dumps(
            {
                "plan_id": current["plan_id"],
                "revision": current["revision"],
                "reviewer_id": "professor",
                "reason": "approve this digest",
                "idempotency_key": "approval-digest",
                "expected_digest": current["digest"],
            }
        ).encode(),
        "decision",
    )
    assert recorded[1]["approved"] is True
    assert recorded[1]["execution_authorized"] is False
    assert recorded[1]["plan_approval_grants_execution"] is False
    assert recorded[1]["scientific_execution"] == "disabled"
    assert recorded[1]["authorization_id"] is None
    plans.close()


def _revise(plans, drafts, plan, payload, configuration):
    body = {
        "request_id": plan["design_request_id"],
        "request_revision": plan["design_request_revision"],
        "plan_id": plan["plan_id"],
        "expected_plan_revision": plan["revision"],
    }
    body.update(payload)
    return handle_design_request_plan_revision(
        plans,
        drafts,
        body=json.dumps(body).encode(),
        configuration=configuration,
    )
