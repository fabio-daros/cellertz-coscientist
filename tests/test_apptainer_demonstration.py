import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from cellertz.application.apptainer_demonstration import (
    DEMONSTRATION_DEADLINE_SECONDS,
    FILTER_POLICY,
    PROFILE_ID,
    SIF_SHA256,
    SMOKE_TOKEN,
    ApptainerDemonstrationLauncher,
    DemonstrationLaunch,
    GroupSupervisedRuntime,
    _executor_diagnosis,
    _safe_attempt_directory,
    demonstration_contract,
    gpu_launch_blocker,
    gpu_launch_readiness,
    vram_failure_report,
    host_commands,
    reap_abandoned,
)
from cellertz.application.attempt_coordinator import AttemptCoordinator
from cellertz.application.attempt_evidence import evidence_payload, geometric_contacts, load_catalog
from cellertz.application.generation_plan import GenerationPlanService, RegisteredInputCatalog
from cellertz.application.scientific_run import _PROFILES
from cellertz.application.scientific_run_config import resolve_scientific_run_execution
from cellertz.application.scientific_runner_readiness import _EXECUTABLE_PROFILES, DOCKER_PROFILE_ID
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    DEFAULT_SMOKE_ADVANCED_PROFILE,
)
from cellertz.runtime.base import ExecutionRequest


def test_approved_seed_and_budget_reach_the_runtime_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    import cellertz.application.apptainer_demonstration as demonstration

    profile = json.loads(DEFAULT_SMOKE_ADVANCED_PROFILE.read_text(encoding="utf-8"))
    profile["max_trajectories"] = 4
    advanced = tmp_path / "advanced.json"
    advanced.write_text(json.dumps(profile), encoding="utf-8")
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
    target.write_bytes(b"ATOM\n")
    settings.write_bytes(b'{"chain": "A"}\n')
    sif = tmp_path / "bindcraft.sif"
    sif.write_bytes(b"sif")
    apptainer = tmp_path / "apptainer"
    apptainer.write_bytes(b"")
    digest = demonstration._sha256(sif)
    seen: list[tuple[object, tuple[str, ...]]] = []
    real = demonstration.build_apptainer_plan

    def capture(authorized, **kwargs):
        plan = real(authorized, **kwargs)
        seen.append((authorized.execution_request.seed, plan.argv))
        return plan

    monkeypatch.setattr(demonstration, "build_apptainer_plan", capture)
    fields = {
        "filter_policy": FILTER_POLICY,
        "filter_policy_explicit": True,
        "seed": 2,
        "binder_length": 65,
        "cpu_count": 4,
        "memory_mib": 32768,
        "execution_deadline_seconds": 7200,
        "gpu": "0",
        "sequence_limits": {"max_trajectories": 4, "num_seqs": 1, "max_mpnn_sequences": 1},
    }
    attempt = tmp_path / "attempt.abcd1234"
    request = DemonstrationLaunch(
        fields=fields,
        request_text=f"One {SMOKE_TOKEN}.",
        approval_source="human_review",
        approved=True,
        input_selection={"same": True},
        live_input_selection={"same": True},
        target_path=target,
        settings_path=settings,
        advanced_profile_path=advanced,
        attempt_id="attempt.abcd1234",
        attempt_directory=attempt,
        gpu_index=0,
        apptainer_bin=apptainer,
        sif_path=sif,
        expected_sif_sha256=digest,
    )
    result = ApptainerDemonstrationLauncher(inert_outputs=True).launch(request)
    assert result["status"] == "verified"
    assert result["launched"] is True
    assert len(seen) == 1
    seed, argv = seen[0]
    assert seed == 2
    assert argv[argv.index("--cellertz-seed") + 1] == "2"
    runtime = attempt / "attempt.abcd1234" / ".cellertz_runtime" / "advanced.json"
    materialized = json.loads(runtime.read_text(encoding="utf-8"))
    assert materialized["max_trajectories"] == 4
    assert materialized["num_seqs"] == 1
    assert materialized["max_mpnn_sequences"] == 1
    assert materialized["weights_plddt"] == profile["weights_plddt"]
    bootstrap = (runtime.parent / "bindcraft_seeded_entrypoint.py").read_text(encoding="utf-8")
    assert "np.random.seed(args.cellertz_seed)" in bootstrap
    assert "np.random.randint" not in bootstrap
    pinned = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "bindcraft"
        / "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d"
        / "bindcraft.py"
    ).read_text(encoding="utf-8")
    assert "np.random.randint(0, high=999999" in pinned
    mismatched = replace(
        request,
        attempt_directory=tmp_path / "attempt.abcd5678",
        attempt_id="attempt.abcd5678",
        fields={
            **fields,
            "sequence_limits": {"max_trajectories": 1, "num_seqs": 1, "max_mpnn_sequences": 1},
        },
    )
    refused = ApptainerDemonstrationLauncher(inert_outputs=True).launch(mismatched)
    assert refused["status"] == "launch_rejected"
    assert refused["launched"] is False
    assert len(seen) == 1
    assert not mismatched.attempt_directory.exists()


