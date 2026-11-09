"""Cellertz MCP server.

Read-only tools plus one fixture-only mutating start tool. Science
lives behind ProteinRunStarter, not in this module.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic_core import core_schema
from starlette.requests import Request
from starlette.responses import JSONResponse

from cellertz.mcp.capabilities import cellertz_capabilities
from cellertz.mcp.episodic_memory import (
    scientific_memory_find_candidate as read_scientific_memory_candidate,
)
from cellertz.mcp.episodic_memory import (
    scientific_memory_find_runs_by_target as read_scientific_memory_runs,
)
from cellertz.mcp.episodic_memory import (
    scientific_memory_get_run as read_scientific_memory_run,
)
from cellertz.mcp.knowledge import knowledge_get as read_knowledge
from cellertz.mcp.knowledge import (
    knowledge_ground_package as read_knowledge_ground_package,
)
from cellertz.mcp.knowledge import knowledge_neighbors as read_knowledge_neighbors
from cellertz.mcp.run_state import protein_get_run_state as read_protein_run_state
from cellertz.mcp.start_run import protein_start_fixture_run as start_fixture_run


class _UncoercedLimit:
    """Integer MCP argument left uncoerced for adapter validation.

    The generated tool schema stays ``integer``. Pydantic's lax ``int``
    would turn ``true`` into ``1`` and a numeric string into an int
    before this module can reject them.
    """

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: Any,
    ) -> Any:
        return core_schema.no_info_plain_validator_function(lambda value: value)

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        _schema: Any,
        _handler: Any,
    ) -> dict[str, str]:
        return {"type": "integer"}


mcp = MCPServer(
    "cellertz",
    instructions=(
        "Cellertz tools. Read-only: cellertz_get_capabilities, "
        "protein_get_run_state, scientific_memory_get_run, "
        "scientific_memory_find_runs_by_target, "
        "scientific_memory_find_candidate, knowledge_get, "
        "knowledge_neighbors, knowledge_ground_package. "
        "protein_get_run_state is operational status. "
        "scientific_memory_* returns recorded observed episodic results, "
        "not validated scientific knowledge. knowledge_* is semantic "
        "knowledge and existing grounding. Mutating: "
        "protein_start_fixture_run is fixture-only. For scientific why "
        "questions about protein scoring, ranking, ipSAE, or Boltz2, "
        "call knowledge_ground_package once with "
        "package_id=protein_scoring_bcma_boltz2_v2 and reason only over "
        "the compact EvidencePacket. Use claims, evidence numbers, and "
        "validated_interpretation as given. Do not walk Markdown, do not "
        "recompute the ranking decision, and do not define or expand "
        "terms listed in unsupported_expansions. Reply in English even "
        "when the question is in another language. Do not treat BC1, RFD5, "
        "ipSAE, or Boltz2 as knowledge IDs. knowledge_get remains for "
        "navigation. Do not request BindCraft, RFdiffusion, mBER, or GPU."
    ),
)


@mcp.tool()
def cellertz_get_capabilities() -> dict:
    """Return the Cellertz service capability map."""
    return cellertz_capabilities()


@mcp.tool()
def protein_get_run_state(run_id: str) -> dict:
    """Return operational status for an existing protein run. Read-only."""
    return read_protein_run_state(run_id)


@mcp.tool()
def scientific_memory_get_run(run_id: str) -> dict:
    """Return one recorded episodic scientific run. Not validated knowledge. Read-only."""
    return read_scientific_memory_run(run_id)


@mcp.tool()
def scientific_memory_find_runs_by_target(
    target_id: str,
    limit: _UncoercedLimit = 20,
) -> dict:
    """Return compact observed runs for one exact target. Read-only."""
    return read_scientific_memory_runs(target_id, limit)


@mcp.tool()
def scientific_memory_find_candidate(
    candidate_id: str,
    limit: _UncoercedLimit = 20,
) -> dict:
    """Return observed candidate occurrences across runs. Read-only."""
    return read_scientific_memory_candidate(candidate_id, limit)


@mcp.tool()
def knowledge_get(id: str) -> dict:
    """Return one Second Brain item and its outgoing relations. Read-only."""
    return read_knowledge(id)


@mcp.tool()
def knowledge_neighbors(id: str) -> dict:
    """Return Second Brain graph neighbors for one item. Read-only."""
    return read_knowledge_neighbors(id)


@mcp.tool()
def knowledge_ground_package(package_id: str) -> dict:
    """Return the compact EvidencePacket for one package. For ipSAE/Boltz2 ranking questions use protein_scoring_bcma_boltz2_v2. Read-only."""
    return read_knowledge_ground_package(package_id)


@mcp.tool()
def protein_start_fixture_run(
    target_id: str,
    objective: str,
    n_candidates: int,
    idempotency_key: str,
) -> dict:
    """Start one fixture-only protein-design run. Requires an idempotency key."""
    return start_fixture_run(
        target_id=target_id,
        objective=objective,
        n_candidates=n_candidates,
        idempotency_key=idempotency_key,
    )


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def main() -> None:
    security = TransportSecuritySettings(
        allowed_hosts=[
            "cellertz-mcp",
            "cellertz-mcp:*",
            "localhost",
            "localhost:*",
            "127.0.0.1",
            "127.0.0.1:*",
        ],
    )
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        transport_security=security,
    )


if __name__ == "__main__":
    main()
