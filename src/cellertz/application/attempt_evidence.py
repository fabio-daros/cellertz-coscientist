"""Deterministic evidence for verified manual attempts.

Numbers come from the checked review record and from the PDB bytes.
A language model does not calculate them.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
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
