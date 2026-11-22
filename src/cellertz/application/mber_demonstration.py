"""Bounded mBER demonstration on the GPU, through the existing launch path.

The Apptainer demonstration already carries everything that is not the
generator: the human approval, the idempotency key, the attempt directory, the
GPU index, the process-group supervisor, and the progress file. This module
reuses all of it and replaces only what is BindCraft's. ``GenerationPlanService``
duck-types ``launch``, so the frontend's existing run action reaches this
launcher unchanged when the composition root builds this one.

Three things are not a parameter swap, which is why this is a sibling module
and not a flag on the other one:

**One trajectory per process.** mBER builds a torch ESM2 model per trajectory
and frees none, while JAX holds AlphaFold2's allocation, so the second
trajectory in a process fails whichever allocator asks second. BindCraft runs
its whole budget in one process; mBER cannot. The loop below is that fact.

**A hotspot list does not steer mBER.** The atlas is explicit. The epitope is
still declared, because the ranking measures against it afterwards, but the
run is not steered by it and this module never reports that it was.

**The output tree is mBER's.** ``accepted.csv`` plus absolute structure paths,
read by ``rank_mber_output``, not by ``ingest_bindcraft_output``.

What this is not: a campaign. The recorded stopping policy for CD22 is 5000
accepted designs out of at most 50000 trajectories, and the observed yield is
1 accepted design in 8 trajectories at roughly 8.8 minutes each. A button
cannot start that, so ``MAX_DEMONSTRATION_TRAJECTORIES`` caps what one launch
may ask for and the reply says ``scientific_validation`` is false.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cellertz.application.apptainer_demonstration import (
    DemonstrationLaunch,
    GroupSupervisedRuntime,
    _executor_diagnosis,
    _executor_log,
    _gpu_observation,
    _now,
    _progress,
    _safe_attempt_directory,
    _sha256,
    explicit_gpu_index,
    gpu_launch_blocker,
    gpu_launch_readiness,
    vram_failure_report,
)
from cellertz.application.attempt_evidence import (
    PDL1_PD1_FOOTPRINT,
    PDL1_PD1_FOOTPRINT_IDENTITIES,
    numbering_offset,
)
from cellertz.application.contracts import ApplicationContractError
from cellertz.application.generation_plan import COMPLEX_FILENAME
from cellertz.application.mber_campaign import SETTINGS_FILENAME, rank_mber_output
from cellertz.application.mber_settings import (
    GENERATOR_VERSION,
    SeedDesign,
    seeded_masked_sequence,
    write_mber_vhh_settings,
)
from cellertz.application.vhh_campaign import (
    AcceptanceMetric,
    AcceptancePolicy,
    ArtifactReference,
    ChainMapping,
    ChainRef,
    EpitopeDefinition,
    GeneratorProtocol,
    ResidueReference,
    ScientificProvenance,
    StoppingPolicy,
    TargetStructureReference,
    VHHCampaignSpecification,
    blank_evidence,
)

# The same masked VHH framework the recorded campaigns use. Reused rather than
# copied: two masked sequences that drift apart are two different scaffolds.
from cellertz.application.vhh_campaign_fixtures import _scaffold
from cellertz.domains.protein_design.adapters.mber import STATS_FILENAME
from cellertz.domains.protein_design.scoring.hotspot_null_model import CONTACT_ANGSTROM
from cellertz.domains.protein_design.scoring.interface_area import InterfaceAreaError
from cellertz.domains.protein_design.scoring.partner_classification import (
    partner_contact_residues,
)
from cellertz.runtime.base import ExecutionRequest

PROFILE_ID = "pdl1-mber-apptainer-demonstration-v1"
SIF_PATH = "/home/fabio/containers/cellertz/mber/mber.sif"
SIF_SHA256 = "edadcf1a7cd26c56ff7e2cd2412746e003b27f2de3353431341e484fa5b98e93"
# mBER hardcodes ~/.mber/af_params and ~/.mber/nbb2_weights and reads no
# environment variable for them. HOME is /mber in the image, so the 8.3 GB of
# weights are bound over /mber/.mber rather than baked in.
WEIGHTS_DIR = "/home/fabio/containers/cellertz/mber/weights"
WEIGHTS_BIND = "/mber/.mber"
RUNTIME_NAME = "apptainer-group-supervisor"
# One accepted design in 8 trajectories at ~8.8 minutes each, measured on this
# card. Eight is the bound on a single launch, not a scientific sample size.
MAX_DEMONSTRATION_TRAJECTORIES = 8
TRAJECTORY_DEADLINE_SECONDS = 1800
TRAJECTORY_FIELD = "mber_trajectories"
CAMPAIGN_ID = "pdl1-mber-demonstration"
COHORT_DIRNAME = "cohort"
_NOTE = (
    "Bounded technical demonstration of mber-open on the registered PD-L1 "
    "target. Not a campaign, not scientific validation, and not experimental "
    "proof of binding."
)
# The PD-1 interface on PD-L1, in the registered target file's frame. It is
# the site a PD-L1 blocker has to cover, which is why the ranking takes it as
# the clinical reference; it is not an antibody-derived epitope and is not
# recorded as one.
CLINICAL_REFERENCE = "pdl1_pd1_interface_4zqk"


def _mber_progress(directory: Path, status: str, **extra: Any) -> None:
    """``_progress`` stamps BindCraft's profile id; the extras override it."""

    _progress(directory, status, profile_id=PROFILE_ID, **extra)


