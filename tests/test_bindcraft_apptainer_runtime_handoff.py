import hashlib
import json
from pathlib import Path

from cellertz.domains.protein_design.adapters.bindcraft import (
    BindCraftAdapter,
    BindCraftParser,
    ExistingBindCraftArtifactsExecutor,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    ProteinGenerationAgent,
)
from cellertz.domains.protein_design.agents.supervisor import (
    ProteinSupervisorV1,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    ApptainerBindCraftExecution,
    ApptainerBindCraftGenerator,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    BindCraftRealExecutionBridge,
    developer_bindcraft_authorization,
)
from cellertz.runtime import ExecutionRequest, ExecutionResult


class RecordingRuntime:
    name = "recording"
    version = "recording_v1"

    def __init__(
        self,
        *,
        status: str = "complete",
    ) -> None:
        self.calls = 0
        self.last_request = None
        self.status = status

    def execute(
        self,
        request: ExecutionRequest,
    ) -> ExecutionResult:
        self.calls += 1
        self.last_request = request

        return ExecutionResult(
            status=self.status,
            return_code=0 if self.status == "complete" else 2,
            stdout="fake stdout",
            stderr="" if self.status == "complete" else "fake failure",
            runtime_name=self.name,
            runtime_version=self.version,
            started_at="test-start",
            completed_at="test-end",
            error=None if self.status == "complete" else "fake failure",
        )


def _bridge_result(
    tmp_path: Path,
    *,
    target_id: str = "BCMA",
    length_min: int = 50,
    length_max: int = 60,
):
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
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
                "binder_name": target_id,
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
        "fake-apptainer\n",
        encoding="utf-8",
    )

    container_bytes = b"fake-sif"
    container.write_bytes(container_bytes)

    request = BindCraftExecutionRequest(
        generation=GenerationRequest(
            target_id=target_id,
            objective="binder_design",
            generator="bindcraft",
            n_candidates=1,
            constraints={
                "length_min": length_min,
                "length_max": length_max,
            },
        ),
        run_id="protein_run_runtime_handoff",
        execution_id="exec_runtime_handoff",
        target_path=target,
        output_root=tmp_path / "out",
        apptainer_bin=apptainer,
        settings_path=settings,
        container_image=str(container),
        container_digest=hashlib.sha256(
            container_bytes
        ).hexdigest(),
        tool_version="bindcraft-test",
        seed=7,
        gpu_required=True,
    )

    result = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.preflight is not None
    assert result.preflight.status == "ready"
    assert result.authorized_request is not None

    return result


def test_apptainer_generator_hands_plan_to_runtime(
    tmp_path,
):
    bridge_result = _bridge_result(tmp_path)

    runtime = RecordingRuntime()

    generator = ApptainerBindCraftGenerator(
        runtime=runtime,
        timeout_seconds=123,
    )

    returned = BindCraftRealExecutionBridge().generate(
        bridge_result,
        generator,
        enable_generation=True,
    )

    assert isinstance(
        returned,
        ApptainerBindCraftExecution,
    )

    assert runtime.calls == 1
    assert runtime.last_request is not None

    assert (
        runtime.last_request.argv
        == returned.plan.argv
    )

    assert (
        runtime.last_request.timeout_seconds
        == 123
    )

    assert runtime.last_request.cwd is None
    assert runtime.last_request.env is None

    assert (
        returned.execution_request
        is runtime.last_request
    )

    assert returned.result.status == "complete"
    assert returned.result.return_code == 0


def test_apptainer_generator_preserves_runtime_failure(
    tmp_path,
):
    bridge_result = _bridge_result(tmp_path)

    runtime = RecordingRuntime(
        status="failed",
    )

    generator = ApptainerBindCraftGenerator(
        runtime=runtime,
        timeout_seconds=456,
    )

    returned = BindCraftRealExecutionBridge().generate(
        bridge_result,
        generator,
        enable_generation=True,
    )

    assert runtime.calls == 1

    assert returned.result.status == "failed"
    assert returned.result.return_code == 2
    assert returned.result.error == "fake failure"

    assert (
        returned.execution_request.timeout_seconds
        == 456
    )


def test_successful_apptainer_execution_exposes_bindcraft_artifacts(
    tmp_path,
):
    planned = _bridge_result(tmp_path)

    runtime = RecordingRuntime()

    generator = ApptainerBindCraftGenerator(
        runtime=runtime,
        timeout_seconds=123,
    )

    execution = BindCraftRealExecutionBridge().generate(
        planned,
        generator,
        enable_generation=True,
    )

    assert execution.result.status == "complete"
    assert execution.artifacts is not None

    assert (
        execution.artifacts.output_dir
        == execution.plan.bindcraft_output_dir
    )

    assert execution.artifacts.stats_csv == (
        execution.plan.bindcraft_output_dir
        / "final_design_stats.csv"
    )

    assert execution.artifacts.command == execution.plan.argv

    assert execution.artifacts.bindcraft_version == (
        planned.authorized_request.manifest.tool_version
    )

    assert execution.artifacts.returncode == 0