def test_profile_does_not_impersonate_docker() -> None:
    contract = demonstration_contract()
    assert contract["profile_id"] == PROFILE_ID
    assert contract["profile_id"] != DOCKER_PROFILE_ID
    assert PROFILE_ID not in _EXECUTABLE_PROFILES
    assert DOCKER_PROFILE_ID in _EXECUTABLE_PROFILES
    assert PROFILE_ID not in _PROFILES
    assert contract["filter_policy"] == FILTER_POLICY
    assert contract["filter_is_scientific_validation"] is False
    assert contract["timeout_seconds"] == DEMONSTRATION_DEADLINE_SECONDS
    assert contract["sif_sha256"] == SIF_SHA256
    assert any(role["name"] == "bindcraft_gpu" for role in contract["roles"])
    assert any(role["name"] == "ipsae_ranking_score" for role in contract["roles"])
    assert resolve_scientific_run_execution({}) == "disabled"
    mode = {"CELLERTZ_SCIENTIFIC_RUN_EXECUTION": "apptainer-demonstration"}
    assert resolve_scientific_run_execution(mode) == "apptainer-demonstration"


def test_explicit_run_delivers_the_displayed_settings_and_does_not_start(tmp_path: Path) -> None:
    original = {
        "design_path": "/old",
        "starting_pdb": "/old.pdb",
        "lengths": [65, 150],
        "number_of_final_designs": 100,
        "target_hotspot_residues": "56",
    }
    launch = replace(
        _launch(tmp_path, approval_source="explicit_run"),
        n_designs=1,
        fields={
            "filter_policy": FILTER_POLICY,
            "filter_policy_explicit": True,
            "hotspot_strategy": "none",
            "seed": 65,
            "binder_length": 65,
            "cpu_count": 4,
            "memory_mib": 32768,
            "execution_deadline_seconds": 7200,
            "gpu": "0",
            "request_text": "#5 design an new binder to PDL1",
        },
    )
    launch.settings_path.write_text(json.dumps(original), encoding="utf-8")
    advanced = json.loads(DEFAULT_SMOKE_ADVANCED_PROFILE.read_text(encoding="utf-8"))
    advanced["max_trajectories"] = 4
    launch.advanced_profile_path.write_text(json.dumps(advanced), encoding="utf-8")

    class Stopped:
        def __init__(self) -> None:
            self.request: ExecutionRequest | None = None

        def execute(self, request: ExecutionRequest, on_start: object = None, **_extra: object) -> ExecutionRequest:
            self.request = request
            raise RuntimeError("stopped_before_process")

    runtime = Stopped()
    try:
        ApptainerDemonstrationLauncher(
            gpu_available=True,
            gpu_readiness=lambda index: {"status": "launch_ready", "launch_ready": True, "gpu_index": index},
            runtime=runtime,
        ).launch(launch)
    except RuntimeError as exc:
        assert str(exc) == "stopped_before_process"
    else:
        raise AssertionError("the runtime was not reached")
    assert json.loads(launch.settings_path.read_text(encoding="utf-8"))["target_hotspot_residues"] == "56"
    delivered = json.loads(next(launch.attempt_directory.rglob("settings.json")).read_text(encoding="utf-8"))
    assert delivered["target_hotspot_residues"] == ""
    assert delivered["lengths"] == [65, 65]
    assert delivered["number_of_final_designs"] == 1
    assert runtime.request is not None
    argv = runtime.request.argv
    assert "--cpus" in argv and argv[argv.index("--cpus") + 1] == "4"
    assert "--memory" in argv and argv[argv.index("--memory") + 1] == "32768M"
    assert "--cellertz-seed" in argv and argv[argv.index("--cellertz-seed") + 1] == "65"
    assert "CUDA_VISIBLE_DEVICES=0" in argv
    assert runtime.request.timeout_seconds == 7200


