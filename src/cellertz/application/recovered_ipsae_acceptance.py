"""Post-hoc ipSAE acceptance for recovered attempt.c5c1f13e.

Reads one saved designs directory and writes a separate report.
Does not launch BindCraft, attach a journey round, or rewrite artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

from cellertz.domains.protein_design.agents.generation import (
    NormalizedCandidate,
)
from cellertz.domains.protein_design.evidence.bindcraft_ipsae import (
    BindCraftArtifactResolver,
    BindCraftIpSAEEvidenceProvider,
)
from cellertz.domains.protein_design.execution.bindcraft_scientific import (
    BINDCRAFT_IPSAE_CONFIG,
)
from cellertz.tools.protein_scoring.ipsae import (
    _load_pdb_ca_chains,
)

RECOVERED_ATTEMPT_ID = "attempt.c5c1f13e"
RECOVERED_DESIGN = "PDL1_l65_s400711_mpnn1"
RECOVERED_PDB_SHA256 = {
    1: "1518cfd1ddb6a46939183a16536a15881b0a3fe3fcfcfdb789c2bf0dc49d789c",
    2: "e5456bb66512a5a1ba32bb4361e80b9323d3c1434c2a4a939a5980ae75f8cb15",
}
RECOVERED_PAE_SHA256 = {
    1: "25f3c5c410e625d42bcc45c87c13baca4d11991f571335a5a7693db5760c8767",
    2: "54d16c9b1247a6a078389d69cc77a580e6141846970d9894fcc30f484ebb70b5",
}
_BINDER_LENGTH = re.compile(r"_l(\d+)_s\d+_mpnn\d+$")
_NOT_INVOKED = "Scoring was not invoked."
_LIMITATIONS = (
    "Post-hoc analysis of recovered artifacts. It is not attached to a design journey round.",
    "It does not modify review.json, raw artifacts, or execution_constraints.unmet.",
    "It does not establish scientific validation.",
    (
        "The representative value is the mean of the per-model raw ipSAE scores. "
        "The model with the highest score is not selected."
    ),
    "BindCraft i_pTM is not used and is not ranking_score.",
    (
        "distance_cutoff is part of CARPNN_2026_REFERENCE and is not used "
        "by the primary ipSAE calculation."
    ),
    "A CPU test of this command is not the host score for attempt.c5c1f13e.",
)
_SELECTION_NOTE = (
    "The representative value is the mean of the per-model raw ipSAE scores. "
    "The model with the highest score is not selected."
)


def analyze_recovered_artifacts(
    designs_dir: str | Path,
    *,
    candidate: str,
    execution_id: str,
    report_path: str | Path,
    expected_hashes: dict[int, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Check association, then score with the existing provider."""

    designs = Path(designs_dir)
    report = Path(report_path)
    document = _base(candidate, execution_id)
    report_blocker = _report_blocker(designs, report)
    if report_blocker is not None:
        return _blocked(document, report_blocker, report, write=False)

    layout = _layout_blocker(designs, execution_id)
    if layout is not None:
        return _blocked(document, layout, report, write=True)

    output_root = designs.resolve().parent.parent
    try:
        resolved = BindCraftArtifactResolver(output_root=output_root).resolve(
            _candidate(candidate, execution_id)
        )
    except (FileNotFoundError, TypeError, ValueError) as exc:
        return _blocked(document, f"{exc} {_NOT_INVOKED}", report, write=True)

    if expected_hashes is not None and set(expected_hashes) != {
        item.model_number for item in resolved.models
    }:
        return _blocked(
            document,
            "The resolved models do not match the expected sha256 manifest. " + _NOT_INVOKED,
            report,
            write=True,
        )

    preflight: dict[int, dict[str, Any]] = {}
    watched: list[Path] = []
    review = designs / "review.json"
    if review.is_file():
        watched.append(review)
    for artifacts in resolved.models:
        watched.extend((artifacts.pdb_path, artifacts.pae_path))
        blocker, details = _inspect_model(
            artifacts.pdb_path,
            artifacts.pae_path,
            design=candidate,
            model_number=artifacts.model_number,
            expected=None if expected_hashes is None else expected_hashes[artifacts.model_number],
        )
        if blocker is not None:
            return _blocked(document, blocker, report, write=True)
        preflight[artifacts.model_number] = details

    before = {path: path.read_bytes() for path in watched}
    document["scoring_invoked"] = True
    provider = BindCraftIpSAEEvidenceProvider(
        resolver=BindCraftArtifactResolver(output_root=output_root),
        ipsae_config=BINDCRAFT_IPSAE_CONFIG,
    )
    try:
        evidence = provider.evaluate(_candidate(candidate, execution_id))
    except (OSError, TypeError, ValueError) as exc:
        document["status"] = "failed"
        document["failure_reason"] = (
            f"{type(exc).__name__}: scoring stopped before a reportable ipSAE. "
            "No substitute score was invented."
        )
        _write_report(report, document)
        return document

    document["raw_artifacts_modified"] = any(path.read_bytes() != before[path] for path in before)
    if document["raw_artifacts_modified"]:
        document["status"] = "failed"
        document["failure_reason"] = "Scoring changed an artifact. The score was not accepted."
        _write_report(report, document)
        return document

    document["status"] = "scored"
    document["model_aggregation"] = evidence.model_aggregation
    document["ranking_value"] = evidence.ranking_value
    document["ipsae_mean"] = evidence.ipsae_mean
    document["ipsae_min"] = evidence.ipsae_min
    document["ipsae_max"] = evidence.ipsae_max
    document["selection_note"] = _SELECTION_NOTE
    document["models"] = [
        {
            "model_number": model.model_number,
            "ipsae": model.ipsae,
            "ipsae_ab": model.ipsae_ab,
            "ipsae_ba": model.ipsae_ba,
            "pdb_sha256": model.pdb_sha256,
            "pae_sha256": model.pae_sha256,
            "pdb_path": str(model.pdb_path),
            "pae_path": str(model.pae_path),
            "chain_order": preflight[model.model_number]["chain_order"],
            "chain_ca_counts": preflight[model.model_number]["chain_ca_counts"],
        }
        for model in evidence.models
    ]
    document["host_identity_confirmed"] = _host_identity(
        candidate,
        execution_id,
        document["models"],
    )
    if document["host_identity_confirmed"] is False:
        document["limitations"] = (
            *_LIMITATIONS,
            (
                "Artifact hashes were not confirmed against attempt.c5c1f13e. "
                "This result is not that attempt's score."
            ),
        )
    _write_report(report, document)
    return document


