import inspect
from dataclasses import replace
from pathlib import Path

from cellertz.execution.contracts import (
    DISABLED_REAL_AUTHORIZATION,
    PROTEIN_BINDCRAFT_GENERATE,
    DataAccessDeclaration,
    RealExecutionAuthorization,
    RuntimeBackend,
    ScientificExecutionManifest,
    ScientificExecutionMode,
    ScientificExecutionResult,
    new_execution_id,
    system_real_authorization,
)
from cellertz.execution.policy import (
    AUTHORIZATION_MISSING,
    AUTHORIZED,
    FIXTURE_ALLOWED,
    LABEL_ACCESS_FORBIDDEN,
    MANIFEST_INCOMPLETE,
    OPERATION_NOT_ALLOWLISTED,
    REAL_EXECUTION_DISABLED,
    RealExecutionPolicy,
    prepare_execution,
)
from cellertz.execution.provenance import freeze_manifest, manifest_sha256
from cellertz.mcp.capabilities import cellertz_capabilities
from cellertz.runtime.base import ExecutionRequest, ExecutionResult


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls += 1
        raise AssertionError("scientific execution must not reach runtime in 4A")


def _manifest(**overrides) -> ScientificExecutionManifest:
    payload = {
        "execution_id": "exec_test_a",
        "run_id": "protein_run_4a",
        "domain": "protein_design",
        "operation": PROTEIN_BINDCRAFT_GENERATE,
        "execution_mode": ScientificExecutionMode.REAL,
        "runtime_backend": RuntimeBackend.LOCAL,
        "adapter_name": "bindcraft",
        "adapter_version": "bindcraft_adapter_v1",
        "tool_name": "bindcraft",
        "tool_version": "bindcraft-simulated",
        "input_hashes": {"request": "abc123"},
        "parameters": {"n_candidates": 4, "target_id": "BCMA"},
        "seed": 7,
    }
    payload.update(overrides)
    return ScientificExecutionManifest(**payload)


def _enabled_auth(*operations: str) -> RealExecutionAuthorization:
    return RealExecutionAuthorization(
        enabled=True,
        allowed_operations=frozenset(operations or (PROTEIN_BINDCRAFT_GENERATE,)),
        policy_id="scientific_real_execution",
        policy_version="v1",
    )


def _deny_does_not_touch_runtime(manifest, authorization):
    runtime = RecordingRuntime()
    prepared = prepare_execution(manifest, authorization)
    assert prepared.decision.allowed is False
    assert runtime.calls == 0
    return prepared


def test_system_authorization_stays_disabled():
    auth = system_real_authorization()
    assert auth == DISABLED_REAL_AUTHORIZATION
    assert auth.enabled is False
    assert auth.allowed_operations == frozenset()


def test_real_without_authorization_is_denied():
    prepared = _deny_does_not_touch_runtime(_manifest(), None)
    assert prepared.decision.reason_code == AUTHORIZATION_MISSING


def test_real_with_disabled_authorization_is_denied():
    prepared = _deny_does_not_touch_runtime(
        _manifest(),
        DISABLED_REAL_AUTHORIZATION,
    )
    assert prepared.decision.reason_code == REAL_EXECUTION_DISABLED


def test_real_operation_not_allowlisted_is_denied():
    prepared = _deny_does_not_touch_runtime(
        _manifest(),
        _enabled_auth("protein.rfdiffusion.generate"),
    )
    assert prepared.decision.reason_code == OPERATION_NOT_ALLOWLISTED


def test_real_incomplete_manifest_is_denied():
    prepared = _deny_does_not_touch_runtime(
        _manifest(input_hashes={}),
        _enabled_auth(),
    )
    assert prepared.decision.reason_code == MANIFEST_INCOMPLETE


def test_real_label_access_is_denied():
    prepared = _deny_does_not_touch_runtime(
        _manifest(
            data_access=DataAccessDeclaration(experimental_labels=True),
        ),
        _enabled_auth(),
    )
    assert prepared.decision.reason_code == LABEL_ACCESS_FORBIDDEN


def test_simulated_authorization_does_not_launch_bindcraft():
    runtime = RecordingRuntime()
    prepared = prepare_execution(_manifest(), _enabled_auth())
    package = (
        Path("src/cellertz/execution/contracts.py").read_text()
        + Path("src/cellertz/execution/policy.py").read_text()
        + Path("src/cellertz/execution/provenance.py").read_text()
    )

    assert prepared.decision.allowed is True
    assert prepared.decision.reason_code == AUTHORIZED
    assert runtime.calls == 0
    assert "BindCraftAdapter" not in package
    assert "from cellertz.runtime" not in package
    assert "import hermes" not in package


