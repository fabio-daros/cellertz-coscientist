"""BindCraft real-execution bridge (4B.1) and preflight (4B.2).

The adapter does not decide whether it may run. This module builds
the manifest, asks RealExecutionPolicy, then inspects the environment.
It does not call BindCraft generation. 4B.4 stays compute-gated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cellertz.domains.protein_design.adapters.bindcraft import (
    ADAPTER_VERSION,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    request_input_hash,
)
from cellertz.execution.contracts import (
    POLICY_ID,
    POLICY_VERSION,
    PROTEIN_BINDCRAFT_GENERATE,
    DataAccessDeclaration,
    PreparedExecution,
    RealExecutionAuthorization,
    RuntimeBackend,
    ScientificExecutionManifest,
    ScientificExecutionMode,
    new_execution_id,
    system_real_authorization,
)
from cellertz.execution.policy import AUTHORIZED, prepare_execution

OPERATION = PROTEIN_BINDCRAFT_GENERATE
TOOL_NAME = "bindcraft"
FORBIDDEN_PARAMETER_KEYS = frozenset(
    {
        "ysd",
        "ipsae",
        "ranking_score",
        "rank",
        "selected",
        "critic",
        "binder_by_ysd_1000nm",
        "evaluation_label",
        "experimental_label",
    }
)


class BindCraftGenerationBlocked(RuntimeError):
    """4B.4 is compute-gated. Preflight or command-building is not generation."""


class BindCraftGenerator(Protocol):
    def generate(self, request: BindCraftAuthorizedRequest) -> object:
        ...

    def effective_advanced_profile_sha256(self) -> str:
        ...


def developer_bindcraft_authorization() -> RealExecutionAuthorization:
    """Explicit local allowlist. Not the process default. Not MCP."""
    return RealExecutionAuthorization(
        enabled=True,
        allowed_operations=frozenset({OPERATION}),
        policy_id=POLICY_ID,
        policy_version=POLICY_VERSION,
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def detect_gpu() -> bool:
    """Conservative host check. Does not start BindCraft."""
    return Path("/dev/nvidia0").exists()


@dataclass(frozen=True)
class BindCraftExecutionRequest:
    generation: GenerationRequest
    run_id: str
    execution_id: str | None = None
    target_path: Path | None = None
    output_root: Path | None = None
    bindcraft_bin: Path | None = None
    apptainer_bin: Path | None = None
    settings_path: Path | None = None
    checkpoint_path: Path | None = None
    container_image: str | None = None
    container_digest: str | None = None
    tool_version: str | None = None
    seed: int | None = None
    gpu_required: bool = True


@dataclass(frozen=True)
class BindCraftPreflight:
    operation: str
    status: str
    requirements: dict[str, bool]
    execution_attempted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "status": self.status,
            "requirements": dict(self.requirements),
            "execution_attempted": self.execution_attempted,
        }


@dataclass(frozen=True)
class BindCraftAuthorizedRequest:
    generation: GenerationRequest
    manifest: ScientificExecutionManifest
    output_dir: Path
    execution_request: BindCraftExecutionRequest
    advanced_profile_path: Path | None = None


@dataclass(frozen=True)
class BindCraftBridgeResult:
    prepared: PreparedExecution
    preflight: BindCraftPreflight | None
    authorized_request: BindCraftAuthorizedRequest | None
    generation_attempted: bool

    @property
    def authorized(self) -> bool:
        return self.prepared.decision.reason_code == AUTHORIZED


def _scientific_parameters(request: GenerationRequest) -> dict[str, object]:
    constraints: dict[str, object] = {}
    for key, value in request.constraints.items():
        lowered = str(key).lower()
        if lowered in FORBIDDEN_PARAMETER_KEYS:
            continue
        if lowered in {"bindcraft_command", "bindcraft_bin"}:
            continue
        constraints[str(key)] = value
    return {
        "target_id": request.target_id,
        "objective": request.objective,
        "n_candidates": request.n_candidates,
        "constraints": constraints,
    }


def canonical_bindcraft_generation_request(
    request: GenerationRequest,
) -> GenerationRequest:
    parameters = _scientific_parameters(request)
    return GenerationRequest(
        target_id=str(parameters["target_id"]),
        objective=str(parameters["objective"]),
        generator="bindcraft",
        n_candidates=int(parameters["n_candidates"]),
        constraints=dict(parameters["constraints"]),
    )


def scientific_request_sha256(request: GenerationRequest) -> str:
    return request_input_hash(
        canonical_bindcraft_generation_request(request)
    )


def _input_hashes(
    request: BindCraftExecutionRequest,
) -> dict[str, str]:
    hashes = {
        "generation_request": request_input_hash(request.generation),
    }
    if request.target_path is not None and request.target_path.is_file():
        hashes["target"] = file_sha256(request.target_path)
    if request.settings_path is not None and request.settings_path.is_file():
        hashes["settings"] = file_sha256(request.settings_path)
    if (
        request.checkpoint_path is not None
        and request.checkpoint_path.is_file()
    ):
        hashes["checkpoint"] = file_sha256(request.checkpoint_path)
    return hashes


def build_bindcraft_manifest(
    request: BindCraftExecutionRequest,
) -> ScientificExecutionManifest:
    execution_id = request.execution_id or new_execution_id()
    seed = request.seed
    if seed is None:
        raw_seed = request.generation.constraints.get("seed")
        seed = int(raw_seed) if raw_seed is not None else None
    return ScientificExecutionManifest(
        execution_id=execution_id,
        run_id=request.run_id,
        domain="protein_design",
        operation=OPERATION,
        execution_mode=ScientificExecutionMode.REAL,
        runtime_backend=RuntimeBackend.LOCAL,
        adapter_name="bindcraft",
        adapter_version=ADAPTER_VERSION,
        tool_name=TOOL_NAME,
        tool_version=request.tool_version,
        container_image=request.container_image,
        container_digest=request.container_digest,
        checkpoint_name=(
            request.checkpoint_path.name if request.checkpoint_path else None
        ),
        checkpoint_hash=(
            file_sha256(request.checkpoint_path)
            if request.checkpoint_path is not None
            and request.checkpoint_path.is_file()
            else None
        ),
        input_hashes=_input_hashes(request),
        parameters=_scientific_parameters(request.generation),
        seed=seed,
        data_access=DataAccessDeclaration(),
    )


def output_dir_for(
    request: BindCraftExecutionRequest,
    execution_id: str,
) -> Path:
    root = request.output_root or Path("outputs/scientific_execution")
    return root / execution_id


def inspect_bindcraft_environment(
    request: BindCraftExecutionRequest,
    manifest: ScientificExecutionManifest,
    *,
    gpu_available: bool | None = None,
) -> BindCraftPreflight:
    """Readiness only. execution_attempted stays false."""

    container_mode = request.container_image is not None

    if container_mode:
        tool_available = bool(
            request.apptainer_bin is not None
            and request.apptainer_bin.exists()
        )

        container_path = Path(request.container_image)
        container_available = container_path.is_file()

        container_digest_matches = bool(
            container_available
            and request.container_digest
            and file_sha256(container_path) == request.container_digest
        )
    else:
        tool_available = bool(
            request.bindcraft_bin is not None
            and request.bindcraft_bin.exists()
        )
        container_available = True
        container_digest_matches = True

    checkpoint_available = (
        request.checkpoint_path is None
        or request.checkpoint_path.is_file()
    )

    settings_available = (
        bool(
            request.settings_path is not None
            and request.settings_path.is_file()
        )
        if container_mode
        else True
    )

    input_valid = False
    if request.target_path is not None and request.target_path.is_file():
        input_valid = (
            manifest.input_hashes.get("target")
            == file_sha256(request.target_path)
        )

    output_dir = output_dir_for(request, manifest.execution_id)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_writable = output_dir.is_dir()
    except OSError:
        output_writable = False

    adapter_version_matches = manifest.adapter_version == ADAPTER_VERSION
    tool_version_identified = bool(manifest.tool_version)

    detected_gpu = (
        detect_gpu()
        if gpu_available is None
        else gpu_available
    )

    requirements = {
        "tool_available": tool_available,
        "container_available": container_available,
        "container_digest_matches": container_digest_matches,
        "checkpoint_available": checkpoint_available,
        "settings_available": settings_available,
        "input_valid": input_valid,
        "output_writable": output_writable,
        "adapter_version_matches": adapter_version_matches,
        "tool_version_identified": tool_version_identified,
        "gpu_required": request.gpu_required,
        "gpu_available": detected_gpu,
    }

    ready = all(
        (
            tool_available,
            container_available,
            container_digest_matches,
            checkpoint_available,
            settings_available,
            input_valid,
            output_writable,
            adapter_version_matches,
            tool_version_identified,
            (
                detected_gpu
                if request.gpu_required
                else True
            ),
        )
    )

    return BindCraftPreflight(
        operation=OPERATION,
        status="ready" if ready else "not_ready",
        requirements=requirements,
        execution_attempted=False,
    )

class BindCraftRealExecutionBridge:
    """Translate request → manifest → authorized adapter request."""

    def prepare(
        self,
        request: BindCraftExecutionRequest,
        authorization: RealExecutionAuthorization | None = None,
    ) -> BindCraftBridgeResult:
        """Authorization and immutable identity only. No preflight, no mkdir."""
        if authorization is None:
            authorization = system_real_authorization()
        prepared = prepare_execution(
            build_bindcraft_manifest(request),
            authorization,
        )
        if prepared.decision.reason_code != AUTHORIZED:
            return BindCraftBridgeResult(
                prepared=prepared,
                preflight=None,
                authorized_request=None,
                generation_attempted=False,
            )
        return BindCraftBridgeResult(
            prepared=prepared,
            preflight=None,
            authorized_request=BindCraftAuthorizedRequest(
                generation=request.generation,
                manifest=prepared.manifest,
                output_dir=output_dir_for(
                    request,
                    prepared.manifest.execution_id,
                ),
                execution_request=request,
            ),
            generation_attempted=False,
        )

    def attach_preflight(
        self,
        result: BindCraftBridgeResult,
        request: BindCraftExecutionRequest,
        *,
        gpu_available: bool | None = None,
    ) -> BindCraftBridgeResult:
        """Physical readiness. May create the attempt output directory."""
        if not result.authorized:
            return result
        preflight = inspect_bindcraft_environment(
            request,
            result.prepared.manifest,
            gpu_available=gpu_available,
        )
        authorized = result.authorized_request
        if authorized is None:
            authorized = BindCraftAuthorizedRequest(
                generation=request.generation,
                manifest=result.prepared.manifest,
                output_dir=output_dir_for(
                    request,
                    result.prepared.manifest.execution_id,
                ),
                execution_request=request,
            )
        return BindCraftBridgeResult(
            prepared=result.prepared,
            preflight=preflight,
            authorized_request=authorized,
            generation_attempted=False,
        )

    def plan(
        self,
        request: BindCraftExecutionRequest,
        authorization: RealExecutionAuthorization | None = None,
        *,
        gpu_available: bool | None = None,
    ) -> BindCraftBridgeResult:
        prepared = self.prepare(request, authorization)
        if not prepared.authorized:
            return prepared
        return self.attach_preflight(
            prepared,
            request,
            gpu_available=gpu_available,
        )

    def generate(
        self,
        result: BindCraftBridgeResult,
        generator: BindCraftGenerator,
        *,
        enable_generation: bool = False,
    ) -> object:
        """4B.4 only. Default path never starts BindCraft."""
        if not enable_generation:
            raise BindCraftGenerationBlocked(
                "BindCraft scientific generation is compute-gated (4B.4)."
            )
        if not result.authorized:
            raise BindCraftGenerationBlocked(
                "BindCraft generation requires AUTHORIZED."
            )
        if result.preflight is None or result.preflight.status != "ready":
            raise BindCraftGenerationBlocked(
                "BindCraft generation requires a ready preflight."
            )
        if result.authorized_request is None:
            raise BindCraftGenerationBlocked(
                "BindCraft generation requires an authorized request."
            )
        return generator.generate(result.authorized_request)