def test_an_explicit_run_with_a_different_input_identity_does_not_start(tmp_path: Path) -> None:
    from dataclasses import replace

    launch = replace(
        _launch(tmp_path, approval_source="explicit_run"),
        live_input_selection={"other": True},
    )
    result = ApptainerDemonstrationLauncher().launch(launch)
    assert result["launched"] is False
    assert result["status"] == "launch_rejected"
    assert not (tmp_path / "attempt.abcd1234").exists()


def test_language_model_cannot_launch(tmp_path: Path) -> None:
    launch = _launch(tmp_path, approval_source="language_model", approved=False)
    result = ApptainerDemonstrationLauncher(inert_outputs=True).launch(launch)
    assert result["launched"] is False
    assert result["status"] == "approval_required"
    assert not (tmp_path / "attempt.abcd1234").exists()


def test_inert_runner_verifies_one_candidate_without_invented_scores(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt.abcd1234"
    result = ApptainerDemonstrationLauncher(inert_outputs=True).launch(_launch(tmp_path))
    assert result["status"] == "verified"
    assert result["scientific_validation"] is False
    review = json.loads((attempt / "review.json").read_text(encoding="utf-8"))
    assert review["platform_ipsae_ranking"] is False
    assert review["ranking_score_computed"] is False
    assert review["provenance"]["omitted"] == ["ipSAE", "ranking_score"]
    assert review["models"]["model1"]["sequence"] == "AAAAAA"
    catalog = load_catalog()
    catalog.update(load_catalog_from(attempt.parent))
    item = catalog["attempt.abcd1234"]
    assert item["pdb_verified"] is True
    payload = evidence_payload("attempt.abcd1234", item)
    assert payload["ranking_score"] == "not_recorded"
    contacts = geometric_contacts(item)
    assert contacts["models"]["model1"]["calculated"]["residue_pairs"] == 1


def test_a_controlled_failure_keeps_stdout_and_stderr(tmp_path: Path) -> None:
    stdout_path = tmp_path / "executor.stdout"
    stderr_path = tmp_path / "executor.stderr"
    result = GroupSupervisedRuntime().execute(
        ExecutionRequest(
            argv=("/bin/sh", "-c", "echo ready; echo 'FATAL: controlled failure' >&2; exit 255"),
            timeout_seconds=5,
        ),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    assert result.status == "failed"
    assert result.return_code == 255
    assert stdout_path.read_text(encoding="utf-8") == "ready\n"
    assert stderr_path.read_text(encoding="utf-8") == "FATAL: controlled failure\n"
    assert result.stdout == "ready\n"
    assert result.stderr == "FATAL: controlled failure\n"
    assert _executor_diagnosis(result) == "FATAL: controlled failure"


def test_wall_clock_kills_the_process_group() -> None:
    seen: list[int] = []
    result = GroupSupervisedRuntime().execute(
        ExecutionRequest(argv=("/bin/sleep", "30"), timeout_seconds=0.3),
        on_start=seen.append,
    )
    assert result.status == "failed"
    assert result.error == "Process timed out."
    assert seen and not _alive(seen[0])


def test_caller_loss_kills_the_process_group() -> None:
    seen: list[int] = []
    result = GroupSupervisedRuntime().execute(
        ExecutionRequest(argv=("/bin/sleep", "30"), timeout_seconds=10),
        alive=lambda: False,
        on_start=seen.append,
    )
    assert result.status == "failed"
    assert not seen or not _alive(seen[0])


def _escape_script() -> str:
    return (
        "import os, sys, time\n"
        "session, group = sys.argv[1], sys.argv[2]\n"
        "first = os.fork()\n"
        "if first == 0:\n"
        "    os.setsid()\n"
        "    open(session, 'w').write(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "second = os.fork()\n"
        "if second == 0:\n"
        "    os.setpgid(0, 0)\n"
        "    open(group, 'w').write(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "time.sleep(60)\n"
    )


def test_timeout_kills_a_new_session_and_a_new_group(tmp_path: Path) -> None:
    session = tmp_path / "session.pid"
    group = tmp_path / "group.pid"
    script = tmp_path / "escape.py"
    script.write_text(_escape_script(), encoding="utf-8")
    result = GroupSupervisedRuntime().execute(
        ExecutionRequest(
            argv=(sys.executable, str(script), str(session), str(group)),
            timeout_seconds=1,
        )
    )
    assert result.status == "failed"
    assert result.error == "Process timed out."
    assert session.exists() and group.exists()
    assert not _alive(int(session.read_text()))
    assert not _alive(int(group.read_text()))


def test_worker_loss_kills_a_new_session_and_a_new_group(tmp_path: Path) -> None:
    session = tmp_path / "session.pid"
    group = tmp_path / "group.pid"
    script = tmp_path / "escape.py"
    script.write_text(_escape_script(), encoding="utf-8")
    result = GroupSupervisedRuntime().execute(
        ExecutionRequest(
            argv=(sys.executable, str(script), str(session), str(group)),
            timeout_seconds=20,
        ),
        alive=lambda: not (session.exists() and group.exists()),
    )
    assert result.status == "failed"
    assert session.exists() and group.exists()
    assert not _alive(int(session.read_text()))
    assert not _alive(int(group.read_text()))


def test_parent_process_loss_kills_the_child(tmp_path: Path) -> None:
    script = tmp_path / "worker.py"
    marker = tmp_path / "supervisor.pid"
    script.write_text(
        "from cellertz.application.apptainer_demonstration import GroupSupervisedRuntime\n"
        "from cellertz.runtime.base import ExecutionRequest\n"
        f"GroupSupervisedRuntime().execute(\n"
        f"    ExecutionRequest(argv=('/bin/sleep', '30'), timeout_seconds=20),\n"
        f"    on_start=lambda pid: open({str(marker)!r}, 'w').write(str(pid)),\n"
        ")\n",
        encoding="utf-8",
    )
    worker = subprocess.Popen([sys.executable, str(script)], start_new_session=True)
    deadline = time.time() + 5
    while time.time() < deadline and not marker.exists():
        time.sleep(0.05)
    assert marker.exists()
    supervisor = int(marker.read_text())
    os.kill(worker.pid, signal.SIGKILL)
    worker.wait(timeout=5)
    deadline = time.time() + 5
    while time.time() < deadline and _alive(supervisor):
        time.sleep(0.05)
    assert not _alive(supervisor)


def test_reap_kills_only_an_abandoned_group(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    attempt = root / "attempt.abcd1234"
    attempt.mkdir(parents=True)
    sleep = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    (attempt / "supervisor.pid").write_text(f"{sleep.pid}\n", encoding="utf-8")
    (attempt / "progress.json").write_text(
        json.dumps({"status": "started", "worker_pid": 2**22}),
        encoding="utf-8",
    )
    assert reap_abandoned(root) == ["attempt.abcd1234"]
    sleep.wait(timeout=5)
    assert sleep.returncode is not None


def test_human_approval_does_not_launch(tmp_path: Path) -> None:
    catalog = RegisteredInputCatalog(
        {
            "target_structure": tmp_path / "target.pdb",
            "settings": tmp_path / "settings.json",
            "advanced_profile": tmp_path / "advanced.json",
        }
    )
    (tmp_path / "target.pdb").write_bytes(b"ATOM\n")
    (tmp_path / "settings.json").write_bytes(b'{"chain": "A"}\n')
    (tmp_path / "advanced.json").write_bytes(DEFAULT_SMOKE_ADVANCED_PROFILE.read_bytes())
    identities = catalog.identities()
    assert identities is not None
    calls: list[object] = []

    class Recorder:
        def launch(self, request: object) -> dict[str, object]:
            calls.append(request)
            return {"status": "verified", "launched": True, "scientific_validation": False}

    service = GenerationPlanService(None, inputs=catalog, demonstration=Recorder())
    proposal = {
        "request_text": f"One {SMOKE_TOKEN}. Filter policy {FILTER_POLICY}.",
        "target_identity": "PDL1",
        "hotspot_assumption": "Recorded for this demonstration and not newly validated.",
        "filter_policy": FILTER_POLICY,
        "seed": 1,
        "binder_length": 65,
        "cpu_count": 4,
        "memory_mib": 32768,
        "gpu": "0",
        "execution_deadline_seconds": 7200,
        "input_artifacts": [
            {"role": item["role"], "sha256": item["sha256"]} for item in identities["artifacts"]
        ],
    }
    drafted = service.draft("conversation", proposal)
    assert drafted["questions"] == []
    refused = service.launch_apptainer_demonstration(
        plan_id=drafted["plan_id"],
        revision=drafted["revision"],
        reviewer_id="professor",
        reason="Launch the smoke test.",
        idempotency_key="launch-1",
        gpu_index=0,
        attempt_id="attempt.abcd1234",
        attempt_directory=str(tmp_path / "runs" / "attempt.abcd1234"),
    )
    assert refused["status"] == "approval_required"
    assert calls == []
    approved = service.approve(
        plan_id=drafted["plan_id"],
        revision=drafted["revision"],
        reviewer_id="professor",
        reason="The limits are the demonstration limits.",
        idempotency_key="approve-1",
    )
    assert approved["approved"] is True
    service.dispatch(
        plan_id=drafted["plan_id"],
        revision=drafted["revision"],
        idempotency_key="dispatch-1",
    )
    assert calls == []
    launched = service.launch_apptainer_demonstration(
        plan_id=drafted["plan_id"],
        revision=drafted["revision"],
        reviewer_id="professor",
        reason="Launch the smoke test.",
        idempotency_key="launch-1",
        gpu_index=0,
        attempt_id="attempt.abcd1234",
        attempt_directory=str(tmp_path / "runs" / "attempt.abcd1234"),
    )
    assert launched["status"] == "verified"
    assert len(calls) == 1
    assert calls[0].gpu_index == 0
    mismatched = service.launch_apptainer_demonstration(
        plan_id=drafted["plan_id"],
        revision=drafted["revision"],
        reviewer_id="professor",
        reason="Launch the smoke test.",
        idempotency_key="launch-mismatch",
        gpu_index=1,
        attempt_id="attempt.abcd5678",
        attempt_directory=str(tmp_path / "runs" / "attempt.abcd5678"),
    )
    assert mismatched["status"] == "gpu_index_mismatch"
    assert len(calls) == 1
    assert not (tmp_path / "runs" / "attempt.abcd5678").exists()
    absent = GenerationPlanService(None, inputs=catalog)
    configured = absent.launch_apptainer_demonstration(
        plan_id=drafted["plan_id"],
        revision=drafted["revision"],
        reviewer_id="professor",
        reason="Launch the smoke test.",
        idempotency_key="launch-2",
        gpu_index=0,
        attempt_id="attempt.abcd1234",
        attempt_directory=str(tmp_path / "runs" / "attempt.abcd1234"),
    )
    assert configured["status"] == "demonstration_not_configured"


def test_new_attempt_is_visible_to_the_coordinator(tmp_path: Path, monkeypatch) -> None:
    ApptainerDemonstrationLauncher(inert_outputs=True).launch(_launch(tmp_path))
    monkeypatch.setenv("CELLERTZ_ATTEMPT_CATALOG", str(tmp_path))
    seen: dict[str, str] = {}

    def complete(messages, _tools, _choice):
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        if not tool_messages:
            return {
                "tool_calls": [
                    {
                        "id": "evidence",
                        "function": {
                            "name": "get_attempt_evidence",
                            "arguments": json.dumps({"attempt_id": "attempt.abcd1234"}),
                        },
                    }
                ]
            }
        seen["tool"] = tool_messages[-1]["content"]
        return {"content": "The recorded cells were read. ipSAE and ranking_score were not computed."}

    reply = AttemptCoordinator({}, complete).ask("Review the new demonstration attempt.")
    assert reply["status"] == "answered"
    payload = json.loads(seen["tool"])
    assert payload["attempt_id"] == "attempt.abcd1234"
    assert payload["ranking_score"] == "not_recorded"


def test_launcher_accepts_one_attempt_id_and_refuses_the_twelve_character_example(tmp_path: Path) -> None:
    accepted = _launch(tmp_path)
    assert _safe_attempt_directory(accepted.attempt_id, accepted.attempt_directory) is None
    refused = replace(
        accepted,
        attempt_id="attempt.7d818cba2a1f",
        attempt_directory=tmp_path / "attempt.7d818cba2a1f",
    )

    class Runtime:
        def execute(self, request: object, on_start: object = None) -> object:
            raise AssertionError(request)

    result = ApptainerDemonstrationLauncher(
        inert_outputs=False,
        gpu_available=True,
        runtime=Runtime(),
    ).launch(refused)
    assert result["status"] == "launch_rejected"
    assert result["launched"] is False
    assert not refused.attempt_directory.exists()


def test_a_missing_or_different_gpu_index_is_refused_before_a_directory_is_created(tmp_path: Path) -> None:
    missing = replace(_launch(tmp_path), fields={**_launch(tmp_path).fields, "gpu": None})
    absent = ApptainerDemonstrationLauncher(inert_outputs=True).launch(missing)
    assert absent["status"] == "gpu_index_mismatch"
    assert absent["launched"] is False
    assert not missing.attempt_directory.exists()

    mismatched = replace(_launch(tmp_path), gpu_index=1)
    refused = ApptainerDemonstrationLauncher(
        inert_outputs=False,
        gpu_readiness=lambda index: {"status": "launch_ready", "launch_ready": True, "gpu_index": index},
    ).launch(mismatched)
    assert refused["status"] == "gpu_index_mismatch"
    assert refused["launched"] is False
    assert not mismatched.attempt_directory.exists()

    unavailable = ApptainerDemonstrationLauncher(
        inert_outputs=False,
        gpu_readiness=lambda index: {
            "status": "selected_gpu_missing",
            "launch_ready": False,
            "gpu_index": index,
        },
    ).launch(_launch(tmp_path))
    assert unavailable["status"] == "selected_gpu_missing"
    assert unavailable["launched"] is False
    assert unavailable["gpu_index"] == 0
    assert not (tmp_path / "attempt.abcd1234").exists()
    shared = {
        "status": "selected_gpu_busy",
        "launch_ready": False,
        "gpu_index": 0,
        "occupancy": "shared",
        "compute_apps": [{"pid": "42", "process_name": "other"}],
    }
    assert gpu_launch_blocker(shared) is None


def test_gpu_readiness_uses_the_supplied_occupancy_snapshot() -> None:
    busy = gpu_launch_readiness(
        0,
        query=lambda: {
            "available": True,
            "gpus": [{"index": 0, "uuid": "GPU-0", "memory_used": "20000 MiB", "memory_free": "12760 MiB"}],
            "compute_apps": [{"gpu_uuid": "GPU-0", "pid": "42", "process_name": "other"}],
        },
    )
    free = gpu_launch_readiness(
        0,
        query=lambda: {
            "available": True,
            "gpus": [{"index": 0, "uuid": "GPU-0", "memory_free": "30000 MiB"}],
            "compute_apps": [],
        },
    )
    other_gpu = gpu_launch_readiness(
        0,
        query=lambda: {
            "available": True,
            "gpus": [{"index": 0, "uuid": "GPU-0"}, {"index": 1, "uuid": "GPU-1"}],
            "compute_apps": [{"gpu_uuid": "GPU-1", "pid": "7", "process_name": "other"}],
        },
    )
    missing = gpu_launch_readiness(
        0,
        query=lambda: {
            "available": True,
            "gpus": [{"index": 1, "uuid": "GPU-1"}],
            "compute_apps": [{"gpu_uuid": "GPU-1", "pid": "7", "process_name": "other"}],
        },
    )
    unread = gpu_launch_readiness(0, query=lambda: {"available": False, "gpus": [], "compute_apps": []})
    assert busy["status"] == "launch_ready"
    assert busy["launch_ready"] is True
    assert busy["occupancy"] == "shared"
    assert busy["memory"] == {"used": "20000 MiB", "free": "12760 MiB"}
    assert gpu_launch_blocker(busy) is None
    assert other_gpu["status"] == "launch_ready"
    assert other_gpu["occupancy"] == "clear"
    assert free["status"] == "launch_ready"
    assert free["launch_ready"] is True
    assert missing["status"] == "selected_gpu_missing"
    assert gpu_launch_blocker(missing) == "selected_gpu_missing"
    assert unread["status"] == "nvidia_smi_unavailable"
    exhausted = vram_failure_report(
        "",
        "jax.errors.XlaRuntimeError: RESOURCE_EXHAUSTED: CUDA out of memory",
        memory_query=lambda: {"available": True, "gpus": [{"index": "0", "free": "100 MiB"}]},
    )
    assert exhausted is not None
    assert exhausted["status"] == "vram_insufficient"
    assert exhausted["automatic_retry"] is False
    assert exhausted["other_services_signaled"] is False
    assert exhausted["diagnostics"]["gpu_memory"]["gpus"][0]["free"] == "100 MiB"
    assert vram_failure_report("ordinary failure", "") is None


def test_host_commands_keep_docker_paused() -> None:
    text = host_commands()
    assert PROFILE_ID in text
    assert SIF_SHA256 in text
    assert SMOKE_TOKEN in text
    assert "fallocate" not in text
    assert "docker build" not in text


def _alive(pid: int) -> bool:
    completed = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    state = completed.stdout.strip()
    return bool(state) and not state.startswith(("Z", "T"))


def load_catalog_from(root: Path):
    from cellertz.application.attempt_evidence import AttemptFiles

    files = tuple(
        AttemptFiles(review_json=path, pdb_dir=path.parent) for path in root.glob("*/review.json")
    )
    return load_catalog(files)


def _launch(tmp_path: Path, *, approval_source: str = "human_review", approved: bool = True):
    sif = tmp_path / "bindcraft.sif"
    sif.write_bytes(b"sif-bytes")
    apptainer = tmp_path / "apptainer"
    apptainer.write_bytes(b"#!/bin/sh\n")
    target = tmp_path / "target.pdb"
    target.write_bytes(b"ATOM\n")
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"design_path": "/old", "starting_pdb": "/old.pdb", "lengths": [65, 65]}),
        encoding="utf-8",
    )
    advanced = tmp_path / "advanced.json"
    advanced.write_bytes(DEFAULT_SMOKE_ADVANCED_PROFILE.read_bytes())
    return DemonstrationLaunch(
        fields={
            "filter_policy": FILTER_POLICY,
            "filter_policy_explicit": True,
            "seed": 1,
            "binder_length": 65,
            "cpu_count": 4,
            "memory_mib": 32768,
            "execution_deadline_seconds": 7200,
            "gpu": "0",
            "request_text": f"Run one {SMOKE_TOKEN} with {FILTER_POLICY}.",
        },
        request_text=f"Run one {SMOKE_TOKEN} with {FILTER_POLICY}.",
        approval_source=approval_source,
        approved=approved,
        input_selection={"same": True},
        live_input_selection={"same": True},
        target_path=target,
        settings_path=settings,
        advanced_profile_path=advanced,
        attempt_id="attempt.abcd1234",
        attempt_directory=tmp_path / "attempt.abcd1234",
        gpu_index=0,
        apptainer_bin=apptainer,
        sif_path=sif,
        expected_sif_sha256=__import__("hashlib").sha256(b"sif-bytes").hexdigest(),
    )