def test_real_denial_does_not_fallback_to_fixture():
    denied = prepare_execution(_manifest(), DISABLED_REAL_AUTHORIZATION)
    fixture = prepare_execution(
        _manifest(execution_mode=ScientificExecutionMode.FIXTURE),
        DISABLED_REAL_AUTHORIZATION,
    )
    assert denied.decision.allowed is False
    assert denied.decision.reason_code == REAL_EXECUTION_DISABLED
    assert denied.manifest.execution_mode is ScientificExecutionMode.REAL
    assert fixture.decision.reason_code == FIXTURE_ALLOWED
    assert fixture.manifest.execution_mode is ScientificExecutionMode.FIXTURE


def test_fixture_policy_does_not_require_real_authorization():
    prepared = prepare_execution(
        _manifest(execution_mode=ScientificExecutionMode.FIXTURE),
        None,
    )
    assert prepared.decision.allowed is True
    assert prepared.decision.reason_code == FIXTURE_ALLOWED


def test_same_scientific_identity_same_hash():
    first = freeze_manifest(_manifest(execution_id="exec_a"))
    second = freeze_manifest(_manifest(execution_id="exec_b"))
    assert first.execution_id != second.execution_id
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.manifest_sha256 == manifest_sha256(first)


def test_parameter_and_seed_and_versions_change_hash():
    base = freeze_manifest(_manifest())
    changed = {
        "parameters": freeze_manifest(
            _manifest(parameters={"n_candidates": 8, "target_id": "BCMA"})
        ),
        "seed": freeze_manifest(_manifest(seed=99)),
        "checkpoint": freeze_manifest(
            _manifest(checkpoint_name="rfd", checkpoint_hash="chk-2")
        ),
        "tool": freeze_manifest(_manifest(tool_version="other")),
        "adapter": freeze_manifest(_manifest(adapter_version="v2")),
    }
    assert len({base.manifest_sha256, *(item.manifest_sha256 for item in changed.values())}) == 6


def test_hash_ignores_attempt_metadata():
    left = freeze_manifest(_manifest(execution_id="exec_1", run_id="run_1"))
    right = freeze_manifest(_manifest(execution_id="exec_2", run_id="run_2"))
    from cellertz.execution import provenance as provenance_module

    source = inspect.getsource(provenance_module.identity_payload)
    assert left.manifest_sha256 == right.manifest_sha256
    assert "execution_id" not in provenance_module.identity_payload(left)
    assert "run_id" not in provenance_module.identity_payload(left)
    assert "started_at" not in source
    assert "created_at" not in source


def test_execution_id_is_not_the_manifest_hash():
    first = new_execution_id()
    second = new_execution_id()
    frozen = freeze_manifest(_manifest(execution_id=first))
    assert first != second
    assert not first.startswith(frozen.manifest_sha256)
    assert frozen.execution_id != frozen.manifest_sha256


def test_result_is_separate_from_manifest():
    frozen = freeze_manifest(_manifest())
    result = ScientificExecutionResult(
        execution_id=frozen.execution_id,
        manifest_sha256=frozen.manifest_sha256,
        status="denied",
        runtime_backend=frozen.runtime_backend,
        error=REAL_EXECUTION_DISABLED,
    )
    assert result.execution_id == frozen.execution_id
    assert result.manifest_sha256 == frozen.manifest_sha256
    assert result.status == "denied"


def test_mcp_does_not_expose_real_execution():
    payload = cellertz_capabilities()
    mcp_source = Path("src/cellertz/mcp/server.py").read_text()
    start_source = Path("src/cellertz/mcp/start_run.py").read_text()
    assert payload["mutation"]["real_generators_allowed"] is False
    assert "cellertz.execution" not in mcp_source
    assert "cellertz.execution" not in start_source
    assert "prepare_execution" not in mcp_source


def test_execution_package_does_not_import_scientific_generators():
    for name in ("contracts.py", "policy.py", "provenance.py", "__init__.py"):
        source = Path("src/cellertz/execution", name).read_text()
        assert "from cellertz.domains.protein_design.adapters" not in source
        assert "BindCraftAdapter" not in source
        assert "RFdiffusionAdapter" not in source
        assert "MBERAdapter" not in source
        assert "from cellertz.runtime" not in source
        assert "/v1/runs" not in source


def test_default_data_access_forbids_label_leakage():
    frozen = freeze_manifest(_manifest())
    assert frozen.data_access.experimental_labels is False
    assert frozen.data_access.evaluation_labels is False


def test_policy_does_not_rewrite_real_as_fixture():
    denied = RealExecutionPolicy().evaluate(
        freeze_manifest(_manifest()),
        DISABLED_REAL_AUTHORIZATION,
    )
    rewritten = replace(
        freeze_manifest(_manifest()),
        execution_mode=ScientificExecutionMode.FIXTURE,
    )
    assert denied.allowed is False
    assert rewritten.execution_mode is ScientificExecutionMode.FIXTURE
    assert denied.reason_code != FIXTURE_ALLOWED
