"""Developer-only PDL1 BindCraft smoke operator.

Composes existing production objects. Does not duplicate starter,
scoring, or execution logic. Not an MCP tool. Default is inspect-only.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cellertz.domains.protein_design.adapters.bindcraft import (
    ADAPTER_VERSION,
    STATS_FILENAME,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    DEFAULT_SMOKE_ADVANCED_PROFILE,
    ApptainerBindCraftGenerator,
    advanced_profile_identity_sha256,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    detect_gpu,
    developer_bindcraft_authorization,
    file_sha256,
)
from cellertz.domains.protein_design.execution.bindcraft_scientific import (
    HANDOFF_FILENAME,
)
from cellertz.runs.idempotency import (
    FileIdempotencyStore,
    default_idempotency_root,
)
from cellertz.runs.starter import (
    LocalProteinRunStarter,
    default_artifact_root,
)
from cellertz.runtime import ExecutionRuntime, LocalExecutionRuntime
from cellertz.state.run_state import (
    FileProteinRunStateStore,
    default_run_state_root,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = REPO_ROOT / "data" / "bindcraft" / "smoke_inputs" / "PDL1.pdb"
DEFAULT_SETTINGS = (
    REPO_ROOT / "data" / "bindcraft" / "smoke_inputs" / "PDL1.json"
)
FORBIDDEN_OUTPUT_PREFIXES = ("/mnt/hd10tb",)
APPTAINERENV_CUDA_VISIBLE_DEVICES = "APPTAINERENV_CUDA_VISIBLE_DEVICES"
PINNED_BRANCH = "phase-4b4-apptainer"

PINNED_TARGET_ID = "PDL1"
PINNED_OBJECTIVE = "binder_design"
PINNED_GENERATOR = "bindcraft"
PINNED_N_CANDIDATES = 1
PINNED_LENGTH_MIN = 65
PINNED_LENGTH_MAX = 65
PINNED_SEED = 7
PINNED_TARGET_SHA256 = (
    "d3c95434dcadf26d005340b15bd92be61e101ed921478c26f2a5550f198e61f6"
)
PINNED_SETTINGS_SHA256 = (
    "71815fb750eacb81e7e59d0591bb37404e6d98239a9977ca73bcc4c2f88e2cfd"
)
PINNED_ADVANCED_PROFILE_SHA256 = (
    "93dfe147057978bea1fed6c21e9c4251d73acf73ab8e9b37f7be664bb6ef6daa"
)
PINNED_BINDCRAFT_TOOL_VERSION = (
    "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d"
)
PINNED_BINDCRAFT_SIF_SHA256 = (
    "bec155fd18c4a0f6150b44328673fa18e10431b4567598eebf8e4ccb9930f0b6"
)

DEFAULT_RUN_ID = "protein_run_pdl1_smoke"
DEFAULT_EXECUTION_ID = "exec_pdl1_smoke"
DEFAULT_TIMEOUT_SECONDS = 14400
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "outputs" / "scientific_execution"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Developer-only PDL1 BindCraft smoke through "
            "LocalProteinRunStarter. Default is inspect-only."
        )
    )
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument(
        "--execute-real",
        action="store_true",
        help="Actually invoke start_bindcraft_run for one GPU smoke.",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay an existing complete key. Does not launch BindCraft.",
    )
    parser.add_argument("--target-pdb", type=Path, default=DEFAULT_TARGET)
    parser.add_argument(
        "--settings-json",
        type=Path,
        default=DEFAULT_SETTINGS,
    )
    parser.add_argument(
        "--advanced-profile",
        type=Path,
        default=DEFAULT_SMOKE_ADVANCED_PROFILE,
    )
    parser.add_argument("--container-image", type=Path, default=None)
    parser.add_argument("--container-digest", default=None)
    parser.add_argument("--apptainer-bin", type=Path, default=None)
    parser.add_argument("--tool-version", default=None)
    parser.add_argument("--seed", type=int, default=PINNED_SEED)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--execution-id", default=DEFAULT_EXECUTION_ID)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--idempotency-root",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--run-state-root",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--gpu-index", type=int, default=None)
    parser.add_argument(
        "--allow-shared-gpu",
        action="store_true",
        help="Permit selected-GPU compute apps. Default is fail-closed.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write the machine-readable report.",
    )
    return parser.parse_args(argv)


def run_bindcraft_pdl1_smoke(
    args: argparse.Namespace,
    *,
    runtime: ExecutionRuntime | None = None,
    gpu_available: bool | None = None,
    supervisor_factory: Any = None,
    require_clean_repo: bool = True,
    require_expected_branch: bool = True,
    nvidia_smi: dict[str, Any] | None = None,
    enforce_pinned_identity: bool = True,
) -> dict[str, Any]:
    if args.execute_real and args.replay:
        return _failed(
            "operator_execute_and_replay_conflict",
        )
    if args.seed != PINNED_SEED:
        return _failed(
            "bindcraft_smoke_seed_mismatch",
            expected=PINNED_SEED,
            actual=args.seed,
        )

    inspect = inspect_smoke_configuration(
        args,
        gpu_available=gpu_available,
        require_clean_repo=require_clean_repo,
        require_expected_branch=require_expected_branch,
        nvidia_smi=nvidia_smi,
        enforce_pinned_identity=enforce_pinned_identity,
    )
    errors = list(inspect["errors"])
    report: dict[str, Any] = {
        "ok": False,
        "execution_attempted": False,
        "replay_attempted": False,
        "inspect": inspect,
        "result": None,
        "persisted": None,
        "replay": None,
        "requested_host_gpu_index": args.gpu_index,
        "effective_apptainerenv_cuda_visible_devices": None,
    }
    if errors:
        report["error"] = errors[0]
        report["errors"] = errors
        return report

    if not args.execute_real and not args.replay:
        report["ok"] = True
        report["status"] = "inspect_only"
        return report

    store = FileIdempotencyStore(
        args.idempotency_root or default_idempotency_root()
    )
    existing = store.get(args.idempotency_key)
    if args.execute_real and existing is not None:
        report["error"] = "idempotency_key_collision"
        report["errors"] = ["idempotency_key_collision"]
        report["existing_status"] = existing.status
        return report
    if args.replay and existing is None:
        report["error"] = "idempotency_key_missing"
        report["errors"] = ["idempotency_key_missing"]
        return report

    request = _execution_request(args)
    generator = ApptainerBindCraftGenerator(
        runtime=runtime or LocalExecutionRuntime(),
        timeout_seconds=args.timeout_seconds,
        advanced_profile_path=args.advanced_profile,
    )
    starter = LocalProteinRunStarter(
        idempotency_store=store,
        run_state_store=FileProteinRunStateStore(
            args.run_state_root or default_run_state_root()
        ),
        artifact_root=args.artifact_root or default_artifact_root(),
        supervisor_factory=supervisor_factory,
    )

    def _start() -> Any:
        return starter.start_bindcraft_run(
            request,
            args.idempotency_key,
            generator=generator,
            authorization=developer_bindcraft_authorization(),
            gpu_available=gpu_available,
        )

    if args.execute_real:
        report["execution_attempted"] = True
        with _apptainer_cuda_devices(args.gpu_index) as assigned:
            report["effective_apptainerenv_cuda_visible_devices"] = assigned
            result = _start()
    else:
        report["replay_attempted"] = True
        result = _start()

    report["result"] = result.to_dict()
    report["persisted"] = collect_persisted_report(
        store,
        args.idempotency_key,
        artifact_root=starter.artifact_root,
    )
    report["ok"] = result.status == "complete"
    if args.replay:
        report["replay"] = {
            "idempotent_replay": result.idempotent_replay,
            "status": result.status,
            "run_id": result.run_id,
            "execution_status": result.execution_status,
            "scientific_status": result.scientific_status,
        }
    if result.error is not None:
        report["error"] = result.error
    return report


def inspect_smoke_configuration(
    args: argparse.Namespace,
    *,
    gpu_available: bool | None = None,
    require_clean_repo: bool = True,
    require_expected_branch: bool = True,
    nvidia_smi: dict[str, Any] | None = None,
    enforce_pinned_identity: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    git_report = _git_report()
    gpu_report = _gpu_report(
        gpu_available,
        gpu_index=args.gpu_index,
        nvidia_smi=nvidia_smi,
        allow_shared=bool(args.allow_shared_gpu),
    )
    apptainer_bin = _resolve_apptainer_bin(args.apptainer_bin)
    container = args.container_image
    container_digest = args.container_digest
    actual_sif = (
        file_sha256(container)
        if container is not None and container.is_file()
        else None
    )
    target_sha = _optional_file_sha(args.target_pdb)
    settings_sha = _optional_file_sha(args.settings_json)
    try:
        profile_sha = advanced_profile_identity_sha256(args.advanced_profile)
    except (OSError, ValueError, TypeError) as exc:
        profile_sha = None
        errors.append(f"advanced_profile_unreadable:{exc}")

    if target_sha != PINNED_TARGET_SHA256:
        errors.append("pinned_target_sha256_mismatch")
    if settings_sha != PINNED_SETTINGS_SHA256:
        errors.append("pinned_settings_sha256_mismatch")
    if profile_sha != PINNED_ADVANCED_PROFILE_SHA256:
        errors.append("pinned_advanced_profile_sha256_mismatch")
    if args.seed != PINNED_SEED:
        errors.append("bindcraft_smoke_seed_mismatch")

    for root in (
        args.output_root,
        args.artifact_root or default_artifact_root(),
        args.idempotency_root or default_idempotency_root(),
        args.run_state_root or default_run_state_root(),
    ):
        if _forbidden_output(root):
            errors.append("forbidden_output_location")

    tool_matches = args.tool_version == PINNED_BINDCRAFT_TOOL_VERSION
    digest_matches_pin = container_digest == PINNED_BINDCRAFT_SIF_SHA256
    actual_matches_pin = actual_sif == PINNED_BINDCRAFT_SIF_SHA256

    acting = args.execute_real or args.replay
    if acting:
        if not args.tool_version:
            errors.append("tool_version_required")
        elif enforce_pinned_identity and not tool_matches:
            errors.append("pinned_tool_version_mismatch")
        if container is None or not container_digest:
            errors.append("container_identity_required")
        elif enforce_pinned_identity and not digest_matches_pin:
            errors.append("pinned_container_digest_mismatch")
        if apptainer_bin is None:
            errors.append("apptainer_bin_required")
        elif not apptainer_bin.exists():
            errors.append("apptainer_bin_missing")
    if args.execute_real:
        if args.gpu_index is None:
            errors.append("gpu_index_required")
        if require_clean_repo and git_report.get("dirty"):
            errors.append("repository_dirty")
        if require_expected_branch and git_report.get("branch") != PINNED_BRANCH:
            errors.append("repository_branch_mismatch")
        if container is None or not container.is_file():
            errors.append("container_image_missing")
        else:
            if actual_sif != container_digest:
                errors.append("container_digest_mismatch")
            if enforce_pinned_identity and not actual_matches_pin:
                errors.append("pinned_sif_sha256_mismatch")
        if not gpu_report["gpu_available"]:
            errors.append("gpu_not_available")
        occupancy = gpu_report["occupancy"]
        if not occupancy.get("available"):
            errors.append("nvidia_smi_unavailable")
        selected = gpu_report.get("selected")
        if args.gpu_index is not None:
            if selected is None or selected.get("missing"):
                errors.append("selected_gpu_missing")

    planned_apptainerenv = (
        str(args.gpu_index) if args.gpu_index is not None else None
    )
    return {
        "commit": git_report.get("commit"),
        "branch": git_report.get("branch"),
        "dirty": git_report.get("dirty"),
        "expected_branch": PINNED_BRANCH,
        "target": str(args.target_pdb),
        "target_sha256": target_sha,
        "settings": str(args.settings_json),
        "settings_sha256": settings_sha,
        "advanced_profile": str(args.advanced_profile),
        "advanced_profile_sha256": profile_sha,
        "sif_path": None if container is None else str(container),
        "sif_sha256": actual_sif,
        "expected_pinned_tool_version": PINNED_BINDCRAFT_TOOL_VERSION,
        "provided_tool_version": args.tool_version,
        "tool_version_matches": tool_matches,
        "expected_pinned_sif_digest": PINNED_BINDCRAFT_SIF_SHA256,
        "provided_container_digest": container_digest,
        "actual_sif_digest": actual_sif,
        "container_digest_matches_pin": digest_matches_pin,
        "actual_sif_matches_pin": actual_matches_pin,
        "expected_container_digest": container_digest,
        "tool_version": args.tool_version,
        "adapter_version": ADAPTER_VERSION,
        "seed": args.seed,
        "gpu": gpu_report,
        "requested_host_gpu_index": args.gpu_index,
        "effective_apptainerenv_cuda_visible_devices": planned_apptainerenv,
        "apptainer_bin": None if apptainer_bin is None else str(apptainer_bin),
        "run_id": args.run_id,
        "execution_id": args.execution_id,
        "idempotency_key": args.idempotency_key,
        "output_root": str(args.output_root),
        "authorization": "developer_bindcraft_authorization",
        "errors": errors,
    }


def collect_persisted_report(
    store: FileIdempotencyStore,
    idempotency_key: str,
    *,
    artifact_root: Path,
) -> dict[str, Any]:
    record = store.get(idempotency_key)
    if record is None:
        return {"record": None}
    document = record.to_document()
    output_dir = Path(record.output_dir) if record.output_dir else None
    handoff = None
    stats = None
    snapshot = None
    if output_dir is not None:
        handoff_path = output_dir / HANDOFF_FILENAME
        if handoff_path.is_file():
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        stats_path = output_dir / "designs" / STATS_FILENAME
        stats = {
            "path": str(stats_path),
            "exists": stats_path.is_file(),
        }
        snapshot_dir = output_dir / ".cellertz_claimed_inputs"
        snapshot = _snapshot_report(snapshot_dir, handoff)
    science = _science_artifacts(artifact_root / record.run_id)
    return {
        "record": document,
        "handoff": handoff,
        "stats": stats,
        "snapshot": snapshot,
        "science": science,
    }


def _execution_request(args: argparse.Namespace) -> BindCraftExecutionRequest:
    if args.container_image is None or args.container_digest is None:
        raise ValueError("container_identity_required")
    apptainer_bin = _resolve_apptainer_bin(args.apptainer_bin)
    if apptainer_bin is None:
        raise ValueError("apptainer_bin_required")
    return BindCraftExecutionRequest(
        generation=GenerationRequest(
            target_id=PINNED_TARGET_ID,
            objective=PINNED_OBJECTIVE,
            generator=PINNED_GENERATOR,
            n_candidates=PINNED_N_CANDIDATES,
            constraints={
                "length_min": PINNED_LENGTH_MIN,
                "length_max": PINNED_LENGTH_MAX,
            },
        ),
        run_id=args.run_id,
        execution_id=args.execution_id,
        target_path=args.target_pdb,
        output_root=args.output_root,
        apptainer_bin=apptainer_bin,
        settings_path=args.settings_json,
        container_image=str(args.container_image),
        container_digest=args.container_digest,
        tool_version=args.tool_version,
        seed=args.seed,
        gpu_required=True,
    )


@contextmanager
def _apptainer_cuda_devices(gpu_index: int) -> Iterator[str]:
    previous = os.environ.get(APPTAINERENV_CUDA_VISIBLE_DEVICES)
    assigned = str(gpu_index)
    os.environ[APPTAINERENV_CUDA_VISIBLE_DEVICES] = assigned
    try:
        yield assigned
    finally:
        if previous is None:
            os.environ.pop(APPTAINERENV_CUDA_VISIBLE_DEVICES, None)
        else:
            os.environ[APPTAINERENV_CUDA_VISIBLE_DEVICES] = previous


def _snapshot_report(
    snapshot_dir: Path,
    handoff: dict[str, Any] | None,
) -> dict[str, Any]:
    files = []
    if snapshot_dir.is_dir():
        files = sorted(str(path) for path in snapshot_dir.iterdir())
    provenance = {}
    if isinstance(handoff, dict):
        raw = handoff.get("execution_provenance")
        if isinstance(raw, dict):
            provenance = raw
    sif_snapshot = provenance.get("execution_container_image")
    sif_digest = None
    if isinstance(sif_snapshot, str) and Path(sif_snapshot).is_file():
        sif_digest = file_sha256(Path(sif_snapshot))
    return {
        "dir": str(snapshot_dir),
        "exists": snapshot_dir.is_dir(),
        "files": files,
        "claimed_container_digest": provenance.get("container_digest"),
        "snapshot_container_digest": sif_digest,
        "advanced_profile_sha256": provenance.get("advanced_profile_sha256"),
    }


def _science_artifacts(artifact_dir: Path) -> dict[str, Any]:
    scoring = _read_json(artifact_dir / "scoring.json")
    ranking = _read_json(artifact_dir / "ranking.json")
    generation = _read_json(artifact_dir / "generation.json")
    return {
        "artifact_dir": str(artifact_dir),
        "generation": generation,
        "scoring": scoring,
        "ranking": ranking,
        "ipsae_is_probability": _ipsae_probability_flag(scoring),
        "ipsae_hard_filter": _ipsae_hard_filter_flag(scoring),
    }


def _ipsae_probability_flag(scoring: object) -> bool | None:
    evidence = _first_ipsae_evidence(scoring)
    if evidence is None:
        return None
    flag = evidence.get("is_probability")
    return flag if isinstance(flag, bool) else None


def _ipsae_hard_filter_flag(scoring: object) -> bool | None:
    evidence = _first_ipsae_evidence(scoring)
    if evidence is None:
        return None
    flag = evidence.get("recommended_as_hard_filter")
    return flag if isinstance(flag, bool) else None


def _first_ipsae_evidence(scoring: object) -> dict[str, Any] | None:
    if not isinstance(scoring, list) or not scoring:
        return None
    first = scoring[0]
    if not isinstance(first, dict):
        return None
    provenance = first.get("provenance")
    if not isinstance(provenance, dict):
        return None
    evidence = provenance.get("ipsae_evidence")
    return evidence if isinstance(evidence, dict) else None


def _read_json(path: Path) -> object | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _gpu_report(
    gpu_available: bool | None,
    *,
    gpu_index: int | None,
    nvidia_smi: dict[str, Any] | None,
    allow_shared: bool,
) -> dict[str, Any]:
    detected = detect_gpu() if gpu_available is None else gpu_available
    occupancy = nvidia_smi if nvidia_smi is not None else _query_nvidia_smi()
    selected = _selected_gpu(occupancy, gpu_index)
    return {
        "gpu_available": detected,
        "device_nvidia0": Path("/dev/nvidia0").exists(),
        "requested_host_gpu_index": gpu_index,
        "effective_apptainerenv_cuda_visible_devices": (
            str(gpu_index) if gpu_index is not None else None
        ),
        "occupancy": occupancy,
        "gpus": occupancy.get("gpus") or [],
        "selected": selected,
        "busy": bool(selected and selected.get("busy")),
        "sharing_explicit": allow_shared,
    }


def _selected_gpu(
    occupancy: dict[str, Any],
    gpu_index: int | None,
) -> dict[str, Any] | None:
    if gpu_index is None:
        return None
    gpus = occupancy.get("gpus") or []
    for gpu in gpus:
        if gpu.get("index") != gpu_index:
            continue
        uuid = gpu.get("uuid")
        processes = [
            app
            for app in occupancy.get("compute_apps") or []
            if app.get("gpu_uuid") == uuid
        ]
        return {
            **gpu,
            "processes": processes,
            "busy": bool(processes),
            "missing": False,
        }
    return {
        "index": gpu_index,
        "missing": True,
        "busy": False,
        "processes": [],
    }


def _query_nvidia_smi() -> dict[str, Any]:
    try:
        gpus = subprocess.run(
            [
                "nvidia-smi",
                (
                    "--query-gpu=index,uuid,name,memory.used,"
                    "memory.total,utilization.gpu"
                ),
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        apps = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name",
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {
            "available": False,
            "reason": "nvidia_smi_unavailable",
            "gpus": [],
            "compute_apps": [],
        }
    if gpus.returncode != 0:
        return {
            "available": False,
            "reason": "nvidia_smi_failed",
            "gpus": [],
            "compute_apps": [],
        }
    return {
        "available": True,
        "gpus": _parse_gpu_rows(gpus.stdout),
        "compute_apps": _parse_app_rows(apps.stdout),
    }


def _parse_gpu_rows(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for parts in _csv_rows(raw):
        if len(parts) < 6:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        rows.append(
            {
                "index": index,
                "uuid": parts[1],
                "name": parts[2],
                "memory_used": parts[3],
                "memory_total": parts[4],
                "utilization": parts[5],
            }
        )
    return rows


def _parse_app_rows(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for parts in _csv_rows(raw):
        if len(parts) < 3:
            continue
        rows.append(
            {
                "gpu_uuid": parts[0],
                "pid": parts[1],
                "process_name": parts[2],
            }
        )
    return rows


def _csv_rows(raw: str) -> list[list[str]]:
    return [
        [part.strip() for part in row]
        for row in csv.reader(io.StringIO(raw))
        if row
    ]


def _git_report() -> dict[str, Any]:
    try:
        commit = _git(["rev-parse", "HEAD"])
        branch = _git(["branch", "--show-current"])
        porcelain = _git(["status", "--porcelain"])
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {"commit": None, "branch": None, "dirty": True}
    return {
        "commit": commit,
        "branch": branch,
        "dirty": bool(porcelain),
    }


def _git(arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        cwd=REPO_ROOT,
    )
    return completed.stdout.strip()


def _resolve_apptainer_bin(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    found = shutil.which("apptainer")
    return Path(found) if found else None


def _optional_file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    return file_sha256(path)


def _forbidden_output(path: Path) -> bool:
    resolved = str(path.expanduser().resolve())
    return any(
        resolved == prefix or resolved.startswith(prefix + "/")
        for prefix in FORBIDDEN_OUTPUT_PREFIXES
    )


def _failed(error: str, **extra: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "execution_attempted": False,
        "replay_attempted": False,
        "error": error,
        "errors": [error],
    }
    payload.update(extra)
    return payload


def emit_report(report: dict[str, Any], output_json: Path | None) -> None:
    encoded = json.dumps(report, indent=2, sort_keys=True, default=str)
    print(encoded)
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(encoded + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_bindcraft_pdl1_smoke(args)
    emit_report(report, args.output_json)
    if not report.get("ok"):
        return 1
    if args.execute_real or args.replay:
        result = report.get("result") or {}
        if result.get("status") != "complete":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
