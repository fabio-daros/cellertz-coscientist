"""Scientific execution contracts. Transversal, not a protein-design type.

This package decides whether a scientific execution may happen.
`cellertz.runtime` decides how a process is launched. 4A does not
run BindCraft, RFdiffusion, mBER, or any GPU job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4


class ScientificExecutionMode(str, Enum):
    FIXTURE = "fixture"
    REAL = "real"


class RuntimeBackend(str, Enum):
    LOCAL = "local"
    AWS_BATCH = "aws_batch"


POLICY_ID = "scientific_real_execution"
POLICY_VERSION = "v1"

PROTEIN_BINDCRAFT_GENERATE = "protein.bindcraft.generate"
PROTEIN_RFDIFFUSION_GENERATE = "protein.rfdiffusion.generate"
PROTEIN_MBER_GENERATE = "protein.mber.generate"


@dataclass(frozen=True)
class DataAccessDeclaration:
    experimental_labels: bool = False
    evaluation_labels: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "experimental_labels": self.experimental_labels,
            "evaluation_labels": self.evaluation_labels,
        }


@dataclass(frozen=True)
class RealExecutionAuthorization:
    enabled: bool
    allowed_operations: frozenset[str]
    policy_id: str
    policy_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allowed_operations": sorted(self.allowed_operations),
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
        }


DISABLED_REAL_AUTHORIZATION = RealExecutionAuthorization(
    enabled=False,
    allowed_operations=frozenset(),
    policy_id=POLICY_ID,
    policy_version=POLICY_VERSION,
)


def system_real_authorization() -> RealExecutionAuthorization:
    """Process authorization for 4A. Always disabled."""
    return DISABLED_REAL_AUTHORIZATION


def new_execution_id() -> str:
    """Identity of one attempt. Never derived from the manifest hash."""
    return f"exec_{uuid4().hex}"


@dataclass(frozen=True)
class ScientificExecutionManifest:
    execution_id: str
    run_id: str
    domain: str
    operation: str
    execution_mode: ScientificExecutionMode
    runtime_backend: RuntimeBackend
    adapter_name: str
    adapter_version: str
    tool_name: str
    tool_version: str | None = None
    container_image: str | None = None
    container_digest: str | None = None
    checkpoint_name: str | None = None
    checkpoint_hash: str | None = None
    input_hashes: Mapping[str, str] = field(default_factory=dict)
    parameters: Mapping[str, object] = field(default_factory=dict)
    seed: int | None = None
    authorization_policy_id: str = POLICY_ID
    authorization_policy_version: str = POLICY_VERSION
    data_access: DataAccessDeclaration = field(
        default_factory=DataAccessDeclaration
    )
    manifest_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_hashes", dict(self.input_hashes))
        object.__setattr__(self, "parameters", dict(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "run_id": self.run_id,
            "domain": self.domain,
            "operation": self.operation,
            "execution_mode": self.execution_mode.value,
            "runtime_backend": self.runtime_backend.value,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "container_image": self.container_image,
            "container_digest": self.container_digest,
            "checkpoint_name": self.checkpoint_name,
            "checkpoint_hash": self.checkpoint_hash,
            "input_hashes": dict(self.input_hashes),
            "parameters": dict(self.parameters),
            "seed": self.seed,
            "authorization_policy_id": self.authorization_policy_id,
            "authorization_policy_version": self.authorization_policy_version,
            "data_access": self.data_access.to_dict(),
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True)
class ExecutionArtifact:
    uri: str
    sha256: str
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class ScientificExecutionResult:
    execution_id: str
    manifest_sha256: str
    status: str
    runtime_backend: RuntimeBackend
    started_at: str | None = None
    completed_at: str | None = None
    exit_code: int | None = None
    stdout_artifact: str | None = None
    stderr_artifact: str | None = None
    artifacts: tuple[ExecutionArtifact, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "manifest_sha256": self.manifest_sha256,
            "status": self.status,
            "runtime_backend": self.runtime_backend.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "exit_code": self.exit_code,
            "stdout_artifact": self.stdout_artifact,
            "stderr_artifact": self.stderr_artifact,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "error": self.error,
        }


@dataclass(frozen=True)
class ExecutionPolicyDecision:
    allowed: bool
    reason_code: str
    policy_id: str
    policy_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class PreparedExecution:
    manifest: ScientificExecutionManifest
    decision: ExecutionPolicyDecision
