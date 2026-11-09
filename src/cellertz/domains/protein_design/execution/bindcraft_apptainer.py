from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from cellertz.application import trajectory_budget as trajectory_budget_module
from cellertz.domains.protein_design.adapters.bindcraft import (
    STATS_FILENAME,
    BindCraftArtifacts,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftAuthorizedRequest,
)
from cellertz.runtime import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionRuntime,
    LocalExecutionRuntime,
)

CONTAINER_BINDCRAFT_ROOT = "/opt/BindCraft"
CONTAINER_BINDCRAFT_SCRIPT = "/opt/BindCraft/bindcraft.py"
CONTAINER_PYTHON = "/opt/conda/envs/BindCraft/bin/python"

CONTAINER_TARGET = "/cellertz/input/target.pdb"
CONTAINER_CONFIG_DIR = "/cellertz/config"
CONTAINER_SETTINGS = "/cellertz/config/settings.json"
CONTAINER_ADVANCED = "/cellertz/config/advanced.json"
CONTAINER_BOOTSTRAP = "/cellertz/config/bindcraft_seeded_entrypoint.py"
CONTAINER_OUTPUT = "/cellertz/output/designs"

CONTAINER_NO_FILTERS = (
    "/opt/BindCraft/settings_filters/no_filters.json"
)

DEFAULT_SMOKE_ADVANCED_PROFILE = (
    Path(__file__).resolve().parents[5]
    / "data"
    / "bindcraft"
    / "profiles"
    / "smoke_4stage_multimer.json"
)

# One LowConfidence trajectory exhausted rounds whose budget was 1.
# Four is the reviewed ceiling for a later round. It is not a default,
# and it does not relax BindCraft's confidence termination.
ATTEMPTED_TRAJECTORY_BUDGET_UPPER_BOUND = 4


def bounded_sequence_limits(value: object) -> dict[str, int] | None:
    """Attempted-trajectory budget that may be registered. Other sequence counts stay 1."""

    if not isinstance(value, dict):
        return None
    budget = value.get("max_trajectories")
    if (
        type(budget) is not int
        or isinstance(budget, bool)
        or not 1 <= budget <= ATTEMPTED_TRAJECTORY_BUDGET_UPPER_BOUND
    ):
        return None
    limits = {"max_trajectories": budget}
    for name in ("num_seqs", "max_mpnn_sequences"):
        if value.get(name) != 1:
            return None
        limits[name] = 1
    return limits


