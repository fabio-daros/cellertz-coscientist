import sys
from pathlib import Path

from cellertz.domains.protein_design.adapters.bindcraft import BindCraftAdapter
from cellertz.domains.protein_design.adapters.mber import MBERAdapter
from cellertz.domains.protein_design.adapters.rfdiffusion import RFdiffusionAdapter
from cellertz.runtime import (
    ExecutionRequest,
    LocalExecutionRuntime,
)

FORBIDDEN_RUNTIME_TERMS = (
    "ipsae",
    "iptm",
    "i_ptm",
    "candidate_id",
    "ranking_score",
    "ranking",
    "prioritization",
    "ysd",
    "boltz",
)


def _runtime() -> LocalExecutionRuntime:
    return LocalExecutionRuntime()


def test_successful_structured_argv_completes():
    result = _runtime().execute(
        ExecutionRequest(
            argv=(sys.executable, "-c", "print('ok-runtime')"),
        )
    )

    assert result.status == "complete"
    assert result.return_code == 0
    assert "ok-runtime" in result.stdout
    assert result.runtime_name == "local"
    assert result.runtime_version == "local_execution_runtime_v2"
    assert result.started_at
    assert result.completed_at
    assert result.error is None


def test_nonzero_exit_is_structured_failure():
    result = _runtime().execute(
        ExecutionRequest(
            argv=(
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('boom\\n'); sys.exit(2)",
            ),
        )
    )

    assert result.status == "failed"
    assert result.return_code == 2
    assert "boom" in result.stderr
    assert result.error is not None
    assert "return code 2" in result.error


def test_timeout_is_structured_failure():
    result = _runtime().execute(
        ExecutionRequest(
            argv=(sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_seconds=0.2,
        )
    )

    assert result.status == "failed"
    assert result.return_code is None
    assert result.error == "Process timed out."


def test_stdout_and_stderr_are_captured():
    result = _runtime().execute(
        ExecutionRequest(
            argv=(
                sys.executable,
                "-c",
                "import sys; print('out'); sys.stderr.write('err\\n')",
            ),
        )
    )

    assert result.status == "complete"
    assert "out" in result.stdout
    assert "err" in result.stderr


def test_cwd_and_env_are_applied(tmp_path):
    result = _runtime().execute(
        ExecutionRequest(
            argv=(
                sys.executable,
                "-c",
                (
                    "import os\n"
                    "from pathlib import Path\n"
                    "print(Path.cwd())\n"
                    "print(os.environ['CELLERTZ_RUNTIME_TEST'])\n"
                ),
            ),
            cwd=tmp_path,
            env={"CELLERTZ_RUNTIME_TEST": "visible"},
        )
    )

    assert result.status == "complete"
    assert tmp_path.as_posix() in result.stdout.replace("\\", "/")
    assert "visible" in result.stdout


def test_shell_string_argv_is_rejected():
    result = _runtime().execute(
        ExecutionRequest(argv=("bindcraft --config config.yaml",))
    )

    assert result.status == "failed"
    assert result.return_code is None
    assert result.error is not None
    assert "structured tokens" in result.error


def test_empty_argv_is_rejected():
    result = _runtime().execute(ExecutionRequest(argv=()))

    assert result.status == "failed"
    assert "empty" in (result.error or "")


def test_runtime_does_not_encode_scientific_concepts():
    sources = [
        Path("src/cellertz/runtime/base.py").read_text(),
        Path("src/cellertz/runtime/local.py").read_text(),
        Path("src/cellertz/runtime/__init__.py").read_text(),
    ]
    blob = "\n".join(sources).lower()

    for term in FORBIDDEN_RUNTIME_TERMS:
        assert term not in blob


def test_adapters_accept_injected_local_runtime():
    runtime = LocalExecutionRuntime()

    bindcraft = BindCraftAdapter(runtime=runtime)
    rfdiffusion = RFdiffusionAdapter(runtime=runtime)
    mber = MBERAdapter(runtime=runtime)

    assert bindcraft.runtime is runtime
    assert rfdiffusion.runtime is runtime
    assert mber.runtime is runtime
    assert bindcraft.executor.runtime is runtime
    assert rfdiffusion.executor.runtime is runtime
    assert mber.executor.runtime is runtime


def test_failed_runtime_propagates_through_bindcraft_adapter(tmp_path):
    from cellertz.domains.protein_design.agents.generation import (
        GenerationRequest,
        ProteinGenerationAgent,
    )

    script = tmp_path / "fail.py"
    script.write_text("import sys\nsys.exit(2)\n")
    result = ProteinGenerationAgent(
        adapters={
            "bindcraft": BindCraftAdapter(
                runtime=LocalExecutionRuntime(),
                work_dir=tmp_path / "work",
            )
        }
    ).generate(
        GenerationRequest(
            target_id="BCMA",
            objective="binder_design",
            generator="bindcraft",
            n_candidates=1,
            constraints={
                "bindcraft_command": [sys.executable, str(script)],
            },
        )
    )

    assert result.status == "failed"
    assert result.candidates == ()
    assert result.error is not None
    assert "return code 2" in result.error


def test_timeout_terminates_spawned_child_process(tmp_path):
    import os
    import time

    if os.name != "posix":
        return

    sentinel = tmp_path / "child_survived.txt"
    child = tmp_path / "child.py"
    parent = tmp_path / "parent.py"

    child.write_text(
        "import time\n"
        "from pathlib import Path\n"
        "time.sleep(1.0)\n"
        f"Path({str(sentinel)!r}).write_text('survived')\n"
    )

    parent.write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "time.sleep(30)\n"
    )

    result = _runtime().execute(
        ExecutionRequest(
            argv=(
                sys.executable,
                str(parent),
            ),
            timeout_seconds=0.2,
        )
    )

    time.sleep(1.1)

    assert result.status == "failed"
    assert result.return_code is None
    assert result.error == "Process timed out."
    assert not sentinel.exists()
