"""Deterministic evidence for verified manual attempts.

Numbers come from the checked review record and from the PDB bytes.
A language model does not calculate them.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTACT_ANGSTROM = 5.0

_SPECIALIST = {
    "list_verified_attempts": ("ranking", "Lists verified attempts. It does not rank them."),
    "get_attempt_evidence": ("ranking", "Reads the verified selection rule and provenance."),
    "compare_model_metrics": ("scoring", "Returns recorded metric cells. It does not score them."),
    "get_geometric_contacts": ("critique", "Counts heavy-atom contacts from the PDB bytes."),
    "link_structural_review": ("critique", "Returns the structural review links for the verified PDBs."),
    "propose_generation_plan": ("generation", "Drafts a plan. It does not launch or approve execution."),
    "propose_refinement": ("generation", "Drafts a new plan revision. It does not start a run."),
}


@dataclass(frozen=True)
class AttemptFiles:
    review_json: Path
    pdb_dir: Path


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[3] / "apps" / "protein-design-workspace"


def default_attempt_files() -> tuple[AttemptFiles, ...]:
    root = workspace_root()
    files = [
        AttemptFiles(
            review_json=root / "src" / "manual-attempt-2LUTUOX4.json",
            pdb_dir=root / "public" / "manual-review" / "attempt.2LUTUOX4",
        )
    ]
    extra = os.environ.get("CELLERTZ_ATTEMPT_CATALOG", "").strip()
    if extra:
        catalog = Path(extra)
        if catalog.is_dir():
            for review_json in sorted(catalog.glob("*/review.json")):
                files.append(AttemptFiles(review_json=review_json, pdb_dir=review_json.parent))
    return tuple(files)


def merge_demonstration_attempts(catalog: dict[str, dict[str, Any]]) -> None:
    """Add attempts written after the coordinator started. Existing records stay."""

    extra = os.environ.get("CELLERTZ_ATTEMPT_CATALOG", "").strip()
    if not extra:
        return
    root = Path(extra)
    if not root.is_dir():
        return
    files = tuple(
        AttemptFiles(review_json=path, pdb_dir=path.parent)
        for path in sorted(root.glob("*/review.json"))
    )
    if files:
        catalog.update(load_catalog(files))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_catalog(files: tuple[AttemptFiles, ...] | None = None) -> dict[str, dict[str, Any]]:
    """Load review records and check PDB hashes. A mismatch is recorded, not replaced."""

    catalog: dict[str, dict[str, Any]] = {}
    for item in files if files is not None else default_attempt_files():
        record = json.loads(item.review_json.read_text(encoding="utf-8"))
        attempt_id = str(record["attempt_id"])
        models: dict[str, Any] = {}
        verified = True
        for model_id, model in record["models"].items():
            pdb_path = item.pdb_dir / f"{model_id}.pdb"
            expected = str(model["sha256"])
            if not pdb_path.is_file():
                actual = None
                verified = False
            else:
                actual = sha256_file(pdb_path)
                if actual != expected:
                    verified = False
            models[model_id] = {
                "record": model,
                "pdb_path": pdb_path,
                "expected_sha256": expected,
                "actual_sha256": actual,
            }
        catalog[attempt_id] = {"record": record, "models": models, "pdb_verified": verified}
    return catalog


def specialist_for(tool: str) -> tuple[str, str]:
    return _SPECIALIST[tool]


def list_attempts(catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    attempts = []
    for attempt_id, item in catalog.items():
        record = item["record"]
        attempts.append(
            {
                "attempt_id": attempt_id,
                "design": record["design"],
                "filter_policy": record["filter_policy"],
                "scientific_evaluation": record["scientific_evaluation"],
                "pdb_verified": item["pdb_verified"],
            }
        )
    return {"attempts": attempts}


def resolve_attempt_id(catalog: dict[str, dict[str, Any]], requested: str) -> dict[str, Any]:
    """Resolve one supplied identifier. An unknown id never selects another attempt."""

    known = list(catalog)
    requested_id = requested.strip()
    found = _identifier_payload(requested_id, known)
    if requested_id in catalog:
        found["status"] = "ok"
        found["resolved_attempt_id"] = requested_id
        found["matching_attempt_ids"] = [requested_id]
        found["item"] = catalog[requested_id]
        return found
    matches = [attempt_id for attempt_id in known if attempt_id.endswith("." + requested_id)]
    found["matching_attempt_ids"] = matches
    if len(matches) == 1:
        found["status"] = "ok"
        found["resolved_attempt_id"] = matches[0]
        found["item"] = catalog[matches[0]]
        return found
    if len(matches) > 1:
        shown = ", ".join(matches)
        found["status"] = "ambiguous"
        found["error"] = (
            f"Attempt identifier '{requested_id}' matches more than one registered id: {shown}. "
            "Ask which attempt to use. This rejection does not mean the attempt's evidence is unavailable."
        )
        found["question"] = "Which verified attempt should I analyze?"
        return found
    shown = ", ".join(known) if known else "(none)"
    found["status"] = "unknown_attempt"
    found["error"] = (
        f"Unknown attempt identifier '{requested_id}'. Available identifiers: {shown}. "
        "This rejection does not mean the attempt's evidence is unavailable."
    )
    return found


def resolve_attempt(
    catalog: dict[str, dict[str, Any]],
    attempt_id: str | None,
    selected: str | None,
) -> dict[str, Any]:
    """Resolve a supplied id, otherwise the conversation's canonical id or the only registered id."""

    if isinstance(attempt_id, str) and attempt_id.strip():
        return resolve_attempt_id(catalog, attempt_id)
    known = list(catalog)
    chosen = selected if isinstance(selected, str) and selected in catalog else None
    if chosen is None and len(catalog) == 1:
        chosen = next(iter(catalog))
    if chosen is not None:
        found = _identifier_payload(None, known)
        found["status"] = "ok"
        found["resolved_attempt_id"] = chosen
        found["matching_attempt_ids"] = [chosen]
        found["item"] = catalog[chosen]
        return found
    found = _identifier_payload(None, known)
    found["status"] = "ambiguous"
    found["matching_attempt_ids"] = known
    found["error"] = (
        "More than one verified attempt is registered. Ask which attempt to use. "
        "This rejection does not mean the attempt's evidence is unavailable."
    )
    found["question"] = "Which verified attempt should I analyze?"
    found["attempts"] = list_attempts(catalog)["attempts"]
    return found


