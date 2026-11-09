"""Phase 6C: conversational ProteinDesignRequest drafts stay unauthorized."""

from __future__ import annotations

import inspect
import json
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.conversation import (
    CONVERSATIONAL_READONLY_CAPABILITIES,
    ListUsageRecorder,
    ModelUsageReport,
    QueryPage,
    ReadOnlyConversation,
    ReadOnlyFact,
    RecordedFactsModel,
)
from cellertz.application.conversation_transport import configured_handler
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DesignDrafting,
    DeterministicDraftExtractor,
    DraftPatch,
    FieldProposal,
    ProteinDesignRequest,
    ProteinDesignRequestDraft,
)
from cellertz.application.request_transport import handle_design_draft

WHEN = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
SESSION = "session_pdl1_prototype"


class ScriptedExtractor:
    def __init__(self, patches: list[DraftPatch], error: BaseException | None = None) -> None:
        self.patches = list(patches)
        self.error = error
        self.calls = 0

    def extract(self, *, message: str, draft: ProteinDesignRequestDraft | None) -> DraftPatch:
        del message, draft
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.patches.pop(0)


def _service(
    extractor: ScriptedExtractor | DeterministicDraftExtractor,
    recorder: ListUsageRecorder | None = None,
) -> DesignDrafting:
    session = pdl1_prototype_session()
    return DesignDrafting(
        {session.session_id: session},
        extractor,
        recorder=recorder if recorder is not None else ListUsageRecorder(),
        clock=lambda: WHEN,
        interaction_ids=lambda: "interaction_1",
        request_ids=lambda: "request_1",
    )


def _ask(
    service: DesignDrafting,
    message: str,
    request_id: str | None = None,
    revision: int | None = None,
    session_id: str = SESSION,
) -> object:
    return service.revise(
        DesignDraftCommand(session_id, message, request_id=request_id, revision=revision)
    )


def _patch(*pairs: tuple[str, object]) -> DraftPatch:
    return DraftPatch(tuple(FieldProposal(name, value) for name, value in pairs))


def _fields(reply: object) -> dict[str, dict[str, object]]:
    assert reply.draft is not None  # type: ignore[attr-defined]
    return {field.name: field.to_dict() for field in reply.draft.fields}  # type: ignore[attr-defined]


def test_valid_message_creates_an_incomplete_draft() -> None:
    service = _service(ScriptedExtractor([_patch(("target_id", "PDL1"), ("objective", "binder_design"))]))
    reply = _ask(service, "Quero desenhar binders para PDL1")
    assert reply.status == "draft_created"
    assert reply.draft is not None
    assert reply.draft.revision == 1
    assert reply.request is None
    assert reply.execution_authorized is False
    assert reply.executable is False
    fields = _fields(reply)
    assert fields["target_id"] == {"name": "target_id", "value": "PDL1", "provenance": "user"}
    assert fields["objective"]["provenance"] == "user"
    assert reply.changed_fields == ("target_id", "objective")


def test_update_preserves_the_previous_revision_object() -> None:
    extractor = ScriptedExtractor(
        [
            _patch(("target_id", "PDL1"), ("objective", "binder_design")),
            _patch(("generator", "bindcraft")),
        ]
    )
    service = _service(extractor)
    first = _ask(service, "Quero desenhar binders para PDL1")
    before = first.draft.to_dict()
    second = _ask(service, "use bindcraft", request_id="request_1", revision=1)
    assert first.draft.to_dict() == before
    assert first.draft.revision == 1
    assert second.status == "draft_updated"
    assert second.draft.revision == 2
    assert _fields(second)["target_id"]["value"] == "PDL1"
    assert _fields(second)["target_id"]["provenance"] == "preserved"
    assert _fields(second)["generator"] == {
        "name": "generator",
        "value": "bindcraft",
        "provenance": "user",
    }
    assert "target_id" not in second.changed_fields


def test_unknown_and_invalid_values_do_not_revise() -> None:
    extractor = ScriptedExtractor(
        [
            DraftPatch((FieldProposal("ranking_score", 0.9), FieldProposal("not_a_field", 1))),
            _patch(("n_candidates", -3), ("n_candidates", True)),
        ]
    )
    service = _service(extractor)
    unknown = _ask(service, "Quero desenhar binders para PDL1")
    assert unknown.status == "invalid_patch"
    assert unknown.draft is None
    assert {issue.field for issue in unknown.issues} >= {"ranking_score", "not_a_field"}
    invalid = _ask(service, "Quero desenhar binders")
    assert invalid.status == "invalid_patch"
    assert invalid.draft is None
    assert any(issue.field == "n_candidates" and issue.code == "invalid" for issue in invalid.issues)