def _provenance() -> ScientificProvenance:
    return ScientificProvenance("operational_reference", _NOTE, (CLINICAL_REFERENCE,))


class TargetNotDeclared(ValueError):
    """The plan does not say enough about its target to run a campaign on it."""


@dataclass(frozen=True)
class DeclaredTarget:
    """What a plan says this campaign is aimed at, and how its epitope was got.

    The specification used to be PD-L1's whatever the plan asked for: target
    ``PDL1``, structure 4ZQK, the PD-1 footprint as the epitope. A campaign on
    another target recorded the wrong target id on its accepted candidate and
    measured J_clin against the wrong surface — a record that is not merely
    incomplete but false.

    ``epitope`` is in the **registered target file's** frame. The ranking's
    output structures are renumbered, so ``identities`` travels with it: every
    one of these residues has to match by identity before an offset between
    the two frames is accepted.
    """

    target_id: str
    source_structure: str | None
    target_chain: str | None
    partner_chains: tuple[str, ...]
    epitope: frozenset[int]
    identities: Mapping[int, str]
    epitope_provenance: str
    epitope_note: str


_RECORDED_PDL1_NOTE = (
    "PD-1 interface on PD-L1. All 22 residues match the registered "
    "target file by identity, which is what establishes the frame. "
    "A hotspot list does not steer mBER; this is declared so the "
    "ranking can measure against it afterwards."
)
# The demonstration this module was written for, so its own bounds can still
# be tested without a registered root. A launch never uses it implicitly: it
# passes what the plan declared.
_RECORDED_PDL1 = DeclaredTarget(
    target_id="PDL1",
    source_structure="4ZQK",
    target_chain="A",
    partner_chains=(),
    epitope=frozenset(PDL1_PD1_FOOTPRINT),
    identities=PDL1_PD1_FOOTPRINT_IDENTITIES,
    epitope_provenance="recorded_operational_reference",
    epitope_note=_RECORDED_PDL1_NOTE,
)