def main(argv: list[str] | None = None) -> int:
    """Score the recovered attempt and write one new report."""

    parser = argparse.ArgumentParser(
        description=(
            "Post-hoc ipSAE acceptance for recovered attempt.c5c1f13e. "
            "Does not launch generation or rewrite artifacts."
        )
    )
    parser.add_argument("--designs-dir", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    document = analyze_recovered_artifacts(
        args.designs_dir,
        candidate=RECOVERED_DESIGN,
        execution_id=RECOVERED_ATTEMPT_ID,
        report_path=args.report,
        expected_hashes={
            model: {
                "pdb": RECOVERED_PDB_SHA256[model],
                "pae": RECOVERED_PAE_SHA256[model],
            }
            for model in RECOVERED_PDB_SHA256
        },
    )
    sys.stdout.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    if document.get("status") == "scored":
        return 0
    return 2


def _base(candidate: str, execution_id: str) -> dict[str, Any]:
    config = BINDCRAFT_IPSAE_CONFIG
    return {
        "analysis_kind": "recovered_artifact_post_hoc",
        "attempt_id": execution_id,
        "attached_to_round": None,
        "candidate": candidate,
        "execution_constraints_unmet_changed": False,
        "host_identity_confirmed": False,
        "implementation": "BindCraftIpSAEEvidenceProvider",
        "limitations": list(_LIMITATIONS),
        "protocol": {
            "chain_a": config.chain_a,
            "chain_b": config.chain_b,
            "distance_cutoff": config.distance_cutoff,
            "distance_cutoff_used_by_primary_ipsae": False,
            "model_aggregation": "mean",
            "pae_cutoff": config.pae_cutoff,
            "protocol_id": config.protocol_id,
            "within_model_aggregation": config.aggregation,
        },
        "raw_artifacts_modified": False,
        "scientific_validation_established": False,
        "scoring_invoked": False,
        "status": "blocked",
    }


def _blocked(
    document: dict[str, Any],
    blocker: str,
    report: Path,
    *,
    write: bool,
) -> dict[str, Any]:
    document["status"] = "blocked"
    document["blocker"] = blocker
    document["scoring_invoked"] = False
    if write:
        _write_report(report, document)
    return document


def _report_blocker(designs: Path, report: Path) -> str | None:
    if report.name == "review.json":
        return "The report path is review.json. " + _NOT_INVOKED
    if report.exists():
        return "The report already exists and was not overwritten. " + _NOT_INVOKED
    resolved = report.resolve()
    attempt = designs.resolve().parent
    if resolved.is_relative_to(designs.resolve()) or resolved.is_relative_to(attempt):
        return "The report path is inside the attempt directory. " + _NOT_INVOKED
    return None


def _layout_blocker(designs: Path, execution_id: str) -> str | None:
    if designs.name != "designs" or designs.parent.name != execution_id:
        return (
            f"The designs directory is not {execution_id}/designs. "
            "Another attempt was not attached. " + _NOT_INVOKED
        )
    return None


def _candidate(design: str, execution_id: str) -> NormalizedCandidate:
    return NormalizedCandidate(
        candidate_id=design,
        sequence="",
        target_id="PDL1",
        generator="bindcraft",
        generator_metadata={
            "design": design,
            "provenance": {"execution": {"execution_id": execution_id}},
        },
    )


def _inspect_model(
    pdb_path: Path,
    pae_path: Path,
    *,
    design: str,
    model_number: int,
    expected: dict[str, str] | None,
) -> tuple[str | None, dict[str, Any]]:
    try:
        with np.load(pae_path, allow_pickle=False) as payload:
            if not {"pae_angstrom", "candidate_id", "model_number"} <= set(payload.files):
                return (
                    "PAE artifact is missing candidate_id, model_number, or pae_angstrom. "
                    + _NOT_INVOKED,
                    {},
                )
            pae = np.asarray(payload["pae_angstrom"])
            stored_candidate = np.asarray(payload["candidate_id"])
            stored_model = np.asarray(payload["model_number"])
    except (OSError, ValueError) as exc:
        return (
            "PAE file could not be read with allow_pickle=False "
            f"({type(exc).__name__}). " + _NOT_INVOKED,
            {},
        )

    candidate_text = _scalar_text(stored_candidate)
    if candidate_text != design:
        return (
            f"PAE candidate_id is {candidate_text!r}, not the design name {design!r}. "
            "The filename match is not sufficient. " + _NOT_INVOKED,
            {},
        )
    stored_number = _scalar_int(stored_model)
    if stored_number != model_number:
        return (
            f"PAE model_number is {stored_number}, not {model_number}. " + _NOT_INVOKED,
            {},
        )

    pdb_sha = _sha256(pdb_path)
    pae_sha = _sha256(pae_path)
    if expected is not None and (
        pdb_sha != expected.get("pdb") or pae_sha != expected.get("pae")
    ):
        return (
            f"Artifact sha256 does not match the expected file. "
            f"PDB {pdb_sha} PAE {pae_sha}. " + _NOT_INVOKED,
            {},
        )

    if pae.dtype != np.float32:
        return (f"PAE dtype is {pae.dtype}, not float32. " + _NOT_INVOKED, {})
    if pae.ndim != 2 or pae.shape[0] != pae.shape[1] or not np.isfinite(pae).all():
        return (
            "PAE matrix is not a finite square float32 array. " + _NOT_INVOKED,
            {},
        )

    try:
        chains = _load_pdb_ca_chains(pdb_path)
    except ValueError as exc:
        return (f"PDB CA residue order cannot be read ({exc}). " + _NOT_INVOKED, {})
    runs = _chain_runs(chains)
    chain_a = BINDCRAFT_IPSAE_CONFIG.chain_a
    chain_b = BINDCRAFT_IPSAE_CONFIG.chain_b
    if (
        len(runs) != 2
        or runs[0][0] != chain_a
        or runs[1][0] != chain_b
    ):
        return (
            "PDB CA atoms are not one contiguous chain "
            f"{chain_a} block followed by one contiguous chain {chain_b} block. "
            "The PAE file does not record residue order. " + _NOT_INVOKED,
            {},
        )
    if pae.shape[0] != runs[0][1] + runs[1][1]:
        return (
            f"PAE length {pae.shape[0]} does not equal the PDB CA count "
            f"{runs[0][1] + runs[1][1]}. " + _NOT_INVOKED,
            {},
        )
    length_match = _BINDER_LENGTH.search(design)
    if length_match is None:
        return (
            "The binder length could not be read from the design name. " + _NOT_INVOKED,
            {},
        )
    binder_length = int(length_match.group(1))
    if runs[1][1] != binder_length:
        return (
            f"Chain {chain_b} has {runs[1][1]} CA atoms, not the binder length "
            f"{binder_length} in the design name. " + _NOT_INVOKED,
            {},
        )
    return None, {
        "chain_order": [runs[0][0], runs[1][0]],
        "chain_ca_counts": {runs[0][0]: runs[0][1], runs[1][0]: runs[1][1]},
        "pdb_sha256": pdb_sha,
        "pae_sha256": pae_sha,
    }


def _chain_runs(chains: np.ndarray) -> list[tuple[str, int]]:
    runs: list[tuple[str, int]] = []
    for chain in chains.tolist():
        if runs and runs[-1][0] == chain:
            runs[-1] = (chain, runs[-1][1] + 1)
        else:
            runs.append((str(chain), 1))
    return runs


def _scalar_text(value: np.ndarray) -> str | None:
    if value.shape != () or value.dtype.kind not in {"U", "S"}:
        return None
    item = value.item()
    if type(item) is not str:
        return None
    return item


def _scalar_int(value: np.ndarray) -> int | None:
    if value.shape != () or value.dtype.kind not in {"i", "u"}:
        return None
    item = value.item()
    if type(item) is not int:
        return None
    return item


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _host_identity(
    candidate: str,
    execution_id: str,
    models: list[dict[str, Any]],
) -> bool:
    if candidate != RECOVERED_DESIGN or execution_id != RECOVERED_ATTEMPT_ID:
        return False
    if {item["model_number"] for item in models} != set(RECOVERED_PDB_SHA256):
        return False
    return all(
        item["pdb_sha256"] == RECOVERED_PDB_SHA256[item["model_number"]]
        and item["pae_sha256"] == RECOVERED_PAE_SHA256[item["model_number"]]
        for item in models
    )


def _write_report(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    sys.exit(main())
