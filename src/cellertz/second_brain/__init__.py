"""Second Brain package.

Architectural umbrella for Cellertz memory houses. This stage
implements only episodic scientific run memory.

Future modules, not implemented here:

- semantic/     KnowledgePackage vault and promotion
- retrieval/    later consumers of memory, including RAG
- promotion/    explicit writeback from observed runs to claims
"""

from cellertz.second_brain.episodic import (
    ArtifactReference,
    CandidateMemory,
    CandidateMemoryHit,
    CriticMemory,
    DatabricksScientificRunMemoryStore,
    DecisionMemory,
    ExecutionProvenanceMemory,
    FileScientificRunMemoryStore,
    IpSAEModelEvidence,
    ScientificRunMemory,
    ScientificRunMemoryConflict,
    ScientificRunMemoryError,
    ScientificRunMemoryProjectionError,
    ScientificRunMemoryReader,
    ScientificRunMemoryStore,
    ScientificRunMemoryWriter,
    StructuralEvidenceMemory,
    create_scientific_run_memory_store,
    default_scientific_memory_root,
    project_protein_design_run,
    resolve_scientific_memory_backend,
    scientific_run_memory_from_document,
    validate_memory_digest,
    validate_scientific_run_memory,
)

__all__ = [
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
