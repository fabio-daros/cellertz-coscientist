from pathlib import Path

from cellertz.domains.protein_design.agents.generation import GenerationRequest
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    BindCraftRealExecutionBridge,
    developer_bindcraft_authorization,
)


class RecordingGenerator:
    def __init__(self) -> None:
        self.calls = 0
        self.last_request = None

    def generate(self, request):
        self.calls += 1
        self.last_request = request
        return "generated"


def _generation() -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator="bindcraft",
        n_candidates=1,
        constraints={
            "length_min": 50,
            "length_max": 60,
            "seed": 0,
        },
    )


def _request(tmp_path: Path) -> BindCraftExecutionRequest:
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
    checkpoint = tmp_path / "params.bin"
    bindcraft_bin = tmp_path / "bindcraft"

    target.write_text("ATOM\n")
    settings.write_text("{}\n")
    checkpoint.write_bytes(b"ckpt")
    bindcraft_bin.write_text("#!/bin/sh\n")

    return BindCraftExecutionRequest(
        generation=_generation(),
        run_id="protein_run_authorized_handoff",
        execution_id="exec_authorized_handoff",
        target_path=target,
        output_root=tmp_path / "out",
        bindcraft_bin=bindcraft_bin,
        settings_path=settings,
        checkpoint_path=checkpoint,
        tool_version="bindcraft-test",
        seed=0,
        gpu_required=True,
    )


def test_enabled_generation_receives_full_authorized_request(tmp_path):
    request = _request(tmp_path)
    bridge = BindCraftRealExecutionBridge()

    result = bridge.plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.preflight is not None
    assert result.preflight.status == "ready"
    assert result.authorized_request is not None

    generator = RecordingGenerator()

    returned = bridge.generate(
        result,
        generator,
        enable_generation=True,
    )

    assert returned == "generated"
    assert generator.calls == 1
    assert generator.last_request is result.authorized_request
    assert generator.last_request.execution_request is request
    assert generator.last_request.manifest is result.prepared.manifest
    assert (
        generator.last_request.output_dir
        == result.authorized_request.output_dir
    )
