"""The Supervisor analysis is bound to one saved revision and does not start work."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from cellertz.application.conversation import ModelCompletion, ModelUsageReport, RecordedFactsModel, ResponseOrigin
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DesignDrafting,
    DeterministicDraftExtractor,
)
from cellertz.application.request_supervisor import (
    PROPOSED_OPENROUTER_MODEL,
    DesignRequestSupervisor,
    SupervisorLimits,
    handle_design_supervisor,
    handle_list_supervisor_analyses,
)
from cellertz.conversation.model import ChatCompletionModel

SESSION = "session_pdl1_prototype"
MESSAGE = "Design a binder for a named target."
_QUESTION = (
    "You are the Protein Design Supervisor. Write a short summary in English of the "
    "design objective you understand. Do not only repeat the saved text. "
    "The facts keep three sources separate. researcher_information is what the "
    "researcher supplied. server_configuration is the server's compatible "
    "configuration, such as BindCraft for the PDL1 pilot, and is not the "
    "researcher's text and not your decision. missing_or_ambiguous lists gaps "
    "already shown as Recorded gaps. Do not assume PD-1/PD-L1 blockade, a "
    "therapeutic use, affinity, length, or any other property the researcher "
    "did not state. Ask at most two questions, and only when they are needed to "
    "clarify this request. Do not repeat a recorded gap or a question the "
    "researcher already answered. Do not invent a question to fill the response. "
    "A question clarifies the request and is not an execution plan. "
    "When a question is about a known field, start that line with the field name and a colon, "
    "for example 'n_candidates: How many candidates should be requested?'. "
    "Known fields are target_id, objective, generator, n_candidates, length_min, length_max, seed, and filter_policy. "
    "Do not ask again about a field already listed as missing or already supplied. "
    "Put questions after a line that starts with 'Questions:'. If you have no question, write "
    "'Questions: none'. Do not plan, approve, authorize, or start a run."
)


class ScriptedModel:
    def __init__(self, text: str) -> None:
        self.text = text
        self.questions: list[str] = []
        self.facts: list[tuple[object, ...]] = []
        self.calls = 0

    def complete(self, *, facts: tuple[object, ...], question: str) -> ModelCompletion:
        self.calls += 1
        self.questions.append(question)
        self.facts.append(facts)
        return ModelCompletion(
            text=self.text,
            usage=ModelUsageReport(
                provider="openrouter",
                model="test-model",
                input_tokens=11,
                output_tokens=7,
                total_tokens=18,
            ),
            origin=ResponseOrigin(provider="openrouter", model="test-model"),
        )


def _drafted() -> tuple[DesignDrafting, str]:
    session = pdl1_prototype_session()
    service = DesignDrafting({session.session_id: session}, DeterministicDraftExtractor())
    reply = service.revise(DesignDraftCommand(SESSION, MESSAGE))
    assert reply.draft is not None
    return service, reply.draft.request_id


def _analyze(service: DesignDrafting, request_id: str, model: object, **limits: int) -> dict:
    supervisor = DesignRequestSupervisor(
        service,
        model,  # type: ignore[arg-type]
        SupervisorLimits(**limits) if limits else None,
    )
    result = supervisor.analyze(SESSION, request_id, 1, MESSAGE)
    result["records"] = supervisor.records
    return result


def test_supervisor_analysis_is_bound_to_the_saved_revision() -> None:
    service, request_id = _drafted()
    model = ScriptedModel(
        "The request asks for a binder.\n"
        "Questions:\n"
        "- Which scientific target should this request use?\n"
        "- Should the binder prefer a particular epitope?"
    )
    result = _analyze(service, request_id, model)

    assert result["status"] == "analyzed"
    assert result["revision"] == 1
    assert result["message"] == MESSAGE
    assert result["summary"] == "The request asks for a binder."
    assert result["validation"]["status"] == "accepted"
    assert result["extracted_questions"][0]["source"] == "recorded"
    assert result["extracted_questions"][0]["field"] == "target_id"
    assert result["supervisor_questions"] == [
        {
            "question": "Should the binder prefer a particular epitope?",
            "source": "supervisor",
            "field": None,
        }
    ]
    assert result["validation"]["dropped_repeated_gaps"] == [
        "Which scientific target should this request use?"
    ]
    assert model.questions == [_QUESTION]
    attributes = dict(model.facts[0][0].attributes)  # type: ignore[attr-defined]
    assert attributes["message"] == MESSAGE
    assert attributes["revision"] == "1"
    researcher = json.loads(attributes["researcher_information"])
    assert researcher["source"] == "researcher"
    assert researcher["text"] == MESSAGE
    assert "blockade" not in attributes["researcher_information"].casefold()
    assert "affinity" not in attributes["researcher_information"].casefold()
    assert "length_min" not in attributes["researcher_information"]
    server = json.loads(attributes["server_configuration"])
    assert server["source"] == "server_configuration"
    assert server["configured_generator"] is None
    missing = json.loads(attributes["missing_or_ambiguous"])
    assert missing["source"] == "missing_or_ambiguous"
    assert any("target" in gap.casefold() for gap in missing["recorded_gaps"])
    assert result["current"] is True
    assert result["records"][0]["revision"] == 1
    assert result["records"][0]["current"] is True
    assert result["model_call"] == {
        "executed": True,
        "model": "test-model",
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
    assert "api_key" not in json.dumps(result["records"])
    assert len(service.revisions(request_id)) == 1


def test_an_older_revision_is_refused_before_the_model_is_called() -> None:
    service, request_id = _drafted()
    updated = service.revise(DesignDraftCommand(SESSION, f"{MESSAGE}\nPDL1", request_id, 1))
    assert updated.draft is not None and updated.draft.revision == 2
    model = ScriptedModel("This must not be used.\nQuestions: none")
    supervisor = DesignRequestSupervisor(service, model)
    result = supervisor.analyze(SESSION, request_id, 1, MESSAGE)

    assert result["status"] == "revision_conflict"
    assert result["current"] is False
    assert result["request_id"] == request_id
    assert result["revision"] == 1
    assert result["message"] == MESSAGE
    assert "PDL1" not in result["message"]
    assert model.calls == 0
    assert supervisor.records == []
    assert result["model_call"]["model"] == "Model not run"
    assert result["supervisor_questions"] == []
    assert result["execution_authorized"] is False
    assert result["executable"] is False
    assert len(service.revisions(request_id)) == 2


def test_a_different_text_is_not_analyzed() -> None:
    service, request_id = _drafted()
    model = ScriptedModel("This must not be used.")
    supervisor = DesignRequestSupervisor(service, model)
    result = supervisor.analyze(SESSION, request_id, 1, f"{MESSAGE} edited")

    assert result["status"] == "revision_conflict"
    assert result["revision"] == 1
    assert result["message"] == MESSAGE
    assert model.calls == 0
    assert supervisor.records == []
    assert result["model_call"]["model"] == "Model not run"
    assert result["model_call"]["total_tokens"] == "Model not run"
    assert result["instrumentation"]["total_tokens"] is None
    assert len(service.revisions(request_id)) == 1


def test_recorded_provider_does_not_substitute_an_analysis() -> None:
    service, request_id = _drafted()
    result = _analyze(service, request_id, RecordedFactsModel())

    assert result["status"] == "supervisor_unavailable"
    assert result["summary"] == "The Supervisor model is not configured. No analysis was substituted."
    assert result["extracted_questions"] == []
    assert result["supervisor_questions"] == []
    assert result["validation"]["status"] == "not_run"
    assert result["model_call"] == {
        "executed": False,
        "model": "Model not run",
        "input_tokens": "Model not run",
        "output_tokens": "Model not run",
        "total_tokens": "Model not run",
    }
    assert result["instrumentation"]["model"] is None
    assert result["instrumentation"]["total_tokens"] is None
    assert result["model_call"]["input_tokens"] != 0
    assert result["model_call"]["output_tokens"] != 0
    assert result["model_call"]["total_tokens"] != 0
    assert len(service.revisions(request_id)) == 1


def test_usage_above_the_token_budget_is_recorded_and_rejected() -> None:
    service, request_id = _drafted()

    class Expensive(ScriptedModel):
        def complete(self, *, facts: tuple[object, ...], question: str) -> ModelCompletion:
            self.calls += 1
            return ModelCompletion(
                text="The request asks for a binder.\nQuestions: none",
                usage=ModelUsageReport(
                    provider="openrouter",
                    model="test-model",
                    input_tokens=30,
                    output_tokens=20,
                    total_tokens=50,
                ),
                origin=ResponseOrigin(provider="openrouter", model="test-model"),
            )

    model = Expensive("")
    supervisor = DesignRequestSupervisor(service, model, SupervisorLimits(token_budget=20))
    result = supervisor.analyze(SESSION, request_id, 1, MESSAGE)

    assert result["status"] == "analysis_rejected"
    assert result["summary"] == "The Supervisor analysis was not accepted."
    assert result["supervisor_questions"] == []
    assert result["validation"]["findings"][0]["code"] == "token_budget_exceeded"
    assert supervisor.records[0]["summary"] == "The request asks for a binder."
    assert supervisor.records[0]["validation"]["status"] == "rejected"
    assert result["model_call"]["executed"] is True
    assert result["model_call"]["model"] == "test-model"
    assert result["model_call"]["total_tokens"] == 50
    assert "api_key" not in json.dumps(supervisor.records)


def test_the_call_limit_stops_another_model_call() -> None:
    service, request_id = _drafted()
    model = ScriptedModel("The request asks for a binder.\nQuestions: none")
    supervisor = DesignRequestSupervisor(service, model, SupervisorLimits(max_model_calls=1))
    first = supervisor.analyze(SESSION, request_id, 1, MESSAGE)
    second = supervisor.analyze(SESSION, request_id, 1, MESSAGE)

    assert first["status"] == "analyzed"
    assert second["status"] == "supervisor_unavailable"
    assert second["model_call"]["model"] == "Model not run"
    assert second["instrumentation"]["total_tokens"] is None
    assert model.calls == 1


def test_chat_completion_carries_the_selected_revision_and_no_tools() -> None:
    service, request_id = _drafted()
    seen: list[bytes] = []

    def transport(*, url: str, headers: dict[str, str], body: bytes, timeout: float = 30) -> tuple[int, bytes]:
        del url, headers
        assert timeout == 30
        seen.append(body)
        return 200, json.dumps(
            {
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": (
                                "The saved revision asks for a binder.\n"
                                "Questions:\n"
                                "- Which scientific target should this request use?"
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            }
        ).encode()

    model = ChatCompletionModel(
        provider="openrouter",
        model="test-model",
        api_key="test-key",
        url="https://openrouter.ai/api/v1/chat/completions",
        transport=transport,
    )
    status, result = handle_design_supervisor(
        DesignRequestSupervisor(service, model, SupervisorLimits()),
        json.dumps(
            {"session_id": SESSION, "request_id": request_id, "revision": 1, "message": MESSAGE}
        ).encode(),
    )

    assert status == 200
    assert result["status"] == "analyzed"
    assert result["revision"] == 1
    assert result["message"] == MESSAGE
    assert result["supervisor_questions"] == []
    assert result["extracted_questions"][0]["source"] == "recorded"
    assert result["limits"]["max_output_tokens"] == 400
    assert result["limits"]["token_budget"] == 2000
    assert result["limits"]["retries"] == 0
    assert result["model_call"] == {
        "executed": True,
        "model": "test-model",
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
    payload = json.loads(seen[0])
    assert payload["max_tokens"] == 400
    assert "tools" not in payload
    assert "test-key" not in seen[0].decode()
    assert payload["messages"][0]["content"] == _QUESTION
    assert "Phrase only the supplied facts" not in payload["messages"][0]["content"]
    user = json.loads(payload["messages"][1]["content"])
    assert user["facts"][0]["attributes"]["message"] == MESSAGE
    assert user["facts"][0]["attributes"]["revision"] == "1"
    assert "researcher_information" in user["facts"][0]["attributes"]
    assert "server_configuration" in user["facts"][0]["attributes"]
    assert "missing_or_ambiguous" in user["facts"][0]["attributes"]
    assert "bindcraft" not in user["facts"][0]["attributes"]["server_configuration"]
    assert result["current"] is True
    assert len(service.revisions(request_id)) == 1
    assert PROPOSED_OPENROUTER_MODEL == "openai/gpt-4.1-mini"


def test_a_completed_call_without_token_counts_is_not_reported_as_zero() -> None:
    service, request_id = _drafted()

    class Omitted(ScriptedModel):
        def complete(self, *, facts: tuple[object, ...], question: str) -> ModelCompletion:
            self.calls += 1
            return ModelCompletion(
                text="The request asks for a binder.\nQuestions: none",
                usage=ModelUsageReport(provider="openrouter", model="test-model", total_tokens=None),
                origin=ResponseOrigin(provider="openrouter", model="served-model"),
            )

    result = _analyze(service, request_id, Omitted(""))

    assert result["status"] == "analysis_rejected"
    assert result["model_call"]["executed"] is True
    assert result["model_call"]["model"] == "served-model"
    assert result["model_call"]["input_tokens"] == "Not reported"
    assert result["model_call"]["output_tokens"] == "Not reported"
    assert result["model_call"]["total_tokens"] == "Not reported"
    assert result["instrumentation"]["total_tokens"] is None
    assert result["model_call"]["input_tokens"] != 0
    assert result["model_call"]["output_tokens"] != 0
    assert result["model_call"]["total_tokens"] != 0


def test_unknown_request_is_not_analyzed() -> None:
    service, _request_id = _drafted()
    status, result = handle_design_supervisor(
        DesignRequestSupervisor(service, ScriptedModel("unused")),
        json.dumps(
            {"session_id": SESSION, "request_id": "request_absent", "revision": 1, "message": MESSAGE}
        ).encode(),
    )
    assert status == 404
    assert result["status"] == "draft_not_found"
    assert result["model_call"]["model"] == "Model not run"
    assert result["instrumentation"]["total_tokens"] is None
    assert result["execution_authorized"] is False


class _PilotConfig:
    def __init__(self, tool_name: str | None, target_scope: str | None) -> None:
        self.tool_name = tool_name
        self.target_scope = target_scope

    def read(self) -> object:
        return self


def _saved(message: str) -> tuple[DesignDrafting, str]:
    session = pdl1_prototype_session()
    service = DesignDrafting({session.session_id: session}, DeterministicDraftExtractor())
    reply = service.revise(DesignDraftCommand(SESSION, message))
    assert reply.draft is not None
    return service, reply.draft.request_id


def _generator_record(service: DesignDrafting, request_id: str) -> tuple[int, object, str, str]:
    found = service.recorded_at(request_id, 1)
    assert found is not None
    draft, message = found
    field = next(item for item in draft.fields if item.name == "generator")
    return draft.revision, field.value, field.provenance, message


def test_a_pdl1_request_uses_the_configured_generator_without_rewriting_the_draft() -> None:
    message = "Design a binder for PDL1."
    service, request_id = _saved(message)
    before = _generator_record(service, request_id)
    model = ScriptedModel(
        "The saved design request text states the objective.\n"
        "Questions:\n"
        "- Which generator should be used? Documented adapters are bindcraft, rfdiffusion, mber, and fixture."
    )
    supervisor = DesignRequestSupervisor(service, model, None, _PilotConfig("bindcraft", "PDL1"))
    result = supervisor.analyze(SESSION, request_id, 1, message)

    assert result["status"] == "analyzed"
    assert result["configured_choices"] == [
        {
            "field": "generator",
            "value": "bindcraft",
            "text": "Generation method: BindCraft",
            "source": "server_configuration",
        }
    ]
    assert result["generator_configuration_limitation"] is None
    assert all(item["field"] != "generator" for item in result["extracted_questions"])
    assert any(item["field"] == "n_candidates" for item in result["extracted_questions"])
    assert result["supervisor_questions"] == []
    assert result["execution_authorized"] is False
    assert result["executable"] is False
    assert supervisor.records[0]["configured_choices"][0]["source"] == "server_configuration"
    attributes = dict(model.facts[0][0].attributes)  # type: ignore[attr-defined]
    server = json.loads(attributes["server_configuration"])
    assert server["configured_generator"]["value"] == "bindcraft"
    assert server["configured_generator"]["source"] == "server_configuration"
    missing = json.loads(attributes["missing_or_ambiguous"])
    assert all("generator" not in gap.casefold() for gap in missing["recorded_gaps"])
    researcher = json.loads(attributes["researcher_information"])
    assert researcher["text"] == message
    assert any(field["name"] == "target_id" and field["value"] == "PDL1" for field in researcher["fields"])
    assert all(field["name"] != "generator" for field in researcher["fields"])
    assert "blockade" not in attributes["researcher_information"].casefold()
    assert _generator_record(service, request_id) == before
    assert len(service.revisions(request_id)) == 1


def test_a_pdl1_request_without_compatible_configuration_does_not_assume_bindcraft() -> None:
    message = "Design a binder for PDL1."
    service, request_id = _saved(message)
    before = _generator_record(service, request_id)
    model = ScriptedModel("The saved design request text states the objective.\nQuestions: none")
    supervisor = DesignRequestSupervisor(service, model, None, _PilotConfig(None, "CD22"))
    result = supervisor.analyze(SESSION, request_id, 1, message)

    assert result["configured_choices"] == []
    assert result["generator_configuration_limitation"] == (
        "The server configuration does not name a generation method for this PDL1 request. "
        "BindCraft was not assumed."
    )
    assert "Generation method: BindCraft" not in json.dumps(result["configured_choices"])
    assert any(item["field"] == "generator" for item in result["extracted_questions"])
    assert _generator_record(service, request_id) == before


def test_another_target_does_not_receive_the_pdl1_generator() -> None:
    service, request_id = _drafted()
    model = ScriptedModel("The request asks for a binder.\nQuestions: none")
    supervisor = DesignRequestSupervisor(service, model, None, _PilotConfig("bindcraft", "PDL1"))
    result = supervisor.analyze(SESSION, request_id, 1, MESSAGE)

    assert result["configured_choices"] == []
    assert result["generator_configuration_limitation"] is None
    assert any(item["field"] == "generator" for item in result["extracted_questions"])
    assert "Generation method" not in json.dumps(result)


def test_a_generator_already_recorded_by_the_researcher_stays_unchanged() -> None:
    message = "Design a binder for PDL1 bindcraft."
    service, request_id = _saved(message)
    before = _generator_record(service, request_id)
    assert before[1] == "bindcraft"
    assert before[2] == "user"
    model = ScriptedModel("The saved design request text states the objective.\nQuestions: none")
    supervisor = DesignRequestSupervisor(service, model, None, _PilotConfig("bindcraft", "PDL1"))
    result = supervisor.analyze(SESSION, request_id, 1, message)

    assert result["configured_choices"] == []
    assert result["generator_configuration_limitation"] is None
    assert all(item["field"] != "generator" for item in result["extracted_questions"])
    assert _generator_record(service, request_id) == before
    assert len(service.revisions(request_id)) == 1


def test_supervisor_questions_stay_within_two_and_skip_answered_fields() -> None:
    service, request_id = _drafted()
    model = ScriptedModel(
        "The researcher wants a binder and has not named the target.\n"
        "Questions:\n"
        "- Which scientific target should this request use?\n"
        "- What design objective should be used? The documented objective is binder_design.\n"
        "- Which epitope should the binder prefer?\n"
        "- What expression host should be used?\n"
        "- Should the binder be humanized?"
    )
    result = _analyze(service, request_id, model)

    assert result["status"] == "analyzed"
    assert result["summary"] == "The researcher wants a binder and has not named the target."
    assert result["summary"] != MESSAGE
    assert [item["question"] for item in result["supervisor_questions"]] == [
        "Which epitope should the binder prefer?",
        "What expression host should be used?",
    ]
    assert "Which scientific target should this request use?" in result["validation"]["dropped_repeated_gaps"]
    assert (
        "What design objective should be used? The documented objective is binder_design."
        in result["validation"]["dropped_repeated_gaps"]
    )
    assert result["validation"]["dropped_extra_questions"] == ["Should the binder be humanized?"]
    assert result["execution_authorized"] is False
    assert len(service.revisions(request_id)) == 1


def test_a_paraphrased_candidate_question_is_the_same_field() -> None:
    service, request_id = _drafted()
    model = ScriptedModel(
        "The researcher wants a binder and has not said how many to request.\n"
        "Questions:\n"
        "- How many candidates do you want?\n"
        "- n_candidates: What quantity should be requested?\n"
        "- Should the binder prefer a particular epitope?"
    )
    result = _analyze(service, request_id, model)

    assert result["status"] == "analyzed"
    assert [item["question"] for item in result["extracted_questions"] if item["field"] == "n_candidates"] == [
        "How many candidates should be requested?"
    ]
    assert result["supervisor_questions"] == [
        {
            "question": "Should the binder prefer a particular epitope?",
            "source": "supervisor",
            "field": None,
        }
    ]
    assert "How many candidates do you want?" in result["validation"]["dropped_repeated_gaps"]
    assert "n_candidates: What quantity should be requested?" in result["validation"]["dropped_repeated_gaps"]
    assert result["execution_authorized"] is False


def test_a_revision_that_changes_during_the_call_stays_historical() -> None:
    service, request_id = _drafted()

    class Moving(ScriptedModel):
        def complete(self, *, facts: tuple[object, ...], question: str) -> ModelCompletion:
            updated = service.revise(DesignDraftCommand(SESSION, f"{MESSAGE}\nPDL1", request_id, 1))
            assert updated.draft is not None and updated.draft.revision == 2
            return super().complete(facts=facts, question=question)

    model = Moving(
        "The researcher wants a binder for a target that is not named.\n"
        "Questions:\n"
        "- Which epitope should the binder prefer?"
    )
    supervisor = DesignRequestSupervisor(service, model)
    result = supervisor.analyze(SESSION, request_id, 1, MESSAGE)

    assert model.calls == 1
    assert result["status"] == "analyzed"
    assert result["current"] is False
    assert result["request_id"] == request_id
    assert result["revision"] == 1
    assert result["message"] == MESSAGE
    assert result["supervisor_questions"] == [
        {"question": "Which epitope should the binder prefer?", "source": "supervisor", "field": None}
    ]
    assert result["execution_authorized"] is False
    assert result["executable"] is False
    assert supervisor.records[0]["revision"] == 1
    assert supervisor.records[0]["current"] is False
    assert len(service.revisions(request_id)) == 2


def test_stored_analyses_reload_after_the_store_is_reopened(tmp_path) -> None:
    from cellertz.application.request_supervisor_store import (
        SQLiteSupervisorAnalysisStore,
        open_supervisor_analysis_store,
    )

    draft_db = tmp_path / "design-drafts.sqlite"
    draft_db.write_bytes(b"existing-draft-rows")
    digest = hashlib.sha256(draft_db.read_bytes()).hexdigest()
    moments = iter(
        (
            datetime(2026, 11, 6, 21, 0, tzinfo=UTC),
            datetime(2026, 11, 6, 21, 5, tzinfo=UTC),
            datetime(2026, 11, 6, 21, 9, tzinfo=UTC),
        )
    )
    store = SQLiteSupervisorAnalysisStore(tmp_path / "design-supervisor-analyses.sqlite")
    service, request_id = _drafted()
    first_model = ScriptedModel("The first reading of the binder request.\nQuestions: none")
    supervisor = DesignRequestSupervisor(
        service,
        first_model,
        analyses=store,
        clock=lambda: next(moments),
    )
    first = supervisor.analyze(SESSION, request_id, 1, MESSAGE)
    refused = supervisor.analyze(SESSION, request_id, 1, f"{MESSAGE} edited")
    assert refused["status"] == "revision_conflict"
    assert first_model.calls == 1
    assert len(store.list_for(request_id)) == 1

    class Unavailable(ScriptedModel):
        def complete(self, *, facts: tuple[object, ...], question: str) -> ModelCompletion:
            self.calls += 1
            raise RuntimeError("provider down")

    failed_model = Unavailable("unused")
    failed = DesignRequestSupervisor(
        service, failed_model, analyses=store, clock=lambda: next(moments)
    ).analyze(SESSION, request_id, 1, MESSAGE)
    assert failed["status"] == "supervisor_unavailable"
    assert len(store.list_for(request_id)) == 1

    second = DesignRequestSupervisor(
        service,
        ScriptedModel("The second reading of the same revision.\nQuestions: none"),
        analyses=store,
        clock=lambda: next(moments),
    ).analyze(SESSION, request_id, 1, MESSAGE)
    updated = service.revise(DesignDraftCommand(SESSION, f"{MESSAGE}\nPDL1", request_id, 1))
    assert updated.draft is not None and updated.draft.revision == 2
    third = DesignRequestSupervisor(
        service,
        ScriptedModel("The reading after the revision changed.\nQuestions: none"),
        analyses=store,
        clock=lambda: datetime(2026, 11, 6, 21, 12, tzinfo=UTC),
    ).analyze(SESSION, request_id, 2, f"{MESSAGE}\nPDL1")
    store.close()

    reopened = SQLiteSupervisorAnalysisStore(tmp_path / "design-supervisor-analyses.sqlite")
    rows = reopened.list_for(request_id)
    assert [item["summary"] for item in rows] == [
        "The reading after the revision changed.",
        "The second reading of the same revision.",
        "The first reading of the binder request.",
    ]
    assert [item["revision"] for item in rows] == [2, 1, 1]
    assert rows[2]["analyzed_at"] == "2026-11-06T21:00:00+00:00"
    assert rows[0]["request_id"] == request_id
    assert reopened.list_for("request_other") == []
    status, listed = handle_list_supervisor_analyses(
        DesignRequestSupervisor(service, ScriptedModel("This must not be used.\nQuestions: none"), analyses=reopened),
        f"/api/design-supervisor?request_id={request_id}",
    )
    assert status == 200
    assert listed["analyses"][0]["summary"] == third["summary"]
    assert listed["analyses"][1]["summary"] == second["summary"]
    assert listed["analyses"][2]["analysis_id"] == first["analysis_id"]
    assert listed["execution_authorized"] is False
    reopened.close()
    assert hashlib.sha256(draft_db.read_bytes()).hexdigest() == digest
    assert open_supervisor_analysis_store({"CELLERTZ_DESIGN_DRAFT_STORE": "memory"}) is None
