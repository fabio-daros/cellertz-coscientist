"""Select the conversation phrasing model. The default calls no provider."""

from __future__ import annotations

import os
from collections.abc import Mapping

from cellertz.application.conversation import ConversationModel, RecordedFactsModel
from cellertz.conversation.model import ChatCompletionModel, ChatTransport, urllib_transport

PROVIDER_ENV = "CELLERTZ_CONVERSATION_PROVIDER"
MODEL_ENV = "CELLERTZ_CONVERSATION_MODEL"
API_BASE_ENV = "CELLERTZ_CONVERSATION_API_BASE"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
RECORDED = "recorded"
OPENROUTER = "openrouter"
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class ConversationConfigurationError(Exception):
    """The process is not allowed to select this conversation provider."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def resolve_conversation_model(
    environ: Mapping[str, str] | None = None,
    transport: ChatTransport | None = None,
) -> ConversationModel:
    env = os.environ if environ is None else environ
    provider = env.get(PROVIDER_ENV, RECORDED).strip().lower()
    if provider in {"", RECORDED}:
        return RecordedFactsModel()
    if provider != OPENROUTER:
        raise ConversationConfigurationError("invalid_conversation_provider")
    model = env.get(MODEL_ENV, "").strip()
    key = env.get(OPENROUTER_KEY_ENV, "").strip()
    if not model:
        raise ConversationConfigurationError("conversation_model_required")
    if not key:
        raise ConversationConfigurationError("conversation_provider_unconfigured")
    base = env.get(API_BASE_ENV, _OPENROUTER_BASE).strip().rstrip("/")
    if not base.startswith("https://") or " " in base:
        raise ConversationConfigurationError("invalid_conversation_api_base")
    return ChatCompletionModel(
        provider=OPENROUTER,
        model=model,
        api_key=key,
        url=f"{base}/chat/completions",
        transport=transport or urllib_transport,
    )
