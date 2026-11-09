import hashlib
from pathlib import Path

from cellertz.domains.protein_design.agents.generation import GenerationRequest
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    BindCraftRealExecutionBridge,
    developer_bindcraft_authorization,
    file_sha256,
)


def test_file_sha256_streams_without_read_bytes(tmp_path, monkeypatch):
    path = tmp_path / "artifact.bin"
    payload = b"cellertz-bindcraft-" * 65536
    path.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()

    def forbidden_read_bytes(self):
        raise AssertionError("file_sha256 must stream large files")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    assert file_sha256(path) == expected

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


def _container_request(tmp_path: Path) -> BindCraftExecutionRequest:
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
    sif = tmp_path / "bindcraft.sif"
    apptainer = tmp_path / "apptainer"

    target.write_text("ATOM\n")
    settings.write_text("{}\n")
    sif.write_bytes(b"fake-sif")
    apptainer.write_text("#!/bin/sh\n")

    return BindCraftExecutionRequest(
        generation=_generation(),
        run_id="protein_run_apptainer",
        execution_id="exec_apptainer_test",
        target_path=target,
        output_root=tmp_path / "out",
        settings_path=settings,
        container_image=str(sif),
        container_digest=file_sha256(sif),
        tool_version="bindcraft-test",
        seed=0,
        gpu_required=True,
        apptainer_bin=apptainer,
    )


def test_container_preflight_ready(tmp_path):
    request = _container_request(tmp_path)

    result = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.preflight is not None
    assert result.preflight.status == "ready"
    assert result.preflight.execution_attempted is False

    requirements = result.preflight.requirements
    assert requirements["tool_available"] is True
    assert requirements["container_available"] is True
    assert requirements["container_digest_matches"] is True


def test_container_preflight_rejects_missing_sif(tmp_path):
    request = _container_request(tmp_path)
    Path(request.container_image).unlink()

    result = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.preflight is not None
    assert result.preflight.status == "not_ready"
    assert result.preflight.requirements["container_available"] is False

def test_container_preflight_rejects_missing_settings(tmp_path):
    request = _container_request(tmp_path)
    request.settings_path.unlink()

    result = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.preflight is not None
    assert result.preflight.status == "not_ready"
    assert result.preflight.requirements["settings_available"] is False

def test_container_preflight_rejects_wrong_digest(tmp_path):
    request = _container_request(tmp_path)

    request = BindCraftExecutionRequest(
        **{
            **request.__dict__,
            "container_digest": "0" * 64,
        }
    )

    result = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.preflight is not None
    assert result.preflight.status == "not_ready"
    assert result.preflight.requirements["container_digest_matches"] is False


def test_container_preflight_rejects_missing_apptainer(tmp_path):
    request = _container_request(tmp_path)
    request.apptainer_bin.unlink()

    result = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.preflight is not None
    assert result.preflight.status == "not_ready"
    assert result.preflight.requirements["tool_available"] is False