def declared_target(fields: object, target_path: Path) -> DeclaredTarget:
    """Read the campaign's target off the approved plan, measuring its epitope.

    The epitope is the partner's footprint on the target, measured on the
    source complex beside the registered file with the same function the
    block classification uses — so the surface the ranking scores against is
    the one the plan was approved against, not a constant.

    PD-L1 is the one exception, and it is not a fallback: that campaign's
    epitope is a **recorded** operational reference (the PD-1 interface on
    4ZQK, curated, 22 residues) and its root holds no complex. It is accepted
    only when the registered file's own residues still match that record by
    identity, so the name alone cannot borrow it.
    """

    if not isinstance(fields, dict):
        raise TargetNotDeclared("plan_declares_no_fields")
    target_id = fields.get("target_id")
    if not isinstance(target_id, str) or not target_id.strip():
        raise TargetNotDeclared("plan_declares_no_target_id")
    target_id = target_id.strip()
    present = _prepared_identities(target_path)
    if not present:
        raise TargetNotDeclared("registered_target_has_no_chain_a_residues")
    chain = _text(fields.get("target_chain"))
    partners = tuple(
        item for item in (_text(fields.get("partner_chains")) or "").split(",") if item
    )
    measured = _measured_epitope(target_path, chain, partners)
    if measured is None:
        if target_id.upper() != "PDL1":
            raise TargetNotDeclared(
                f"no_measured_partner_footprint_for:{target_id}. The plan has to declare "
                "target_chain and partner_chains, and the input root a complex.pdb, "
                "because this campaign's epitope is measured and not assumed."
            )
        if any(
            present.get(residue) != code
            for residue, code in PDL1_PD1_FOOTPRINT_IDENTITIES.items()
        ):
            raise TargetNotDeclared(
                "registered_target_is_not_the_recorded_pdl1_reference"
            )
        epitope = frozenset(PDL1_PD1_FOOTPRINT)
        provenance_label = "recorded_operational_reference"
        note = _RECORDED_PDL1_NOTE
    else:
        # A preparation only removes, so a footprint residue the prepared file
        # no longer holds cannot be part of this campaign's epitope. Dropping
        # one silently would overstate the surface, so the count is recorded.
        epitope = frozenset(measured) & set(present)
        dropped = len(measured) - len(epitope)
        if not epitope:
            raise TargetNotDeclared("measured_footprint_is_absent_from_the_prepared_target")
        provenance_label = "measured_partner_footprint"
        note = (
            f"The footprint of chain(s) {','.join(partners)} on chain {chain} of "
            f"{_text(fields.get('source_structure')) or 'the registered complex'}, "
            f"measured at {CONTACT_ANGSTROM} A heavy atom contact: {len(epitope)} "
            "residues present in the registered target file"
            + (f", {dropped} removed by the preparation. " if dropped else ". ")
            + "A hotspot list does not steer mBER; this is declared so the "
            "ranking can measure against it afterwards."
        )
    return DeclaredTarget(
        target_id=target_id,
        source_structure=_text(fields.get("source_structure")),
        target_chain=chain,
        partner_chains=partners,
        epitope=epitope,
        identities={residue: present[residue] for residue in sorted(epitope)},
        epitope_provenance=provenance_label,
        epitope_note=note,
    )


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _prepared_identities(target_path: Path) -> dict[int, str]:
    """Chain A residue identities of the registered target file.

    Chain A because that is the frame mBER writes its target in and the one
    ``rank_mber_output`` measures occlusion on; a prepared file that is not
    chain A would not be measurable later either.
    """

    try:
        body = target_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    found: dict[int, str] = {}
    for line in body.splitlines():
        if line.startswith(("ATOM", "HETATM")) and line[21:22].strip() == "A":
            try:
                found[int(line[22:26])] = line[17:20].strip().upper()
            except ValueError:
                continue
    return found


def _measured_epitope(
    target_path: Path, chain: str | None, partners: tuple[str, ...]
) -> frozenset[int] | None:
    """The partner's footprint on the target, from the complex beside the file.

    ``None`` when this campaign has nothing to measure it from: no declared
    chains, or no registered complex. A refusal to measure is not a licence to
    assume, so the caller decides what that means.
    """

    if chain is None or not partners:
        return None
    complex_pdb = target_path.parent / COMPLEX_FILENAME
    try:
        body = complex_pdb.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        found = partner_contact_residues(body, chain, partners)
    except InterfaceAreaError:
        return None
    return frozenset(found["contact_residues"])


