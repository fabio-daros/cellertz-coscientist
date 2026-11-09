"""Prepare the BindCraft settings a launch would derive from one plan revision.

This calls ``derive_settings``. It does not start a container, BindCraft,
or a GPU, and it does not approve or authorize a run. Original input
files are read and left in place. A derived settings file is written only
when the saved quantity, length, hotspot, and filter are all present and
supported. A later plan revision or digest does not rewrite that file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cellertz.application.apptainer_demonstration import (
    RUNTIME_NAME,
    SIF_PATH,
    SIF_SHA256,
    GroupSupervisedRuntime,
    historical_resource_application,
)
from cellertz.application.generation_plan import (
    _READINESS_LIMITS,
    design_request_conversation,
    explicit_gpu_index,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    CONTAINER_BINDCRAFT_SCRIPT,
    CONTAINER_BOOTSTRAP,
    CONTAINER_PYTHON,
)
from cellertz.application.pdl1_container_entrypoint import (
    DESIGN_COUNT_MAX,
    DESIGN_COUNT_MIN,
    IMAGE_NO_FILTERS,
    LENGTH_MAX,
    LENGTH_MIN,
    ContainerLayout,
    Pdl1ContainerPolicy,
    Pdl1EntrypointError,
    derive_settings,
)

_EXECUTION_BLOCKERS = ("scientific_execution_disabled",)
_INTERPRETATION_LIMITATIONS = (
    "preparation_does_not_start_bindcraft",
    "process_seed_does_not_set_the_bindcraft_trajectory",
    "binder_length_match_is_not_a_scientific_guarantee",
    "no_filters_applies_no_scientific_thresholds",
)
_HISTORICAL_ENTRYPOINT = (
    CONTAINER_PYTHON,
    CONTAINER_BOOTSTRAP,
    "--bindcraft-script",
    CONTAINER_BINDCRAFT_SCRIPT,
)


def preparation_is_current(record: Mapping[str, Any], plan: Mapping[str, Any]) -> bool:
    """True only when the derived file still names this exact plan revision."""

    request_id = record.get("request_id")
    request_revision = record.get("request_revision")
    if not isinstance(request_id, str) or type(request_revision) is not int:
        return False
    if plan.get("conversation_id") != design_request_conversation(request_id, request_revision):
        return False
    stated_id = plan.get("design_request_id")
    stated_revision = plan.get("design_request_revision")
    if stated_id is not None and stated_id != request_id:
        return False
    if stated_revision is not None and stated_revision != request_revision:
        return False
    return (
        record.get("prepared") is True
        and record.get("plan_id") == plan.get("plan_id")
        and record.get("plan_revision") == plan.get("revision")
        and record.get("plan_digest") == plan.get("digest")
    )


def prepare_bindcraft_inputs(
    plans: object,
    drafter: object,
    *,
    request_id: str,
    request_revision: int,
    plan_id: str,
    expected_plan_revision: int,
    expected_digest: str,
    settings_path: Path,
    target_path: Path,
    advanced_path: Path,
    destination: Path,
) -> dict[str, Any]:
    """Map one saved revision onto derived settings, or return pendencies."""

    published = getattr(plans, "published", None)
    if not callable(published):
        return _refused("plan_absent")
    plan = published(plan_id)
    if not isinstance(plan, dict) or plan.get("conversation_id") != design_request_conversation(
        request_id, request_revision
    ):
        return _refused("plan_absent")
    if plan.get("revision") != expected_plan_revision:
        return _refused("plan_revision_conflict")
    if plan.get("digest") != expected_digest:
        return _refused("plan_digest_conflict")
    recorded = _recorded(drafter, request_id, request_revision)
    if recorded is None:
        return _refused("request_absent")
    draft, _message = recorded
    if getattr(draft, "revision", None) != request_revision:
        return _refused("request_revision_conflict")
    fields = plan.get("fields")
    if not isinstance(fields, dict):
        return _refused("plan_absent")
    count, count_blocker = _count(_user_value(draft, "n_candidates"))
    length, length_blocker = _length(fields.get("binder_length"))
    hotspot, hotspot_pendency, hotspot_blocker = _hotspot(fields)
    filters, filter_pendency, filter_blocker = _filters(fields)
    pendencies = [
        name
        for name, missing in (
            ("n_candidates", count is None and count_blocker is None),
            ("binder_length", length is None and length_blocker is None),
            ("hotspot_assumption", hotspot_pendency),
            ("filter_policy", filter_pendency),
        )
        if missing
    ]
    blockers = [
        item
        for item in (count_blocker, length_blocker, hotspot_blocker, filter_blocker)
        if item is not None
    ]
    if pendencies or blockers or count is None or length is None or hotspot is None or filters is None:
        return _incomplete(pendencies, blockers)
    originals = _snapshot(settings_path, target_path, advanced_path)
    if originals is None:
        return _refused("inputs_unreadable")
    if _overlaps(destination, settings_path, target_path, advanced_path):
        return _refused("destination_rejected")
    identity = f"{plan_id}-r{plan['revision']}"
    workspace = destination / identity
    layout = ContainerLayout(
        target_pdb=target_path,
        settings_json=settings_path,
        advanced_json=advanced_path,
        design_records=workspace / "design_records.csv",
        workspace=workspace,
    )
    # Seed is required by the policy type and is not written into settings.
    # Zero here is not a saved choice and is not reported as process_seed.
    policy = Pdl1ContainerPolicy(
        seed=0,
        length_min=length,
        length_max=length,
        n_designs=count,
        filters=filters,
        hotspot=hotspot,
    )
    try:
        derived = derive_settings(layout, policy)
    except Pdl1EntrypointError:
        return _refused("settings_rejected")
    if _snapshot(settings_path, target_path, advanced_path) != originals:
        return _refused("inputs_changed")
    payload = derived.settings_path.read_bytes()
    record = {
        "status": "inputs_prepared",
        "prepared": True,
        "preparation_id": "prep_" + hashlib.sha256(payload).hexdigest()[:32],
        "request_id": request_id,
        "request_revision": request_revision,
        "plan_id": plan_id,
        "plan_revision": plan["revision"],
        "plan_digest": plan["digest"],
        "derived_settings": str(derived.settings_path),
        "derived_settings_sha256": hashlib.sha256(payload).hexdigest(),
        "source_settings_sha256": hashlib.sha256(originals[0]).hexdigest(),
        "source_target_sha256": hashlib.sha256(originals[1]).hexdigest(),
        "source_advanced_sha256": hashlib.sha256(originals[2]).hexdigest(),
        "applied": {
            "n_designs": count,
            "length_min": length,
            "length_max": length,
            "target_hotspot_residues": hotspot,
            "filters": filters,
            "filter_policy": "no_filters",
            "process_seed": _saved_seed(fields.get("seed")),
        },
        "execution_blockers": list(_EXECUTION_BLOCKERS),
        "interpretation_limitations": list(_INTERPRETATION_LIMITATIONS),
        "executable": False,
        "execution_authorized": False,
        "approved": False,
        "scientific_execution": "disabled",
    }
    manifest = workspace / "preparation.json"
    manifest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def _recorded(drafter: object, request_id: str, revision: int) -> tuple[Any, str] | None:
    reader = getattr(drafter, "recorded_at", None)
    if not callable(reader):
        return None
    found = reader(request_id, revision)
    if not isinstance(found, tuple) or len(found) != 2 or not isinstance(found[1], str):
        return None
    return found


def _user_value(draft: object, name: str) -> object:
    fields = {field.name: field for field in getattr(draft, "fields", ())}
    field = fields.get(name)
    if field is None or getattr(field, "provenance", None) != "user":
        return None
    return getattr(field, "value", None)


def _count(value: object) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    if type(value) is not int or isinstance(value, bool) or not DESIGN_COUNT_MIN <= value <= DESIGN_COUNT_MAX:
        return None, "n_candidates_rejected"
    return value, None


def _length(value: object) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    if type(value) is not int or isinstance(value, bool) or not LENGTH_MIN <= value <= LENGTH_MAX:
        return None, "binder_length_rejected"
    return value, None


def _hotspot(fields: Mapping[str, Any]) -> tuple[str | None, bool, str | None]:
    strategy = fields.get("hotspot_strategy")
    if strategy == "none":
        assumption = fields.get("hotspot_assumption")
        if "hotspot_assumption" in fields and assumption not in (None, ""):
            return None, False, "hotspot_rejected"
        return "", False, None
    if strategy != "residues":
        return None, True, None
    value = fields.get("hotspot_assumption")
    if type(value) is not str or not value.strip() or "\n" in value or "\r" in value or len(value) > 160:
        return None, False, "hotspot_rejected"
    return value, False, None


def _saved_hotspot(fields: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The hotspot a delivered settings file must contain."""

    value, pending, blocker = _hotspot(fields)
    if blocker is not None or pending or value is None:
        return None, "hotspot_mismatch"
    return value, None


