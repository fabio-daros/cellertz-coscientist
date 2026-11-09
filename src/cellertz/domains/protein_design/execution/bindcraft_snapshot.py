"""Claimed BindCraft inputs must be the bytes that execute.

Content-addressed files, including the container image, are
re-hashed and copied into an immutable snapshot. The runtime
consumes snapshot paths, not the original mutable sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    canonical_advanced_profile_bytes,
    canonical_advanced_profile_sha256,
    load_validated_smoke_profile,
    resolve_advanced_profile_path,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    file_sha256,
)


class BindCraftInputDrift(ValueError):
    """Live input bytes no longer match the claimed scientific identity."""

    def __init__(self, input_name: str) -> None:
        self.input_name = input_name
        super().__init__(
            f"BindCraft input drift: {input_name}"
        )


@dataclass(frozen=True)
class BindCraftClaimedInputDigests:
    target_sha256: str | None
    settings_sha256: str | None
    checkpoint_sha256: str | None
    advanced_profile_sha256: str
    container_digest: str | None


@dataclass(frozen=True)
class BindCraftExecutionSnapshot:
    snapshot_dir: Path
    execution_request: BindCraftExecutionRequest
    advanced_profile_path: Path
    input_digests: BindCraftClaimedInputDigests


def capture_claimed_input_digests(
    request: BindCraftExecutionRequest,
    *,
    advanced_profile_sha256: str,
) -> BindCraftClaimedInputDigests:
    return BindCraftClaimedInputDigests(
        target_sha256=_optional_file_digest(request.target_path),
        settings_sha256=_optional_file_digest(request.settings_path),
        checkpoint_sha256=_optional_file_digest(
            request.checkpoint_path
        ),
        advanced_profile_sha256=advanced_profile_sha256,
        container_digest=request.container_digest,
    )


def snapshot_claimed_bindcraft_inputs(
    request: BindCraftExecutionRequest,
    claimed: BindCraftClaimedInputDigests,
    *,
    advanced_profile_source: Path | None,
    snapshot_dir: Path,
    execution_id: str,
) -> BindCraftExecutionSnapshot:
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    target_path = _snapshot_optional_file(
        request.target_path,
        claimed.target_sha256,
        snapshot_dir,
        "target",
    )
    settings_path = _snapshot_optional_file(
        request.settings_path,
        claimed.settings_sha256,
        snapshot_dir,
        "settings",
    )
    checkpoint_path = _snapshot_optional_file(
        request.checkpoint_path,
        claimed.checkpoint_sha256,
        snapshot_dir,
        "checkpoint",
    )
    advanced_profile_path = _snapshot_advanced_profile(
        advanced_profile_source,
        claimed.advanced_profile_sha256,
        snapshot_dir,
    )
    container_image = _snapshot_container(
        request,
        claimed,
        snapshot_dir,
    )

    return BindCraftExecutionSnapshot(
        snapshot_dir=snapshot_dir,
        execution_request=BindCraftExecutionRequest(
            generation=request.generation,
            run_id=request.run_id,
            execution_id=execution_id,
            target_path=target_path,
            output_root=request.output_root,
            bindcraft_bin=request.bindcraft_bin,
            apptainer_bin=request.apptainer_bin,
            settings_path=settings_path,
            checkpoint_path=checkpoint_path,
            container_image=container_image,
            container_digest=claimed.container_digest,
            tool_version=request.tool_version,
            seed=request.seed,
            gpu_required=request.gpu_required,
        ),
        advanced_profile_path=advanced_profile_path,
        input_digests=claimed,
    )


def _optional_file_digest(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return file_sha256(path)


def _snapshot_optional_file(
    source: Path | None,
    claimed_digest: str | None,
    snapshot_dir: Path,
    input_name: str,
) -> Path | None:
    if claimed_digest is None:
        if source is not None and source.is_file():
            raise BindCraftInputDrift(input_name)
        return None
    if source is None or not source.is_file():
        raise BindCraftInputDrift(input_name)
    if file_sha256(source) != claimed_digest:
        raise BindCraftInputDrift(input_name)

    destination = snapshot_dir / source.name
    destination.write_bytes(source.read_bytes())
    if file_sha256(destination) != claimed_digest:
        raise BindCraftInputDrift(input_name)
    destination.chmod(0o444)
    return destination


def _snapshot_advanced_profile(
    source: Path | None,
    claimed_digest: str,
    snapshot_dir: Path,
) -> Path:
    advanced = load_validated_smoke_profile(
        resolve_advanced_profile_path(source)
    )
    live_sha256 = canonical_advanced_profile_sha256(advanced)
    if live_sha256 != claimed_digest:
        raise BindCraftInputDrift("advanced_profile")
    canonical = canonical_advanced_profile_bytes(advanced)
    destination = snapshot_dir / "advanced.json"
    destination.write_bytes(canonical)
    if file_sha256(destination) != claimed_digest:
        raise BindCraftInputDrift("advanced_profile")
    destination.chmod(0o444)
    return destination


def _snapshot_container(
    request: BindCraftExecutionRequest,
    claimed: BindCraftClaimedInputDigests,
    snapshot_dir: Path,
) -> str | None:
    if claimed.container_digest is None:
        if request.container_image:
            raise BindCraftInputDrift("container")
        return None

    if request.container_digest != claimed.container_digest:
        raise BindCraftInputDrift("container")
    if request.container_image is None:
        raise BindCraftInputDrift("container")

    source = Path(request.container_image)
    if not source.is_file():
        raise BindCraftInputDrift("container")
    if file_sha256(source) != claimed.container_digest:
        raise BindCraftInputDrift("container")

    destination = snapshot_dir / source.name
    destination.write_bytes(source.read_bytes())
    if file_sha256(destination) != claimed.container_digest:
        raise BindCraftInputDrift("container")
    destination.chmod(0o444)
    return str(destination)
