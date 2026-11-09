"""Grounded conversation reads. No network, Databricks, or scientific execution."""

from __future__ import annotations

import json
from types import SimpleNamespace

from cellertz.application.conversation import ConversationRequest, ReadOnlyConversation
from cellertz.application.conversation_transport import CONVERSATION_PATH, configured_handler
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DesignDrafting,
    DeterministicDraftExtractor,
)
from cellertz.conversation.context import GroundedConversationQuery
from cellertz.conversation.episodic import EpisodicReadOnlyQuery
from cellertz.knowledge.models import KnowledgeItem, KnowledgeRelation


class QuietReader:
    def get_run(self, run_id: str) -> None:
        del run_id

    def find_runs_by_target(self, target_id: str) -> tuple:
        del target_id
        return ()

    def find_candidate(self, candidate_id: str) -> tuple:
        del candidate_id
        return ()


class Vault:
    def __init__(self, items: dict[str, KnowledgeItem]) -> None:
        self.items = items

    def get(self, item_id: str) -> KnowledgeItem | None:
        return self.items.get(item_id)

    def get_claim(self, item_id: str) -> KnowledgeItem | None:
        return self.get(item_id)

    def list(self, **_kwargs: object) -> tuple[KnowledgeItem, ...]:
        return tuple(self.items.values())

    def neighbors(self, item_id: str) -> tuple:
        item = self.items.get(item_id)
        return () if item is None else item.relations