def _identifier_payload(requested_id: str | None, known: list[str]) -> dict[str, Any]:
    return {
        "requested_id": requested_id,
        "resolved_attempt_id": None,
        "available_attempt_ids": known,
        "matching_attempt_ids": [],
    }


def evidence_payload(attempt_id: str, item: dict[str, Any]) -> dict[str, Any]:
    record = item["record"]
    representative = record["representative"]
    constraints = _execution_constraints(record)
    return {
        "attempt_id": attempt_id,
        "workflow_origin": "historical_manual_attempt",
        "launched_by_conversation": False,
        "pdb_verified": item["pdb_verified"],
        "design": record["design"],
        "filter_policy": record["filter_policy"],
        "scientific_evaluation": record["scientific_evaluation"],
        "platform_scientific_execution": record["platform_scientific_execution"],
        "platform_ipsae_ranking": record["platform_ipsae_ranking"],
        "experimental_validation": record["experimental_validation"],
        "ranking_score": "not_recorded",
        "representative": representative,
        "selection_is_not_interface_assessment": True,
        "catalog_size_does_not_explain_selection": True,
        "interface_tools_not_included_here": ["compare_model_metrics", "get_geometric_contacts"],
        "interface_residues_note": (
            "interface_residues is one review field. An empty_field here is not the metric "
            "n_InterfaceResidues and does not mean interface metrics are absent. "
            "Call compare_model_metrics for recorded cells."
        ),
        "chains": record["chains"],
        "interface_residues": record["interface_residues"],
        "blank_model2_metrics": record["blank_model2_metrics"],
        "blank_model2_note": record["blank_model2_note"],
        "absent_models": record["absent_models"],
        "provenance": record["provenance"],
        "execution_constraints": constraints,
        "execution_constraints_reported": constraints is not None,
        "absence_of_execution_constraints_is_not_compliance": True,
        "artifact_verification": {"pdb_verified": item["pdb_verified"]},
        "execution_compliance_established": False,
        "scientific_validation_established": record.get("experimental_validation") is True,
        "models": {
            model_id: {
                "sha256": model["expected_sha256"],
                "actual_sha256": model["actual_sha256"],
                "hash_matches": model["actual_sha256"] == model["expected_sha256"],
                "representative": model["record"]["representative"],
                "pdb_url": model["record"]["pdb_url"],
            }
            for model_id, model in item["models"].items()
        },
    }


