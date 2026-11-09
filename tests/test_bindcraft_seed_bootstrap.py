import subprocess
import sys

from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    BOOTSTRAP_SOURCE,
)


def test_seed_bootstrap_supports_sibling_imports(tmp_path):
    functions_path = tmp_path / "functions.py"
    script_path = tmp_path / "bindcraft.py"
    bootstrap_path = tmp_path / "bootstrap.py"

    functions_path.write_text(
        "VALUE = 'sibling-import-ok'\n"
    )

    script_path.write_text(
        "from functions import VALUE\n"
        "print(VALUE)\n"
    )

    bootstrap_path.write_text(
        BOOTSTRAP_SOURCE
    )

    result = subprocess.run(
        [
            sys.executable,
            str(bootstrap_path),
            "--cellertz-seed",
            "0",
            "--bindcraft-script",
            str(script_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "sibling-import-ok"
    assert result.stderr == ""


def test_seed_bootstrap_preserves_existing_library_path(
    tmp_path,
):
    import os

    script_path = tmp_path / "bindcraft.py"
    bootstrap_path = tmp_path / "bootstrap.py"

    script_path.write_text(
        "import os\n"
        "print(os.environ['LD_LIBRARY_PATH'])\n"
    )

    bootstrap_path.write_text(
        BOOTSTRAP_SOURCE
    )

    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = (
        "/usr/local/nvidia/lib:"
        "/usr/local/nvidia/lib64:"
        "/.singularity.d/libs"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(bootstrap_path),
            "--cellertz-seed",
            "0",
            "--bindcraft-script",
            str(script_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0

    assert result.stdout.strip() == (
        "/opt/conda/envs/BindCraft/lib:"
        "/opt/conda/lib:"
        "/usr/local/nvidia/lib:"
        "/usr/local/nvidia/lib64:"
        "/.singularity.d/libs"
    )

    assert result.stderr == ""
