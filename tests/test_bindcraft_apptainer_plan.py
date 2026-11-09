import json
from pathlib import Path

from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    CONTAINER_BINDCRAFT_SCRIPT,
    CONTAINER_NO_FILTERS,
    CONTAINER_OUTPUT,
    CONTAINER_SETTINGS,
    build_apptainer_plan,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    BindCraftRealExecutionBridge,
    developer_bindcraft_authorization,
)


def _generation() -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator="bindcraft",
        n_candidates=1,
        constraints={
            "length_min": 50,
            "length_max": 60,
        },
    )


def _execution_request(
    tmp_path: Path,
) -> BindCraftExecutionRequest:
    target = tmp_path / "target.pdb"
    settings = tmp_path / "target_settings.json"
    apptainer = tmp_path / "apptainer"
    container = tmp_path / "bindcraft.sif"

    target.write_text(
        "ATOM\n",
        encoding="utf-8",
    )

    settings.write_text(
        json.dumps(
            {
                "design_path": "/old/output",
                "binder_name": "BCMA",
                "starting_pdb": "/old/target.pdb",
                "chains": "A",
                "target_hotspot_residues": "56",
                "lengths": [65, 150],
                "number_of_final_designs": 100,
            }
        ),
        encoding="utf-8",
    )

    apptainer.write_text(
        "apptainer\n",
        encoding="utf-8",
    )

    container.write_bytes(b"sif")

    return BindCraftExecutionRequest(
        generation=_generation(),
        run_id="protein_run_plan_test",
        execution_id="exec_plan_test",
        target_path=target,
        output_root=tmp_path / "out",
        apptainer_bin=apptainer,
        settings_path=settings,
        container_image=str(container),
        container_digest=None,
        tool_version="bindcraft-test",
        seed=7,
        gpu_required=True,
    )


def _authorized(tmp_path: Path):
    request = _execution_request(tmp_path)

    result = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.authorized_request is not None

    return result.authorized_request


def test_materializes_authorized_target_settings(
    tmp_path,
):
    authorized = _authorized(tmp_path)

    plan = build_apptainer_plan(
        authorized,
        timeout_seconds=123,
    )

    settings = json.loads(
        plan.runtime_settings_path.read_text(
            encoding="utf-8"
        )
    )

    assert settings["starting_pdb"] == (
        "/cellertz/input/target.pdb"
    )
    assert settings["design_path"] == CONTAINER_OUTPUT
    assert settings["lengths"] == [50, 60]
    assert settings["number_of_final_designs"] == 1

    assert settings["binder_name"] == "BCMA"
    assert settings["chains"] == "A"
    assert settings["target_hotspot_residues"] == "56"

    assert plan.timeout_seconds == 123


def test_builds_structured_apptainer_argv(
    tmp_path,
):
    authorized = _authorized(tmp_path)

    plan = build_apptainer_plan(authorized)

    argv = plan.argv

    assert argv[1:5] == (
        "exec",
        "--cleanenv",
        "--contain",
        "--nv",
    )

    assert "--bind" in argv
    assert CONTAINER_BINDCRAFT_SCRIPT in argv
    assert CONTAINER_SETTINGS in argv
    assert CONTAINER_NO_FILTERS in argv

    assert "--cellertz-seed" in argv

    seed_index = argv.index(
        "--cellertz-seed"
    )

    assert argv[seed_index + 1] == "7"


def test_bootstrap_seeds_numpy_before_bindcraft(
    tmp_path,
):
    authorized = _authorized(tmp_path)

    plan = build_apptainer_plan(authorized)

    source = plan.bootstrap_path.read_text(
        encoding="utf-8"
    )

    assert "np.random.seed(args.cellertz_seed)" in source
    assert "runpy.run_path" in source


def test_materializes_bounded_advanced_profile(
    tmp_path,
):
    authorized = _authorized(tmp_path)

    profile = tmp_path / "smoke_advanced.json"
    profile.write_text(
        json.dumps(
            {
                "design_algorithm": "4stage",
                "max_trajectories": 1,
                "num_seqs": 1,
                "max_mpnn_sequences": 1,
            }
        ),
        encoding="utf-8",
    )

    plan = build_apptainer_plan(
        authorized,
        advanced_profile_path=profile,
    )

    advanced = json.loads(
        plan.runtime_advanced_path.read_text(
            encoding="utf-8"
        )
    )

    assert advanced["design_algorithm"] == "4stage"
    assert advanced["max_trajectories"] == 1
    assert advanced["num_seqs"] == 1
    assert advanced["max_mpnn_sequences"] == 1

    assert "/cellertz/config/advanced.json" in plan.argv

    advanced_index = plan.argv.index("--advanced")

    assert (
        plan.argv[advanced_index + 1]
        == "/cellertz/config/advanced.json"
    )


def test_apptainer_plan_disables_jax_preallocation(
    tmp_path,
):
    authorized = _authorized(tmp_path)

    plan = build_apptainer_plan(authorized)

    assert "/usr/bin/env" in plan.argv
    assert (
        "XLA_PYTHON_CLIENT_PREALLOCATE=false"
        in plan.argv
    )
    assert "PYTHONUNBUFFERED=1" in plan.argv

    env_index = plan.argv.index("/usr/bin/env")

    assert plan.argv[env_index + 1] == (
        "XLA_PYTHON_CLIENT_PREALLOCATE=false"
    )
    assert plan.argv[env_index + 2] == (
        "PYTHONUNBUFFERED=1"
    )