def _execution_constraints(record: dict[str, Any]) -> dict[str, Any] | None:
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return None
    constraints = provenance.get("execution_constraints")
    if not isinstance(constraints, dict):
        return None
    return constraints


def metric_identifiers(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for row in item["record"]["metrics"]:
        name = row.get("metric")
        if isinstance(name, str) and name not in names:
            names.append(name)
    return names


def compare_metrics(item: dict[str, Any], names: object = None) -> dict[str, Any]:
    """Return recorded cells. Unknown names are an error, not missing evidence."""

    available = metric_identifiers(item)
    known = set(available)
    rejected: list[str] = []
    if names is None:
        selected = available
    elif not isinstance(names, list):
        rejected = ["metrics"]
        selected = []
    else:
        selected = []
        for name in names:
            if not isinstance(name, str) or name not in known:
                rejected.append(name if isinstance(name, str) else "non_string_metric")
            elif name not in selected:
                selected.append(name)
    rows = [
        {
            "metric": row["metric"],
            "model1": row["model1"],
            "model2": row["model2"],
            "average": row["average"],
        }
        for row in item["record"]["metrics"]
        if row.get("metric") in set(selected)
    ]
    status = "unsupported_metrics" if rejected else "ok"
    error = None
    if rejected:
        shown = ", ".join(str(name) for name in rejected)
        error = (
            f"Unsupported metric names: {shown}. "
            "Use one of valid_metrics. This rejection does not mean the measurement is absent, "
            "empty, or zero."
        )
    return {
        "status": status,
        "attempt_id": item["record"].get("attempt_id"),
        "pdb_verified": item["pdb_verified"],
        "requested_metrics": names,
        "rejected_metrics": rejected,
        "valid_metrics": available,
        "error": error,
        "absence_established": False,
        "value_kinds": {
            "recorded": "a recorded non-zero value",
            "recorded_zero": "a recorded zero, distinct from an empty field",
            "empty_field": "the cell is empty, not the string 0",
            "absent": "the model was not recorded",
        },
        "artifacts": [
            {
                "model_id": model_id,
                "sha256": model["expected_sha256"],
                "pdb_url": model["record"]["pdb_url"],
            }
            for model_id, model in item["models"].items()
        ],
        "metrics": rows,
    }


def link_review(attempt_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "review_url": "/?area=manual",
        "pdb_verified": item["pdb_verified"],
        "models": {
            model_id: {
                "pdb_url": model["record"]["pdb_url"],
                "sha256": model["expected_sha256"],
                "representative": model["record"]["representative"],
            }
            for model_id, model in item["models"].items()
        },
    }


def propose_plan(attempt_id: str | None, request_text: str) -> dict[str, Any]:
    return {
        "status": "proposed",
        "approved": False,
        "execution_connected": False,
        "platform_scientific_execution": "disabled",
        "based_on_attempt": attempt_id,
        "requested": request_text,
        "steps_for_review": [
            "Confirm the target, hotspot, length, and seed policy with the professor.",
            "Review this plan. It is not an approval.",
            "Only a separate authorized execution path could start a generator.",
        ],
        "will_not": [
            "launch BindCraft",
            "mark this plan approved",
            "assign ipSAE, ranking_score, affinity, or validation",
        ],
    }


def geometric_contacts(item: dict[str, Any]) -> dict[str, Any]:
    """Count residue pairs whose closest heavy atoms are at most 5 Å."""

    if not item["pdb_verified"]:
        return {
            "status": "hash_mismatch",
            "pdb_verified": False,
            "contacts": None,
            "detail": "A served PDB does not match the verified hash, so contacts were not calculated from it.",
        }
    results = {}
    for model_id, model in item["models"].items():
        calculated = _contacts_for_pdb(model["pdb_path"])
        recorded = model["record"]["contacts"]
        results[model_id] = {
            "sha256": model["expected_sha256"],
            "cutoff_angstrom": CONTACT_ANGSTROM,
            "atoms": "non-hydrogen",
            "calculated": calculated,
            "recorded_in_review": {
                "binder_residues_in_contact": recorded["binder_residues_in_contact"],
                "target_residues_in_contact": recorded["target_residues_in_contact"],
                "residue_pairs": recorded["residue_pairs"],
                "minimum_heavy_atom_distance_angstrom": recorded["minimum_heavy_atom_distance_angstrom"],
            },
            "matches_recorded_counts": (
                calculated["binder_residues_in_contact"] == recorded["binder_residues_in_contact"]
                and calculated["target_residues_in_contact"] == recorded["target_residues_in_contact"]
                and calculated["residue_pairs"] == recorded["residue_pairs"]
            ),
        }
    return {
        "status": "calculated",
        "pdb_verified": True,
        "independent_of_bindcraft": True,
        "not_bindcraft_metrics": [
            "InterfaceResidues",
            "n_InterfaceResidues",
            "dSASA",
            "ShapeComplementarity",
        ],
        "models": results,
    }


def audit_hotspot_blocks(blocks: list[dict[str, Any]], item: dict[str, Any]) -> dict[str, Any]:
    """How much of each declared block the designs actually reached.

    The atlas records this per block as contact_n and contact_pct, measured
    after the run. A block is a claim until this is computed, and the professor's
    campaigns turned on exactly this number: a declared block the designs never
    touched is a specification the optimiser ignored, not a binding site.
    """

    contacts = geometric_contacts(item)
    if contacts["status"] != "calculated":
        return {
            "status": contacts["status"],
            "detail": contacts.get("detail", ""),
            "blocks": [],
        }
    reached: dict[str, set[int]] = {
        model_id: set(model["calculated"]["target_residues"])
        for model_id, model in contacts["models"].items()
    }
    audited = []
    for block in blocks:
        numbers = []
        for label in block.get("residues") or []:
            match = _RESIDUE_NUMBER.match(str(label))
            if match is not None:
                numbers.append(int(match.group(2)))
        per_model = {
            model_id: sorted(number for number in numbers if number in hit)
            for model_id, hit in reached.items()
        }
        best = max(per_model.values(), key=len, default=[])
        audited.append(
            {
                "id": block.get("id"),
                "declared": len(numbers),
                # The labels too, so a drawing colours what was declared rather
                # than guessing the chain from the count.
                "declared_residues": [
                    label for label in (block.get("residues") or []) if _RESIDUE_NUMBER.match(str(label))
                ],
                "contact_n": len(best),
                "contact_pct": round(100 * len(best) / len(numbers)) if numbers else 0,
                "contacted_residues": best,
                "per_model": per_model,
            }
        )
    return {
        "status": "calculated",
        "cutoff_angstrom": CONTACT_ANGSTROM,
        "declared_but_not_reached_is_not_a_binding_site": True,
        # Which numbering the comparison happened in. A block declared against
        # the registered target file (chain A, 18 to 132) and measured on a
        # structure numbered from 1 would name a residue 17 positions away.
        "numbering": _numbering_note(item),
        "blocks": audited,
    }


def _numbering_note(item: dict[str, Any]) -> dict[str, Any]:
    """The structure's own chain A frame, and its offset from the declared one."""

    frames = set()
    offsets = set()
    for model in item["models"].values():
        try:
            pdb = model["pdb_path"].read_text(encoding="utf-8")
        except OSError:
            return {"status": "structure_unreadable"}
        numbers = sorted({resseq for chain, resseq, _, _, _ in _heavy_atoms(pdb) if chain == "A"})
        if not numbers:
            return {"status": "no_target_chain"}
        frames.add((numbers[0], numbers[-1], len(numbers)))
        offsets.add(pdl1_numbering_offset(pdb))
    if len(frames) != 1:
        return {"status": "models_disagree_on_numbering"}
    first, last, count = frames.pop()
    offset = offsets.pop() if len(offsets) == 1 else None
    note: dict[str, Any] = {
        "status": "read_from_structure",
        "structure_chain_a": {"first": first, "last": last, "residues": count},
        "declared_target_frame": {
            "first": PDL1_DECLARED_FRAME[0],
            "last": PDL1_DECLARED_FRAME[1],
            "residues": PDL1_DECLARED_FRAME[2],
        },
        "block_residues_compared_in": "the structure's own numbering",
    }
    if offset:
        note["offset_from_declared_frame"] = offset
        note["warning"] = (
            f"This structure is numbered {offset} positions below the registered target "
            "file. A hotspot block declared in that file's numbering must be shifted by "
            "the same amount before it is compared, or it names another residue."
        )
    return note


_RESIDUE_NUMBER = re.compile(r"^([A-Za-z])(\d{1,6})$")

# The atlas pre-filter thresholds, from the professor's BCMA campaign: 11,430
# accepted designs -> 7,540 on liabilities -> 842 on occlusion -> 754 on charge.
# They are his numbers, not ours.
THRESHOLDS_FROM = "engager_target_atlas_bcma_campaign"
# PD-L1 residues that PD-1 contacts within 5 A in PDB 4ZQK, from the vault's
# pdl1_pd1_interface_4zqk note. That note verifies the numbering against the
# registered target file: chain A, residues 18 to 132, all 115 positions
# agreeing in number and identity. The frame is checked below before these
# numbers are used, because a footprint read into another numbering frame
# would name the wrong residues.
PDL1_PD1_FOOTPRINT = (
    18, 19, 20, 23, 26, 54, 56, 58, 63, 66, 76,
    113, 115, 117, 119, 120, 121, 122, 123, 124, 125, 126,
)
PDL1_PD1_FOOTPRINT_SOURCE = "pdl1_pd1_interface_4zqk"
# The same note's residue identities. They are what establishes that a
# renumbered structure is the same protein and not another one.
PDL1_PD1_FOOTPRINT_IDENTITIES = {
    18: "ALA", 19: "PHE", 20: "THR", 23: "VAL", 26: "ASP", 54: "ILE",
    56: "TYR", 58: "GLU", 63: "ASN", 66: "GLN", 76: "VAL", 113: "ARG",
    115: "MET", 117: "SER", 119: "GLY", 120: "GLY", 121: "ALA", 122: "ASP",
    123: "TYR", 124: "LYS", 125: "ARG", 126: "ILE",
}
# Chain A of the registered target file, from the same note.
PDL1_DECLARED_FRAME = (18, 132, 115)
NET_CHARGE_LIMIT = 2
OCCLUSION_LIMIT = 8
# S-S is ~2.05 A; beyond this the two thiols are not bonded.
DISULFIDE_ANGSTROM = 2.5
FILTER_ORDER = ("unpaired_cysteine", "glycosylation_sequon", "occlusion_footprint", "net_charge")
_THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}


