import hashlib
import inspect
import os
import shutil
import sys
from pathlib import Path

import pytest

from cellertz.domains.protein_design.adapters.bindcraft import (
    STATS_FILENAME,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    CONTAINER_OUTPUT,
)
from cellertz.mcp import server as mcp_server
from cellertz.mcp import start_run as mcp_start_run
from cellertz.runtime import ExecutionRequest, ExecutionResult

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validation.bindcraft_pdl1_smoke import (
    APPTAINERENV_CUDA_VISIBLE_DEVICES,
    PINNED_ADVANCED_PROFILE_SHA256,
    PINNED_BINDCRAFT_SIF_SHA256,
    PINNED_BINDCRAFT_TOOL_VERSION,
    PINNED_SEED,
    PINNED_SETTINGS_SHA256,
    PINNED_TARGET_SHA256,
    parse_args,
    run_bindcraft_pdl1_smoke,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
GPU3_STATS = FIXTURE_ROOT / "bindcraft_gpu3" / "final_design_stats.csv"
GPU4_EVIDENCE = FIXTURE_ROOT / "ipsae_gpu4"
DESIGN = "PDL1_l65_s985772_mpnn1"
GOLDEN_IPSAE = 0.013557
OPERATOR_SOURCE = Path("tools/validation/bindcraft_pdl1_smoke.py")


class FrozenGpu4Runtime:
    name = "frozen-gpu4"
    version = "frozen_gpu4_v1"

    def __init__(self) -> None:
        self.calls = 0
        self.apptainerenv: str | None = None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls += 1
        self.apptainerenv = os.environ.get(
            APPTAINERENV_CUDA_VISIBLE_DEVICES
        )
        marker = f":{CONTAINER_OUTPUT}"
        for part in request.argv:
            if part.endswith(marker):
                _materialize_gpu4_designs(Path(part[: -len(marker)]))
                break
        return ExecutionResult(
            status="complete",
            return_code=0,
            stdout="",
            stderr="",
            runtime_name=self.name,
            runtime_version=self.version,
            started_at="test-start",
            completed_at="test-end",
        )


class ExplodingRuntime:
    name = "exploding"
    version = "exploding_v1"
    calls = 0

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls += 1
        raise AssertionError("operator inspect must not execute")


def _materialize_gpu4_designs(designs_dir: Path) -> Path:
    pdb_dir = designs_dir / "MPNN" / "Relaxed"
    pae_dir = designs_dir / "CellertzEvidence" / "PAE"
    pdb_dir.mkdir(parents=True, exist_ok=True)
    pae_dir.mkdir(parents=True, exist_ok=True)
    stats_csv = designs_dir / STATS_FILENAME
    shutil.copyfile(GPU3_STATS, stats_csv)
    for model in (1, 2):
        shutil.copyfile(
            GPU4_EVIDENCE / f"{DESIGN}_model{model}.pdb",
            pdb_dir / f"{DESIGN}_model{model}.pdb",
        )
        shutil.copyfile(
            GPU4_EVIDENCE / f"{DESIGN}_model{model}_pae.npz",
            pae_dir / f"{DESIGN}_model{model}_pae.npz",
        )
    return stats_csv


def _container(tmp_path: Path) -> Path:
    sif = tmp_path / "bindcraft.sif"
    sif.write_bytes(b"fake-sif")
    return sif


def _apptainer(tmp_path: Path) -> Path:
    binary = tmp_path / "apptainer"
    binary.write_text("fake-apptainer\n", encoding="utf-8")
    return binary


def _free_occupancy() -> dict:
    return {
        "available": True,
        "gpus": [
            {
                "index": 0,
                "uuid": "GPU-0000",
                "name": "NVIDIA-A100",
                "memory_used": "1 MiB",
                "memory_total": "81920 MiB",
                "utilization": "0 %",
            },
            {
                "index": 1,
                "uuid": "GPU-1111",
                "name": "NVIDIA-A100",
                "memory_used": "20000 MiB",
                "memory_total": "81920 MiB",
                "utilization": "80 %",
            },
        ],
        "compute_apps": [],
    }


def _occupancy_with_app(uuid: str, pid: str = "999") -> dict:
    occupancy = _free_occupancy()
    occupancy["compute_apps"] = [
        {
            "gpu_uuid": uuid,
            "pid": pid,
            "process_name": "unrelated",
        }
    ]
    return occupancy


def _operator_args(tmp_path: Path, extra: list[str]) -> list[str]:
    sif = _container(tmp_path)
    digest = hashlib.sha256(b"fake-sif").hexdigest()
    return [
        "--idempotency-key",
        "bindcraft-operator-test",
        "--container-image",
        str(sif),
        "--container-digest",
        digest,
        "--apptainer-bin",
        str(_apptainer(tmp_path)),
        "--tool-version",
        PINNED_BINDCRAFT_TOOL_VERSION,
        "--output-root",
        str(tmp_path / "scientific_execution"),
        "--artifact-root",
        str(tmp_path / "artifacts"),
        "--idempotency-root",
        str(tmp_path / "idempotency"),
        "--run-state-root",
        str(tmp_path / "runs"),
        *extra,
    ]


def _run(
    tmp_path: Path,
    extra: list[str],
    runtime,
    **kwargs,
):
    args = parse_args(_operator_args(tmp_path, extra))
    options = {
        "runtime": runtime,
        "gpu_available": True,
        "require_clean_repo": False,
        "require_expected_branch": False,
        "nvidia_smi": _free_occupancy(),
        "enforce_pinned_identity": False,
    }
    options.update(kwargs)
    return run_bindcraft_pdl1_smoke(args, **options)


def test_parse_args_requires_idempotency_key():
    with pytest.raises(SystemExit):
        parse_args([])


def test_inspect_only_does_not_execute(tmp_path):
    runtime = ExplodingRuntime()
    report = _run(tmp_path, [], runtime)
    assert report["ok"] is True
    assert report["status"] == "inspect_only"
    assert report["execution_attempted"] is False
    assert report["replay_attempted"] is False
    assert runtime.calls == 0
    inspect_report = report["inspect"]
    assert inspect_report["target_sha256"] == PINNED_TARGET_SHA256
    assert inspect_report["settings_sha256"] == PINNED_SETTINGS_SHA256
    assert inspect_report["advanced_profile_sha256"] == (
        PINNED_ADVANCED_PROFILE_SHA256
    )
    assert inspect_report["seed"] == PINNED_SEED
    assert inspect_report["expected_pinned_tool_version"] == (
        PINNED_BINDCRAFT_TOOL_VERSION
    )
    assert inspect_report["provided_tool_version"] == (
        PINNED_BINDCRAFT_TOOL_VERSION
    )
    assert inspect_report["expected_pinned_sif_digest"] == (
        PINNED_BINDCRAFT_SIF_SHA256
    )
    assert inspect_report["tool_version_matches"] is True
    assert inspect_report["container_digest_matches_pin"] is False
    assert inspect_report["actual_sif_matches_pin"] is False
    assert inspect_report["authorization"] == (
        "developer_bindcraft_authorization"
    )
    assert inspect_report["errors"] == []
    assert inspect_report["gpu"]["gpus"][0]["index"] == 0
    assert inspect_report["gpu"]["gpus"][1]["index"] == 1


def test_execute_real_requires_gpu_index(tmp_path):
    runtime = ExplodingRuntime()
    report = _run(tmp_path, ["--execute-real"], runtime)
    assert report["ok"] is False
    assert report["execution_attempted"] is False
    assert "gpu_index_required" in report["errors"]
    assert runtime.calls == 0


def test_execute_real_sets_and_restores_apptainerenv(tmp_path):
    runtime = FrozenGpu4Runtime()
    os.environ[APPTAINERENV_CUDA_VISIBLE_DEVICES] = "9"
    try:
        report = _run(
            tmp_path,
            ["--execute-real", "--gpu-index", "0"],
            runtime,
        )
        assert report["ok"] is True
        assert report["execution_attempted"] is True
        assert report["replay_attempted"] is False
        assert report["requested_host_gpu_index"] == 0
        assert report["effective_apptainerenv_cuda_visible_devices"] == "0"
        assert runtime.apptainerenv == "0"
        assert runtime.calls == 1
        assert os.environ[APPTAINERENV_CUDA_VISIBLE_DEVICES] == "9"
    finally:
        os.environ.pop(APPTAINERENV_CUDA_VISIBLE_DEVICES, None)


def test_execute_real_composes_production_starter_once(tmp_path):
    runtime = FrozenGpu4Runtime()
    report = _run(
        tmp_path,
        ["--execute-real", "--gpu-index", "0"],
        runtime,
    )
    assert report["ok"] is True
    assert report["execution_attempted"] is True
    assert report["replay_attempted"] is False
    assert runtime.calls == 1
    result = report["result"]
    assert result["status"] == "complete"
    assert result["execution_status"] == "complete"
    assert result["scientific_status"] == "complete"
    persisted = report["persisted"]
    assert persisted["record"]["status"] == "complete"
    assert persisted["stats"]["exists"] is True
    assert persisted["handoff"]["execution_id"] == "exec_pdl1_smoke"
    assert persisted["handoff"]["run_id"] == "protein_run_pdl1_smoke"
    assert persisted["snapshot"]["exists"] is True
    assert persisted["snapshot"]["snapshot_container_digest"] == (
        persisted["snapshot"]["claimed_container_digest"]
    )
    assert persisted["snapshot"]["advanced_profile_sha256"] == (
        PINNED_ADVANCED_PROFILE_SHA256
    )
    scoring = persisted["science"]["scoring"]
    assert scoring[0]["ranking_score"] == pytest.approx(GOLDEN_IPSAE, abs=5e-7)
    assert persisted["science"]["ipsae_is_probability"] is False
    assert persisted["science"]["ipsae_hard_filter"] is False
    ranking = persisted["science"]["ranking"]
    assert ranking[0]["rank"] == 1


def test_same_key_replay_does_not_require_or_mutate_gpu(tmp_path):
    runtime = FrozenGpu4Runtime()
    first = _run(
        tmp_path,
        ["--execute-real", "--gpu-index", "0"],
        runtime,
    )
    assert first["ok"] is True
    os.environ[APPTAINERENV_CUDA_VISIBLE_DEVICES] = "9"
    try:
        replay = _run(
            tmp_path,
            ["--replay"],
            runtime,
            gpu_available=False,
            nvidia_smi={"available": False, "gpus": [], "compute_apps": []},
        )
        assert runtime.calls == 1
        assert replay["ok"] is True
        assert replay["execution_attempted"] is False
        assert replay["replay_attempted"] is True
        assert replay["effective_apptainerenv_cuda_visible_devices"] is None
        assert os.environ[APPTAINERENV_CUDA_VISIBLE_DEVICES] == "9"
        assert replay["result"]["idempotent_replay"] is True
        assert replay["result"]["status"] == "complete"
        assert replay["result"]["run_id"] == first["result"]["run_id"]
        assert replay["persisted"]["handoff"]["execution_id"] == (
            first["persisted"]["handoff"]["execution_id"]
        )
    finally:
        os.environ.pop(APPTAINERENV_CUDA_VISIBLE_DEVICES, None)


def test_execute_real_collision_does_not_rerun(tmp_path):
    runtime = FrozenGpu4Runtime()
    first = _run(
        tmp_path,
        ["--execute-real", "--gpu-index", "0"],
        runtime,
    )
    second = _run(
        tmp_path,
        ["--execute-real", "--gpu-index", "0"],
        runtime,
    )
    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error"] == "idempotency_key_collision"
    assert second["execution_attempted"] is False
    assert second["replay_attempted"] is False
    assert runtime.calls == 1


def test_container_digest_mismatch_stops_before_execution(tmp_path):
    runtime = ExplodingRuntime()
    argv = _operator_args(tmp_path, ["--execute-real", "--gpu-index", "0"])
    digest_index = argv.index("--container-digest") + 1
    argv[digest_index] = "0" * 64
    report = run_bindcraft_pdl1_smoke(
        parse_args(argv),
        runtime=runtime,
        gpu_available=True,
        require_clean_repo=False,
        require_expected_branch=False,
        nvidia_smi=_free_occupancy(),
    )
    assert report["ok"] is False
    assert report["execution_attempted"] is False
    assert "pinned_container_digest_mismatch" in report["errors"]
    assert runtime.calls == 0


def test_pinned_sif_sha256_mismatch_stops_before_execution(tmp_path):
    runtime = ExplodingRuntime()
    argv = _operator_args(tmp_path, ["--execute-real", "--gpu-index", "0"])
    digest_index = argv.index("--container-digest") + 1
    argv[digest_index] = PINNED_BINDCRAFT_SIF_SHA256
    report = run_bindcraft_pdl1_smoke(
        parse_args(argv),
        runtime=runtime,
        gpu_available=True,
        require_clean_repo=False,
        require_expected_branch=False,
        nvidia_smi=_free_occupancy(),
    )
    assert report["ok"] is False
    assert report["execution_attempted"] is False
    assert "pinned_sif_sha256_mismatch" in report["errors"]
    assert runtime.calls == 0


def test_pinned_tool_version_mismatch_stops_before_execution(tmp_path):
    runtime = ExplodingRuntime()
    report = _run(
        tmp_path,
        ["--execute-real", "--gpu-index", "0", "--tool-version", "other"],
        runtime,
        enforce_pinned_identity=True,
    )
    assert report["ok"] is False
    assert report["execution_attempted"] is False
    assert "pinned_tool_version_mismatch" in report["errors"]
    assert runtime.calls == 0


def test_process_on_other_gpu_does_not_block_selected_gpu(tmp_path):
    runtime = FrozenGpu4Runtime()
    report = _run(
        tmp_path,
        ["--execute-real", "--gpu-index", "0"],
        runtime,
        nvidia_smi=_occupancy_with_app("GPU-1111"),
    )
    assert report["ok"] is True
    assert runtime.calls == 1
    selected = report["inspect"]["gpu"]["selected"]
    assert selected["index"] == 0
    assert selected["busy"] is False


def test_process_on_selected_gpu_stays_informative(tmp_path):
    runtime = FrozenGpu4Runtime()
    report = _run(
        tmp_path,
        ["--execute-real", "--gpu-index", "0"],
        runtime,
        nvidia_smi=_occupancy_with_app("GPU-0000"),
    )
    assert report["ok"] is True
    assert "selected_gpu_busy" not in report.get("errors", [])
    assert runtime.calls == 1
    selected = report["inspect"]["gpu"]["selected"]
    assert selected["busy"] is True
    assert selected["processes"][0]["pid"] == "999"


def test_selected_gpu_missing_fails_closed(tmp_path):
    runtime = ExplodingRuntime()
    occupancy = _free_occupancy()
    occupancy["gpus"] = occupancy["gpus"][1:]
    report = _run(
        tmp_path,
        ["--execute-real", "--gpu-index", "0"],
        runtime,
        nvidia_smi=occupancy,
    )
    assert report["ok"] is False
    assert report["execution_attempted"] is False
    assert "selected_gpu_missing" in report["errors"]
    assert runtime.calls == 0


def test_inspect_reports_per_gpu_occupancy(tmp_path):
    occupancy = _occupancy_with_app("GPU-1111")
    report = _run(
        tmp_path,
        ["--gpu-index", "0"],
        ExplodingRuntime(),
        nvidia_smi=occupancy,
    )
    assert report["status"] == "inspect_only"
    gpus = report["inspect"]["gpu"]["gpus"]
    assert gpus[0]["memory_used"] == "1 MiB"
    assert gpus[1]["utilization"] == "80 %"
    selected = report["inspect"]["gpu"]["selected"]
    assert selected["index"] == 0
    assert selected["memory_total"] == "81920 MiB"
    assert report["inspect"]["gpu"]["sharing_explicit"] is False


def test_forbidden_hd10tb_output_is_rejected(tmp_path):
    runtime = ExplodingRuntime()
    report = _run(
        tmp_path,
        [
            "--execute-real",
            "--gpu-index",
            "0",
            "--output-root",
            "/mnt/hd10tb/cellertz",
        ],
        runtime,
    )
    assert report["ok"] is False
    assert report["execution_attempted"] is False
    assert "forbidden_output_location" in report["errors"]
    assert runtime.calls == 0


def test_seed_mismatch_is_rejected(tmp_path):
    runtime = ExplodingRuntime()
    report = _run(
        tmp_path,
        ["--seed", "985772"],
        runtime,
    )
    assert report["ok"] is False
    assert report["execution_attempted"] is False
    assert report["replay_attempted"] is False
    assert report["error"] == "bindcraft_smoke_seed_mismatch"
    assert runtime.calls == 0


def test_execute_and_replay_together_is_rejected(tmp_path):
    report = _run(
        tmp_path,
        ["--execute-real", "--replay"],
        ExplodingRuntime(),
    )
    assert report["error"] == "operator_execute_and_replay_conflict"
    assert report["execution_attempted"] is False
    assert report["replay_attempted"] is False


def test_operator_is_not_an_mcp_surface():
    start_source = inspect.getsource(mcp_start_run)
    server_source = inspect.getsource(mcp_server)
    operator_source = OPERATOR_SOURCE.read_text(encoding="utf-8")
    assert "start_bindcraft_run" not in start_source
    assert "start_bindcraft_run" not in server_source
    assert "bindcraft_pdl1_smoke" not in start_source
    assert "bindcraft_pdl1_smoke" not in server_source
    assert "tools.validation" not in start_source
    assert "tools.validation" not in server_source
    assert "start_bindcraft_run" in operator_source
    assert "developer_bindcraft_authorization" in operator_source
    assert "ApptainerBindCraftGenerator" in operator_source
    assert "LocalProteinRunStarter" in operator_source
    assert "cellertz.mcp" not in operator_source
    assert "from cellertz.mcp" not in operator_source
    assert "APPTAINERENV_CUDA_VISIBLE_DEVICES" in operator_source
