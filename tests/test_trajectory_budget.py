"""CPU-only checks for the attempted-trajectory scheduling boundary.

The worker is the pinned BindCraft script. Its ``from functions import *``
line binds the replaced scheduler. Hallucination is a local function that
does not import or run BindCraft.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from cellertz.application import trajectory_budget as trajectory_budget_module
from cellertz.application.trajectory_budget import (
    ADAPTER_VERSION,
    BINDCRAFT_SCRIPT_SHA256,
    classify_budget_evidence,
    install_attempted_trajectory_budget,
    script_sha256,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    BOOTSTRAP_SOURCE,
    materialize_seed_bootstrap,
)

PINNED_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "bindcraft"
    / "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d"
    / "bindcraft.py"
)

_STUB = '''\
import argparse
import gc
import json
import os
import time
from pathlib import Path

import numpy as np


def check_jax_gpu():
    return None


def perform_input_check(parsed):
    return parsed.settings, parsed.filters, parsed.advanced


def load_json_settings(settings_path, filters_path, advanced_path):
    advanced = json.loads(Path(advanced_path).read_text(encoding="utf-8"))
    target = {
        "design_path": os.environ["CELLERTZ_FAKE_DESIGN_PATH"],
        "binder_name": "PDL1",
        "lengths": [65],
        "starting_pdb": "target.pdb",
        "chains": "A",
        "target_hotspot_residues": "1",
        "number_of_final_designs": 1,
    }
    return target, advanced, {}


def load_af2_models(use_multimer):
    return [], [], False


def perform_advanced_settings_check(advanced_settings, bindcraft_folder):
    return advanced_settings


def generate_directories(design_path):
    keys = [
        "Trajectory",
        "Trajectory/Relaxed",
        "Trajectory/LowConfidence",
        "Trajectory/Clashing",
        "Accepted",
        "MPNN/Relaxed",
    ]
    paths = {}
    root = Path(design_path)
    for key in keys:
        directory = root / key
        directory.mkdir(parents=True, exist_ok=True)
        paths[key] = str(directory)
    return paths


def generate_dataframe_labels():
    return [], [], []


def create_dataframe(path, labels):
    return None


def generate_filter_pass_csv(path, filters):
    return None


class _PyRosetta:
    def init(self, flags):
        return None


pr = _PyRosetta()


def load_helicity(advanced_settings):
    return 0


def check_n_trajectories(design_paths, advanced_settings):
    raise AssertionError("native trajectory counter was called")


def check_accepted_designs(*args, **kwargs):
    return os.environ.get("CELLERTZ_FAKE_ACCEPTED") == "1"


def copy_dict(values):
    return dict(values)


class _Trajectory:
    def __init__(self, terminate):
        self._tmp = {
            "best": {
                "aux": {
                    "log": {
                        "plddt": 0.1,
                        "ptm": 0.1,
                        "i_ptm": 0.1,
                        "pae": 0.1,
                        "i_pae": 0.1,
                    }
                }
            }
        }
        self.aux = {"log": {"terminate": terminate}}


def _record_call():
    path = Path(os.environ["CELLERTZ_FAKE_CALLS"])
    count = int(path.read_text(encoding="utf-8") or "0") + 1 if path.exists() else 1
    path.write_text(str(count), encoding="utf-8")
    return count


def binder_hallucination(
    design_name,
    starting_pdb,
    chain,
    target_hotspot_residues,
    length,
    seed,
    helicity_value,
    design_models,
    advanced_settings,
    design_paths,
    failure_csv,
):
    report = json.loads(Path(os.environ["CELLERTZ_BUDGET_REPORT"]).read_text(encoding="utf-8"))
    last = report["reservations"][-1]
    if last.get("state") != "started" or last.get("scheduler_reserved") is not True:
        raise RuntimeError("reservation was not recorded before hallucination")
    count = _record_call()
    mode = os.environ["CELLERTZ_FAKE_HALLUCINATION"]
    if mode == "return_without_pdb":
        return _Trajectory("LowConfidence")
    if mode == "exception":
        raise RuntimeError("hallucination failed before writing a pdb")
    if mode == "low_then_clash":
        folder = "Trajectory/LowConfidence" if count == 1 else "Trajectory/Clashing"
        label = "LowConfidence" if count == 1 else "Clashing"
        Path(design_paths[folder], f"{design_name}.pdb").write_text("ATOM\\n", encoding="utf-8")
        return _Trajectory(label)
    if mode == "multiple_files":
        if count == 1:
            Path(design_paths["Trajectory/Relaxed"], "one.pdb").write_text("ATOM\\n", encoding="utf-8")
            Path(design_paths["Trajectory/LowConfidence"], "two.pdb").write_text(
                "ATOM\\n",
                encoding="utf-8",
            )
        else:
            Path(design_paths["Trajectory/Relaxed"], f"{design_name}.pdb").write_text(
                "ATOM\\n",
                encoding="utf-8",
            )
        return _Trajectory("LowConfidence")
    raise RuntimeError("unknown hallucination mode")
'''


def test_pinned_script_calls_the_imported_scheduler_names() -> None:
    source = PINNED_SCRIPT.read_text(encoding="utf-8")
    assert script_sha256(PINNED_SCRIPT) == BINDCRAFT_SCRIPT_SHA256
    tree = ast.parse(source)
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "functions" and node.names[0].name == "*"
        for node in tree.body
    )
    loop = next(node for node in ast.walk(tree) if isinstance(node, ast.While))
    names = {"check_accepted_designs", "check_n_trajectories", "binder_hallucination"}
    called: list[str] = []
    for node in ast.walk(loop):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names:
            called.append(node.func.id)
    assert called[0] == "check_accepted_designs"
    assert called[1] == "check_n_trajectories"
    assert "binder_hallucination" in called


def test_a_failure_without_a_pdb_does_not_schedule_another_attempt(tmp_path: Path) -> None:
    result, report, calls, design = _run_pinned(tmp_path, budget=1, mode="return_without_pdb")
    assert result.returncode == 0, result.stderr
    assert _call_count(calls) == 1
    assert result.stdout.count("Starting trajectory:") == 1
    assert _pdb_files(design) == []
    saved = _ledger(report)
    assert saved["adapter_version"] == ADAPTER_VERSION
    assert saved["observed_attempted_trajectories"] == 1
    assert saved["reservations"][0]["state"] == "finished"
    assert saved["reservations"][0]["scheduler_reserved"] is True
    assert saved["termination_reason"] == "attempted_trajectory_budget_exhausted"
    assert saved["compliance_established"] is True
    assert saved["resume_supported"] is False


def test_rejected_low_confidence_trajectories_consume_the_budget_and_do_not_retry(
    tmp_path: Path,
) -> None:
    result, report, calls, _design = _run_pinned(tmp_path, budget=4, mode="return_without_pdb")
    assert result.returncode == 0, result.stderr
    assert _call_count(calls) == 4
    assert result.stdout.count("Starting trajectory:") == 4
    saved = _ledger(report)
    assert saved["requested_attempted_trajectories"] == 4
    assert saved["observed_attempted_trajectories"] == 4
    assert len(saved["reservations"]) == 4
    assert all(item["state"] == "finished" for item in saved["reservations"])
    assert saved["termination_reason"] == "attempted_trajectory_budget_exhausted"
    assert saved["resume_supported"] is False
    assert saved["compliance_established"] is True


def test_low_confidence_and_clashing_each_consume_one_attempt(tmp_path: Path) -> None:
    result, report, calls, design = _run_pinned(tmp_path, budget=2, mode="low_then_clash")
    assert result.returncode == 0, result.stderr
    assert _call_count(calls) == 2
    assert result.stdout.count("Starting trajectory:") == 2
    saved = _ledger(report)
    assert saved["observed_attempted_trajectories"] == 2
    assert len(saved["reservations"]) == 2
    assert saved["termination_reason"] == "attempted_trajectory_budget_exhausted"
    assert saved["compliance_established"] is True
    assert list((design / "Trajectory" / "LowConfidence").glob("*.pdb"))
    assert list((design / "Trajectory" / "Clashing").glob("*.pdb"))


def test_multiple_files_from_one_attempt_do_not_consume_multiple_attempts(tmp_path: Path) -> None:
    result, report, calls, design = _run_pinned(tmp_path, budget=2, mode="multiple_files")
    assert result.returncode == 0, result.stderr
    assert _call_count(calls) == 2
    assert len(_pdb_files(design)) >= 3
    saved = _ledger(report)
    assert saved["observed_attempted_trajectories"] == 2
    assert len(saved["reservations"]) == 2
    assert saved["termination_reason"] == "attempted_trajectory_budget_exhausted"
    assert saved["compliance_established"] is True


def test_unknown_source_or_invalid_budget_does_not_run_the_worker(tmp_path: Path) -> None:
    result, _report, calls, _design = _run_pinned(tmp_path, budget=False, mode="return_without_pdb")
    assert result.returncode != 0
    assert "trajectory_budget_invalid" in result.stderr
    assert "Starting trajectory:" not in result.stdout
    assert not calls.exists()

    unknown = tmp_path / "unknown-worker"
    unknown.mkdir()
    script = unknown / "bindcraft.py"
    script.write_text("print('STARTED')\n", encoding="utf-8")
    (unknown / "functions.py").write_text(
        "def check_n_trajectories(design_paths, advanced_settings):\n    return False\n",
        encoding="utf-8",
    )
    source = Path(trajectory_budget_module.__file__ or "")
    (unknown / "trajectory_budget.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    bootstrap = unknown / "bootstrap.py"
    bootstrap.write_text(BOOTSTRAP_SOURCE, encoding="utf-8")
    refused = subprocess.run(
        [sys.executable, str(bootstrap), "--cellertz-seed", "1", "--bindcraft-script", str(script)],
        cwd=unknown,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert refused.returncode != 0
    assert "STARTED" not in refused.stdout
    assert "bindcraft_source_unverified" in refused.stderr + refused.stdout


def test_retry_cannot_reset_a_consumed_budget(tmp_path: Path) -> None:
    result, report, calls, _design = _run_pinned(tmp_path, budget=1, mode="exception")
    assert result.returncode != 0
    assert _call_count(calls) == 1
    assert "hallucination failed before writing a pdb" in result.stderr
    saved = _ledger(report)
    assert saved["reservations"][0]["state"] == "failed"
    assert saved["observed_attempted_trajectories"] == 1
    assert saved["termination_reason"] == "attempt_failed"
    assert saved["compliance_established"] is True
    preserved = report.read_bytes()

    script = tmp_path / "bindcraft.py"
    advanced = tmp_path / "advanced.json"
    bootstrap = tmp_path / "bootstrap.py"
    env = os.environ.copy()
    env["CELLERTZ_FAKE_HALLUCINATION"] = "exception"
    env["CELLERTZ_FAKE_CALLS"] = str(calls)
    env["CELLERTZ_BUDGET_REPORT"] = str(report)
    env["CELLERTZ_FAKE_DESIGN_PATH"] = str(tmp_path / "design")
    env["CELLERTZ_FAKE_ACCEPTED"] = "0"
    retry = _execute(tmp_path, script, advanced, bootstrap, report, env)
    assert retry.returncode != 0
    assert "trajectory_budget_retry_refused" in retry.stderr
    assert report.read_bytes() == preserved
    assert _call_count(calls) == 1


def test_early_completion_is_distinct_from_budget_exhaustion(tmp_path: Path) -> None:
    result, report, calls, _design = _run_pinned(
        tmp_path,
        budget=1,
        mode="return_without_pdb",
        accepted=True,
    )
    assert result.returncode == 0, result.stderr
    assert _call_count(calls) == 0
    assert "Starting trajectory:" not in result.stdout
    saved = _ledger(report)
    assert saved["observed_attempted_trajectories"] == 0
    assert saved["termination_reason"] == "successful_early_completion"
    assert saved["compliance_established"] is True


def test_a_mismatched_approved_budget_does_not_start(tmp_path: Path) -> None:
    script = tmp_path / "bindcraft.py"
    script.write_bytes(PINNED_SCRIPT.read_bytes())
    advanced = tmp_path / "advanced.json"
    advanced.write_text('{"max_trajectories": 1}\n', encoding="utf-8")
    report = tmp_path / "budget.json"

    class Functions:
        def check_n_trajectories(self, design_paths: dict[str, str], advanced_settings: dict[str, object]) -> bool:
            raise AssertionError("native trajectory counter was called")

        def binder_hallucination(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("hallucination started")

        def check_accepted_designs(self, *args: object, **kwargs: object) -> bool:
            return False

    functions = Functions()
    install_attempted_trajectory_budget(
        functions,
        script_path=script,
        advanced_path=advanced,
        report_path=report,
    )
    assert functions.check_n_trajectories({}, {"max_trajectories": 2}) is True
    saved = _ledger(report)
    assert saved["termination_reason"] == "approved_budget_mismatch"
    assert saved["compliance_established"] is False
    assert saved["reservations"] == []


def test_an_unresolved_reservation_blocks_another_start_without_compliance(tmp_path: Path) -> None:
    script = tmp_path / "bindcraft.py"
    script.write_bytes(PINNED_SCRIPT.read_bytes())
    advanced = tmp_path / "advanced.json"
    advanced.write_text('{"max_trajectories": 1}\n', encoding="utf-8")
    report = tmp_path / "budget.json"
    calls = {"count": 0}

    class Functions:
        def check_n_trajectories(self, design_paths: dict[str, str], advanced_settings: dict[str, object]) -> bool:
            raise AssertionError("native trajectory counter was called")

        def binder_hallucination(self, *args: object, **kwargs: object) -> object:
            calls["count"] += 1
            raise AssertionError("hallucination started")

        def check_accepted_designs(self, *args: object, **kwargs: object) -> bool:
            return False

    functions = Functions()
    install_attempted_trajectory_budget(
        functions,
        script_path=script,
        advanced_path=advanced,
        report_path=report,
    )
    document = _ledger(report)
    reservations = document["reservations"]
    assert isinstance(reservations, list)
    reservations.append({"ordinal": 1, "state": "started", "scheduler_reserved": True})
    document["observed_attempted_trajectories"] = 1
    report.write_text(json.dumps(document), encoding="utf-8")
    assert functions.check_n_trajectories({}, {"max_trajectories": 1}) is True
    saved = _ledger(report)
    assert saved["termination_reason"] == "missing_evidence"
    assert saved["compliance_established"] is False
    assert calls["count"] == 0


def test_deadline_and_missing_evidence_are_not_compliance() -> None:
    exhausted = {
        "adapter_version": ADAPTER_VERSION,
        "resume_supported": False,
        "requested_attempted_trajectories": 1,
        "reservations": [{"ordinal": 1, "state": "finished", "scheduler_reserved": True}],
        "termination_reason": "attempted_trajectory_budget_exhausted",
        "compliance_established": True,
    }
    deadline = classify_budget_evidence(exhausted, process_disposition="deadline")
    assert deadline == {"termination_reason": "deadline_terminated", "compliance_established": False}
    missing = classify_budget_evidence(None)
    assert missing == {"termination_reason": "missing_evidence", "compliance_established": False}
    assert classify_budget_evidence(exhausted)["compliance_established"] is True


def test_materialized_bootstrap_carries_the_budget_adapter(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    materialize_seed_bootstrap(runtime)
    bootstrap = (runtime / "bindcraft_seeded_entrypoint.py").read_text(encoding="utf-8")
    budget = (runtime / "trajectory_budget.py").read_text(encoding="utf-8")
    assert "install_attempted_trajectory_budget" in bootstrap
    assert ADAPTER_VERSION in budget
    assert bootstrap.index("install_attempted_trajectory_budget") < bootstrap.index("runpy.run_path")


def _run_pinned(
    tmp_path: Path,
    *,
    budget: object,
    mode: str,
    accepted: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    script = tmp_path / "bindcraft.py"
    script.write_bytes(PINNED_SCRIPT.read_bytes())
    package = tmp_path / "functions"
    package.mkdir()
    (package / "__init__.py").write_text(_STUB, encoding="utf-8")
    advanced = tmp_path / "advanced.json"
    advanced.write_text(
        json.dumps(
            {
                "max_trajectories": budget,
                "use_multimer_design": False,
                "dalphaball_path": "unused",
            }
        ),
        encoding="utf-8",
    )
    source = Path(trajectory_budget_module.__file__ or "")
    (tmp_path / "trajectory_budget.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text(BOOTSTRAP_SOURCE, encoding="utf-8")
    design = tmp_path / "design"
    report = tmp_path / "budget.json"
    calls = tmp_path / "calls.txt"
    env = os.environ.copy()
    env["CELLERTZ_FAKE_DESIGN_PATH"] = str(design)
    env["CELLERTZ_FAKE_CALLS"] = str(calls)
    env["CELLERTZ_BUDGET_REPORT"] = str(report)
    env["CELLERTZ_FAKE_HALLUCINATION"] = mode
    env["CELLERTZ_FAKE_ACCEPTED"] = "1" if accepted else "0"
    return _execute(tmp_path, script, advanced, bootstrap, report, env), report, calls, design


def _execute(
    tmp_path: Path,
    script: Path,
    advanced: Path,
    bootstrap: Path,
    report: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(bootstrap),
            "--cellertz-seed",
            "1",
            "--bindcraft-script",
            str(script),
            "--cellertz-budget-report",
            str(report),
            "--settings",
            str(tmp_path / "settings.json"),
            "--filters",
            str(tmp_path / "filters.json"),
            "--advanced",
            str(advanced),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _ledger(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _call_count(path: Path) -> int:
    if not path.exists():
        return 0
    return int(path.read_text(encoding="utf-8"))


def _pdb_files(design: Path) -> list[Path]:
    if not design.exists():
        return []
    return list(design.rglob("*.pdb"))
