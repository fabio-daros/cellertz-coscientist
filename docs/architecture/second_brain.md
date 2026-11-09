# Second Brain — knowledge contract

Phase 3A. This document freezes the knowledge model. It does not
store packages, expose retrieval, or let Hermes write memory.

Scientific **run memory** is the episodic house of the Second Brain.
`ScientificRunMemory` records what happened in a completed scientific
run with `memory_house=episodic` and `epistemic_status=observed`.
It is not this Markdown KnowledgePackage contract, not
`ProteinRunState`, not the idempotency store, not RAG, and not
validated scientific truth. Run completion does not promote an
observation into a semantic claim. See
`docs/architecture/scientific_run_memory.md`.

The five questions every record must answer:

1. What is the unit of knowledge?
2. What evidence supports it?
3. What is its status / confidence?
4. Where did it come from?
5. Who may change it?

If a record cannot answer all five, it does not belong in the
Second Brain.

```
2B.3  fixture mutation                 done
        ↓
3A    knowledge contract               this document
        ↓
3B    first validated evidence package
        ↓
3C    retrieval read-only
        ↓
3D    Hermes consumes knowledge
        ↓
later protein agents receive context
later real generators
```

BindCraft, RFdiffusion, mBER, GPU, and wet lab stay closed
through 3D. Retrieval is not implemented in 3A.

## 1. Shape

```
Second Brain
│
├── Knowledge Package
│     ├── identity
│     ├── scope
│     ├── claims
│     ├── evidence
│     ├── provenance
│     ├── status
│     └── version
│
├── Reference Knowledge
│     papers / methods / protocols
│
├── Validated Knowledge
│     benchmarks / experimental results / approved conclusions
│
└── Run Context
      current campaign / current constraints / current run
```

The Second Brain is not Databricks. Databricks is one
infrastructure backend used by the Episodic Scientific Memory
house. It does not define the Second Brain domain contract.
The Second Brain is not S3 or a results warehouse.
Packages may **point** at datasets and artifacts. They must not
duplicate them.

A later URI may change from a local path to
`s3://cellertz-science/...` without changing this contract.

## 2. What is a unit of knowledge?

The unit is a **Knowledge Package**: one versioned JSON document
with identity, scope, claims, evidence, provenance, status, and
version.

It is not a chat log, a fixture `state.json`, a CSV dump, or an
agent's latest opinion.

### Identity

```json
{
  "package_id": "protein_scoring_bcma_boltz2_v2",
  "domain": "protein_design",
  "knowledge_type": "validated_evidence",
  "version": "v2"
}
```

`package_id` is stable for the scientific object. `version`
changes when the conclusion changes. Do not reuse an id for a
different decision.

### Scope

Scope says what the package is about and what it is not about.

```json
{
  "scope": {
    "target": "BCMA",
    "task": "structural_ranking",
    "benchmark": "Chow 2026",
    "not_in_scope": [
      "binding_probability",
      "hard_lab_filter",
      "learned_composite_scorer",
      "wet_lab_selection"
    ]
  }
}
```

### Layers

| layer | `knowledge_type` (examples) | meaning |
| --- | --- | --- |
| Reference Knowledge | `reference` | external papers, methods, protocols; not necessarily validated by Cellertz |
| Validated Knowledge | `validated_evidence` | benchmarks, experimental results, approved conclusions |
| Run Context | `run_context` | current campaign, constraints, or run; operational, not a substitute for validated claims |

Run Context must not be promoted to Validated Knowledge by
retrieval or by an agent finishing a fixture run.

## 3. Claims versus evidence

A **claim** is a statement an agent may repeat.

**Evidence** is the measurement that makes the claim scientific.
Claims do not float. Each claim is only as strong as the
evidence and provenance it points at.

