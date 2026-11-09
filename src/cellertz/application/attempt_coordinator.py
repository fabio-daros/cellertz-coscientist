"""Professor-to-coordinator flow for verified manual attempts.

The language model chooses tools and writes the English answer.
Metric cells, hashes, and 5 Å contacts are calculated in Python.
"""

from __future__ import annotations

import inspect
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cellertz.application.attempt_evidence import (
    compare_metrics,
    evidence_payload,
    geometric_contacts,
    link_review,
    list_attempts,
    load_catalog,
    merge_demonstration_attempts,
    propose_plan,
    resolve_attempt,
    specialist_for,
)
from cellertz.application.coordinator_harness import (
    VALIDATION_LIMITATIONS,
    CoordinatorLimits,
    rejection_text,
    resolve_coordinator_limits,
    validate_synthesis,
)
from cellertz.application.coordinator_trace import (
    MemoryCoordinatorTraceStore,
    open_coordinator_trace_store,
    trace_document,
)
from cellertz.conversation.config import resolve_conversation_model
from cellertz.conversation.model import ChatCompletionModel

PATH = "/api/attempt-coordinator"
_SUBSTANTIVE = {
    "get_attempt_evidence",
    "compare_model_metrics",
    "get_geometric_contacts",
    "link_structural_review",
    "propose_generation_plan",
    "propose_refinement",
}

