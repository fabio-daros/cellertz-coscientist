"""Recorded inspection for one verified attempt, candidate, and model.

The route reads the catalog. It does not launch generation, and it does
not invent specialist activity, ipSAE, or ranking_score.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from cellertz.application.attempt_evidence import (
    AttemptFiles,
    geometric_contacts,
    load_catalog,
    resolve_attempt_id,
)
from cellertz.application.coordinator_trace import absent_trace_note

_ATTEMPT_ID = re.compile(r"attempt\.[A-Za-z0-9]+")
_MODEL_ID = re.compile(r"model[1-9][0-9]*")


@dataclass(frozen=True)
class StructureArtifact:
    status: int
    body: bytes
    media_type: str
    filename: str


def _journey_attempt_files(attempt_id: str) -> AttemptFiles | None:
    """The directory recorded for this attempt id. Another attempt is not substituted."""

    raw = os.environ.get("CELLERTZ_JOURNEY_ATTEMPT_ROOT", "").strip()
    if not raw:
        return None
    directory = Path(raw) / attempt_id
    try:
        directory.resolve().relative_to(Path(raw).resolve())
    except (OSError, ValueError):
        return None
    review = directory / "review.json"
    if not review.is_file():
        return None
    return AttemptFiles(review_json=review, pdb_dir=directory)


def structure_url(attempt_id: str, model_id: str) -> str:
    return f"/api/attempts/{attempt_id}/structures/{model_id}.pdb"


def handle_attempt_get(
    path: str,
    catalog: dict[str, dict[str, Any]] | None = None,
    traces_for: Any | None = None,
    journey_documents: list[dict[str, Any]] | None = None,
) -> tuple[int, dict[str, Any]] | StructureArtifact | None:
    """Serve an inspection or one verified PDB. Other paths return None."""

    split = urlsplit(path)
    parts = [part for part in split.path.split("/") if part]
    if len(parts) < 4 or parts[0] != "api" or parts[1] != "attempts":
        return None
    if not _ATTEMPT_ID.fullmatch(parts[2]):
        return _error(404, "invalid_attempt_id", "The attempt identifier is not exact.")
    loaded = load_catalog() if catalog is None else catalog
    if catalog is None and parts[2] not in loaded:
        linked = _journey_attempt_files(parts[2])
        if linked is not None:
            loaded = {**loaded, **load_catalog((linked,))}
    if parts[3] == "inspection" and len(parts) == 4:
        return inspect_attempt(
            loaded,
            parts[2],
            _query(split.query),
            traces_for=traces_for,
            journey_documents=journey_documents,
        )
    if parts[3] == "structures" and len(parts) == 5 and parts[4].endswith(".pdb"):
        model_id = parts[4][:-4]
        if split.query or not _MODEL_ID.fullmatch(model_id):
            return _error(404, "invalid_model", "The structure route is not a verified model PDB.")
        return read_verified_structure(loaded, parts[2], model_id)
    return _error(404, "unknown_attempt_route", "That attempt route is not available.")


def inspect_attempt(
    catalog: dict[str, dict[str, Any]],
    attempt_id: str,
    query: dict[str, str],
    traces_for: Any | None = None,
    journey_documents: list[dict[str, Any]] | None = None,
) -> tuple[int, dict[str, Any]]:
    candidate = query.get("candidate", "")
    model_id = query.get("model", "")
    if not candidate or not model_id:
        return _error(
            400,
            "attempt_candidate_and_model_required",
            "Name the attempt, candidate, and model. No attempt is selected by default.",
        )
    if not _MODEL_ID.fullmatch(model_id):
        return _error(404, "invalid_model", "The model identifier is not exact.")
    resolved = resolve_attempt_id(catalog, attempt_id)
    if resolved["status"] != "ok":
        return _error(404, str(resolved["status"]), str(resolved.get("error") or "Unknown attempt."))
    item = resolved["item"]
    record = item["record"]
    if candidate != record.get("design"):
        return _error(
            404,
            "candidate_mismatch",
            f"Candidate {candidate} is not the recorded design {record.get('design')}.",
        )
    if model_id not in item["models"]:
        available = ", ".join(sorted(item["models"])) or "(none)"
        return _error(404, "unknown_model", f"Model {model_id} is not recorded. Available: {available}.")
    contacts = geometric_contacts(item)
    resolved_id = str(resolved["resolved_attempt_id"])
    payload = _payload(resolved_id, item, model_id, contacts)
    payload["saved_specialist_processing"] = _saved_specialist(resolved_id, item, journey_documents)
    payload["experimental_validation_established"] = (
        record.get("experimental_validation") is True
    )
    payload["execution_compliance_note"] = (
        "Overall execution compliance is not established by PDB verification, "
        "attempted-trajectory-budget compliance, or a missing constraint record."
    )
    payload.update(_trace_view(resolved_id, traces_for))
    return 200, payload


def read_verified_structure(
    catalog: dict[str, dict[str, Any]],
    attempt_id: str,
    model_id: str,
) -> tuple[int, dict[str, Any]] | StructureArtifact:
    resolved = resolve_attempt_id(catalog, attempt_id)
    if resolved["status"] != "ok":
        return _error(404, str(resolved["status"]), str(resolved.get("error") or "Unknown attempt."))
    item = resolved["item"]
    model = item["models"].get(model_id)
    if not isinstance(model, dict):
        return _error(404, "unknown_model", f"Model {model_id} is not recorded.")
    pdb_path = model["pdb_path"]
    pdb_dir = pdb_path.parent
    if not _inside(pdb_dir, pdb_path) or pdb_path.name != f"{model_id}.pdb":
        return _error(404, "structure_path_rejected", "The structure path is not contained.")
    if not pdb_path.is_file():
        return _error(404, "structure_missing", "The verified structure file is not present.")
    if model["actual_sha256"] != model["expected_sha256"]:
        return _error(409, "hash_mismatch", "The structure file does not match the verified hash.")
    return StructureArtifact(
        status=200,
        body=pdb_path.read_bytes(),
        media_type="chemical/x-pdb",
        filename=f"{model_id}.pdb",
    )


def _payload(
    attempt_id: str,
    item: dict[str, Any],
    selected_model: str,
    contacts: dict[str, Any],
) -> dict[str, Any]:
    record = item["record"]
    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
    constraints = provenance.get("execution_constraints")
    reported = isinstance(constraints, dict)
    models = {}
    for model_id, model in item["models"].items():
        recorded = model["record"]
        calculated = None
        recorded_contacts = recorded.get("contacts")
        matches = None
        if isinstance(contacts.get("models"), dict) and model_id in contacts["models"]:
            entry = contacts["models"][model_id]
            raw_calculated = entry.get("calculated")
            if isinstance(raw_calculated, dict):
                calculated = {**raw_calculated, "cutoff_angstrom": entry.get("cutoff_angstrom")}
            recorded_contacts = entry.get("recorded_in_review")
            matches = entry.get("matches_recorded_counts")
        models[model_id] = {
            "id": model_id,
            "representative": recorded.get("representative") is True,
            "sequence": recorded.get("sequence"),
            "sha256": model["expected_sha256"],
            "actual_sha256": model["actual_sha256"],
            "hash_matches": model["actual_sha256"] == model["expected_sha256"],
            "pdb_url": structure_url(attempt_id, model_id),
            "source_pdb": recorded.get("source_pdb"),
            "ca_counts": _ca_counts(model["pdb_path"]) if model["actual_sha256"] else {},
            "calculated_contacts": calculated,
            "recorded_contacts": recorded_contacts,
            "contacts_match_recorded": matches,
        }
    return {
        "status": "ok",
        "attempt_id": attempt_id,
        "candidate": record.get("design"),
        "selected_model": selected_model,
        "origin": _origin(record),
        "live_processing": False,
        "specialist_activity": [],
        "specialist_activity_note": (
            "This inspection reads the recorded review. It does not replay specialists."
        ),
        "pdb_verified": item["pdb_verified"],
        "artifact_verification": {"pdb_verified": item["pdb_verified"]},
        "execution_compliance_established": False,
        "scientific_validation_established": record.get("experimental_validation") is True,
        "scientific_evaluation": record.get("scientific_evaluation"),
        "platform_ipsae_ranking": record.get("platform_ipsae_ranking"),
        "ranking_score": "not_recorded",
        "ranking_note": (
            "ranking_score is raw ipSAE. BindCraft i_pTM is a recorded metric and does not "
            "replace ipSAE. A missing ranking_score stays missing."
        ),
        "filter_policy": record.get("filter_policy"),
        "chains": record.get("chains"),
        "sequence": models[selected_model]["sequence"],
        "representative": record.get("representative"),
        "interface_residues": record.get("interface_residues"),
        "metrics": record.get("metrics") or [],
        "execution_constraints": constraints if reported else None,
        "execution_constraints_reported": reported,
        "absence_of_execution_constraints_is_not_compliance": True,
        "models": models,
        "contact_status": contacts.get("status"),
        "evidence_references": _references(attempt_id, record, models, provenance),
    }


def _saved_specialist(
    attempt_id: str,
    item: dict[str, Any],
    documents: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    from cellertz.application.results_specialist_projection import (
        project_saved_specialist_evidence,
    )

    review_models = {
        model_id: model["expected_sha256"]
        for model_id, model in item["models"].items()
        if isinstance(model, dict) and isinstance(model.get("expected_sha256"), str)
    }
    return project_saved_specialist_evidence(
        attempt_id=attempt_id,
        design=str(item["record"].get("design") or ""),
        review_models=review_models,
        documents=documents,
    )


def _trace_view(attempt_id: str, traces_for: Any | None) -> dict[str, Any]:
    raw = traces_for(attempt_id) if callable(traces_for) else []
    traces = [_present_trace(attempt_id, item) for item in raw if isinstance(item, dict)]
    traces = [item for item in traces if item.get("attempt_id") == attempt_id]
    questionless = [item for item in traces if not _asked_question(item)]
    traces = [item for item in traces if item not in questionless]
    if not traces:
        note = (
            "No recorded coordinator trace. A saved record without a question is not "
            "a coordinator trace."
            if questionless
            else absent_trace_note()
        )
        return {"coordinator_traces": [], "coordinator_trace_note": note}
    note = None
    if questionless:
        note = "Records without a question are not coordinator traces."
    return {"coordinator_traces": traces, "coordinator_trace_note": note}


def _asked_question(trace: dict[str, Any]) -> bool:
    question = trace.get("source_query")
    return isinstance(question, str) and bool(question.strip())


def _present_trace(attempt_id: str, trace: dict[str, Any]) -> dict[str, Any]:
    presented = json.loads(json.dumps(trace))
    for step in presented.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for reference in step.get("evidence_references") or []:
            if isinstance(reference, dict):
                _bind_structure_links(attempt_id, reference)
    return presented


def _bind_structure_links(attempt_id: str, reference: dict[str, Any]) -> None:
    models = reference.get("models")
    if not isinstance(models, dict):
        return
    linked: dict[str, Any] = {}
    for model_id, model in models.items():
        if not isinstance(model_id, str) or not isinstance(model, dict):
            continue
        copied = dict(model)
        if _MODEL_ID.fullmatch(model_id):
            copied["structure_href"] = structure_url(attempt_id, model_id)
        linked[model_id] = copied
    reference["models"] = linked


def _origin(record: dict[str, Any]) -> dict[str, Any]:
    execution = record.get("execution")
    if execution == "recovered_without_apptainer":
        kind = "recovered"
        detail = "Recovered from a completed attempt. This is not live generation."
    elif execution == "manual":
        kind = "historical_manual"
        detail = "Historical manual attempt. This is not live generation."
    else:
        kind = "recorded"
        detail = "Recorded attempt. This is not live generation."
    return {"kind": kind, "execution": execution, "detail": detail}


def _references(
    attempt_id: str,
    record: dict[str, Any],
    models: dict[str, Any],
    provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = [
        {"kind": "attempt", "attempt_id": attempt_id, "candidate": record.get("design")}
    ]
    for model_id, model in models.items():
        references.append(
            {
                "kind": "structure",
                "model_id": model_id,
                "sha256": model["sha256"],
                "href": model["pdb_url"],
            }
        )
    if isinstance(provenance.get("stats_csv_sha256"), str):
        references.append(
            {"kind": "stats_csv", "sha256": provenance["stats_csv_sha256"]}
        )
    return references


def _ca_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    seen: dict[str, set[int]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")) or line[12:16].strip() != "CA":
            continue
        chain = line[21:22].strip()
        try:
            resseq = int(line[22:26])
        except ValueError:
            continue
        seen.setdefault(chain, set()).add(resseq)
    return {chain: len(residues) for chain, residues in sorted(seen.items())}


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _query(text: str) -> dict[str, str]:
    parsed = parse_qs(text, keep_blank_values=True)
    found: dict[str, str] = {}
    for key, values in parsed.items():
        if len(values) == 1:
            found[key] = values[0]
    return found


def _error(status: int, code: str, detail: str) -> tuple[int, dict[str, Any]]:
    return status, {"status": code, "error": detail, "live_processing": False}