def screen_developability(
    item: dict[str, Any],
    ligand_residues: object = (),
    occlusion_limit: int | None = None,
) -> dict[str, Any]:
    """The atlas pre-filters, measured from the verified PDB.

    The professor screens before he ranks, and the order is his: sequence
    liabilities first, then how much of the natural ligand's footprint the
    design occludes, then net charge. A rejected design is named by the filter
    that rejected it, so the screen can be argued with.

    The sequence and the disulfide pairing are read from the structure, never
    from a review field. An unverified PDB refuses the screen.

    Occlusion of the natural ligand's footprint is **measured whenever the
    footprint is declared, and rejects only when a limit is given**. Its
    direction belongs to the campaign, not to the screen: the atlas's limit of
    8 of 18 comes from a BCMA engager that had to leave APRIL's site alone,
    while a PD-L1 binder that covers PD-1's site is doing the job. A screen
    that rejected on occlusion by default would throw away the designs a
    blockade campaign wants.
    """

    if not item["pdb_verified"]:
        return {
            "status": "hash_mismatch",
            "detail": "A served PDB does not match the verified hash, so the screen was not run on it.",
            "models": {},
        }
    ligand = sorted({int(number) for number in ligand_residues or ()})
    contacts = geometric_contacts(item)
    models: dict[str, Any] = {}
    for model_id, model in item["models"].items():
        # Chain B, the same binder chain ``_contacts_for_pdb`` reads.
        residues, thiols = _binder_chain(model["pdb_path"].read_text(encoding="utf-8"), "B")
        sequence = "".join(name for _, name in residues)
        cysteines = _cysteine_pairing(residues, thiols)
        sequons = _glycosylation_sequons(residues)
        charge = sum(sequence.count(name) for name in "KR") - sum(sequence.count(name) for name in "DE")
        reached = set(contacts["models"][model_id]["calculated"]["target_residues"])
        occluded = [number for number in ligand if number in reached]
        rejected = None
        if cysteines["unpaired"]:
            rejected = "unpaired_cysteine"
        elif sequons:
            rejected = "glycosylation_sequon"
        elif occlusion_limit is not None and ligand and len(occluded) > occlusion_limit:
            rejected = "occlusion_footprint"
        elif charge > NET_CHARGE_LIMIT:
            rejected = "net_charge"
        models[model_id] = {
            "binder_chain": "B",
            "sequence": sequence,
            "length": len(sequence),
            "cysteines": cysteines,
            "sequons": sequons,
            "net_charge": charge,
            "net_charge_limit": NET_CHARGE_LIMIT,
            "net_charge_rule": "K+R minus D+E, at neutral pH; histidine is not counted",
            "occlusion": (
                {"status": "not_declared"}
                if not ligand
                else {
                    "status": "measured",
                    "ligand_residues": len(ligand),
                    "footprint": len(occluded),
                    "occluded_residues": occluded,
                    "limit": occlusion_limit,
                    # Whether covering the ligand's site is the goal or the
                    # fault is the campaign's call, so a declared footprint
                    # without a declared limit is a number, not a gate.
                    "is_a_gate": occlusion_limit is not None,
                }
            ),
            "rejected_by": rejected,
            "verdict": "rejected" if rejected else "passed_measured_filters",
            "unscreened": [] if ligand else ["occlusion_footprint"],
        }
    return {
        "status": "calculated",
        "thresholds_from": THRESHOLDS_FROM,
        "filter_order": list(FILTER_ORDER),
        "models": models,
    }


