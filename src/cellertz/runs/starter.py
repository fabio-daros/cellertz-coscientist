"""ProteinRunStarter: the only mutating layer allowed to know the Supervisor."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from cellertz.domains.protein_design.adapters.bindcraft import (
    BindCraftArtifacts,
)
from cellertz.domains.protein_design.agents.generation import GenerationRequest
from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftAuthorizedRequest,
    BindCraftBridgeResult,
    BindCraftExecutionRequest,
    BindCraftGenerator,
    BindCraftRealExecutionBridge,
    build_bindcraft_manifest,
    canonical_bindcraft_generation_request,
    scientific_request_sha256,
)
from cellertz.domains.protein_design.execution.bindcraft_scientific import (
    BindCraftHandoffError,
    load_bindcraft_execution_handoff,
    persist_bindcraft_execution_handoff,
    run_bindcraft_scientific_pipeline,
)
from cellertz.domains.protein_design.execution.bindcraft_snapshot import (
    BindCraftClaimedInputDigests,
    BindCraftInputDrift,
    capture_claimed_input_digests,
    snapshot_claimed_bindcraft_inputs,
)
from cellertz.execution.contracts import RealExecutionAuthorization
from cellertz.runs.authorization import fixture_mutations_allowed
from cellertz.runs.idempotency import (
    FileIdempotencyStore,
    IdempotencyRecord,
    ProteinRunIdempotencyStore,
    canonical_request_hash,
    default_idempotency_root,
)
from cellertz.runs.models import ProteinFixtureRunRequest, ProteinRunStartResult
from cellertz.state.run_state import (
    FileProteinRunStateStore,
    ProteinRunStateWriter,
    default_run_state_root,
)

FIXTURE_CONSTRAINTS: dict[str, object] = {
    "length_min": 50,
    "length_max": 100,
}


def default_artifact_root() -> Path:
    return Path(os.environ.get("CELLERTZ_RUN_ARTIFACT_ROOT", "outputs/runs"))


def bindcraft_run_request_hash(
    request: BindCraftExecutionRequest,
    *,
    advanced_profile_sha256: str,
) -> str:
    """Stable scientific identity, excluding execution-attempt identity."""
    manifest = build_bindcraft_manifest(request)

    input_hashes = {
        key: value
        for key, value in manifest.input_hashes.items()
        if key != "generation_request"
    }

    return canonical_request_hash(
        {
            "mode": "bindcraft_real",
            "domain": manifest.domain,
            "operation": manifest.operation,
            "adapter_name": manifest.adapter_name,
            "adapter_version": manifest.adapter_version,
            "tool_name": manifest.tool_name,
            "tool_version": manifest.tool_version,
            "container_digest": manifest.container_digest,
            "input_hashes": input_hashes,
            "parameters": dict(manifest.parameters),
            "seed": manifest.seed,
            "gpu_required": request.gpu_required,
            "advanced_profile_sha256": advanced_profile_sha256,
        }
    )


class ProteinRunStarter(Protocol):
    def start_fixture_run(
        self,
        request: ProteinFixtureRunRequest,
        idempotency_key: str,
    ) -> ProteinRunStartResult:
        ...


class LocalProteinRunStarter:
    def __init__(
        self,
        *,
        idempotency_store: ProteinRunIdempotencyStore | None = None,
        run_state_store: ProteinRunStateWriter | None = None,
        artifact_root: Path | None = None,
        supervisor_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.idempotency_store = idempotency_store or FileIdempotencyStore(
            default_idempotency_root()
        )
        self.run_state_store = run_state_store or FileProteinRunStateStore(
            default_run_state_root()
        )
        self.artifact_root = (
            Path(artifact_root) if artifact_root is not None else default_artifact_root()
        )
        self.supervisor_factory = supervisor_factory or ProteinSupervisorV1

    def start_bindcraft_run(
        self,
        request: BindCraftExecutionRequest,
        idempotency_key: str,
        *,
        generator: BindCraftGenerator,
        authorization: RealExecutionAuthorization | None = None,
        gpu_available: bool | None = None,
    ) -> ProteinRunStartResult:
        """Execute one claimed BindCraft scientific identity.

        Historical rejected / bindcraft_run_not_implemented records
        stay terminal. A real execution uses a fresh key.

        SCIENTIFIC IDENTITY CLAIMED == SCIENTIFIC INPUTS EXECUTED.
        Snapshot or fail closed before runtime.execute().
        Durable replay inspects the original key before physical preflight.
        """
        bridge = BindCraftRealExecutionBridge()
        prepared = bridge.prepare(request, authorization)

        if not prepared.authorized:
            return ProteinRunStartResult(
                run_id=None,
                mode="real",
                created=False,
                idempotent_replay=False,
                status="rejected",
                idempotency_key=idempotency_key,
                error=prepared.prepared.decision.reason_code,
            )

        profile_sha256 = generator.effective_advanced_profile_sha256()
        request_hash = bindcraft_run_request_hash(
            request,
            advanced_profile_sha256=profile_sha256,
        )
        existing = self.idempotency_store.get(idempotency_key)
        if existing is not None:
            return self._replay_or_resume_bindcraft(
                existing,
                request_hash,
                idempotency_key,
            )

        planned = bridge.attach_preflight(
            prepared,
            request,
            gpu_available=gpu_available,
        )
        if planned.preflight is None or planned.preflight.status != "ready":
            return ProteinRunStartResult(
                run_id=None,
                mode="real",
                created=False,
                idempotent_replay=False,
                status="rejected",
                idempotency_key=idempotency_key,
                error="bindcraft_preflight_not_ready",
            )

        claimed_inputs = capture_claimed_input_digests(
            request,
            advanced_profile_sha256=profile_sha256,
        )
        pending = IdempotencyRecord(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            run_id=request.run_id,
            mode="real",
            created_at=datetime.now(UTC).isoformat(),
            status="pending",
        )
        acquired, claimed = self.idempotency_store.try_claim(pending)
        if not acquired:
            return self._replay_or_resume_bindcraft(
                claimed,
                request_hash,
                idempotency_key,
            )

        return self._execute_claimed_bindcraft_run(
            request,
            planned,
            pending,
            claimed_inputs,
            generator,
        )

    def _replay_or_resume_bindcraft(
        self,
        claimed: IdempotencyRecord,
        request_hash: str,
        idempotency_key: str,
    ) -> ProteinRunStartResult:
        if claimed.request_hash != request_hash:
            return ProteinRunStartResult(
                run_id=None,
                mode="real",
                created=False,
                idempotent_replay=False,
                status="conflict",
                idempotency_key=idempotency_key,
                error="idempotency_key_conflict",
            )
        settled = claimed
        if claimed.status == "pending":
            settled = (
                self.idempotency_store.wait_until_settled(
                    idempotency_key
                )
                or claimed
            )
        if settled.status == "execution_complete":
            return self._acquire_bindcraft_science(
                settled,
                created=False,
                idempotent_replay=True,
            )
        if settled.status == "scientific_pending":
            settled = (
                self.idempotency_store.wait_until_settled(
                    idempotency_key
                )
                or settled
            )
        return self._bindcraft_replay_result(
            settled,
            idempotency_key,
        )

    def _execute_claimed_bindcraft_run(
        self,
        request: BindCraftExecutionRequest,
        planned: BindCraftBridgeResult,
        pending: IdempotencyRecord,
        claimed_inputs: BindCraftClaimedInputDigests,
        generator: BindCraftGenerator,
    ) -> ProteinRunStartResult:
        assert planned.authorized_request is not None
        try:
            snapshot = snapshot_claimed_bindcraft_inputs(
                request,
                claimed_inputs,
                advanced_profile_source=getattr(
                    generator,
                    "advanced_profile_path",
                    None,
                ),
                snapshot_dir=(
                    planned.authorized_request.output_dir
                    / ".cellertz_claimed_inputs"
                ),
                execution_id=(
                    planned.prepared.manifest.execution_id
                ),
            )
        except BindCraftInputDrift as exc:
            error = (
                "bindcraft_container_digest_mismatch"
                if exc.input_name == "container"
                else "bindcraft_input_drift"
            )
            failed = self._put_bindcraft_record(
                pending.evolve(
                    status="failed",
                    execution_status="failed",
                    error=error,
                )
            )
            return self._bindcraft_owned_result(
                failed,
                created=True,
                idempotent_replay=False,
            )

        authorized = BindCraftAuthorizedRequest(
            generation=planned.authorized_request.generation,
            manifest=planned.authorized_request.manifest,
            output_dir=planned.authorized_request.output_dir,
            execution_request=snapshot.execution_request,
            advanced_profile_path=snapshot.advanced_profile_path,
        )
        executable = BindCraftBridgeResult(
            prepared=planned.prepared,
            preflight=planned.preflight,
            authorized_request=authorized,
            generation_attempted=False,
        )

        try:
            outcome = BindCraftRealExecutionBridge().generate(
                executable,
                generator,
                enable_generation=True,
            )
        except Exception as exc:  # noqa: BLE001
            failed = self._put_bindcraft_record(
                pending.evolve(
                    status="failed",
                    execution_status="failed",
                    error=str(exc),
                )
            )
            return self._bindcraft_owned_result(
                failed,
                created=True,
                idempotent_replay=False,
            )

        status, error = _bindcraft_execution_status(outcome)
        if status != "complete":
            failed = self._put_bindcraft_record(
                pending.evolve(
                    status="failed",
                    execution_status="failed",
                    error=error,
                )
            )
            return self._bindcraft_owned_result(
                failed,
                created=True,
                idempotent_replay=False,
            )

        artifacts = getattr(outcome, "artifacts", None)
        if artifacts is None:
            failed = self._put_bindcraft_record(
                pending.evolve(
                    status="failed",
                    execution_status="failed",
                    error="bindcraft_artifacts_missing",
                )
            )
            return self._bindcraft_owned_result(
                failed,
                created=True,
                idempotent_replay=False,
            )

        located = self._with_execution_locator(pending, planned)
        assert located.output_dir is not None
        assert located.execution_id is not None
        assert located.manifest_sha256 is not None
        manifest = planned.prepared.manifest
        expected_provenance = {
            "execution_id": located.execution_id,
            "run_id": located.run_id,
            "manifest_sha256": located.manifest_sha256,
            "container_digest": manifest.container_digest,
            "tool_version": manifest.tool_version,
            "seed": manifest.seed,
            "advanced_profile_sha256": (
                claimed_inputs.advanced_profile_sha256
            ),
        }
        scientific_request = canonical_bindcraft_generation_request(
            request.generation
        )
        request_digest = scientific_request_sha256(scientific_request)
        try:
            persist_bindcraft_execution_handoff(
                Path(located.output_dir),
                artifacts,
                expected_provenance=expected_provenance,
                request_hash=located.request_hash,
                scientific_request=scientific_request,
            )
        except BindCraftHandoffError as exc:
            persist_error = str(exc)
        except Exception:  # noqa: BLE001
            persist_error = "bindcraft_handoff_persist_failed"
        else:
            persist_error = None
        if persist_error is not None:
            failed = self._put_bindcraft_record(
                located.evolve(
                    status="failed",
                    execution_status="complete",
                    scientific_status="failed",
                    error=persist_error,
                )
            )
            return self._bindcraft_owned_result(
                failed,
                created=True,
                idempotent_replay=False,
            )
        complete_execution = self._put_bindcraft_record(
            located.evolve(
                status="execution_complete",
                execution_status="complete",
                scientific_status=None,
                scientific_request_sha256=request_digest,
            )
        )
        return self._acquire_bindcraft_science(
            complete_execution,
            created=True,
            idempotent_replay=False,
        )

    def _acquire_bindcraft_science(
        self,
        execution_complete: IdempotencyRecord,
        *,
        created: bool,
        idempotent_replay: bool,
    ) -> ProteinRunStartResult:
        if (
            not execution_complete.output_dir
            or not execution_complete.execution_id
            or not execution_complete.manifest_sha256
        ):
            failed = self._put_bindcraft_record(
                execution_complete.evolve(
                    status="failed",
                    execution_status="complete",
                    scientific_status="failed",
                    error="bindcraft_execution_locator_missing",
                )
            )
            return self._bindcraft_owned_result(
                failed,
                created=created,
                idempotent_replay=idempotent_replay,
            )
        if not execution_complete.scientific_request_sha256:
            failed = self._put_bindcraft_record(
                execution_complete.evolve(
                    status="failed",
                    execution_status="complete",
                    scientific_status="failed",
                    error="bindcraft_scientific_request_integrity_missing",
                )
            )
            return self._bindcraft_owned_result(
                failed,
                created=created,
                idempotent_replay=idempotent_replay,
            )
        pending_science = execution_complete.evolve(
            status="scientific_pending",
            execution_status="complete",
            scientific_status="pending",
        )
        owned, current = self.idempotency_store.try_transition(
            execution_complete.idempotency_key,
            expected_status="execution_complete",
            record=pending_science,
        )
        if not owned:
            settled = current
            if settled.status == "scientific_pending":
                settled = (
                    self.idempotency_store.wait_until_settled(
                        execution_complete.idempotency_key
                    )
                    or settled
                )
            return self._bindcraft_replay_result(
                settled,
                execution_complete.idempotency_key,
            )
        try:
            handoff = load_bindcraft_execution_handoff(
                Path(current.output_dir or ""),
                expected_execution_id=current.execution_id or "",
                expected_run_id=current.run_id,
                expected_manifest_sha256=current.manifest_sha256 or "",
                expected_request_hash=current.request_hash,
                expected_scientific_request_sha256=(
                    current.scientific_request_sha256 or ""
                ),
            )
        except BindCraftHandoffError as exc:
            failed = self._put_bindcraft_record(
                current.evolve(
                    status="failed",
                    execution_status="complete",
                    scientific_status="failed",
                    error=str(exc),
                )
            )
            return self._bindcraft_owned_result(
                failed,
                created=created,
                idempotent_replay=idempotent_replay,
            )
        return self._run_bindcraft_science(
            handoff.scientific_request,
            current,
            handoff.artifacts,
            created=created,
            idempotent_replay=idempotent_replay,
        )

    def _run_bindcraft_science(
        self,
        scientific_request: GenerationRequest,
        owner: IdempotencyRecord,
        artifacts: BindCraftArtifacts,
        *,
        created: bool,
        idempotent_replay: bool,
    ) -> ProteinRunStartResult:
        output_dir = Path(owner.output_dir or "")
        try:
            design_run = run_bindcraft_scientific_pipeline(
                request=scientific_request,
                artifacts=artifacts,
                output_root=output_dir.parent,
                artifact_dir=self.artifact_root / owner.run_id,
                run_id=owner.run_id,
                run_state_store=self.run_state_store,
                supervisor_factory=self.supervisor_factory,
            )
        except Exception as exc:  # noqa: BLE001
            failed = self._put_bindcraft_record(
                owner.evolve(
                    status="failed",
                    execution_status="complete",
                    scientific_status="failed",
                    error=str(exc),
                )
            )
            return self._bindcraft_owned_result(
                failed,
                created=created,
                idempotent_replay=idempotent_replay,
            )

        if getattr(design_run, "stage", None) == "complete":
            completed = self._put_bindcraft_record(
                owner.evolve(
                    status="complete",
                    execution_status="complete",
                    scientific_status="complete",
                    error=None,
                )
            )
            return self._bindcraft_owned_result(
                completed,
                created=created,
                idempotent_replay=idempotent_replay,
            )

        failed = self._put_bindcraft_record(
            owner.evolve(
                status="failed",
                execution_status="complete",
                scientific_status="failed",
                error=(
                    _failed_stage_error(design_run)
                    or "bindcraft_scientific_failed"
                ),
            )
        )
        return self._bindcraft_owned_result(
            failed,
            created=created,
            idempotent_replay=idempotent_replay,
        )

    def _bindcraft_replay_result(
        self,
        settled: IdempotencyRecord,
        idempotency_key: str,
    ) -> ProteinRunStartResult:
        return ProteinRunStartResult(
            run_id=settled.run_id,
            mode=settled.mode,
            created=False,
            idempotent_replay=True,
            status=settled.status,
            idempotency_key=idempotency_key,
            error=settled.error,
            execution_status=settled.execution_status,
            scientific_status=settled.scientific_status,
        )

    def _bindcraft_owned_result(
        self,
        record: IdempotencyRecord,
        *,
        created: bool,
        idempotent_replay: bool,
    ) -> ProteinRunStartResult:
        return ProteinRunStartResult(
            run_id=record.run_id,
            mode=record.mode,
            created=created,
            idempotent_replay=idempotent_replay,
            status=record.status,
            idempotency_key=record.idempotency_key,
            error=record.error,
            execution_status=record.execution_status,
            scientific_status=record.scientific_status,
        )

    def _put_bindcraft_record(
        self,
        record: IdempotencyRecord,
    ) -> IdempotencyRecord:
        self.idempotency_store.put(record)
        return record

    def _with_execution_locator(
        self,
        record: IdempotencyRecord,
        planned: BindCraftBridgeResult,
    ) -> IdempotencyRecord:
        assert planned.authorized_request is not None
        manifest = planned.prepared.manifest
        return record.evolve(
            execution_id=manifest.execution_id,
            output_dir=str(planned.authorized_request.output_dir),
            manifest_sha256=manifest.manifest_sha256,
        )

    def start_fixture_run(
        self,
        request: ProteinFixtureRunRequest,
        idempotency_key: str,
    ) -> ProteinRunStartResult:
        if not fixture_mutations_allowed():
            return ProteinRunStartResult(
                run_id=None,
                mode="disabled",
                created=False,
                idempotent_replay=False,
                status="rejected",
                idempotency_key=idempotency_key,
                error="mutation_disabled",
            )
        if not idempotency_key.strip():
            return ProteinRunStartResult(
                run_id=None,
                mode="fixture",
                created=False,
                idempotent_replay=False,
                status="rejected",
                idempotency_key=idempotency_key,
                error="missing_idempotency_key",
            )
        if request.n_candidates < 1:
            return ProteinRunStartResult(
                run_id=None,
                mode="fixture",
                created=False,
                idempotent_replay=False,
                status="rejected",
                idempotency_key=idempotency_key,
                error="invalid_n_candidates",
            )

        request_hash = request.request_hash()
        run_id = f"protein_run_{secrets.token_hex(8)}"
        pending = IdempotencyRecord(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            run_id=run_id,
            mode="fixture",
            created_at=datetime.now(timezone.utc).isoformat(),
            status="pending",
        )
        acquired, claimed = self.idempotency_store.try_claim(pending)
        if not acquired:
            if claimed.request_hash != request_hash:
                return ProteinRunStartResult(
                    run_id=None,
                    mode="fixture",
                    created=False,
                    idempotent_replay=False,
                    status="conflict",
                    idempotency_key=idempotency_key,
                    error="idempotency_key_conflict",
                )
            settled = claimed
            if claimed.status == "pending":
                settled = (
                    self.idempotency_store.wait_until_settled(idempotency_key)
                    or claimed
                )
            return ProteinRunStartResult(
                run_id=settled.run_id,
                mode=settled.mode,
                created=False,
                idempotent_replay=True,
                status=settled.status,
                idempotency_key=idempotency_key,
            )
        generation = GenerationRequest(
            target_id=request.target_id,
            objective=request.objective,
            generator="fixture",
            n_candidates=request.n_candidates,
            constraints=dict(FIXTURE_CONSTRAINTS),
        )
        error: str | None = None
        try:
            supervisor = self.supervisor_factory(
                request=generation,
                artifact_dir=self.artifact_root / run_id,
                run_id=run_id,
                run_state_store=self.run_state_store,
            )
            design_run = supervisor.run()
        except Exception as exc:
            status = "failed"
            error = str(exc)
        else:
            status = "complete" if design_run.stage == "complete" else "failed"
            if status == "failed":
                error = _failed_stage_error(design_run)
        self.idempotency_store.put(
            IdempotencyRecord(
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                run_id=run_id,
                mode="fixture",
                created_at=pending.created_at,
                status=status,
            )
        )
        return ProteinRunStartResult(
            run_id=run_id,
            mode="fixture",
            created=True,
            idempotent_replay=False,
            status=status,
            idempotency_key=idempotency_key,
            error=error,
        )


def _bindcraft_execution_status(
    outcome: object,
) -> tuple[str, str | None]:
    result = getattr(outcome, "result", None)
    status = getattr(result, "status", None)
    if status == "complete":
        artifacts = getattr(outcome, "artifacts", None)
        if artifacts is None:
            return "failed", "bindcraft_artifacts_missing"
        return _bindcraft_physical_output_status(artifacts)
    if status == "failed":
        error = getattr(result, "error", None)
        return "failed", str(error) if error else "bindcraft_runtime_failed"
    return "failed", "bindcraft_execution_incomplete"


def _bindcraft_physical_output_status(
    artifacts: object,
) -> tuple[str, str | None]:
    stats_csv = getattr(artifacts, "stats_csv", None)
    output_dir = getattr(artifacts, "output_dir", None)
    if stats_csv is None or not Path(stats_csv).is_file():
        return "failed", "bindcraft_stats_missing"
    if output_dir is None:
        return "failed", "bindcraft_stats_missing"
    try:
        Path(stats_csv).resolve().relative_to(
            Path(output_dir).resolve()
        )
    except ValueError:
        return "failed", "bindcraft_stats_outside_output"
    return "complete", None


def _failed_stage_error(design_run: object) -> str | None:
    for name in (
        "generation",
        "scoring",
        "ranking",
        "critique",
        "prioritization",
    ):
        stage = getattr(design_run, name, None)
        if getattr(stage, "status", None) == "failed":
            message = getattr(stage, "error", None)
            if message:
                return str(message)
    return None