```json
{
  "package_id": "protein_scoring_bcma_boltz2_v2",
  "domain": "protein_design",
  "knowledge_type": "validated_evidence",
  "status": "validated",
  "claims": [
    {
      "id": "primary_ranking_signal",
      "statement": "raw ipSAE remains the primary structural ranking signal"
    },
    {
      "id": "boltz2_role",
      "statement": "Boltz2 ensemble metrics are auxiliary structural evidence"
    },
    {
      "id": "composite_score",
      "statement": "no composite ipSAE + Boltz2 score is currently validated"
    }
  ],
  "evidence": {
    "benchmark": {
      "dataset": "Chow BCMA",
      "candidates": 766,
      "binders": 34
    },
    "global": {
      "ipsae_ap": 0.0924,
      "ipsae_roc_auc": 0.7535,
      "boltz2_mean_ap": 0.0825
    },
    "holdouts": {
      "bindcraft_campaign_1": {
        "ipsae_ap": 0.3082,
        "boltz2_std_inverted_ap": 0.3553
      },
      "rfd5": {
        "ipsae_ap": 0.2501,
        "boltz2_std_inverted_ap": 0.0962
      }
    }
  }
}
```

The hold-out pair is load-bearing. BindCraft Campaign 1 shows a
local Boltz std inverted AP of 0.3553 versus ipSAE 0.3082. RFD5
reverses that (0.0962 versus 0.2501). That is why
`composite_score` stays unvalidated: a campaign-local gain is
not a new ranking contract.

Claims this package must **not** contain:

- any `ipSAE + Boltz2` formula
- `ipSAE > t → send to lab`
- `ranking_score` as P(binder)
- generator name as a ranking feature
- fixture structural numbers as this benchmark

3B stores this package. 3A only freezes the shape.

## 4. Status

Keep the taxonomy small:

| status | meaning |
| --- | --- |
| `reference` | external information; not necessarily validated by Cellertz |
| `observed` | a result was produced, but no approved conclusion yet |
| `validated` | evidence / conclusion accepted by the scientific workflow |
| `superseded` | kept for provenance; replaced by a later version |

Hermes retrieval in 3C–3D may return `validated` packages.
`reference` and `observed` are not automatic agent memory.
`superseded` remains readable for audit, and must name what
replaced it.

If a later internal benchmark beats ipSAE, do not delete:

```
protein_scoring_bcma_boltz2_v2
status = superseded
superseded_by = protein_scoring_internal_v3
```

Scientific memory stays auditable.

## 5. Provenance and artifacts

Provenance answers “where did this come from?”. Artifacts are
pointers, not copies.

```json
{
  "provenance": {
    "pre_unblind_tag": "protein-boltz2-benchmark-v2",
    "pre_unblind_commit": "be40731",
    "evaluation_commit": "2e3a4dc",
    "blind_summary_sha256": "d174f2a00629d1011619b6b1575b1a0a320c8ec4875fc3f6be89976fdc7d3751",
    "unblinded_sha256": "f6c5fb9750ead6bf17ec6cd7023662036b0abde1ce78fe16c7474aaa2ee30596",
    "results_sha256": "9e0e23a2e0b1410dd9f637c21318d652437cbb5bae4b55a4971d61de83860a10"
  },
  "artifacts": [
    {
      "type": "benchmark_results",
      "uri": "outputs/bcma_boltz2_v2_results.csv",
      "sha256": "9e0e23a2e0b1410dd9f637c21318d652437cbb5bae4b55a4971d61de83860a10"
    },
    {
      "type": "unblinded_labels",
      "uri": "outputs/bcma_boltz2_v2_unblinded.csv",
      "sha256": "f6c5fb9750ead6bf17ec6cd7023662036b0abde1ce78fe16c7474aaa2ee30596"
    }
  ]
}
```

The CSVs stay outside Git. The package records their hashes.
Source narrative remains in:

- `docs/experiments/chow2026_bcma_benchmark.md`
- `docs/experiments/boltz2_v2_evaluation_protocol.md`
- `docs/experiments/boltz2_v2_unblind_results.md`
- `docs/architecture/protein_ranking_critic_agent.md`

