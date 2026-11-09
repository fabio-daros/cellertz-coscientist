import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    advanced_profile_identity_sha256,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    file_sha256,
)
from cellertz.domains.protein_design.execution.bindcraft_snapshot import (
    BindCraftInputDrift,
    capture_claimed_input_digests,
    snapshot_claimed_bindcraft_inputs,
)


def _safe_profile(path: Path, **overrides) -> Path:
    payload = {
        "design_algorithm": "4stage",
        "max_trajectories": 1,
        "num_seqs": 1,
        "max_mpnn_sequences": 1,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _request(tmp_path: Path, *, checkpoint: bool = False):
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
    container = tmp_path / "bindcraft.sif"
    target.write_text("ATOM\n", encoding="utf-8")
    settings.write_text("{}\n", encoding="utf-8")
    container.write_bytes(b"fake-sif")
    checkpoint_path = None
    if checkpoint:
        checkpoint_path = tmp_path / "checkpoint.bin"
        checkpoint_path.write_bytes(b"checkpoint-v1")
    return BindCraftExecutionRequest(
        generation=GenerationRequest(
            target_id="PDL1",
            objective="binder_design",
            generator="bindcraft",
            n_candidates=1,
            constraints={"length_min": 65, "length_max": 65},
        ),
        run_id="protein_run_snapshot",
        execution_id="exec_snapshot",
        target_path=target,
        output_root=tmp_path / "out",
        settings_path=settings,
        checkpoint_path=checkpoint_path,
        container_image=str(container),
        container_digest=hashlib.sha256(b"fake-sif").hexdigest(),
        tool_version="bindcraft-test",
        seed=7,
    )


def test_snapshot_copies_claimed_bytes_not_later_source(
    tmp_path,
):
    profile = _safe_profile(tmp_path / "profile.json")
    request = _request(tmp_path, checkpoint=True)
    claimed = capture_claimed_input_digests(
        request,
        advanced_profile_sha256=advanced_profile_identity_sha256(
            profile
        ),
    )
    snapshot = snapshot_claimed_bindcraft_inputs(
        request,
        claimed,
        advanced_profile_source=profile,
        snapshot_dir=tmp_path / "snapshot",
        execution_id="exec_snapshot",
    )

    assert snapshot.execution_request.target_path != request.target_path
    assert snapshot.execution_request.settings_path != request.settings_path
    assert snapshot.execution_request.checkpoint_path != (
        request.checkpoint_path
    )
    assert file_sha256(snapshot.execution_request.target_path) == (
        claimed.target_sha256
    )
    assert file_sha256(snapshot.advanced_profile_path) == (
        claimed.advanced_profile_sha256
    )
    assert snapshot.execution_request.container_image != (
        request.container_image
    )
    assert file_sha256(
        Path(snapshot.execution_request.container_image)
    ) == claimed.container_digest


def test_snapshot_rejects_mutated_target(tmp_path):
    profile = _safe_profile(tmp_path / "profile.json")
    request = _request(tmp_path)
    claimed = capture_claimed_input_digests(
        request,
        advanced_profile_sha256=advanced_profile_identity_sha256(
            profile
        ),
    )
    assert request.target_path is not None
    request.target_path.write_text("ATOM\nMUTATED\n", encoding="utf-8")
    with pytest.raises(BindCraftInputDrift) as exc:
        snapshot_claimed_bindcraft_inputs(
            request,
            claimed,
            advanced_profile_source=profile,
            snapshot_dir=tmp_path / "snapshot",
            execution_id="exec_snapshot",
        )
    assert exc.value.input_name == "target"


def test_snapshot_rejects_mutated_settings(tmp_path):
    profile = _safe_profile(tmp_path / "profile.json")
    request = _request(tmp_path)
    claimed = capture_claimed_input_digests(
        request,
        advanced_profile_sha256=advanced_profile_identity_sha256(
            profile
        ),
    )
    assert request.settings_path is not None
    request.settings_path.write_text('{"chains":"B"}\n', encoding="utf-8")
    with pytest.raises(BindCraftInputDrift) as exc:
        snapshot_claimed_bindcraft_inputs(
            request,
            claimed,
            advanced_profile_source=profile,
            snapshot_dir=tmp_path / "snapshot",
            execution_id="exec_snapshot",
        )
    assert exc.value.input_name == "settings"


def test_snapshot_rejects_mutated_checkpoint(tmp_path):
    profile = _safe_profile(tmp_path / "profile.json")
    request = _request(tmp_path, checkpoint=True)
    claimed = capture_claimed_input_digests(
        request,
        advanced_profile_sha256=advanced_profile_identity_sha256(
            profile
        ),
    )
    assert request.checkpoint_path is not None
    request.checkpoint_path.write_bytes(b"checkpoint-v2")
    with pytest.raises(BindCraftInputDrift) as exc:
        snapshot_claimed_bindcraft_inputs(
            request,
            claimed,
            advanced_profile_source=profile,
            snapshot_dir=tmp_path / "snapshot",
            execution_id="exec_snapshot",
        )
    assert exc.value.input_name == "checkpoint"


def test_snapshot_rejects_mutated_advanced_profile(tmp_path):
    profile = _safe_profile(tmp_path / "profile.json")
    request = _request(tmp_path)
    claimed = capture_claimed_input_digests(
        request,
        advanced_profile_sha256=advanced_profile_identity_sha256(
            profile
        ),
    )
    _safe_profile(profile, extra="after-claim")
    with pytest.raises(BindCraftInputDrift) as exc:
        snapshot_claimed_bindcraft_inputs(
            request,
            claimed,
            advanced_profile_source=profile,
            snapshot_dir=tmp_path / "snapshot",
            execution_id="exec_snapshot",
        )
    assert exc.value.input_name == "advanced_profile"


def test_snapshot_rejects_container_digest_mismatch(tmp_path):
    profile = _safe_profile(tmp_path / "profile.json")
    request = _request(tmp_path)
    claimed = capture_claimed_input_digests(
        request,
        advanced_profile_sha256=advanced_profile_identity_sha256(
            profile
        ),
    )
    Path(request.container_image).write_bytes(b"other-sif")
    with pytest.raises(BindCraftInputDrift) as exc:
        snapshot_claimed_bindcraft_inputs(
            request,
            claimed,
            advanced_profile_source=profile,
            snapshot_dir=tmp_path / "snapshot",
            execution_id="exec_snapshot",
        )
    assert exc.value.input_name == "container"


def test_snapshot_rejects_caller_digest_disagreeing_with_bytes(
    tmp_path,
):
    profile = _safe_profile(tmp_path / "profile.json")
    request = replace(_request(tmp_path), container_digest="c" * 64)
    claimed = capture_claimed_input_digests(
        request,
        advanced_profile_sha256=advanced_profile_identity_sha256(
            profile
        ),
    )
    with pytest.raises(BindCraftInputDrift) as exc:
        snapshot_claimed_bindcraft_inputs(
            request,
            claimed,
            advanced_profile_source=profile,
            snapshot_dir=tmp_path / "snapshot",
            execution_id="exec_snapshot",
        )
    assert exc.value.input_name == "container"


def test_snapshot_profile_sha_matches_canonical_identity(tmp_path):
    profile = _safe_profile(tmp_path / "profile.json", extra="x")
    request = _request(tmp_path)
    claimed_sha = advanced_profile_identity_sha256(profile)
    claimed = capture_claimed_input_digests(
        request,
        advanced_profile_sha256=claimed_sha,
    )
    snapshot = snapshot_claimed_bindcraft_inputs(
        request,
        claimed,
        advanced_profile_source=profile,
        snapshot_dir=tmp_path / "snapshot",
        execution_id="exec_snapshot",
    )
    assert file_sha256(snapshot.advanced_profile_path) == claimed_sha
    assert claimed_sha != hashlib.sha256(profile.read_bytes()).hexdigest()


def test_snapshot_container_is_independent_of_later_source_mutation(
    tmp_path,
):
    profile = _safe_profile(tmp_path / "profile.json")
    request = _request(tmp_path)
    claimed = capture_claimed_input_digests(
        request,
        advanced_profile_sha256=advanced_profile_identity_sha256(
            profile
        ),
    )
    snapshot = snapshot_claimed_bindcraft_inputs(
        request,
        claimed,
        advanced_profile_source=profile,
        snapshot_dir=tmp_path / "snapshot",
        execution_id="exec_snapshot",
    )
    snapshot_container = Path(
        snapshot.execution_request.container_image
    )
    Path(request.container_image).write_bytes(b"other-sif")

    assert file_sha256(snapshot_container) == claimed.container_digest
    assert file_sha256(Path(request.container_image)) != (
        claimed.container_digest
    )
    assert snapshot_container.resolve() != (
        Path(request.container_image).resolve()
    )
