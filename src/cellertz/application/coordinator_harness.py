"""Checks a coordinator synthesis against tool evidence.

This is not a scientific review and it is not an LLM judge. It looks for
explicit attempt ids, 64-character hashes, ranking_score literals, and a
fixed list of phrases that present approval, authorization, or generation
as something that already happened. A paraphrase that avoids those forms
is not detected. Private reasoning is not stored and is not reviewed.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_MODEL_CALLS = 6
DEFAULT_MAX_OUTPUT_TOKENS = 1500
DEFAULT_CALL_TIMEOUT_SECONDS = 30.0
RETRIES = 0

VALIDATION_LIMITATIONS = (
    "The harness checks explicit attempt ids, 64-character hashes, ranking_score literals, "
    "and a fixed list of phrases that claim approval, authorization, or generation already happened.",
    "A paraphrase that avoids those forms is not detected.",
    "This check does not establish scientific correctness.",
    "A cited attempt id is checked against tool evidence from the conversation. "
    "The check does not prove that the attempt belongs to the design request.",
    "Private reasoning is not stored and is not reviewed.",
    "max_output_tokens_per_call is the max_tokens sent on each model call. It is not a total cost ceiling.",
    "token_budget counts only provider-reported total_tokens. A call that omits usage does not add to that total.",
    "HTTP 402 from the provider stops the loop. A failed call is not retried.",
    "The coordinator cannot approve, authorize, or launch. Those actions stay on their own routes.",
)

_ATTEMPT = re.compile(r"\battempt\.[A-Za-z0-9]+\b")
_HEX = re.compile(r"\b[0-9a-fA-F]{64}\b")
_SCORE = re.compile(
    r"ranking_score(?:\s+is|\s*[:=])\s*(not_recorded|-?[0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)
_EXECUTED = (
    "was approved",
    "has been approved",
    "approval was recorded",
    "was authorized",
    "has been authorized",
    "authorization was recorded",
    "was launched",
    "has been launched",
    "generation was started",
    "generation completed",
    "bindcraft ran",
    "gpu execution completed",
)


class CoordinatorLimitsError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CoordinatorLimits:
    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    max_output_tokens_per_call: int = DEFAULT_MAX_OUTPUT_TOKENS
    token_budget: int | None = None
    call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS
    retries: int = RETRIES

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_output_tokens_per_call": self.max_output_tokens_per_call,
            "token_budget": self.token_budget,
            "call_timeout_seconds": self.call_timeout_seconds,
            "retries": self.retries,
            "output_token_limit_is_per_call": True,
        }


def resolve_coordinator_limits(environ: Mapping[str, str] | None = None) -> CoordinatorLimits:
    env = os.environ if environ is None else environ
    return CoordinatorLimits(
        max_model_calls=_positive_int(env.get("CELLERTZ_COORDINATOR_MAX_MODEL_CALLS", ""), DEFAULT_MAX_MODEL_CALLS, 20),
        max_output_tokens_per_call=_positive_int(
            env.get("CELLERTZ_COORDINATOR_MAX_OUTPUT_TOKENS", ""),
            DEFAULT_MAX_OUTPUT_TOKENS,
            8000,
        ),
        token_budget=_optional_int(env.get("CELLERTZ_COORDINATOR_TOKEN_BUDGET", "")),
        call_timeout_seconds=_timeout(env.get("CELLERTZ_COORDINATOR_CALL_TIMEOUT_SECONDS", "")),
        retries=RETRIES,
    )


def validate_synthesis(text: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Accept or reject one draft. Rejection is not a published conclusion."""

    blob = json.dumps(evidence, sort_keys=True).casefold()
    findings: list[dict[str, str]] = []
    for attempt_id in _ATTEMPT.findall(text):
        if attempt_id.casefold() not in blob:
            findings.append({"code": "unknown_citation", "value": attempt_id})
    for digest in _HEX.findall(text):
        if digest.casefold() not in blob:
            findings.append({"code": "unknown_hash", "value": digest.casefold()})
    recorded = _recorded_scores(evidence)
    for match in _SCORE.finditer(text):
        claimed = match.group(1).casefold()
        if claimed not in recorded:
            findings.append({"code": "divergent_score", "value": claimed})
    lowered = text.casefold()
    for phrase in _EXECUTED:
        if phrase in lowered:
            findings.append({"code": "executed_decision", "value": phrase})
    status = "rejected" if findings else "accepted"
    return {"status": status, "findings": findings, "limitations": list(VALIDATION_LIMITATIONS)}


def rejection_text(validation: Mapping[str, Any]) -> str:
    codes = []
    for item in validation.get("findings") or []:
        if isinstance(item, dict) and isinstance(item.get("code"), str) and item["code"] not in codes:
            codes.append(item["code"])
    listed = ", ".join(codes) if codes else "rejected"
    return (
        "The coordinator output was rejected. It is not a conclusion. "
        f"Findings: {listed}. "
        "An unknown citation, a divergent ranking_score, or a claim that approval, "
        "authorization, or generation already happened is not published."
    )


def _recorded_scores(evidence: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            if "ranking_score" in value:
                found.add(_score_token(value.get("ranking_score")))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(evidence)
    return {item for item in found if item}


def _score_token(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return format(value, "g")
    return ""


def _positive_int(raw: str, default: int, ceiling: int) -> int:
    text = raw.strip()
    if text == "":
        return default
    if not text.isdigit():
        raise CoordinatorLimitsError("invalid_coordinator_limit")
    value = int(text)
    if value < 1 or value > ceiling:
        raise CoordinatorLimitsError("invalid_coordinator_limit")
    return value


def _optional_int(raw: str) -> int | None:
    text = raw.strip()
    if text == "":
        return None
    if not text.isdigit():
        raise CoordinatorLimitsError("invalid_coordinator_token_budget")
    value = int(text)
    if value > 2_000_000:
        raise CoordinatorLimitsError("invalid_coordinator_token_budget")
    return value


def _timeout(raw: str) -> float:
    text = raw.strip()
    if text == "":
        return DEFAULT_CALL_TIMEOUT_SECONDS
    try:
        value = float(text)
    except ValueError:
        raise CoordinatorLimitsError("invalid_coordinator_timeout") from None
    if not math.isfinite(value) or value <= 0 or value > 120:
        raise CoordinatorLimitsError("invalid_coordinator_timeout")
    return value