def _binder_chain(
    pdb: str,
    chain: str,
) -> tuple[list[tuple[int, str]], dict[int, tuple[float, float, float]]]:
    """The chain's residues in order, and the coordinate of each cysteine thiol."""

    residues: dict[int, str] = {}
    thiols: dict[int, tuple[float, float, float]] = {}
    for line in pdb.splitlines():
        if not line.startswith("ATOM") or line[21:22].strip() != chain:
            continue
        name = _THREE_TO_ONE.get(line[17:20].strip().upper())
        if name is None:
            continue
        try:
            resseq = int(line[22:26])
        except ValueError:
            continue
        residues[resseq] = name
        if name == "C" and line[12:16].strip() == "SG":
            try:
                thiols[resseq] = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            except ValueError:
                continue
    return sorted(residues.items()), thiols


def _cysteine_pairing(
    residues: list[tuple[int, str]],
    thiols: dict[int, tuple[float, float, float]],
) -> dict[str, Any]:
    """Which cysteines hold a disulfide. A free thiol is the liability.

    A cysteine whose SG atom the file does not carry cannot be shown to be
    paired, so it is reported as unpaired and named: a screen that passed an
    unjudged liability would be worse than one that over-flags it.
    """

    cysteines = [number for number, name in residues if name == "C"]
    paired: set[int] = set()
    for first in thiols:
        for second in thiols:
            if first < second and math.dist(thiols[first], thiols[second]) <= DISULFIDE_ANGSTROM:
                paired.update((first, second))
    return {
        "cysteines": cysteines,
        "unpaired": [number for number in cysteines if number not in paired],
        "without_sg_atom": [number for number in cysteines if number not in thiols],
        "disulfide_angstrom": DISULFIDE_ANGSTROM,
    }


