"""JSON edge for ProteinDesignRequest drafts. Stdlib HTTP only.

This is a separate path from `/api/conversation` so a read-only question
cannot be parsed as a draft, and a client cannot send authorization
flags into the 6B endpoint. The handler does not start a run.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.conversation import ConversationInstrumentation
from cellertz.application.request_draft import (
    DesignDraftCommand,
    DesignDrafting,
    DesignDraftReply,
)

DESIGN_DRAFT_PATH = "/api/design-draft"
_UNSUPPORTED_TEXT = "This message is not a protein design request draft."
_ADAPTER_TEXT = "The conversational adapter failed. No scientific action was taken."
# request_id is the draft's server-issued id; revision is its integer revision.
# There is no revision_id.
_BODY_KEYS = frozenset({"session_id", "message", "request_id", "revision", "clarifications"})
_FORBIDDEN_KEYS = frozenset({"execution_authorized", "executable"})


def handle_list_design_drafts(service: DesignDrafting) -> tuple[int, dict[str, Any]]:
    """List the latest revision of each saved draft. This does not revise or execute."""

    return 200, {
        "status": "drafts_recorded",
        "drafts": [
            {
                "message": message,
                "draft": draft.to_dict(),
                "execution_authorized": False,
                "executable": False,
            }
            for draft, message in service.catalog()
        ],
        "execution_authorized": False,
        "executable": False,
    }


def handle_read_design_draft(service: DesignDrafting, path: str) -> tuple[int, dict[str, Any]]:
    """Read one saved draft, or the latest revision of each. This does not revise or execute."""

    parsed = urlsplit(path)
    if parsed.path != DESIGN_DRAFT_PATH:
        return 404, _absent()
    if parsed.query == "":
        return handle_list_design_drafts(service)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"request_id"} or len(query["request_id"]) != 1:
        return 400, _rejected("")
    found = service.recorded(query["request_id"][0])
    if found is None:
        return 404, _absent()
    draft, message = found
    return 200, {
        "status": "draft_recorded",
        "message": message,
        "draft": draft.to_dict(),
        "execution_authorized": False,
        "executable": False,
    }


def handle_design_draft(service: DesignDrafting, body: bytes) -> tuple[int, dict[str, Any]]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, _rejected("")
    if not isinstance(payload, dict):
        return 400, _rejected("")
    session_id = payload.get("session_id")
    safe_session = session_id if isinstance(session_id, str) else ""
    unknown = set(payload) - _BODY_KEYS
    if unknown & _FORBIDDEN_KEYS:
        return 400, _forbidden(safe_session)
    if unknown:
        return 400, _rejected(safe_session)
    try:
        command = DesignDraftCommand(
            session_id=payload.get("session_id", ""),
            message=payload.get("message", ""),
            request_id=_optional_text(payload.get("request_id")),
            revision=_optional_revision(payload.get("revision")),
            clarifications=_clarifications(payload["clarifications"]) if "clarifications" in payload else (),
        )
    except ApplicationContractError:
        session_id = payload.get("session_id")
        return 400, _rejected(session_id if isinstance(session_id, str) else "")
    try:
        reply = service.revise(command)
    except Exception:  # noqa: BLE001 — the edge returns a safe body, not a traceback
        reply = _failure(command.session_id)
    return (409 if reply.status == "revision_conflict" else 200), reply.to_dict()


def _optional_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ApplicationContractError("invalid_request_id")
    return value


def _clarifications(value: object) -> tuple[tuple[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ApplicationContractError("invalid_clarification")
    items: list[tuple[str, object]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"field", "value"}:
            raise ApplicationContractError("invalid_clarification")
        field = item["field"]
        raw = item["value"]
        if not isinstance(field, str) or not field.strip():
            raise ApplicationContractError("invalid_clarification")
        if isinstance(raw, bool) or not isinstance(raw, (str, int)):
            raise ApplicationContractError("invalid_clarification")
        if isinstance(raw, str) and not raw.strip():
            raise ApplicationContractError("invalid_clarification")
        items.append((field.strip(), raw.strip() if isinstance(raw, str) else raw))
    return tuple(items)


def _optional_revision(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApplicationContractError("invalid_revision")
    return value


def _absent() -> dict[str, Any]:
    return {
        "status": "draft_not_found",
        "message": None,
        "draft": None,
        "execution_authorized": False,
        "executable": False,
    }


def _rejected(session_id: str) -> dict[str, Any]:
    return DesignDraftReply(
        status="unsupported_message",
        text=_UNSUPPORTED_TEXT,
        session_id=session_id or "unknown",
        draft=None,
        request=None,
        questions=(),
        issues=(),
        changed_fields=(),
        instrumentation=_idle(),
    ).to_dict()


def _forbidden(session_id: str) -> dict[str, Any]:
    return DesignDraftReply(
        status="forbidden_action",
        text="This action is blocked. A draft is not approval and not execution.",
        session_id=session_id or "unknown",
        draft=None,
        request=None,
        questions=(),
        issues=(),
        changed_fields=(),
        instrumentation=_idle(),
    ).to_dict()


def _failure(session_id: str) -> DesignDraftReply:
    return DesignDraftReply(
        status="adapter_failure",
        text=_ADAPTER_TEXT,
        session_id=session_id,
        draft=None,
        request=None,
        questions=(),
        issues=(),
        changed_fields=(),
        instrumentation=ConversationInstrumentation(
            interaction_id="unknown",
            recorded=False,
            event=None,
            total_tokens=None,
            observation="adapter_failure",
        ),
    )


def _idle() -> ConversationInstrumentation:
    return ConversationInstrumentation(
        interaction_id="unknown",
        recorded=False,
        event=None,
        total_tokens=None,
        observation="no_model_usage",
    )
