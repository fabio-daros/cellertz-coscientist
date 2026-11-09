"""Fail-closed authorization for scientific execution.

REAL denied never falls back to fixture. Fixture failure never
promotes to real. This module does not call ExecutionRuntime.
"""

from __future__ import annotations

from cellertz.execution.contracts import (
    POLICY_ID,
    POLICY_VERSION,
    PROTEIN_BINDCRAFT_GENERATE,
    PROTEIN_MBER_GENERATE,
    PROTEIN_RFDIFFUSION_GENERATE,
    ExecutionPolicyDecision,
    PreparedExecution,
    RealExecutionAuthorization,
    ScientificExecutionManifest,
    ScientificExecutionMode,
)
from cellertz.execution.provenance import freeze_manifest


AUTHORIZED = "AUTHORIZED"
AUTHORIZATION_MISSING = "AUTHORIZATION_MISSING"
FIXTURE_ALLOWED = "FIXTURE_ALLOWED"
LABEL_ACCESS_FORBIDDEN = "LABEL_ACCESS_FORBIDDEN"
MANIFEST_INCOMPLETE = "MANIFEST_INCOMPLETE"
OPERATION_NOT_ALLOWLISTED = "OPERATION_NOT_ALLOWLISTED"
REAL_EXECUTION_DISABLED = "REAL_EXECUTION_DISABLED"

DEFAULT_REAL_REQUIREMENTS = frozenset(
    {
        "adapter_name",
        "adapter_version",
        "domain",
        "input_hashes",
        "operation",
        "tool_name",
    }
)
OPERATION_REQUIREMENTS: dict[str, frozenset[str]] = {
    PROTEIN_BINDCRAFT_GENERATE: DEFAULT_REAL_REQUIREMENTS,
    PROTEIN_RFDIFFUSION_GENERATE: DEFAULT_REAL_REQUIREMENTS,
    PROTEIN_MBER_GENERATE: DEFAULT_REAL_REQUIREMENTS,
}


def _decision(allowed: bool, reason_code: str) -> ExecutionPolicyDecision:
    return ExecutionPolicyDecision(
        allowed=allowed,
        reason_code=reason_code,
        policy_id=POLICY_ID,
        policy_version=POLICY_VERSION,
    )


def _present(manifest: ScientificExecutionManifest, field_name: str) -> bool:
    value = getattr(manifest, field_name)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value)
    return True


def _incomplete(manifest: ScientificExecutionManifest) -> bool:
    required = OPERATION_REQUIREMENTS.get(
        manifest.operation,
        DEFAULT_REAL_REQUIREMENTS,
    )
    return any(not _present(manifest, name) for name in required)


class RealExecutionPolicy:
    """Evaluate a frozen manifest. Never launches a tool."""

    def evaluate(
        self,
        manifest: ScientificExecutionManifest,
        authorization: RealExecutionAuthorization | None,
    ) -> ExecutionPolicyDecision:
        if manifest.execution_mode is ScientificExecutionMode.FIXTURE:
            return _decision(True, FIXTURE_ALLOWED)

        if authorization is None:
            return _decision(False, AUTHORIZATION_MISSING)
        if not authorization.enabled:
            return _decision(False, REAL_EXECUTION_DISABLED)
        if manifest.operation not in authorization.allowed_operations:
            return _decision(False, OPERATION_NOT_ALLOWLISTED)
        if _incomplete(manifest):
            return _decision(False, MANIFEST_INCOMPLETE)
        if (
            manifest.data_access.experimental_labels
            or manifest.data_access.evaluation_labels
        ):
            return _decision(False, LABEL_ACCESS_FORBIDDEN)
        return _decision(True, AUTHORIZED)


def prepare_execution(
    manifest: ScientificExecutionManifest,
    authorization: RealExecutionAuthorization | None = None,
    policy: RealExecutionPolicy | None = None,
) -> PreparedExecution:
    """Freeze the manifest, then decide. Runtime is not called."""
    frozen = freeze_manifest(manifest)
    decision = (policy or RealExecutionPolicy()).evaluate(frozen, authorization)
    return PreparedExecution(manifest=frozen, decision=decision)
