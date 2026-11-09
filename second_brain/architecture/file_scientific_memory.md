# File Scientific Memory

Architectural note for `FileScientificRunMemoryStore`.
Not a KnowledgePackage.

Role: default local persistence backend for
[[episodic_scientific_memory|Episodic Scientific Memory]].

Root:

```
outputs/scientific_memory/episodic/
```

Stores `ScientificRunMemory` JSON documents. File is the local
default. Databricks is the optional backend.
