"""One path from a VHH campaign specification to a ranked mBER cohort.

The pieces existed and nothing joined them, so none of them had ever seen a
real mBER output:

    specification
        |  mber_settings            settings.json
        v
    mber-vhh --settings             (the GPU host runs this; not here)
        |  MBERParser              accepted.csv -> candidates
        v
    screen_structure                the atlas pre-filters, his order
        |
        v
    rank_engager_designs            two axes, worse of the two

This module joins them and runs nothing. ``launch_request`` builds what the
adapter needs; the adapter, not this, executes. ``rank_mber_output`` reads a
finished output directory.

Every design costs one solvent-accessible-surface pass, not two: the occlusion
measurement already yields both the residues the binder covers and the area it
buries, and those two agree with the per-chain calculation to within a square
angstrom.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cellertz.application.attempt_evidence import screen_structure
from cellertz.application.contracts import ApplicationContractError
from cellertz.application.mber_settings import (
    MBER_VHH_COMMAND,
    SeedDesign,
    mber_command,
    write_mber_vhh_settings,
)
from cellertz.application.vhh_campaign import VHHCampaignSpecification
from cellertz.domains.protein_design.adapters.mber import (
    STATS_FILENAME,
    MBERArtifacts,
    MBERParser,
)
from cellertz.domains.protein_design.agents.generation import GenerationRequest
from cellertz.domains.protein_design.scoring.engager_two_axis import (
    ORDERING_I_PTM_GATED,
    EngagerDesign,
    rank_engager_designs,
)
from cellertz.domains.protein_design.scoring.interface_area import occluded_by_partners

SETTINGS_FILENAME = "settings.json"
TARGET_CHAIN = "A"
BINDER_CHAIN = "B"
# The occlusion pass reports the target's own buried area, which is the half of
# the delta that belongs to the target. The composite takes it as a percentile,
# so the halving cannot change an order.
BURIED_AREA_SOURCE = "target-side buried area from the occlusion pass"
# Which design of a ranked cohort a launch declares. The panel keeps a
# diverse set; a round carries one candidate.
ACCEPTED_RULE = "rank 1 among the designs the two-axis panel selected"


def launch_request(
    specification: VHHCampaignSpecification,
    prepared_pdb: str | Path,
    work_dir: str | Path,
    seed_from: SeedDesign | None = None,
    mber_bin: str = MBER_VHH_COMMAND,
) -> GenerationRequest:
    """Write the settings and describe the run the adapter should execute.

    The binary is the caller's: an installation registers it. This never
    takes a command from a request field or from anything a person typed.
    """

    directory = Path(work_dir)
    directory.mkdir(parents=True, exist_ok=True)
    settings_path = write_mber_vhh_settings(
        specification,
        directory,
        prepared_pdb,
        directory / SETTINGS_FILENAME,
        seed_from=seed_from,
    )
    stopping = specification.stopping_policy
    if stopping is None:
        raise ApplicationContractError("incomplete_specification_for_settings")
    return GenerationRequest(
        target_id=specification.target_id,
        objective=f"vhh-campaign:{specification.campaign_id}",
        generator="mber",
        n_candidates=stopping.accepted_design_target,
        constraints={
            "mber_command": mber_command(settings_path, mber_bin),
            "mber_version": specification.generator_protocol.generator_version
            if specification.generator_protocol is not None
            else None,
        },
    )


def rank_mber_output(
    specification: VHHCampaignSpecification,
    output_dir: str | Path,
    clinical_epitopes: tuple[frozenset[int], ...] | list[frozenset[int]],
    ligand_residues: object = (),
    occlusion_limit: int | None = None,
    select: int | None = None,
    ordering: str = ORDERING_I_PTM_GATED,
) -> dict[str, Any]:
    """Screen a finished mBER cohort, then rank what survives.

    ``ordering`` picks between the atlas's two: i_pTM gated, as its BCMA panel
    ranks, or epitope-first with i_pTM as a tie-break, as its GPC3 panel does
    because i_pTM anti-correlates with coverage there. It is a declared choice
    per target, never inferred from the cohort.

    His order, not ours: the pre-filters run first and a rejected design never
    reaches the ranking. A design whose structure is missing is reported by
    name rather than dropped, because a silent disappearance from a cohort
    changes every percentile in it.
    """

    directory = Path(output_dir)
    accepted = directory / STATS_FILENAME
    if not accepted.is_file():
        raise ApplicationContractError("mber_output_has_no_accepted_csv")
    scaffold = specification.scaffold
    if scaffold is None or not scaffold.cdr_regions:
        raise ApplicationContractError("scaffold_declares_no_cdr_region")
    cdr_spans = tuple((span.start, span.end) for span in scaffold.cdr_regions)

    candidates = _parse(specification, accepted, directory)
    screened: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unmeasurable: list[dict[str, Any]] = []
    designs: list[EngagerDesign] = []
    # Keyed by the id the panel ranks under, because the panel carries neither
    # the sequence nor the design name and an acceptance needs both.
    identities: dict[str, dict[str, str]] = {}
    for candidate in candidates:
        metadata = candidate.generator_metadata
        structure = _structure_path(metadata, directory)
        if structure is None:
            unmeasurable.append(
                {"design_id": candidate.candidate_id, "reason": "complex_structure_not_found"}
            )
            continue
        occlusion = occluded_by_partners(structure, TARGET_CHAIN, (BINDER_CHAIN,))
        screen = screen_structure(
            structure.read_text(encoding="utf-8"),
            occlusion["occluded_residues"],
            ligand_residues=ligand_residues,
            occlusion_limit=occlusion_limit,
            binder_chain=BINDER_CHAIN,
            reached_definition="surface shielded by the binder",
        )
        if screen["sequence"] != candidate.sequence.upper():
            # accepted.csv names the sequence; the screen reads it off the
            # structure. If the two disagree, the charge and the liabilities
            # describe a different molecule from the one being ranked.
            unmeasurable.append(
                {
                    "design_id": candidate.candidate_id,
                    "reason": "sequence_does_not_match_structure",
                    "in_csv": candidate.sequence,
                    "in_structure": screen["sequence"],
                }
            )
            continue
        row = {
            "design_id": candidate.candidate_id,
            "trajectory": str(metadata.get("trajectory_name") or ""),
            "screen": screen,
            "buried_area_angstrom2": occlusion["total_occluded_area_angstrom2"],
            "buried_area_source": BURIED_AREA_SOURCE,
            "epitope": occlusion["occluded_residues"],
        }
        if screen["rejected_by"] is not None:
            rejected.append({**row, "rejected_by": screen["rejected_by"]})
            continue
        screened.append(row)
        i_ptm = _number(metadata, "i_ptm")
        if i_ptm is None:
            unmeasurable.append(
                {"design_id": candidate.candidate_id, "reason": "i_ptm_not_recorded"}
            )
            screened.pop()
            continue
        identities[candidate.candidate_id] = {
            "design": str(metadata.get("design") or candidate.candidate_id),
            "sequence": candidate.sequence,
            "target_id": candidate.target_id,
        }
        designs.append(
            EngagerDesign(
                design_id=candidate.candidate_id,
                trajectory=row["trajectory"] or candidate.candidate_id,
                sequence=candidate.sequence,
                i_ptm=i_ptm,
                epitope=frozenset(occlusion["occluded_residues"]),
                buried_area=occlusion["total_occluded_area_angstrom2"],
                net_charge=screen["net_charge"],
            )
        )

    ranking = (
        rank_engager_designs(
            designs, clinical_epitopes, cdr_spans, select=select, ordering=ordering
        ).to_dict()
        if designs
        else {"designs": [], "status": "no_design_survived_the_screen"}
    )
    return {
        "campaign_id": specification.campaign_id,
        "generator_version": (
            specification.generator_protocol.generator_version
            if specification.generator_protocol is not None
            else None
        ),
        "generated": len(candidates),
        "screened_in": len(screened),
        "rejected_by_screen": rejected,
        "not_measurable": unmeasurable,
        "ranking": ranking,
        "accepted_design": _accepted_design(ranking, identities),
    }


def _accepted_design(
    ranking: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Which one design of the cohort a launch may declare as its candidate.

    The panel is a set, not a winner: with no ``select`` the diverse selection
    keeps every design that is neither a trajectory repeat nor a near
    duplicate. A journey round carries one candidate, so the declaration is
    **rank 1 among the selected** — the panel's own ordering, not a new one.

    Being in the panel is itself the acceptance evidence. A design only reaches
    it after its structure was found, its sequence was read off that structure
    and matched ``accepted.csv``, the atlas's pre-filters passed, and i_pTM was
    recorded and cleared the gate. Nothing here re-decides any of that; it
    reports which checks the design is standing on.
    """

    rows = ranking.get("designs")
    selected = [
        row
        for row in (rows if isinstance(rows, list) else ())
        if isinstance(row, dict) and row.get("selected") is True
    ]
    if not selected:
        return _acceptance_refused(["ranking_selected_no_design"])
    best = min(selected, key=lambda row: row.get("rank") or 0)
    design_id = best.get("design_id")
    identity = identities.get(design_id) if isinstance(design_id, str) else None
    if identity is None:
        # The panel ranked an id the cohort never produced, so the sequence
        # behind it is unknown and no candidate can be declared from it.
        return _acceptance_refused([f"panel_row_has_no_cohort_identity:{design_id}"])
    return {
        "accepted": True,
        "reasons": [],
        "candidate_id": design_id,
        # execution_id is the launch's to fill: this function does not know
        # which attempt is reading the output directory.
        "candidate_identity": {
            "candidate_id": design_id,
            "sequence": identity["sequence"],
            "target_id": identity["target_id"],
            "design": identity["design"],
        },
        "summary": {
            "accepted": True,
            "design": identity["design"],
            "candidate_id": design_id,
            "rule": ACCEPTED_RULE,
            "protocol_id": ranking.get("protocol_id"),
            "ordering": ranking.get("ordering"),
            "rank": best.get("rank"),
            "score": best.get("score"),
            "i_ptm": best.get("i_ptm"),
            "on_pareto_front": best.get("on_pareto_front"),
            "trajectory": best.get("trajectory"),
            "ranked_cohort": len(rows) if isinstance(rows, list) else 0,
            "selected_in_panel": len(selected),
            "scientific_validation_established": False,
        },
    }


