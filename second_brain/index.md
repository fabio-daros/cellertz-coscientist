# Cellertz Scientific Memory Architecture

This vault represents the Cellertz Second Brain.

```
Purpose
-------
Persistent validated scientific knowledge.

Current retrieval
-----------------
FileKnowledgeReader
knowledge_get
knowledge_neighbors
ground_package
knowledge_ground_package

Decision boundary
-----------------
Knowledge provides grounded context.
It does not modify:
- rank
- ranking_score
- deterministic critic assessment
- prioritization

Writeback
---------
Automatic agent writeback is forbidden.

Current validated packages
--------------------------
protein_scoring_bcma_boltz2_v2
```

Open this folder as an Obsidian vault. The contract stays in
`docs/architecture/second_brain.md`.

## Architecture

- [[architecture/working_memory|Working Memory]]
- [[architecture/episodic_scientific_memory|Episodic Scientific Memory]]
  - File Scientific Memory
  - Databricks Scientific Memory
- [[architecture/semantic_memory|Semantic Memory]]
- [[architecture/procedural_memory|Procedural Memory]]

## Memory houses

| Memory | Owner | Lifetime |
| --- | --- | --- |
| Working | request / session | this call |
| Episodic | `ScientificRunMemory` backed by `ScientificRunMemoryStore` | observed completed runs |
| Semantic | `second_brain/` | what Cellertz knows |
| Procedural | code + Hermes skills | how the system operates |

Working is not semantic. Episodic is not validated.
Procedural is not scientific evidence.

```
Episodic Scientific Memory
    ScientificRunMemory
        ↓
    ScientificRunMemoryStore
        ├── FileScientificRunMemoryStore
        │     local default
        └── DatabricksScientificRunMemoryStore
              validated optional backend
```

File local root: `outputs/scientific_memory/episodic/`.

Databricks logical table (no workspace or credential data):

```
catalog: cellertz
schema: science
table: scientific_run_memory
```

`epistemic_status = observed`.

```
Databricks persistence != validated scientific knowledge
```

Hermes conversation does not become a package. A fixture run
does not become a validated claim.

## Obsidian versus Databricks

```
Obsidian / second_brain/
    = human-readable semantic knowledge vault and architecture view

Databricks
    = structured persistence backend for episodic scientific memory
```

Do not mirror Databricks rows automatically into Markdown.

A future explicit scientific promotion may produce:

```
ScientificRunMemory (observed)
        ↓
scientific review
        ↓
KnowledgePackage (validated)
        ↓
semantic vault
```

That promotion remains future.

## Refinement

```
External source
      ↓
Reference
      ↓
Structured reference
      ↓
Validated evidence / claim
      ↓
Operational EvidencePacket
```

Only the last two levels may reach the Critic as normative
context. A paper is a source, not Cellertz knowledge.

## Vault layout

```
second_brain/
├── index.md
├── packages/       Knowledge Package
├── claims/         statements an agent may repeat
├── evidence/       measurements that support claims
├── papers/         Reference Knowledge
├── datasets/       datasets a package points at
├── experiments/    experimental records
├── decisions/      approved conclusions
└── runs/           reserved; not episodic ScientificRunMemory
```

Operational `ProteinRunState` / Supervisor run artifacts remain
under `outputs/runs/`. This vault is Semantic Memory only. It
does not store episodic `ScientificRunMemory` documents.

Semantic search, vector DB, and free-walk retrieval are not
this phase. Knowledge health is a CI check, not an MCP tool.