def test_real_gpu3_output_traverses_execution_artifacts_and_parser(
    tmp_path,
):
    planned = _bridge_result(
        tmp_path,
        target_id="PDL1",
        length_min=65,
        length_max=65,
    )

    runtime = RecordingRuntime()

    generator = ApptainerBindCraftGenerator(
        runtime=runtime,
        timeout_seconds=123,
    )

    execution = BindCraftRealExecutionBridge().generate(
        planned,
        generator,
        enable_generation=True,
    )

    assert execution.result.status == "complete"
    assert execution.artifacts is not None

    real_fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "bindcraft_gpu3"
        / "final_design_stats.csv"
    )

    execution.artifacts.stats_csv.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    execution.artifacts.stats_csv.write_bytes(
        real_fixture.read_bytes()
    )

    candidates = BindCraftParser().parse(
        planned.authorized_request.generation,
        execution.artifacts,
    )

    assert len(candidates) == 1

    candidate = candidates[0]

    assert candidate.candidate_id == (
        "bindcraft_PDL1_l65_s985772_mpnn1"
    )

    assert candidate.target_id == "PDL1"
    assert candidate.generator == "bindcraft"

    assert candidate.sequence == (
        "SSLFEKIVKWAMEFTVAMKEYMEKNPELTDEQIRTIQKEVM"
        "RRFEAKDFGDPRKLIEEALAKGPP"
    )

    metrics = candidate.generator_metadata["native_metrics"]

    assert metrics["Average_pLDDT"] == "0.83"
    assert metrics["Average_pTM"] == "0.79"
    assert metrics["Average_i_pTM"] == "0.61"
    assert metrics["Average_i_pAE"] == "0.36"


def test_real_gpu3_artifacts_become_generation_result(
    tmp_path,
):
    planned = _bridge_result(
        tmp_path,
        target_id="PDL1",
        length_min=65,
        length_max=65,
    )

    execution = BindCraftRealExecutionBridge().generate(
        planned,
        ApptainerBindCraftGenerator(
            runtime=RecordingRuntime(),
            timeout_seconds=123,
        ),
        enable_generation=True,
    )

    assert execution.artifacts is not None

    real_fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "bindcraft_gpu3"
        / "final_design_stats.csv"
    )

    execution.artifacts.stats_csv.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    execution.artifacts.stats_csv.write_bytes(
        real_fixture.read_bytes()
    )

    adapter = BindCraftAdapter(
        executor=ExistingBindCraftArtifactsExecutor(
            execution.artifacts
        )
    )

    agent = ProteinGenerationAgent(
        adapters={"bindcraft": adapter}
    )

    result = agent.generate(
        planned.authorized_request.generation
    )

    assert result.status == "complete"
    assert result.adapter_name == "bindcraft"
    assert len(result.candidates) == 1

    candidate = result.candidates[0]

    assert candidate.candidate_id == (
        "bindcraft_PDL1_l65_s985772_mpnn1"
    )
    assert candidate.target_id == "PDL1"
    assert candidate.generator == "bindcraft"

    provenance = candidate.generator_metadata["provenance"]
    execution_provenance = provenance["execution"]

    manifest = planned.authorized_request.manifest

    assert execution_provenance["execution_id"] == (
        manifest.execution_id
    )
    assert execution_provenance["run_id"] == manifest.run_id
    assert execution_provenance["manifest_sha256"] == (
        manifest.manifest_sha256
    )
    assert execution_provenance["container_digest"] == (
        manifest.container_digest
    )
    assert execution_provenance["runtime_name"] == "recording"
    assert execution_provenance["runtime_version"] == (
        "recording_v1"
    )
    assert execution_provenance["started_at"] == "test-start"
    assert execution_provenance["completed_at"] == "test-end"


def test_real_gpu3_candidate_traverses_scientific_supervisor(
    tmp_path,
):
    planned = _bridge_result(
        tmp_path,
        target_id="PDL1",
        length_min=65,
        length_max=65,
    )

    execution = BindCraftRealExecutionBridge().generate(
        planned,
        ApptainerBindCraftGenerator(
            runtime=RecordingRuntime(),
            timeout_seconds=123,
        ),
        enable_generation=True,
    )

    assert execution.artifacts is not None

    real_fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "bindcraft_gpu3"
        / "final_design_stats.csv"
    )

    execution.artifacts.stats_csv.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    execution.artifacts.stats_csv.write_bytes(
        real_fixture.read_bytes()
    )

    generation_agent = ProteinGenerationAgent(
        adapters={
            "bindcraft": BindCraftAdapter(
                executor=ExistingBindCraftArtifactsExecutor(
                    execution.artifacts
                )
            )
        }
    )

    manifest = planned.authorized_request.manifest

    supervisor = ProteinSupervisorV1(
        request=planned.authorized_request.generation,
        artifact_dir=tmp_path / "supervisor_run",
        run_id=manifest.run_id,
        generation_agent=generation_agent,
    )

    run = supervisor.run()

    assert run.generation.status == "complete"
    assert run.scoring.status == "complete"
    assert run.ranking.status == "complete"
    assert run.critique.status == "complete"
    assert run.prioritization.status == "complete"

    generation = supervisor.generation_result

    assert generation is not None
    assert generation.status == "complete"
    assert len(generation.candidates) == 1

    candidate = generation.candidates[0]

    assert candidate.candidate_id == (
        "bindcraft_PDL1_l65_s985772_mpnn1"
    )

    provenance = candidate.generator_metadata["provenance"]
    execution_provenance = provenance["execution"]

    assert execution_provenance["execution_id"] == (
        manifest.execution_id
    )
    assert execution_provenance["manifest_sha256"] == (
        manifest.manifest_sha256
    )


def test_apptainer_artifacts_preserve_advanced_profile_digest(
    tmp_path,
):
    bridge_result = _bridge_result(tmp_path)

    execution = BindCraftRealExecutionBridge().generate(
        bridge_result,
        ApptainerBindCraftGenerator(
            runtime=RecordingRuntime(),
        ),
        enable_generation=True,
    )

    assert execution.artifacts is not None

    provenance = execution.artifacts.execution_provenance

    assert provenance["advanced_profile_sha256"] == (
        execution.plan.advanced_sha256
    )
