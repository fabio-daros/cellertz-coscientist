"""Episodic scientific run memory.

Durable record of observed scientific execution and results from a
completed run. Not validated semantic knowledge.
"""

from cellertz.second_brain.episodic.databricks import (
    DatabricksScientificRunMemoryStore,
)
from cellertz.second_brain.episodic.factory import (
    BACKEND_DATABRICKS,
    BACKEND_FILE,
    create_scientific_run_memory_store,
    resolve_scientific_memory_backend,
)
from cellertz.second_brain.episodic.models import (
    EPISTEMIC_STATUS_OBSERVED,
    MEMORY_HOUSE_EPISODIC,
    ArtifactReference,
    CandidateMemory,
    CandidateMemoryHit,
    CriticMemory,
    DecisionMemory,
    ExecutionProvenanceMemory,
    IpSAEModelEvidence,
    ScientificRunMemory,
    ScientificRunMemoryConflict,
    ScientificRunMemoryError,
    ScientificRunMemoryProjectionError,
    StructuralEvidenceMemory,
    scientific_run_memory_from_document,
    validate_memory_digest,
    validate_scientific_run_memory,
)
from cellertz.second_brain.episodic.protein_design import (
    project_protein_design_run,
)
from cellertz.second_brain.episodic.store import (
    FileScientificRunMemoryStore,
    ScientificRunMemoryReader,
    ScientificRunMemoryStore,
    ScientificRunMemoryWriter,
    default_scientific_memory_root,
)

__all__ = [
    "BACKEND_DATABRICKS",
    "BACKEND_FILE",
    "EPISTEMIC_STATUS_OBSERVED",
    "MEMORY_HOUSE_EPISODIC",
    "ArtifactReference",
    "CandidateMemory",
    "CandidateMemoryHit",
    "CriticMemory",
    "DatabricksScientificRunMemoryStore",
    "DecisionMemory",
    "ExecutionProvenanceMemory",
    "FileScientificRunMemoryStore",
    "IpSAEModelEvidence",
    "ScientificRunMemory",
    "ScientificRunMemoryConflict",
    "ScientificRunMemoryError",
    "ScientificRunMemoryProjectionError",
    "ScientificRunMemoryReader",
    "ScientificRunMemoryStore",
    "ScientificRunMemoryWriter",
    "StructuralEvidenceMemory",
    "create_scientific_run_memory_store",
    "default_scientific_memory_root",
    "project_protein_design_run",
    "resolve_scientific_memory_backend",
    "scientific_run_memory_from_document",
    "validate_memory_digest",
    "validate_scientific_run_memory",
]