_SYSTEM = (
    "You are the Protein Design coordinator for a professor. "
    "Interpret a request written in Portuguese or English. Reply in English. "
    "Call tools for every number, hash, contact count, and selection rule. "
    "Do not calculate those yourself and do not invent ipSAE, ranking_score, "
    "affinity, or experimental validation. "
    "Global pLDDT selection is not an interface assessment. "
    "A recorded zero, an empty field, and an absent value are different. "
    "Heavy-atom contacts at 5 angstroms are an independent geometric calculation, "
    "not a BindCraft interface metric. "
    "no_filters acceptance does not establish scientific quality. "
    "filter_policy no_filters is a recorded policy, not the absence of a policy. "
    "If the professor asks for a new generation or a refinement, call "
    "propose_generation_plan or propose_refinement. Do not fill filter_policy "
    "with no_filters unless the professor stated that policy. Chat text is not "
    "approval. Tell the professor to use Record approval in the review panel. "
    "Plan approval does not authorize execution. Binding an authorization is a "
    "separate human action and is not something the model can grant. "
    "A refinement is a new draft and does not start a run. "
    "If pdb_verified is false, say the structure file does not match the verified hash "
    "and do not describe contacts as calculated. "
    "pdb_verified is artifact verification. It does not establish execution "
    "compliance or scientific validation. "
    "execution_constraints comes from get_attempt_evidence. "
    "If execution_constraints_reported is false, say the review does not report "
    "execution constraints. That absence is not compliance. "
    "If unmet is true, cite requested_max_trajectories and the trajectory files. "
    "Cite the attempt id, each model sha256, and each pdb_url returned by the tool. "
    "Do not invent a reference. "
    "When no attempt id was supplied and one verified attempt is registered, use that attempt. "
    "Ask which attempt only when a tool says the choice is ambiguous. "
    "Pass registered attempt ids exactly, including the attempt. prefix. "
    "A bare suffix resolves only when one registered id ends with that suffix. "
    "If a tool status is unknown_attempt, report that error and the available ids. "
    "If a tool status is ambiguous, ask which registered id to use. "
    "An identifier error does not mean the evidence is unavailable. "
    "Do not substitute another attempt. "
    "A catalog list does not explain a selection. "
    "Do not infer why a model was selected from how many attempts exist. "
    "representative.rule chooses the PDB. accepted_order_rule only orders accepted designs. "
    "Do not describe the accepted-order rule as the reason the representative PDB was chosen. "
    "Binder pLDDT is not the selection rule. "
    "Quote tool fields. Do not call a recorded value incomplete. "
    "When a cell kind is empty_field, say empty field. Do not call it zero. "
    "A recorded_zero is a recorded zero. An absent model is not an empty field. "
    "The review field interface_residues is separate from the metric n_InterfaceResidues. "
    "An empty interface_residues field does not make n_InterfaceResidues empty or absent. "
    "compare_model_metrics returns valid_metrics. Request only those identifiers. "
    "If its status is unsupported_metrics, report that tool error and the valid identifiers. "
    "Do not say the measurement is unavailable, unrecorded, or absent because the name was rejected. "
    "When asked what to investigate next, name gaps the tools show: "
    "scientific evaluation pending, no_filters is not quality, platform ipSAE was not computed, "
    "the interface_residues field is empty, and model2 has no 5 angstrom contacts. "
    "If execution_constraints.unmet is true, report that unmet constraint and its recorded files. "
    "Do not say that no unmet trajectory execution constraints are reported. "
    "If execution constraints were not reported, say so. That is not compliance. "
    "Do not invent affinity, ranking_score, or an experimental result. "
    "Separate tool evidence from your interpretation. "
    "Do not reveal private reasoning."
)

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_verified_attempts",
            "description": "List verified manual attempts. Does not rank them.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_attempt_evidence",
            "description": (
                "Provenance, hashes, filter policy, execution_constraints, "
                "and the representative-selection rule. "
                "A missing execution_constraints object is not compliance."
            ),
            "parameters": {
                "type": "object",
                "properties": {"attempt_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_model_metrics",
            "description": (
                "Recorded per-model metric cells. Omit metrics to receive every cell. "
                "Each name must be one of valid_metrics. An unsupported name is an error "
                "and does not mean the cell is empty, zero, or absent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "attempt_id": {"type": "string"},
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Recorded metric identifiers. The response lists valid_metrics.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_geometric_contacts",
            "description": "Independently calculated heavy-atom contacts at 5 angstroms for both models.",
            "parameters": {
                "type": "object",
                "properties": {"attempt_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link_structural_review",
            "description": "Links to the structural review and the verified PDB files.",
            "parameters": {
                "type": "object",
                "properties": {"attempt_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_generation_plan",
            "description": (
                "Record a generation draft from fields the professor stated. "
                "Omit unknown fields. Do not invent hashes, seeds, or no_filters. "
                "Does not approve or launch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_text": {"type": "string"},
                    "target_identity": {"type": "string"},
                    "input_artifacts": {
                        "type": "array",
                        "description": (
                            "Only artifacts the professor named. Each item needs role and sha256. "
                            "Do not invent a hash."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["role", "sha256"],
                            "additionalProperties": False,
                        },
                    },
                    "binder_length": {"type": "integer"},
                    "hotspot_assumption": {"type": "string"},
                    "filter_policy": {"type": "string"},
                    "seed": {"type": "integer"},
                    "cpu_count": {"type": "integer"},
                    "memory_mib": {"type": "integer"},
                    "gpu": {"type": "string"},
                    "execution_deadline_seconds": {"type": "integer"},
                    "unresolved_questions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["request_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_refinement",
            "description": "Draft a new plan revision from a verified result. Does not start a run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "change_note": {"type": "string"},
                },
                "required": ["plan_id", "change_note"],
                "additionalProperties": False,
            },
        },
    },
]


Complete = Callable[[list[dict[str, Any]], list[dict[str, Any]], str], dict[str, Any]]


@dataclass
class _Memory:
    attempt_id: str | None = None
    models: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    conclusions: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    conversation_id: str = ""
    request_id: str | None = None
    request_revision: int | None = None


class AttemptCoordinator:
    """One in-process conversation memory. Tools never call a provider."""

    def __init__(
        self,
        catalog: dict[str, Any],
        complete: Complete | None,
        plans: object | None = None,
        traces: object | None = None,
        limits: CoordinatorLimits | None = None,
        model_record: dict[str, str] | None = None,
    ) -> None:
        self._catalog = catalog
        self._complete = complete
        self._plans = plans
        self._traces = traces if traces is not None else MemoryCoordinatorTraceStore()
        self._limits = limits if limits is not None else CoordinatorLimits()
        self._model_record = model_record
        self._memory: dict[str, _Memory] = {}
        self._lock = threading.Lock()

    def traces_for(self, attempt_id: str) -> list[dict[str, Any]]:
        reader = getattr(self._traces, "for_attempt", None)
        if not callable(reader):
            return []
        found = reader(attempt_id)
        return found if isinstance(found, list) else []

    def traces_for_request(self, request_id: str, request_revision: int) -> list[dict[str, Any]]:
        reader = getattr(self._traces, "for_request", None)
        if not callable(reader):
            return []
        found = reader(request_id, request_revision)
        return found if isinstance(found, list) else []

    def ask(
        self,
        question: str,
        conversation_id: str | None = None,
        request_id: str | None = None,
        request_revision: int | None = None,
    ) -> dict[str, Any]:
        if not question.strip():
            return _reply("unsupported_question", "The question is empty.", conversation_id or "", None, [], [])
        with self._lock:
            return self._ask_locked(question, conversation_id, request_id, request_revision)

    def _ask_locked(
        self,
        question: str,
        conversation_id: str | None,
        request_id: str | None,
        request_revision: int | None,
    ) -> dict[str, Any]:
        merge_demonstration_attempts(self._catalog)
        conversation = conversation_id or f"coord_{uuid4().hex}"
        memory = self._memory.setdefault(conversation, _Memory())
        memory.conversation_id = conversation
        binding = _bind_request(memory, request_id, request_revision)
        if binding is not None:
            return _reply("unsupported_question", binding, conversation, memory.attempt_id, [], [])
        if self._complete is None:
            return self._finish(
                question,
                memory,
                "provider_unavailable",
                "The language model is not configured. No fixture answer was substituted.",
                [],
                [],
                {"calls": 0, "reported_calls": 0, "total_tokens": None, "input_tokens": None, "output_tokens": None},
                "provider_unavailable",
            )
        memory.messages.append({"role": "user", "content": question})
        steps: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        account = _usage_account()
        try:
            for _round in range(self._limits.max_model_calls):
                choice = "required" if not _has_substantive_evidence(memory) else "auto"
                account["calls"] += 1
                message = self._complete(_transcript(memory), _tools(self._catalog), choice)
                _note_usage(account, message)
                if _over_budget(self._limits, account):
                    steps.append(
                        {
                            "specialist": "prioritization",
                            "mode": "language_model",
                            "tool": None,
                            "summary": (
                                "Stopped because the provider-reported token total passed the configured budget. "
                                "The draft was not accepted and no further tool was called."
                            ),
                            "status": "budget_interrupted",
                            "rejected_output": str(message.get("content") or "").strip() or None,
                        }
                    )
                    return self._finish(
                        question,
                        memory,
                        "budget_interrupted",
                        (
                            "The coordinator stopped because the provider-reported token total "
                            "passed the configured budget. No conclusion was accepted."
                        ),
                        steps,
                        evidence,
                        account,
                        "token_budget",
                    )
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    text = str(message.get("content") or "").strip()
                    if not _has_substantive_evidence(memory):
                        memory.messages.append({"role": "assistant", "content": text})
                        memory.messages.append({"role": "user", "content": _recovery_prompt(memory)})
                        continue
                    if not text:
                        raise RuntimeError("provider_unavailable")
                    text = _reflect_saved_plan(text, memory.plan)
                    text = _cite_recorded_evidence(text, memory.links or evidence)
                    validation = validate_synthesis(text, memory.links or evidence)
                    if validation["status"] != "accepted":
                        steps.append(
                            {
                                "specialist": "prioritization",
                                "mode": "language_model",
                                "tool": None,
                                "summary": "The harness rejected this synthesis. It was not published as a conclusion.",
                                "status": "synthesis_rejected",
                                "rejected_output": text,
                            }
                        )
                        return self._finish(
                            question,
                            memory,
                            "synthesis_rejected",
                            rejection_text(validation),
                            steps,
                            evidence,
                            account,
                            "synthesis_rejected",
                            validation,
                            text,
                        )
                    memory.conclusions.append(text)
                    memory.messages.append({"role": "assistant", "content": text})
                    steps.append(
                        {
                            "specialist": "prioritization",
                            "mode": "language_model",
                            "tool": None,
                            "summary": "Interpreted the tool evidence in English. This is not a new measurement.",
                            "status": "accepted",
                        }
                    )
                    return self._finish(
                        question,
                        memory,
                        "answered",
                        text,
                        steps,
                        evidence,
                        account,
                        "answered",
                        validation,
                    )
                memory.messages.append(
                    {"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls}
                )
                for call in tool_calls:
                    step, payload, link = self._run_tool(memory, question, call)
                    steps.append(step)
                    if link is not None:
                        evidence.append(link)
                        memory.links.append(link)
                    memory.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or step["tool"],
                            "content": json.dumps(payload, sort_keys=True),
                        }
                    )
        except RuntimeError as exc:
            code = str(exc)
            status = code if code in {"provider_unavailable", "provider_budget"} else "provider_unavailable"
            return self._finish(
                question,
                memory,
                status,
                _PROVIDER_TEXT[status],
                steps,
                evidence,
                account,
                status,
            )
        return self._finish(
            question,
            memory,
            "tool_failure",
            "The coordinator stopped after the tool limit. No fixture answer was substituted.",
            steps,
            evidence,
            account,
            "model_call_limit",
        )

    def _finish(
        self,
        question: str,
        memory: _Memory,
        status: str,
        text: str,
        steps: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        account: dict[str, Any],
        stop: str,
        validation: dict[str, Any] | None = None,
        rejected_output: str | None = None,
    ) -> dict[str, Any]:
        reply = _reply(status, text, memory.conversation_id, memory.attempt_id, steps, evidence, memory.plan)
        accepted = status == "answered"
        reply["request_id"] = memory.request_id
        reply["request_revision"] = memory.request_revision
        reply["model"] = self._model_record
        reply["model_calls"] = account["calls"]
        reply["usage"] = {
            "reported_calls": account["reported_calls"],
            "total_tokens": account["total_tokens"],
            "input_tokens": account["input_tokens"],
            "output_tokens": account["output_tokens"],
        }
        reply["limits"] = self._limits.to_dict()
        reply["validation"] = validation
        reply["termination"] = {"status": status, "stop": stop}
        reply["conclusion_accepted"] = accepted
        reply["rejected_output"] = rejected_output
        reply["limitations"] = list(VALIDATION_LIMITATIONS)
        append = getattr(self._traces, "append", None)
        if callable(append) and question.strip():
            append(trace_document(question, reply, steps))
        return reply

    def _run_tool(
        self, memory: _Memory, question: str, call: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(function.get("name") or "")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        if name not in {item["function"]["name"] for item in _TOOLS}:
            payload = {"status": "unknown_tool", "tool": name, "error": "The requested tool is not available."}
            reference = _evidence_reference(name or "unknown_tool", arguments, payload)
            step = _step("ranking", name or "unknown_tool", "The requested tool is not available.")
            step["status"] = reference["status"]
            step["arguments"] = reference["arguments"]
            step["evidence_references"] = reference["references"]
            return step, payload, reference
        specialist, summary = specialist_for(name)
        attempt_id = arguments.get("attempt_id")
        attempt_id = attempt_id if isinstance(attempt_id, str) and attempt_id else None
        if name == "list_verified_attempts":
            payload = list_attempts(self._catalog)
        elif name == "propose_generation_plan":
            proposal = dict(arguments)
            proposal["request_text"] = question
            proposal.pop("approved", None)
            payload = _draft_generation(self, memory, attempt_id, proposal)
        elif name == "propose_refinement":
            plan_id = arguments.get("plan_id")
            note = arguments.get("change_note")
            if self._plans is None or not isinstance(plan_id, str) or not isinstance(note, str):
                payload = {"status": "plan_absent", "approved": False, "execution_connected": False}
            else:
                payload = self._plans.refine(plan_id, note)
                if payload.get("plan_id"):
                    memory.plan = payload
        else:
            resolved = resolve_attempt(self._catalog, attempt_id, memory.attempt_id)
            if resolved["status"] != "ok":
                payload = _without_item(resolved)
            else:
                chosen = str(resolved["resolved_attempt_id"])
                memory.attempt_id = chosen
                item = resolved["item"]
                if name == "get_attempt_evidence":
                    payload = evidence_payload(chosen, item)
                    memory.models = list(payload["models"])
                elif name == "compare_model_metrics":
                    names = arguments.get("metrics")
                    payload = compare_metrics(item, names if isinstance(names, list) else None)
                elif name == "get_geometric_contacts":
                    payload = geometric_contacts(item)
                else:
                    payload = link_review(chosen, item)
                payload["requested_id"] = resolved["requested_id"]
                payload["resolved_attempt_id"] = chosen
                payload.setdefault("status", "ok")
        if isinstance(payload, dict) and payload.get("status") == "hash_mismatch":
            summary = "Refused to calculate from a PDB whose hash does not match."
        if isinstance(payload, dict) and payload.get("status") in {"unknown_attempt", "ambiguous"}:
            summary = str(payload.get("error") or summary)
        status = payload.get("status") if isinstance(payload, dict) else None
        memory.evidence.append(
            {"tool": name, "specialist": specialist, "status": status if isinstance(status, str) else "ok"}
        )
        reference = _evidence_reference(name, arguments, payload)
        step = _step(specialist, name, summary)
        step["status"] = reference["status"]
        step["arguments"] = reference["arguments"]
        step["requested_id"] = payload.get("requested_id") if isinstance(payload, dict) else None
        step["resolved_attempt_id"] = payload.get("resolved_attempt_id") if isinstance(payload, dict) else None
        step["evidence_references"] = reference["references"]
        return step, payload, reference


def coordinator_from_environment(
    plans: object | None = None,
    traces: object | None = None,
) -> AttemptCoordinator:
    """Use the configured chat model. A recorded-facts model is not a fallback."""

    limits = resolve_coordinator_limits()
    model = resolve_conversation_model()
    complete = None
    model_record = None
    if isinstance(model, ChatCompletionModel):
        complete = _complete_with(model, limits)
        model_record = {"provider": model._provider, "name": model._model}
    store = open_coordinator_trace_store() if traces is None else traces
    return AttemptCoordinator(load_catalog(), complete, plans, store, limits, model_record)


def _complete_with(
    model: ChatCompletionModel,
    limits: CoordinatorLimits | None = None,
) -> Complete:
    active = limits if limits is not None else CoordinatorLimits()

    def complete(
        messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str
    ) -> dict[str, Any]:
        payload = {
            "model": model._model,
            "stream": False,
            "max_tokens": active.max_output_tokens_per_call,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        body = json.dumps(payload).encode("utf-8")
        call = {
            "url": model._url,
            "headers": {
                "authorization": f"Bearer {model._api_key}",
                "content-type": "application/json",
            },
            "body": body,
        }
        if _accepts_timeout(model._transport):
            call["timeout"] = active.call_timeout_seconds
        try:
            status, raw = model._transport(**call)
        except Exception as exc:
            raise RuntimeError("provider_unavailable") from exc
        if status == 402:
            raise RuntimeError("provider_budget")
        if status != 200:
            raise RuntimeError("provider_unavailable")
        return _message(raw)

    return complete


def _accepts_timeout(transport: object) -> bool:
    try:
        return "timeout" in inspect.signature(transport).parameters
    except (TypeError, ValueError):
        return False


def _message(raw: bytes) -> dict[str, Any]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("provider_unavailable") from exc
    message = _provider_message(document)
    if message is None:
        raise RuntimeError("provider_unavailable")
    # Drop provider reasoning fields. Only the answer, tool calls, and token counts leave.
    public: dict[str, Any] = {
        "content": message.get("content") or "",
        "tool_calls": message.get("tool_calls") or [],
    }
    usage = _public_usage(document.get("usage") if isinstance(document, dict) else None)
    if usage:
        public["usage"] = usage
    return public


def _provider_message(document: object) -> dict[str, Any] | None:
    if not isinstance(document, dict):
        return None
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    return message


def _transcript(memory: _Memory) -> list[dict[str, Any]]:
    context = {
        "selected_attempt": memory.attempt_id,
        "models_consulted": memory.models,
        "evidence_tools": [item["tool"] for item in memory.evidence],
        "previous_conclusions": memory.conclusions[-4:],
        "generation_plan_id": None if memory.plan is None else memory.plan.get("plan_id"),
        "generation_plan_revision": None if memory.plan is None else memory.plan.get("revision"),
        "generation_plan_approved": False if memory.plan is None else memory.plan.get("approved"),
    }
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "system", "content": "Conversation context: " + json.dumps(context, sort_keys=True)},
        *memory.messages,
    ]


def _draft_generation(
    coordinator: AttemptCoordinator,
    memory: _Memory,
    attempt_id: str | None,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    based_on = memory.attempt_id
    requested_id = None
    if attempt_id:
        resolved = resolve_attempt(coordinator._catalog, attempt_id, None)
        if resolved["status"] != "ok":
            return _without_item(resolved)
        based_on = resolved["resolved_attempt_id"]
        memory.attempt_id = based_on
        requested_id = resolved["requested_id"]
    if coordinator._plans is None:
        payload = propose_plan(based_on, proposal["request_text"])
    else:
        payload = coordinator._plans.draft(memory_id(memory), proposal)
        memory.plan = payload
    payload["requested_id"] = requested_id
    payload["resolved_attempt_id"] = based_on
    return payload


def _without_item(resolved: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in resolved.items() if key != "item"}


def _has_substantive_evidence(memory: _Memory) -> bool:
    if any(
        item["tool"] in _SUBSTANTIVE and item.get("status") != "unknown_attempt"
        for item in memory.evidence
    ):
        return True
    return _identifier_recovery_sent(memory)


def _identifier_recovery_sent(memory: _Memory) -> bool:
    return any(
        item.get("role") == "user" and "attempt identifier was not resolved" in str(item.get("content"))
        for item in memory.messages
    )


def _cite_recorded_evidence(text: str, evidence: list[dict[str, Any]]) -> str:
    """The saved tool evidence decides constraint compliance. The model cannot clear it."""

    constraints = _recorded_constraints(evidence)
    if constraints is None:
        if _claims_constraint_compliance(text):
            return _absent_constraint_text(evidence)
        return text
    citation = _constraint_citation(constraints, evidence)
    if constraints.get("unmet") is True and _claims_no_unmet_constraint(text):
        return citation
    if citation in text:
        return text
    return text.rstrip() + "\n\n" + citation


def _absent_constraint_text(evidence: list[dict[str, Any]]) -> str:
    if _evidence_tool_called(evidence, "get_attempt_evidence"):
        lead = "The review does not report execution constraints."
    else:
        lead = "Execution constraint evidence was not retrieved."
    return (
        f"{lead} That absence is not compliance. "
        "Artifact verification is separate from execution compliance and scientific validation."
    )


def _recorded_constraints(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in evidence:
        if item.get("tool") != "get_attempt_evidence":
            continue
        for reference in item.get("references") or []:
            if not isinstance(reference, dict) or reference.get("kind") != "attempt_record":
                continue
            if reference.get("execution_constraints_reported") is not True:
                return None
            constraints = reference.get("execution_constraints")
            if isinstance(constraints, dict):
                return constraints
    return None


def _claims_constraint_compliance(text: str) -> bool:
    return _claims_no_unmet_constraint(text)


def _claims_no_unmet_constraint(text: str) -> bool:
    lowered = text.casefold()
    return any(
        phrase in lowered
        for phrase in (
            "no unmet",
            "none are reported",
            "constraints are not reported",
            "constraints were not reported",
            "constraints were met",
            "constraint was met",
        )
    )


def _constraint_citation(constraints: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    attempt_id = _attempt_reference(evidence)
    accepted = constraints.get("accepted_trajectory_files") or []
    low = constraints.get("low_confidence_trajectory_files") or []
    record = _attempt_record(evidence)
    lines = [
        f"Recorded execution constraints for {attempt_id}: unmet is {constraints.get('unmet')}.",
        f"requested_max_trajectories is {constraints.get('requested_max_trajectories')}.",
        "Accepted trajectory files: " + ", ".join(str(item) for item in accepted) + ".",
        "Low-confidence trajectory files: " + ", ".join(str(item) for item in low) + ".",
        "Artifact verification is separate from execution compliance and scientific validation.",
        (
            "execution_compliance_established is "
            f"{record.get('execution_compliance_established')}."
        ),
        (
            "scientific_validation_established is "
            f"{record.get('scientific_validation_established')}."
        ),
    ]
    provenance = record.get("provenance_references")
    if isinstance(provenance, dict):
        for key in ("stats_csv_sha256", "recorded_seed", "recorded_rank", "recorded_filters"):
            if key in provenance:
                lines.append(f"{key} {provenance[key]}.")
    for model_id, model in _model_references(evidence).items():
        lines.append(f"{model_id} sha256 {model.get('sha256')} pdb_url {model.get('pdb_url')}.")
    lines.extend(_metric_citations(evidence))
    lines.extend(_geometry_citations(evidence))
    lines.extend(_review_citations(evidence))
    return " ".join(lines)


def _provenance_references(provenance: object) -> dict[str, Any]:
    if not isinstance(provenance, dict):
        return {}
    keys = ("stats_csv_sha256", "recorded_seed", "recorded_rank", "recorded_filters")
    return {key: provenance[key] for key in keys if key in provenance}


def _attempt_record(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    for item in evidence:
        if item.get("tool") != "get_attempt_evidence":
            continue
        for reference in item.get("references") or []:
            if isinstance(reference, dict) and reference.get("kind") == "attempt_record":
                return reference
    return {}


def _metric_citations(evidence: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in evidence:
        if item.get("tool") != "compare_model_metrics":
            continue
        for reference in item.get("references") or []:
            rows = reference.get("metrics") if isinstance(reference, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("metric"), str):
                    continue
                for model_id in ("model1", "model2", "average"):
                    cell = row.get(model_id)
                    if isinstance(cell, dict) and isinstance(cell.get("kind"), str):
                        shown = cell["kind"]
                        if isinstance(cell.get("text"), str):
                            shown = f"{shown} {cell['text']}"
                        lines.append(f"{row['metric']} {model_id} {shown}.")
    return lines


def _geometry_citations(evidence: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in evidence:
        if item.get("tool") != "get_geometric_contacts":
            continue
        for reference in item.get("references") or []:
            models = reference.get("models") if isinstance(reference, dict) else None
            if not isinstance(models, dict):
                continue
            for model_id, model in models.items():
                if not isinstance(model, dict):
                    continue
                calculated = model.get("calculated")
                if not isinstance(calculated, dict):
                    continue
                lines.append(
                    f"{model_id} geometric residue_pairs {calculated.get('residue_pairs')} "
                    f"sha256 {model.get('sha256')}."
                )
    return lines


def _review_citations(evidence: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in evidence:
        if item.get("tool") != "link_structural_review":
            continue
        for reference in item.get("references") or []:
            if isinstance(reference, dict) and isinstance(reference.get("href"), str):
                lines.append(f"Structural review reference: {reference['href']}.")
    return lines


def _evidence_tool_called(evidence: list[dict[str, Any]], name: str) -> bool:
    return any(item.get("tool") == name for item in evidence)


def _attempt_reference(evidence: list[dict[str, Any]]) -> str:
    for item in evidence:
        for reference in item.get("references") or []:
            if isinstance(reference, dict) and isinstance(reference.get("attempt_id"), str):
                return reference["attempt_id"]
    return "the reviewed attempt"


def _model_references(evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for item in evidence:
        if item.get("tool") != "get_attempt_evidence":
            continue
        for reference in item.get("references") or []:
            recorded = reference.get("models") if isinstance(reference, dict) else None
            if not isinstance(recorded, dict):
                continue
            for model_id, model in recorded.items():
                if isinstance(model_id, str) and isinstance(model, dict):
                    models[model_id] = model
    return models


def _reflect_saved_plan(text: str, plan: dict[str, Any] | None) -> str:
    """The saved plan decides which questions remain. The model cannot clear them."""

    if not isinstance(plan, dict):
        return text
    questions = [item for item in plan.get("questions") or [] if isinstance(item, dict)]
    if not questions:
        return text
    lines = [
        f"- {item.get('field')}: {item.get('question')}"
        for item in questions
        if isinstance(item.get("question"), str)
    ]
    notice = "This plan revision is unapproved. Open questions remain:\n" + "\n".join(lines)
    lowered = text.casefold()
    if "question" in lowered and "resolv" in lowered:
        return notice
    return text + "\n\n" + notice


def _recovery_prompt(memory: _Memory) -> str:
    latest = memory.evidence[-1]["status"] if memory.evidence else None
    if latest == "unknown_attempt":
        return (
            "The attempt identifier was not resolved. "
            "Report requested_id, the tool error, and available_attempt_ids. "
            "Do not conclude that the evidence is unavailable. "
            "Do not select a different attempt."
        )
    return (
        "A catalog list is not evidence for a conclusion. "
        "Call get_attempt_evidence before answering. "
        "For an interface comparison, also call compare_model_metrics "
        "and get_geometric_contacts. For a new generation, call "
        "propose_generation_plan and do not assume no_filters."
    )


def _tools(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    names: list[str] = []
    for item in catalog.values():
        record = item.get("record") if isinstance(item, dict) else None
        if not isinstance(record, dict):
            continue
        for row in record.get("metrics") or []:
            name = row.get("metric") if isinstance(row, dict) else None
            if isinstance(name, str) and name not in names:
                names.append(name)
    tools = json.loads(json.dumps(_TOOLS))
    registered = [attempt_id for attempt_id in catalog if isinstance(attempt_id, str)]
    hint = "Registered attempt id"
    if registered:
        hint += ": " + ", ".join(registered)
    hint += ". A unique bare suffix is accepted and returned as resolved_attempt_id."
    for tool in tools:
        properties = tool["function"]["parameters"].get("properties") or {}
        if "attempt_id" in properties:
            properties["attempt_id"]["description"] = hint
    for tool in tools:
        if tool["function"]["name"] != "compare_model_metrics":
            continue
        items: dict[str, Any] = {"type": "string"}
        if names:
            items["enum"] = names
        tool["function"]["parameters"]["properties"]["metrics"]["items"] = items
        tool["function"]["description"] += " Valid identifiers: " + ", ".join(names) + "."
    return tools


def _sanitized_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    attempt_id = arguments.get("attempt_id")
    if isinstance(attempt_id, str):
        clean["attempt_id"] = attempt_id
    metrics = arguments.get("metrics")
    if isinstance(metrics, list):
        clean["metrics"] = [name if isinstance(name, str) else "non_string_metric" for name in metrics]
    elif "metrics" in arguments:
        clean["metrics"] = "invalid"
    return clean


def _evidence_reference(name: str, arguments: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status") if isinstance(payload.get("status"), str) else "ok"
    references: list[dict[str, Any]] = []
    if name == "compare_model_metrics":
        references.append(
            {
                "kind": "metric_cells",
                "attempt_id": payload.get("attempt_id"),
                "status": status,
                "rejected_metrics": payload.get("rejected_metrics") or [],
                "valid_metrics": payload.get("valid_metrics") or [],
                "error": payload.get("error"),
                "artifacts": payload.get("artifacts") or [],
                "metrics": payload.get("metrics") or [],
            }
        )
    elif name == "get_attempt_evidence":
        references.append(
            {
                "kind": "attempt_record",
                "attempt_id": payload.get("attempt_id"),
                "pdb_verified": payload.get("pdb_verified"),
                "artifact_verification": payload.get("artifact_verification"),
                "execution_constraints": payload.get("execution_constraints"),
                "execution_constraints_reported": payload.get("execution_constraints_reported"),
                "provenance_references": _provenance_references(payload.get("provenance")),
                "absence_of_execution_constraints_is_not_compliance": payload.get(
                    "absence_of_execution_constraints_is_not_compliance"
                ),
                "execution_compliance_established": payload.get("execution_compliance_established"),
                "scientific_validation_established": payload.get("scientific_validation_established"),
                "interface_residues": payload.get("interface_residues"),
                "interface_residues_note": payload.get("interface_residues_note"),
                "models": payload.get("models"),
                "ranking_score": payload.get("ranking_score"),
            }
        )
    elif name == "list_verified_attempts":
        attempts = payload.get("attempts") if isinstance(payload.get("attempts"), list) else []
        references.append(
            {
                "kind": "attempt_catalog",
                "attempt_ids": [
                    item.get("attempt_id") for item in attempts if isinstance(item, dict)
                ],
            }
        )
    elif name == "get_geometric_contacts":
        references.append(
            {
                "kind": "geometric_contacts",
                "status": status,
                "pdb_verified": payload.get("pdb_verified"),
                "models": payload.get("models"),
            }
        )
    elif name == "link_structural_review" and "review_url" in payload:
        references.append(
            {
                "kind": "structural_review",
                "label": f"Structural review for {payload.get('attempt_id')}",
                "href": payload["review_url"],
                "models": payload.get("models"),
            }
        )
    elif status != "ok":
        references.append({"kind": "tool_error", "tool": name, "status": status, "error": payload.get("error")})
    return {
        "tool": name,
        "status": status,
        "arguments": _sanitized_arguments(arguments),
        "requested_id": payload.get("requested_id"),
        "resolved_attempt_id": payload.get("resolved_attempt_id"),
        "references": references,
    }


def _step(specialist: str, tool: str, summary: str) -> dict[str, Any]:
    return {"specialist": specialist, "mode": "deterministic", "tool": tool, "summary": summary}


def _reply(
    status: str,
    text: str,
    conversation_id: str,
    attempt_id: str | None,
    steps: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "text": text,
        "conversation_id": conversation_id,
        "attempt_id": attempt_id,
        "steps": steps,
        "evidence": evidence,
        "plan": plan,
        "execution_connected": False,
        "read_only": True,
    }


def memory_id(memory: _Memory) -> str:
    return memory.conversation_id or "conversation"


_PROVIDER_TEXT = {
    "provider_unavailable": "The language model is unavailable. No fixture answer was substituted.",
    "provider_budget": "The language provider reached its budget. No fixture answer was substituted.",
}


def handle_attempt_coordinator(coordinator: AttemptCoordinator, body: bytes) -> tuple[int, dict[str, Any]]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, _reply("unsupported_question", "The request was not valid JSON.", "", None, [], [])
    if not isinstance(payload, dict):
        return 400, _reply("unsupported_question", "The request was not valid JSON.", "", None, [], [])
    question = payload.get("question")
    conversation_id = payload.get("conversation_id")
    request_id = payload.get("request_id") if "request_id" in payload else None
    request_revision = payload.get("request_revision") if "request_revision" in payload else None
    reply = coordinator.ask(
        question if isinstance(question, str) else "",
        conversation_id if isinstance(conversation_id, str) and conversation_id else None,
        request_id if isinstance(request_id, str) else ("" if request_id is not None else None),
        request_revision if type(request_revision) is int else None,
    )
    return (400 if reply["status"] == "unsupported_question" else 200), reply


def handle_attempt_coordinator_read(coordinator: AttemptCoordinator, raw_path: str) -> tuple[int, dict[str, Any]]:
    query = parse_qs(urlsplit(raw_path).query, keep_blank_values=True)
    request_id = (query.get("request_id") or [""])[0]
    revision_raw = (query.get("request_revision") or [""])[0]
    if not _valid_request_id(request_id) or not revision_raw.isdigit() or int(revision_raw) < 1:
        return 400, {
            "status": "unsupported_question",
            "traces": [],
            "limitations": list(VALIDATION_LIMITATIONS),
        }
    return 200, {
        "traces": coordinator.traces_for_request(request_id, int(revision_raw)),
        "limitations": list(VALIDATION_LIMITATIONS),
    }


def _bind_request(memory: _Memory, request_id: str | None, request_revision: int | None) -> str | None:
    if request_id is None and request_revision is None:
        return None
    if not isinstance(request_id, str) or not _valid_request_id(request_id):
        return "The request id is not valid."
    if type(request_revision) is not int or request_revision < 1:
        return "The request revision is not valid."
    if memory.request_id is None:
        memory.request_id = request_id
        memory.request_revision = request_revision
        return None
    if memory.request_id != request_id or memory.request_revision != request_revision:
        return "This conversation is already bound to another request revision."
    return None


def _valid_request_id(value: str) -> bool:
    if not value.startswith("request_") or len(value) > 80:
        return False
    suffix = value.removeprefix("request_")
    return bool(suffix) and suffix.isalnum()


def _usage_account() -> dict[str, Any]:
    return {
        "calls": 0,
        "reported_calls": 0,
        "total_tokens": None,
        "input_tokens": None,
        "output_tokens": None,
    }


def _note_usage(account: dict[str, Any], message: object) -> None:
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return
    total = usage.get("total_tokens")
    if type(total) is not int or total < 0:
        return
    account["reported_calls"] += 1
    account["total_tokens"] = (account["total_tokens"] or 0) + total
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if type(value) is int and value >= 0:
            account[key] = (account[key] or 0) + value


def _over_budget(limits: CoordinatorLimits, account: dict[str, Any]) -> bool:
    total = account["total_tokens"]
    if limits.token_budget is None or type(total) is not int:
        return False
    return total > limits.token_budget


def _public_usage(usage: object) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    public: dict[str, int] = {}
    for source, target in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        value = usage.get(source)
        if type(value) is int and value >= 0:
            public[target] = value
    return public