def _glycosylation_sequons(residues: list[tuple[int, str]]) -> list[str]:
    """N-X-S/T with X not proline, over residues numbered consecutively.

    The triplet must be consecutive in the structure: read across a chain
    break, three residues that are not neighbours would be a false sequon.
    """

    by_number = dict(residues)
    found = []
    for number, name in residues:
        if name != "N":
            continue
        middle = by_number.get(number + 1)
        last = by_number.get(number + 2)
        if middle is None or middle == "P" or last not in {"S", "T"}:
            continue
        found.append(f"N{number}-{middle}-{last}{number + 2}")
    return found


def _contacts_for_pdb(path: Path) -> dict[str, Any]:
    target: list[tuple[int, float, float, float]] = []
    binder: list[tuple[int, float, float, float]] = []
    for chain, resseq, x, y, z in _heavy_atoms(path.read_text(encoding="utf-8")):
        if chain == "A":
            target.append((resseq, x, y, z))
        elif chain == "B":
            binder.append((resseq, x, y, z))
    pairs: set[tuple[int, int]] = set()
    target_hit: set[int] = set()
    binder_hit: set[int] = set()
    minimum = None
    for residue_a, ax, ay, az in target:
        for residue_b, bx, by, bz in binder:
            distance = math.dist((ax, ay, az), (bx, by, bz))
            if minimum is None or distance < minimum:
                minimum = distance
            if distance <= CONTACT_ANGSTROM:
                pairs.add((residue_a, residue_b))
                target_hit.add(residue_a)
                binder_hit.add(residue_b)
    return {
        "binder_residues_in_contact": len(binder_hit),
        "target_residues_in_contact": len(target_hit),
        # The identities, not only the count: a declared hotspot block can only
        # be audited against the residues the binder actually reached.
        "target_residues": sorted(target_hit),
        "residue_pairs": len(pairs),
        "minimum_heavy_atom_distance_angstrom": None if minimum is None else round(minimum, 3),
        "target_heavy_atoms": len(target),
        "binder_heavy_atoms": len(binder),
    }