def demonstration_specification(
    trajectories: int,
    accepted_target: int | None = None,
    target: DeclaredTarget | None = None,
) -> VHHCampaignSpecification:
    """The campaign specification one bounded demonstration run records.

    Every value the settings document needs comes from here, including the
    generator version, which 6C.1 requires and a readiness manifest is not
    allowed to supply. The target is recorded as it arrives: the registered
    file is already chain A in the interface frame, and no preparation step is
    claimed here because this module performs none.

    ``target`` is what the approved plan declared, from ``declared_target``.
    Leaving it out keeps the recorded PD-L1 demonstration, which is what the
    tests of this module's own bounds are about; a launch always passes one,
    because a specification that names a target the plan did not ask for
    misreports the run.
    """

    if (
        type(trajectories) is not int
        or isinstance(trajectories, bool)
        or not 1 <= trajectories <= MAX_DEMONSTRATION_TRAJECTORIES
    ):
        raise ValueError("invalid_demonstration_trajectory_count")
    if accepted_target is not None and (
        type(accepted_target) is not int or isinstance(accepted_target, bool) or accepted_target < 1
    ):
        raise ValueError("invalid_demonstration_accepted_target")
    provenance = _provenance()
    declared = _RECORDED_PDL1 if target is None else target
    measured = declared.epitope_provenance == "measured_partner_footprint"
    source = declared.source_structure or "4ZQK"
    structure = TargetStructureReference(
        source_type="pdb",
        source_identifier=source,
        target_name=declared.target_id,
        source_target_chain_ids=(declared.target_chain or "A",),
        source_partner_chain_ids=declared.partner_chains,
        prepared_target_chain_ids=("A",),
        chain_mapping=(ChainMapping(declared.target_chain or "A", "A"),),
        preparation_provenance=provenance,
        preparation_status="recorded",
        original_structure=ArtifactReference(f"pdb:{source}", "original_structure", provenance),
        prepared_structure=ArtifactReference(
            f"registered-{declared.target_id.lower()}-target", "prepared_structure", provenance
        ),
    )
    epitope = EpitopeDefinition(
        residues=tuple(
            ResidueReference("A", number, chain_namespace="prepared")
            for number in sorted(declared.epitope)
        ),
        # Either measured here, by the same contact function the block
        # classification uses, or the recorded curated reference. The two are
        # not interchangeable, so the derivation says which one this is rather
        # than claiming a calculation that did not run.
        derivation_method="structure_contact" if measured else "manually_curated",
        provenance=provenance,
        verification_status="operational_reference",
        source_structure_identifier=source,
        contact_cutoff_angstrom=CONTACT_ANGSTROM if measured else None,
        # The partner is named in the *source* namespace: those chains exist
        # in the complex the footprint was measured on, not in the prepared
        # single-chain file the generator opens.
        partner_chains=tuple(
            ChainRef("source", chain) for chain in declared.partner_chains
        )
        if measured
        else (),
        biological_rationale=declared.epitope_note,
    )
    protocol = GeneratorProtocol(
        protocol_id="mber-vhh-operational",
        generator_name="mber",
        design_modality="vhh",
        algorithm_family="alphafold_multimer_backprop",
        scaffold_mode="fixed_masked_vhh",
        evaluation_strategy="independent_refold",
        provenance=provenance,
        protocol_version=None,
        generator_version=GENERATOR_VERSION,
    )
    acceptance = AcceptancePolicy(
        policy_id="mber-vhh-operational-filters",
        protocol_id="mber-vhh-operational",
        metrics=(
            AcceptanceMetric(
                "i_ptm", "gte", 0.75, "generator_acceptance", "generator_filter", provenance
            ),
            AcceptanceMetric(
                "plddt", "gte", 0.70, "generator_acceptance", "generator_filter", provenance
            ),
        ),
        provenance=provenance,
    )
    return VHHCampaignSpecification(
        campaign_id=CAMPAIGN_ID,
        request_id=f"request_{CAMPAIGN_ID}",
        session_id="session_mber_demonstration",
        target_id=declared.target_id,
        provenance=provenance,
        completeness="incomplete",
        target_structure=structure,
        preparation=(),
        epitope=epitope,
        scaffold=_scaffold(),
        generator_protocol=protocol,
        acceptance_policy=acceptance,
        stopping_policy=StoppingPolicy(
            # One trajectory may accept more than one design, so the accepted
            # target is not the trajectory bound. Whichever is reached first
            # ends the launch: the target comes from the displayed design
            # count when there is one, and the trajectory bound caps the card.
            accepted_design_target=trajectories if accepted_target is None else accepted_target,
            max_trajectories=1,
            provenance=provenance,
            stopping_reason=None,
        ),
        rationale=_NOTE,
        evidence=blank_evidence(provenance),
    )