def test_missing_required_fields_ask_only_for_those_fields() -> None:
    extractor = ScriptedExtractor(
        [
            _patch(("target_id", "PDL1"), ("objective", "binder_design")),
            _patch(("generator", "bindcraft")),
        ]
    )
    service = _service(extractor)
    first = _ask(service, "Quero desenhar binders para PDL1")
    asked = {item.field for item in first.questions}
    assert asked == {"generator", "n_candidates"}
    assert all(item.question for item in first.questions)
    second = _ask(service, "bindcraft", request_id="request_1", revision=1)
    assert {item.field for item in second.questions} == {"n_candidates"}
    assert _fields(second)["target_id"]["provenance"] == "preserved"


def test_a_candidate_count_answer_revises_the_same_request() -> None:
    service = _service(DeterministicDraftExtractor())
    opened = _ask(service, "#2 design an new binder to PDL1\nbindcraft")
    assert opened.status == "draft_updated" or opened.status == "draft_created"  # type: ignore[attr-defined]
    assert opened.draft.request_id == "request_1"  # type: ignore[attr-defined]
    assert _fields(opened)["n_candidates"]["value"] is None
    assert _fields(opened)["generator"]["value"] == "bindcraft"
    stale = _ask(service, "#2 design an new binder to PDL1\nbindcraft\n1", "request_1", 2)
    assert stale.status == "revision_conflict"  # type: ignore[attr-defined]
    assert stale.draft.revision == 1  # type: ignore[attr-defined]
    answered = _ask(service, "#2 design an new binder to PDL1\nbindcraft\n1", "request_1", 1)
    assert answered.status == "request_complete"  # type: ignore[attr-defined]
    assert answered.draft.request_id == "request_1"  # type: ignore[attr-defined]
    assert answered.draft.revision == 2  # type: ignore[attr-defined]
    assert _fields(answered)["n_candidates"] == {
        "name": "n_candidates",
        "value": 1,
        "provenance": "user",
    }
    assert answered.execution_authorized is False  # type: ignore[attr-defined]
    assert answered.executable is False  # type: ignore[attr-defined]
    assert len(service.revisions("request_1")) == 2
    patch = DeterministicDraftExtractor().extract(message="#2 design an new binder to PDL1", draft=None)
    assert all(item.name != "n_candidates" for item in patch.proposals)


def test_documented_defaults_are_not_user_facts_and_lengths_stay_absent() -> None:
    service = _service(DeterministicDraftExtractor())
    reply = _ask(
        service,
        "Quero desenhar binders para PDL1, priorizando ipSAE e mantendo Boltz2 somente como evidência auxiliar.",
    )
    fields = _fields(reply)
    assert fields["target_id"]["provenance"] == "user"
    assert fields["objective"]["value"] == "binder_design"
    assert fields["ranking_signal"] == {
        "name": "ranking_signal",
        "value": "raw_ipsae",
        "provenance": "documented_default",
    }
    assert fields["boltz_role"]["value"] == "auxiliary_evidence"
    assert fields["boltz_role"]["provenance"] == "documented_default"
    assert fields["length_min"]["value"] is None
    assert fields["length_max"]["value"] is None
    assert fields["n_candidates"]["value"] is None
    assert fields["generator"]["value"] is None
    assert reply.execution_authorized is False


def test_punctuation_does_not_hide_documented_words() -> None:
    service = _service(DeterministicDraftExtractor())
    reply = _ask(
        service,
        "Design a PDL1 binder with bindcraft, 1 candidates, length 65 65, seed 1, no_filters.",
    )
    fields = _fields(reply)
    assert fields["target_id"]["value"] == "PDL1"
    assert fields["objective"]["value"] == "binder_design"
    assert fields["generator"]["value"] == "bindcraft"
    assert fields["n_candidates"]["value"] == 1
    assert fields["length_min"]["value"] == 65
    assert fields["length_max"]["value"] == 65
    assert fields["seed"]["value"] == 1
    assert fields["filter_policy"]["value"] == "no_filters"
    assert reply.status == "request_complete"
    assert reply.execution_authorized is False