BOOTSTRAP_SOURCE = """\
import argparse
import hashlib
import json
import os
import runpy
import sys
from pathlib import Path

import numpy as np


parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--cellertz-seed", type=int, required=True)
parser.add_argument("--bindcraft-script", required=True)
parser.add_argument(
    "--cellertz-budget-report",
    default=(
        "/cellertz/output/designs/"
        "cellertz_trajectory_budget.json"
    ),
)

args, bindcraft_args = parser.parse_known_args()

np.random.seed(args.cellertz_seed)

existing_library_path = os.environ.get(
    "LD_LIBRARY_PATH",
    "",
)

conda_library_path = (
    "/opt/conda/envs/BindCraft/lib:"
    "/opt/conda/lib"
)

os.environ["LD_LIBRARY_PATH"] = (
    conda_library_path
    + (
        ":" + existing_library_path
        if existing_library_path
        else ""
    )
)

bindcraft_script = Path(
    args.bindcraft_script
).resolve()

sys.path.insert(
    0,
    str(bindcraft_script.parent),
)

_CAPTURED = {}
_RESTYPE_3 = (
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY",
    "HIS", "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER",
    "THR", "TRP", "TYR", "VAL", "UNK",
)
_CHAIN_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
)


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_vector(value):
    array = np.asarray(value)
    while array.ndim > 1 and array.shape[0] == 1:
        array = array[0]
    return array


def _snapshot_residue_order(prediction_model, pae):
    # Read the residue order of this PAE from the model that just produced it.
    # The relaxed PDB is not an input to this record.
    aux = getattr(prediction_model, "aux", None)
    lengths = getattr(prediction_model, "_lengths", None)
    if not isinstance(aux, dict) or "aatype" not in aux or "residue_index" not in aux:
        return None, "model_residue_order_absent"
    if not isinstance(lengths, (list, tuple)) or not lengths:
        return None, "model_chain_lengths_absent"
    try:
        aatype = _as_vector(aux["aatype"])
        residue_index = _as_vector(aux["residue_index"])
        chain_lengths = [int(item) for item in lengths]
    except (TypeError, ValueError):
        return None, "model_residue_order_unreadable"
    if aatype.ndim != 1 or residue_index.shape != aatype.shape:
        return None, "model_residue_order_unreadable"
    if int(pae.shape[0]) != int(aatype.shape[0]):
        return None, "pae_residue_length_mismatch"
    if any(item <= 0 for item in chain_lengths):
        return None, "model_chain_lengths_absent"
    if sum(chain_lengths) != int(aatype.shape[0]) or len(chain_lengths) > len(_CHAIN_ALPHABET):
        return None, "model_chain_lengths_do_not_cover_pae"
    chains = []
    for length, letter in zip(chain_lengths, _CHAIN_ALPHABET):
        chains.extend([letter] * length)
    residues = []
    previous = None
    number = 1
    current_chain = chains[0]
    for index in range(int(aatype.shape[0])):
        code = int(aatype[index])
        model_index = int(residue_index[index])
        if code < 0 or code >= len(_RESTYPE_3):
            return None, "model_residue_name_unreadable"
        chain = chains[index]
        if previous is None:
            previous = model_index
        else:
            number += model_index - previous
            previous = model_index
        if chain != current_chain:
            number = 1
            current_chain = chain
        residues.append(
            {
                "index": index,
                "chain": chain,
                "residue_name": _RESTYPE_3[code],
                "residue_number": int(number),
                "insertion_code": "",
                "model_residue_index": model_index,
            }
        )
    return residues, None


def _pdb_ca_residues(path):
    residues = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")) or len(line) < 27:
            continue
        if line[12:16].strip() != "CA":
            continue
        chain = line[21].strip()
        name = line[17:20].strip()
        if not chain or not name:
            raise ValueError("CA residue identity is incomplete")
        residues.append(
            {
                "chain": chain,
                "residue_name": name,
                "residue_number": int(line[22:26]),
                "insertion_code": line[26].strip(),
            }
        )
    return residues


def _residue_order_preserved(captured, parsed):
    if len(captured) != len(parsed):
        return False
    for left, right in zip(captured, parsed):
        if (
            left["chain"] != right["chain"]
            or left["residue_name"] != right["residue_name"]
            or left["residue_number"] != right["residue_number"]
            or left["insertion_code"] != right["insertion_code"]
        ):
            return False
    return True


def _retain_residue_order(evidence_dir):
    # Bind the capture-time residue list to the files. Do not rebuild it from the PDB.
    relaxed = evidence_dir.parents[1] / "MPNN" / "Relaxed"
    if not evidence_dir.is_dir():
        return
    for pae_path in sorted(evidence_dir.glob("*_pae.npz")):
        name = pae_path.name[: -len("_pae.npz")]
        record = {
            "contract": "pae_index_equals_capture_time_residue_order",
            "established": False,
            "source": "prediction_model.aux",
        }
        try:
            with np.load(pae_path, allow_pickle=False) as payload:
                candidate = np.asarray(payload["candidate_id"])
                model_number = np.asarray(payload["model_number"])
                pae = np.asarray(payload["pae_angstrom"])
            candidate_text = candidate.item() if candidate.shape == () else None
            model_value = model_number.item() if model_number.shape == () else None
            captured, capture_reason = _CAPTURED.get(pae_path.name, (None, "capture_snapshot_missing"))
            if type(candidate_text) is not str or type(model_value) is not int:
                record["reason"] = "pae_identity_unreadable"
            elif pae.dtype != np.float32 or pae.ndim != 2 or pae.shape[0] != pae.shape[1]:
                record["reason"] = "pae_shape_unusable"
            elif captured is None:
                record["reason"] = capture_reason
            else:
                record["candidate_id"] = candidate_text
                record["model_number"] = model_value
                record["residues"] = captured
                record["pae_sha256"] = _sha256_file(pae_path)
                pdb_path = relaxed / f"{name}.pdb"
                if not pdb_path.is_file():
                    record["reason"] = "relaxed_pdb_missing"
                else:
                    record["pdb_sha256"] = _sha256_file(pdb_path)
                    parsed = _pdb_ca_residues(pdb_path)
                    if len(captured) != int(pae.shape[0]):
                        record["reason"] = "pae_residue_length_mismatch"
                    elif not _residue_order_preserved(captured, parsed):
                        record["reason"] = "relaxed_pdb_residue_order_differs"
                    else:
                        record["established"] = True
        except (OSError, ValueError, KeyError) as exc:
            record["reason"] = type(exc).__name__
        sidecar = evidence_dir / f"{name}_residue_order.json"
        sidecar.write_text(json.dumps(record, sort_keys=True) + "\\n", encoding="utf-8")


try:
    import functions
except ModuleNotFoundError as exc:
    if exc.name != "functions":
        raise
    functions = None

if (
    functions is not None
    and hasattr(
        functions,
        "predict_binder_complex",
    )
):
    _original_predict_binder_complex = (
        functions.predict_binder_complex
    )


    def _cellertz_predict_binder_complex(
        prediction_model,
        binder_sequence,
        mpnn_design_name,
        target_pdb,
        chain,
        length,
        trajectory_pdb,
        prediction_models,
        advanced_settings,
        filters,
        design_paths,
        failure_csv,
        seed=None,
    ):
        original_predict = prediction_model.predict

        evidence_dir = (
            Path(design_paths["MPNN"]).parent
            / "CellertzEvidence"
            / "PAE"
        )
        evidence_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        def _capture_predict(
            *predict_args,
            **predict_kwargs,
        ):
            result = original_predict(
                *predict_args,
                **predict_kwargs,
            )

            pae = np.asarray(
                prediction_model.aux["pae"],
                dtype=np.float32,
            )

            if (
                pae.ndim != 2
                or pae.shape[0] != pae.shape[1]
            ):
                raise RuntimeError(
                    "Cellertz PAE capture requires "
                    "a square 2D matrix."
                )

            if not np.isfinite(pae).all():
                raise RuntimeError(
                    "Cellertz PAE capture received "
                    "non-finite values."
                )

            models = predict_kwargs.get("models")

            if (
                not isinstance(models, (list, tuple))
                or len(models) != 1
            ):
                raise RuntimeError(
                    "Cellertz PAE capture requires "
                    "exactly one AF2 model."
                )

            model_number = int(models[0]) + 1

            artifact_path = (
                evidence_dir
                / (
                    f"{mpnn_design_name}"
                    f"_model{model_number}_pae.npz"
                )
            )

            np.savez_compressed(
                artifact_path,
                pae_angstrom=pae,
                candidate_id=np.asarray(
                    mpnn_design_name
                ),
                model_number=np.asarray(
                    model_number,
                    dtype=np.int32,
                ),
            )
            _CAPTURED[artifact_path.name] = (
                _snapshot_residue_order(
                    prediction_model,
                    pae,
                )
            )

            return result

        prediction_model.predict = _capture_predict

        try:
            predicted = _original_predict_binder_complex(
                prediction_model,
                binder_sequence,
                mpnn_design_name,
                target_pdb,
                chain,
                length,
                trajectory_pdb,
                prediction_models,
                advanced_settings,
                filters,
                design_paths,
                failure_csv,
                seed=seed,
            )
        finally:
            prediction_model.predict = original_predict
        _retain_residue_order(evidence_dir)
        return predicted


    functions.predict_binder_complex = (
        _cellertz_predict_binder_complex
    )

def _bound_argument(argv, name, fallback):
    for index, item in enumerate(argv):
        if item == name and index + 1 < len(argv):
            return argv[index + 1]
    return fallback


if (
    functions is not None
    and hasattr(functions, "check_n_trajectories")
):
    sys.path.insert(1, str(Path(__file__).resolve().parent))
    from trajectory_budget import install_attempted_trajectory_budget

    install_attempted_trajectory_budget(
        functions,
        script_path=bindcraft_script,
        advanced_path=Path(
            _bound_argument(
                bindcraft_args,
                "--advanced",
                "/cellertz/config/advanced.json",
            )
        ),
        report_path=Path(args.cellertz_budget_report),
    )

sys.argv = [
    str(bindcraft_script),
    *bindcraft_args,
]

runpy.run_path(
    str(bindcraft_script),
    run_name="__main__",
)
"""