A package without provenance is not `validated`.

## 6. Who may change it?

| actor | may write? |
| --- | --- |
| retrieve / `knowledge_get` / `knowledge_ground_package` | no |
| Hermes | no |
| agent conclusion, critic text, fixture run | no |
| ProteinRunStarter / Supervisor | no |
| implicit “the model said so” | no |
| explicit scientific writeback, later phase | yes, only then |

Change means: create a new package version, or mark the current
one `superseded` with `superseded_by`. In-place silent edits of
a `validated` package are forbidden.

Until a later writeback phase exists, the only legitimate
mutation is a human-reviewed addition of a new package document
(3B is that addition for the first package).

## 7. Retrieval does not change knowledge

```
retrieve     → read-only
agent conclusion → does not enter the Second Brain
writeback    → future phase + explicit validation
```

This is the guard against an agent turning inference or
hallucination into memory.

When 3C exists:

```
read-only:
  - knowledge_get(id)
  - knowledge_neighbors(id)
  - knowledge_ground_package(package_id)
  - knowledge_search(query)   optional, still read-only

mutating:
  none
```

Missing IDs return `found: false` and create nothing. Knowledge
tools do not inherit `CELLERTZ_MCP_MUTATION_MODE`. Reading
memory is not starting a run.

Hermes may quote claims, evidence, and provenance. Hermes may
not start BindCraft, RFdiffusion, or mBER because a package
exists.

Retrieved packages later enter Ranking / Critic /
Prioritization as **context**. They must not change
`ranking_score`, invent a composite, or fail a run if retrieval
misses. Ranking still uses raw ipSAE from `ProteinScoringTool`.

## 8. What 3A does not build

- no knowledge store
- no MCP knowledge tool
- no Hermes allowlist change
- no writeback API
- no Databricks / S3 KnowledgePackage layout
- no Paperclip / token optimization

Phase 5A later added an optional Databricks adapter for
episodic `ScientificRunMemory` only. That backend is storage
infrastructure for the episodic house, not this KnowledgePackage
contract and not semantic promotion.

Token growth is already visible and will matter later
(Paperclip / cost-per-workflow). Record it. Do not optimize
the model here.

| surface | input tokens (observed) |
| --- | --- |
| no tools | ~11.9k |
| 1 MCP capability | ~35.5k |
| 2 read-only tools | ~37.1k |
| fixture operation | ~51.2k |
| 3D Second Brain retrieval | ~110k |
| 3E free traversal attempt | ~216–254k |
| 3E grounded EvidencePacket | ~34.8k |
| 3F.2 critic explanation | ~12.7k |

The 3E compact packet collapsed the traversal explosion
back to the simple-MCP range. That is the observability
baseline for later cost-per-workflow measurement.

## 9. Implementation order

1. This contract
2. Persist `protein_scoring_bcma_boltz2_v2` as the first
   validated evidence package (3B)
3. `KnowledgeReader` / read-only retrieval (3C) —
   `FileKnowledgeReader` over `second_brain/`
4. MCP `knowledge_get` / `knowledge_neighbors` (3D)
5. Grounded retrieval (3E): compact EvidencePacket,
   `knowledge_ground_package`, no unsupported definitions,
   validated interpretation delivered as already-audited
   knowledge, operational metrics recorded
6. Protein agents consume retrieved knowledge (3F)
   - 3F.1 grounded critic context, no Hermes, no Supervisor
   - 3F.2 Hermes verbalizes GroundedCriticResult; Python keeps invariants
   - 3F.3 Supervisor wires optional explanation under critique;
     prioritization still consumes scientific state only
   Phase 3 closes at `docs/architecture/protein_design_v1_fixture.md`.
7. Second Brain hardening (3G), before real generators:
   - 3G.1 front door + four memory houses
   - 3G.2 knowledge health + scientific canaries
   - 3G.3 freshness / supersession + context receipt
   Phase 4A is `docs/architecture/scientific_execution.md`.
   Real generators stay locked.

