---
id: ipsae_primary_ranking
type: claim
domain: protein_design
status: validated
version: 1
statement: Raw ipSAE remains the primary structural ranking signal.
definition: not_provided
undefined_terms: ipSAE
---

# Raw ipSAE remains the primary structural ranking signal

## Scope of the validation

Read off the evidence below, not added to it. The cohort is the Chow BCMA
benchmark (`chow_bcma`): 766 candidates, 34 yeast-display positives, 732
negatives, against one target. The two hold-outs are a **BindCraft** campaign
and an **RFdiffusion** campaign. ipSAE beat paper ipTM on both (AP 0.3082 vs
0.2421 on BindCraft1, 0.2501 vs 0.1589 on RFD5), and globally by very little
(0.0924 vs 0.0887).

No VHH design and no mBER design was in this benchmark. The claim is therefore
evidence about BindCraft and RFdiffusion minibinders against one target, and
carrying it over to an mBER VHH campaign would be the unprovenanced transfer
that `no_validated_composite_score` refuses elsewhere. ipSAE is not a property
of BindCraft — any AlphaFold-style PAE matrix and structure yields it — but
mBER writes no PAE matrix, so ipSAE for an mBER design requires an independent
refold that produces one.

Names here are deliberately not wikilinks: this section scopes what was
measured and adds no edge to the graph.

Supported by:

- [[boltz2_v2_global_benchmark]]
- [[boltz2_v2_bindcraft1_holdout]]
- [[boltz2_v2_rfd5_holdout]]

Part of:

- [[protein_scoring_bcma_boltz2_v2]]
