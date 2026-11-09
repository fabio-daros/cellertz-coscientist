"""Durable external-attempt record. Not a scientific result."""

from __future__ import annotations

from dataclasses import dataclass

from cellertz.application.contracts import ApplicationContractError

ATTEMPT_STATES = frozenset({"reserved", "creating", "starting", "running", "exited", "uncertain"})
_NEXT = {
    "reserved": frozenset({"creating", "uncertain"}),
    "creating": frozenset({"starting", "uncertain"}),
    "starting": frozenset({"running", "exited", "uncertain"}),
    "running": frozenset({"exited", "uncertain"}),
    "exited": frozenset(),
    "uncertain": frozenset(),
}


def transition_allowed(current: str, new: str) -> bool:
    return current == new or new in _NEXT.get(current, frozenset())


def reservation_unchanged(current: ExternalAttempt, new: ExternalAttempt) -> bool:
    if (
        current.run_id != new.run_id
        or current.attempt != new.attempt
        or current.authorization_id != new.authorization_id
        or current.binding_hash != new.binding_hash
        or current.deployment_id != new.deployment_id
        or current.daemon_id != new.daemon_id
        or current.container_name != new.container_name
    ):
        return False
    if current.container_id is not None and new.container_id != current.container_id:
        return False
    return transition_allowed(current.state, new.state)


def retry_flag(value: object) -> bool:
    """Accept only integer 0 or 1 from SQLite. Do not coerce other integers."""

    if type(value) is bool or type(value) is not int or value not in {0, 1}:
        raise ApplicationContractError("pdl1_attempt_invalid")
    return value == 1


@dataclass(frozen=True)
class ExternalAttempt:
    """Identity reserved before any container create."""

    run_id: str
    attempt: int
    authorization_id: str
    binding_hash: str
    deployment_id: str
    daemon_id: str
    container_name: str
    state: str
    container_id: str | None = None
    retry_allowed: bool = False
    output_sha256: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.run_id) is not str
            or type(self.authorization_id) is not str
            or type(self.binding_hash) is not str
            or type(self.deployment_id) is not str
            or type(self.daemon_id) is not str
            or type(self.container_name) is not str
            or type(self.attempt) is not int
            or isinstance(self.attempt, bool)
            or self.attempt < 1
            or self.state not in ATTEMPT_STATES
            or type(self.retry_allowed) is not bool
            or (self.container_id is not None and type(self.container_id) is not str)
            or (self.output_sha256 is not None and type(self.output_sha256) is not str)
        ):
            raise ApplicationContractError("pdl1_attempt_invalid")
