from pathlib import Path

from cellertz.domains.protein_design.adapters.bindcraft import ADAPTER_VERSION
from cellertz.domains.protein_design.agents.generation import GenerationRequest
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    OPERATION,
    BindCraftExecutionRequest,
    BindCraftGenerationBlocked,
    BindCraftRealExecutionBridge,
    developer_bindcraft_authorization,
)
from cellertz.execution.contracts import (
    DISABLED_REAL_AUTHORIZATION,
    system_real_authorization,
)
from cellertz.execution.policy import (
    AUTHORIZED,
    OPERATION_NOT_ALLOWLISTED,
    REAL_EXECUTION_DISABLED,
)
from cellertz.mcp.capabilities import cellertz_capabilities


class CountingGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        raise AssertionError("4B must not start BindCraft generation")


def _generation() -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator="bindcraft",
        n_candidates=1,
        constraints={"length_min": 50, "length_max": 60, "seed": 0},
    )


def _request(tmp_path: Path, **overrides) -> BindCraftExecutionRequest:
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
    checkpoint = tmp_path / "params.bin"
    bindcraft_bin = tmp_path / "bindcraft"
    target.write_text("ATOM\n")
    settings.write_text("{}\n")
    checkpoint.write_bytes(b"ckpt")
    bindcraft_bin.write_text("#!/bin/sh\n")
    payload = {
        "generation": _generation(),
        "run_id": "protein_run_4b",
        "execution_id": "exec_4b_test",
        "target_path": target,
        "output_root": tmp_path / "out",
        "bindcraft_bin": bindcraft_bin,
        "settings_path": settings,
        "checkpoint_path": checkpoint,
        "tool_version": "bindcraft-dev",
        "seed": 0,
        "gpu_required": True,
    }
    payload.update(overrides)
    return BindCraftExecutionRequest(**payload)


def test_default_authorization_denies_and_does_not_call_adapter(tmp_path):
    generator = CountingGenerator()
    bridge = BindCraftRealExecutionBridge()
    result = bridge.plan(_request(tmp_path))

    assert system_real_authorization() == DISABLED_REAL_AUTHORIZATION
    assert result.authorized is False
    assert result.prepared.decision.reason_code == REAL_EXECUTION_DISABLED
    assert result.preflight is None
    assert result.authorized_request is None
    assert result.generation_attempted is False
    assert generator.calls == 0


def test_denied_allowlist_does_not_call_adapter(tmp_path):
    generator = CountingGenerator()
    result = BindCraftRealExecutionBridge().plan(
        _request(tmp_path),
        DISABLED_REAL_AUTHORIZATION,
    )
    assert result.prepared.decision.reason_code in {
        REAL_EXECUTION_DISABLED,
        OPERATION_NOT_ALLOWLISTED,
    }
    assert result.generation_attempted is False
    assert generator.calls == 0


def test_authorized_without_gpu_is_not_ready_and_does_not_generate(tmp_path):
    generator = CountingGenerator()
    result = BindCraftRealExecutionBridge().plan(
        _request(tmp_path),
        developer_bindcraft_authorization(),
        gpu_available=False,
    )
    preflight = result.preflight

    assert result.authorized is True
    assert result.prepared.decision.reason_code == AUTHORIZED
    assert preflight is not None
    assert preflight.status == "not_ready"
    assert preflight.execution_attempted is False
    assert preflight.requirements["gpu_required"] is True
    assert preflight.requirements["gpu_available"] is False
    assert preflight.requirements["tool_available"] is True
    assert preflight.requirements["input_valid"] is True
    assert result.generation_attempted is False
    assert generator.calls == 0
    try:
        BindCraftRealExecutionBridge().generate(
            result,
            generator,
            enable_generation=True,
        )
    except BindCraftGenerationBlocked:
        pass
    else:
        raise AssertionError("expected generation to stay blocked")
    assert generator.calls == 0


