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
    pdl1_numbering_offset,
)
from cellertz.application.mber_campaign import SETTINGS_FILENAME, rank_mber_output
from cellertz.application.mber_settings import GENERATOR_VERSION, write_mber_vhh_settings
from cellertz.application.vhh_campaign import (
    AcceptanceMetric,
    AcceptancePolicy,
    ArtifactReference,
    ChainMapping,
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


def demonstration_specification(
    trajectories: int, accepted_target: int | None = None
) -> VHHCampaignSpecification:
    """The campaign specification one bounded demonstration run records.

    Every value the settings document needs comes from here, including the
    generator version, which 6C.1 requires and a readiness manifest is not
    allowed to supply. The target is recorded as it arrives: the registered
    file is already chain A in the interface frame, and no preparation step is
    claimed here because this module performs none.
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
    structure = TargetStructureReference(
        source_type="pdb",
        source_identifier="4ZQK",
        target_name="PDL1",
        source_target_chain_ids=("A",),
        source_partner_chain_ids=(),
        prepared_target_chain_ids=("A",),
        chain_mapping=(ChainMapping("A", "A"),),
        preparation_provenance=provenance,
        preparation_status="recorded",
        original_structure=ArtifactReference("pdb:4ZQK", "original_structure", provenance),
        prepared_structure=ArtifactReference(
            "registered-pdl1-target", "prepared_structure", provenance
        ),
    )
    epitope = EpitopeDefinition(
        residues=tuple(
            ResidueReference("A", number, chain_namespace="prepared")
            for number in PDL1_PD1_FOOTPRINT
        ),
        # Curated, not measured here: the 22 residues come from the recorded
        # interface note and are verified against the registered file by
        # residue identity. Calling it a contact derivation would claim a
        # contact calculation and a partner chain this module never ran.
        derivation_method="manually_curated",
        provenance=provenance,
        verification_status="operational_reference",
        source_structure_identifier="4ZQK",
        contact_cutoff_angstrom=None,
        partner_chains=(),
        biological_rationale=(
            "PD-1 interface on PD-L1. All 22 residues match the registered "
            "target file by identity, which is what establishes the frame. "
            "A hotspot list does not steer mBER; this is declared so the "
            "ranking can measure against it afterwards."
        ),
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
        target_id="PDL1",
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
    """One Apptainer invocation. The image's runscript is ``mber-vhh``."""

    return (
        str(apptainer_bin),
        "run",
        "--nv",
        "--env",
        f"CUDA_VISIBLE_DEVICES={gpu_index}",
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


def clinical_epitope_in_output_frame(cohort: Path) -> tuple[frozenset[int], ...] | None:
    """The PD-1 interface, renumbered into the frame the output structures use.

    mBER renumbers the target on output exactly as the BindCraft attempts do,
    so a residue number carried across the two frames without this names a
    different residue. The offset is established by residue identity on a real
    output structure, never assumed; without one, there is no clinical epitope
    to rank against and this returns ``None``.
    """

    for pdb in sorted(cohort.parent.rglob("*_complex.pdb")):
        offset = pdl1_numbering_offset(pdb.read_text(encoding="utf-8"))
        if offset is not None:
            return (frozenset(residue - offset for residue in PDL1_PD1_FOOTPRINT),)
    return None


class MberDemonstrationLauncher:
    """Explicit launch of mBER. Construction does not start Apptainer.

    It exposes ``sif_path``, ``sif_sha256`` and ``profile_id`` because the plan
    service asks the launcher which image it runs rather than importing
    BindCraft's constants.
    """

    profile_id = PROFILE_ID
    sif_path = SIF_PATH
    sif_sha256 = SIF_SHA256

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
        # The screen's design count is what the person asked for, so it is the
        # accepted target rather than a number the run quietly ignores. The
        # trajectory bound still caps the card.
        accepted_target = request.n_designs if type(request.n_designs) is int else None
        specification = demonstration_specification(trajectories, accepted_target)
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
        }
        if accepted_rows == 0:
            # mBER's own filter rejecting every binder is a legitimate outcome
            # and not a failure: 7 of 8 trajectories did exactly this.
            _mber_progress(request.attempt_directory, "no_accepted_design")
            return {**delivered, "status": "no_accepted_design"}
        clinical = clinical_epitope_in_output_frame(cohort)
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
        return {**delivered, "status": "ranked", "ranking": ranked, "completed_at": _now()}
