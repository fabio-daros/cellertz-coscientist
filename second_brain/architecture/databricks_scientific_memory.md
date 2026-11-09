# Databricks Scientific Memory

Architectural note for `DatabricksScientificRunMemoryStore`.
Not a KnowledgePackage. Not a table-row mirror.

Role: structured persistence backend for
[[episodic_scientific_memory|Episodic Scientific Memory]].

```
Databricks != Second Brain
Databricks persistence != validated scientific knowledge
```

Databricks is one infrastructure backend used by the Episodic
house. It does not define the Second Brain domain contract.

Logical location:

```
catalog: cellertz
schema: science
table: scientific_run_memory
```

Current status: real integration validated on a trial/serverless
environment. Not production.

Validated operations:

- `ensure_schema`
- `put`
- `get_run`
- `find_runs_by_target`
- `find_candidate`
- same-digest idempotent `put`

Validation record:

`docs/validation/databricks_scientific_memory_real_validation.md`