def test_complete_request_stays_unauthorized() -> None:
    service = _service(
        ScriptedExtractor(
            [
                _patch(
                    ("target_id", "PDL1"),
                    ("objective", "binder_design"),
                    ("generator", "bindcraft"),
                    ("n_candidates", 4),
                )
            ]
        )
    )
    reply = _ask(service, "Quero desenhar binders para PDL1")
    assert reply.status == "request_complete"
    assert reply.request is not None
    assert reply.request.execution_authorized is False
    assert reply.request.executable is False
    payload = reply.request.generation_payload()
    assert payload["target_id"] == "PDL1"
    assert payload["n_candidates"] == 4
    assert "execution_authorized" not in payload
    assert "ranking_score" not in reply.request.to_dict()
    assert "composite_score" not in reply.request.to_dict()
    with pytest.raises(ApplicationContractError) as caught:
        ProteinDesignRequest(
            request_id="request_1",
            session_id=SESSION,
            revision=1,
            target_id="PDL1",
            objective="binder_design",
            generator="bindcraft",
            n_candidates=4,
            constraints=(),
            execution_authorized=True,
        )
    assert caught.value.code == "request_is_not_authorized"


@pytest.mark.parametrize("message", ["confirmo", "confirm", "looks good"])
def test_isolated_confirmation_keeps_the_draft(message: str) -> None:
    extractor = ScriptedExtractor(
        [
            _patch(
                ("target_id", "PDL1"),
                ("objective", "binder_design"),
                ("generator", "bindcraft"),
                ("n_candidates", 2),
            )
        ]
    )
    service = _service(extractor)
    created = _ask(service, "Quero desenhar binders para PDL1")
    before = created.draft.to_dict()
    reply = _ask(service, message, request_id="request_1", revision=1)
    assert reply.status == "confirmation_not_available_until_phase_6d"
    assert reply.text == "Confirmation is not available until Phase 6D. The draft was kept."
    assert reply.execution_authorized is False
    assert reply.executable is False
    assert reply.request is None
    assert reply.draft is not None
    assert reply.draft.to_dict() == before
    assert extractor.calls == 1


@pytest.mark.parametrize(
    "message",
    [
        "approve and run",
        "execute",
        "start BindCraft",
        "promote this into the second brain",
        "ingest the run into memory",
    ],
)
def test_execution_orders_stay_forbidden(message: str) -> None:
    extractor = ScriptedExtractor(
        [
            _patch(
                ("target_id", "PDL1"),
                ("objective", "binder_design"),
                ("generator", "bindcraft"),
                ("n_candidates", 2),
            ),
            _patch(("execution_authorized", True)),
        ]
    )
    service = _service(extractor)
    created = _ask(service, "Quero desenhar binders para PDL1")
    assert created.status == "request_complete"
    before = created.draft.revision
    blocked = _ask(service, message, request_id="request_1", revision=1)
    assert blocked.status == "forbidden_action"
    assert blocked.execution_authorized is False
    assert blocked.executable is False
    assert blocked.request is None
    assert blocked.draft is not None
    assert blocked.draft.revision == before
    assert extractor.calls == 1


def test_drafting_has_no_execution_port_and_writes_no_scientific_memory() -> None:
    source = Path("src/cellertz/application/request_draft.py").read_text(encoding="utf-8")
    assert "ProteinRunStarter" not in source
    assert "ProteinSupervisor" not in source
    assert "ScientificRunMemory" not in source
    assert "start_fixture_run" not in source
    assert "promote_memory" not in source
    assert "cellertz.runs" not in source
    assert "cellertz.execution" not in source
    assert "executor" not in inspect.signature(DesignDrafting.__init__).parameters
    extractor = ScriptedExtractor([_patch(("target_id", "PDL1"))])
    service = _service(extractor)
    _ask(service, "execute the run")
    assert extractor.calls == 0


def test_adapter_failure_records_nothing() -> None:
    recorder = ListUsageRecorder()
    service = _service(
        ScriptedExtractor([], error=RuntimeError("secret-backend")),
        recorder,
    )
    reply = _ask(service, "Quero desenhar binders para PDL1")
    assert reply.status == "adapter_failure"
    assert "secret-backend" not in reply.text
    assert reply.draft is None
    assert recorder.events == []


