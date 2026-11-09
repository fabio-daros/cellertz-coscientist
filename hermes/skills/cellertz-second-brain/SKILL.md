---
name: cellertz-second-brain
description: "Answer Cellertz science from one compact EvidencePacket."
version: 1.1.0
---

# Cellertz grounded retrieval

For scientific why questions about protein scoring, ranking, ipSAE, or Boltz2, do not walk Markdown and do not chain `knowledge_get` / `knowledge_neighbors`.

## Procedure

1. Call `knowledge_ground_package` once with
   `package_id=protein_scoring_bcma_boltz2_v2`.
2. Reason only over the returned EvidencePacket.
3. Use `claims`, `evidence` numbers, and `validated_interpretation` as given.
4. Do not recompute the ranking decision.
5. Do not expand terms in `unsupported_expansions`.
6. `knowledge_get` remains for navigation only.

## Packet labels

- `evidence.bindcraft1` is BC1 / BindCraft Campaign 1.
- `evidence.rfd5` is RFD5.
- Those labels are not knowledge IDs. Do not call `knowledge_get("BC1")`,
  `knowledge_get("ipSAE")`, or `knowledge_get("Boltz2")`.

## How to read the packet

- Use `claims[].statement` for the validated claims.
- Quote `evidence.bindcraft1` and `evidence.rfd5` AP values in the answer.
- Use `validated_interpretation` as already-validated knowledge.
- Report `unsupported_expansions` as not provided. Do not invent expansions.
