"""Simulated approval checks for seed and filter policy. No daemon or GPU."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from cellertz.application.approval import canonical_digest, revision_document
from cellertz.application.approved_run import ApprovedRunService
from cellertz.application.pdl1_container_entrypoint import Pdl1EntrypointError, parse_policy
from cellertz.application.pdl1_docker import (
    ScriptedDockerControl,
    container_arguments_agree,
    protocol_executor,
)
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DeterministicDraftExtractor,
    ProteinDesignRequest,
    _request_from,
)
from cellertz.application.scientific_authorization import (
    ScientificAuthorizationService,
    public_authorization,
)
from cellertz.application.scientific_authorization_store import (
    SQLiteScientificAuthorizationStore,
)
from cellertz.application.scientific_run import (
    FAILED_TEXT,
    InMemoryScientificRunStore,
    ScientificRunService,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import CONTAINER_NO_FILTERS
from tests.test_design_request import ScriptedExtractor, _ask, _patch, _service
from tests.test_human_approval import _decide
from tests.test_pdl1_docker_executor import (
    _BOUND_ARGV,
    _LEGACY_MESSAGE,
    _POLICY_MESSAGE,
    _attestation_from_path,
    _bundle,
)
from tests.test_scientific_authorization import SESSION, WHEN, _authorize
from tests.test_scientific_runner_readiness import CountingProbe, _load

RESULT_LABEL = "simulated"


def _complete(*pairs: tuple[str, object]):
    return _patch(
        ("target_id", "PDL1"),
        ("objective", "binder_design"),
        ("generator", "bindcraft"),
        ("n_candidates", 1),
        ("length_min", 65),
        ("length_max", 65),
        *pairs,
    )


def test_seed_and_filter_policy_change_the_revision_digest() -> None:
    assert RESULT_LABEL == "simulated"
    bare = _service(ScriptedExtractor([_complete()]))
    recorded = _service(
        ScriptedExtractor([_complete(("seed", 7), ("filter_policy", "no_filters"))])
    )
    other_seed = _service(
        ScriptedExtractor([_complete(("seed", 8), ("filter_policy", "no_filters"))])
    )
    other_filter = _service(
        ScriptedExtractor([_complete(("seed", 7), ("filter_policy", "default"))])
    )
    first = _ask(bare, "design a binder")
    second = _ask(recorded, "design a binder")
    changed = _ask(other_seed, "design a binder")
    rejected = _ask(other_filter, "design a binder")
    assert first.request is not None and second.request is not None
    assert first.request.filter_policy is None
    assert "seed" not in dict(first.request.constraints)
    assert second.request.filter_policy == "no_filters"
    assert dict(second.request.constraints)["seed"] == 7
    assert "filter_policy" not in first.request.to_dict()
    assert second.request.to_dict()["filter_policy"] == "no_filters"
    left = canonical_digest(revision_document(first.draft, first.request, _budget()))
    right = canonical_digest(revision_document(second.draft, second.request, _budget()))
    assert left != right
    assert changed.request is not None
    shifted = canonical_digest(revision_document(changed.draft, changed.request, _budget()))
    assert shifted != right
    assert rejected.request is not None
    assert rejected.request.filter_policy is None
    assert any(
        issue.field == "filter_policy" and issue.code == "invalid" for issue in rejected.issues
    )


def test_boolean_and_overlong_seed_are_rejected() -> None:
    assert RESULT_LABEL == "simulated"
    boolean = _ask(_service(ScriptedExtractor([_complete(("seed", True))])), "design a binder")
    huge = _ask(_service(ScriptedExtractor([_complete(("seed", 4294967296))])), "design a binder")
    zero = _ask(_service(ScriptedExtractor([_complete(("seed", 0))])), "design a binder")
    assert boolean.request is not None and "seed" not in dict(boolean.request.constraints)
    assert any(issue.field == "seed" and issue.code == "invalid" for issue in boolean.issues)
    assert huge.request is not None and "seed" not in dict(huge.request.constraints)
    assert any(issue.field == "seed" and issue.code == "invalid" for issue in huge.issues)
    assert zero.request is not None and dict(zero.request.constraints)["seed"] == 0


def test_legacy_phrase_does_not_invent_policy() -> None:
    assert RESULT_LABEL == "simulated"
    reply = _ask(_service(DeterministicDraftExtractor()), _LEGACY_MESSAGE)
    assert reply.request is not None
    assert reply.request.filter_policy is None
    assert "seed" not in dict(reply.request.constraints)
    document = revision_document(reply.draft, reply.request, _budget())
    assert "filter_policy" not in document["request"]
    assert "seed" not in document["request"]["constraints"]
    assert "seed" not in {field["name"] for field in document["fields"]}


def test_legacy_request_stops_before_docker_create(tmp_path: Path) -> None:
    assert RESULT_LABEL == "simulated"
    store = InMemoryScientificRunStore()
    from cellertz.application.pdl1_docker import ScriptedDockerControl, protocol_executor

    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, selected = _bundle(
        tmp_path, control, store, _LEGACY_MESSAGE
    )
    request = _request_from(selected)
    assert request is not None and request.filter_policy is None
    assert "seed" not in dict(request.constraints)
    executor = protocol_executor(spec, bundle)
    authorizations = _authorizations(gate, tmp_path, f"authorization_{11:032x}")
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-legacy")
    assert granted.authorization is not None
    assert granted.authorization.request_digest == canonical_digest(
        revision_document(selected, request, gate._budget(SESSION))
    )
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
        idempotency_key="run-legacy",
    )
    finished = service.process_next()
    assert finished is not None
    assert finished.status == "failed"
    assert finished.text == FAILED_TEXT
    assert all(call[0] != "create" for call in control.calls)
    assert _BOUND_ARGV == bundle.config.argv


def test_revised_seed_needs_a_new_approval_before_create(tmp_path: Path) -> None:
    assert RESULT_LABEL == "simulated"
    from cellertz.application.pdl1_docker import ScriptedDockerControl, protocol_executor

    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, _selected = _bundle(tmp_path, control, store)
    executor = protocol_executor(spec, bundle)
    authorizations = _authorizations(gate, tmp_path, f"authorization_{12:032x}")
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-seed")
    revised = gate._drafter.revise(
        DesignDraftCommand(
            SESSION,
            _POLICY_MESSAGE.replace("seed 7", "seed 8"),
            request_id=approved.approval.request_id,
            revision=approved.approval.request_revision,
        )
    )
    assert revised.request is not None
    assert dict(revised.request.constraints)["seed"] == 8
    fresh = canonical_digest(
        revision_document(revised.draft, revised.request, gate._budget(SESSION))
    )
    assert fresh != approved.approval.revision_digest
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
        idempotency_key="run-revised",
    )
    assert created.status == "authorization_unavailable"
    assert created.run is None
    assert all(call[0] != "create" for call in control.calls)
    again = _decide(gate, revised, idempotency_key="decision-seed-8")
    assert again.approval is not None
    assert again.approval.revision_digest == fresh
    assert again.approval.approval_id != approved.approval.approval_id


def test_exact_adapter_command_is_accepted_by_checker_and_entrypoint() -> None:
    request = ProteinDesignRequest(
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
    assert container_arguments_agree(request, _BOUND_ARGV) is True
    policy = parse_policy(list(_BOUND_ARGV), filters_path=CONTAINER_NO_FILTERS)
    assert (policy.n_designs, policy.length_min, policy.length_max, policy.seed) == (
        1,
        65,
        65,
        7,
    )
    assert policy.filters == CONTAINER_NO_FILTERS
    with pytest.raises(Pdl1EntrypointError) as caught:
        parse_policy(
            ["--recorded-template", *list(_BOUND_ARGV)],
            filters_path=CONTAINER_NO_FILTERS,
        )
    assert caught.value.code == "unsupported_argument"


def test_out_of_range_approved_values_stop_before_docker_create(tmp_path: Path) -> None:
    assert RESULT_LABEL == "simulated"
    count = tmp_path / "count"
    length = tmp_path / "length"
    count.mkdir()
    length.mkdir()
    _refused_before_create(
        count,
        _POLICY_MESSAGE.replace("with 1 candidates", "with 65 candidates"),
        _adapter_argv("65", "65", "65"),
        f"authorization_{14:032x}",
        "run-count",
        designs=65,
        low=65,
        high=65,
    )
    _refused_before_create(
        length,
        _POLICY_MESSAGE.replace("length 65 65", "length 2147483648 2147483648"),
        _adapter_argv("1", "2147483648", "2147483648"),
        f"authorization_{15:032x}",
        "run-length",
        designs=1,
        low=2147483648,
        high=2147483648,
    )


def test_sqlite_restart_restores_approval_identity_only(tmp_path: Path) -> None:
    """Restart restores the authorization digest and binding hash.

    The authorization document does not store the draft or the request,
    so seed and filter_policy are not recovered.
    """

    assert RESULT_LABEL == "simulated"
    path = tmp_path / "policy-authorizations.sqlite3"
    database = SQLiteScientificAuthorizationStore(path)
    control = ScriptedDockerControl()
    gate, approved, _bundle_value, _spec, _snapshot, selected = _bundle(
        tmp_path, control, InMemoryScientificRunStore()
    )
    authorizations = _authorizations(gate, tmp_path, f"authorization_{13:032x}", store=database)
    granted = _authorize(authorizations, approved.approval.approval_id, key="auth-store")
    request = _request_from(selected)
    digest = canonical_digest(revision_document(selected, request, gate._budget(SESSION)))
    assert granted.authorization is not None
    assert granted.authorization.request_digest == digest
    authorizations.close()
    reopened = SQLiteScientificAuthorizationStore(path)
    restored = reopened.read(SESSION, granted.authorization.authorization_id)
    assert restored is not None
    assert restored.request_digest == digest
    assert restored.binding_hash == granted.authorization.binding_hash
    assert restored.approval_id == granted.authorization.approval_id
    public = public_authorization(restored)
    assert "filter_policy" not in public
    assert "constraints" not in public
    raw = sqlite3.connect(path).execute("SELECT document FROM authorizations").fetchone()
    assert raw is not None
    stored = json.loads(raw[0])
    assert _nested_keys(stored).isdisjoint({"seed", "filter_policy", "constraints"})
    reopened.close()


def _adapter_argv(designs: str, low: str, high: str) -> tuple[str, ...]:
    return (
        "--n-designs",
        designs,
        "--length-min",
        low,
        "--length-max",
        high,
        "--cellertz-seed",
        "7",
        "--filters",
        CONTAINER_NO_FILTERS,
    )


def _refused_before_create(
    tmp_path: Path,
    message: str,
    argv: tuple[str, ...],
    authorization_id: str,
    key: str,
    *,
    designs: int,
    low: int,
    high: int,
) -> None:
    store = InMemoryScientificRunStore()
    control = ScriptedDockerControl()
    gate, approved, bundle, spec, _snapshot, selected = _bundle(tmp_path, control, store, message)
    request = _request_from(selected)
    assert request is not None
    assert request.n_candidates == designs
    assert dict(request.constraints)["length_min"] == low
    assert dict(request.constraints)["length_max"] == high
    assert argv[1] == str(designs)
    assert argv[3] == str(low)
    assert argv[5] == str(high)
    assert container_arguments_agree(request, argv) is False
    bundle = replace(bundle, config=replace(bundle.config, argv=argv))
    spec = replace(spec, recorded_argv_template=argv)
    executor = protocol_executor(spec, bundle)
    authorizations = _authorizations(gate, tmp_path, authorization_id)
    granted = _authorize(authorizations, approved.approval.approval_id, key=key)
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
        idempotency_key=key,
    )
    assert created.run is not None
    finished = service.process_next()
    assert finished is not None
    assert finished.status == "failed"
    assert all(call[0] != "create" for call in control.calls)


def _nested_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(str(key) for key in value)
        for item in value.values():
            found.update(_nested_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_nested_keys(item))
    return found


def _budget():
    from cellertz.application.fixtures import pdl1_prototype_session

    return pdl1_prototype_session().budget


def _authorizations(gate, tmp_path: Path, authorization_id: str, store=None):
    return ScientificAuthorizationService(
        gate._sessions,
        gate._drafter,
        gate,
        ApprovedRunService(gate, clock=lambda: WHEN),
        _load(
            tmp_path / "manifest.json",
            CountingProbe(_attestation_from_path(tmp_path / "manifest.json")),
        ),
        store=store,
        clock=lambda: WHEN,
        authorization_ids=lambda: authorization_id,
    )
