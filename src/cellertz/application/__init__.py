"""Application contracts and read-only conversation for Protein Design.

Display state does not authorize execution. Conversation reads through
ports in this package and does not import scientific backends.

This initializer does not import those ports. ``python -m
cellertz.application.pdl1_container_entrypoint`` and the image probe
both load this file. Loading ``conversation`` or ``fixtures`` here would
execute ``datetime.UTC``, which Python 3.10 does not provide. The
BindCraft image stays on Python 3.10. The rest of this project stays on
``requires-python >=3.12``.
"""

import importlib

__all__ = [
    "BLOCKED_CAPABILITIES",
    "CONVERSATIONAL_READONLY_CAPABILITIES",
    "AgentSnapshot",
    "ApplicationContractError",
    "ArtifactSummary",
    "CandidateSummary",
    "ConversationMessage",
    "ConversationReply",
    "ConversationRequest",
    "EvidenceSummary",
    "ExecutionBudget",
    "ModelUsageEvent",
    "PipelineStageSnapshot",
    "ProjectContext",
    "ProteinDesignDraft",
    "ReadOnlyConversation",
    "RunSummary",
    "UsageBudgetSnapshot",
    "WorkspaceSession",
    "pdl1_prototype_session",
]

_EXPORTS = {
    "AgentSnapshot": "cellertz.application.contracts",
    "ApplicationContractError": "cellertz.application.contracts",
    "ArtifactSummary": "cellertz.application.contracts",
    "CandidateSummary": "cellertz.application.contracts",
    "ConversationMessage": "cellertz.application.contracts",
    "EvidenceSummary": "cellertz.application.contracts",
    "ExecutionBudget": "cellertz.application.contracts",
    "ModelUsageEvent": "cellertz.application.contracts",
    "PipelineStageSnapshot": "cellertz.application.contracts",
    "ProjectContext": "cellertz.application.contracts",
    "ProteinDesignDraft": "cellertz.application.contracts",
    "RunSummary": "cellertz.application.contracts",
    "UsageBudgetSnapshot": "cellertz.application.contracts",
    "WorkspaceSession": "cellertz.application.contracts",
    "BLOCKED_CAPABILITIES": "cellertz.application.conversation",
    "CONVERSATIONAL_READONLY_CAPABILITIES": "cellertz.application.conversation",
    "ConversationReply": "cellertz.application.conversation",
    "ConversationRequest": "cellertz.application.conversation",
    "ReadOnlyConversation": "cellertz.application.conversation",
    "pdl1_prototype_session": "cellertz.application.fixtures",
}


def __getattr__(name: str) -> object:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
