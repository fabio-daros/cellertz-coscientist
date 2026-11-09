import hashlib
import json
from pathlib import Path

from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    DEFAULT_SMOKE_ADVANCED_PROFILE,
    ApptainerBindCraftGenerator,
    advanced_profile_identity_sha256,
    build_apptainer_plan,
    materialize_advanced_profile,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    BindCraftRealExecutionBridge,
    developer_bindcraft_authorization,
)
from cellertz.runs.starter import bindcraft_run_request_hash


def _safe_profile_payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "design_algorithm": "4stage",
        "max_trajectories": 1,
        "num_seqs": 1,
        "max_mpnn_sequences": 1,
    }
    payload.update(overrides)
    return payload


def _write_profile(
    path: Path,
    payload: dict[str, object] | None = None,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
) -> Path:
    document = payload if payload is not None else _safe_profile_payload()
    path.write_text(
        json.dumps(
            document,
            indent=indent,
            sort_keys=sort_keys,
        ),
        encoding="utf-8",
    )
    return path


def _authorized(tmp_path: Path):
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
    apptainer = tmp_path / "apptainer"
    container = tmp_path / "bindcraft.sif"

    target.write_text("ATOM\n", encoding="utf-8")
    settings.write_text(
        json.dumps(
            {
                "chains": "A",
                "lengths": [65, 65],
            }
        ),
        encoding="utf-8",
    )
    apptainer.write_text("apptainer\n", encoding="utf-8")
    container.write_bytes(b"sif")

    request = BindCraftExecutionRequest(
        generation=GenerationRequest(
            target_id="PDL1",
            objective="binder_design",
            generator="bindcraft",
            n_candidates=1,
            constraints={
                "length_min": 65,
                "length_max": 65,
            },
        ),
        run_id="protein_run_profile_identity",
        execution_id="exec_profile_identity",
        target_path=target,
        output_root=tmp_path / "scientific_execution",
        apptainer_bin=apptainer,
        settings_path=settings,
        container_image=str(container),
        container_digest=hashlib.sha256(b"sif").hexdigest(),
        tool_version="bindcraft-test",
        seed=7,
        gpu_required=True,
    )
    planned = BindCraftRealExecutionBridge().plan(
        request,
        developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert planned.authorized_request is not None
    return planned.authorized_request, request


def test_custom_profile_pure_digest_matches_materialized_digest(tmp_path):
    profile = _write_profile(
        tmp_path / "custom.json",
        indent=None,
        sort_keys=False,
    )

    identity = advanced_profile_identity_sha256(profile)
    _path, materialized = materialize_advanced_profile(
        profile,
        tmp_path / "runtime",
    )

    assert identity == materialized
    assert identity != hashlib.sha256(profile.read_bytes()).hexdigest()


def test_default_profile_pure_digest_matches_materialized_digest(tmp_path):
    identity = advanced_profile_identity_sha256()
    _path, materialized = materialize_advanced_profile(
        DEFAULT_SMOKE_ADVANCED_PROFILE,
        tmp_path / "runtime",
    )

    raw_source = hashlib.sha256(
        DEFAULT_SMOKE_ADVANCED_PROFILE.read_bytes()
    ).hexdigest()

    assert identity == materialized
    assert identity != raw_source


def test_pure_digest_creates_no_runtime_or_output_state(tmp_path):
    profile = _write_profile(tmp_path / "source.json")
    before = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")}

    advanced_profile_identity_sha256(profile)
    advanced_profile_identity_sha256()

    after = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")}

    assert before == after
    assert not (tmp_path / "runtime").exists()
    assert not (tmp_path / "scientific_execution").exists()
    assert not list(tmp_path.glob("**/.cellertz_runtime"))


def test_equivalent_json_formatting_shares_canonical_digest(tmp_path):
    compact = _write_profile(
        tmp_path / "compact.json",
        _safe_profile_payload(extra="same"),
        indent=None,
        sort_keys=False,
    )
    pretty = _write_profile(
        tmp_path / "pretty.json",
        {
            "extra": "same",
            "num_seqs": 1,
            "max_mpnn_sequences": 1,
            "design_algorithm": "4stage",
            "max_trajectories": 1,
        },
        indent=4,
        sort_keys=True,
    )

    assert advanced_profile_identity_sha256(compact) == (
        advanced_profile_identity_sha256(pretty)
    )


def test_unsafe_smoke_profile_fails_identity_and_materialization(tmp_path):
    unsafe = _write_profile(
        tmp_path / "unsafe.json",
        _safe_profile_payload(max_trajectories=8),
    )

    try:
        advanced_profile_identity_sha256(unsafe)
    except ValueError as exc:
        identity_error = str(exc)
    else:
        raise AssertionError("unsafe identity must fail closed")

    try:
        materialize_advanced_profile(unsafe, tmp_path / "runtime")
    except ValueError as exc:
        materialize_error = str(exc)
    else:
        raise AssertionError("unsafe materialization must fail closed")

    assert identity_error == materialize_error
    assert "max_trajectories" in identity_error
    assert not (tmp_path / "runtime").exists()


def test_pure_digest_matches_plan_and_would_match_provenance(tmp_path):
    profile = _write_profile(
        tmp_path / "custom.json",
        indent=None,
    )
    authorized, _request = _authorized(tmp_path)
    identity = advanced_profile_identity_sha256(profile)
    plan = build_apptainer_plan(
        authorized,
        advanced_profile_path=profile,
    )

    assert plan.advanced_sha256 == identity
    assert {
        "advanced_profile_sha256": plan.advanced_sha256,
    }["advanced_profile_sha256"] == identity


def test_generator_identity_matches_materialized_and_plan_digest(tmp_path):
    profile = _write_profile(tmp_path / "custom.json")
    authorized, _request = _authorized(tmp_path)
    generator = ApptainerBindCraftGenerator(
        advanced_profile_path=profile,
    )

    identity = generator.effective_advanced_profile_sha256()
    _path, materialized = materialize_advanced_profile(
        profile,
        tmp_path / "runtime",
    )
    plan = build_apptainer_plan(
        authorized,
        advanced_profile_path=profile,
    )

    assert identity == materialized
    assert identity == plan.advanced_sha256


def test_scientific_request_hash_uses_canonical_not_raw_profile(
    tmp_path,
):
    profile = _write_profile(
        tmp_path / "custom.json",
        indent=None,
        sort_keys=False,
    )
    _authorized_request, request = _authorized(tmp_path)
    canonical = advanced_profile_identity_sha256(profile)
    raw = hashlib.sha256(profile.read_bytes()).hexdigest()

    assert canonical != raw
    assert bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=canonical,
    ) != bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=raw,
    )
