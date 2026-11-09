# Explicit scientific knowledge promotion — Phase 5D

Promotion turns a human-reviewed bundle into semantic knowledge.
It does not decide that an observed run is validated science.

```
ScientificRunMemory
    memory_house=episodic
    epistemic_status=observed
        ↓
explicit promotion bundle
        ↓
human scientific review
        ↓
deterministic preflight
        ↓
explicit --apply
        ↓
validated semantic knowledge
```

```
RUN COMPLETE != SCIENTIFIC CLAIM VALIDATED
PERSISTED != VALIDATED
OBSERVED != VALIDATED
EPISODIC MEMORY != SEMANTIC MEMORY
DATABRICKS PERSISTENCE != SCIENTIFIC TRUTH
```

The PDL1 smoke run stays episodic. It is a recorded computation,
not an approved biological conclusion, and this phase does not
promote it.

## Bundle

A bundle is a directory outside the vault:

```
manifest.json
packages/*.md
claims/*.md
evidence/*.md
datasets/*.md
```

`schema` is `scientific_knowledge_promotion_v1`.
`review.decision` must be `approved`.
`reviewed_by` is an operator-supplied audit identity. This phase
does not cryptographically authenticate that person.
`reviewed_by` and `review.reason` must not carry leading or
trailing whitespace.
`reviewed_at` is timezone-aware ISO-8601.
`source.run_id` and `source.memory_digest` must match the
configured episodic store.

Every promoted Markdown file is listed with its SHA-256.
Absolute paths, traversal, and symlinks are rejected.
A declared document that is not Markdown is rejected, and so is
Markdown inside the bundle that the manifest does not list.
Other undeclared files that are not Markdown are ignored.
At least one document is `type: validated_evidence` and
`status: validated`. Its frontmatter repeats the promotion id,
source run, source digest, reviewer, review time, and reason.
Claims and evidence text are human-authored. The promoter does
not write scientific statements.

## Source check

The reader comes from `create_scientific_run_memory_store()`.
The bundle cannot choose the backend, credentials, SQL, or paths.
A missing run, digest mismatch, or backend failure stops the
promotion. Databricks failure does not fall back to File.

## Inspect and apply

```
python -m tools.validation.promote_scientific_knowledge \
  --bundle /path/to/promotion_bundle
```

That command parses, checks the source run, and preflights a
staged copy of the vault. It does not change the vault.

```
python -m tools.validation.promote_scientific_knowledge \
  --bundle /path/to/promotion_bundle \
  --apply
```

`--apply` repeats those checks, then writes:

```
second_brain/promotions/<promotion_id>/
```

The directory is assembled beside the destination and renamed
into place. `--apply` holds an exclusive lock in the parent of
the vault, then repeats preflight before the rename. Inspect-only
does not take that lock. Final validation runs after the rename.
If it fails, the new promotion directory is removed. A failed
preflight leaves the vault unchanged.
The same promotion id with the same promotion digest is
idempotent only when the installed manifest, receipt, document
hashes, and vault health still match. A matching digest with
altered content returns `promotion_integrity_failed` and is not
repaired. The same id with a different digest conflicts and is
not overwritten.

The promotion digest covers schema, id, source identity, review
decision, reviewer, review time, reason, and the sorted document
paths and hashes. It does not cover `applied_at` or local paths.

`promotion_receipt.json` records that audit identity and the
result. It does not record credentials or hostnames.

## Boundaries

Phase 5D adds new semantic documents only. It does not edit a
validated document, replace a claim, or mark a package
superseded. Supersession needs a later contract.

There is no MCP promotion tool, Hermes allowlist entry, REST
endpoint, Supervisor writeback, automatic ingest, LLM review,
or semantic Databricks table.
