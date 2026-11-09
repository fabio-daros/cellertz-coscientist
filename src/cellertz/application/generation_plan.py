"""Versioned generation plans for the professor conversation.

The language model may propose fields. It cannot approve a plan, bind an
authorization, or start a run. Plan approval and execution authorization
are separate human actions. Dispatch asks the existing scientific-run
service which gates are unmet and does not call an executor while any
gate remains.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cellertz.application.approval import canonical_digest
from cellertz.application.apptainer_demonstration import (
    BOOTSTRAP_SEED_MAXIMUM,
    BOOTSTRAP_SEED_MINIMUM,
    explicit_gpu_index,
)
from cellertz.application.pdl1_container_entrypoint import (
    LENGTH_MAX as CONTAINER_LENGTH_MAX,
    LENGTH_MIN as CONTAINER_LENGTH_MIN,
    SEED_MAX as CONTAINER_SEED_MAX,
    SEED_MIN as CONTAINER_SEED_MIN,
)
from cellertz.application.contracts import ApplicationContractError
from cellertz.application.generation_plan_store import SQLiteGenerationPlanStore
from cellertz.application.pdl1_executor import (
    ARGV_POLICY_VERSION,
    DIRECTORY_POLICY,
    ENVIRONMENT_POLICY,
    Pdl1ValidatedInputIdentity,
)
from cellertz.application.pdl1_input_verification import (
    MAX_INPUT_BYTES,
    Pdl1InputLimits,
    Pdl1InputLocations,
    verify_pdl1_inputs,
)
from cellertz.application.pdl1_selection import Pdl1InputSelection
from cellertz.application.scientific_runner_readiness import (
    DOCKER_PROFILE_ID,
    PDL1_PROFILE_ID,
    _LIMITS as _READINESS_LIMITS,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    bounded_sequence_limits,
    canonical_advanced_profile_bytes,
)

_SHA = re.compile(r"^[0-9a-f]{64}$")
_ROLES = ("target_structure", "settings", "advanced_profile")
_REQUIRED_OUTPUT_KINDS = ("execution_record", "candidate_record")
_KIND_BY_FILENAME = {
    "scientific-execution-record.json": "execution_record",
    "scientific-candidate-record.json": "candidate_record",
    "scientific-agent-workflow.json": "workflow_trace",
    "scientific-execution-note.md": "execution_note",
    "scientific-execution-note.txt": "execution_note",
}
INPUT_ROOT_ENV = "CELLERTZ_PDL1_INPUT_ROOT"
_ROLE_FILES = {
    "target_structure": "target.pdb",
    "settings": "settings.json",
    "advanced_profile": "advanced.json",
}
_INPUT_QUESTIONS = {
    "input_root_unconfigured": (
        "Set CELLERTZ_PDL1_INPUT_ROOT to an absolute directory containing "
        "target.pdb, settings.json, and advanced.json. A stated SHA-256 is not verification."
    ),
    "input_root_invalid": (
        "CELLERTZ_PDL1_INPUT_ROOT must be an absolute directory. "
        "A stated SHA-256 is not verification."
    ),
    "input_files_unverified": (
        "target.pdb, settings.json, and advanced.json could not be verified. "
        "A stated SHA-256 is not verification."
    ),
    "input_identity_mismatch": (
        "The stated SHA-256 does not match the registered files. "
        "A stated SHA-256 is not verification."
    ),
}
_SETTING_NAMES = (
    "target_identity",
    "hotspot_assumption",
    "gpu",
    "filter_policy",
    "binder_length",
    "seed",
    "cpu_count",
    "memory_mib",
    "execution_deadline_seconds",
)
_PRESERVED_SETTINGS = _SETTING_NAMES + tuple(
    f"{name}_{suffix}" for name in _SETTING_NAMES for suffix in ("provenance", "source")
) + ("sequence_limits", "sequence_limits_source", "sequence_limits_provenance")
_SEQUENCE_LIMITS = ("max_trajectories", "num_seqs", "max_mpnn_sequences")
_QUESTIONS = {
    "target_identity": "Which target should this generation use?",
    "input_artifacts": (
        "Registered input files are not verified. A stated SHA-256 is not verification."
    ),
    "binder_length": "How many residues should the binder have?",
    "hotspot_assumption": "Which hotspot assumption should be recorded?",
    "filter_policy": (
        "Which filter policy should be recorded? no_filters is not assumed "
        "and does not establish scientific quality."
    ),
    "seed": "Which integer seed should the plan record?",
    "cpu_count": "How many CPUs should the resource limit record?",
    "memory_mib": (
        "What host RAM limit, in MiB, should the plan record? "
        "This is host memory, not GPU VRAM."
    ),
    "gpu": "Which GPU assignment should the plan record? Use not_assigned if none is chosen.",
    "execution_deadline_seconds": "What execution deadline, in seconds, should the plan record?",
}


def governed_host_readiness() -> dict[str, Any]:
    """Describe the governed runtime. This does not start it."""

    from cellertz.application.pdl1_docker import WORKER_LOSS_DEADLINE

    return {
        "manual_attempt_runtime": "apptainer",
        "manual_attempt_image": "BindCraft SIF",
        "manual_attempt_profile": PDL1_PROFILE_ID,
        "governed_profile_id": DOCKER_PROFILE_ID,
        "governed_runtime": "docker",
        "governed_image": (
            "The server deployment pins repository and image_digest for "
            "deploy/pdl1-bindcraft. That image is not the manual SIF."
        ),
        "governed_deadline": "SystemdHostDeadline",
        "docker_is_the_supported_governed_path": True,
        "manual_launcher_is_not_the_governed_executor": True,
        "unresolved_gate": WORKER_LOSS_DEADLINE,
        "scientific_execution": "disabled",
        "host_only_steps": [
            "Register a SystemdHostDeadline supervisor on the Linux GPU host.",
            "Satisfy readiness: deadline_manager, deadline_interpreter, deadline_state_dir, systemctl, systemd-run, docker, and Linger=yes in user mode.",
            "Pin docker_host, docker_root, and the image digest in the server deployment.",
            "Issue a scientific authorization through the existing authorization service.",
            "Enable CELLERTZ_SCIENTIFIC_RUN_EXECUTION only after those checks pass.",
        ],
        "complete_locally": [
            "Durable plan store when CELLERTZ_GENERATION_PLAN_STORE=sqlite",
            "Startup registers files from CELLERTZ_PDL1_INPUT_ROOT",
            "Separate plan approval and authorization binding",
            "Dispatch reevaluation after blockers clear",
            "Output byte verification before evidence",
        ],
    }


class RegisteredInputCatalog:
    """Server-owned input files. Replies contain roles and digests only."""

    def __init__(self, files: Mapping[str, Path], *, configuration: str = "configured") -> None:
        self._files = {role: Path(files[role]) for role in _ROLES if role in files}
        self._configuration = configuration

    def registration_status(self) -> str:
        """Why these files are or are not an input identity. This does not trust a stated hash."""

        if self._configuration != "configured":
            return self._configuration
        if self.identities() is None:
            return "input_files_unverified"
        return "verified"

    def identities(self) -> dict[str, Any] | None:
        if any(role not in self._files for role in _ROLES):
            return None
        flags = _open_flags()
        if flags is None:
            return None
        try:
            target = _read_regular(self._files["target_structure"], flags)
            settings = _read_regular(self._files["settings"], flags)
            profile = _read_regular(self._files["advanced_profile"], flags)
            canonical = _canonical_profile(profile)
        except (OSError, ValueError, TypeError):
            return None
        if target is None or settings is None or canonical is None:
            return None
        target_sha = hashlib.sha256(target).hexdigest()
        settings_sha = hashlib.sha256(settings).hexdigest()
        advanced_sha = hashlib.sha256(canonical).hexdigest()
        manifest = hashlib.sha256(f"{target_sha}\n{settings_sha}\n{advanced_sha}".encode()).hexdigest()
        try:
            verified = verify_pdl1_inputs(
                Pdl1ValidatedInputIdentity(
                    manifest_digest=manifest,
                    target_structure_sha256=target_sha,
                    settings_sha256=settings_sha,
                    advanced_profile_sha256=advanced_sha,
                    argv_policy_version=ARGV_POLICY_VERSION,
                    directory_policy=DIRECTORY_POLICY,
                    environment_policy=ENVIRONMENT_POLICY,
                ),
                Pdl1InputLocations(
                    target_structure=str(self._files["target_structure"]),
                    settings=str(self._files["settings"]),
                    advanced_profile=str(self._files["advanced_profile"]),
                ),
                Pdl1InputLimits(
                    target_structure_bytes=len(target),
                    settings_bytes=len(settings),
                    advanced_profile_bytes=len(profile),
                    canonical_advanced_profile_bytes=len(canonical),
                ),
            )
        except ApplicationContractError:
            return None
        selection = Pdl1InputSelection.from_verified(verified).to_dict()
        sequence_limits = _sequence_limits(profile)
        artifacts = [
            {
                "role": "target_structure",
                "artifact_id": "registered_target_structure",
                "sha256": verified.target_structure_sha256,
                "verified": True,
            },
            {
                "role": "settings",
                "artifact_id": "registered_settings",
                "sha256": verified.settings_sha256,
                "verified": True,
            },
            {
                "role": "advanced_profile",
                "artifact_id": "registered_advanced_profile",
                "sha256": verified.advanced_profile_sha256,
                "verified": True,
            },
        ]
        recorded = {"artifacts": artifacts, "input_selection": selection}
        if sequence_limits is not None:
            recorded["sequence_limits"] = sequence_limits
        hotspot = _recorded_hotspot(settings)
        if hotspot is not None:
            recorded["hotspot_assumption"] = hotspot
        return recorded

    def verified_paths(self) -> dict[str, Path] | None:
        if self.identities() is None:
            return None
        return dict(self._files)

    def verified_target_bytes(self) -> tuple[str, bytes] | None:
        """Return the registered target.pdb and its hash. A stated hash is not accepted."""

        flags = _open_flags()
        if flags is None or "target_structure" not in self._files:
            return None
        target = _read_regular(self._files["target_structure"], flags)
        identities = self.identities()
        if target is None or identities is None:
            return None
        digest = hashlib.sha256(target).hexdigest()
        artifacts = identities.get("artifacts")
        if not isinstance(artifacts, list):
            return None
        expected = next(
            (
                item.get("sha256")
                for item in artifacts
                if isinstance(item, dict) and item.get("role") == "target_structure"
            ),
            None,
        )
        if expected != digest:
            return None
        return digest, target


def open_registered_input_catalog(
    environ: Mapping[str, str] | None = None,
) -> RegisteredInputCatalog:
    """Build the startup catalog. An empty root stays unverified.

    The returned object does not contain professor-supplied digests.
    Hashes are computed later from the three files in the directory.
    """

    env = os.environ if environ is None else environ
    raw = env.get(INPUT_ROOT_ENV, "").strip()
    if raw == "":
        return RegisteredInputCatalog({}, configuration="input_root_unconfigured")
    if not os.path.isabs(raw) or any(char in raw for char in "\x00\n\r"):
        return RegisteredInputCatalog({}, configuration="input_root_invalid")
    root = Path(raw)
    files = {role: root / name for role, name in _ROLE_FILES.items()}
    return RegisteredInputCatalog(files)


@dataclass
class _Plan:
    plan_id: str
    conversation_id: str
    revision: int
    fields: dict[str, Any]
    questions: list[dict[str, str]]
    approval: dict[str, Any] | None = None
    run_id: str | None = None
    authorization_id: str | None = None
    authorization_session_id: str | None = None
    authorization_binding_hash: str | None = None
    outputs: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] | None = None
    events: list[dict[str, str]] = field(default_factory=list)
    dispatches: dict[str, dict[str, Any]] = field(default_factory=dict)
    approval_history: list[dict[str, Any]] = field(default_factory=list)
    revision_history: list[dict[str, Any]] = field(default_factory=list)


class GenerationPlanService:
    """Plan record. A sqlite store survives a process restart."""

    def __init__(
        self,
        runs: object | None = None,
        *,
        store: SQLiteGenerationPlanStore | None = None,
        inputs: RegisteredInputCatalog | None = None,
        authorizations: object | None = None,
        session_id: str | None = None,
        demonstration: object | None = None,
    ) -> None:
        self._runs = runs
        self._store = store
        self._inputs = inputs
        self._authorizations = authorizations
        self._session_id = session_id
        self._demonstration = demonstration
        self._plans: dict[str, _Plan] = {}
        self._by_conversation: dict[str, str] = {}
        if store is not None:
            for document in store.load_all():
                plan = _from_document(document)
                if plan is None:
                    continue
                self._plans[plan.plan_id] = plan
                self._by_conversation[plan.conversation_id] = plan.plan_id

    def close(self) -> None:
        if self._store is not None:
            self._store.close()

    def published(self, plan_id: str) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id)
        if plan is None:
            return None
        return self._published(plan)

    def verified_input_paths(self) -> dict[str, Path] | None:
        if self._inputs is None:
            return None
        found = self._inputs.verified_paths()
        if not isinstance(found, dict) or not found:
            return None
        return found

    def server_execution(self) -> dict[str, Any]:
        """Current process capability. A saved journey field is not consulted."""

        configured = self._demonstration is not None
        return {
            "mode": "apptainer-demonstration" if configured else "disabled",
            "demonstration_configured": configured,
        }

    def draft(self, conversation_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        identities = self._live_inputs()
        status = None if self._inputs is None else self._inputs.registration_status()
        current = self._plans.get(self._by_conversation.get(conversation_id, ""))
        previous = None if current is None else current.fields
        user_requests = _user_requests(previous, proposal.get("request_text"))
        merged = _preserve_explicit(previous, proposal)
        merged["user_requests"] = user_requests
        merged["request_text"] = "\n".join(user_requests)
        fields, questions = _fields(
            merged,
            identities,
            registration_status=status,
            latest_user_request=user_requests[-1] if user_requests else "",
        )
        if current is not None and canonical_digest(_document(current.fields, current.questions)) == canonical_digest(
            _document(fields, questions)
        ):
            return self._published(current)
        if current is None:
            plan = _Plan(
                plan_id=f"plan_{uuid4().hex}",
                conversation_id=conversation_id,
                revision=1,
                fields=fields,
                questions=questions,
            )
            plan.events.append(_event("drafted", "A generation plan was recorded."))
            self._plans[plan.plan_id] = plan
            self._by_conversation[conversation_id] = plan.plan_id
            self._persist(plan)
            return self._published(plan)
        current.revision += 1
        current.fields = fields
        current.questions = questions
        if isinstance(current.approval, dict):
            current.approval_history.append(dict(current.approval))
        current.approval = None
        current.run_id = None
        current.authorization_id = None
        current.authorization_session_id = None
        current.authorization_binding_hash = None
        current.outputs = []
        current.evidence = None
        current.dispatches = {}
        current.events.append(
            _event(
                "revised",
                "The latest user request was recorded. Omitted explicit settings were kept. "
                "Approval was cleared. No run was started.",
            )
        )
        self._persist(current)
        return self._published(current)

    def plans_for_design_request(self, request_id: str) -> list[dict[str, Any]]:
        """Saved drafts for one design request. This does not create or revise a plan."""

        found: list[dict[str, Any]] = []
        for plan in self._plans.values():
            binding = _design_request_binding(plan.conversation_id)
            if binding is None or binding[0] != request_id:
                continue
            found.append(_with_design_binding(self._published(plan), binding))
        found.sort(
            key=lambda item: (int(item["design_request_revision"]), str(item["plan_id"])),
            reverse=True,
        )
        return found

    def open_design_request_plan(
        self, request_id: str, revision: int, proposal: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Return the plan for this request revision, creating it only when absent."""

        key = design_request_conversation(request_id, revision)
        current = self._plans.get(self._by_conversation.get(key, ""))
        if current is not None:
            return _with_design_binding(self._published(current), (request_id, revision)), False
        created = self.draft(key, proposal)
        return _with_design_binding(created, (request_id, revision)), True

    def revise_parameters(
        self,
        request_id: str,
        request_revision: int,
        plan_id: str,
        expected_plan_revision: int,
        scientific: dict[str, Any],
        technical: dict[str, Any],
        adopted_origins: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Record parameter choices on this plan. The design request stays put."""

        plan = self._plans.get(plan_id)
        binding = None if plan is None else _design_request_binding(plan.conversation_id)
        if plan is None or binding != (request_id, request_revision):
            return {"status": "plan_absent"}, False
        if plan.revision != expected_plan_revision:
            return {"status": "plan_revision_conflict", "revision": plan.revision}, False
        if not scientific and not technical:
            return self._published(plan), False
        updates, reason = _parameter_updates(scientific, technical)
        if reason is not None or updates is None:
            return {"status": "rejected", "reason": reason or "rejected"}, False
        fields = copy.deepcopy(plan.fields)
        questions = [dict(item) for item in plan.questions]
        _apply_parameter_updates(fields, questions, updates, self._live_inputs(), adopted_origins or {})
        if canonical_digest(_document(fields, questions)) == canonical_digest(
            _document(plan.fields, plan.questions)
        ):
            return self._published(plan), False
        plan.revision_history.append(_revision_snapshot(plan))
        if isinstance(plan.approval, dict):
            plan.approval_history.append(dict(plan.approval))
        plan.revision += 1
        plan.fields = fields
        plan.questions = questions
        plan.approval = None
        plan.run_id = None
        plan.authorization_id = None
        plan.authorization_session_id = None
        plan.authorization_binding_hash = None
        plan.outputs = []
        plan.evidence = None
        plan.dispatches = {}
        plan.events.append(
            _event(
                "parameters_revised",
                "Plan parameters were recorded on a new revision. "
                "The design request text was kept. Approval and execution permission were cleared. "
                "No run was started.",
            )
        )
        self._persist(plan)
        return self._published(plan), True

    def refine(self, plan_id: str, change_note: str) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if plan is None:
            return {"status": "plan_absent", "approved": False, "execution_connected": False}
        note = change_note.strip()
        if not note:
            return self._published(plan, status="refinement_note_required")
        self.draft(plan.conversation_id, {"request_text": note})
        stored = self._plans[plan_id]
        stored.events.append(
            _event("refinement_drafted", "A refinement draft was recorded. No run was started.")
        )
        self._persist(stored)
        return self._published(stored)

    def approve(
        self,
        *,
        plan_id: str,
        revision: int,
        reviewer_id: str,
        reason: str,
        idempotency_key: str,
        expected_digest: str | None = None,
        require_execution_readiness: bool = True,
    ) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if plan is None or not reviewer_id.strip() or not reason.strip() or not idempotency_key.strip():
            return {"status": "approval_rejected", "approved": False, "execution_connected": False}
        digest = _digest(plan)
        if plan.revision != revision or (expected_digest is not None and expected_digest != digest):
            return self._published(plan, status="approval_rejected")
        ready = (
            not plan.questions
            and plan.fields.get("input_hashes_verified_against_files") is True
            and not _execution_integration_pending(plan.fields)
        )
        if require_execution_readiness and not ready:
            return self._published(plan, status="approval_rejected")
        reviewer = reviewer_id.strip()
        stated_reason = reason.strip()
        if (
            not require_execution_readiness
            and plan.approval is not None
            and plan.approval.get("revision") == revision
            and plan.approval.get("digest") == digest
        ):
            same_reviewer = plan.approval.get("reviewer_id") == reviewer
            same_reason = plan.approval.get("reason") == stated_reason
            same = same_reviewer and same_reason
            if same:
                return self._published(plan)
            return self._published(plan, status="idempotency_conflict")
        content = canonical_digest(
            {
                "plan_id": plan_id,
                "revision": revision,
                "digest": digest,
                "reviewer_id": reviewer,
                "reason": stated_reason,
            }
        )
        previous = plan.dispatches.get(f"approval:{idempotency_key}")
        if previous is not None:
            if previous.get("content") != content:
                return self._published(plan, status="idempotency_conflict")
            return self._published(plan)
        if (
            require_execution_readiness
            and isinstance(plan.approval, dict)
            and plan.approval.get("scope") == "recorded_plan"
        ):
            plan.approval_history.append(dict(plan.approval))
        plan.approval = {
            "revision": revision,
            "digest": digest,
            "reviewer_id": reviewer,
            "reason": stated_reason,
            "recorded_at": _stamp(),
            "source": "human_review",
        }
        if not require_execution_readiness:
            plan.approval["scope"] = "recorded_plan"
            plan.approval["execution_authorized"] = False
            plan.approval["scientific_validation_established"] = False
            plan.approval["launches_compute"] = False
        plan.dispatches[f"approval:{idempotency_key}"] = {"content": content}
        plan.events.append(_event("approval_recorded", "A person approved this plan revision."))
        self._persist(plan)
        return self._published(plan)

    def bind_authorization(
        self,
        *,
        plan_id: str,
        revision: int,
        authorization_id: str,
        reviewer_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Record a reference to an authorization the existing service already holds.

        This does not call authorize(). Plan approval remains a separate fact.
        """

        plan = self._plans.get(plan_id)
        if (
            plan is None
            or not authorization_id.strip()
            or not reviewer_id.strip()
            or not reason.strip()
            or not idempotency_key.strip()
        ):
            return {"status": "authorization_rejected", "approved": False, "execution_connected": False}
        if plan.revision != revision or plan.approval is None or plan.approval["digest"] != _digest(plan):
            return self._published(plan, status="approval_required")
        observed = self._observed_authorization(authorization_id.strip())
        if observed is None:
            return self._published(plan, status="scientific_authorization_required")
        content = canonical_digest(
            {
                "plan_id": plan_id,
                "revision": revision,
                "digest": _digest(plan),
                "authorization_id": authorization_id.strip(),
                "reviewer_id": reviewer_id.strip(),
                "reason": reason.strip(),
            }
        )
        previous = plan.dispatches.get(f"authorization:{idempotency_key}")
        if previous is not None:
            if previous.get("content") != content:
                return self._published(plan, status="idempotency_conflict")
            return self._published(plan)
        plan.authorization_id = authorization_id.strip()
        plan.authorization_session_id = observed["session_id"]
        plan.authorization_binding_hash = observed["binding_hash"]
        plan.dispatches[f"authorization:{idempotency_key}"] = {"content": content}
        plan.events.append(
            _event(
                "authorization_bound",
                "A person bound an existing scientific authorization. Plan approval did not grant it.",
            )
        )
        self._persist(plan)
        return self._published(plan)

    def dispatch(self, *, plan_id: str, revision: int, idempotency_key: str) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if plan is None or not idempotency_key.strip():
            return {"status": "dispatch_rejected", "approved": False, "execution_connected": False}
        if plan.revision != revision or plan.approval is None or plan.approval["digest"] != _digest(plan):
            return self._published(plan, status="approval_required", blockers=["approval_required"])
        if plan.approval.get("scope") == "recorded_plan":
            return self._published(
                plan,
                status="dispatch_blocked",
                blockers=["journey_execution_not_connected"],
                run_id=None,
                execution_connected=False,
            )
        content = canonical_digest({"plan_id": plan_id, "revision": revision, "digest": _digest(plan)})
        key = f"dispatch:{idempotency_key}"
        cached = plan.dispatches.get(key)
        if cached is not None and cached.get("content") != content:
            return self._published(plan, status="idempotency_conflict")
        if plan.run_id:
            reply = {
                "status": "dispatch_referred",
                "blockers": [],
                "run_id": plan.run_id,
                "execution_connected": False,
            }
            return self._published(plan, **reply)
        if cached is not None and cached["reply"].get("status") != "dispatch_blocked":
            return self._published(plan, **cached["reply"])
        blockers = self._current_blockers(plan)
        if cached is not None and cached["reply"].get("blockers") == blockers:
            return self._published(plan, **cached["reply"])
        if blockers:
            reply = {
                "status": "dispatch_blocked",
                "blockers": blockers,
                "run_id": None,
                "execution_connected": False,
            }
            plan.events.append(_event("dispatch_blocked", "Dispatch stopped because a gate is unmet."))
        else:
            created = self._runs.create(  # type: ignore[union-attr]
                session_id=plan.authorization_session_id or plan.conversation_id,
                authorization_id=plan.authorization_id,
                idempotency_key=idempotency_key,
            )
            run = getattr(created, "run", None)
            plan.run_id = None if run is None else getattr(run, "run_id", None)
            reply = {
                "status": "dispatch_referred",
                "blockers": [],
                "run_id": plan.run_id,
                "run_status": None if run is None else getattr(run, "status", None),
                "execution_connected": False,
            }
            plan.events.append(
                _event("run_referenced", "The approved plan was referred to the scientific run service.")
            )
        plan.dispatches[key] = {"content": content, "reply": reply}
        self._persist(plan)
        return self._published(plan, **reply)

    def note_verified_run(self, plan_id: str, run: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = self._plans.get(plan_id)
        if plan is None:
            return {"status": "plan_absent"}
        verified = _verified_outputs(plan, run, self._runs)
        if verified is None:
            return self._published(plan, status="outputs_unverified")
        plan.outputs = verified["outputs"]
        plan.evidence = verified["evidence"]
        plan.events.append(
            _event("outputs_verified", "Verified run artifacts were linked. This is not a new measurement.")
        )
        self._persist(plan)
        public = self._published(plan)
        public["historical_manual_attempt"] = "attempt.2LUTUOX4"
        public["launched_by_this_workflow"] = plan.run_id is not None
        public["evidence"] = plan.evidence
        return public

    def _published(self, plan: _Plan, **extra: Any) -> dict[str, Any]:
        payload = _public(plan)
        payload["input_registration"] = self._plan_input_status(plan)
        payload.update(extra)
        return payload

    def _plan_input_status(self, plan: _Plan) -> str:
        if plan.fields.get("input_identity_mismatch") is True:
            return "input_identity_mismatch"
        if plan.fields.get("input_hashes_verified_against_files") is True:
            live = self._live_inputs()
            stored = plan.fields.get("input_selection")
            if live is None or stored != live["input_selection"]:
                return "input_files_changed"
            return "verified"
        status = None if self._inputs is None else self._inputs.registration_status()
        if status in {None, "verified"}:
            return "input_files_unverified" if status == "verified" else "input_root_unconfigured"
        return status

    def _current_blockers(self, plan: _Plan) -> list[str]:
        blockers = list(self._gates())
        blockers.extend(self._input_blockers(plan))
        authorization = self._authorization_blocker(plan)
        if authorization is not None:
            blockers.append(authorization)
        return blockers

    def _input_blockers(self, plan: _Plan) -> list[str]:
        if not plan.fields.get("input_hashes_verified_against_files"):
            return ["input_identity_unverified"]
        live = self._live_inputs()
        stored = plan.fields.get("input_selection")
        if live is None or stored != live["input_selection"]:
            return ["input_identity_rejected"]
        return []

    def _authorization_blocker(self, plan: _Plan) -> str | None:
        if not plan.authorization_id or self._authorizations is None:
            return "scientific_authorization_required"
        observed = self._observed_authorization(plan.authorization_id)
        if observed is None or observed["binding_hash"] != plan.authorization_binding_hash:
            return "scientific_authorization_required"
        return None

    def _observed_authorization(self, authorization_id: str) -> dict[str, str] | None:
        reader = getattr(self._authorizations, "read", None)
        if not callable(reader):
            return None
        session_id = self._session_id or ""
        try:
            reply = reader(session_id=session_id, authorization_id=authorization_id)
        except Exception:  # noqa: BLE001 — a failed read is not an authorization
            return None
        record = getattr(reply, "authorization", None)
        if getattr(reply, "status", None) != "authorization_observed" or record is None:
            return None
        if getattr(record, "status", None) != "authorized":
            return None
        if getattr(record, "session_id", None) != session_id or not session_id:
            return None
        if getattr(record, "authorization_id", authorization_id) != authorization_id:
            return None
        binding = getattr(record, "binding_hash", None)
        if not isinstance(binding, str) or not binding:
            return None
        return {"session_id": session_id, "binding_hash": binding}

    def _live_inputs(self) -> dict[str, Any] | None:
        if self._inputs is None:
            return None
        return self._inputs.identities()

    def recorded_input_facts(self) -> dict[str, Any]:
        """Values read from verified inputs. Missing values stay missing."""

        identities = self._live_inputs()
        if identities is None:
            return {"verified": False}
        hotspot = identities.get("hotspot_assumption")
        limits = identities.get("sequence_limits")
        return {
            "verified": True,
            "target_identity": "PDL1",
            "hotspot_assumption": hotspot if isinstance(hotspot, str) and hotspot.strip() else None,
            "sequence_limits": dict(limits) if isinstance(limits, dict) else None,
        }

    def launch_apptainer_demonstration(
        self,
        *,
        plan_id: str,
        revision: int,
        reviewer_id: str,
        reason: str,
        idempotency_key: str,
        gpu_index: int,
        attempt_id: str,
        attempt_directory: str,
    ) -> dict[str, Any]:
        """Explicit human launch. Approval of the plan does not call this."""

        from cellertz.application.apptainer_demonstration import (
            PROFILE_ID,
            SIF_PATH,
            SIF_SHA256,
            DemonstrationLaunch,
            explicit_gpu_index,
        )

        refused = {"status": "launch_rejected", "launched": False, "approved": False, "profile_id": PROFILE_ID}
        if self._demonstration is None:
            return {**refused, "status": "demonstration_not_configured"}
        plan = self._plans.get(plan_id)
        if plan is None or not reviewer_id.strip() or not reason.strip() or not idempotency_key.strip():
            return refused
        approved = (
            plan.approval is not None
            and plan.revision == revision
            and plan.approval["digest"] == _digest(plan)
            and plan.approval["source"] == "human_review"
            and plan.approval["reviewer_id"] == reviewer_id.strip()
        )
        if not approved:
            return {**refused, "status": "approval_required"}
        if plan.approval is not None and plan.approval.get("scope") == "recorded_plan":
            return {**refused, "status": "journey_execution_not_connected", "approved": True}
        content = canonical_digest(
            {
                "attempt_directory": attempt_directory,
                "attempt_id": attempt_id,
                "digest": _digest(plan),
                "gpu_index": gpu_index,
                "plan_id": plan_id,
                "revision": revision,
            }
        )
        key = f"apptainer-demonstration:{idempotency_key}"
        previous = plan.dispatches.get(key)
        if previous is not None:
            if previous.get("content") != content:
                return {**refused, "status": "idempotency_conflict"}
            return previous["reply"]
        identities = self._live_inputs()
        paths = None if self._inputs is None else self._inputs.verified_paths()
        if identities is None or paths is None:
            return {**refused, "status": "input_identity_unverified"}
        assigned = explicit_gpu_index(plan.fields.get("gpu"))
        if assigned is None or assigned != gpu_index:
            return {**refused, "status": "gpu_index_mismatch", "gpu_index": gpu_index}
        launch = getattr(self._demonstration, "launch", None)
        if not callable(launch):
            return {**refused, "status": "demonstration_not_configured"}
        reply = launch(
            DemonstrationLaunch(
                fields=plan.fields,
                request_text=str(plan.fields.get("request_text") or ""),
                approval_source=plan.approval["source"] if plan.approval else None,
                approved=True,
                input_selection=plan.fields.get("input_selection"),
                live_input_selection=identities["input_selection"],
                target_path=paths["target_structure"],
                settings_path=paths["settings"],
                advanced_profile_path=paths["advanced_profile"],
                attempt_id=attempt_id,
                attempt_directory=Path(attempt_directory),
                gpu_index=gpu_index,
                apptainer_bin=Path(os.environ.get("CELLERTZ_APPTAINER_BIN", "/usr/bin/apptainer")),
                sif_path=Path(SIF_PATH),
                expected_sif_sha256=SIF_SHA256,
            )
        )
        plan.dispatches[key] = {"content": content, "reply": reply}
        plan.events.append(_event("apptainer_demonstration", "A person requested the Apptainer demonstration."))
        self._persist(plan)
        return reply

    def launch_explicit_run(
        self,
        *,
        plan_id: str,
        revision: int,
        digest: str,
        idempotency_key: str,
        gpu_index: int,
        attempt_id: str,
        attempt_directory: str,
        n_designs: int,
    ) -> dict[str, Any]:
        """Launch one displayed revision. This does not invent an approval."""

        from cellertz.application.apptainer_demonstration import (
            PROFILE_ID,
            SIF_PATH,
            SIF_SHA256,
            DemonstrationLaunch,
            explicit_gpu_index,
        )

        refused = {
            "status": "launch_rejected",
            "launched": False,
            "automatic_retry": False,
            "profile_id": PROFILE_ID,
        }
        if self._demonstration is None:
            return {**refused, "status": "demonstration_not_configured"}
        plan = self._plans.get(plan_id)
        if plan is None or plan.revision != revision or _digest(plan) != digest or not idempotency_key.strip():
            return {**refused, "status": "revision_mismatch"}
        for previous in plan.dispatches.values():
            if (
                isinstance(previous, dict)
                and previous.get("kind") == "explicit_run"
                and previous.get("revision") == revision
                and previous.get("digest") == digest
            ):
                reply = previous.get("reply")
                if isinstance(reply, dict) and reply.get("status") != "launch_failed":
                    return reply
        paths = self.verified_input_paths()
        identities = self._live_inputs()
        if paths is None or identities is None:
            return {**refused, "status": "input_identity_unverified"}
        if explicit_gpu_index(plan.fields.get("gpu")) != gpu_index:
            return {**refused, "status": "gpu_index_mismatch"}
        launch = getattr(self._demonstration, "launch", None)
        if not callable(launch):
            return {**refused, "status": "demonstration_not_configured"}
        reply = launch(
            DemonstrationLaunch(
                fields=plan.fields,
                request_text=str(plan.fields.get("request_text") or ""),
                approval_source="explicit_run",
                approved=True,
                input_selection=plan.fields.get("input_selection"),
                live_input_selection=identities["input_selection"],
                target_path=paths["target_structure"],
                settings_path=paths["settings"],
                advanced_profile_path=paths["advanced_profile"],
                attempt_id=attempt_id,
                attempt_directory=Path(attempt_directory),
                gpu_index=gpu_index,
                apptainer_bin=Path(os.environ.get("CELLERTZ_APPTAINER_BIN", "/usr/bin/apptainer")),
                sif_path=Path(SIF_PATH),
                expected_sif_sha256=SIF_SHA256,
                n_designs=n_designs,
            )
        )
        if not isinstance(reply, dict):
            reply = {**refused, "status": "launch_failed"}
        reply = {**reply, "automatic_retry": False}
        plan.dispatches[f"explicit-run:{idempotency_key}:{attempt_id}"] = {
            "kind": "explicit_run",
            "revision": revision,
            "digest": digest,
            "reply": reply,
        }
        plan.events.append(
            _event("explicit_run", "Run design was requested for this displayed revision.")
        )
        self._persist(plan)
        return reply

    def _gates(self) -> tuple[str, ...]:
        gates = getattr(self._runs, "dispatch_gates", None)
        if not callable(gates):
            return ("scientific_execution_disabled", "pdl1_worker_loss_deadline_unresolved")
        found = gates()
        if type(found) is not tuple:
            return ("scientific_execution_disabled",)
        return found

    def _persist(self, plan: _Plan) -> None:
        if self._store is None:
            return
        self._store.save(plan.plan_id, plan.conversation_id, _state(plan))


def _fields(
    proposal: dict[str, Any],
    identities: dict[str, Any] | None,
    *,
    registration_status: str | None = None,
    latest_user_request: str = "",
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    request_text = proposal.get("request_text") if isinstance(proposal.get("request_text"), str) else ""
    user_requests = [
        item for item in proposal.get("user_requests") or [] if isinstance(item, str) and item.strip()
    ]
    fields: dict[str, Any] = {"request_text": request_text, "user_requests": user_requests}
    missing: list[str] = []
    for name in ("target_identity", "hotspot_assumption", "gpu"):
        value = proposal.get(name)
        if isinstance(value, str) and value.strip():
            fields[name] = value.strip()
        else:
            missing.append(name)
    if (
        fields.get("target_identity")
        and identities is not None
        and fields["target_identity"] != "PDL1"
    ):
        fields["target_identity_conflict"] = True
    recorded_hotspot = None if identities is None else identities.get("hotspot_assumption")
    if (
        isinstance(recorded_hotspot, str)
        and recorded_hotspot.strip()
        and fields.get("hotspot_assumption")
        and fields["hotspot_assumption"] != recorded_hotspot
    ):
        fields["hotspot_assumption_conflict"] = True
    policy = proposal.get("filter_policy")
    request_text = fields["request_text"]
    if isinstance(policy, str) and policy.strip():
        stated = policy.strip()
        professor_stated = stated != "no_filters" or _no_filters_consented(request_text)
        if professor_stated:
            fields["filter_policy"] = stated
            fields["filter_policy_explicit"] = True
            fields["filter_policy_establishes_scientific_quality"] = False
            fields["filter_policy_is_technical_smoke_test"] = "technical-smoke-test" in request_text
        else:
            missing.append("filter_policy")
    else:
        missing.append("filter_policy")
    for name in ("binder_length", "seed", "cpu_count", "memory_mib", "execution_deadline_seconds"):
        value = proposal.get(name)
        accepted = (
            _bootstrap_seed(value) is not None
            if name == "seed"
            else type(value) is int and not isinstance(value, bool) and value >= 1
        )
        if accepted:
            fields[name] = value
        else:
            missing.append(name)
    _record_provenance(fields, proposal)
    stated = _stated_artifacts(proposal.get("input_artifacts"))
    if identities is None:
        missing.append("input_artifacts")
        fields["input_hashes_verified_against_files"] = False
        if stated:
            fields["stated_input_artifacts"] = stated
    else:
        stated_map = {item["role"]: item["sha256"] for item in stated or []}
        known = {item["role"] for item in identities["artifacts"]}
        mismatch = any(role not in known or stated_map[role] != _role_hash(identities, role) for role in stated_map)
        if mismatch:
            missing.append("input_artifacts")
            fields["input_identity_mismatch"] = True
            fields["input_hashes_verified_against_files"] = False
            fields["stated_input_artifacts"] = stated
        else:
            fields["input_artifacts"] = identities["artifacts"]
            fields["input_selection"] = identities["input_selection"]
            fields["input_hashes_verified_against_files"] = True
    questions = [{"field": name, "question": _input_question(name, fields, registration_status)} for name in missing]
    if fields.get("target_identity_conflict") is True:
        questions.append(
            {
                "field": "target_identity",
                "question": (
                    "The stated target conflicts with the registered PDL1 inputs. "
                    "The stated target was kept."
                ),
            }
        )
    if fields.get("hotspot_assumption_conflict") is True:
        questions.append(
            {
                "field": "hotspot_assumption",
                "question": (
                    "The stated hotspot conflicts with the verified settings. "
                    "The stated hotspot was kept."
                ),
            }
        )
    verified_limits = None if identities is None else identities.get("sequence_limits")
    stated_limits = _stated_sequence_limits(proposal.get("sequence_limits"))
    if stated_limits is not None:
        if isinstance(verified_limits, dict) and stated_limits == verified_limits:
            fields["sequence_limits"] = dict(verified_limits)
            source = proposal.get("sequence_limits_source")
            provenance = proposal.get("sequence_limits_provenance")
            if source in {
                "technical_demonstration_profile",
                "verified_input",
                "professor",
                "verified_advanced_profile",
            }:
                fields["sequence_limits_source"] = source
            if provenance in {"user", "preserved"}:
                fields["sequence_limits_provenance"] = provenance
        else:
            questions.append(
                {
                    "field": "sequence_limits",
                    "question": (
                        "The stated sequence limits do not match the verified advanced profile. "
                        "They were not recorded."
                    ),
                }
            )
    limits = verified_limits
    if stated_limits is None and _confirms_sequence_limits(latest_user_request):
        if isinstance(limits, dict):
            fields["sequence_limits"] = dict(limits)
            fields["sequence_limits_source"] = "verified_advanced_profile"
        else:
            questions.append(
                {
                    "field": "sequence_limits",
                    "question": (
                        "The verified advanced profile does not record max_trajectories, "
                        "num_seqs, and max_mpnn_sequences."
                    ),
                }
            )
    return fields, questions


def _user_requests(previous: dict[str, Any] | None, request: object) -> list[str]:
    stored: list[str] = []
    if isinstance(previous, dict):
        prior = previous.get("user_requests")
        if isinstance(prior, list):
            stored = [item for item in prior if isinstance(item, str) and item.strip()]
        elif isinstance(previous.get("request_text"), str) and previous["request_text"].strip():
            stored = [previous["request_text"]]
    text = request.strip() if isinstance(request, str) else ""
    if text and text not in stored:
        stored.append(text)
    return stored


def _preserve_explicit(previous: dict[str, Any] | None, proposal: dict[str, Any]) -> dict[str, Any]:
    merged = {key: value for key, value in proposal.items() if key != "unresolved_questions"}
    if not isinstance(previous, dict):
        return merged
    for name in _PRESERVED_SETTINGS:
        incoming = proposal.get(name)
        omitted = name not in proposal or incoming is None or incoming == ""
        if omitted and previous.get(name) not in (None, ""):
            merged[name] = previous[name]
    return merged


def _no_filters_consented(request_text: str) -> bool:
    """Consent is the saved user text, including the technical smoke-test token."""

    return "no_filters" in request_text and "technical-smoke-test" in request_text


def _confirms_sequence_limits(text: str) -> bool:
    lowered = text.casefold()
    return "profile" in lowered and all(name in lowered for name in _SEQUENCE_LIMITS)


def _record_provenance(fields: dict[str, Any], proposal: dict[str, Any]) -> None:
    for name in _SETTING_NAMES:
        if name not in fields:
            continue
        provenance = proposal.get(f"{name}_provenance")
        source = proposal.get(f"{name}_source")
        if provenance in {"user", "preserved"}:
            fields[f"{name}_provenance"] = provenance
        if source in {"technical_demonstration_profile", "verified_input", "professor"}:
            fields[f"{name}_source"] = source


def _stated_sequence_limits(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict) or set(value) != set(_SEQUENCE_LIMITS):
        return None
    limits: dict[str, int] = {}
    for name in _SEQUENCE_LIMITS:
        item = value[name]
        if type(item) is not int or isinstance(item, bool) or item < 1:
            return None
        limits[name] = item
    return limits


def _recorded_hotspot(payload: bytes) -> str | None:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("target_hotspot_residues")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _approval_history(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _revision_history(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _bootstrap_seed(value: object) -> int | None:
    if type(value) is not int or isinstance(value, bool):
        return None
    if not BOOTSTRAP_SEED_MINIMUM <= value <= BOOTSTRAP_SEED_MAXIMUM:
        return None
    return value


def _sequence_limits(payload: bytes) -> dict[str, int] | None:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return bounded_sequence_limits(parsed)


def _input_question(name: str, fields: dict[str, Any], registration_status: str | None) -> str:
    if name != "input_artifacts":
        return _QUESTIONS[name]
    if fields.get("input_identity_mismatch") is True:
        return _INPUT_QUESTIONS["input_identity_mismatch"]
    key = registration_status or "input_root_unconfigured"
    if key == "verified":
        key = "input_files_unverified"
    return _INPUT_QUESTIONS[key]


def _role_hash(identities: dict[str, Any], role: str) -> str:
    for item in identities["artifacts"]:
        if item["role"] == role:
            return str(item["sha256"])
    return ""


def _stated_artifacts(value: object) -> list[dict[str, str]] | None:
    if not isinstance(value, list) or not value:
        return None
    artifacts: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        role = item.get("role")
        digest = item.get("sha256")
        if not isinstance(role, str) or not role.strip() or not isinstance(digest, str):
            return None
        if _SHA.fullmatch(digest) is None:
            return None
        artifacts.append({"role": role.strip(), "sha256": digest})
    return artifacts


def _texts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _document(fields: dict[str, Any], questions: list[dict[str, str]]) -> dict[str, Any]:
    return {"fields": fields, "questions": questions}


def _digest(plan: _Plan) -> str:
    return canonical_digest({"revision": plan.revision, **_document(plan.fields, plan.questions)})


def _public(plan: _Plan) -> dict[str, Any]:
    approved = (
        plan.approval is not None
        and plan.approval["revision"] == plan.revision
        and plan.approval["digest"] == _digest(plan)
    )
    authorized = approved and bool(plan.authorization_id)
    return {
        "status": (
            "approved"
            if approved
            else "draft"
            if plan.questions or _execution_integration_pending(plan.fields)
            else "ready_for_review"
        ),
        "plan_id": plan.plan_id,
        "conversation_id": plan.conversation_id,
        "revision": plan.revision,
        "digest": _digest(plan),
        "fields": plan.fields,
        "questions": plan.questions,
        "approved": approved,
        "approval": plan.approval if approved else None,
        "approval_history": list(plan.approval_history),
        "revision_history": [dict(item) for item in plan.revision_history],
        "approval_source": "human_review" if approved else None,
        "plan_approval_grants_execution": False,
        "authorization_id": plan.authorization_id if authorized else None,
        "execution_authorized": False,
        "authorization_bound": authorized,
        "llm_approval": False,
        "run_id": plan.run_id,
        "outputs": plan.outputs,
        "evidence": plan.evidence,
        "events": plan.events,
        "execution_connected": False,
        "workflow_origin": "conversation_generation_plan",
        "historical_manual_attempt": "attempt.2LUTUOX4",
        "launched_by_this_workflow": False,
        "scientific_execution": "disabled",
    }


def _event(kind: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "at": _stamp(), "detail": detail}


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _state(plan: _Plan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "conversation_id": plan.conversation_id,
        "revision": plan.revision,
        "fields": plan.fields,
        "questions": plan.questions,
        "approval": plan.approval,
        "approval_history": plan.approval_history,
        "revision_history": plan.revision_history,
        "run_id": plan.run_id,
        "authorization_id": plan.authorization_id,
        "authorization_session_id": plan.authorization_session_id,
        "authorization_binding_hash": plan.authorization_binding_hash,
        "outputs": plan.outputs,
        "evidence": plan.evidence,
        "events": plan.events,
        "dispatches": plan.dispatches,
    }


def _from_document(document: dict[str, Any]) -> _Plan | None:
    plan_id = document.get("plan_id")
    conversation_id = document.get("conversation_id")
    revision = document.get("revision")
    fields = document.get("fields")
    questions = document.get("questions")
    if not isinstance(plan_id, str) or not isinstance(conversation_id, str):
        return None
    if type(revision) is not int or not isinstance(fields, dict) or not isinstance(questions, list):
        return None
    events = document.get("events")
    dispatches = document.get("dispatches")
    outputs = document.get("outputs")
    return _Plan(
        plan_id=plan_id,
        conversation_id=conversation_id,
        revision=revision,
        fields=fields,
        questions=questions,
        approval=document.get("approval") if isinstance(document.get("approval"), dict) else None,
        run_id=document.get("run_id") if isinstance(document.get("run_id"), str) else None,
        authorization_id=document.get("authorization_id") if isinstance(document.get("authorization_id"), str) else None,
        authorization_session_id=(
            document.get("authorization_session_id")
            if isinstance(document.get("authorization_session_id"), str)
            else None
        ),
        authorization_binding_hash=(
            document.get("authorization_binding_hash")
            if isinstance(document.get("authorization_binding_hash"), str)
            else None
        ),
        outputs=outputs if isinstance(outputs, list) else [],
        evidence=document.get("evidence") if isinstance(document.get("evidence"), dict) else None,
        events=events if isinstance(events, list) else [],
        dispatches=dispatches if isinstance(dispatches, dict) else {},
        approval_history=_approval_history(document.get("approval_history")),
        revision_history=_revision_history(document.get("revision_history")),
    )


def _verified_outputs(
    plan: _Plan, run: dict[str, Any] | None, runs: object | None
) -> dict[str, Any] | None:
    if not plan.run_id:
        return None
    if run is not None:
        if run.get("status") != "completed" or run.get("run_id") != plan.run_id:
            return None
        return _outputs_from_bodies(plan, run.get("artifacts"))
    loaded = _outputs_from_service(plan, runs)
    if loaded is None:
        return None
    return _outputs_from_bodies(plan, loaded)


def _outputs_from_service(plan: _Plan, runs: object | None) -> list[dict[str, Any]] | None:
    read = getattr(runs, "read", None)
    read_artifact = getattr(runs, "read_artifact", None)
    if not callable(read) or not callable(read_artifact):
        return None
    session_id = plan.authorization_session_id or plan.conversation_id
    reply = read(session_id=session_id, run_id=plan.run_id)
    stored = getattr(reply, "run", None)
    if stored is None or getattr(stored, "status", None) != "completed":
        return None
    if getattr(stored, "run_id", None) != plan.run_id:
        return None
    collected: list[dict[str, Any]] = []
    for item in getattr(stored, "artifacts", ()):
        artifact_id = getattr(item, "artifact_id", None)
        expected = getattr(item, "sha256", None)
        if not isinstance(artifact_id, str) or not isinstance(expected, str):
            return None
        record = read_artifact(session_id=session_id, run_id=plan.run_id, artifact_id=artifact_id)
        content = getattr(record, "content", None)
        filename = getattr(record, "filename", "")
        if type(content) is not bytes or hashlib.sha256(content).hexdigest() != expected:
            return None
        collected.append(
            {
                "kind": _KIND_BY_FILENAME.get(filename, ""),
                "artifact_id": artifact_id,
                "run_id": plan.run_id,
                "sha256": expected,
                "body": content,
            }
        )
    return collected


def _outputs_from_bodies(plan: _Plan, artifacts: object) -> dict[str, Any] | None:
    if not isinstance(artifacts, list) or not artifacts:
        return None
    found: dict[str, dict[str, Any]] = {}
    models: list[dict[str, Any]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            return None
        body = item.get("body")
        kind = item.get("kind")
        digest = item.get("sha256")
        artifact_id = item.get("artifact_id")
        if (
            type(body) is not bytes
            or len(body) < 1
            or not isinstance(kind, str)
            or not isinstance(digest, str)
            or not isinstance(artifact_id, str)
            or hashlib.sha256(body).hexdigest() != digest
            or item.get("run_id") not in (None, plan.run_id)
        ):
            return None
        found[kind] = {
            "artifact_id": artifact_id,
            "kind": kind,
            "sha256": digest,
            "run_id": plan.run_id,
        }
        if kind == "candidate_record":
            models = _models(body)
            if models is None:
                return None
    if any(kind not in found for kind in _REQUIRED_OUTPUT_KINDS):
        return None
    outputs = [found[kind] for kind in _REQUIRED_OUTPUT_KINDS]
    return {
        "outputs": outputs,
        "evidence": {
            "workflow_origin": "governed_generation_plan",
            "plan_id": plan.plan_id,
            "run_id": plan.run_id,
            "historical_manual_attempt": "attempt.2LUTUOX4",
            "launched_by_this_workflow": True,
            "representative_selection": None,
            "interface_quality": None,
            "models": models,
            "artifacts": outputs,
        },
    }


def _models(body: bytes) -> list[dict[str, Any]] | None:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    rows = parsed.get("models") if isinstance(parsed, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    models: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("model_id"), str) or not row["model_id"]:
            return None
        digest = row.get("sha256")
        if not isinstance(digest, str) or _SHA.fullmatch(digest) is None:
            return None
        models.append(
            {
                "model_id": row["model_id"],
                "sha256": digest,
                "contact_count": row.get("contact_count", None),
            }
        )
    return models


def _open_flags() -> int | None:
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")):
        return None
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC  # type: ignore[attr-defined]


def _read_regular(path: Path, flags: int) -> bytes | None:
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None
        chunks: list[bytes] = []
        remaining = MAX_INPUT_BYTES
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            return None
    except OSError:
        return None
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    if not payload:
        return None
    return payload


def _canonical_profile(payload: bytes) -> bytes | None:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        return canonical_advanced_profile_bytes(parsed)
    except (TypeError, ValueError):
        return None


def design_request_conversation(request_id: str, revision: int) -> str:
    return f"design-request:{request_id}:revision:{revision}"


def _design_request_binding(conversation_id: str) -> tuple[str, int] | None:
    marker = ":revision:"
    prefix = "design-request:"
    if not conversation_id.startswith(prefix) or marker not in conversation_id:
        return None
    request_id, revision_text = conversation_id[len(prefix) :].rsplit(marker, 1)
    if not request_id or not revision_text.isascii() or not revision_text.isdigit():
        return None
    revision = int(revision_text)
    if revision < 1:
        return None
    return request_id, revision


def _with_design_binding(plan: dict[str, Any], binding: tuple[str, int]) -> dict[str, Any]:
    bound = dict(plan)
    bound["design_request_id"] = binding[0]
    bound["design_request_revision"] = binding[1]
    bound["execution_authorized"] = False
    bound["execution_connected"] = False
    bound["scientific_execution"] = "disabled"
    return bound


_PLAN_PARAMETER_NAMES = (
    "target_identity",
    "binder_length",
    "hotspot_assumption",
    "filter_policy",
    "seed",
    "cpu_count",
    "memory_mib",
    "gpu",
    "execution_deadline_seconds",
)
_PROPOSED_WORKFLOW = (
    "Read the saved design request at this revision.",
    "Record only parameters present on that revision or verified by the server.",
    "Leave every other parameter pending.",
    "Stop. This draft does not approve, authorize, or start execution.",
)


def handle_design_request_plan(
    plans: GenerationPlanService,
    drafter: object,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    configuration: object | None = None,
) -> tuple[int, dict[str, Any]]:
    """Read or open one design-request plan. A read does not create a plan."""

    parsed = urlsplit(path)
    if parsed.path != "/api/generation-plan":
        return 404, _plan_absent()
    if method == "GET":
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"request_id"} or len(query["request_id"]) != 1:
            return 400, _plan_rejected()
        request_id = query["request_id"][0].strip()
        if not request_id:
            return 400, _plan_rejected()
        recorded = _recorded_draft(drafter, request_id, None)
        if recorded is None:
            return 200, _plan_catalog(request_id, None, None, [], service=plans)
        draft, message = recorded
        return 200, _plan_catalog(
            request_id,
            draft.revision,
            _request_summary(draft, message, configuration),
            plans.plans_for_design_request(request_id),
            service=plans,
        )
    if method != "POST":
        return 404, _plan_absent()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, _plan_rejected()
    if not isinstance(payload, dict):
        return 400, _plan_rejected()
    if _FORBIDDEN_PLAN_KEYS & set(payload):
        return 400, _plan_rejected()
    request_id = payload.get("request_id")
    revision = payload.get("revision")
    if not isinstance(request_id, str) or not request_id.strip() or type(revision) is not int or revision < 1:
        return 400, _plan_rejected()
    request_id = request_id.strip()
    recorded = _recorded_draft(drafter, request_id, None)
    if recorded is None:
        return 404, _plan_absent()
    draft, message = recorded
    if draft.revision != revision:
        return 409, _plan_conflict(request_id, draft.revision)
    proposal = _proposal_from_draft(draft, message)
    plan, created = plans.open_design_request_plan(request_id, revision, proposal)
    reply = _plan_catalog(
        request_id,
        draft.revision,
        _request_summary(draft, message, configuration),
        plans.plans_for_design_request(request_id),
        service=plans,
    )
    reply["status"] = "plan_recorded"
    reply["plan"] = plan
    reply["created"] = created
    return 200, reply


def handle_design_request_plan_revision(
    plans: GenerationPlanService,
    drafter: object,
    *,
    body: bytes,
    configuration: object | None = None,
) -> tuple[int, dict[str, Any]]:
    """Save plan parameters. This does not revise the design request or start a run."""

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, _plan_rejected()
    if not isinstance(payload, dict) or set(payload) - _REVISION_BODY_KEYS:
        rejected = _plan_rejected()
        rejected["reason"] = "rejected"
        return 400, rejected
    request_id = payload.get("request_id")
    request_revision = payload.get("request_revision")
    plan_id = payload.get("plan_id")
    expected = payload.get("expected_plan_revision")
    scientific = payload.get("scientific", {})
    technical = payload.get("technical", {})
    adopted = payload.get("adopted_origins", {})
    if (
        not isinstance(request_id, str)
        or not request_id.strip()
        or type(request_revision) is not int
        or request_revision < 1
        or not isinstance(plan_id, str)
        or not plan_id.strip()
        or type(expected) is not int
        or expected < 1
        or not isinstance(scientific, dict)
        or not isinstance(technical, dict)
        or not isinstance(adopted, dict)
    ):
        return 400, _plan_rejected()
    recorded = _recorded_draft(drafter, request_id.strip(), None)
    if recorded is None:
        return 404, _plan_absent()
    draft, message = recorded
    if draft.revision != request_revision:
        return 409, _plan_conflict(request_id.strip(), draft.revision)
    published, changed = plans.revise_parameters(
        request_id.strip(),
        request_revision,
        plan_id.strip(),
        expected,
        scientific,
        technical,
        adopted,
    )
    if published.get("status") == "plan_absent":
        return 404, _plan_absent()
    if published.get("status") == "plan_revision_conflict":
        reply = _plan_catalog(request_id.strip(), draft.revision, None, [])
        reply["status"] = "plan_revision_conflict"
        reply["expected_plan_revision"] = expected
        reply["plan_revision"] = published.get("revision")
        return 409, reply
    if published.get("status") == "rejected":
        rejected = _plan_rejected()
        rejected["reason"] = published.get("reason") or "rejected"
        if rejected["reason"] == "binder_length_rejected":
            rejected["recording_bound"] = "container_argv"
            rejected["bindcraft_capacity_recorded"] = False
        if rejected["reason"] == "seed_rejected":
            rejected["accepted_interval"] = "container_argv_0_through_4294967295"
            rejected["plan_bootstrap_minimum"] = BOOTSTRAP_SEED_MINIMUM
            rejected["reproduces_bindcraft_trajectory"] = False
        return 400, rejected
    reply = _plan_catalog(
        request_id.strip(),
        draft.revision,
        _request_summary(draft, message, configuration),
        plans.plans_for_design_request(request_id.strip()),
        service=plans,
    )
    reply["status"] = "plan_recorded"
    reply["plan"] = _with_design_binding(published, (request_id.strip(), request_revision))
    reply["created"] = False
    reply["plan_revision_changed"] = changed
    return 200, reply


_REVISION_BODY_KEYS = frozenset(
    {
        "request_id",
        "request_revision",
        "plan_id",
        "expected_plan_revision",
        "scientific",
        "technical",
        "adopted_origins",
    }
)
_SCIENTIFIC_KEYS = frozenset({"binder_length", "hotspot", "filter_policy"})
_TECHNICAL_KEYS = frozenset(
    {"seed", "gpu", "cpu_count", "memory_mib", "execution_deadline_seconds"}
)
_EDITABLE_PARAMETERS = (
    "binder_length",
    "hotspot_assumption",
    "filter_policy",
    "seed",
    "gpu",
    "cpu_count",
    "memory_mib",
    "execution_deadline_seconds",
)


def _parameter_updates(
    scientific: dict[str, Any], technical: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    if set(scientific) - _SCIENTIFIC_KEYS or set(technical) - _TECHNICAL_KEYS:
        return None, "rejected"
    updates: dict[str, Any] = {}
    if "binder_length" in scientific:
        length = _bounded_int(scientific["binder_length"], CONTAINER_LENGTH_MIN, CONTAINER_LENGTH_MAX)
        if length is None:
            return None, "binder_length_rejected"
        updates["binder_length"] = length
    if "hotspot" in scientific:
        hotspot = _hotspot_choice(scientific["hotspot"])
        if hotspot is None:
            return None, "hotspot_rejected"
        updates["hotspot"] = hotspot
    if "filter_policy" in scientific:
        policy = scientific["filter_policy"]
        if (
            not isinstance(policy, dict)
            or set(policy) != {"mode", "policy"}
            or policy.get("mode") != "technical_demonstration"
            or policy.get("policy") != "no_filters"
        ):
            return None, "filter_policy_unsupported"
        updates["filter_policy"] = "no_filters"
    if "seed" in technical:
        seed = _bounded_int(technical["seed"], CONTAINER_SEED_MIN, CONTAINER_SEED_MAX)
        if seed is None:
            return None, "seed_rejected"
        updates["seed"] = seed
    if "gpu" in technical:
        gpu = _gpu_choice(technical["gpu"])
        if gpu is None:
            return None, "gpu_rejected"
        updates["gpu"] = gpu
    if "cpu_count" in technical:
        low, high = _READINESS_LIMITS["cpu_count"]
        cpu_count = _bounded_int(technical["cpu_count"], low, high)
        if cpu_count is None:
            return None, "cpu_count_rejected"
        updates["cpu_count"] = cpu_count
    if "memory_mib" in technical:
        low, high = _READINESS_LIMITS["memory_mib"]
        memory = _bounded_int(technical["memory_mib"], low, high)
        if memory is None:
            return None, "memory_mib_rejected"
        updates["memory_mib"] = memory
    if "execution_deadline_seconds" in technical:
        low, high = _READINESS_LIMITS["timeout_seconds"]
        deadline = _bounded_int(technical["execution_deadline_seconds"], low, high)
        if deadline is None:
            return None, "execution_deadline_rejected"
        updates["execution_deadline_seconds"] = deadline
    return updates, None


def _apply_parameter_updates(
    fields: dict[str, Any],
    questions: list[dict[str, str]],
    updates: dict[str, Any],
    identities: dict[str, Any] | None,
    adopted_origins: dict[str, Any] | None = None,
) -> None:
    origins = adopted_origins or {}
    if "binder_length" in updates:
        _record_choice(
            fields,
            questions,
            "binder_length",
            updates["binder_length"],
            "not_connected",
            origins.get("binder_length"),
        )
        fields["binder_length_recording_bound"] = "container_argv"
        fields["binder_length_bindcraft_capacity_recorded"] = False
    if "hotspot" in updates:
        choice = updates["hotspot"]
        questions[:] = [item for item in questions if item.get("field") != "hotspot_assumption"]
        fields.pop("hotspot_assumption_conflict", None)
        if choice["mode"] == "none":
            same = fields.get("hotspot_strategy") == "none" and "hotspot_assumption" not in fields
            fields.pop("hotspot_assumption", None)
            fields["hotspot_strategy"] = "none"
            fields["hotspot_assumption_execution_integration"] = "pending"
            fields["hotspot_assumption_execution_support"] = "not_connected"
        else:
            same = fields.get("hotspot_strategy") == "residues" and fields.get("hotspot_assumption") == choice["value"]
            fields["hotspot_assumption"] = choice["value"]
            fields["hotspot_strategy"] = "residues"
            fields["hotspot_assumption_execution_integration"] = "pending"
            fields["hotspot_assumption_execution_support"] = "not_connected"
            recorded = None if identities is None else identities.get("hotspot_assumption")
            if isinstance(recorded, str) and recorded.strip() and recorded.strip() != choice["value"]:
                fields["hotspot_assumption_conflict"] = True
                questions.append(
                    {
                        "field": "hotspot_assumption",
                        "question": (
                            "The stated hotspot conflicts with the verified settings. "
                            "The stated hotspot was kept."
                        ),
                    }
                )
        if not _stamp_origin(fields, "hotspot_assumption", origins.get("hotspot_assumption")):
            _keep_or_reset_origin(fields, "hotspot_assumption", same)
    if "filter_policy" in updates:
        _record_choice(
            fields,
            questions,
            "filter_policy",
            "no_filters",
            "not_connected",
            origins.get("filter_policy"),
        )
        fields["filter_policy_explicit"] = True
        fields["filter_policy_mode"] = "technical_demonstration"
        fields["filter_policy_establishes_scientific_quality"] = False
        fields["filter_policy_is_technical_smoke_test"] = True
    if "seed" in updates:
        _record_choice(fields, questions, "seed", updates["seed"], "numpy_bootstrap_only", origins.get("seed"))
        fields["seed_accepted_interval"] = "container_argv_0_through_4294967295"
        fields["seed_plan_bootstrap_minimum"] = BOOTSTRAP_SEED_MINIMUM
        fields["seed_reproduces_bindcraft_trajectory"] = False
    if "gpu" in updates:
        support = "unsupported" if updates["gpu"] == "not_assigned" else "not_connected"
        _record_choice(fields, questions, "gpu", updates["gpu"], support, origins.get("gpu"))
    if "cpu_count" in updates:
        _record_choice(fields, questions, "cpu_count", updates["cpu_count"], "not_connected", origins.get("cpu_count"))
    if "memory_mib" in updates:
        _record_choice(fields, questions, "memory_mib", updates["memory_mib"], "not_connected", origins.get("memory_mib"))
    if "execution_deadline_seconds" in updates:
        _record_choice(
            fields,
            questions,
            "execution_deadline_seconds",
            updates["execution_deadline_seconds"],
            "unsupported",
            origins.get("execution_deadline_seconds"),
        )


def _record_choice(
    fields: dict[str, Any],
    questions: list[dict[str, str]],
    name: str,
    value: object,
    support: str,
    origin: object = None,
) -> None:
    previous = fields.get(name)
    fields[name] = value
    same = previous == value
    if not _stamp_origin(fields, name, origin):
        _keep_or_reset_origin(fields, name, same)
    fields[f"{name}_execution_integration"] = "pending"
    fields[f"{name}_execution_support"] = support
    questions[:] = [item for item in questions if item.get("field") != name]


def _keep_or_reset_origin(fields: dict[str, Any], name: str, same: bool) -> None:
    if same and fields.get(f"{name}_provenance") == "adopted_suggestion":
        return
    fields[f"{name}_provenance"] = "user"
    fields[f"{name}_source"] = "professor"
    fields.pop(f"{name}_suggestion_id", None)


def _stamp_origin(fields: dict[str, Any], name: str, origin: object) -> bool:
    if not isinstance(origin, dict):
        return False
    source = origin.get("source")
    suggestion_id = origin.get("suggestion_id")
    if source not in {"hotspot_identification_agent", "technical_demonstration_profile"}:
        return False
    if not isinstance(suggestion_id, str) or not suggestion_id.strip():
        return False
    fields[f"{name}_provenance"] = "adopted_suggestion"
    fields[f"{name}_source"] = source
    fields[f"{name}_suggestion_id"] = suggestion_id.strip()
    return True


def _hotspot_choice(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    mode = value.get("mode")
    if mode == "none" and set(value) == {"mode"}:
        return {"mode": "none"}
    if mode != "residues" or set(value) != {"mode", "value"}:
        return None
    residue = value.get("value")
    if not isinstance(residue, str) or not residue.strip() or "\n" in residue or len(residue.strip()) > 160:
        return None
    return {"mode": "residues", "value": residue.strip()}


def _gpu_choice(value: object) -> str | None:
    if value == "not_assigned":
        return "not_assigned"
    if explicit_gpu_index(value) is None or not isinstance(value, str):
        return None
    return value


def _bounded_int(value: object, low: int, high: int) -> int | None:
    if type(value) is not int or isinstance(value, bool) or value < low or value > high:
        return None
    return value


def _execution_integration_pending(fields: dict[str, Any]) -> bool:
    """True only for a saved choice that still blocks review.

    A value the Apptainer executor applies, and a reproducibility limit,
    are not configuration failures. Refusing to assign a GPU still is.
    """

    for name in _EDITABLE_PARAMETERS:
        if fields.get(f"{name}_execution_integration") != "pending":
            continue
        support = fields.get(f"{name}_execution_support")
        if name == "gpu" and support == "unsupported":
            return True
        if support in {"not_connected", "numpy_bootstrap_only"}:
            continue
        if name == "execution_deadline_seconds" and support == "unsupported":
            continue
        return True
    return False


def _revision_snapshot(plan: _Plan) -> dict[str, Any]:
    return {
        "revision": plan.revision,
        "digest": _digest(plan),
        "fields": copy.deepcopy(plan.fields),
        "questions": copy.deepcopy(plan.questions),
        "approval": copy.deepcopy(plan.approval),
        "authorization_id": plan.authorization_id,
    }


_FORBIDDEN_PLAN_KEYS = frozenset(
    {
        "execution_authorized",
        "executable",
        "approved",
        "target_identity",
        "n_candidates",
        "binder_length",
        "hotspot",
        "hotspot_assumption",
        "filter_policy",
        "seed",
        "gpu",
        "cpu_count",
        "memory_mib",
        "execution_deadline_seconds",
        "scientific",
        "technical",
    }
)


def _recorded_draft(drafter: object, request_id: str, revision: int | None) -> tuple[Any, str] | None:
    reader = getattr(drafter, "recorded_at" if revision is not None else "recorded", None)
    if not callable(reader):
        return None
    found = reader(request_id) if revision is None else reader(request_id, revision)
    if not isinstance(found, tuple) or len(found) != 2 or not isinstance(found[1], str):
        return None
    return found


def _proposal_from_draft(draft: Any, message: str) -> dict[str, Any]:
    """Copy only values stored on this revision. Absent values stay absent."""

    fields = {field.name: field for field in getattr(draft, "fields", ())}
    proposal: dict[str, Any] = {"request_text": message}
    target = _stated(fields.get("target_id"))
    if isinstance(target, str):
        proposal["target_identity"] = target
        proposal["target_identity_provenance"] = "user"
        proposal["target_identity_source"] = "professor"
    low = _stated(fields.get("length_min"))
    high = _stated(fields.get("length_max"))
    if type(low) is int and type(high) is int and low == high and low >= 1:
        proposal["binder_length"] = low
        proposal["binder_length_provenance"] = "user"
        proposal["binder_length_source"] = "professor"
    seed = _stated(fields.get("seed"))
    if _bootstrap_seed(seed) is not None:
        proposal["seed"] = seed
        proposal["seed_provenance"] = "user"
        proposal["seed_source"] = "professor"
    policy = _stated(fields.get("filter_policy"))
    if isinstance(policy, str):
        proposal["filter_policy"] = policy
        proposal["filter_policy_provenance"] = "user"
        proposal["filter_policy_source"] = "professor"
    return proposal


def _stated(field: Any) -> Any:
    if field is None or getattr(field, "provenance", None) != "user":
        return None
    value = getattr(field, "value", None)
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _request_summary(draft: Any, message: str, configuration: object | None) -> dict[str, Any]:
    from cellertz.application.request_supervisor import _GENERATOR_DISPLAY, _configured_generator

    fields = {field.name: field for field in getattr(draft, "fields", ())}
    target = _stated(fields.get("target_id"))
    count = _stated(fields.get("n_candidates"))
    generator = _stated(fields.get("generator"))
    if isinstance(generator, str):
        method = _GENERATOR_DISPLAY.get(generator, generator)
        method_source = "researcher"
    else:
        choice, _limitation = _configured_generator(draft, configuration)
        if choice is None:
            method = None
            method_source = "pending"
        else:
            method = _GENERATOR_DISPLAY.get(choice["value"], choice["value"])
            method_source = "server_configuration"
    return {
        "objective": _fact(message, "researcher"),
        "target": _fact(target if isinstance(target, str) else None, "researcher" if isinstance(target, str) else "pending"),
        "candidates_requested": _fact(count if type(count) is int else None, "researcher" if type(count) is int else "pending"),
        "generation_method": _fact(method, method_source),
    }


def _fact(value: Any, source: str) -> dict[str, Any]:
    return {"value": value, "source": source, "pending": value is None}


def _plan_catalog(
    request_id: str,
    revision: int | None,
    summary: dict[str, Any] | None,
    plans: list[dict[str, Any]],
    *,
    service: Any | None = None,
) -> dict[str, Any]:
    report = getattr(service, "server_execution", None)
    found = report() if callable(report) else None
    permitted = isinstance(found, dict) and found.get("demonstration_configured") is True
    current_execution = found if isinstance(found, dict) else {"mode": "disabled", "demonstration_configured": False}
    return {
        "status": "plans_recorded",
        "request_id": request_id,
        "revision": revision,
        "summary": summary,
        "plans": plans,
        "proposed_workflow": list(_PROPOSED_WORKFLOW),
        "planning_origin": "deterministic_generation_plan",
        "model_decided": False,
        "agents_executed": False,
        "approved": False,
        "execution_authorized": False,
        "executable": False,
        "scientific_execution": "disabled",
        "execution_permitted": permitted,
        "server_execution": {
            "mode": current_execution.get("mode") if current_execution.get("mode") in {"disabled", "apptainer-demonstration"} else "disabled",
            "demonstration_configured": current_execution.get("demonstration_configured") is True,
        },
    }


def _plan_conflict(request_id: str, revision: int) -> dict[str, Any]:
    reply = _plan_catalog(request_id, revision, None, [])
    reply["status"] = "revision_conflict"
    return reply


def _plan_rejected() -> dict[str, Any]:
    return {
        "status": "rejected",
        "approved": False,
        "created": False,
        "execution_authorized": False,
        "executable": False,
        "scientific_execution": "disabled",
    }


def _plan_absent() -> dict[str, Any]:
    return {
        "status": "draft_not_found",
        "approved": False,
        "created": False,
        "execution_authorized": False,
        "executable": False,
        "scientific_execution": "disabled",
    }


def handle_generation_plan(
    service: GenerationPlanService, body: bytes, action: str
) -> tuple[int, dict[str, Any]]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, {"status": "rejected", "approved": False, "execution_connected": False}
    if not isinstance(payload, dict):
        return 400, {"status": "rejected", "approved": False, "execution_connected": False}
    plan_id = payload.get("plan_id")
    revision = payload.get("revision")
    key = payload.get("idempotency_key")
    if not isinstance(plan_id, str) or type(revision) is not int or not isinstance(key, str):
        return 400, {"status": "rejected", "approved": False, "execution_connected": False}
    if action in {"decision", "authorization"}:
        reviewer = payload.get("reviewer_id")
        reason = payload.get("reason")
        if not isinstance(reviewer, str) or not isinstance(reason, str):
            return 400, {"status": "rejected", "approved": False, "execution_connected": False}
        if action == "decision":
            expected_digest = payload.get("expected_digest")
            if expected_digest is not None and not isinstance(expected_digest, str):
                return 400, {"status": "rejected", "approved": False, "execution_connected": False}
            return 200, service.approve(
                plan_id=plan_id,
                revision=revision,
                reviewer_id=reviewer,
                reason=reason,
                idempotency_key=key,
                expected_digest=expected_digest,
            )
        authorization_id = payload.get("authorization_id")
        if not isinstance(authorization_id, str):
            return 400, {"status": "rejected", "approved": False, "execution_connected": False}
        return 200, service.bind_authorization(
            plan_id=plan_id,
            revision=revision,
            authorization_id=authorization_id,
            reviewer_id=reviewer,
            reason=reason,
            idempotency_key=key,
        )
    if action == "apptainer-demonstration":
        reviewer = payload.get("reviewer_id")
        reason = payload.get("reason")
        attempt_id = payload.get("attempt_id")
        attempt_directory = payload.get("attempt_directory")
        gpu_index = payload.get("gpu_index")
        if (
            not isinstance(reviewer, str)
            or not isinstance(reason, str)
            or not isinstance(attempt_id, str)
            or not isinstance(attempt_directory, str)
            or type(gpu_index) is not int
        ):
            return 400, {"status": "rejected", "approved": False, "execution_connected": False}
        return 200, service.launch_apptainer_demonstration(
            plan_id=plan_id,
            revision=revision,
            reviewer_id=reviewer,
            reason=reason,
            idempotency_key=key,
            gpu_index=gpu_index,
            attempt_id=attempt_id,
            attempt_directory=attempt_directory,
        )
    return 200, service.dispatch(plan_id=plan_id, revision=revision, idempotency_key=key)