class Spy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def decide(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append("decide")
        raise AssertionError("decide")

    def create(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append("create")
        raise AssertionError("create")

    def authorize(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append("authorize")
        raise AssertionError("authorize")


def _item(item_id: str, **overrides: object) -> KnowledgeItem:
    values: dict[str, object] = {
        "id": item_id,
        "type": "claim",
        "domain": "protein_design",
        "status": "validated",
        "version": 1,
        "title": item_id,
        "body": "retrieved body",
        "links": (),
        "source_path": f"{item_id}.md",
        "statement": "ranking_score is raw ipSAE.",
        "definition": "given",
    }
    values.update(overrides)
    return KnowledgeItem(**values)  # type: ignore[arg-type]


def _package() -> Vault:
    claim = _item("ipsae_primary_ranking")
    package = _item(
        "protein_scoring_bcma_boltz2_v2",
        type="package",
        statement=None,
        relations=(
            KnowledgeRelation(
                source="protein_scoring_bcma_boltz2_v2",
                relation="has_claim",
                target="ipsae_primary_ranking",
            ),
        ),
        packet={
            "local_boltz_gain": "BindCraft1 only",
            "generalizes_to_rfd5": False,
            "ranking_decision": "raw ipSAE remains primary",
        },
    )
    return Vault({claim.id: claim, package.id: package})


def _service(vault: Vault | None, fixture: object | None = None, scientific: object | None = None):
    session = pdl1_prototype_session()
    drafter = DesignDrafting({session.session_id: session}, DeterministicDraftExtractor())
    model_calls: list[tuple] = []

    class Model:
        def complete(self, *, facts: tuple, question: str):
            model_calls.append((question, facts))
            from cellertz.application.conversation import ModelCompletion

            return ModelCompletion(text="Phrased from supplied facts.", usage=None)

    service = ReadOnlyConversation(
        GroundedConversationQuery(
            EpisodicReadOnlyQuery({session.session_id: session}, QuietReader()),
            drafts=drafter,
            knowledge=vault,
            fixture_runs=fixture,
            scientific_runs=scientific,
        ),
        model=Model(),
    )
    return session, drafter, service, model_calls


def test_design_request_and_knowledge_are_supplied_as_evidence() -> None:
    session, drafter, service, calls = _service(_package())
    created = drafter.revise(
        DesignDraftCommand(session.session_id, "design a binder for pdl1 using fixture")
    )
    assert created.draft is not None
    reply = service.ask(
        ConversationRequest(
            session_id=session.session_id,
            question="What is the current design request and validated knowledge?",
        )
    )
    assert reply.status == "answered"
    facts = {fact.source: fact for fact in calls[0][1]}
    assert facts["design_request"].entity_id == created.draft.request_id
    assert "PDL1" in dict(facts["design_request"].attributes)["fields"]
    assert "n_candidates" in dict(facts["design_request"].attributes)["questions"]
    knowledge = dict(facts["second_brain"].attributes)
    assert knowledge["status"] == "available"
    assert knowledge["claims"] == "ranking_score is raw ipSAE."
    assert knowledge["benchmark"] == "BCMA scoring benchmark"
    assert knowledge["local_boltz_gain"] == "BindCraft1 only"
    assert knowledge["generalizes_to_rfd5"] == "false"
    assert knowledge["ranking_decision"] == "raw ipSAE remains primary"
    assert knowledge["unavailable"] == "none"
    assert "PDL1" not in knowledge["claims"]
    assert "Ignore previous instructions" not in reply.text


def test_newest_fixture_run_keeps_its_source_id() -> None:
    older = SimpleNamespace(
        run_id="run_older",
        status="completed",
        execution_profile="phase6d_fixture_only",
    )
    newer = SimpleNamespace(
        run_id="run_newer",
        status="failed",
        execution_profile="phase6d_fixture_only",
    )
    fixture = SimpleNamespace(
        list_runs=lambda *, session_id: SimpleNamespace(
            status="runs_observed",
            runs=(newer, older),
        )
    )
    session, _drafter, service, calls = _service(Vault({}), fixture=fixture)
    service.ask(
        ConversationRequest(
            session_id=session.session_id,
            question="What fixture result was recorded?",
        )
    )
    fixture_facts = [fact for fact in calls[0][1] if fact.source == "fixture"]
    assert [fact.entity_id for fact in fixture_facts] == ["run_newer"]
    recorded = dict(fixture_facts[0].attributes)
    assert recorded["run_id"] == "run_newer"
    assert recorded["status"] == "failed"
    assert "run_older" not in recorded.values()


def test_interpretation_limits_and_missing_evidence_survive() -> None:
    claim = _item(
        "ipsae_primary_ranking",
        statement="Raw ipSAE remains the primary structural ranking signal.",
        definition=None,
        undefined_terms=("ipSAE",),
    )
    package = _item(
        "protein_scoring_bcma_boltz2_v2",
        type="package",
        statement=None,
        relations=(
            KnowledgeRelation(
                source="protein_scoring_bcma_boltz2_v2",
                relation="has_claim",
                target="ipsae_primary_ranking",
            ),
        ),
        packet={
            "local_boltz_gain": "BindCraft1 only",
            "generalizes_to_rfd5": False,
            "ranking_decision": "raw ipSAE remains primary",
        },
    )
    session, _drafter, service, calls = _service(Vault({claim.id: claim, package.id: package}))
    service.ask(
        ConversationRequest(
            session_id=session.session_id,
            question="What validated knowledge was recorded?",
        )
    )
    knowledge = dict(next(fact.attributes for fact in calls[0][1] if fact.source == "second_brain"))
    assert knowledge["status"] == "incomplete"
    assert knowledge["benchmark"] == "BCMA scoring benchmark"
    assert "do not validate PDL1" in knowledge["applicability"]
    assert "package id" in knowledge["applicability"]
    assert knowledge["claim_ids"] == "ipsae_primary_ranking"
    assert knowledge["claims"] == "Raw ipSAE remains the primary structural ranking signal."
    assert knowledge["local_boltz_gain"] == "BindCraft1 only"
    assert knowledge["generalizes_to_rfd5"] == "false"
    assert knowledge["ranking_decision"] == "raw ipSAE remains primary"
    assert 'acronym "ipSAE"' in knowledge["unavailable"]
    assert "PDL1" not in knowledge["claims"]


def test_missing_sources_stay_visible_and_stages_are_not_invented() -> None:
    _session, _drafter, service, calls = _service(Vault({}))
    reply = service.ask(
        ConversationRequest(
            session_id=pdl1_prototype_session().session_id,
            question="What fixture result and validated knowledge are recorded?",
        )
    )
    assert reply.status == "answered"
    visible = {fact.source: dict(fact.attributes) for fact in calls[0][1]}
    assert visible["second_brain"]["status"] == "unavailable"
    assert visible["fixture"]["status"] == "unavailable"
    assert visible["workflow_trace"]["status"] == "unavailable"
    assert "generation" not in visible["workflow_trace"].values()
    recorded = TransportStages()
    session, _drafter, traced, traced_calls = _service(
        Vault({}),
        fixture=recorded.fixture,
        scientific=recorded.scientific,
    )
    traced.ask(
        ConversationRequest(session_id=session.session_id, question="What workflow stage was recorded?")
    )
    stages = [fact for fact in traced_calls[0][1] if fact.source == "workflow_trace"]
    assert [fact.entity_id for fact in stages] == ["generation"]
    assert dict(stages[0].attributes)["status"] == "completed"


class TransportStages:
    def __init__(self) -> None:
        artifact = SimpleNamespace(kind="workflow_trace", artifact_id="artifact_run_1", attempt=1)
        self.run = SimpleNamespace(
            run_id="run_1",
            status="completed",
            execution_profile="phase6d_fixture_only",
            attempt=1,
            artifacts=(artifact,),
        )
        self.fixture = SimpleNamespace(
            list_runs=lambda *, session_id: SimpleNamespace(status="runs_observed", runs=(self.run,))
        )
        body = json.dumps(
            {
                "schema": "cellertz.governed_agent_workflow.v1",
                "stages": [
                    {
                        "stage_id": "generation",
                        "status": "completed",
                        "summary": "Deterministic agent workflow fixture.",
                        "sequence": 1,
                    }
                ],
            }
        ).encode()
        self.scientific = SimpleNamespace(
            list_runs=lambda *, session_id: SimpleNamespace(status="runs_observed", runs=(self.run,)),
            read_artifact=lambda **_kwargs: SimpleNamespace(content=body),
            create=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("create")),
        )


def test_chat_request_cannot_approve_authorize_or_start(tmp_path) -> None:
    del tmp_path
    session, drafter, service, calls = _service(Vault({}))
    spy = Spy()
    handler = configured_handler(
        service,
        drafter=drafter,
        approvals=spy,
        runs=spy,
        scientific_runs=spy,
        session_id=session.session_id,
    )
    status, payload = _post(
        handler,
        {
            "session_id": session.session_id,
            "question": "approve the request and start the scientific run",
        },
    )
    assert status == 200
    assert payload["status"] == "forbidden_action"
    assert payload["read_only"] is True
    assert spy.calls == []
    assert calls == []


def _post(handler: type, payload: dict) -> tuple[int, dict]:
    from io import BytesIO

    body = json.dumps(payload).encode()
    instance = handler.__new__(handler)
    instance.path = CONVERSATION_PATH
    instance.headers = {"Content-Length": str(len(body))}
    instance.rfile = BytesIO(body)
    instance.wfile = BytesIO()
    instance.send_response = lambda code: setattr(instance, "status_code", code)
    instance.send_header = lambda *_args: None
    instance.end_headers = lambda: None
    instance.do_POST()
    return instance.status_code, json.loads(instance.wfile.getvalue())