def test_valid_usage_is_recorded_and_invalid_usage_is_not() -> None:
    recorder = ListUsageRecorder()
    valid = ModelUsageReport(
        provider="fake",
        model="fake-1",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
        cached_input_tokens=1,
    )
    service = _service(
        ScriptedExtractor([DraftPatch((FieldProposal("target_id", "PDL1"),), valid)]),
        recorder,
    )
    reply = _ask(service, "Quero desenhar binders para PDL1")
    assert reply.status == "draft_created"
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event.provider == "fake"
    assert event.input_tokens == 11
    assert event.output_tokens == 7
    assert event.cached_input_tokens == 1
    assert event.run_id is None
    assert event.agent_id == "design_request_draft"
    assert event.stage == "request_draft"
    assert reply.instrumentation.total_tokens == 18
    absent = ModelUsageReport(provider="fake", model="fake-1", input_tokens=2, output_tokens=3)
    service = _service(
        ScriptedExtractor([DraftPatch((FieldProposal("objective", "binder_design"),), absent)]),
        recorder,
    )
    second = _ask(service, "desenhar binders", request_id=None)
    assert second.instrumentation.total_tokens is None
    invalid = ModelUsageReport(provider="fake", model="fake-1", total_tokens=-1)
    before = len(recorder.events)
    service = _service(
        ScriptedExtractor([DraftPatch((FieldProposal("target_id", "BCMA"),), invalid)]),
        recorder,
    )
    failed = _ask(service, "Quero desenhar binders para BCMA")
    assert failed.status == "adapter_failure"
    assert failed.draft is None
    assert len(recorder.events) == before


def test_missing_session_and_missing_revision_skip_the_extractor() -> None:
    extractor = ScriptedExtractor([_patch(("target_id", "PDL1"))])
    service = _service(extractor)
    missing = _ask(service, "Quero desenhar binders para PDL1", session_id="session_missing")
    assert missing.status == "session_not_found"
    created = _ask(service, "Quero desenhar binders para PDL1")
    assert created.draft is not None
    stale = _ask(service, "bindcraft", request_id="request_missing", revision=1)
    assert stale.status == "draft_not_found"
    wrong = _ask(service, "bindcraft", request_id="request_1", revision=4)
    assert wrong.status == "revision_conflict"
    assert extractor.calls == 1


def test_invalid_json_and_client_authorization_flags() -> None:
    service = _service(ScriptedExtractor([_patch(("target_id", "PDL1"))]))
    status, body = handle_design_draft(service, b"{")
    assert status == 400
    assert body["status"] == "unsupported_message"
    assert "Traceback" not in json.dumps(body)
    flagged, blocked = handle_design_draft(
        service,
        json.dumps(
            {
                "session_id": SESSION,
                "message": "Quero desenhar binders para PDL1",
                "execution_authorized": True,
                "executable": True,
            }
        ).encode(),
    )
    assert flagged == 400
    assert blocked["status"] == "forbidden_action"
    assert blocked["execution_authorized"] is False
    assert blocked["executable"] is False
    assert blocked["draft"] is None


def test_http_draft_uses_the_session_and_returns_a_revision() -> None:
    service = _service(DeterministicDraftExtractor())
    status, body = handle_design_draft(
        service,
        json.dumps(
            {"session_id": SESSION, "message": "Quero desenhar binders para PDL1"}
        ).encode(),
    )
    assert status == 200
    assert body["status"] == "draft_created"
    assert body["draft"]["session_id"] == SESSION
    assert body["draft"]["revision"] == 1
    assert body["execution_authorized"] is False
    names = {field["name"]: field for field in body["draft"]["fields"]}
    assert names["target_id"]["value"] == "PDL1"
    assert names["ranking_signal"]["value"] == "raw_ipsae"


def test_negated_authorization_is_not_an_approval_order() -> None:
    service = _service(ScriptedExtractor([_patch(("target_id", "PDL1"))]))
    reply = _ask(service, "Quero desenhar binders para PDL1 e manter o request unauthorized")
    assert reply.status == "draft_created"
    assert reply.execution_authorized is False