def _filters(fields: Mapping[str, Any]) -> tuple[str | None, bool, str | None]:
    if "filter_policy" not in fields:
        return None, True, None
    supported = (
        fields.get("filter_policy") == "no_filters"
        and fields.get("filter_policy_explicit") is True
        and fields.get("filter_policy_mode") == "technical_demonstration"
    )
    if not supported:
        return None, False, "filter_policy_unsupported"
    return IMAGE_NO_FILTERS, False, None


def _saved_seed(value: object) -> int | None:
    if type(value) is not int or isinstance(value, bool):
        return None
    return value


def _snapshot(*paths: Path) -> tuple[bytes, bytes, bytes] | None:
    try:
        payloads = tuple(path.read_bytes() for path in paths)
    except OSError:
        return None
    if len(payloads) != 3:
        return None
    return payloads[0], payloads[1], payloads[2]


def _overlaps(destination: Path, *sources: Path) -> bool:
    try:
        target = destination.resolve()
        roots = [path.resolve().parent for path in sources]
    except OSError:
        return True
    return any(target == root or root in target.parents or target in root.parents for root in roots)


def _refused(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "prepared": False,
        "executable": False,
        "execution_authorized": False,
        "approved": False,
        "scientific_execution": "disabled",
    }


def _incomplete(pendencies: list[str], blockers: list[str]) -> dict[str, Any]:
    refused = _refused("incomplete")
    refused["pendencies"] = pendencies
    refused["blockers"] = blockers
    return refused