When answering from Cellertz knowledge:

1. Use only statements supported by retrieved knowledge.
2. Do not define or expand technical terms unless the
   retrieved knowledge explicitly defines them.
3. Separate validated claim, supporting evidence, and
   unavailable information.
4. Identify the knowledge IDs used.

3B does not invent fixture knowledge. The first content is the
already audited Boltz2 v2 / Chow result:

```
protein_scoring_bcma_boltz2_v2
        ↓
validated evidence package
        ↓
Chow 766 / 34
        ↓
ipSAE primary
Boltz2 auxiliary
no composite
        ↓
full provenance
```

## 10. Four memory houses (3G)

These are contract labels, not four new systems.

```
Second Brain
│
├── Working Memory
│      request / session
│
├── Episodic Scientific Memory
│      ScientificRunMemory
│      ScientificRunMemoryStore
│           ├── File
│           └── Databricks
│      epistemic status: observed
│
├── Semantic Memory
│      KnowledgePackage / Obsidian vault
│      claims / evidence / provenance / status / version
│      vault: second_brain/
│      validated only through explicit future promotion
│
└── Procedural Memory
      code + Hermes skills
```

| Memory | Owner | What it is |
| --- | --- | --- |
| Working | request / session | temporary execution context |
| Episodic | `ScientificRunMemoryStore` (File default; Databricks optional) | what happened: observed `ScientificRunMemory` |
| Semantic | `second_brain/` | what Cellertz knows: claims, evidence, packages |
| Procedural | code + Hermes skills | how the system operates |

```
Obsidian / second_brain/
    = human-readable semantic knowledge vault and architecture view

Databricks
    = structured persistence backend for episodic scientific memory
```

Do not mirror Databricks rows automatically into this vault.
Databricks persistence is not validated scientific knowledge.

Operational run artifacts (`outputs/runs/`, `ProteinRunState`) remain
the Supervisor/MCP boundary. They are not semantic knowledge and they
are not this episodic scientific-memory document.

```
Working ≠ Semantic
Episodic ≠ Validated
Procedural ≠ Scientific evidence
RUN COMPLETE ≠ SCIENTIFIC CLAIM VALIDATED
```

A Hermes conversation does not enter `second_brain/`.
A completed BindCraft run does not become a validated claim.
RAG/retrieval must not silently promote an episodic observation.

Explicit promotion is a separate developer-operated step.
It does not run from retrieval, Hermes, or MCP. See
`docs/architecture/scientific_knowledge_promotion.md`.

```
ScientificRunMemory
        ↓
explicit scientific writeback / knowledge promotion
        ↓
human / scientific review
        ↓
Semantic KnowledgePackage
```

## 11. Refinement (3G)

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

Only validated evidence / claims and the EvidencePacket may
reach the Critic as normative context. A paper is a source.

Semantic search, vector DB, and free-walk retrieval stay closed.

## 12. Freshness and supersession (3G)

Scientific knowledge does not expire by calendar.

```
status: validated
validated_at: 2026-09-19
reviewed_at: 2026-09-19
superseded_by:
review_reason:
```

`not reviewed recently` may be reported. It does not become
`invalid`. Invalidation requires new evidence and a successor:

```
validated
     │
nova evidência
     ▼
superseded
     │
     └──► successor package
```

There is no `expires_at` rule.

## 13. Health versus canaries (3G)

Health is structural integrity. It is a CI / local check:

```
python -m cellertz.knowledge.health
```

It is not an MCP tool. Hermes does not call it.

Canaries are scientific meaning. They ask whether the knowledge
we believe the system has is still the knowledge it has.

A context receipt records which knowledge was available when
a candidate was criticised:

```
critique.grounded_context.knowledge_context
  package_id
  package_status
  knowledge_used
  evidence_packet_sha256
  retrieval_contract_version
  payload_bytes
```