@dataclass(frozen=True)
class BindCraftApptainerPlan:
    argv: tuple[str, ...]
    runtime_dir: Path
    runtime_settings_path: Path
    runtime_advanced_path: Path
    advanced_sha256: str
    bootstrap_path: Path
    bindcraft_output_dir: Path
    timeout_seconds: int


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def _load_json_object(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise TypeError(
            f"Expected JSON object in {path}"
        )

    return data


def _requested_lengths(
    authorized: BindCraftAuthorizedRequest,
    target_settings: dict[str, object],
) -> list[int]:
    constraints = authorized.generation.constraints

    length_min = constraints.get("length_min")
    length_max = constraints.get("length_max")

    if length_min is None and length_max is None:
        existing = target_settings.get("lengths")

        if (
            isinstance(existing, list)
            and len(existing) == 2
            and all(
                isinstance(value, int)
                for value in existing
            )
        ):
            return [existing[0], existing[1]]

        raise ValueError(
            "BindCraft lengths are missing from both "
            "GenerationRequest and target settings"
        )

    if not isinstance(length_min, int):
        raise TypeError(
            "length_min must be an integer"
        )

    if not isinstance(length_max, int):
        raise TypeError(
            "length_max must be an integer"
        )

    if length_min <= 0 or length_max <= 0:
        raise ValueError(
            "BindCraft lengths must be positive"
        )

    if length_min > length_max:
        raise ValueError(
            "length_min cannot be greater than length_max"
        )

    return [length_min, length_max]


def materialize_target_settings(
    authorized: BindCraftAuthorizedRequest,
    runtime_dir: Path,
) -> Path:
    execution = authorized.execution_request

    if execution.settings_path is None:
        raise ValueError(
            "BindCraft settings_path is required"
        )

    target_settings = _load_json_object(
        execution.settings_path
    )

    target_settings["starting_pdb"] = CONTAINER_TARGET
    target_settings["design_path"] = CONTAINER_OUTPUT
    target_settings["lengths"] = _requested_lengths(
        authorized,
        target_settings,
    )
    target_settings["number_of_final_designs"] = (
        authorized.generation.n_candidates
    )

    runtime_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    runtime_settings_path = (
        runtime_dir / "settings.json"
    )

    with runtime_settings_path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            target_settings,
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")

    return runtime_settings_path


def resolve_advanced_profile_path(
    source_path: Path | None = None,
) -> Path:
    profile_path = (
        DEFAULT_SMOKE_ADVANCED_PROFILE
        if source_path is None
        else source_path
    )

    if not profile_path.is_file():
        raise FileNotFoundError(
            f"BindCraft advanced profile not found: "
            f"{profile_path}"
        )

    return profile_path


def _validate_smoke_profile(
    advanced: dict[str, object],
) -> dict[str, object]:
    if bounded_sequence_limits(advanced) is None:
        raise ValueError(
            "Unsafe BindCraft smoke profile: "
            f"max_trajectories={advanced.get('max_trajectories')!r}, "
            f"num_seqs={advanced.get('num_seqs')!r}, "
            f"max_mpnn_sequences={advanced.get('max_mpnn_sequences')!r}, "
            "expected max_trajectories 1 through "
            f"{ATTEMPTED_TRAJECTORY_BUDGET_UPPER_BOUND} "
            "and num_seqs=1, max_mpnn_sequences=1"
        )

    return advanced


def load_validated_smoke_profile(
    source_path: Path,
) -> dict[str, object]:
    return _validate_smoke_profile(
        _load_json_object(source_path)
    )


def canonical_advanced_profile_bytes(
    advanced: dict[str, object],
) -> bytes:
    return (
        json.dumps(
            advanced,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def canonical_advanced_profile_sha256(
    advanced: dict[str, object],
) -> str:
    return hashlib.sha256(
        canonical_advanced_profile_bytes(advanced)
    ).hexdigest()


def advanced_profile_identity_sha256(
    source_path: Path | None = None,
) -> str:
    """Canonical execution-profile SHA. Does not write files."""
    advanced = load_validated_smoke_profile(
        resolve_advanced_profile_path(source_path)
    )
    return canonical_advanced_profile_sha256(advanced)


def materialize_advanced_profile(
    source_path: Path,
    runtime_dir: Path,
) -> tuple[Path, str]:
    advanced = load_validated_smoke_profile(source_path)
    canonical = canonical_advanced_profile_bytes(advanced)

    runtime_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    runtime_advanced_path = (
        runtime_dir / "advanced.json"
    )
    runtime_advanced_path.write_bytes(canonical)

    return (
        runtime_advanced_path,
        _file_sha256(runtime_advanced_path),
    )


def materialize_seed_bootstrap(
    runtime_dir: Path,
) -> Path:
    runtime_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    bootstrap_path = (
        runtime_dir
        / "bindcraft_seeded_entrypoint.py"
    )
    budget_path = (
        runtime_dir
        / "trajectory_budget.py"
    )
    source_file = trajectory_budget_module.__file__
    if source_file is None:
        raise RuntimeError("trajectory budget adapter source is unavailable")
    budget_source = Path(source_file).read_text(encoding="utf-8")

    bootstrap_path.write_text(
        BOOTSTRAP_SOURCE,
        encoding="utf-8",
    )
    budget_path.write_text(
        budget_source,
        encoding="utf-8",
    )
    if trajectory_budget_module.ADAPTER_VERSION not in budget_source:
        raise RuntimeError("trajectory budget adapter was not materialized")

    return bootstrap_path


def build_apptainer_plan(
    authorized: BindCraftAuthorizedRequest,
    *,
    timeout_seconds: int = 900,
    advanced_profile_path: Path | None = None,
) -> BindCraftApptainerPlan:
    execution = authorized.execution_request

    if execution.apptainer_bin is None:
        raise ValueError(
            "apptainer_bin is required for container execution"
        )

    if execution.container_image is None:
        raise ValueError(
            "container_image is required for container execution"
        )

    if execution.target_path is None:
        raise ValueError(
            "target_path is required for BindCraft execution"
        )

    if timeout_seconds <= 0:
        raise ValueError(
            "timeout_seconds must be positive"
        )

    profile_path = resolve_advanced_profile_path(
        advanced_profile_path
    )

    runtime_dir = (
        authorized.output_dir
        / ".cellertz_runtime"
    )

    runtime_settings_path = materialize_target_settings(
        authorized,
        runtime_dir,
    )

    (
        runtime_advanced_path,
        advanced_sha256,
    ) = materialize_advanced_profile(
        profile_path,
        runtime_dir,
    )

    bootstrap_path = materialize_seed_bootstrap(
        runtime_dir
    )

    bindcraft_output_dir = (
        authorized.output_dir
        / "designs"
    )

    bindcraft_output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    target_bind = (
        f"{execution.target_path}:"
        f"{CONTAINER_TARGET}:ro"
    )

    config_bind = (
        f"{runtime_dir}:"
        f"{CONTAINER_CONFIG_DIR}:ro"
    )

    output_bind = (
        f"{bindcraft_output_dir}:"
        f"{CONTAINER_OUTPUT}"
    )

    argv = (
        str(execution.apptainer_bin),
        "exec",
        "--cleanenv",
        "--contain",
        "--nv",
        "--bind",
        target_bind,
        "--bind",
        config_bind,
        "--bind",
        output_bind,
        execution.container_image,
        "/usr/bin/env",
        "XLA_PYTHON_CLIENT_PREALLOCATE=false",
        "PYTHONUNBUFFERED=1",
        CONTAINER_PYTHON,
        CONTAINER_BOOTSTRAP,
        "--cellertz-seed",
        str(execution.seed),
        "--bindcraft-script",
        CONTAINER_BINDCRAFT_SCRIPT,
        "--settings",
        CONTAINER_SETTINGS,
        "--filters",
        CONTAINER_NO_FILTERS,
        "--advanced",
        CONTAINER_ADVANCED,
    )

    return BindCraftApptainerPlan(
        argv=argv,
        runtime_dir=runtime_dir,
        runtime_settings_path=runtime_settings_path,
        runtime_advanced_path=runtime_advanced_path,
        advanced_sha256=advanced_sha256,
        bootstrap_path=bootstrap_path,
        bindcraft_output_dir=bindcraft_output_dir,
        timeout_seconds=timeout_seconds,
    )


@dataclass(frozen=True)
class ApptainerBindCraftExecution:
    plan: BindCraftApptainerPlan
    execution_request: ExecutionRequest
    result: ExecutionResult
    artifacts: BindCraftArtifacts | None


class ApptainerBindCraftGenerator:
    """Delegate an authorized BindCraft plan to an execution runtime."""

    def __init__(
        self,
        *,
        runtime: ExecutionRuntime | None = None,
        timeout_seconds: int = 900,
        advanced_profile_path: Path | None = None,
    ) -> None:
        self.runtime = (
            runtime
            if runtime is not None
            else LocalExecutionRuntime()
        )
        self.timeout_seconds = timeout_seconds
        self.advanced_profile_path = advanced_profile_path

    def effective_advanced_profile_sha256(self) -> str:
        return advanced_profile_identity_sha256(
            self.advanced_profile_path
        )

    def generate(
        self,
        request: BindCraftAuthorizedRequest,
    ) -> ApptainerBindCraftExecution:
        plan = build_apptainer_plan(
            request,
            timeout_seconds=self.timeout_seconds,
            advanced_profile_path=(
                request.advanced_profile_path
                if request.advanced_profile_path is not None
                else self.advanced_profile_path
            ),
        )

        execution_request = ExecutionRequest(
            argv=plan.argv,
            timeout_seconds=plan.timeout_seconds,
        )

        result = self.runtime.execute(
            execution_request
        )

        artifacts = None

        if result.status == "complete":
            artifacts = BindCraftArtifacts(
                output_dir=plan.bindcraft_output_dir,
                stats_csv=(
                    plan.bindcraft_output_dir
                    / STATS_FILENAME
                ),
                command=plan.argv,
                bindcraft_version=(
                    request.manifest.tool_version
                ),
                returncode=result.return_code or 0,
                execution_provenance={
                    "execution_id": request.manifest.execution_id,
                    "run_id": request.manifest.run_id,
                    "manifest_sha256": (
                        request.manifest.manifest_sha256
                    ),
                    "container_image": (
                        request.manifest.container_image
                    ),
                    "container_digest": (
                        request.manifest.container_digest
                    ),
                    "execution_container_image": (
                        request.execution_request.container_image
                    ),
                    "tool_version": request.manifest.tool_version,
                    "seed": request.manifest.seed,
                    "advanced_profile_sha256": plan.advanced_sha256,
                    "runtime_name": result.runtime_name,
                    "runtime_version": result.runtime_version,
                    "started_at": result.started_at,
                    "completed_at": result.completed_at,
                },
            )

        return ApptainerBindCraftExecution(
            plan=plan,
            execution_request=execution_request,
            result=result,
            artifacts=artifacts,
        )