def consume_prepared_launch(
    control: object,
    *,
    plans: object,
    drafter: object,
    request_id: str,
    request_revision: int,
    plan_id: str,
    plan_revision: int,
    plan_digest: str,
    preparation: Mapping[str, Any] | None,
    argv: tuple[str, ...] | list[str],
    original_settings: Path,
) -> dict[str, Any]:
    """Hand the prepared settings to the launcher. Do not derive or start a process.

    The original settings file is not the launch input. A missing, stale,
    or hash-mismatched preparation returns before any delivery.
    """

    try:
        original = original_settings.read_bytes()
    except OSError:
        original = None

    def blocked(status: str) -> dict[str, Any]:
        if original is not None and original_settings.read_bytes() != original:
            status = "inputs_changed"
        return {
            "status": status,
            "delivered": False,
            "started": False,
            "execution_authorized": False,
            "scientific_execution": "disabled",
            "remaining_blockers": list(_EXECUTION_BLOCKERS),
            "interpretation_limitations": list(_INTERPRETATION_LIMITATIONS),
        }

    if preparation is None or not isinstance(preparation, Mapping):
        return blocked("preparation_absent")
    published = getattr(plans, "published", None)
    plan = published(plan_id) if callable(published) else None
    if not isinstance(plan, dict) or plan.get("conversation_id") != design_request_conversation(
        request_id, request_revision
    ):
        return blocked("plan_absent")
    if plan.get("revision") != plan_revision:
        return blocked("plan_revision_conflict")
    if plan.get("digest") != plan_digest:
        return blocked("plan_digest_conflict")
    if not preparation_is_current(preparation, plan):
        return blocked("preparation_stale")
    if plan.get("approved") is not True or plan.get("execution_authorized") is not False:
        return blocked("approval_required")
    if plan.get("scientific_execution") != "disabled":
        return blocked("execution_not_disabled")
    recorded = _recorded(drafter, request_id, request_revision)
    if recorded is None:
        return blocked("request_absent")
    count, count_blocker = _count(_user_value(recorded[0], "n_candidates"))
    fields = plan.get("fields")
    if not isinstance(fields, dict) or count is None or count_blocker is not None:
        return blocked("quantity_mismatch")
    length, length_blocker = _length(fields.get("binder_length"))
    if length is None or length_blocker is not None:
        return blocked("length_mismatch")
    hotspot, hotspot_status = _saved_hotspot(fields)
    if hotspot_status is not None:
        return blocked(hotspot_status)
    applied = preparation.get("applied")
    filters = applied.get("filters") if isinstance(applied, dict) else None
    if type(filters) is not str or not filters:
        return blocked("filter_mismatch")
    raw_path = preparation.get("derived_settings")
    if type(raw_path) is not str or not raw_path:
        return blocked("preparation_absent")
    try:
        payload = Path(raw_path).read_bytes()
    except OSError:
        return blocked("preparation_unreadable")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != preparation.get("derived_settings_sha256"):
        return blocked("preparation_hash_mismatch")
    if Path(raw_path).resolve() == original_settings.resolve():
        return blocked("original_settings_rejected")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return blocked("preparation_hash_mismatch")
    flags = _launch_flags(argv)
    if (
        type(parsed) is not dict
        or flags is None
        or parsed.get("number_of_final_designs") != count
        or flags.get("--n-designs") != str(count)
        or count == 100
    ):
        return blocked("quantity_mismatch")
    if parsed.get("lengths") != [length, length] or flags.get("--length-min") != str(length) or flags.get("--length-max") != str(length):
        return blocked("length_mismatch")
    if parsed.get("target_hotspot_residues") != hotspot:
        return blocked("hotspot_mismatch")
    if flags.get("--filters") != filters:
        return blocked("filter_mismatch")
    if original is not None and original_settings.read_bytes() != original:
        return blocked("inputs_changed")
    gpu_device, cpu_count, memory_mib, deadline_seconds, resource_blockers = _plan_resources(fields)
    application = historical_resource_application(
        cpu_count=cpu_count,
        memory_mib=memory_mib,
        gpu_index=gpu_device,
    )
    enforcement = _deadline_enforcement(deadline_seconds)
    delivery = {
        "settings": payload,
        "argv": tuple(argv),
        "n_designs": count,
        "number_of_final_designs": parsed["number_of_final_designs"],
        "lengths": parsed["lengths"],
        "target_hotspot_residues": parsed["target_hotspot_residues"],
        "filters": filters,
        "settings_sha256": digest,
        "runtime": RUNTIME_NAME,
        "image": SIF_PATH,
        "image_sha256": SIF_SHA256,
        "entrypoint": _HISTORICAL_ENTRYPOINT,
        "gpu_device": gpu_device,
        "cpu_count": cpu_count,
        "memory_mib": memory_mib,
        "deadline_seconds": deadline_seconds,
        "deadline_enforcement": enforcement,
        "resource_application": {
            "cpu_count": application["cpu_count"],
            "memory_mib": application["memory_mib"],
            "gpu": application["gpu"],
        },
        "apptainer_exec": _historical_exec(application),
    }
    setattr(control, "runtime", RUNTIME_NAME)
    if gpu_device is not None:
        setattr(control, "gpu_device", gpu_device)
    if cpu_count is not None:
        setattr(control, "cpu_count", cpu_count)
    if memory_mib is not None:
        setattr(control, "memory_mib", memory_mib)
    if enforcement is not None:
        setattr(control, "deadline_enforcement", enforcement)
    setattr(control, "prepared_delivery", delivery)
    return {
        "status": "delivered_not_started",
        "delivered": True,
        "started": False,
        "delivery": delivery,
        "execution_authorized": False,
        "scientific_execution": "disabled",
        "remaining_blockers": [*_EXECUTION_BLOCKERS, *resource_blockers],
        "interpretation_limitations": list(_INTERPRETATION_LIMITATIONS),
    }


