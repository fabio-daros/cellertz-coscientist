"""Plan revisions prepare BindCraft inputs without launching BindCraft."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from cellertz.application.bindcraft_input_preparation import (
    consume_prepared_launch,
    prepare_bindcraft_inputs,
    preparation_is_current,
)
from cellertz.application.pdl1_docker import Pdl1DockerExecutor, ScriptedDockerControl
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.generation_plan import (
    GenerationPlanService,
    handle_design_request_plan,
    handle_design_request_plan_revision,
)
from cellertz.application.generation_plan_store import SQLiteGenerationPlanStore
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DesignDrafting,
    DeterministicDraftExtractor,
)

EXAMPLE = {
    "design_path": "/content/drive/My Drive/BindCraft/PDL1/",
    "binder_name": "PDL1",
    "starting_pdb": "/content/bindcraft/example/PDL1.pdb",
    "chains": "A",
    "target_hotspot_residues": "56",
    "lengths": [65, 150],
    "number_of_final_designs": 100,
}


def _sources(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, bytes]]:
    root = tmp_path / "sources"
    root.mkdir()
    settings = root / "settings.json"
    target = root / "target.pdb"
    advanced = root / "advanced.json"
    settings.write_text(json.dumps(EXAMPLE, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    target.write_bytes(b"ATOM\n")
    advanced.write_text('{"max_trajectories": 4}\n', encoding="utf-8")
    originals = {
        "settings": settings.read_bytes(),
        "target": target.read_bytes(),
        "advanced": advanced.read_bytes(),
    }
    return settings, target, advanced, originals


def _plan(tmp_path: Path, *, candidates: int | None = 3):
    session = pdl1_prototype_session()
    drafts = DesignDrafting(
        {session.session_id: session},
        DeterministicDraftExtractor(),
        request_ids=lambda: "request_prepared",
    )
    opened = drafts.revise(
        DesignDraftCommand(session.session_id, "#5 design an new binder to PDL1")
    )
    assert opened.draft is not None
    counted = opened
    if candidates is not None:
        counted = drafts.revise(
            DesignDraftCommand(
                session.session_id,
                "#5 design an new binder to PDL1",
                request_id=opened.draft.request_id,
                revision=opened.draft.revision,
                clarifications=(("n_candidates", candidates),),
            )
        )
        assert counted.draft is not None
    configuration = SimpleNamespace(
        read=lambda: SimpleNamespace(tool_name="bindcraft", target_scope="PDL1")
    )
    plans = GenerationPlanService(store=SQLiteGenerationPlanStore(tmp_path / "plans.sqlite"))
    created = handle_design_request_plan(
        plans,
        drafts,
        method="POST",
        path="/api/generation-plan",
        body=json.dumps(
            {"request_id": counted.draft.request_id, "revision": counted.draft.revision}
        ).encode(),
        configuration=configuration,
    )
    assert created[0] == 200
    return plans, drafts, created[1]["plan"], configuration


def _revise(plans, drafts, plan, scientific, configuration, technical=None):
    body = {
        "request_id": plan["design_request_id"],
        "request_revision": plan["design_request_revision"],
        "plan_id": plan["plan_id"],
        "expected_plan_revision": plan["revision"],
        "scientific": scientific,
        "technical": {} if technical is None else technical,
    }
    saved = handle_design_request_plan_revision(
        plans, drafts, body=json.dumps(body).encode(), configuration=configuration
    )
    assert saved[0] == 200 and saved[1]["plan_revision_changed"] is True
    return saved[1]["plan"]


def _prepare(tmp_path, plans, drafts, plan, sources):
    settings, target, advanced, _originals = sources
    return prepare_bindcraft_inputs(
        plans,
        drafts,
        request_id=plan["design_request_id"],
        request_revision=plan["design_request_revision"],
        plan_id=plan["plan_id"],
        expected_plan_revision=plan["revision"],
        expected_digest=plan["digest"],
        settings_path=settings,
        target_path=target,
        advanced_path=advanced,
        destination=tmp_path / "prepared",
    )


def test_saved_choices_replace_the_example_counts_length_hotspot_and_filter(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    plans, drafts, plan, configuration = _plan(tmp_path)
    revised = _revise(
        plans,
        drafts,
        plan,
        {
            "binder_length": 90,
            "hotspot": {"mode": "residues", "value": "A12"},
            "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
        },
        configuration,
    )
    prepared = _prepare(tmp_path, plans, drafts, revised, sources)
    written = json.loads(Path(prepared["derived_settings"]).read_text(encoding="utf-8"))
    assert written["number_of_final_designs"] == 3
    assert written["lengths"] == [90, 90]
    assert written["target_hotspot_residues"] == "A12"
    assert written["number_of_final_designs"] != 100
    assert written["lengths"] != [65, 150]
    assert written["target_hotspot_residues"] != "56"
    assert written["starting_pdb"] == str(sources[1])
    assert prepared["applied"]["filters"] == "/opt/BindCraft/settings_filters/no_filters.json"
    assert prepared["applied"]["process_seed"] is None
    assert "seed" not in written
    assert prepared["executable"] is False
    assert prepared["execution_authorized"] is False
    assert prepared["scientific_execution"] == "disabled"
    assert prepared["execution_blockers"] == ["scientific_execution_disabled"]
    assert "process_seed_does_not_set_the_bindcraft_trajectory" in prepared["interpretation_limitations"]
    assert "preparation_does_not_start_bindcraft" in prepared["interpretation_limitations"]
    assert "process_seed_does_not_set_the_bindcraft_trajectory" not in prepared["execution_blockers"]
    digest = hashlib.sha256(Path(prepared["derived_settings"]).read_bytes()).hexdigest()
    assert prepared["derived_settings_sha256"] == digest
    assert prepared["preparation_id"] != revised["plan_id"]
    assert preparation_is_current(prepared, revised) is True
    settings, target, advanced, originals = sources
    assert settings.read_bytes() == originals["settings"]
    assert target.read_bytes() == originals["target"]
    assert advanced.read_bytes() == originals["advanced"]
    assert plans.published(revised["plan_id"])["digest"] == revised["digest"]


def test_missing_choices_do_not_produce_a_derived_settings_file(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    plans, drafts, plan, _configuration = _plan(tmp_path, candidates=None)
    prepared = _prepare(tmp_path, plans, drafts, plan, sources)
    assert prepared["prepared"] is False
    assert prepared["status"] == "incomplete"
    assert set(prepared["pendencies"]) >= {
        "n_candidates",
        "binder_length",
        "hotspot_assumption",
        "filter_policy",
    }
    assert not (tmp_path / "prepared").exists()
    assert sources[0].read_bytes() == sources[3]["settings"]


def test_an_explicit_empty_hotspot_is_prepared_and_the_example_residue_stays_in_the_source(
    tmp_path: Path,
) -> None:
    sources = _sources(tmp_path)
    plans, drafts, plan, configuration = _plan(tmp_path)
    partial = _revise(plans, drafts, plan, {"hotspot": {"mode": "none"}}, configuration)
    assert partial["fields"]["hotspot_strategy"] == "none"
    assert "hotspot_assumption" not in partial["fields"]
    assert all(item["field"] != "hotspot_assumption" for item in partial["questions"])
    incomplete = _prepare(tmp_path, plans, drafts, partial, sources)
    assert incomplete["prepared"] is False
    assert "hotspot_assumption" not in incomplete["pendencies"]
    assert "hotspot_unsupported" not in incomplete["blockers"]
    assert {"binder_length", "filter_policy"} <= set(incomplete["pendencies"])
    assert not (tmp_path / "prepared").exists()
    revised = _revise(
        plans,
        drafts,
        partial,
        {
            "binder_length": 90,
            "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
        },
        configuration,
    )
    plans.close()
    plans = GenerationPlanService(store=SQLiteGenerationPlanStore(tmp_path / "plans.sqlite"))
    prepared = _prepare(tmp_path, plans, drafts, revised, sources)
    assert prepared["prepared"] is True
    assert prepared["applied"]["target_hotspot_residues"] == ""
    written = json.loads(Path(prepared["derived_settings"]).read_text(encoding="utf-8"))
    assert written["target_hotspot_residues"] == ""
    assert written["number_of_final_designs"] == 3
    assert written["lengths"] == [90, 90]
    assert b'"56"' in sources[0].read_bytes()
    assert sources[0].read_bytes() == sources[3]["settings"]
    assert sources[1].read_bytes() == sources[3]["target"]
    assert sources[2].read_bytes() == sources[3]["advanced"]
    plans.approve(
        plan_id=revised["plan_id"],
        revision=revised["revision"],
        reviewer_id="professor",
        reason="record this revision only",
        idempotency_key="approval-empty-hotspot",
        expected_digest=revised["digest"],
        require_execution_readiness=False,
    )
    result, outcome = _launch(
        ScriptedDockerControl(), plans, drafts, revised, prepared, sources, _argv()
    )
    assert result.status == "failed"
    assert outcome["status"] == "delivered_not_started"
    assert outcome["started"] is False
    delivered = outcome["delivery"]["settings"]
    assert json.loads(delivered)["target_hotspot_residues"] == ""
    assert b'"56"' not in delivered
    assert sources[0].read_bytes() == sources[3]["settings"]


def test_a_divergent_revision_or_digest_writes_nothing(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    settings, target, advanced, originals = sources
    plans, drafts, plan, configuration = _plan(tmp_path)
    revised = _revise(
        plans,
        drafts,
        plan,
        {
            "binder_length": 90,
            "hotspot": {"mode": "residues", "value": "A12"},
            "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
        },
        configuration,
    )
    stale_revision = prepare_bindcraft_inputs(
        plans,
        drafts,
        request_id=revised["design_request_id"],
        request_revision=revised["design_request_revision"],
        plan_id=revised["plan_id"],
        expected_plan_revision=revised["revision"] - 1,
        expected_digest=revised["digest"],
        settings_path=settings,
        target_path=target,
        advanced_path=advanced,
        destination=tmp_path / "prepared",
    )
    stale_digest = prepare_bindcraft_inputs(
        plans,
        drafts,
        request_id=revised["design_request_id"],
        request_revision=revised["design_request_revision"],
        plan_id=revised["plan_id"],
        expected_plan_revision=revised["revision"],
        expected_digest="0" * 64,
        settings_path=settings,
        target_path=target,
        advanced_path=advanced,
        destination=tmp_path / "prepared",
    )
    assert stale_revision["status"] == "plan_revision_conflict"
    assert stale_digest["status"] == "plan_digest_conflict"
    assert stale_revision["prepared"] is False
    assert stale_digest["prepared"] is False
    assert not (tmp_path / "prepared").exists()
    assert settings.read_bytes() == originals["settings"]


def test_a_later_plan_revision_makes_the_previous_preparation_stale(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    plans, drafts, plan, configuration = _plan(tmp_path)
    revised = _revise(
        plans,
        drafts,
        plan,
        {
            "binder_length": 90,
            "hotspot": {"mode": "residues", "value": "A12"},
            "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
        },
        configuration,
    )
    prepared = _prepare(tmp_path, plans, drafts, revised, sources)
    changed = _revise(
        plans,
        drafts,
        revised,
        {
            "binder_length": 91,
            "hotspot": {"mode": "residues", "value": "A12"},
            "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
        },
        configuration,
    )
    assert preparation_is_current(prepared, changed) is False
    assert json.loads(Path(prepared["derived_settings"]).read_text(encoding="utf-8"))["lengths"] == [90, 90]
    again = _prepare(tmp_path, plans, drafts, changed, sources)
    assert again["plan_revision"] == changed["revision"]
    assert again["plan_digest"] == changed["digest"]
    assert json.loads(Path(again["derived_settings"]).read_text(encoding="utf-8"))["lengths"] == [91, 91]
    assert again["derived_settings"] != prepared["derived_settings"]
    assert sources[0].read_bytes() == sources[3]["settings"]


def _argv(count: str = "3", length: str = "90") -> tuple[str, ...]:
    return (
        "--n-designs",
        count,
        "--length-min",
        length,
        "--length-max",
        length,
        "--filters",
        "/opt/BindCraft/settings_filters/no_filters.json",
    )


def _launch(control, plans, drafts, plan, prepared, sources, argv):
    executor = Pdl1DockerExecutor.__new__(Pdl1DockerExecutor)
    executor.control = control
    executor.prepared_launch = {
        "plans": plans,
        "drafter": drafts,
        "request_id": plan["design_request_id"],
        "request_revision": plan["design_request_revision"],
        "plan_id": plan["plan_id"],
        "plan_revision": plan["revision"],
        "plan_digest": plan["digest"],
        "preparation": prepared,
        "argv": argv,
        "original_settings": sources[0],
    }
    executor.prepared_outcome = None
    result = executor.execute(None)  # type: ignore[arg-type]
    return result, executor.prepared_outcome


def test_the_launcher_delivers_the_prepared_settings_and_does_not_start(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    plans, drafts, plan, configuration = _plan(tmp_path)
    revised = _revise(
        plans,
        drafts,
        plan,
        {
            "binder_length": 90,
            "hotspot": {"mode": "residues", "value": "A12"},
            "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
        },
        configuration,
    )
    prepared = _prepare(tmp_path, plans, drafts, revised, sources)
    control = ScriptedDockerControl()
    unapproved, outcome = _launch(control, plans, drafts, revised, prepared, sources, _argv())
    assert unapproved.status == "failed"
    assert outcome["status"] == "approval_required"
    assert outcome["delivered"] is False
    assert control.calls == []
    approved = plans.approve(
        plan_id=revised["plan_id"],
        revision=revised["revision"],
        reviewer_id="professor",
        reason="record this revision only",
        idempotency_key="approval-prepared",
        expected_digest=revised["digest"],
        require_execution_readiness=False,
    )
    assert approved["approved"] is True
    assert approved["execution_authorized"] is False
    assert approved["digest"] == revised["digest"]
    control = ScriptedDockerControl()
    result, outcome = _launch(control, plans, drafts, revised, prepared, sources, _argv())
    assert result.status == "failed"
    assert outcome["status"] == "delivered_not_started"
    assert outcome["started"] is False
    assert outcome["delivery"]["gpu_device"] is None
    assert "gpu_not_selected" in outcome["remaining_blockers"]
    assert not hasattr(control, "gpu_device")
    delivered = control.prepared_delivery
    written = json.loads(delivered["settings"])
    assert written["number_of_final_designs"] == 3
    assert delivered["n_designs"] == 3
    assert delivered["argv"][delivered["argv"].index("--n-designs") + 1] == "3"
    assert written["lengths"] == [90, 90]
    assert written["target_hotspot_residues"] == "A12"
    assert delivered["filters"] == "/opt/BindCraft/settings_filters/no_filters.json"
    assert b"100" not in delivered["settings"]
    assert delivered["settings"] == Path(prepared["derived_settings"]).read_bytes()
    assert control.calls == []
    assert sources[0].read_bytes() == sources[3]["settings"]
    assert "100" in sources[0].read_text(encoding="utf-8")


def test_the_launcher_rejects_a_stale_tampered_or_original_settings_file(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    plans, drafts, plan, configuration = _plan(tmp_path)
    revised = _revise(
        plans,
        drafts,
        plan,
        {
            "binder_length": 90,
            "hotspot": {"mode": "residues", "value": "A12"},
            "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
        },
        configuration,
    )
    prepared = _prepare(tmp_path, plans, drafts, revised, sources)
    plans.approve(
        plan_id=revised["plan_id"],
        revision=revised["revision"],
        reviewer_id="professor",
        reason="record this revision only",
        idempotency_key="approval-prepared",
        expected_digest=revised["digest"],
        require_execution_readiness=False,
    )
    settings_path = Path(prepared["derived_settings"])
    original_payload = settings_path.read_bytes()
    settings_path.write_bytes(original_payload.replace(b"A12", b"A13"))
    _tampered, tampered_outcome = _launch(
        ScriptedDockerControl(), plans, drafts, revised, prepared, sources, _argv()
    )
    assert tampered_outcome["status"] == "preparation_hash_mismatch"
    assert tampered_outcome["delivered"] is False
    settings_path.write_bytes(original_payload)
    fallback = dict(prepared)
    fallback["derived_settings"] = str(sources[0])
    rejected = consume_prepared_launch(
        ScriptedDockerControl(),
        plans=plans,
        drafter=drafts,
        request_id=revised["design_request_id"],
        request_revision=revised["design_request_revision"],
        plan_id=revised["plan_id"],
        plan_revision=revised["revision"],
        plan_digest=revised["digest"],
        preparation=fallback,
        argv=_argv(),
        original_settings=sources[0],
    )
    assert rejected["delivered"] is False
    assert rejected["status"] == "preparation_hash_mismatch"
    mismatched = consume_prepared_launch(
        ScriptedDockerControl(),
        plans=plans,
        drafter=drafts,
        request_id=revised["design_request_id"],
        request_revision=revised["design_request_revision"],
        plan_id=revised["plan_id"],
        plan_revision=revised["revision"],
        plan_digest=revised["digest"],
        preparation=prepared,
        argv=_argv(count="100"),
        original_settings=sources[0],
    )
    assert mismatched["status"] == "quantity_mismatch"
    assert mismatched["delivered"] is False
    assert json.loads(settings_path.read_text(encoding="utf-8"))["number_of_final_designs"] == 3
    changed = _revise(
        plans,
        drafts,
        revised,
        {
            "binder_length": 91,
            "hotspot": {"mode": "residues", "value": "A12"},
            "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
        },
        configuration,
    )
    stale_control = ScriptedDockerControl()
    stale = consume_prepared_launch(
        stale_control,
        plans=plans,
        drafter=drafts,
        request_id=changed["design_request_id"],
        request_revision=changed["design_request_revision"],
        plan_id=changed["plan_id"],
        plan_revision=changed["revision"],
        plan_digest=changed["digest"],
        preparation=prepared,
        argv=_argv(length="91"),
        original_settings=sources[0],
    )
    assert stale["status"] == "preparation_stale"
    assert stale["delivered"] is False
    assert not hasattr(stale_control, "prepared_delivery")
    assert json.loads(settings_path.read_text(encoding="utf-8"))["number_of_final_designs"] == 3
    assert sources[0].read_bytes() == sources[3]["settings"]
    absent = consume_prepared_launch(
        ScriptedDockerControl(),
        plans=plans,
        drafter=drafts,
        request_id=revised["design_request_id"],
        request_revision=revised["design_request_revision"],
        plan_id=revised["plan_id"],
        plan_revision=revised["revision"],
        plan_digest=revised["digest"],
        preparation=None,
        argv=_argv(),
        original_settings=sources[0],
    )
    assert absent["status"] == "preparation_absent"
    assert absent["delivered"] is False


def test_explicit_plan_resources_reach_the_executor_without_a_device_default(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    plans, drafts, plan, configuration = _plan(tmp_path)
    scientific = {
        "binder_length": 90,
        "hotspot": {"mode": "residues", "value": "A12"},
        "filter_policy": {"mode": "technical_demonstration", "policy": "no_filters"},
    }
    revised = _revise(plans, drafts, plan, scientific, configuration)
    _prepare(tmp_path, plans, drafts, revised, sources)
    limited = _revise(
        plans,
        drafts,
        revised,
        scientific,
        configuration,
        technical={
            "gpu": "1",
            "cpu_count": 4,
            "memory_mib": 8192,
            "execution_deadline_seconds": 120,
        },
    )
    prepared = _prepare(tmp_path, plans, drafts, limited, sources)
    approved = plans.approve(
        plan_id=limited["plan_id"],
        revision=limited["revision"],
        reviewer_id="professor",
        reason="record this revision only",
        idempotency_key="approval-resources",
        expected_digest=limited["digest"],
        require_execution_readiness=False,
    )
    assert approved["execution_authorized"] is False
    control = ScriptedDockerControl()
    result, outcome = _launch(control, plans, drafts, limited, prepared, sources, _argv())
    assert result.status == "failed"
    assert outcome["started"] is False
    assert control.calls == []
    assert outcome["delivery"]["gpu_device"] == "1"
    assert outcome["delivery"]["runtime"] == "apptainer-group-supervisor"
    assert outcome["delivery"]["image"].endswith("bindcraft-preflight.sif")
    assert outcome["delivery"]["entrypoint"][1].endswith("bindcraft_seeded_entrypoint.py")
    assert control.gpu_device == "1"
    assert control.cpu_count == 4
    assert control.memory_mib == 8192
    enforcement = outcome["delivery"]["deadline_enforcement"]
    assert enforcement["timeout_seconds"] == 120
    assert enforcement["on_worker_loss"] == "parent_watch_kills_descendants_including_new_session_or_group"
    assert enforcement["supervisor"].endswith("GroupSupervisedRuntime")
    resources = outcome["delivery"]["resource_application"]
    assert resources["cpu_count"] == {
        "kind": "imposed_limit",
        "mechanism": "apptainer_cgroup_cpu.max",
        "value": 4,
    }
    assert resources["memory_mib"]["kind"] == "imposed_limit"
    assert resources["memory_mib"]["value"] == 8192
    assert resources["gpu"]["kind"] == "library_configuration"
    assert resources["gpu"]["value"] == "1"
    assert resources["gpu"]["device_nodes_remain_visible"] is True
    command = outcome["delivery"]["apptainer_exec"]
    assert command[command.index("--cpus") + 1] == "4"
    assert command[command.index("--memory") + 1] == "8192M"
    assert "CUDA_VISIBLE_DEVICES=1" in command
    assert outcome["delivery"]["resource_application"]["gpu"]["kind"] == "library_configuration"
    assert outcome["delivery"]["resource_application"]["gpu"]["isolation_required"] is False
    assert outcome["delivery"]["resource_application"]["gpu"]["vram_reservation_required"] is False
    assert "gpu_device_limit_not_imposed" not in outcome["remaining_blockers"]
    assert "cpu_count_not_saved" not in outcome["remaining_blockers"]
    assert "host_memory_not_saved" not in outcome["remaining_blockers"]
    assert "execution_deadline_service_unresolved" not in outcome["remaining_blockers"]
    unassigned = _revise(
        plans,
        drafts,
        limited,
        scientific,
        configuration,
        technical={"gpu": "not_assigned", "cpu_count": 4, "memory_mib": 8192, "execution_deadline_seconds": 120},
    )
    again = _prepare(tmp_path, plans, drafts, unassigned, sources)
    plans.approve(
        plan_id=unassigned["plan_id"],
        revision=unassigned["revision"],
        reviewer_id="professor",
        reason="record this revision only",
        idempotency_key="approval-unassigned",
        expected_digest=unassigned["digest"],
        require_execution_readiness=False,
    )
    plain = ScriptedDockerControl()
    _ignored, withheld = _launch(plain, plans, drafts, unassigned, again, sources, _argv())
    assert withheld["delivery"]["gpu_device"] is None
    assert withheld["delivery"]["resource_application"]["gpu"]["kind"] == "absent"
    assert withheld["delivery"]["resource_application"]["cpu_count"]["kind"] == "imposed_limit"
    assert "CUDA_VISIBLE_DEVICES" not in withheld["delivery"]["apptainer_exec"]
    assert "--cpus" in withheld["delivery"]["apptainer_exec"]
    assert "gpu_not_selected" in withheld["remaining_blockers"]
    assert "gpu_device_limit_not_imposed" not in withheld["remaining_blockers"]
    assert not hasattr(plain, "gpu_device")
    assert plain.calls == []
