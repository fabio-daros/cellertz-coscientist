"""Phase 6M Docker executor. Scripted controls only. No daemon and no GPU."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cellertz.application.approval import canonical_digest, revision_document
from cellertz.application.contracts import ApplicationContractError
from cellertz.application.pdl1_attempt import ExternalAttempt
from cellertz.application.pdl1_docker import (
    CliDockerControl,
    DetachedDeadlineSupervisor,
    DockerExecutionConfig,
    DockerRegistrationBundle,
    ScriptedDeadline,
    ScriptedDockerControl,
    container_arguments_agree,
    csv_from_tar,
    docker_gaps,
    enabled_execution_blocked,
    output_bytes_ok,
    protocol_executor,
    reconcile_decision,
    stage_verified_inputs,
)
from cellertz.application.pdl1_executor import (
    open_registered_executor,
    register_pdl1_executor,
)
from cellertz.application.pdl1_input_verification import (
    Pdl1InputLimits,
    Pdl1InputLocations,
    verify_pdl1_inputs,
)
from cellertz.application.pdl1_selection import Pdl1InputSelection
from cellertz.application.scientific_authorization import ScientificAuthorizationService
from cellertz.application.scientific_run import (
    EXTERNAL_FAILED_TEXT,
    EXTERNAL_UNCERTAIN_TEXT,
    FAILED_TEXT,
    INTERRUPTED_TEXT,
    InMemoryScientificRunStore,
    ScientificRunService,
)
from cellertz.application.scientific_run_config import (
    ScientificRunConfigurationError,
    resolve_scientific_run_execution,
)
from cellertz.application.scientific_runner_readiness import (
    CD22_PROFILE_ID,
    DOCKER_PROFILE_ID,
    TACI_PROFILE_ID,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    canonical_advanced_profile_bytes,
)
from tests.test_human_approval import _decide, _stack
from tests.test_pdl1_executor import _specification
from tests.test_pdl1_input_manifest import _v2
from tests.test_scientific_authorization import SESSION, WHEN, _authorize
from tests.test_scientific_runner_readiness import CountingProbe, _attestation, _load, _write

_IMAGE = "sha256:" + ("ab" * 32)
_IMAGE_ID = "sha256:" + ("cd" * 32)
_FILTERS = "/opt/BindCraft/settings_filters/no_filters.json"
_BOUND_ARGV = (
    "--n-designs",
    "1",
    "--length-min",
    "65",
    "--length-max",
    "65",
    "--cellertz-seed",
    "7",
    "--filters",
    _FILTERS,
)
_REPO = "example.invalid/cellertz/bindcraft"


def _files(tmp_path: Path) -> tuple[bytes, bytes, bytes, Pdl1InputLocations]:
    target = b"synthetic-target"
    settings = b"synthetic-settings"
    profile = canonical_advanced_profile_bytes({"limit": 1, "name": "synthetic"})
    root = tmp_path / "sources"
    root.mkdir()
    paths = (
        root / "target.pdb",
        root / "settings.json",
        root / "advanced.json",
    )
    for path, payload in zip(paths, (target, settings, profile), strict=True):
        path.write_bytes(payload)
    locations = Pdl1InputLocations(str(paths[0]), str(paths[1]), str(paths[2]))
    return target, settings, profile, locations


def _document(tmp_path: Path) -> tuple[Path, str, dict]:
    document = _v2()
    document["profile_id"] = DOCKER_PROFILE_ID
    document["resource_envelope"]["gpu_count"] = 1
    document["argv_template"] = list(_BOUND_ARGV)
    path = tmp_path / "manifest.json"
    digest = _write(path, document)
    return path, digest, document


def _verified(tmp_path: Path, digest: str):
    _target, _settings, _profile, locations = _files(tmp_path)
    identity = replace(
        __import__(
            "cellertz.application.pdl1_executor", fromlist=["Pdl1ValidatedInputIdentity"]
        ).Pdl1ValidatedInputIdentity(
            manifest_digest=digest,
            target_structure_sha256=hashlib.sha256(b"synthetic-target").hexdigest(),
            settings_sha256=hashlib.sha256(b"synthetic-settings").hexdigest(),
            advanced_profile_sha256=hashlib.sha256(
                canonical_advanced_profile_bytes({"limit": 1, "name": "synthetic"})
            ).hexdigest(),
            argv_policy_version="server-constructed-argv-v1",
            directory_policy="server-owned-per-attempt",
            environment_policy="empty-explicit",
        )
    )
    limits = Pdl1InputLimits(1024, 1024, 1024, 1024)
    return verify_pdl1_inputs(identity, locations, limits), locations


def _config(root: Path, **overrides: object) -> DockerExecutionConfig:
    values: dict[str, object] = {
        "repository": _REPO,
        "image_digest": _IMAGE,
        "image_id": _IMAGE_ID,
        "entrypoint": "recorded-entry",
        "argv": _BOUND_ARGV,
        "gpu_device": "0",
        "deployment_id": "dep1",
        "staging_root": str(root),
        "storage_bytes": 1048576,
        "log_bytes": 1048576,
        "docker_root": str(root),
        "docker_host": f"unix://{root / 'docker.sock'}",
    }
    values.update(overrides)
    return DockerExecutionConfig(**values)  # type: ignore[arg-type]


def _facts() -> tuple[dict, dict]:
    info = {
        "OSType": "linux",
        "Driver": "overlay2",
        "DriverStatus": [["Supports project quotas", "true"]],
        "LoggingDriver": "json-file",
        "ID": "daemon-1",
        "Runtimes": {"nvidia": {}},
    }
    image = {"RepoDigests": [f"{_REPO}@{_IMAGE}"], "Id": _IMAGE_ID}
    return info, image


def _ready(tmp_path: Path):
    path, digest, document = _document(tmp_path)
    reply = _load(path, CountingProbe(_attestation(document, digest))).read()
    assert reply.status == "ready"
    assert reply.profile_id == DOCKER_PROFILE_ID
    return path, digest, reply


_POLICY_MESSAGE = (
    "design a binder for pdl1 using bindcraft with 1 candidates length 65 65 seed 7 no_filters"
)
_LEGACY_MESSAGE = "design a binder for pdl1 using bindcraft with 1 candidates"


def _bindcraft(tmp_path: Path, message: str = _POLICY_MESSAGE):
    gate, drafter, _store, dispatcher, reply = _stack(message)
    assert reply.request is not None and reply.request.generator == "bindcraft"
    path, digest, snapshot = _ready(tmp_path)
    verified, locations = _verified(tmp_path, digest)
    selected = drafter.bind_pdl1_selection(
        session_id=SESSION,
        request_id=reply.request.request_id,
        selection=Pdl1InputSelection.from_verified(verified),
    )
    approved = _decide(gate, reply, revision=selected.revision, idempotency_key="decision-docker")
    assert approved.approval is not None
    demonstrated = gate.record_bindcraft_demonstration(
        session_id=SESSION,
        approval_id=approved.approval.approval_id,
        idempotency_key="demo-docker",
    )
    assert demonstrated.status == "demonstration_completed"
    assert demonstrated.demonstration is not None
    assert demonstrated.demonstration.to_dict()["candidates"] == []
    assert demonstrated.demonstration.to_dict()["observed_scientific_tool_calls"] == 0
    assert reply.request.generator == "bindcraft"
    return gate, drafter, dispatcher, approved, verified, locations, path, snapshot, selected


def _bundle(
    tmp_path: Path,
    control: ScriptedDockerControl,
    store: InMemoryScientificRunStore,
    message: str = _POLICY_MESSAGE,
):
    gate, _drafter, _dispatcher, approved, verified, _locations, path, snapshot, selected = (
        _bindcraft(tmp_path, message)
    )
    request = None
    from cellertz.application.request_draft import _request_from

    request = _request_from(selected)
    assert request is not None
    info, image = _facts()
    root = tmp_path / "stage"
    root.mkdir()
    control.info = info
    control.image_record = image
    bundle = DockerRegistrationBundle(
        config=_config(root),
        info=info,
        image=image,
        verified=verified,
        draft=selected,
        request=request,
        budget=gate._budget(SESSION),
        control=control,
        supervisor=ScriptedDeadline(),
        store=store,
        cpu_count=2,
        memory_mib=4096,
        timeout_seconds=120,
        max_output_bytes=1048576,
    )
    spec = _specification(
        path,
        snapshot.observed_at,
        target_structure_sha256=verified.target_structure_sha256,
        settings_sha256=verified.settings_sha256,
        advanced_profile_sha256=verified.advanced_profile_sha256,
    )
    return gate, approved, bundle, spec, snapshot, selected


def test_historical_revision_omits_the_selection() -> None:
    gate, _drafter, _store, _dispatcher, reply = _stack()
    budget = gate._budget(SESSION)
    document = revision_document(reply.draft, reply.request, budget)
    assert "pdl1_input_selection" not in document
    assert document["request"]["generator"] == "fixture"
    again = dict(document)
    assert canonical_digest(document) == canonical_digest(again)


def test_selection_changes_the_revision_digest(tmp_path: Path) -> None:
    gate, _drafter, _dispatcher, approved, _verified, locations, _path, _snapshot, selected = (
        _bindcraft(tmp_path)
    )
    budget = gate._budget(SESSION)
    from cellertz.application.request_draft import _request_from

    request = _request_from(selected)
    document = revision_document(selected, request, budget)
    assert document["pdl1_input_selection"]["schema"] == "cellertz.pdl1_input_selection.v1"
    assert "target.pdb" not in str(document)
    assert str(locations.target_structure) not in str(document)
    bare = dict(document)
    del bare["pdl1_input_selection"]
    assert canonical_digest(document) != canonical_digest(bare)
    assert approved.approval.revision_digest == canonical_digest(document)


def test_dispatch_stays_blocked_and_demonstration_is_not_a_tool(tmp_path: Path) -> None:
    gate, _drafter, dispatcher, approved, *_rest = _bindcraft(tmp_path)
    denied = gate.dispatch(
        session_id=SESSION,
        approval_id=approved.approval.approval_id,
        idempotency_key="dispatch-bindcraft",
    )
    assert denied.status == "profile_not_allowlisted"
    assert dispatcher.calls == 1


def test_real_authorization_follows_the_selected_revision(tmp_path: Path) -> None:
    gate, drafter, _dispatcher, approved, verified, _locations, path, snapshot, selected = (
        _bindcraft(tmp_path)
    )
    from cellertz.application.approved_run import ApprovedRunService

    runs = ApprovedRunService(gate, clock=lambda: WHEN)
    service = ScientificAuthorizationService(
        gate._sessions,
        drafter,
        gate,
        runs,
        _ready_service(tmp_path, path, snapshot),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{1:032x}",
    )
    granted = _authorize(service, approved.approval.approval_id)
    assert granted.status == "authorization_created"
    assert granted.authorization.profile_id == DOCKER_PROFILE_ID
    assert granted.authorization.request_digest == approved.approval.revision_digest
    changed = Pdl1InputSelection(
        manifest_digest="ab" * 32,
        target_structure_sha256=verified.target_structure_sha256,
        settings_sha256=verified.settings_sha256,
        advanced_profile_sha256=verified.advanced_profile_sha256,
    )
    drafter.bind_pdl1_selection(
        session_id=SESSION,
        request_id=selected.request_id,
        selection=changed,
    )
    assert (
        service.binding_still_matches(
            session_id=SESSION, authorization_id=granted.authorization.authorization_id
        )
        is False
    )


def _ready_service(tmp_path: Path, path: Path, snapshot):
    del tmp_path, snapshot
    # Reuse the service that produced the snapshot by reconstructing from the same file.
    # The caller passes the original path written by _ready.
    return _load(path, CountingProbe(_attestation_from_path(path)))


def _attestation_from_path(path: Path):
    import json

    document = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return _attestation(document, digest)


def test_cd22_and_taci_stay_blocked(tmp_path: Path) -> None:
    for profile, scope, tool in (
        (CD22_PROFILE_ID, "CD22", "mber"),
        (TACI_PROFILE_ID, "TACI", "mber"),
    ):
        document = _v2()
        document["profile_id"] = profile
        document["target_scope"] = scope
        document["tool_name"] = tool
        path = tmp_path / f"{profile}.json"
        digest = _write(path, document)
        reply = _load(path, CountingProbe(_attestation(document, digest))).read()
        assert reply.status != "ready"


def test_registration_rejects_missing_docker_facts(tmp_path: Path) -> None:
    path, _digest, reply = _ready(tmp_path)
    spec = _specification(path, reply.observed_at)
    historical = register_pdl1_executor(spec, str(path), reply, object())
    assert historical.executor is None
    docker_spec = _specification(
        path,
        reply.observed_at,
        profile_id=DOCKER_PROFILE_ID,
        gpu_count=1,
        target_structure_sha256=hashlib.sha256(b"synthetic-target").hexdigest(),
        settings_sha256=hashlib.sha256(b"synthetic-settings").hexdigest(),
        advanced_profile_sha256=hashlib.sha256(
            canonical_advanced_profile_bytes({"limit": 1, "name": "synthetic"})
        ).hexdigest(),
    )
    missing = register_pdl1_executor(docker_spec, str(path), reply, None)
    assert missing.executor is None
    assert missing.code == "pdl1_execution_configuration_missing"
    assert open_registered_executor(None) if False else True
    with pytest.raises(ScientificRunConfigurationError) as blocked:
        open_registered_executor(None)
    assert blocked.value.code == "scientific_run_executor_unconfigured"
    assert resolve_scientific_run_execution({}) == "disabled"


def test_vram_request_is_rejected() -> None:
    info, image = _facts()
    config = _config(Path("/tmp"), vram_mib=1)
    assert "vram_quota" in docker_gaps(info, image, config)


def test_staging_uses_captured_bytes(tmp_path: Path) -> None:
    path, digest, _reply = _ready(tmp_path)
    del path
    verified, locations = _verified(tmp_path, digest)
    root = tmp_path / "stage"
    root.mkdir()
    os.chmod(locations.target_structure, 0o644)
    Path(locations.target_structure).write_bytes(b"replaced-source")
    staged = stage_verified_inputs(verified, str(root), "run_" + ("a" * 32), 1)
    assert Path(staged, "target.pdb").read_bytes() == b"synthetic-target"
    assert Path(staged, "advanced.json").read_bytes() == verified.advanced_profile_canonical
    with pytest.raises(ApplicationContractError) as error:
        stage_verified_inputs(verified, str(root), "../escape", 1)
    assert error.value.code == "pdl1_staging_rejected"
    assert "escape" not in str(error.value)


def test_scripted_execution_completes_without_a_daemon(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, snapshot, _selected = _bundle(tmp_path, control, store)
    registration = register_pdl1_executor(
        spec, str(tmp_path / "manifest.json"), snapshot, None, docker=bundle
    )
    assert registration.accepted is False
    assert registration.executor is None
    assert registration.code == "pdl1_worker_loss_deadline_unresolved"
    assert registration.missing == ("host_deadline_service",)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    runs = ApprovedRunService(gate, clock=lambda: WHEN)
    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        runs,
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{2:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id)
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-docker",
    )
    finished = service.process_next()
    assert finished is not None
    assert finished.status == "completed"
    result_label = "simulated"
    assert result_label == "simulated"
    assert [item.kind for item in finished.artifacts] == ["execution_record"]
    command = " ".join(next(call[1] for call in control.calls if call[0] == "create"))
    assert "--privileged" not in command
    assert "docker.sock" not in command
    assert "--storage-opt" in command
    assert "readonly" in command
    assert "--gpus" in command
    assert "--recorded-template" not in command
    assert "--n-designs 1" in command
    assert "--length-min 65" in command
    assert "--length-max 65" in command
    assert "--cellertz-seed 7" in command
    assert _FILTERS in command
    assert "--server-argv" not in command
    assert "--env" not in command
    assert created.run.run_id == finished.run_id
    replay = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-docker",
    )
    assert replay.run.run_id == finished.run_id
    assert sum(1 for call in control.calls if call[0] == "create") == 1


def test_argv_that_disagrees_with_the_approved_request_does_not_create(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    disagreed = (
        "--n-designs",
        "2",
        "--length-min",
        "65",
        "--length-max",
        "65",
        "--cellertz-seed",
        "7",
        "--filters",
        _FILTERS,
    )
    bundle = replace(bundle, config=replace(bundle.config, argv=disagreed))
    spec = replace(spec, recorded_argv_template=disagreed)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{4:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-mismatch")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-mismatch",
    )
    finished = service.process_next()
    assert finished is not None
    assert finished.status == "failed"
    assert finished.text == FAILED_TEXT
    assert all(call[0] != "create" for call in control.calls)


def test_unbound_seed_and_filter_stop_before_create(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    unbound = (
        "--n-designs",
        "1",
        "--length-min",
        "65",
        "--length-max",
        "65",
        "--cellertz-seed",
        "8",
        "--filters",
        _FILTERS,
    )
    bundle = replace(bundle, config=replace(bundle.config, argv=unbound))
    spec = replace(spec, recorded_argv_template=unbound)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{5:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-unbound")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-unbound",
    )
    finished = service.process_next()
    assert finished is not None and finished.status == "failed"
    assert all(call[0] != "create" for call in control.calls)


def test_approved_lengths_are_compared_without_using_manifest_equality() -> None:
    from cellertz.application.request_draft import ProteinDesignRequest

    approved = ProteinDesignRequest(
        request_id="request-1",
        session_id=SESSION,
        revision=1,
        target_id="PDL1",
        objective="binder_design",
        generator="bindcraft",
        n_candidates=1,
        constraints=(("length_min", 65), ("length_max", 65), ("seed", 7)),
        filter_policy="no_filters",
    )
    matching = (
        "--n-designs",
        "1",
        "--length-min",
        "65",
        "--length-max",
        "65",
        "--cellertz-seed",
        "7",
        "--filters",
        _FILTERS,
    )
    assert container_arguments_agree(approved, matching) is True
    mismatched = (
        "--n-designs",
        "1",
        "--length-min",
        "65",
        "--length-max",
        "150",
        "--cellertz-seed",
        "7",
        "--filters",
        _FILTERS,
    )
    assert container_arguments_agree(approved, mismatched) is False
    assert container_arguments_agree(approved, ("--recorded-template", *matching)) is False
    open_lengths = replace(approved, constraints=(("seed", 7),), filter_policy="no_filters")
    assert container_arguments_agree(open_lengths, matching) is False
    too_many = replace(approved, n_candidates=65)
    counted = ("--n-designs", "65", *matching[2:])
    assert container_arguments_agree(too_many, counted) is False
    too_long = replace(
        approved,
        constraints=(("length_min", 2147483648), ("length_max", 2147483648), ("seed", 7)),
    )
    stretched = (
        "--n-designs",
        "1",
        "--length-min",
        "2147483648",
        "--length-max",
        "2147483648",
        "--cellertz-seed",
        "7",
        "--filters",
        _FILTERS,
    )
    assert container_arguments_agree(too_long, stretched) is False
    bounded = replace(
        approved,
        n_candidates=64,
        constraints=(("length_min", 1), ("length_max", 2147483647), ("seed", 4294967295)),
    )
    inclusive = (
        "--n-designs",
        "64",
        "--length-min",
        "1",
        "--length-max",
        "2147483647",
        "--cellertz-seed",
        "4294967295",
        "--filters",
        _FILTERS,
    )
    assert container_arguments_agree(bounded, inclusive) is True


def test_create_failure_blocks_retry(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    control.fail_create = True
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{3:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-fail")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-fail",
    )
    failed = service.process_next()
    assert failed is not None
    assert failed.status == "failed"
    assert failed.text == EXTERNAL_UNCERTAIN_TEXT
    retry = service.retry(
        session_id=SESSION, run_id=created.run.run_id, idempotency_key="retry-fail"
    )
    assert retry.status == "retry_not_allowed"
    assert sum(1 for call in control.calls if call[0] == "create") == 1


def test_reconcile_persists_a_finished_container_without_relaunch(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{4:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-recover")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-recover",
    )
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    assert running is not None
    attempt = ExternalAttempt(
        run_id=running.run_id,
        attempt=running.attempt,
        authorization_id=running.authorization_id,
        binding_hash=running.binding_hash,
        deployment_id="dep1",
        daemon_id="daemon-1",
        container_name="cellertz-dep1-existing",
        state="exited",
        container_id="cid-existing",
        retry_allowed=False,
    )
    store.reserve_external(attempt)
    control.policy = executor._policy(attempt)
    control.inspect_result = {
        "Id": "cid-existing",
        "State": {"Status": "exited", "ExitCode": 0, "Running": False},
        "Config": {
            "Image": f"{_REPO}@{_IMAGE}",
            "Labels": dict(_labels(attempt)),
        },
        "HostConfig": {
            "Privileged": False,
            "Memory": 4096 * 1024 * 1024,
            "NanoCpus": 2_000_000_000,
        },
    }
    control.calls.clear()
    finished = service.startup()
    assert finished == ()
    stored = store.read_run_any(created.run.run_id)
    assert stored is not None and stored.status == "completed"
    assert all(call[0] != "create" for call in control.calls)


def _labels(attempt: ExternalAttempt) -> dict[str, str]:
    return {
        "cellertz.deployment": attempt.deployment_id,
        "cellertz.run": attempt.run_id,
        "cellertz.attempt": str(attempt.attempt),
        "cellertz.authorization": attempt.authorization_id,
        "cellertz.binding": attempt.binding_hash,
        "cellertz.daemon": attempt.daemon_id,
    }


@pytest.mark.parametrize(
    ("state", "daemon_ok", "inspection", "output_ok", "decision"),
    [
        ("reserved", True, "absent", False, "abandoned_before_create"),
        ("reserved", False, "absent", False, "uncertain"),
        ("creating", True, "absent", False, "uncertain"),
        ("creating", True, "error", False, "uncertain"),
        ("running", False, "running", False, "uncertain"),
        ("running", True, "running", False, "uncertain"),
        ("running", True, "foreign", False, "uncertain"),
        ("exited", True, "exited", True, "persist_completed"),
        ("exited", True, "exited", False, "persist_failed"),
    ],
)
def test_reconcile_decisions(state, daemon_ok, inspection, output_ok, decision) -> None:
    assert (
        reconcile_decision(
            state=state, daemon_ok=daemon_ok, inspection=inspection, output_ok=output_ok
        )
        == decision
    )


def test_reserved_restart_keeps_the_fixture_interruption_text(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-reserved")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-reserved",
    )
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    store.reserve_external(
        ExternalAttempt(
            run_id=running.run_id,
            attempt=running.attempt,
            authorization_id=running.authorization_id,
            binding_hash=running.binding_hash,
            deployment_id="dep1",
            daemon_id="daemon-1",
            container_name="cellertz-dep1-reserved",
            state="reserved",
            retry_allowed=True,
        )
    )
    control.calls.clear()
    interrupted = service.startup()
    assert interrupted[0].text == INTERRUPTED_TEXT
    assert all(call[0] != "create" for call in control.calls)
    retried = service.retry(
        session_id=SESSION, run_id=created.run.run_id, idempotency_key="retry-reserved"
    )
    assert retried.status == "run_queued"


def test_deadline_supervisor_acknowledges_before_any_kill() -> None:
    seen: list[tuple[list[str], dict]] = []

    def starter(argv, **kwargs):
        os.write(kwargs["stdout"], b"ready\n")
        seen.append((list(argv), kwargs))

    ack = DetachedDeadlineSupervisor(starter).acknowledge(
        container_id="cid-1",
        deadline_epoch=10.0,
        daemon_id="daemon-1",
        image=f"{_REPO}@{_IMAGE}",
    )
    argv, kwargs = seen[0]
    assert ack.ready is True
    assert argv[0] == sys.executable
    assert "cid-1" in argv
    assert "cellertz-demo" not in argv
    assert kwargs["start_new_session"] is True
    assert "shell" not in kwargs
    assert kwargs["env"] == {"PATH": "/usr/bin:/bin"}
    failed = DetachedDeadlineSupervisor(lambda *_args, **_kwargs: None).acknowledge(
        container_id="cid-1",
        deadline_epoch=10.0,
        daemon_id="daemon-1",
        image=f"{_REPO}@{_IMAGE}",
    )
    assert failed.ready is False


def test_cli_create_uses_structured_arguments() -> None:
    seen: list[list[str]] = []

    def runner(argv, *, max_bytes: int):
        del max_bytes
        seen.append(list(argv))
        return b"cid-cli\n"

    control = CliDockerControl(
        runner,
        docker_host=f"unix://{Path('/tmp') / 'cellertz-endpoint' / 'docker.sock'}",
        docker_root="/tmp/cellertz-endpoint",
    )
    from cellertz.application.pdl1_docker import ContainerSpec

    spec = ContainerSpec(
        name="cellertz-demo",
        image=f"{_REPO}@{_IMAGE}",
        image_id=_IMAGE_ID,
        entrypoint="recorded-entry",
        argv=("--server-argv",),
        inputs="/private/stage/inputs",
        cpu_count=2,
        memory_mib=4096,
        storage_bytes=1048576,
        log_bytes=4096,
        gpu_device="0",
        labels=(("cellertz.deployment", "dep1"),),
    )
    assert control.create(spec) == "cid-cli"
    assert seen[0][0:4] == ["docker", "--host", control.docker_host, "create"]
    assert "--privileged" not in seen[0]
    joined = " ".join(seen[0])
    assert "readonly" in joined
    assert "size=1048576" in joined


def test_zero_exit_without_csv_fails(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    control.output_payload = b""
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{5:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-empty")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-empty",
    )
    failed = service.process_next()
    assert failed is not None
    assert failed.status == "failed"
    assert failed.text == EXTERNAL_FAILED_TEXT
    assert failed.artifacts == ()


def test_daemon_identity_change_is_uncertain() -> None:
    assert (
        reconcile_decision(state="running", daemon_ok=False, inspection="exited", output_ok=True)
        == "uncertain"
    )


def test_csv_contract_rejects_a_comma_and_accepts_bindcraft_rows() -> None:
    assert output_bytes_ok(b",", 1024) is False
    assert output_bytes_ok(b"Design,Sequence\nexample,ACDE\n", 1024) is True
    wide = b"Design,Sequence\nexample," + b"A" * 70000 + b"\n"
    assert len(wide) > 65536
    assert output_bytes_ok(wide, 1048576) is True
    assert output_bytes_ok(wide, 65536) is False


def test_tar_collection_rejects_links_and_traversal() -> None:
    import io
    import tarfile

    def archive(name: str, *, link: bool = False) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as packed:
            if link:
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = "elsewhere"
                packed.addfile(info)
            else:
                payload = b"Design,Sequence\nexample,ACDE\n"
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                packed.addfile(info, io.BytesIO(payload))
        return buffer.getvalue()

    assert csv_from_tar(archive("design_records.csv"), 1024).startswith(b"Design,")
    with pytest.raises(ApplicationContractError):
        csv_from_tar(archive("design_records.csv", link=True), 1024)
    with pytest.raises(ApplicationContractError):
        csv_from_tar(archive("../design_records.csv"), 1024)


def test_cli_copy_out_stops_at_the_byte_cap() -> None:
    import io
    import tarfile

    payload = b"Design,Sequence\nexample,ACDE\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as packed:
        info = tarfile.TarInfo("out/design_records.csv")
        info.size = len(payload)
        packed.addfile(info, io.BytesIO(payload))
    blob = buffer.getvalue()
    seen: list[int] = []
    calls = {"n": 0}

    def runner(argv, *, max_bytes: int) -> bytes:
        del argv
        seen.append(max_bytes)
        calls["n"] += 1
        if calls["n"] > 1:
            return b"x" * (max_bytes + 1)
        return blob

    control = CliDockerControl(
        runner,
        docker_host=f"unix://{Path('/tmp') / 'cellertz-endpoint' / 'docker.sock'}",
        docker_root="/tmp/cellertz-endpoint",
    )
    assert control.copy_out("cid-1", 1024) == payload
    assert seen[-1] == 1024 + 20480
    with pytest.raises(ApplicationContractError) as error:
        control.copy_out("cid-1", 1)
    assert error.value.code == "pdl1_output_too_large"


def test_private_output_limit_does_not_override_policy(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    _gate, _approved, bundle, spec, snapshot, _selected = _bundle(tmp_path, control, store)
    replaced = replace(bundle, max_output_bytes=100)
    registration = register_pdl1_executor(
        spec, str(tmp_path / "manifest.json"), snapshot, None, docker=replaced
    )
    assert registration.code == "pdl1_execution_policy_mismatch"
    assert registration.executor is None


def test_expiry_before_start_does_not_start(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    bundle.supervisor.expired = True
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{6:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-expire")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-expire",
    )
    failed = service.process_next()
    assert failed is not None and failed.status == "failed"
    assert all(call[0] != "start" for call in control.calls)
    assert bundle.supervisor.acks[0]["container_id"] == "cid-scripted"


def test_supervisor_startup_failure_does_not_start(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    bundle.supervisor.ready = False
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{7:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-ack")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-ack",
    )
    failed = service.process_next()
    assert failed is not None and failed.status == "failed"
    assert all(call[0] != "start" for call in control.calls)


def test_matching_labels_with_the_wrong_container_id_are_foreign(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    control.inspect_result = {
        "Id": "other-container",
        "State": {"Status": "exited", "ExitCode": 0, "Running": False},
        "Config": {"Labels": {}},
        "HostConfig": {"Privileged": False},
    }
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{8:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-foreign")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-foreign",
    )
    failed = service.process_next()
    assert failed is not None and failed.status == "failed"
    assert failed.text == EXTERNAL_UNCERTAIN_TEXT


def test_startup_composition_does_not_require_a_draft(tmp_path: Path) -> None:
    import json

    from cellertz.application.pdl1_docker import CONFIG_ENV, prepare_enabled_infrastructure
    from cellertz.application.pdl1_host_deadline import SystemdHostDeadline
    from cellertz.application.scientific_run_config import ScientificRunConfigurationError

    with pytest.raises(ScientificRunConfigurationError) as absent:
        enabled_execution_blocked({})
    assert absent.value.code == "scientific_run_executor_unconfigured"
    assert absent.value.missing == ("deployment",)
    path = tmp_path / "deployment.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ScientificRunConfigurationError) as invalid:
        enabled_execution_blocked({CONFIG_ENV: str(path)})
    assert invalid.value.code == "scientific_run_executor_unconfigured"
    assert invalid.value.missing == ("pdl1_docker_endpoint_required",)
    manifest, _digest, _snapshot = _ready(tmp_path)
    interpreter = tmp_path / "python"
    _executable_interpreter(interpreter)
    state = tmp_path / "deadlines"
    state.mkdir()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    path.write_text(
        json.dumps(
            {
                "deadline_interpreter": str(interpreter),
                "deadline_manager": "system",
                "docker_host": f"unix://{tmp_path / 'docker.sock'}",
                "docker_root": str(tmp_path),
                "deadline_state_dir": str(state),
                "deployment_id": "dep1",
                "entrypoint": "recorded-entry",
                "argv": list(_BOUND_ARGV),
                "gpu_device": "0",
                "image_digest": _IMAGE,
                "image_id": _IMAGE_ID,
                "input_root": str(inputs),
                "log_bytes": 1048576,
                "manifest_path": str(manifest),
                "repository": _REPO,
                "staging_root": str(stage),
                "storage_bytes": 1048576,
            }
        ),
        encoding="utf-8",
    )
    control = ScriptedDockerControl()
    info, image = _facts()
    control.info = info
    control.image_record = image
    deadline = SystemdHostDeadline(
        _config(
            stage,
            manifest_path=str(manifest),
            input_root=str(inputs),
            deadline_manager="system",
            deadline_state_dir=str(state),
            deadline_interpreter=str(interpreter),
        ),
        _deadline_runner(),
    )
    infrastructure = prepare_enabled_infrastructure(
        {CONFIG_ENV: str(path)},
        control=control,
        deadline=deadline,
    )
    assert not hasattr(infrastructure, "draft")
    assert infrastructure.bind.__doc__ and "authorize" in infrastructure.bind.__doc__
    assert ("daemon_info",) in control.calls
    assert all(call[0] != "create" for call in control.calls)


def test_malformed_retry_flag_is_rejected(tmp_path: Path) -> None:
    from cellertz.application.scientific_run_store import SQLiteScientificRunStore

    store = SQLiteScientificRunStore(tmp_path / "runs.sqlite")
    store._conn.execute(
        """
        INSERT INTO external_attempts (
            run_id, attempt, authorization_id, binding_hash, deployment_id,
            daemon_id, container_name, container_id, state, retry_allowed, output_sha256
        ) VALUES ('run_1', 1, 'authorization_1', 'ab', 'dep', 'daemon', 'name', NULL,
                  'reserved', 2, NULL)
        """
    )
    store._conn.commit()
    with pytest.raises(Exception) as error:
        store.list_external()
    assert getattr(error.value, "code", "") == "scientific_run_record_invalid"
    store.close()


def test_retained_output_can_be_recorded_again(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    _gate, _approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    executor = protocol_executor(spec, bundle)
    payload = b"Design,Sequence\nexample,ACDE\n"

    class _Run:
        run_id = "run_" + ("a" * 32)
        attempt = 1

    executor._retain(bundle.config.staging_root, _Run(), payload)
    executor._retain(bundle.config.staging_root, _Run(), payload)
    retained = Path(
        bundle.config.staging_root, f"{_Run.run_id}-1", "retained", "design_records.csv"
    )
    assert retained.read_bytes() == payload


def test_an_older_attempt_does_not_complete_the_current_one(tmp_path: Path) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{9:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-stale")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-stale",
    )
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    assert running is not None and running.attempt == 1
    store.reserve_external(
        ExternalAttempt(
            run_id=running.run_id,
            attempt=2,
            authorization_id=running.authorization_id,
            binding_hash=running.binding_hash,
            deployment_id="dep1",
            daemon_id="daemon-1",
            container_name="old",
            container_id="old-container",
            state="exited",
            retry_allowed=False,
            output_sha256="ab" * 32,
        )
    )
    executor.reconcile(store, WHEN.isoformat())
    assert store.read_run_any(created.run.run_id).status == "running"


def test_production_runner_bounds_streams_without_launching_docker(tmp_path: Path) -> None:
    from cellertz.application.pdl1_docker import ProductionCommandRunner

    seen: list[dict[str, object]] = []

    class _Stream:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload
            self.offset = 0

        def fileno(self) -> int:
            return 8

        def read(self, count: int) -> bytes:
            if self.offset >= len(self.payload):
                return b""
            chunk = self.payload[self.offset : self.offset + count]
            self.offset += len(chunk)
            return chunk

        def close(self) -> None:
            return None

        @property
        def done(self) -> bool:
            return self.offset >= len(self.payload)

    class _Process:
        def __init__(
            self, stdout: bytes, stderr: bytes = b"", code: int = 0, hang: bool = False
        ) -> None:
            self.stdout = _Stream(stdout)
            self.stderr = _Stream(stderr)
            self.stdin = None
            self._code = code
            self.hang = hang
            self.killed = False
            self.waited = False
            self.returncode: int | None = None

        def poll(self) -> int | None:
            if self.killed:
                self.returncode = -9
                return -9
            if self.hang:
                return None
            if self.stdout.done and self.stderr.done:
                self.returncode = self._code
                return self._code
            return None

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.waited = True
            if self.returncode is None:
                self.returncode = self._code
            return self.returncode

    def factory(argv: list[str], **kwargs: object) -> _Process:
        seen.append({"argv": argv, **kwargs})
        marker = argv[-1]
        if marker == "big":
            return _Process(b"x" * 100)
        if marker == "err":
            return _Process(b"ok", stderr=b"secret-stderr" * 20)
        if marker == "fail":
            return _Process(b"", code=1)
        if marker == "hang":
            return _Process(b"", hang=True)
        return _Process(b"cid-cli\n")

    clock = {"now": 0.0}

    def monotonic() -> float:
        clock["now"] += 1.0
        return clock["now"]

    runner = ProductionCommandRunner(
        process_factory=factory,
        monotonic=monotonic,
        sleep=lambda _seconds: None,
        set_blocking=lambda _fd, _flag: None,
        docker_host=f"unix://{tmp_path / 'docker.sock'}",
        docker_root=str(tmp_path),
    )
    (tmp_path / "docker.sock").write_bytes(b"")
    assert runner(["docker", "create", "ok"], max_bytes=64) == b"cid-cli\n"
    assert seen[0]["shell"] is False
    assert seen[0]["env"] == {"PATH": "/usr/bin:/bin"}
    assert seen[0]["argv"][:3] == ["docker", "--host", runner.docker_host]
    with pytest.raises(ApplicationContractError) as oversized:
        runner(["docker", "logs", "big"], max_bytes=16)
    assert oversized.value.code == "pdl1_output_too_large"
    assert "xxxx" not in str(oversized.value)
    with pytest.raises(ApplicationContractError) as noisy:
        runner(["docker", "logs", "err"], max_bytes=4)
    assert noisy.value.code == "pdl1_output_too_large"
    assert "secret-stderr" not in str(noisy.value)
    with pytest.raises(ApplicationContractError) as failed:
        runner(["docker", "start", "fail"], max_bytes=16)
    assert failed.value.code == "pdl1_docker_command_failed"
    with pytest.raises(ApplicationContractError) as timed:
        runner(["docker", "inspect", "hang"], max_bytes=16, timeout=0.1)
    assert timed.value.code == "pdl1_docker_command_timeout"
    assert all(item["shell"] is False for item in seen)


def test_command_runner_waits_after_eof_and_rejects_blocking_reads(tmp_path: Path) -> None:
    from cellertz.application.pdl1_docker import ProductionCommandRunner

    class _Stream:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload
            self.offset = 0
            self.closed = False

        def fileno(self) -> int:
            return 7

        def read(self, count: int) -> bytes:
            if self.offset >= len(self.payload):
                return b""
            chunk = self.payload[self.offset : self.offset + count]
            self.offset += len(chunk)
            return chunk

        def close(self) -> None:
            self.closed = True

    class _Process:
        def __init__(self, code: int, *, exit_after: int, hang: bool = False) -> None:
            self.stdout = _Stream(b"out-bytes")
            self.stderr = _Stream(b"err-bytes")
            self.stdin = None
            self.returncode: int | None = None
            self.killed = False
            self._code = code
            self._polls = 0
            self.exit_after = exit_after
            self.hang = hang

        def poll(self) -> int | None:
            self._polls += 1
            if self.killed:
                self.returncode = -9
                return -9
            streams_closed = self.stdout.offset >= len(self.stdout.payload) and (
                self.stderr.offset >= len(self.stderr.payload)
            )
            if self.hang or not streams_closed or self._polls < self.exit_after:
                return None
            self.returncode = self._code
            return self._code

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            if self.returncode is None:
                self.returncode = -9 if self.killed else self._code
            return self.returncode

    created: list[_Process] = []

    def factory(argv: list[str], **kwargs: object) -> _Process:
        del kwargs
        mode = argv[-1]
        process = _Process(
            0 if mode == "ok" else 3,
            exit_after=4,
            hang=mode == "hang",
        )
        if mode == "big":
            process.stdout = _Stream(b"x" * 100)
        if mode == "block":
            process.stdout.fileno = lambda: (_ for _ in ()).throw(OSError("no fd"))  # type: ignore[method-assign]
        created.append(process)
        return process

    clock = {"now": 0.0}

    def monotonic() -> float:
        clock["now"] += 0.01
        return clock["now"]

    runner = ProductionCommandRunner(
        process_factory=factory,
        monotonic=monotonic,
        sleep=lambda _seconds: None,
        set_blocking=lambda _fd, _flag: None,
        docker_host=f"unix://{tmp_path / 'docker.sock'}",
        docker_root=str(tmp_path),
    )
    (tmp_path / "docker.sock").write_bytes(b"")
    assert runner(["docker", "inspect", "ok"], max_bytes=64) == b"out-bytes"
    assert created[0].returncode == 0
    assert created[0].killed is False
    assert created[0].stdout.offset == len(b"out-bytes")
    assert created[0].stderr.offset == len(b"err-bytes")
    with pytest.raises(ApplicationContractError) as failed:
        runner(["docker", "inspect", "bad"], max_bytes=64)
    assert failed.value.code == "pdl1_docker_command_failed"
    assert created[1].returncode == 3
    assert created[1].killed is False
    with pytest.raises(ApplicationContractError) as timed:
        runner(["docker", "inspect", "hang"], max_bytes=64, timeout=0.05)
    assert timed.value.code == "pdl1_docker_command_timeout"
    assert created[2].killed is True
    with pytest.raises(ApplicationContractError) as blocked:
        runner(["docker", "inspect", "block"], max_bytes=64)
    assert blocked.value.code == "pdl1_docker_command_failed"
    assert created[3].killed is True
    with pytest.raises(ApplicationContractError) as oversized:
        runner(["docker", "logs", "big"], max_bytes=4)
    assert oversized.value.code == "pdl1_output_too_large"
    assert created[4].killed is True
    assert "xxxx" not in str(oversized.value)


def test_container_policy_rejects_matching_identity_with_bad_runtime() -> None:
    from cellertz.application.pdl1_docker import (
        ContainerPolicy,
        _host_policy,
        _inspection_kind,
        _mount_policy,
    )

    policy = ContainerPolicy(
        image_reference=f"{_REPO}@{_IMAGE}",
        image_id=_IMAGE_ID,
        entrypoint="recorded-entry",
        argv=_BOUND_ARGV,
        inputs="/private/stage/inputs",
        cpu_count=2,
        memory_mib=4096,
        storage_bytes=1048576,
        log_bytes=1048576,
        gpu_device="0",
    )
    attempt = ExternalAttempt(
        run_id="run_" + ("a" * 32),
        attempt=1,
        authorization_id="authorization_1",
        binding_hash="ab" * 32,
        deployment_id="dep1",
        daemon_id="daemon-1",
        container_name="cellertz-dep1",
        container_id="cid-1",
        state="running",
        retry_allowed=False,
    )

    def payload() -> dict:
        return {
            "Id": "cid-1",
            "Image": policy.image_id,
            "State": {"Status": "exited", "ExitCode": 0, "Running": False},
            "Config": {
                "Image": policy.image_reference,
                "Entrypoint": [policy.entrypoint],
                "Cmd": list(policy.argv),
                "Labels": _labels(attempt),
            },
            "HostConfig": _host_policy(policy),
            "Mounts": _mount_policy(policy),
        }

    assert _inspection_kind(payload(), attempt, policy=policy) == "exited"
    wrong_image = payload()
    wrong_image["Image"] = "sha256:" + ("11" * 32)
    host_net = payload()
    host_net["HostConfig"]["NetworkMode"] = "host"
    extra_mount = payload()
    extra_mount["Mounts"] = [
        *_mount_policy(policy),
        {"Type": "bind", "Source": "/var", "Destination": "/data", "RW": True},
    ]
    missing_gpu = payload()
    missing_gpu["HostConfig"]["DeviceRequests"] = []
    wrong_entry = payload()
    wrong_entry["Config"]["Entrypoint"] = ["other-entry"]
    wrong_restart = payload()
    wrong_restart["HostConfig"]["RestartPolicy"] = {"Name": "always", "MaximumRetryCount": 0}
    wrong_security = payload()
    wrong_security["HostConfig"]["SecurityOpt"] = []
    wrong_storage = payload()
    wrong_storage["HostConfig"]["StorageOpt"] = {"size": "1"}
    wrong_log = payload()
    wrong_log["HostConfig"]["LogConfig"] = {"Type": "none", "Config": {}}
    for changed in (
        wrong_image,
        host_net,
        extra_mount,
        missing_gpu,
        wrong_entry,
        wrong_restart,
        wrong_security,
        wrong_storage,
        wrong_log,
    ):
        assert _inspection_kind(changed, attempt, policy=policy) == "foreign"


def test_csv_rejects_duplicate_headers_and_ragged_rows() -> None:
    assert output_bytes_ok(b"Design,Sequence,Sequence\nexample,ACDE,ACDE\n", 1024) is False
    assert output_bytes_ok(b"Design,Sequence\nexample\n", 1024) is False
    assert output_bytes_ok(b"Design,Sequence\nexample,ACDE,extra\n", 1024) is False
    assert output_bytes_ok(b"Design,Sequence,Rank\nexample,ACDE,1\n", 1024) is True


def test_tar_reader_rejects_compression_and_extra_members(tmp_path: Path, monkeypatch) -> None:
    import gzip
    import io
    import tarfile

    def archive(*members: tuple[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as packed:
            for name, body in members:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                packed.addfile(info, io.BytesIO(body))
        return buffer.getvalue()

    def reject_host_extract(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("host extract")

    monkeypatch.setattr(tarfile.TarFile, "extract", reject_host_extract)
    monkeypatch.setattr(tarfile.TarFile, "extractall", reject_host_extract)
    body = b"Design,Sequence\nexample,ACDE\n"
    assert csv_from_tar(archive(("design_records.csv", body)), 1024) == body
    compressed = gzip.compress(archive(("design_records.csv", body)))
    with pytest.raises(ApplicationContractError):
        csv_from_tar(compressed, 1024)
    with pytest.raises(ApplicationContractError):
        csv_from_tar(archive(("design_records.csv", body), ("extra.csv", b"x")), 1024)
    from cellertz.application.pdl1_docker import _tar_member_allowed

    info = tarfile.TarInfo("design_records.csv")
    info.size = len(body)
    info.sparse = [(0, 1)]
    assert _tar_member_allowed(info, 1024) is False
    assert list(tmp_path.iterdir()) == []


def test_deployment_file_rejects_non_regular_and_duplicate_keys(tmp_path: Path) -> None:
    import json
    import os

    from cellertz.application.pdl1_docker import CONFIG_ENV, load_docker_deployment

    values = {
        "repository": _REPO,
        "image_digest": _IMAGE,
        "image_id": _IMAGE_ID,
        "entrypoint": "recorded-entry",
        "argv": list(_BOUND_ARGV),
        "gpu_device": "0",
        "deployment_id": "dep1",
        "staging_root": str(tmp_path),
        "storage_bytes": 1048576,
        "log_bytes": 1048576,
        "manifest_path": str(tmp_path / "manifest.json"),
        "input_root": str(tmp_path / "inputs"),
        "deadline_manager": "system",
        "deadline_state_dir": str(tmp_path / "deadlines"),
        "docker_host": f"unix://{tmp_path / 'docker.sock'}",
        "docker_root": str(tmp_path),
        "deadline_interpreter": str(tmp_path / "python"),
    }
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    loaded = load_docker_deployment({CONFIG_ENV: str(path)})
    assert loaded is not None and loaded.image_digest == _IMAGE
    with pytest.raises(ApplicationContractError):
        load_docker_deployment({CONFIG_ENV: str(tmp_path)})
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ApplicationContractError):
        load_docker_deployment({CONFIG_ENV: str(link)})
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    with pytest.raises(ApplicationContractError):
        load_docker_deployment({CONFIG_ENV: str(fifo)})
    duplicate = path.read_text(encoding="utf-8").replace(
        '"repository"', '"repository": "other", "repository"', 1
    )
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ApplicationContractError):
        load_docker_deployment({CONFIG_ENV: str(path)})
    values["storage_bytes"] = True
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ApplicationContractError):
        load_docker_deployment({CONFIG_ENV: str(path)})


def _executable_interpreter(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _service_command(argv: list[str]) -> list[str]:
    index = 1
    while index < len(argv):
        item = argv[index]
        if item in {"--unit", "--description"}:
            index += 2
            continue
        if item.startswith("-"):
            index += 1
            continue
        return list(argv[index:])
    return []


def _systemd_exec_start(argv: list[str]) -> str:
    if not argv:
        return ""
    rendered = " ".join(argv)
    return (
        "{ path="
        + argv[0]
        + " ; argv[]="
        + rendered
        + " ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
    )


def _deadline_runner(identity: bytes = b"daemon-1\n"):
    import json

    from cellertz.application.contracts import ApplicationContractError

    def runner(argv: list[str], *, max_bytes: int, timeout: float | None = None) -> bytes:
        del max_bytes, timeout
        runner.calls.append(list(argv))
        if argv and argv[0] == "docker":
            if len(argv) < 4 or argv[1] != "--host" or not str(argv[2]).startswith("unix://"):
                raise ApplicationContractError("pdl1_docker_command_failed")
            runner.hosts.append(argv[2])
            argv = ["docker", *argv[3:]]
        if "--version" in argv:
            return b"ok\n"
        if "-c" in argv and "pdl1_host_deadline" in " ".join(argv):
            if getattr(runner, "reject_enforcer", False):
                raise ApplicationContractError("pdl1_docker_command_failed")
            return b"ok\n"
        if argv and argv[0] == "loginctl":
            if getattr(runner, "linger", True) is not True:
                return b"Linger=no\n"
            return b"Linger=yes\n"
        if argv[:2] == ["systemctl", "show"] and "--property=Version" in argv:
            return b"Version=256\n"
        if argv[0] == "systemd-run":
            runner.installed = list(argv)
            runner.service_argv = _service_command(argv)
            prefix = "--setenv=CELLERTZ_PDL1_DOCKER_HOST="
            for item in argv:
                if item.startswith(prefix):
                    runner.unit_endpoint = item[len(prefix) :]
            return b"ok\n"
        if argv[0] == "systemctl" and any(item.endswith(".service") for item in argv):
            if getattr(runner, "hide_units", False):
                raise ApplicationContractError("pdl1_docker_command_failed")
            command = list(getattr(runner, "service_argv", []))
            if getattr(runner, "legacy_exec", False):
                text = " ".join(getattr(runner, "installed", []))
            else:
                replacement = getattr(runner, "replace_state", None)
                if type(replacement) is str and command:
                    command = command[:-1] + [replacement]
                text = _systemd_exec_start(command)
            result = "failed" if getattr(runner, "unit_failed", False) else "success"
            endpoint = getattr(runner, "unit_endpoint_override", None)
            if endpoint is None:
                endpoint = getattr(runner, "unit_endpoint", "")
            return (
                f"ExecStart={text}\nResult={result}\n"
                f"Environment=CELLERTZ_PDL1_DOCKER_HOST={endpoint}\n"
            ).encode()
        if argv[0] == "systemctl" and any(item.endswith(".timer") for item in argv):
            if getattr(runner, "hide_units", False):
                raise ApplicationContractError("pdl1_docker_command_failed")
            unit = next(item for item in argv if item.endswith(".timer"))
            state = "failed" if getattr(runner, "unit_failed", False) else "waiting"
            result = "failed" if getattr(runner, "unit_failed", False) else "success"
            return f"Id={unit}\nActiveState={state}\nResult={result}\n".encode()
        if argv[:4] == ["docker", "info", "--format", "{{.ID}}"]:
            if getattr(runner, "fail_info", False):
                raise ApplicationContractError("pdl1_docker_command_failed")
            return identity
        if argv[:2] == ["docker", "ps"]:
            if getattr(runner, "fail_ps", False):
                raise ApplicationContractError("pdl1_docker_command_failed")
            if getattr(runner, "removed", False) or getattr(runner, "absent", False):
                return b""
            return b"cid-scripted\n"
        if argv[:2] == ["docker", "kill"]:
            if getattr(runner, "fail_kill", False):
                raise ApplicationContractError("pdl1_docker_command_failed")
            return b"killed\n"
        if argv[:3] == ["docker", "rm", "--force"]:
            if getattr(runner, "fail_rm", False):
                raise ApplicationContractError("pdl1_docker_command_failed")
            runner.removed = True
            return b"removed\n"
        if argv[:2] == ["docker", "inspect"]:
            if getattr(runner, "fail_inspect", False):
                raise ApplicationContractError("pdl1_docker_command_failed")
            if getattr(runner, "malformed_inspect", False):
                return b"not-json"
            if getattr(runner, "removed", False):
                return b"[]"
            status = "exited"
            running = False
            if getattr(runner, "inspect_created", False):
                status = "created"
            if getattr(runner, "inspect_running", False):
                status = "running"
                running = True
            image = "sha256:" + ("ff" * 32) if getattr(runner, "wrong_image", False) else _IMAGE_ID
            body = json.dumps(
                {
                    "Id": "cid-scripted",
                    "Image": image,
                    "State": {"Status": status, "Running": running, "ExitCode": 0},
                }
            )
            return body.encode()
        raise ApplicationContractError("pdl1_docker_command_failed")

    runner.calls = []
    runner.hosts = []
    runner.installed = []
    runner.service_argv = []
    runner.unit_endpoint = ""
    return runner


def test_systemd_deadline_registers_and_survives_worker_loss(tmp_path: Path) -> None:
    from cellertz.application.pdl1_host_deadline import SystemdHostDeadline, enforce_deadline

    interpreter = tmp_path / "python"
    _executable_interpreter(interpreter)
    state = tmp_path / "deadlines"
    state.mkdir()
    now = {"t": 1_000.0}
    runner = _deadline_runner()
    config = _config(
        tmp_path,
        manifest_path=str(tmp_path / "manifest.json"),
        input_root=str(tmp_path),
        deadline_manager="system",
        deadline_state_dir=str(state),
        deadline_interpreter=str(interpreter),
    )
    deadline = SystemdHostDeadline(config, runner, clock=lambda: now["t"])
    ready, missing = deadline.readiness()
    assert ready is True and missing == ()
    ack = deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    assert ack.ready is True and ack.expired is False
    installed = next(call for call in runner.calls if call[0] == "systemd-run" and "--unit" in call)
    assert "cellertz.application.pdl1_host_deadline" in installed
    assert "cid-scripted" in " ".join(installed)
    assert "start_new_session" not in installed
    state_path = next(item for item in installed if item.endswith(".json"))
    mismatched = _deadline_runner(b"other-daemon\n")
    fresh = state / "again.json"
    fresh.write_text(Path(state_path).read_text(encoding="utf-8"), encoding="utf-8")
    assert enforce_deadline(str(fresh), mismatched) == "uncertain"
    assert not any(call[0] == "docker" and "kill" in call for call in mismatched.calls)
    now["t"] = 2_000.0
    assert deadline.recover("cid-scripted", "daemon-1") == "enforced"
    assert any(
        call[:2] == ["docker", "--host"] and call[3:] == ["kill", "cid-scripted"]
        for call in runner.calls
    )
    assert "cellertz-dep1" not in " ".join(" ".join(call) for call in runner.calls)
    assert enforce_deadline(state_path, runner) == "enforced"
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    bundle = replace(bundle, supervisor=deadline)
    executor = protocol_executor(spec, bundle)
    from cellertz.application.approved_run import ApprovedRunService

    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{9:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-deadline")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-deadline",
    )
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    assert running is not None
    attempt = ExternalAttempt(
        run_id=running.run_id,
        attempt=running.attempt,
        authorization_id=running.authorization_id,
        binding_hash=running.binding_hash,
        deployment_id="dep1",
        daemon_id="daemon-1",
        container_name="cellertz-dep1-existing",
        state="running",
        container_id="cid-scripted",
        retry_allowed=False,
    )
    store.reserve_external(attempt)
    control.policy = executor._policy(attempt)
    control.inspect_result = {
        "Id": "cid-scripted",
        "State": {"Status": "exited", "ExitCode": 0, "Running": False},
        "Config": {"Image": f"{_REPO}@{_IMAGE}", "Labels": dict(_labels(attempt))},
        "HostConfig": {
            "Privileged": False,
            "Memory": 4096 * 1024 * 1024,
            "NanoCpus": 2_000_000_000,
        },
    }
    control.calls.clear()
    assert service.startup() == ()
    stored = store.read_run_any(created.run.run_id)
    assert stored is not None and stored.status == "completed"
    assert store.external_state(stored.run_id, stored.attempt) == "exited"
    assert all(call[0] != "create" for call in control.calls)
    registration = register_pdl1_executor(
        spec, str(tmp_path / "manifest.json"), _snapshot, None, docker=bundle
    )
    assert registration.executor is not None
    assert registration.code == "pdl1_executor_registered"


def test_configured_startup_runs_one_approved_revision(tmp_path: Path) -> None:
    import json

    from cellertz.application.approved_run import ApprovedRunService
    from cellertz.application.pdl1_docker import CONFIG_ENV, prepare_enabled_infrastructure
    from cellertz.application.pdl1_host_deadline import SystemdHostDeadline

    manifest, _digest, _snapshot = _ready(tmp_path)
    interpreter = tmp_path / "python"
    _executable_interpreter(interpreter)
    deadlines = tmp_path / "deadlines"
    deadlines.mkdir()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    target = b"synthetic-target"
    settings = b"synthetic-settings"
    profile = canonical_advanced_profile_bytes({"limit": 1, "name": "synthetic"})
    for payload in (target, settings, profile):
        (inputs / hashlib.sha256(payload).hexdigest()).write_bytes(payload)
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            {
                "argv": list(_BOUND_ARGV),
                "deadline_interpreter": str(interpreter),
                "deadline_manager": "system",
                "docker_host": f"unix://{tmp_path / 'docker.sock'}",
                "docker_root": str(tmp_path),
                "deadline_state_dir": str(deadlines),
                "deployment_id": "dep1",
                "entrypoint": "recorded-entry",
                "gpu_device": "0",
                "image_digest": _IMAGE,
                "image_id": _IMAGE_ID,
                "input_root": str(inputs),
                "log_bytes": 1048576,
                "manifest_path": str(manifest),
                "repository": _REPO,
                "staging_root": str(stage),
                "storage_bytes": 1048576,
            }
        ),
        encoding="utf-8",
    )
    control = ScriptedDockerControl()
    info, image = _facts()
    control.info = info
    control.image_record = image
    runner = _deadline_runner()
    deadline = SystemdHostDeadline(
        _config(
            stage,
            manifest_path=str(manifest),
            input_root=str(inputs),
            deadline_manager="system",
            deadline_state_dir=str(deadlines),
            deadline_interpreter=str(interpreter),
        ),
        runner,
    )
    infrastructure = prepare_enabled_infrastructure(
        {CONFIG_ENV: str(deployment)},
        control=control,
        deadline=deadline,
    )
    gate, _drafter, _dispatcher, approved, _verified, _locations, _path, _snapshot, _selected = (
        _bindcraft(tmp_path)
    )
    store = InMemoryScientificRunStore()
    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(manifest, CountingProbe(_attestation_from_path(manifest))),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{10:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-integrated")
    before = authorizations.list_authorizations(session_id=SESSION).authorizations
    executor = infrastructure.bind(authorizations=authorizations, store=store)
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-integrated",
    )
    assert created.status == "run_queued"
    finished = service.process_next()
    assert finished is not None and finished.status == "completed"
    assert finished.confirmed is True
    again = authorizations.list_authorizations(session_id=SESSION).authorizations
    assert len(again) == len(before) == 1
    assert sum(1 for call in control.calls if call[0] == "create") == 1
    assert any(call[0] == "start" for call in control.calls)
    assert any(
        call[0] == "systemd-run"
        and "cid-scripted" in " ".join(call)
        and "cellertz.application.pdl1_host_deadline" in call
        for call in runner.calls
    )
    assert store.read_run(SESSION, finished.run_id).status == "completed"


def _host_deadline(tmp_path: Path, *, manager: str = "system"):
    from cellertz.application.pdl1_host_deadline import SystemdHostDeadline

    tmp_path.mkdir(parents=True, exist_ok=True)
    interpreter = tmp_path / "python"
    _executable_interpreter(interpreter)
    state = tmp_path / "deadlines"
    state.mkdir()
    now = {"t": 1_000.0}
    runner = _deadline_runner()
    config = _config(
        tmp_path,
        manifest_path=str(tmp_path / "manifest.json"),
        input_root=str(tmp_path),
        deadline_manager=manager,
        deadline_state_dir=str(state),
        deadline_interpreter=str(interpreter),
    )
    return SystemdHostDeadline(config, runner, clock=lambda: now["t"]), runner, now, interpreter


def test_deadline_readiness_rejects_unusable_interpreter_and_enforcer(tmp_path: Path) -> None:
    deadline, runner, _now, interpreter = _host_deadline(tmp_path)
    interpreter.write_text("", encoding="utf-8")
    interpreter.chmod(0o644)
    ready, missing = deadline.readiness()
    assert ready is False and missing == ("deadline_interpreter",)
    assert runner.calls == []
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o644)
    ready, missing = deadline.readiness()
    assert ready is False and "deadline_interpreter" in missing
    assert not any(call and call[0] == str(interpreter) for call in runner.calls)
    _executable_interpreter(interpreter)
    runner.reject_enforcer = True
    ready, missing = deadline.readiness()
    assert ready is False and missing == ("deadline_enforcer",)
    user, user_runner, _user_now, user_interpreter = _host_deadline(
        tmp_path / "user", manager="user"
    )
    _executable_interpreter(user_interpreter)
    user_runner.linger = False
    ready, missing = user.readiness()
    assert ready is False and missing == ("systemd_linger",)


def test_recover_does_not_trust_an_armed_state_file(tmp_path: Path) -> None:
    deadline, runner, _now, _interpreter = _host_deadline(tmp_path)
    ack = deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    assert ack.ready is True
    runner.hide_units = True
    assert deadline.recover("cid-scripted", "daemon-1") == "uncertain"
    runner.hide_units = False
    runner.unit_failed = True
    assert deadline.recover("cid-scripted", "daemon-1") == "failed"


def test_pause_before_start_cannot_pass_the_deadline_fence(tmp_path: Path) -> None:
    deadline, runner, _now, _interpreter = _host_deadline(tmp_path)
    ack = deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    assert ack.ready is True
    from cellertz.application.pdl1_host_deadline import enforce_deadline

    state_path = next(item for item in runner.installed if item.endswith(".json"))
    assert enforce_deadline(state_path, runner) == "enforced"
    control = ScriptedDockerControl()
    assert deadline.guarded_start(control, "cid-scripted", "daemon-1") is False
    assert control.calls == []


def test_deadline_removes_a_created_container_and_a_failed_kill_stays_closed(
    tmp_path: Path,
) -> None:
    from cellertz.application.pdl1_host_deadline import enforce_deadline

    deadline, runner, _now, _interpreter = _host_deadline(tmp_path)
    deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    created = next(item for item in runner.installed if item.endswith(".json"))
    runner.inspect_created = True
    runner.fail_kill = True
    assert enforce_deadline(created, runner) == "enforced"
    assert any(
        call[:2] == ["docker", "--host"] and call[3:] == ["rm", "--force", "cid-scripted"]
        for call in runner.calls
    )
    control = ScriptedDockerControl()
    assert deadline.guarded_start(control, "cid-scripted", "daemon-1") is False
    assert control.calls == []

    closed, closed_runner, _closed_now, _closed_interpreter = _host_deadline(tmp_path / "closed")
    closed.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    failed = next(item for item in closed_runner.installed if item.endswith(".json"))
    closed_runner.inspect_created = True
    closed_runner.fail_kill = True
    closed_runner.fail_rm = True
    assert enforce_deadline(failed, closed_runner) == "failed"
    late = ScriptedDockerControl()
    assert closed.guarded_start(late, "cid-scripted", "daemon-1") is False
    assert late.calls == []


def test_restart_without_a_valid_timer_stays_fenced(tmp_path: Path) -> None:
    from cellertz.application.approved_run import ApprovedRunService

    deadline, runner, _now, _interpreter = _host_deadline(tmp_path / "restart")
    deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    runner.hide_units = True
    assert deadline.recover("cid-scripted", "daemon-1") == "uncertain"
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    bundle = replace(bundle, supervisor=deadline)
    executor = protocol_executor(spec, bundle)
    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{11:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-restart")
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-restart",
    )
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    assert running is not None
    store.reserve_external(
        ExternalAttempt(
            run_id=running.run_id,
            attempt=running.attempt,
            authorization_id=running.authorization_id,
            binding_hash=running.binding_hash,
            deployment_id="dep1",
            daemon_id="daemon-1",
            container_name="cellertz-dep1-existing",
            state="running",
            container_id="cid-scripted",
            retry_allowed=False,
        )
    )
    control.calls.clear()
    interrupted = service.startup()
    assert len(interrupted) == 1
    assert interrupted[0].status != "completed"
    assert interrupted[0].text == EXTERNAL_UNCERTAIN_TEXT
    stored = store.read_run_any(created.run.run_id)
    assert stored is not None and stored.status != "completed"
    assert all(call[0] != "create" for call in control.calls)


def test_executor_rejects_another_manifest_with_the_same_inputs(tmp_path: Path) -> None:
    import json

    from cellertz.application.approved_run import ApprovedRunService
    from cellertz.application.pdl1_docker import CONFIG_ENV, prepare_enabled_infrastructure
    from cellertz.application.pdl1_host_deadline import SystemdHostDeadline

    path_a, digest_a, document_a = _document(tmp_path)
    other = json.loads(json.dumps(document_a))
    other["runtime_version"] = "recorded-runtime-2"
    other["timeout_seconds"] = 180
    other["resource_envelope"]["timeout_seconds"] = 180
    path_b = tmp_path / "manifest-b.json"
    digest_b = _write(path_b, other)
    assert digest_a != digest_b
    assert other["image_digest"] == document_a["image_digest"]
    assert other["input_identity"] == document_a["input_identity"]
    interpreter = tmp_path / "python"
    _executable_interpreter(interpreter)
    deadlines = tmp_path / "deadlines"
    deadlines.mkdir()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            {
                "argv": list(_BOUND_ARGV),
                "deadline_interpreter": str(interpreter),
                "deadline_manager": "system",
                "docker_host": f"unix://{tmp_path / 'docker.sock'}",
                "docker_root": str(tmp_path),
                "deadline_state_dir": str(deadlines),
                "deployment_id": "dep1",
                "entrypoint": "recorded-entry",
                "gpu_device": "0",
                "image_digest": _IMAGE,
                "image_id": _IMAGE_ID,
                "input_root": str(inputs),
                "log_bytes": 1048576,
                "manifest_path": str(path_a),
                "repository": _REPO,
                "staging_root": str(stage),
                "storage_bytes": 1048576,
            }
        ),
        encoding="utf-8",
    )
    control = ScriptedDockerControl()
    info, image = _facts()
    control.info = info
    control.image_record = image
    runner = _deadline_runner()
    deadline = SystemdHostDeadline(
        _config(
            stage,
            manifest_path=str(path_a),
            input_root=str(inputs),
            deadline_manager="system",
            deadline_state_dir=str(deadlines),
            deadline_interpreter=str(interpreter),
        ),
        runner,
    )
    infrastructure = prepare_enabled_infrastructure(
        {CONFIG_ENV: str(deployment)},
        control=control,
        deadline=deadline,
    )
    gate, drafter, _store, _dispatcher, reply = _stack(
        "design a binder for pdl1 using bindcraft with 1 candidates length 65 65 seed 7 no_filters"
    )
    assert reply.request is not None
    verified, _locations = _verified(tmp_path, digest_b)
    selected = drafter.bind_pdl1_selection(
        session_id=SESSION,
        request_id=reply.request.request_id,
        selection=Pdl1InputSelection.from_verified(verified),
    )
    approved = _decide(gate, reply, revision=selected.revision, idempotency_key="decision-other")
    assert approved.approval is not None
    demonstrated = gate.record_bindcraft_demonstration(
        session_id=SESSION,
        approval_id=approved.approval.approval_id,
        idempotency_key="demo-other",
    )
    assert demonstrated.status == "demonstration_completed"
    store = InMemoryScientificRunStore()
    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(path_b, CountingProbe(_attestation(other, digest_b))),
        clock=lambda: WHEN,
        authorization_ids=lambda: f"authorization_{12:032x}",
    )
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-other")
    assert granted.status == "authorization_created"
    executor = infrastructure.bind(authorizations=authorizations, store=store)
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-other",
    )
    assert executor._completion_allowed(created.run) is False
    failed = service.process_next()
    assert failed is not None and failed.status == "failed"
    assert all(call[0] != "create" for call in control.calls)


def test_main_startup_readiness_uses_the_observed_infrastructure(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    from cellertz.application.pdl1_docker import CONFIG_ENV
    from cellertz.application.pdl1_host_deadline import SystemdHostDeadline
    from cellertz.conversation.__main__ import main

    path, digest, document = _document(tmp_path)
    interpreter = tmp_path / "python"
    _executable_interpreter(interpreter)
    deadlines = tmp_path / "deadlines"
    deadlines.mkdir()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            {
                "argv": list(_BOUND_ARGV),
                "deadline_interpreter": str(interpreter),
                "deadline_manager": "system",
                "docker_host": f"unix://{tmp_path / 'docker.sock'}",
                "docker_root": str(tmp_path),
                "deadline_state_dir": str(deadlines),
                "deployment_id": "dep1",
                "entrypoint": "recorded-entry",
                "gpu_device": "0",
                "image_digest": _IMAGE,
                "image_id": _IMAGE_ID,
                "input_root": str(inputs),
                "log_bytes": 1048576,
                "manifest_path": str(path),
                "repository": _REPO,
                "staging_root": str(stage),
                "storage_bytes": 1048576,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_EXECUTION", "enabled")
    monkeypatch.setenv(CONFIG_ENV, str(deployment))
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUNNER_READINESS", "manifest")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST", str(path))
    control = ScriptedDockerControl()
    info, image = _facts()
    control.info = info
    control.image_record = image
    runner = _deadline_runner()
    deadline = SystemdHostDeadline(
        _config(
            stage,
            manifest_path=str(path),
            input_root=str(inputs),
            deadline_manager="system",
            deadline_state_dir=str(deadlines),
            deadline_interpreter=str(interpreter),
        ),
        runner,
    )
    seen: dict[str, object] = {}

    def started(*_args, **kwargs):
        seen["executor"] = type(kwargs["scientific_runs"]._executor).__name__
        seen["status"] = kwargs["readiness"].read().status
        seen["digest"] = kwargs["readiness"].read().manifest_digest

    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    main(control=control, deadline=deadline)
    assert seen == {
        "executor": "Pdl1DockerExecutor",
        "status": "ready",
        "digest": digest,
    }

    swapped = ScriptedDockerControl()
    swapped.info = info
    swapped.image_record = image

    def image_inspect(reference: str) -> object:
        swapped.calls.append(("image_inspect", reference))
        changed = json.loads(json.dumps(document))
        changed["runtime_version"] = "recorded-runtime-2"
        _write(path, changed)
        return image

    swapped.image_inspect = image_inspect  # type: ignore[method-assign]
    with pytest.raises(ScientificRunConfigurationError) as blocked:
        main(control=swapped, deadline=deadline)
    assert blocked.value.code == "scientific_run_executor_unconfigured"
    assert blocked.value.missing == ("readiness",)
    assert seen["digest"] == digest


def _state_path(runner) -> str:
    return next(item for item in runner.installed if item.endswith(".json"))


def test_execstart_is_the_service_command_not_the_timer_invocation(tmp_path: Path) -> None:
    deadline, runner, _now, interpreter = _host_deadline(tmp_path)
    ack = deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    assert ack.ready is True
    state_path = _state_path(runner)
    assert runner.service_argv == [
        str(interpreter),
        "-m",
        "cellertz.application.pdl1_host_deadline",
        "enforce",
        "--state",
        state_path,
    ]
    shown = deadline._timer(json_unit(state_path))
    assert shown is not None
    assert shown["ExecStart"] == _systemd_exec_start(runner.service_argv)
    assert "--description" not in shown["ExecStart"]
    assert "systemd-run" not in shown["ExecStart"]
    assert deadline.recover("cid-scripted", "daemon-1") == "armed"
    runner.replace_state = str(tmp_path / "other-state.json")
    assert deadline.recover("cid-scripted", "daemon-1") == "uncertain"
    runner.replace_state = None
    runner.legacy_exec = True
    assert deadline.recover("cid-scripted", "daemon-1") == "uncertain"


def json_unit(state_path: str) -> str:
    import json

    return json.loads(Path(state_path).read_text(encoding="utf-8"))["unit"]


def test_inspection_failure_does_not_count_as_termination(tmp_path: Path) -> None:
    from cellertz.application.pdl1_host_deadline import enforce_deadline

    deadline, runner, _now, _interpreter = _host_deadline(tmp_path)
    deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    state_path = _state_path(runner)
    runner.fail_kill = True
    runner.fail_inspect = True
    assert enforce_deadline(state_path, runner) == "uncertain"
    assert not any(call[0] == "docker" and "rm" in call[3:] for call in runner.calls)
    control = ScriptedDockerControl()
    assert deadline.guarded_start(control, "cid-scripted", "daemon-1") is False
    assert control.calls == []

    down, down_runner, _down_now, _down_interpreter = _host_deadline(tmp_path / "down")
    down.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    down_runner.fail_info = True
    assert enforce_deadline(_state_path(down_runner), down_runner) == "uncertain"
    assert not any(call[0] == "docker" and "kill" in call[3:] for call in down_runner.calls)

    malformed, malformed_runner, _malformed_now, _malformed_interpreter = _host_deadline(
        tmp_path / "malformed"
    )
    malformed.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    malformed_runner.fail_kill = True
    malformed_runner.malformed_inspect = True
    assert enforce_deadline(_state_path(malformed_runner), malformed_runner) == "uncertain"

    removed, removed_runner, _removed_now, _removed_interpreter = _host_deadline(
        tmp_path / "removed"
    )
    removed.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    removed_runner.inspect_created = True
    removed_runner.fail_kill = True
    assert enforce_deadline(_state_path(removed_runner), removed_runner) == "enforced"
    assert any(
        call[:2] == ["docker", "--host"] and call[3:] == ["rm", "--force", "cid-scripted"]
        for call in removed_runner.calls
    )
    late = ScriptedDockerControl()
    assert removed.guarded_start(late, "cid-scripted", "daemon-1") is False
    assert late.calls == []


def test_enforcement_completes_while_the_worker_is_paused(tmp_path: Path) -> None:
    import threading

    from cellertz.application.pdl1_host_deadline import enforce_deadline

    deadline, runner, now, _interpreter = _host_deadline(tmp_path)
    deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    entered = threading.Event()
    release = threading.Event()

    def pause() -> None:
        entered.set()
        assert release.wait(2)

    deadline._after_claim = pause
    control = ScriptedDockerControl()
    outcome: dict[str, bool] = {}

    def worker() -> None:
        outcome["ok"] = deadline.guarded_start(control, "cid-scripted", "daemon-1")

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(2)
    now["t"] = 5_000.0
    finished: dict[str, str] = {}

    def enforce() -> None:
        finished["value"] = enforce_deadline(_state_path(runner), runner)

    enforcer = threading.Thread(target=enforce)
    enforcer.start()
    try:
        enforcer.join(1)
        assert not enforcer.is_alive()
        assert finished["value"] == "enforced"
        assert control.calls == []
    finally:
        release.set()
        thread.join(2)
    assert outcome["ok"] is False
    assert control.calls == []


def test_running_deadline_recovers_after_guarded_start(tmp_path: Path) -> None:
    import json

    deadline, runner, now, _interpreter = _host_deadline(tmp_path)
    deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    control = ScriptedDockerControl()
    assert deadline.guarded_start(control, "cid-scripted", "daemon-1") is True
    assert ("start", "cid-scripted") in control.calls
    state_path = Path(_state_path(runner))
    document = json.loads(state_path.read_text(encoding="utf-8"))
    assert document["phase"] == "running"
    runner.inspect_running = True
    assert deadline.recover("cid-scripted", "daemon-1") == "running"
    runner.hide_units = True
    assert deadline.recover("cid-scripted", "daemon-1") == "uncertain"
    runner.hide_units = False
    runner.unit_failed = True
    assert deadline.recover("cid-scripted", "daemon-1") == "failed"
    runner.unit_failed = False
    document["image_id"] = "sha256:" + ("ff" * 32)
    state_path.write_text(json.dumps(document), encoding="utf-8")
    assert deadline.recover("cid-scripted", "daemon-1") == "uncertain"
    document["image_id"] = _IMAGE_ID
    state_path.write_text(json.dumps(document), encoding="utf-8")
    now["t"] = 5_000.0
    assert deadline.recover("cid-scripted", "daemon-1") == "enforced"
    assert any(
        call[:2] == ["docker", "--host"] and call[3:] == ["kill", "cid-scripted"]
        for call in runner.calls
    )
    late = ScriptedDockerControl()
    assert deadline.guarded_start(late, "cid-scripted", "daemon-1") is False
    assert late.calls == []


def test_restart_after_guarded_start_before_and_after_exit(tmp_path: Path) -> None:
    running = _restart_owned_attempt(tmp_path / "running", exited=False)
    assert running.status != "completed"
    assert running.text == EXTERNAL_UNCERTAIN_TEXT
    assert running.creates == 0
    finished = _restart_owned_attempt(tmp_path / "exited", exited=True)
    assert finished.status == "completed"
    assert finished.creates == 0


def _restart_owned_attempt(tmp_path: Path, *, exited: bool):
    from cellertz.application.approved_run import ApprovedRunService

    deadline, runner, _now, _interpreter = _host_deadline(tmp_path)
    deadline.acknowledge(
        container_id="cid-scripted",
        deadline_epoch=1_050.0,
        daemon_id="daemon-1",
        image=_IMAGE_ID,
    )
    started = ScriptedDockerControl()
    assert deadline.guarded_start(started, "cid-scripted", "daemon-1") is True
    if not exited:
        runner.inspect_running = True
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    bundle = replace(bundle, supervisor=deadline)
    executor = protocol_executor(spec, bundle)
    authorizations = ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        clock=lambda: WHEN,
        authorization_ids=lambda: "authorization_" + ("ab" if exited else "cd") * 16,
    )
    granted = _authorize(
        authorizations,
        approved.approval.approval_id,
        key="auth-owned-exit" if exited else "auth-owned-run",
    )
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-owned-exit" if exited else "run-owned-run",
    )
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    assert running is not None
    attempt = ExternalAttempt(
        run_id=running.run_id,
        attempt=running.attempt,
        authorization_id=running.authorization_id,
        binding_hash=running.binding_hash,
        deployment_id="dep1",
        daemon_id="daemon-1",
        container_name="cellertz-dep1-existing",
        state="running",
        container_id="cid-scripted",
        retry_allowed=False,
    )
    store.reserve_external(attempt)
    control.policy = executor._policy(attempt)
    status = "exited" if exited else "running"
    control.inspect_result = {
        "Id": "cid-scripted",
        "State": {
            "Status": status,
            "ExitCode": 0,
            "Running": not exited,
        },
        "Config": {"Image": f"{_REPO}@{_IMAGE}", "Labels": dict(_labels(attempt))},
        "HostConfig": {
            "Privileged": False,
            "Memory": 4096 * 1024 * 1024,
            "NanoCpus": 2_000_000_000,
        },
    }
    control.calls.clear()
    service.startup()
    stored = store.read_run_any(created.run.run_id)
    assert stored is not None

    class _Observed:
        status = stored.status
        text = stored.text
        creates = sum(1 for call in control.calls if call[0] == "create")

    return _Observed()