def _heavy_atoms(pdb: str) -> list[tuple[str, int, float, float, float]]:
    atoms: list[tuple[str, int, float, float, float]] = []
    seen: set[tuple[str, int, str, str]] = set()
    for line in pdb.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        name = line[12:16].strip()
        element = line[76:78].strip() if len(line) >= 78 else ""
        if (element or name).upper().startswith("H"):
            continue
        chain = line[21:22].strip()
        try:
            resseq = int(line[22:26])
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except ValueError:
            continue
        key = (chain, resseq, line[26:27], name)
        if key in seen:
            continue
        seen.add(key)
        atoms.append((chain, resseq, x, y, z))
    return atoms


def pdl1_numbering_offset(pdb: str) -> int | None:
    """How far this structure's chain A is shifted from the verified frame.

    The vault note records the identity of all 22 interface residues in the
    registered target file's frame. An offset is accepted only when **every**
    one of them matches by identity, which is what distinguishes a renumbered
    copy of the same protein from a different protein.

    The attempt structures on record are numbered 1 to 115 where the
    registered target file is numbered 18 to 132: the same residues, shifted
    by 17. Comparing a residue number across those two frames without this
    offset names a different residue.
    """

    identities: dict[int, str] = {}
    for line in pdb.splitlines():
        if line.startswith("ATOM") and line[21:22].strip() == "A":
            try:
                identities[int(line[22:26])] = line[17:20].strip().upper()
            except ValueError:
                continue
    if not identities:
        return None
    lowest = min(identities)
    for offset in {verified - lowest for verified in PDL1_PD1_FOOTPRINT}:
        if all(
            identities.get(verified - offset) == PDL1_PD1_FOOTPRINT_IDENTITIES[verified]
            for verified in PDL1_PD1_FOOTPRINT
        ):
            return offset
    return None


def native_ligand_footprint(item: dict[str, Any]) -> dict[str, Any]:
    """The natural ligand's footprint, in this structure's own numbering.

    Only PD-L1 has one recorded. The footprint is declared in the registered
    target file's frame, and the attempt structures are renumbered, so it is
    translated by an offset established from residue identity rather than
    assumed. An unestablished offset refuses the footprint: naming the wrong
    residues would be worse than naming none.
    """

    if not item["pdb_verified"]:
        return {"status": "structure_not_verified", "residues": ()}
    offsets = set()
    for model in item["models"].values():
        offsets.add(pdl1_numbering_offset(model["pdb_path"].read_text(encoding="utf-8")))
    if len(offsets) != 1 or None in offsets:
        return {
            "status": "numbering_frame_does_not_match_any_recorded_footprint",
            "residues": (),
        }
    offset = offsets.pop()
    return {
        "status": "declared",
        "target": "PDL1",
        "ligand": "PD-1",
        "residues": tuple(number - offset for number in PDL1_PD1_FOOTPRINT),
        "residues_in_declared_frame": PDL1_PD1_FOOTPRINT,
        "offset_from_declared_frame": offset,
        "offset_established_by": "identity of all 22 recorded interface residues",
        "source": PDL1_PD1_FOOTPRINT_SOURCE,
        # The note's own words: a contact in a deposited complex is geometry,
        # not a measured contribution to binding.
        "is_geometry_not_binding_energy": True,
    }
