import hashlib
from dataclasses import replace
from pathlib import Path

from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
)
from cellertz.runs.starter import bindcraft_run_request_hash

PROFILE_SHA256 = "a" * 64


def _request(
    tmp_path: Path,
    *,
    run_id: str = "protein_run_a",
    execution_id: str | None = "exec_a",
    seed: int = 7,
) -> BindCraftExecutionRequest:
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"

    target.write_text(
        "ATOM\n",
        encoding="utf-8",
    )
    settings.write_text(
        '{"chains":"A"}\n',
        encoding="utf-8",
    )

    return BindCraftExecutionRequest(
        generation=GenerationRequest(
            target_id="PDL1",
            objective="binder_design",
            generator="bindcraft",
            n_candidates=1,
            constraints={
                "length_min": 65,
                "length_max": 65,
            },
        ),
        run_id=run_id,
        execution_id=execution_id,
        target_path=target,
        output_root=tmp_path / "scientific_execution",
        settings_path=settings,
        container_image="/containers/bindcraft.sif",
        container_digest=hashlib.sha256(
            b"bindcraft-container"
        ).hexdigest(),
        tool_version="bindcraft-test",
        seed=seed,
        gpu_required=True,
    )


def test_bindcraft_identity_ignores_attempt_and_storage_identity(
    tmp_path,
):
    first = _request(
        tmp_path,
        run_id="protein_run_first",
        execution_id="exec_first",
    )

    second = replace(
        first,
        run_id="protein_run_second",
        execution_id="exec_second",
        output_root=tmp_path / "different-output-root",
    )

    assert bindcraft_run_request_hash(
        first,
        advanced_profile_sha256=PROFILE_SHA256,
    ) == bindcraft_run_request_hash(
        second,
        advanced_profile_sha256=PROFILE_SHA256,
    )


def test_bindcraft_identity_changes_with_scientific_inputs(
    tmp_path,
):
    baseline = _request(tmp_path)

    baseline_hash = bindcraft_run_request_hash(
        baseline,
        advanced_profile_sha256=PROFILE_SHA256,
    )

    changed_seed = replace(
        baseline,
        seed=8,
    )

    assert bindcraft_run_request_hash(
        changed_seed,
        advanced_profile_sha256=PROFILE_SHA256,
    ) != baseline_hash

    assert bindcraft_run_request_hash(
        baseline,
        advanced_profile_sha256="b" * 64,
    ) != baseline_hash


def test_bindcraft_identity_changes_when_target_content_changes(
    tmp_path,
):
    request = _request(tmp_path)

    before = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=PROFILE_SHA256,
    )

    assert request.target_path is not None

    request.target_path.write_text(
        "ATOM\nATOM\n",
        encoding="utf-8",
    )

    after = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=PROFILE_SHA256,
    )

    assert after != before


def test_bindcraft_identity_changes_when_settings_content_changes(
    tmp_path,
):
    request = _request(tmp_path)

    before = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=PROFILE_SHA256,
    )

    assert request.settings_path is not None

    request.settings_path.write_text(
        '{"chains":"A","lengths":[65,65]}\n',
        encoding="utf-8",
    )

    after = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=PROFILE_SHA256,
    )

    assert after != before


def test_bindcraft_identity_changes_with_execution_methodology(
    tmp_path,
):
    baseline = _request(tmp_path)

    baseline_hash = bindcraft_run_request_hash(
        baseline,
        advanced_profile_sha256=PROFILE_SHA256,
    )

    changed_container = replace(
        baseline,
        container_digest="c" * 64,
    )
    changed_tool = replace(
        baseline,
        tool_version="bindcraft-other-version",
    )

    assert bindcraft_run_request_hash(
        changed_container,
        advanced_profile_sha256=PROFILE_SHA256,
    ) != baseline_hash

    assert bindcraft_run_request_hash(
        changed_tool,
        advanced_profile_sha256=PROFILE_SHA256,
    ) != baseline_hash


def test_bindcraft_identity_changes_with_scientific_constraints(
    tmp_path,
):
    baseline = _request(tmp_path)

    changed_generation = replace(
        baseline.generation,
        constraints={
            **baseline.generation.constraints,
            "length_max": 70,
        },
    )
    changed_request = replace(
        baseline,
        generation=changed_generation,
    )

    assert bindcraft_run_request_hash(
        changed_request,
        advanced_profile_sha256=PROFILE_SHA256,
    ) != bindcraft_run_request_hash(
        baseline,
        advanced_profile_sha256=PROFILE_SHA256,
    )


def test_bindcraft_identity_ignores_forbidden_evaluation_fields(
    tmp_path,
):
    baseline = _request(tmp_path)

    contaminated_generation = replace(
        baseline.generation,
        constraints={
            **baseline.generation.constraints,
            "ipsae": 0.99,
            "ranking_score": 1.0,
            "rank": 1,
            "selected": True,
        },
    )
    contaminated = replace(
        baseline,
        generation=contaminated_generation,
    )

    assert bindcraft_run_request_hash(
        contaminated,
        advanced_profile_sha256=PROFILE_SHA256,
    ) == bindcraft_run_request_hash(
        baseline,
        advanced_profile_sha256=PROFILE_SHA256,
    )


def test_bindcraft_identity_ignores_input_storage_paths(
    tmp_path,
):
    first = _request(tmp_path)

    relocated = tmp_path / "relocated"
    relocated.mkdir()

    target_copy = relocated / "target-copy.pdb"
    settings_copy = relocated / "settings-copy.json"

    assert first.target_path is not None
    assert first.settings_path is not None

    target_copy.write_bytes(
        first.target_path.read_bytes()
    )
    settings_copy.write_bytes(
        first.settings_path.read_bytes()
    )

    second = replace(
        first,
        target_path=target_copy,
        settings_path=settings_copy,
        container_image="/different/path/bindcraft.sif",
    )

    assert bindcraft_run_request_hash(
        first,
        advanced_profile_sha256=PROFILE_SHA256,
    ) == bindcraft_run_request_hash(
        second,
        advanced_profile_sha256=PROFILE_SHA256,
    )


def test_bindcraft_identity_changes_with_checkpoint_content(
    tmp_path,
):
    checkpoint = tmp_path / "checkpoint.bin"
    checkpoint.write_bytes(b"checkpoint-v1")

    request = replace(
        _request(tmp_path),
        checkpoint_path=checkpoint,
    )

    before = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=PROFILE_SHA256,
    )

    checkpoint.write_bytes(b"checkpoint-v2")

    after = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=PROFILE_SHA256,
    )

    assert after != before
