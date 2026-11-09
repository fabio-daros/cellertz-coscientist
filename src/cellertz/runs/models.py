from __future__ import annotations

from dataclasses import dataclass

from cellertz.runs.idempotency import canonical_request_hash


@dataclass(frozen=True)
class ProteinFixtureRunRequest:
    target_id: str
    objective: str
    n_candidates: int

    def request_hash(self) -> str:
        return canonical_request_hash(
            {
                "mode": "fixture",
                "n_candidates": self.n_candidates,
                "objective": self.objective,
                "target_id": self.target_id,
            }
        )


@dataclass(frozen=True)
class ProteinRunStartResult:
    run_id: str | None
    mode: str
    created: bool
    idempotent_replay: bool
    status: str
    idempotency_key: str
    error: str | None = None
    execution_status: str | None = None
    scientific_status: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "mode": self.mode,
            "created": self.created,
            "idempotent_replay": self.idempotent_replay,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
        }
        if self.run_id is not None:
            payload["run_id"] = self.run_id
        if self.error is not None:
            payload["error"] = self.error
        if self.execution_status is not None:
            payload["execution_status"] = self.execution_status
        if self.scientific_status is not None:
            payload["scientific_status"] = self.scientific_status
        return payload