def test_phase_6a_and_6b_contracts_stay_in_place() -> None:
    session = pdl1_prototype_session()
    assert session.execution_authorized is False
    assert session.draft.executable is False
    assert session.candidates[0].ranking_signal == "raw_ipsae"
    assert session.candidates[0].is_probability is False
    assert "get_ranking" in CONVERSATIONAL_READONLY_CAPABILITIES
    assert "protein_start_fixture_run" not in CONVERSATIONAL_READONLY_CAPABILITIES
    retrospective = " ".join("what was executed?".casefold().split()).replace("executed", " ")
    assert "execute" not in retrospective
    extractor = ScriptedExtractor([_patch(("target_id", "PDL1"))])
    service = _service(extractor)
    reply = _ask(service, "what was executed?")
    assert reply.status == "unsupported_message"
    assert extractor.calls == 0


def test_complete_request_matches_generation_request_fields() -> None:
    service = _service(
        ScriptedExtractor(
            [
                _patch(
                    ("target_id", "PDL1"),
                    ("objective", "binder_design"),
                    ("generator", "bindcraft"),
                    ("n_candidates", 4),
                    ("length_min", 60),
                    ("length_max", 90),
                )
            ]
        )
    )
    reply = _ask(service, "Quero desenhar binders para PDL1")
    assert reply.request is not None
    assert reply.request.generation_payload() == {
        "target_id": "PDL1",
        "objective": "binder_design",
        "generator": "bindcraft",
        "n_candidates": 4,
        "constraints": {"length_min": 60, "length_max": 90},
    }
    assert reply.request.execution_authorized is False
    assert reply.request.executable is False


def test_stale_revision_does_not_overwrite_the_newer_draft() -> None:
    extractor = ScriptedExtractor(
        [
            _patch(("target_id", "PDL1"), ("objective", "binder_design")),
            _patch(("generator", "bindcraft")),
            _patch(("n_candidates", 4)),
        ]
    )
    service = _service(extractor)
    _ask(service, "Quero desenhar binders para PDL1")
    updated = _ask(service, "use bindcraft", request_id="request_1", revision=1)
    assert updated.draft.revision == 2
    stale = _ask(service, "4 candidates", request_id="request_1", revision=1)
    assert stale.status == "revision_conflict"
    assert stale.draft is not None
    assert stale.draft.revision == 2
    assert stale.changed_fields == ()
    assert extractor.calls == 2
    assert [draft.revision for draft in service.revisions("request_1")] == [1, 2]
    status, body = handle_design_draft(
        service,
        json.dumps(
            {
                "session_id": SESSION,
                "message": "4 candidates",
                "request_id": "request_1",
                "revision": 1,
            }
        ).encode(),
    )
    assert status == 409
    assert body["status"] == "revision_conflict"
    assert body["draft"]["revision"] == 2
    assert extractor.calls == 2
    current = _ask(service, "4 candidates", request_id="request_1", revision=2)
    assert current.draft.revision == 3
    assert current.draft.session_id == SESSION
    assert _fields(current)["n_candidates"]["value"] == 4


def test_concurrent_patches_with_one_revision_create_one_successor() -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowExtractor:
        def __init__(self) -> None:
            self.calls = 0

        def extract(self, *, message: str, draft: ProteinDesignRequestDraft | None) -> DraftPatch:
            self.calls += 1
            if draft is None:
                return _patch(("target_id", "PDL1"), ("objective", "binder_design"))
            entered.set()
            release.wait(timeout=5)
            return _patch(("generator", message.split()[-1]))

    extractor = SlowExtractor()
    service = _service(extractor)  # type: ignore[arg-type]
    created = _ask(service, "Quero desenhar binders para PDL1")
    assert created.draft.revision == 1
    replies: dict[str, object] = {}

    def patch(name: str, generator: str) -> None:
        replies[name] = _ask(
            service,
            f"use {generator}",
            request_id="request_1",
            revision=1,
        )

    first = threading.Thread(target=patch, args=("first", "bindcraft"))
    second = threading.Thread(target=patch, args=("second", "mber"))
    first.start()
    assert entered.wait(timeout=5)
    second.start()
    second.join(timeout=0.2)
    assert second.is_alive()
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    statuses = sorted(reply.status for reply in replies.values())  # type: ignore[attr-defined]
    assert statuses == ["draft_updated", "revision_conflict"]
    assert replies["first"].status == "draft_updated"  # type: ignore[attr-defined]
    assert replies["second"].draft.revision == 2  # type: ignore[attr-defined]
    assert [draft.revision for draft in service.revisions("request_1")] == [1, 2]
    assert extractor.calls == 2


