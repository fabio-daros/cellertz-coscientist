# VHH campaign scientific contract — Phase 6C.1

Phase 6C can name a target, an objective, a generator, a candidate
count, and generic constraints. A VHH campaign used with mBER also
needs a structure, a preparation record, an epitope, a masked
scaffold, a generator protocol, an acceptance policy, and a stopping
policy. Phase 6C.1 defines those facts. It does not approve them and
it does not run them.

```
ProteinDesignRequest
        ↓
project_vhh_campaign  (missing scientific fields stay missing)
        ↓
VHHCampaignSpecification
        ↓
human approval (future 6D)
        ↓
execution and monitoring (future 6E)
```

`execution_authorized` and `executable` stay false. GPU choice, shell
commands, screen sessions, process ids, and credentials are execution
infrastructure. They are not fields of the specification.

## Artifact references

An artifact reference may be an opaque id or a URI, including
`artifact://...`, `s3://...`, and a relative locator. That locator is
where a file might be found. It is not the scientific identity of the
structure, and it does not replace `content_digest`. Two campaigns can
point at different URIs and still describe the same prepared input when
the identity components and digest agree. A credential inside a URI, or
a shell command used as a reference, is rejected. This phase does not
read or store the file.

## Prepared-target identity

The PDB id names the source structure. It does not make two prepared
inputs the same. A prepared input is distinguished by the source
structure, the selected source and prepared chains, the ordered
preparation steps, the source-to-prepared chain mapping, the epitope,
and the optional prepared content digest. Those components are
serialized as `prepared_target_identity`. PDB 7O52 with chain U mapped
to prepared chain A is a different input from PDB 7O52 mapped to
another chain. No campaign digest is computed in this phase.

## Source and prepared chain namespaces

Chain letters are qualified as `source` or `prepared`. For TACI, PDB
1XU1, the source target chain is R, the source partner chains are A, B,
and D, and the prepared target chain is A via the mapping R → A. Source
partner A is the ligand. Prepared chain A is the relabeled target.
Epitope residues in the fixture use the prepared namespace.

## Generator version

An unknown generator version stays `None`, with status `not_recorded`.
The CD22 and TACI fixtures remain valid `operational_reference`
objects. Their completeness is `incomplete` and
`reproducible_readiness` is `not_ready` because `generator_version` is
absent. The generic request bridge lists that field as missing. The
word `latest` is rejected. Recording a concrete version can make the
specification complete. That state is still not approval.

## Protocol-specific metrics

mBER's recorded filters are `i_ptm >= 0.75` and `plddt >= 0.70`, role
`generator_filter`. They apply to that protocol only. They are not
`ranking_score`, not a binding probability, and not a default for
BindCraft or any other generator. A generic acceptance threshold must
be a finite real number. Zero and negative values stay available for
other protocols. Where the existing platform already defines it,
`ranking_score` remains raw ipSAE. This contract does not ask mBER to
emit ipSAE. Boltz is not an acceptance filter and is not a ranking
signal here.

## Evidence stages

`generated`, `generator_accepted`, `independently_evaluated`,
`developability_screened`, `off_target_screened`, and
`scientifically_reviewed` are separate records. Recording one does
not set another.

```
GENERATOR_ACCEPTED != SCIENTIFICALLY_VALIDATED
INDEPENDENTLY_EVALUATED != BIOLOGICALLY_VALIDATED
DEVELOPABILITY_SCREENED != OFF_TARGET_SCREENED
COMPUTATIONAL_SUCCESS != EXPERIMENTAL_VALIDATION
```

## Operational references

CD22 (PDB 7O52) and TACI (PDB 1XU1) fixtures copy the recorded mBER
configuration: chains, 4.5 Å structure contacts, the masked VHH
scaffold, the two filters, and the stopping counts 5000 / 50000.
Provenance is `operational_reference`. Because the generator version
was not recorded, both fixtures are `incomplete` and not ready for
reproducible approval. The fixtures do not claim that a generated VHH
binds. In PDB 1XU1, source partner chain A is the ligand. Prepared
target chain A is source chain R after relabel.

Target preparation is an ordered list of operations (`remove_solvent`,
`extract_chain`, `relabel_chain`, `set_segid`, and, for TACI,
`remove_selected_heteroatoms`). The list is provenance. This phase
does not read or write a PDB file.

## What stays manual

Choosing the epitope, checking it against a structure, and deciding
that a campaign may run remain outside this contract. Phase 6D can
record a decision on a design draft. Phase 6E can monitor a
fixture-only run for that demonstration. Phase 6F can persist that
operational run and recover an interrupted one without resuming it.
Phase 6G can enqueue that run and let a server-owned background worker
advance the fixture lifecycle while the workspace polls read-only
state. A queued attempt survives restart; an interrupted running
attempt fails closed and requires explicit retry. Phase 6H can list
that session's fixture runs and read the operational artifact bytes
produced by the fixture adapter. Those bytes are not CD22 or TACI
candidates, not structures, and not scientific memory. None of these phases executes these
CD22 or TACI fixtures. Phase 6I can name `cd22-mber-v1` and
`taci-mber-v1` only to keep them blocked: an mBER version in a
readiness manifest is not accepted as a recorded execution
requirement, and the readiness route does not run them or launch a
probe process. mBER, BindCraft2, and RFantibody
are names of protocols. They are not integrated executors.

Phase 6C.2 projects these fixtures in the workspace. That projection
is presentation. It does not add approval, execution, or experimental
validation. Phase 6D's fixture demonstration is a separate application
gate. It does not record an mBER version, does not set these fixtures
to `execution_authorized`, and does not make them executable. The guided workspace separates configuration, execution
state, candidate evidence, human decision, and handoff readiness. It
does not invent candidates, and it does not connect to Codonscope.