def _plan_resources(
    fields: Mapping[str, Any],
) -> tuple[str | None, int | None, int | None, int | None, list[str]]:
    """Read explicit plan limits. Absent values stay absent."""

    blockers: list[str] = []
    index = explicit_gpu_index(fields.get("gpu"))
    gpu_device = None if index is None else str(index)
    if gpu_device is None:
        blockers.append("gpu_not_selected")
    cpu_count = _limit(fields.get("cpu_count"), "cpu_count")
    if cpu_count is None:
        blockers.append("cpu_count_not_saved")
    memory_mib = _limit(fields.get("memory_mib"), "memory_mib")
    if memory_mib is None:
        blockers.append("host_memory_not_saved")
    deadline_seconds = _limit(fields.get("execution_deadline_seconds"), "timeout_seconds")
    if deadline_seconds is None:
        blockers.append("execution_deadline_not_saved")
    return gpu_device, cpu_count, memory_mib, deadline_seconds, blockers


def _deadline_enforcement(seconds: int | None) -> dict[str, Any] | None:
    """Bind a saved duration to the supervisor that kills every descendant.

    The integer on the plan is the timeout argument. It is not a timer by itself.
    ``GroupSupervisedRuntime`` sends SIGTERM, then SIGKILL, when that timeout
    expires. The parent-watch pipe does the same if the worker exits, including
    a descendant that has created another session or process group.
    """

    if seconds is None or GroupSupervisedRuntime.name != RUNTIME_NAME:
        return None
    return {
        "runtime": RUNTIME_NAME,
        "supervisor": "cellertz.application.apptainer_demonstration.GroupSupervisedRuntime",
        "timeout_seconds": seconds,
        "on_timeout": "SIGTERM_then_SIGKILL",
        "on_worker_loss": "parent_watch_kills_descendants_including_new_session_or_group",
    }