def requested_trajectories(fields: object) -> int | None:
    """How many trajectories the displayed revision asks for, or ``None``.

    Absent means one, which is what a demonstration launch means without the
    field. A value outside the bound is a refusal rather than a clamp: a run
    that silently did less than the screen said is worse than no run.
    """

    if not isinstance(fields, dict):
        return None
    requested = fields.get(TRAJECTORY_FIELD)
    if requested is None:
        return 1
    if type(requested) is not int or isinstance(requested, bool):
        return None
    if not 1 <= requested <= MAX_DEMONSTRATION_TRAJECTORIES:
        return None
    return requested


def trajectory_argv(
    apptainer_bin: Path,
    sif_path: Path,
    settings_path: Path,
    gpu_index: int,
    weights_dir: Path,
) -> tuple[str, ...]:
    """One Apptainer invocation. The image's runscript is ``mber-vhh``.

    The two cache variables are not decoration. HOME is the bind mount point
    and the image is read-only, so torch, matplotlib and fontconfig each
    default to an unwritable path. Observed on the first real launch: "kernel
    cache directory could not be created", which disables CUDA kernel caching
    for the whole process. The built image predates these two lines in
    ``mber.def``, so the caller supplies them.
    """

    return (
        str(apptainer_bin),
        "run",
        "--nv",
        "--env",
        f"CUDA_VISIBLE_DEVICES={gpu_index}",
        "--env",
        "XDG_CACHE_HOME=/tmp/cache",
        "--env",
        "MPLCONFIGDIR=/tmp/matplotlib",
        "--bind",
        f"{weights_dir}:{WEIGHTS_BIND}",
        str(sif_path),
        "--settings",
        str(settings_path),
    )


def merge_accepted(trajectory_dirs: list[Path], cohort: Path) -> int:
    """One cohort CSV from several trajectory outputs, and its row count.

    Percentiles are computed over a cohort, so the trajectories of one launch
    are ranked together rather than separately. The structure paths inside
    ``accepted.csv`` are absolute, so they still resolve from here.
    """

    cohort.mkdir(parents=True, exist_ok=True)
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for directory in trajectory_dirs:
        path = directory / STATS_FILENAME
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                continue
            if header is None:
                header = list(reader.fieldnames)
            elif list(reader.fieldnames) != header:
                raise ValueError("mber_accepted_csv_columns_differ")
            rows.extend(reader)
    target = cohort / STATS_FILENAME
    if header is None:
        target.write_text("", encoding="utf-8")
        return 0
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def clinical_epitope_in_output_frame(
    cohort: Path, target: DeclaredTarget | None = None
) -> tuple[frozenset[int], ...] | None:
    """The declared epitope, renumbered into the frame the output uses.

    mBER renumbers the target on output exactly as the BindCraft attempts do,
    so a residue number carried across the two frames without this names a
    different residue. The offset is established by residue identity on a real
    output structure, never assumed; without one, there is no clinical epitope
    to rank against and this returns ``None``.

    The identities are the declared target's, so the check still refuses a
    structure that is a different protein — it just is no longer only PD-L1's
    that it will accept.
    """

    declared = _RECORDED_PDL1 if target is None else target
    for pdb in sorted(cohort.parent.rglob("*_complex.pdb")):
        offset = numbering_offset(pdb.read_text(encoding="utf-8"), declared.identities)
        if offset is not None:
            return (frozenset(residue - offset for residue in declared.epitope),)
    return None


class SeedRefused(ValueError):
    """A parent design was handed down that this scaffold cannot be seeded on."""