def test_manifest_is_frozen_before_any_generation(tmp_path):
    result = BindCraftRealExecutionBridge().plan(
        _request(tmp_path),
        developer_bindcraft_authorization(),
        gpu_available=False,
    )
    manifest = result.prepared.manifest
    assert manifest.manifest_sha256
    assert manifest.execution_mode.value == "real"
    assert manifest.operation == OPERATION
    assert manifest.adapter_version == ADAPTER_VERSION
    assert manifest.data_access.experimental_labels is False
    assert manifest.data_access.evaluation_labels is False
    assert "ysd" not in str(manifest.parameters).lower()
    assert result.authorized_request is not None
    assert result.authorized_request.output_dir.name == manifest.execution_id


def test_retry_keeps_manifest_hash_and_changes_execution_id(tmp_path):
    request = _request(tmp_path, execution_id="exec_a")
    first = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=False,
    )
    second = BindCraftRealExecutionBridge().plan(
        _request(tmp_path, execution_id="exec_b"),
        developer_bindcraft_authorization(),
        gpu_available=False,
    )
    assert first.prepared.manifest.execution_id != second.prepared.manifest.execution_id
    assert (
        first.prepared.manifest.manifest_sha256
        == second.prepared.manifest.manifest_sha256
    )


def test_missing_target_fails_preflight_not_generation(tmp_path):
    generator = CountingGenerator()
    request = _request(tmp_path, target_path=tmp_path / "missing.pdb")
    result = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert result.preflight is not None
    assert result.preflight.status == "not_ready"
    assert result.preflight.requirements["input_valid"] is False
    assert result.preflight.execution_attempted is False
    assert generator.calls == 0


def test_ready_preflight_still_does_not_generate(tmp_path):
    generator = CountingGenerator()
    result = BindCraftRealExecutionBridge().plan(
        _request(tmp_path),
        developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert result.preflight is not None
    assert result.preflight.status == "ready"
    assert result.preflight.execution_attempted is False
    try:
        BindCraftRealExecutionBridge().generate(result, generator)
    except BindCraftGenerationBlocked:
        pass
    else:
        raise AssertionError("ready preflight is not 4B.4 generation")
    assert generator.calls == 0


def test_generation_default_path_is_blocked(tmp_path):
    generator = CountingGenerator()
    result = BindCraftRealExecutionBridge().plan(
        _request(tmp_path),
        developer_bindcraft_authorization(),
        gpu_available=False,
    )
    try:
        BindCraftRealExecutionBridge().generate(result, generator)
    except BindCraftGenerationBlocked as error:
        assert "4B.4" in str(error)
    else:
        raise AssertionError("expected default generate to be blocked")
    assert generator.calls == 0


def test_real_output_is_not_second_brain(tmp_path):
    vault = Path("second_brain")
    before = {
        path: path.read_bytes()
        for path in vault.rglob("*.md")
        if path.is_file()
    }
    BindCraftRealExecutionBridge().plan(
        _request(tmp_path),
        developer_bindcraft_authorization(),
        gpu_available=False,
    )
    after = {
        path: path.read_bytes()
        for path in vault.rglob("*.md")
        if path.is_file()
    }
    assert before == after
    source = Path(
        "src/cellertz/domains/protein_design/execution/bindcraft_execution.py"
    ).read_text()
    assert "FileKnowledgeReader" not in source
    assert "second_brain" not in source


def test_bridge_does_not_open_mcp_or_hermes():
    source = Path(
        "src/cellertz/domains/protein_design/execution/bindcraft_execution.py"
    ).read_text()
    assert "cellertz.mcp" not in source
    assert "hermes" not in source.lower()
    assert "/v1/runs" not in source
    payload = cellertz_capabilities()
    assert payload["mutation"]["real_generators_allowed"] is False
    assert system_real_authorization().enabled is False
    assert system_real_authorization().allowed_operations == frozenset()


def test_developer_auth_is_not_the_process_default():
    developer = developer_bindcraft_authorization()
    assert developer.enabled is True
    assert OPERATION in developer.allowed_operations
    assert system_real_authorization().enabled is False