def _historical_exec(application: Mapping[str, Any]) -> tuple[str, ...]:
    """The historical Apptainer command with the saved limits. It is not started."""

    command = ["apptainer", "exec", "--cleanenv", "--contain", "--nv"]
    flags = application.get("flags")
    if isinstance(flags, tuple):
        command.extend(flags)
    command.extend([SIF_PATH, "/usr/bin/env"])
    gpu = application.get("gpu")
    if isinstance(gpu, Mapping) and gpu.get("kind") == "library_configuration":
        command.append(f"CUDA_VISIBLE_DEVICES={gpu['value']}")
    command.extend(
        (
            "XLA_PYTHON_CLIENT_PREALLOCATE=false",
            "PYTHONUNBUFFERED=1",
            *_HISTORICAL_ENTRYPOINT,
        )
    )
    return tuple(command)


def _limit(value: object, name: str) -> int | None:
    low, high = _READINESS_LIMITS[name]
    if type(value) is not int or isinstance(value, bool) or not low <= value <= high:
        return None
    return value


def _launch_flags(argv: tuple[str, ...] | list[str]) -> dict[str, str] | None:
    if type(argv) not in {tuple, list} or any(type(item) is not str for item in argv):
        return None
    flags: dict[str, str] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("--") or token in flags or index + 1 >= len(argv):
            return None
        flags[token] = argv[index + 1]
        index += 2
    required = ("--n-designs", "--length-min", "--length-max", "--filters")
    if any(name not in flags for name in required):
        return None
    return flags
