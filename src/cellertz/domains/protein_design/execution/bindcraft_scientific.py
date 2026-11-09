"""Compose scientific interpretation after BindCraft execution.

Does not launch BindCraft. Consumes existing artifacts only.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cellertz.domains.protein_design.adapters.bindcraft import (
    STATS_FILENAME,
    BindCraftAdapter,
    BindCraftArtifacts,
    ExistingBindCraftArtifactsExecutor,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    ProteinGenerationAgent,
)
from cellertz.domains.protein_design.evidence.bindcraft_ipsae import (
    BindCraftArtifactResolver,
    BindCraftIpSAEEvidenceProvider,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    canonical_bindcraft_generation_request,
    scientific_request_sha256,
)
from cellertz.domains.protein_design.scoring.bindcraft_ipsae import (
    BindCraftIpSAEScoreProvider,
)
from cellertz.tools.protein_scoring.ipsae import IpSAEConfig

HANDOFF_FILENAME = ".cellertz_execution_handoff.json"
REQUIRED_PROVENANCE_KEYS = (
    "execution_id",
    "run_id",
    "manifest_sha256",
)
IDENTITY_PROVENANCE_KEYS = (
    *REQUIRED_PROVENANCE_KEYS,
    "container_digest",
    "tool_version",
    "seed",
    "advanced_profile_sha256",
)

BINDCRAFT_IPSAE_CONFIG = IpSAEConfig(
    pae_cutoff=10.0,
    distance_cutoff=10.0,
    chain_a="A",
    chain_b="B",
    aggregation="max",
    protocol_id="CARPNN_2026_REFERENCE",
)


class BindCraftHandoffError(Exception):
    """Fail-closed BindCraft execution handoff."""


class BindCraftHandoffCorrupt(BindCraftHandoffError):
    """Handoff JSON is missing, truncated, or malformed."""


class BindCraftHandoffMismatch(BindCraftHandoffError):
    """Handoff does not belong to the persisted execution."""


class BindCraftHandoffPersistError(BindCraftHandoffError):
    """Official handoff could not be persisted."""


@dataclass(frozen=True)
class BindCraftExecutionHandoff:
    artifacts: BindCraftArtifacts
    scientific_request: GenerationRequest
    request_hash: str
    scientific_request_sha256: str


def bindcraft_ipsae_score_provider(
    output_root: Path,
) -> BindCraftIpSAEScoreProvider:
    return BindCraftIpSAEScoreProvider(
        evidence_provider=BindCraftIpSAEEvidenceProvider(
            resolver=BindCraftArtifactResolver(
                output_root=output_root,
            ),
            ipsae_config=BINDCRAFT_IPSAE_CONFIG,
        ),
    )


def bindcraft_generation_agent(
    artifacts: BindCraftArtifacts,
) -> ProteinGenerationAgent:
    return ProteinGenerationAgent(
        adapters={
            "bindcraft": BindCraftAdapter(
                executor=ExistingBindCraftArtifactsExecutor(
                    artifacts
                )
            )
        }
    )


def persist_bindcraft_execution_handoff(
    output_dir: Path,
    artifacts: BindCraftArtifacts,
    *,
    expected_provenance: Mapping[str, object],
    request_hash: str,
    scientific_request: GenerationRequest,
) -> None:
    provenance = dict(artifacts.execution_provenance)
    _validate_produced_provenance(provenance, expected_provenance)
    canonical = canonical_bindcraft_generation_request(scientific_request)
    request_digest = scientific_request_sha256(canonical)
    execution_id = str(expected_provenance["execution_id"])
    run_id = str(expected_provenance["run_id"])
    manifest_sha256 = str(expected_provenance["manifest_sha256"])
    document = {
        "execution_id": execution_id,
        "run_id": run_id,
        "manifest_sha256": manifest_sha256,
        "request_hash": request_hash,
        "scientific_request_sha256": request_digest,
        "stats_csv": Path(artifacts.stats_csv).name,
        "bindcraft_version": artifacts.bindcraft_version,
        "returncode": artifacts.returncode,
        "command": list(artifacts.command),
        "execution_provenance": provenance,
        "scientific_request": {
            "target_id": canonical.target_id,
            "objective": canonical.objective,
            "generator": "bindcraft",
            "n_candidates": canonical.n_candidates,
            "constraints": dict(canonical.constraints),
        },
    }
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(output_dir / HANDOFF_FILENAME, document)
    except OSError as exc:
        raise BindCraftHandoffPersistError(
            "bindcraft_handoff_persist_failed"
        ) from exc


def load_bindcraft_execution_handoff(
    output_dir: Path,
    *,
    expected_execution_id: str,
    expected_run_id: str,
    expected_manifest_sha256: str,
    expected_request_hash: str | None = None,
    expected_scientific_request_sha256: str,
) -> BindCraftExecutionHandoff:
    handoff_path = output_dir / HANDOFF_FILENAME
    designs_dir = output_dir / "designs"
    stats_csv = designs_dir / STATS_FILENAME
    try:
        raw = handoff_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BindCraftHandoffCorrupt(
            "bindcraft_handoff_unreadable"
        ) from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BindCraftHandoffCorrupt(
            "bindcraft_handoff_malformed"
        ) from exc
    if not isinstance(document, dict):
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    _require_identity(
        document.get("execution_id"),
        expected_execution_id,
        "execution_id",
    )
    _require_identity(document.get("run_id"), expected_run_id, "run_id")
    _require_identity(
        document.get("manifest_sha256"),
        expected_manifest_sha256,
        "manifest_sha256",
    )
    request_hash = document.get("request_hash")
    if not isinstance(request_hash, str) or not request_hash:
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    if expected_request_hash is not None:
        _require_identity(
            request_hash,
            expected_request_hash,
            "request_hash",
        )
    provenance = document.get("execution_provenance")
    if not isinstance(provenance, dict):
        raise BindCraftHandoffCorrupt("bindcraft_handoff_provenance_missing")
    for key in REQUIRED_PROVENANCE_KEYS:
        if key not in provenance:
            raise BindCraftHandoffCorrupt(
                "bindcraft_handoff_provenance_missing"
            )
    _require_identity(
        provenance.get("execution_id"),
        expected_execution_id,
        "execution_id",
    )
    _require_identity(provenance.get("run_id"), expected_run_id, "run_id")
    _require_identity(
        provenance.get("manifest_sha256"),
        expected_manifest_sha256,
        "manifest_sha256",
    )
    if not stats_csv.is_file():
        raise BindCraftHandoffCorrupt("bindcraft_stats_missing")
    command = document.get("command")
    if not isinstance(command, list) or any(
        not isinstance(part, str) for part in command
    ):
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    returncode = document.get("returncode")
    if type(returncode) is not int:
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    bindcraft_version = document.get("bindcraft_version")
    if bindcraft_version is not None and not isinstance(bindcraft_version, str):
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    scientific_request = _scientific_request_from_document(
        document.get("scientific_request")
    )
    recomputed = scientific_request_sha256(scientific_request)
    _require_identity(
        recomputed,
        expected_scientific_request_sha256,
        "scientific_request_sha256",
    )
    stored_digest = document.get("scientific_request_sha256")
    if stored_digest is not None:
        _require_identity(
            stored_digest,
            expected_scientific_request_sha256,
            "scientific_request_sha256",
        )
    return BindCraftExecutionHandoff(
        artifacts=BindCraftArtifacts(
            output_dir=designs_dir,
            stats_csv=stats_csv,
            command=tuple(command),
            bindcraft_version=bindcraft_version,
            returncode=returncode,
            execution_provenance=dict(provenance),
        ),
        scientific_request=scientific_request,
        request_hash=request_hash,
        scientific_request_sha256=recomputed,
    )


def run_bindcraft_scientific_pipeline(
    *,
    request: GenerationRequest,
    artifacts: BindCraftArtifacts,
    output_root: Path,
    artifact_dir: Path,
    run_id: str,
    run_state_store: Any,
    supervisor_factory: Callable[..., Any],
) -> Any:
    supervisor = supervisor_factory(
        request=request,
        artifact_dir=artifact_dir,
        run_id=run_id,
        generation_agent=bindcraft_generation_agent(artifacts),
        score_fn=bindcraft_ipsae_score_provider(output_root),
        run_state_store=run_state_store,
    )
    return supervisor.run()


def _validate_produced_provenance(
    provenance: Mapping[str, object],
    expected: Mapping[str, object],
) -> None:
    for key in IDENTITY_PROVENANCE_KEYS:
        if key not in expected or expected[key] is None:
            continue
        if key not in provenance:
            raise BindCraftHandoffMismatch(
                f"bindcraft_handoff_{key}_mismatch"
            )
        if provenance[key] != expected[key]:
            raise BindCraftHandoffMismatch(
                f"bindcraft_handoff_{key}_mismatch"
            )


def _scientific_request_from_document(document: object) -> GenerationRequest:
    if not isinstance(document, dict):
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    target_id = document.get("target_id")
    objective = document.get("objective")
    generator = document.get("generator")
    n_candidates = document.get("n_candidates")
    constraints = document.get("constraints")
    if not isinstance(target_id, str) or not isinstance(objective, str):
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    if generator != "bindcraft":
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    if type(n_candidates) is not int:
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    if not isinstance(constraints, dict):
        raise BindCraftHandoffCorrupt("bindcraft_handoff_malformed")
    return canonical_bindcraft_generation_request(
        GenerationRequest(
            target_id=target_id,
            objective=objective,
            generator="bindcraft",
            n_candidates=n_candidates,
            constraints=dict(constraints),
        )
    )


def _require_identity(
    actual: object,
    expected: str,
    field: str,
) -> None:
    if not isinstance(actual, str) or actual != expected:
        raise BindCraftHandoffMismatch(
            f"bindcraft_handoff_{field}_mismatch"
        )


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    encoded = json.dumps(
        document,
        sort_keys=True,
        indent=2,
    ) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix="handoff.",
        suffix=".json.tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