def test_a_draft_cannot_be_patched_from_another_session() -> None:
    base = pdl1_prototype_session()
    other = replace(base, session_id="session_other")
    extractor = ScriptedExtractor(
        [
            _patch(("target_id", "PDL1"), ("objective", "binder_design")),
            _patch(("generator", "bindcraft")),
        ]
    )
    service = DesignDrafting(
        {base.session_id: base, other.session_id: other},
        extractor,
        clock=lambda: WHEN,
        interaction_ids=lambda: "interaction_1",
        request_ids=lambda: "request_1",
    )
    created = service.revise(
        DesignDraftCommand(base.session_id, "Quero desenhar binders para PDL1")
    )
    crossed = service.revise(
        DesignDraftCommand(
            other.session_id,
            "use bindcraft",
            request_id=created.draft.request_id,
            revision=1,
        )
    )
    assert crossed.status == "draft_not_found"
    assert crossed.draft is None
    assert crossed.session_id == other.session_id
    assert extractor.calls == 1
    kept = service.revise(
        DesignDraftCommand(base.session_id, "use bindcraft", request_id="request_1", revision=1)
    )
    assert kept.status == "draft_updated"
    assert kept.draft.revision == 2
    assert kept.draft.session_id == base.session_id


def test_patch_cannot_rewrite_identity_or_provenance() -> None:
    extractor = ScriptedExtractor(
        [
            _patch(("target_id", "PDL1")),
            DraftPatch(
                (
                    FieldProposal("session_id", "session_other"),
                    FieldProposal("request_id", "request_hijack"),
                    FieldProposal("revision_id", "server-rev"),
                    FieldProposal("provenance", "documented_default"),
                )
            ),
        ]
    )
    service = _service(extractor)
    created = _ask(service, "Quero desenhar binders para PDL1")
    reply = _ask(service, "use bindcraft", request_id="request_1", revision=1)
    assert reply.status == "invalid_patch"
    assert reply.draft is not None
    assert reply.draft.revision == 1
    assert reply.draft.request_id == created.draft.request_id
    assert reply.draft.session_id == SESSION
    assert {issue.field for issue in reply.issues if issue.code == "rejected"} == {
        "session_id",
        "request_id",
        "revision_id",
        "provenance",
    }


def test_http_rejects_malformed_bodies_without_a_traceback() -> None:
    extractor = ScriptedExtractor([_patch(("target_id", "PDL1"))])
    service = _service(extractor)
    bodies = [
        b"[1]",
        b"null",
        b'"text"',
        json.dumps({"session_id": SESSION, "message": "desenhar", "provenance": "user"}).encode(),
        json.dumps({"session_id": SESSION, "message": "desenhar", "revision_id": "rev"}).encode(),
        json.dumps({"session_id": SESSION, "message": "desenhar", "draft_id": "other"}).encode(),
        json.dumps({"session_id": 12, "message": "Quero desenhar binders"}).encode(),
        json.dumps({"session_id": SESSION, "message": ["not", "text"]}).encode(),
        json.dumps({"session_id": SESSION, "message": "desenhar", "revision": "1"}).encode(),
        json.dumps({"session_id": SESSION, "message": "desenhar", "request_id": 3}).encode(),
    ]
    for body in bodies:
        status, payload = handle_design_draft(service, body)
        encoded = json.dumps(payload)
        assert status == 400
        assert payload["execution_authorized"] is False
        assert payload["executable"] is False
        assert payload["draft"] is None
        assert "Traceback" not in encoded
        assert "secret" not in encoded
    assert extractor.calls == 0


class _RankingQuery:
    def query(self, capability: str, *, session_id: str, run_id: str | None = None, candidate_id: str | None = None) -> QueryPage:
        del session_id, run_id, candidate_id
        return QueryPage(
            True,
            (
                ReadOnlyFact(
                    capability=capability,
                    entity_kind="run",
                    entity_id="run_test",
                    source="session",
                    attributes=(("ranking_signal", "raw_ipsae"),),
                ),
            ),
        )