def _seed_design(recorded: Any, specification: VHHCampaignSpecification) -> SeedDesign | None:
    """Turn the round's recorded parent design into mBER's seeding input.

    ``None`` means an unseeded campaign, which is what a first round is. A
    recorded seed that cannot be reseated is **refused**, never dropped:
    falling back to the bare framework would run a different experiment from
    the one the round was opened for, and the series exists to measure exactly
    that difference.

    Which loop is reopened is **ours**, like the classification's edge cut:
    the last declared CDR region, which on this scaffold is CDR3 at 96-109 and
    is what the seeding test in this repository reopens. Reopening *every* CDR
    is not seeding at all — the parent's residues sit only in design positions,
    so opening all of them reproduces the bare scaffold exactly, which is the
    bug the equality check below catches.

    shortcut: the reopened loop is the last declared CDR. Upgrade when a child
    round declares which positions it reopens, which is the campaign's choice
    and not this module's.
    """

    if recorded is None:
        return None
    if not isinstance(recorded, Mapping):
        raise SeedRefused("seed_is_not_a_record")
    design = recorded.get("design")
    sequence = recorded.get("sequence")
    if not isinstance(design, str) or not design:
        raise SeedRefused("seed_names_no_design")
    if not isinstance(sequence, str) or not sequence:
        raise SeedRefused(f"seed_carries_no_sequence_for:{design}")
    scaffold = specification.scaffold
    if scaffold is None or not scaffold.cdr_regions:
        raise SeedRefused("scaffold_declares_no_cdr_region_to_redesign")
    last = scaffold.cdr_regions[-1]
    spans = ((last.start, last.end),)
    try:
        # Built here so the refusal happens with the other pre-flight checks
        # rather than inside the first trajectory's settings write.
        reseated = seeded_masked_sequence(scaffold.masked_sequence, sequence, spans)
    except ApplicationContractError as error:
        raise SeedRefused(f"{error}:{design}") from error
    if reseated == scaffold.masked_sequence:
        raise SeedRefused(f"seed_reproduces_the_bare_scaffold:{design}")
    return SeedDesign(design_id=design, sequence=sequence, redesign_spans=spans)


def _declared_candidate(accepted: Any, attempt_id: str) -> dict[str, Any]:
    """The acceptance fields a ranked launch adds to its reply.

    A journey round reads ``accepted_candidate`` to decide whether specialists
    may run, and this launcher declared none: a run that screened and ranked a
    real cohort still arrived as "no accepted candidate" and the specialists
    were never invoked.

    The declaration is the panel's, not this function's — ``rank_mber_output``
    decides which design, and only a design the panel selected can be it. The
    attempt id is added here because the panel does not know which attempt
    read the output directory.
    """

    if not isinstance(accepted, dict) or accepted.get("accepted") is not True:
        reasons = accepted.get("reasons") if isinstance(accepted, dict) else None
        return {
            "accepted_candidate": False,
            "successful_candidate_generation": False,
            "acceptance_evidence": (
                accepted.get("summary")
                if isinstance(accepted, dict)
                else {"accepted": False, "reasons": ["ranking_declared_no_candidate"]}
            ),
            "termination_reason": f"ranked_without_an_accepted_candidate:{','.join(reasons or ())}"
            if reasons
            else "ranked_without_an_accepted_candidate",
        }
    identity = accepted.get("candidate_identity")
    identity = dict(identity) if isinstance(identity, dict) else {}
    identity["execution_id"] = attempt_id
    return {
        "accepted_candidate": True,
        "successful_candidate_generation": True,
        "candidate_id": accepted.get("candidate_id"),
        "candidate_identity": identity,
        "acceptance_evidence": accepted.get("summary"),
    }


