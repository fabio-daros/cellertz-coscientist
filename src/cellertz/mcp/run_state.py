"""Read-only MCP adapter over ProteinRunStateReader.

This module must not import the Protein Supervisor.
"""

from __future__ import annotations

from cellertz.state.run_state import (
    FileProteinRunStateStore,
    ProteinRunStateReader,
    default_run_state_root,
)

_reader: ProteinRunStateReader | None = None


def get_run_state_reader() -> ProteinRunStateReader:
    global _reader
    if _reader is None:
        _reader = FileProteinRunStateStore(default_run_state_root())
    return _reader


def set_run_state_reader(reader: ProteinRunStateReader | None) -> None:
    global _reader
    _reader = reader


def protein_get_run_state(run_id: str) -> dict:
    """Return operational stage status for an existing protein run.

    Read-only. Does not start, retry, resume, generate, or score.
    Missing runs return found=false and are not created.
    """
    return get_run_state_reader().get_run_state(run_id).to_tool_dict()
