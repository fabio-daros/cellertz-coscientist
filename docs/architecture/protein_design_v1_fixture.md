# Protein Design v1 fixture baseline

Formal checkpoint after 3F.3. Phase 3 is closed. The one-round
Protein Design pipeline is complete in fixture mode. Hermes and
Second Brain do not cross the decision boundary.

```
Protein Design v1 fixture baseline
----------------------------------
Tag: protein-design-v1-fixture
Tests: 215 passed

Decision boundary:
- ranking deterministic
- critic deterministic
- grounded knowledge contextual only
- Hermes explanatory only
- prioritization independent of LLM output
- real scientific generators disabled
```

## Fixture-complete surface

| Piece | Status |
| --- | --- |
| Generation | fixture |
| Scoring | complete |
| Ranking | complete |
| Critic | complete |
| Second Brain | complete |
| Grounded context | complete |
| Hermes explanation | complete, non-blocking |
| Prioritization | complete |
| Supervisor | complete |
| Run state / provenance | complete |
| Scientific invariants | held |
| Real generators | locked |
| GPU execution | locked |

## Architectural properties

```
Hermes DOWN
    │
    └──► scientific run still completes

Second Brain unavailable
    │
    └──► deterministic Critic still operates

Explanation changes
    │
    └──► candidate selection does not change
```

MCP mutation stays `fixture_only` with
`real_generators_allowed = false`. That is not a toggle for
real BindCraft, RFdiffusion, or mBER.

## Next chapter

Phase 3G is closed at `second-brain-v1-hardened`. Phase 4A is
the Scientific Execution Contract in `docs/architecture/scientific_execution.md`.
`fixture_only` remains closed. Real generators stay locked.

The path stays:

```
Hermes
   ↓ controlled capability
Scientific Supervisor
   ↓
Generator Adapter
   ↓
ExecutionRuntime
   ↓
AWS Batch / local controlled execution
```

Not:

```
Hermes → shell → BindCraft
```