class MberDemonstrationLauncher:
    """Explicit launch of mBER. Construction does not start Apptainer.

    It exposes ``sif_path``, ``sif_sha256`` and ``profile_id`` because the plan
    service asks the launcher which image it runs rather than importing
    BindCraft's constants.
    """

    profile_id = PROFILE_ID
    sif_path = SIF_PATH
    sif_sha256 = SIF_SHA256
    # settings.json and advanced.json are BindCraft's configuration. This
    # launcher writes mBER's own settings per trajectory from the campaign
    # specification, so requiring those two would verify bytes nothing opens.
    required_input_roles = ("target_structure",)

    def __init__(
        self,
        *,
        weights_dir: str = WEIGHTS_DIR,
        gpu_readiness: Any | None = None,
        runtime: Any | None = None,
        check_gpu: bool = True,
    ) -> None:
        self.weights_dir = Path(weights_dir)
        self.gpu_readiness = gpu_readiness
        self.runtime = runtime
        self.check_gpu = check_gpu

    def launch(self, request: DemonstrationLaunch) -> dict[str, Any]:
        refused: dict[str, Any] = {
            "status": "launch_rejected",
            "launched": False,
            "profile_id": PROFILE_ID,
        }
        if request.approved is not True or request.approval_source not in {
            "human_review",
            "explicit_run",
        }:
            return {**refused, "status": "approval_required"}
        if request.input_selection != request.live_input_selection:
            return {**refused, "mismatched": ["input_identity"]}
        if _safe_attempt_directory(request.attempt_id, request.attempt_directory) is not None:
            return refused
        trajectories = requested_trajectories(request.fields)
        if trajectories is None:
            return {**refused, "diagnosis": "trajectory_count_rejected", "automatic_retry": False}
        assigned = explicit_gpu_index(request.fields.get("gpu"))
        if (
            type(request.gpu_index) is not int
            or isinstance(request.gpu_index, bool)
            or assigned is None
            or assigned != request.gpu_index
        ):
            return {
                **refused,
                "status": "gpu_index_mismatch",
                "gpu_index": request.gpu_index if type(request.gpu_index) is int else None,
            }
        try:
            # Before the card, not after: a campaign whose target the plan did
            # not declare would record the wrong target on its candidate and
            # rank against the wrong surface.
            target = declared_target(request.fields, request.target_path)
        except TargetNotDeclared as error:
            return {**refused, "status": "target_not_declared", "diagnosis": str(error)}
        # The screen's design count is what the person asked for, so it is the
        # accepted target rather than a number the run quietly ignores. The
        # trajectory bound still caps the card.
        accepted_target = request.n_designs if type(request.n_designs) is int else None
        specification = demonstration_specification(trajectories, accepted_target, target)
        try:
            # With the other pre-flight refusals: a seed that does not fit this
            # scaffold would silently become an unseeded campaign, and the
            # series would measure the lever it was meant to pull as inert.
            seed = _seed_design(request.seed_design, specification)
        except SeedRefused as error:
            return {**refused, "status": "seed_rejected", "diagnosis": str(error)}
        if not request.sif_path.is_file() or _sha256(request.sif_path) != request.expected_sif_sha256:
            return {**refused, "status": "sif_hash_mismatch"}
        if not request.apptainer_bin.is_file():
            return {**refused, "status": "apptainer_unavailable"}
        if not (self.weights_dir / "af_params").is_dir():
            # Without the bind mount mBER reaches the network mid-run for ESM2
            # and fails on AlphaFold2 outright. Better to refuse than to spend
            # the card discovering it.
            return {**refused, "status": "mber_weights_unavailable"}
        report = None
        if self.check_gpu:
            readiness = self.gpu_readiness or gpu_launch_readiness
            report = readiness(request.gpu_index)
            blocker = gpu_launch_blocker(report if isinstance(report, dict) else None)
            if blocker is not None:
                return {**refused, "status": blocker, "gpu_index": request.gpu_index}
        request.attempt_directory.mkdir()
        _mber_progress(
            request.attempt_directory,
            "planned",
            trajectories=trajectories,
            generator_version=GENERATOR_VERSION,
            gpu_observation=_gpu_observation(report if isinstance(report, dict) else None),
        )
        cohort = request.attempt_directory / COHORT_DIRNAME
        runtime = self.runtime or GroupSupervisedRuntime()
        completed: list[Path] = []
        accepted_rows = 0
        for index in range(1, trajectories + 1):
            output_dir = request.attempt_directory / f"t{index}"
            output_dir.mkdir()
            settings_path = write_mber_vhh_settings(
                specification,
                output_dir,
                request.target_path,
                output_dir / SETTINGS_FILENAME,
                seed_from=seed,
            )
            argv = trajectory_argv(
                request.apptainer_bin,
                request.sif_path,
                settings_path,
                request.gpu_index,
                self.weights_dir,
            )

            def _record_supervisor(pid: int, index: int = index, output_dir: Path = output_dir) -> None:
                (request.attempt_directory / "supervisor.pid").write_text(
                    f"{pid}\n", encoding="utf-8"
                )
                _mber_progress(
                    request.attempt_directory,
                    "started",
                    trajectory=index,
                    worker_pid=os.getpid(),
                    supervisor_pid=pid,
                    output_dir=str(output_dir),
                )

            result = runtime.execute(
                ExecutionRequest(argv=argv, timeout_seconds=TRAJECTORY_DEADLINE_SECONDS),
                on_start=_record_supervisor,
                stdout_path=request.attempt_directory / f"t{index}.stdout",
                stderr_path=request.attempt_directory / f"t{index}.stderr",
            )
            if result.status != "complete":
                diagnosis = _executor_diagnosis(result)
                recorded = {"executor_log": _executor_log(result)} if _executor_log(result) else {}
                vram = vram_failure_report(
                    getattr(result, "stdout", "") or "",
                    getattr(result, "stderr", "") or "",
                )
                _mber_progress(
                    request.attempt_directory,
                    "failed",
                    trajectory=index,
                    error="vram_insufficient" if vram is not None else diagnosis,
                )
                failure = {
                    "profile_id": PROFILE_ID,
                    "gpu_index": request.gpu_index,
                    "diagnosis": diagnosis,
                    "trajectory": index,
                    "trajectories_completed": len(completed),
                    **recorded,
                }
                if vram is not None:
                    return {**vram, **failure}
                return {"status": "launch_failed", "launched": True, **failure}
            completed.append(output_dir)
            accepted_rows = merge_accepted(completed, cohort)
            _mber_progress(
                request.attempt_directory,
                "trajectory_complete",
                trajectory=index,
                trajectories_completed=len(completed),
                accepted_designs=accepted_rows,
            )
            if accepted_target is not None and accepted_rows >= accepted_target:
                break

        delivered = {
            "launched": True,
            "profile_id": PROFILE_ID,
            "attempt_id": request.attempt_id,
            "generator_version": GENERATOR_VERSION,
            # What ran, not what was budgeted: the loop stops early once the
            # displayed design count is met.
            "trajectories": len(completed),
            "trajectory_budget": trajectories,
            "accepted_designs": accepted_rows,
            "scientific_validation": False,
            "runtime_name": RUNTIME_NAME,
            # What the trajectories started from. An unseeded run says so
            # rather than leaving the question open, because the whole point
            # of a campaign series is which of the two this was.
            "seeded_from": None
            if seed is None
            else {
                "design_id": seed.design_id,
                "redesign_spans": [list(span) for span in seed.redesign_spans],
                "parent_round_id": (request.seed_design or {}).get("parent_round_id"),
                "parent_attempt_id": (request.seed_design or {}).get("attempt_id"),
            },
        }
        if accepted_rows == 0:
            # mBER's own filter rejecting every binder is a legitimate outcome
            # and not a failure: 7 of 8 trajectories did exactly this.
            _mber_progress(request.attempt_directory, "no_accepted_design")
            return {**delivered, "status": "no_accepted_design"}
        clinical = clinical_epitope_in_output_frame(cohort, target)
        if clinical is None:
            _mber_progress(
                request.attempt_directory, "rejected", reasons=["clinical_epitope_frame_unverified"]
            )
            return {
                **delivered,
                "status": "outputs_rejected",
                "reasons": ["clinical_epitope_frame_unverified"],
            }
        ranked = rank_mber_output(specification, cohort, clinical)
        _mber_progress(request.attempt_directory, "ranked", accepted_designs=accepted_rows)
        return {
            **delivered,
            "status": "ranked",
            "ranking": ranked,
            "completed_at": _now(),
            **_declared_candidate(ranked.get("accepted_design"), request.attempt_id),
        }
