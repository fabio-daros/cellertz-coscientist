# Episodic Scientific Memory

`ScientificRunMemory`

```
memory_house = episodic
epistemic_status = observed
```

Answers: what happened in a scientific run?

Does not answer: what has Cellertz scientifically validated?

```
Episodic memory != Semantic memory
Observed != Validated
```

Persistence backends:

- [[file_scientific_memory|File Scientific Memory]]
- [[databricks_scientific_memory|Databricks Scientific Memory]]

Selector: `CELLERTZ_SCIENTIFIC_MEMORY_BACKEND=file|databricks`.
Default is File. Databricks requires explicit selection. Ingest
is developer-only and still not automatic.

Future explicit promotion, not automatic:

```
ScientificRunMemory (observed)
        ↓
scientific review
        ↓
KnowledgePackage (validated)
        ↓
Semantic Memory
```

Do not treat a completed run as a KnowledgePackage. Individual
runs are not Markdown nodes in this vault.