def _acceptance_refused(reasons: list[str]) -> dict[str, Any]:
    return {
        "accepted": False,
        "reasons": reasons,
        "summary": {
            "accepted": False,
            "reasons": reasons,
            "rule": ACCEPTED_RULE,
            "scientific_validation_established": False,
        },
    }


def _parse(
    specification: VHHCampaignSpecification,
    accepted: Path,
    directory: Path,
) -> list[Any]:
    """Every accepted row, through the adapter's own parser.

    The row count is read first because the parser caps its result at the
    requested candidate count, and a cohort silently cut short would change
    every percentile computed from it.
    """

    with accepted.open(encoding="utf-8", newline="") as handle:
        rows = sum(1 for _ in csv.DictReader(handle))
    if rows == 0:
        return []
    request = GenerationRequest(
        target_id=specification.target_id,
        objective=f"vhh-campaign:{specification.campaign_id}",
        generator="mber",
        n_candidates=rows,
    )
    artifacts = MBERArtifacts(
        output_dir=directory,
        accepted_csv=accepted,
        command=(MBER_VHH_COMMAND,),
        mber_version=(
            specification.generator_protocol.generator_version
            if specification.generator_protocol is not None
            else None
        ),
    )
    return MBERParser().parse(request, artifacts)


def _structure_path(metadata: dict[str, object], directory: Path) -> Path | None:
    """The accepted complex, preferring the relaxed structure when there is one."""

    for key in ("relaxed_pdb_path", "complex_pdb_path"):
        recorded = metadata.get(key)
        if not isinstance(recorded, str) or not recorded.strip():
            continue
        candidate = Path(recorded)
        for path in (candidate, directory / candidate.name, directory / "Accepted" / candidate.name):
            if path.is_file():
                return path
    return None


def _number(metadata: dict[str, object], name: str) -> float | None:
    native = metadata.get("native_metrics")
    if not isinstance(native, dict):
        return None
    try:
        return float(str(native.get(name, "")).strip())
    except ValueError:
        return None