def test_one_configured_server_serves_both_endpoints() -> None:
    from http.server import ThreadingHTTPServer

    readonly = ReadOnlyConversation(
        _RankingQuery(),
        model=RecordedFactsModel(),
        clock=lambda: WHEN,
        interaction_ids=lambda: "interaction_1",
    )
    drafter = _service(DeterministicDraftExtractor())
    server = ThreadingHTTPServer(("127.0.0.1", 0), configured_handler(readonly, drafter))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conversation = _post(
            host,
            port,
            "/api/conversation",
            {
                "session_id": SESSION,
                "question": "what is the ranking of this run",
                "run_id": "run_test",
            },
        )
        draft = _post(
            host,
            port,
            "/api/design-draft",
            {"session_id": SESSION, "message": "Quero desenhar binders para PDL1"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert conversation["status"] == "answered"
    assert conversation["read_only"] is True
    assert "raw_ipsae" in json.dumps(conversation["evidence"])
    assert draft["status"] == "draft_created"
    assert draft["execution_authorized"] is False
    assert draft["executable"] is False
    assert draft["draft"]["session_id"] == SESSION


def _post_draft(service: DesignDrafting, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    status, body = handle_design_draft(service, json.dumps(payload).encode())
    return status, body


def test_candidate_clarifications_stay_on_the_same_request() -> None:
    service = _service(DeterministicDraftExtractor())
    message = "#3 design an new binder to PDL1"
    created = _ask(service, message)
    assert created.draft is not None and created.draft.revision == 1  # type: ignore[attr-defined]
    assert service.recorded("request_1") == (created.draft, message)  # type: ignore[arg-type]

    status, first = _post_draft(
        service,
        {
            "session_id": SESSION,
            "message": message,
            "request_id": "request_1",
            "revision": 1,
            "clarifications": [{"field": "n_candidates", "value": "2"}],
        },
    )
    assert status == 200
    assert first["status"] == "draft_updated"
    assert first["draft"]["request_id"] == "request_1"  # type: ignore[index]
    assert first["draft"]["revision"] == 2  # type: ignore[index]
    count = next(item for item in first["draft"]["fields"] if item["name"] == "n_candidates")  # type: ignore[index]
    assert count["value"] == 2 and count["provenance"] == "user"
    assert service.recorded("request_1")[1] == message

    status, second = _post_draft(
        service,
        {
            "session_id": SESSION,
            "message": message,
            "request_id": "request_1",
            "revision": 2,
            "clarifications": [{"field": "n_candidates", "value": 3}],
        },
    )
    assert status == 200
    assert second["draft"]["revision"] == 3  # type: ignore[index]
    count = next(item for item in second["draft"]["fields"] if item["name"] == "n_candidates")  # type: ignore[index]
    assert count["value"] == 3 and count["provenance"] == "user"
    assert service.recorded("request_1")[1] == message
    assert [item.revision for item in service.revisions("request_1")] == [1, 2, 3]

    status, conflict = _post_draft(
        service,
        {
            "session_id": SESSION,
            "message": message,
            "request_id": "request_1",
            "revision": 2,
            "clarifications": [{"field": "n_candidates", "value": 9}],
        },
    )
    assert status == 409
    assert conflict["status"] == "revision_conflict"
    assert conflict["draft"]["revision"] == 3  # type: ignore[index]
    assert service.recorded("request_1")[0].revision == 3
    kept = next(field for field in service.recorded("request_1")[0].fields if field.name == "n_candidates")
    assert kept.value == 3
    assert service.recorded("request_1")[1] == message

    status, rewritten = _post_draft(
        service,
        {
            "session_id": SESSION,
            "message": f"{message}\n4",
            "request_id": "request_1",
            "revision": 3,
            "clarifications": [{"field": "n_candidates", "value": 4}],
        },
    )
    assert status == 409
    assert rewritten["status"] == "revision_conflict"
    assert service.recorded("request_1")[1] == message
    kept = next(field for field in service.recorded("request_1")[0].fields if field.name == "n_candidates")
    assert kept.value == 3

    status, invalid = _post_draft(
        service,
        {
            "session_id": SESSION,
            "message": message,
            "request_id": "request_1",
            "revision": 3,
            "clarifications": [{"field": "n_candidates", "value": "many"}],
        },
    )
    assert status == 200
    assert invalid["status"] == "invalid_patch"
    assert service.recorded("request_1")[0].revision == 3
    assert service.recorded("request_1")[1] == message


def _post(host: str, port: int, path: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        f"http://{host}:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        assert response.status == 200
        body = json.loads(response.read().decode())
    assert isinstance(body, dict)
    return body
