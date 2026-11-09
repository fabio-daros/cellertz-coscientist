"""Read-only conversational access. Fakes only; no network."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cellertz.application.contracts import ApplicationContractError, RunSummary
from cellertz.application.conversation import (
    BLOCKED_CAPABILITIES,
    CONVERSATIONAL_READONLY_CAPABILITIES,
    ConversationRequest,
    ListUsageRecorder,
    ModelCompletion,
    ModelUsageReport,
    QueryPage,
    ReadOnlyConversation,
    ReadOnlyFact,
    RecordedFactsModel,
    SessionSnapshotQuery,
    forbidden_action,
    requested_capabilities,
)
from cellertz.application.conversation_transport import handle_conversation
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.conversation.episodic import EpisodicReadOnlyQuery, build_readonly_service

WHEN = datetime(2026, 9, 27, 15, tzinfo=UTC)
SESSION = "session_test"
RUN = "run_test"
CANDIDATE = "candidate_test"


class FakeSources:
    def __init__(self, pages: dict[str, QueryPage]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str, str | None, str | None]] = []
        self.writes = 0

    def query(self, capability: str, *, session_id: str, run_id: str | None = None, candidate_id: str | None = None) -> QueryPage:
        self.calls.append((capability, session_id, run_id, candidate_id))
        if capability not in CONVERSATIONAL_READONLY_CAPABILITIES:
            raise AssertionError(capability)
        return self.pages.get(capability, QueryPage(True, ()))

    def put(self, *_args: object, **_kwargs: object) -> None:
        self.writes += 1
        raise AssertionError("write")

    def start_run(self, *_args: object, **_kwargs: object) -> None:
        self.writes += 1
        raise AssertionError("start_run")


class FakeModel:
    def __init__(self, usage: ModelUsageReport | None = None, text: str | None = None) -> None:
        self.usage = usage
        self.text = text
        self.calls = 0
        self.facts: tuple[ReadOnlyFact, ...] = ()
        self.question = ""

    def complete(self, *, facts: tuple[ReadOnlyFact, ...], question: str) -> ModelCompletion:
        self.calls += 1
        self.facts = facts
        self.question = question
        if self.text is not None:
            body = self.text
        else:
            body = " ".join(
                f"{key}={value}" for fact in facts for key, value in fact.attributes
            )
        return ModelCompletion(text=body or "no facts", usage=self.usage)


class BoomModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, facts: tuple[ReadOnlyFact, ...], question: str) -> ModelCompletion:
        del facts, question
        self.calls += 1
        raise RuntimeError("secret-backend")


class Document:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def to_document(self) -> dict[str, object]:
        return self.payload


class StubReader:
    def __init__(self, documents: dict[str, Document]) -> None:
        self.documents = documents
        self.calls: list[tuple[str, str]] = []

    def get_run(self, run_id: str) -> Document | None:
        self.calls.append(("get_run", run_id))
        return self.documents.get(run_id)

    def put(self, *_args: object) -> None:
        self.calls.append(("put", ""))
        raise AssertionError("put")


def _fact() -> ReadOnlyFact:
    return ReadOnlyFact(
        capability="get_ranking",
        entity_kind="candidate",
        entity_id=CANDIDATE,
        source="session",
        attributes=(
            ("ranking_signal", "raw_ipsae"),
            ("ranking_score", "0.25"),
            ("is_probability", "false"),
        ),
    )


def _pages(**overrides: QueryPage) -> dict[str, QueryPage]:
    pages = {
        "get_session": QueryPage(
            True,
            (
                ReadOnlyFact(
                    capability="get_session",
                    entity_kind="session",
                    entity_id=SESSION,
                    source="session",
                    attributes=(("execution_authorized", "false"),),
                ),
            ),
        ),
        "get_run": QueryPage(
            True,
            (
                ReadOnlyFact(
                    capability="get_run",
                    entity_kind="run",
                    entity_id=RUN,
                    source="session",
                    attributes=(("epistemic_status", "observed"),),
                ),
            ),
        ),
        "get_ranking": QueryPage(True, (_fact(),)),
    }
    pages.update(overrides)
    return pages


def _service(
    pages: dict[str, QueryPage],
    model: FakeModel | BoomModel | RecordedFactsModel | None = None,
    recorder: ListUsageRecorder | None = None,
) -> tuple[ReadOnlyConversation, FakeSources, ListUsageRecorder]:
    sources = FakeSources(pages)
    recorder = recorder or ListUsageRecorder()
    service = ReadOnlyConversation(
        sources,
        model=model or FakeModel(),
        recorder=recorder,
        clock=lambda: WHEN,
        interaction_ids=lambda: "interaction_1",
    )
    return service, sources, recorder


def _ask(service: ReadOnlyConversation, question: str, **overrides: object) -> object:
    payload = {
        "session_id": SESSION,
        "question": question,
        "run_id": RUN,
        "candidate_id": CANDIDATE,
    }
    payload.update(overrides)
    return service.ask(ConversationRequest(**payload))  # type: ignore[arg-type]


def test_valid_question_reads_session_and_run() -> None:
    service, sources, recorder = _service(_pages())
    reply = _ask(service, "what is the ranking of this run")
    assert reply.status == "answered"
    assert reply.session_id == SESSION
    assert reply.run_id == RUN
    assert reply.read_only is True
    assert [call[0] for call in sources.calls] == ["get_session", "get_run", "get_ranking"]
    assert sources.writes == 0
    assert recorder.events == []


def test_answer_uses_facts_given_to_the_fake_model() -> None:
    model = FakeModel()
    service, _, _ = _service(_pages(), model)
    reply = _ask(service, "what is the ranking of this run")
    assert model.calls == 1
    assert model.question == "what is the ranking of this run"
    assert model.facts[-1].entity_id == CANDIDATE
    assert dict(model.facts[-1].attributes)["ranking_score"] == "0.25"
    assert "ranking_score=0.25" in reply.text
    assert "ranking_signal=raw_ipsae" in reply.text
    assert "composite" not in reply.text


def test_evidence_references_are_preserved() -> None:
    service, _, _ = _service(_pages())
    reply = _ask(service, "what is the ranking of this run")
    encoded = reply.to_dict()["evidence"]
    ranking = next(item for item in encoded if item["capability"] == "get_ranking")
    assert ranking["entity_id"] == CANDIDATE
    assert ranking["source"] == "session"
    assert ranking["attributes"]["ranking_signal"] == "raw_ipsae"
    assert ranking["attributes"]["ranking_score"] == "0.25"
    assert ranking["attributes"]["is_probability"] == "false"


def test_missing_session_is_typed_and_skips_the_model() -> None:
    model = FakeModel()
    service, sources, _ = _service(
        _pages(get_session=QueryPage(False, ())),
        model,
    )
    reply = _ask(service, "what is the ranking of this run", session_id="session_missing")
    assert reply.status == "session_not_found"
    assert "session_missing" in reply.text
    assert sources.calls == [("get_session", "session_missing", RUN, CANDIDATE)]
    assert model.calls == 0
    assert sources.writes == 0


def test_missing_run_is_typed_and_skips_the_model() -> None:
    model = FakeModel()
    service, sources, _ = _service(_pages(get_run=QueryPage(False, ())), model)
    reply = _ask(service, "what is the ranking of this run", run_id="run_missing")
    assert reply.status == "run_not_found"
    assert "run_missing" in reply.text
    assert [call[0] for call in sources.calls] == ["get_session", "get_run"]
    assert model.calls == 0
    assert sources.writes == 0


def test_insufficient_recorded_data_does_not_invent_an_answer() -> None:
    model = BoomModel()
    session = pdl1_prototype_session()
    service = ReadOnlyConversation(
        SessionSnapshotQuery({session.session_id: session}),
        model=model,
        clock=lambda: WHEN,
        interaction_ids=lambda: "interaction_1",
    )
    reply = service.ask(
        ConversationRequest(
            session_id=session.session_id,
            question="what did the critic record for this run",
            run_id="protein_run_pdl1_smoke",
        )
    )
    assert reply.status == "insufficient_data"
    assert reply.text == "The recorded data is insufficient to answer this question."
    assert reply.evidence == ()
    assert model.calls == 0
    assert "0." not in reply.text


@pytest.mark.parametrize(
    "question",
    [
        "execute o candidato X",
        "execute candidate X",
        "start the run",
        "approve the request",
        "approve this request",
        "promote this into the second brain",
        "promote this result",
        "ingest the run into memory",
        "create a protein design request",
    ],
)
def test_forbidden_action_is_blocked_before_any_call(question: str) -> None:
    model = BoomModel()
    service, sources, recorder = _service(_pages(), model)
    reply = _ask(service, question)
    assert reply.status == "forbidden_action"
    assert reply.read_only is True
    assert "blocked" in reply.text
    assert sources.calls == []
    assert sources.writes == 0
    assert model.calls == 0
    assert recorder.events == []
    status, body = handle_conversation(
        service,
        json.dumps(
            {
                "session_id": SESSION,
                "question": question,
                "run_id": RUN,
                "candidate_id": CANDIDATE,
            }
        ).encode(),
    )
    assert status == 200
    assert body["status"] == "forbidden_action"
    assert body["read_only"] is True
    assert sources.calls == []


def test_allowlist_contains_only_readonly_capabilities() -> None:
    assert CONVERSATIONAL_READONLY_CAPABILITIES == frozenset(
        {
            "get_session",
            "get_run",
            "inspect_run_state",
            "list_candidates",
            "get_candidate",
            "get_ranking",
            "get_critique",
            "get_evidence",
            "get_artifacts",
            "get_episodic_memory",
            "get_design_request",
            "get_semantic_knowledge",
            "get_fixture_results",
        }
    )
    assert CONVERSATIONAL_READONLY_CAPABILITIES.isdisjoint(BLOCKED_CAPABILITIES)
    for blocked in (
        "protein_start_fixture_run",
        "create_protein_design_request",
        "approve_request",
        "execute_bindcraft",
        "execute_boltz2",
        "promote_second_brain",
        "ingest_memory",
    ):
        assert blocked in BLOCKED_CAPABILITIES
        assert blocked not in CONVERSATIONAL_READONLY_CAPABILITIES
    asked = requested_capabilities("what is the ranking of this run", CANDIDATE)
    assert set(asked) <= CONVERSATIONAL_READONLY_CAPABILITIES
    assert requested_capabilities("execute o candidato X") == ()


def test_readonly_path_does_not_call_a_write_port() -> None:
    service, sources, _ = _service(_pages())
    _ask(service, "what is the ranking of this run")
    assert sources.writes == 0
    assert "put" not in {call[0] for call in sources.calls}
    assert not hasattr(ReadOnlyConversation, "start_run")


def test_adapter_failure_is_a_safe_error() -> None:
    model = BoomModel()
    service, sources, recorder = _service(_pages(), model)
    reply = _ask(service, "what is the ranking of this run")
    assert reply.status == "adapter_failure"
    assert reply.text == "The conversational adapter failed. No scientific action was taken."
    assert "secret-backend" not in reply.text
    assert reply.instrumentation.recorded is False
    assert reply.instrumentation.event is None
    assert reply.instrumentation.total_tokens is None
    assert reply.instrumentation.observation == "adapter_failure"
    assert recorder.events == []
    assert sources.writes == 0
    assert model.calls == 1


def test_usage_event_keeps_the_provider_values() -> None:
    usage = ModelUsageReport(
        provider="fake",
        model="fake-1",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
        cached_input_tokens=1,
    )
    recorder = ListUsageRecorder()
    service, _, _ = _service(_pages(), FakeModel(usage=usage, text="Recorded entity candidate_test."), recorder)
    reply = _ask(service, "what is the ranking of this run")
    event = reply.instrumentation.event
    assert event is not None
    assert reply.instrumentation.recorded is True
    assert recorder.events == [event]
    assert event.session_id == SESSION
    assert event.run_id == RUN
    assert event.agent_id == "readonly_conversation"
    assert event.stage == "conversation"
    assert event.provider == "fake"
    assert event.model == "fake-1"
    assert event.input_tokens == 11
    assert event.output_tokens == 7
    assert event.cached_input_tokens == 1
    assert reply.instrumentation.total_tokens == 18
    assert reply.instrumentation.interaction_id == "interaction_1"
    assert event.started_at == WHEN
    assert event.completed_at == WHEN


def test_absent_token_counts_are_not_invented() -> None:
    usage = ModelUsageReport(provider="fake", model="fake-1", input_tokens=2, output_tokens=3)
    service, _, recorder = _service(
        _pages(),
        FakeModel(usage=usage, text="Recorded entity candidate_test."),
    )
    reply = _ask(service, "what is the ranking of this run")
    event = reply.instrumentation.event
    assert event is not None
    assert event.input_tokens == 2
    assert event.output_tokens == 3
    assert event.cached_input_tokens is None
    assert reply.instrumentation.total_tokens is None
    assert event.to_dict()["input_tokens"] == 2
    assert "total_tokens" not in event.to_dict()
    assert len(recorder.events) == 1
    blank = ModelUsageReport(provider="fake", model="fake-1")
    service, _, recorder = _service(
        _pages(),
        FakeModel(usage=blank, text="Recorded entity candidate_test."),
        recorder,
    )
    second = _ask(service, "what is the ranking of this run")
    assert second.instrumentation.event is not None
    assert second.instrumentation.event.input_tokens is None
    assert second.instrumentation.event.output_tokens is None
    assert second.instrumentation.total_tokens is None
    assert second.instrumentation.event.to_dict()["input_tokens"] is None
    assert len(recorder.events) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("total_tokens", -1),
        ("input_tokens", -1),
        ("output_tokens", -3),
        ("cached_input_tokens", -1),
        ("input_tokens", True),
        ("total_tokens", 1.5),
        ("provider", ""),
        ("provider", " "),
        ("model", ""),
    ],
)
def test_invalid_usage_report_records_nothing(field: str, value: object) -> None:
    payload: dict[str, object] = {"provider": "fake", "model": "fake-1", field: value}
    usage = ModelUsageReport(**payload)  # type: ignore[arg-type]
    recorder = ListUsageRecorder()
    service, _, _ = _service(
        _pages(),
        FakeModel(usage=usage, text="Recorded entity candidate_test."),
        recorder,
    )
    reply = _ask(service, "what is the ranking of this run")
    assert reply.status == "adapter_failure"
    assert reply.text == "The conversational adapter failed. No scientific action was taken."
    assert reply.instrumentation.recorded is False
    assert reply.instrumentation.event is None
    assert reply.instrumentation.total_tokens is None
    assert recorder.events == []


def test_transport_forwards_session_run_and_question() -> None:
    service, sources, _ = _service(_pages())
    status, body = handle_conversation(
        service,
        json.dumps(
            {
                "session_id": SESSION,
                "question": "what is the ranking of this run",
                "run_id": RUN,
                "candidate_id": CANDIDATE,
            }
        ).encode(),
    )
    assert status == 200
    assert body["read_only"] is True
    assert body["session_id"] == SESSION
    assert body["run_id"] == RUN
    assert sources.calls[0] == ("get_session", SESSION, RUN, CANDIDATE)
    assert sources.writes == 0


def test_snapshot_ranking_does_not_invent_a_score_or_composite() -> None:
    session = pdl1_prototype_session()
    service = ReadOnlyConversation(
        SessionSnapshotQuery({session.session_id: session}),
        model=RecordedFactsModel(),
        clock=lambda: WHEN,
        interaction_ids=lambda: "interaction_1",
    )
    reply = service.ask(
        ConversationRequest(
            session_id=session.session_id,
            question="what is the ranking of this run",
            run_id="protein_run_pdl1_smoke",
        )
    )
    assert reply.status == "answered"
    assert reply.read_only is True
    assert reply.instrumentation.recorded is False
    assert reply.instrumentation.event is None
    assert reply.instrumentation.total_tokens is None
    blob = json.dumps(reply.to_dict())
    assert "composite_score" not in blob
    assert "priority_score" not in blob
    ranking = [fact for fact in reply.evidence if fact.capability == "get_ranking"]
    assert ranking
    for fact in ranking:
        attributes = dict(fact.attributes)
        assert attributes["ranking_signal"] == "raw_ipsae"
        assert attributes["ranking_score"] == "not_recorded"
        assert attributes["is_probability"] == "false"
        assert attributes["recommended_as_hard_filter"] == "false"
        assert "boltz" not in attributes
    payload = session.to_dict()
    assert payload["prototype_only"] is True
    assert payload["execution_authorized"] is False
    assert payload["draft"]["executable"] is False
    assert payload["usage"] == []


def test_episodic_reader_is_read_only_and_drops_composite_scores() -> None:
    session = pdl1_prototype_session()
    reader = StubReader(
        {
            "protein_run_pdl1_smoke": Document(
                {
                    "run_id": "protein_run_pdl1_smoke",
                    "target_id": "PDL1",
                    "memory_house": "episodic",
                    "epistemic_status": "observed",
                    "candidates": [
                        {
                            "candidate_id": "candidate_test",
                            "ranking_signal": "raw_ipsae",
                            "ranking_score": None,
                            "composite_score": 99,
                            "is_probability": False,
                            "evidence": {"ipsae_mean": 0.2, "boltz_score": 0.9},
                        }
                    ],
                }
            )
        }
    )
    source = EpisodicReadOnlyQuery({session.session_id: session}, reader)
    with pytest.raises(ApplicationContractError) as blocked:
        source.query(
            "protein_start_fixture_run",
            session_id=session.session_id,
            run_id="protein_run_pdl1_smoke",
        )
    assert blocked.value.code == "capability_not_allowlisted"
    assert reader.calls == []
    ranking = source.query(
        "get_ranking",
        session_id=session.session_id,
        run_id="protein_run_pdl1_smoke",
    )
    attributes = dict(ranking.facts[0].attributes)
    assert attributes["ranking_signal"] == "raw_ipsae"
    assert attributes["ranking_score"] == "not_recorded"
    assert "composite_score" not in attributes
    assert "boltz_score" not in attributes
    evidence = source.query(
        "get_evidence",
        session_id=session.session_id,
        run_id="protein_run_pdl1_smoke",
    )
    evidence_attributes = dict(evidence.facts[0].attributes)
    assert evidence_attributes["ipsae_mean"] == "0.2"
    assert evidence_attributes["boltz_score"] == "0.9"
    assert "ranking_signal" not in evidence_attributes
    assert reader.calls == [("get_run", "protein_run_pdl1_smoke"), ("get_run", "protein_run_pdl1_smoke")]
    assert not hasattr(source, "put")


def test_unsupported_question_calls_nothing() -> None:
    model = BoomModel()
    service, sources, _ = _service(_pages(), model)
    reply = _ask(service, "what is the weather in paris")
    assert reply.status == "unsupported_question"
    assert sources.calls == []
    assert model.calls == 0
    assert sources.writes == 0


def test_http_edge_reads_injected_memory_outside_the_fixture() -> None:
    transport = Path("src/cellertz/application/conversation_transport.py").read_text(
        encoding="utf-8",
    )
    assert "pdl1" not in transport
    assert "fixtures" not in transport
    session = replace(
        pdl1_prototype_session(),
        session_id="session_real",
        runs=(
            RunSummary(
                run_id="run_real",
                target_id="TARGET_REAL",
                memory_house="episodic",
                epistemic_status="observed",
                display_status="Linked recorded run.",
            ),
        ),
    )
    reader = StubReader(
        {
            "run_real": Document(
                {
                    "run_id": "run_real",
                    "target_id": "TARGET_REAL",
                    "memory_house": "episodic",
                    "epistemic_status": "observed",
                    "candidates": [
                        {
                            "candidate_id": "candidate_real",
                            "ranking_signal": "raw_ipsae",
                            "ranking_score": 0.4,
                            "is_probability": False,
                            "recommended_as_hard_filter": False,
                        }
                    ],
                }
            )
        }
    )
    service = build_readonly_service(
        reader,
        {session.session_id: session},
        clock=lambda: WHEN,
        interaction_ids=lambda: "interaction_real",
    )
    status, body = handle_conversation(
        service,
        json.dumps(
            {
                "session_id": "session_real",
                "question": "what is the ranking of this run",
                "run_id": "run_real",
            }
        ).encode(),
    )
    assert status == 200
    assert body["status"] == "answered"
    assert body["session_id"] == "session_real"
    assert body["run_id"] == "run_real"
    ranking = next(item for item in body["evidence"] if item["capability"] == "get_ranking")
    assert ranking["source"] == "episodic"
    assert ranking["entity_id"] == "candidate_real"
    assert ranking["attributes"]["ranking_signal"] == "raw_ipsae"
    assert ranking["attributes"]["ranking_score"] == "0.4"
    assert "bindcraft_PDL1_l65_s585903_mpnn1" not in json.dumps(ranking)
    assert "put" not in {name for name, _run in reader.calls}
    assert ("get_run", "run_real") in reader.calls


@pytest.mark.parametrize(
    "question",
    [
        "what was executed?",
        "what ran in this session?",
        "show the executed stages",
    ],
)
def test_retrospective_questions_are_readonly(question: str) -> None:
    assert forbidden_action(question) is None
    pages = _pages(
        inspect_run_state=QueryPage(
            True,
            (
                ReadOnlyFact(
                    capability="inspect_run_state",
                    entity_kind="stage",
                    entity_id="supervisor",
                    source="session",
                    attributes=(("display_status", "not_executed"),),
                ),
            ),
        )
    )
    model = FakeModel(text="Recorded past execution.")
    service, sources, _ = _service(pages, model)
    reply = _ask(service, question)
    assert reply.status == "answered"
    assert reply.text == "Recorded past execution."
    assert model.calls == 1
    assert sources.writes == 0
    assert sources.calls
    assert {call[0] for call in sources.calls} <= CONVERSATIONAL_READONLY_CAPABILITIES


def test_unlinked_run_is_refused() -> None:
    session = replace(
        pdl1_prototype_session(),
        session_id="session_real",
        runs=(
            RunSummary(
                run_id="run_real",
                target_id="TARGET_REAL",
                memory_house="episodic",
                epistemic_status="observed",
                display_status="Linked recorded run.",
            ),
        ),
    )
    reader = StubReader(
        {
            "run_other": Document(
                {
                    "run_id": "run_other",
                    "target_id": "TARGET_OTHER",
                    "memory_house": "episodic",
                    "epistemic_status": "observed",
                    "candidates": [
                        {
                            "candidate_id": "candidate_other",
                            "ranking_signal": "raw_ipsae",
                            "ranking_score": 0.9,
                        }
                    ],
                }
            )
        }
    )
    model = BoomModel()
    service = build_readonly_service(
        reader,
        {session.session_id: session},
        model=model,
        clock=lambda: WHEN,
        interaction_ids=lambda: "interaction_real",
    )
    status, body = handle_conversation(
        service,
        json.dumps(
            {
                "session_id": "session_real",
                "question": "what is the ranking of this run",
                "run_id": "run_other",
            }
        ).encode(),
    )
    assert status == 200
    assert body["status"] == "run_not_in_session"
    assert body["evidence"] == []
    assert "candidate_other" not in json.dumps(body)
    assert "0.9" not in json.dumps(body)
    assert model.calls == 0
    assert "put" not in {name for name, _run in reader.calls}
