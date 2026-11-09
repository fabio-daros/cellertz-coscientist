"""Server-owned PDL1 execution identity. Not a production launcher.

Registration compares one execution specification, one parsed readiness
manifest, and one readiness snapshot. It does not start a process, invent
a BindCraft command, or manufacture a ready observation.

Digest conventions, fixed by this module:

- ``image_digest``: readiness form ``sha256:`` plus 64 lowercase hex digits.
- ``manifest_digest``: SHA-256 of the readiness canonical manifest
  (``sort_keys``, separators ``,`` and ``:``, ``ensure_ascii``).
- ``target_structure_sha256`` and ``settings_sha256``: SHA-256 of raw file
  bytes, with no prefix. These are not canonical-document digests.
- ``advanced_profile_sha256``: SHA-256 of canonical JSON
  ``json.dumps(obj, indent=2, sort_keys=True) + "\\n"`` encoded as UTF-8.
  That is the same byte convention as
  ``canonical_advanced_profile_bytes`` in the historical adapter. This
  module does not read that file and does not treat its pin as an
  observation.

``scientific-runner-manifest-v1`` records input names and media types only.
A matching v1 manifest digest does not pin the three scientific-input
digests, so v1 registration returns ``pdl1_input_identity_unbound`` and
no executor. ``scientific-runner-manifest-v2`` puts those digests and
the three execution-policy versions in the canonical manifest, so they
are covered by the existing ``manifest_digest``. A historical-profile
v2 match still returns ``pdl1_production_registration_deferred`` and no
executor. ``pdl1-bindcraft-docker-v1`` can return an executor when the
approved revision, verified bytes, and parsed Docker facts match.
Direct construction of ``Pdl1ScientificRunExecutor`` remains a test seam
for the historical profile. A scripted Docker result validates
orchestration, not a GPU host. This module does not extend the frozen
public execution-record schema.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.scientific_run import (
    _EXECUTION_SCHEMA,
    ExecutorArtifact,
    ScientificExecutionResult,
    ScientificRun,
    _canonical,
)
from cellertz.application.scientific_runner_readiness import (
    _ARGV_TOKEN,
    _IMAGE_DIGEST,
    _LIMITS,
    _OBSERVED_AT,
    _PDL1_INPUTS,
    _PDL1_OUTPUTS,
    _TOKEN,
    ARGV_POLICY_VERSION,
    DIRECTORY_POLICY,
    DOCKER_PROFILE_ID,
    ENVIRONMENT_POLICY,
    PDL1_PROFILE_ID,
    ReadinessReply,
    _InputIdentity,
    _Manifest,
    _ManifestRejected,
    _parse_manifest,
    _read_manifest,
    _version,
)

UNBOUND_INPUT_FIELDS = (
    "target_structure_sha256",
    "settings_sha256",
    "advanced_profile_sha256",
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_FAILED = ScientificExecutionResult("failed", ())
_RESOURCES = frozenset({"cpu_count", "memory_mib", "gpu_count"})


def _text_match(pattern: re.Pattern[str], value: object) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _hex_digest(value: object) -> bool:
    return _text_match(_DIGEST, value)


def _trusted_manifest_path(path: object) -> bool:
    return type(path) is str and "\0" not in path and os.path.isabs(path)


def _limit(name: str, value: object) -> bool:
    if type(value) is not int or isinstance(value, bool):
        return False
    low, high = _LIMITS[name]
    return low <= value <= high


@dataclass(frozen=True)
class Pdl1ExecutionSpecification:
    """Immutable server policy. Historical smoke pins are not defaults."""

    profile_id: str
    target_scope: str
    tool_name: str
    tool_version: str
    runtime_name: str
    runtime_version: str
    image_digest: str
    manifest_digest: str
    entrypoint: str
    recorded_argv_template: tuple[str, ...]
    timeout_seconds: int
    max_output_bytes: int
    cpu_count: int
    memory_mib: int
    gpu_count: int
    target_structure_sha256: str
    settings_sha256: str
    advanced_profile_sha256: str
    readiness_observed_at: str
    argv_policy_version: str
    directory_policy: str

    def __post_init__(self) -> None:
        if not _specification_ok(self):
            raise ApplicationContractError("pdl1_execution_specification_invalid")


def _specification_ok(spec: Pdl1ExecutionSpecification) -> bool:
    argv = spec.recorded_argv_template
    return (
        type(spec.profile_id) is str
        and spec.profile_id in {PDL1_PROFILE_ID, DOCKER_PROFILE_ID}
        and spec.target_scope == "PDL1"
        and spec.tool_name == "bindcraft"
        and _version(spec.tool_version)
        and _version(spec.runtime_name)
        and _version(spec.runtime_version)
        and _text_match(_IMAGE_DIGEST, spec.image_digest)
        and _hex_digest(spec.manifest_digest)
        and _text_match(_TOKEN, spec.entrypoint)
        and type(argv) is tuple
        and 1 <= len(argv) <= 16
        and all(type(item) is str and _ARGV_TOKEN.fullmatch(item) is not None for item in argv)
        and all(".." not in item.split("/") for item in argv)
        and _limit("timeout_seconds", spec.timeout_seconds)
        and _limit("max_output_bytes", spec.max_output_bytes)
        and _limit("cpu_count", spec.cpu_count)
        and _limit("memory_mib", spec.memory_mib)
        and _limit("gpu_count", spec.gpu_count)
        and _hex_digest(spec.target_structure_sha256)
        and _hex_digest(spec.settings_sha256)
        and _hex_digest(spec.advanced_profile_sha256)
        and _text_match(_OBSERVED_AT, spec.readiness_observed_at)
        and spec.argv_policy_version == ARGV_POLICY_VERSION
        and spec.directory_policy == DIRECTORY_POLICY
    )


@dataclass(frozen=True)
class Pdl1InputBinding:
    """Test-injected request compatibility. Not a manifest field."""

    request_digest: str
    target_structure_sha256: str
    settings_sha256: str
    advanced_profile_sha256: str

    def __post_init__(self) -> None:
        if not all(
            _hex_digest(item)
            for item in (
                self.request_digest,
                self.target_structure_sha256,
                self.settings_sha256,
                self.advanced_profile_sha256,
            )
        ):
            raise ApplicationContractError("pdl1_input_binding_invalid")


@dataclass(frozen=True)
class RuntimeCapabilities:
    enforces_timeout: bool
    cleanup: str
    output_limit: str
    resources: frozenset[str]
    explicit_environment: bool


@dataclass(frozen=True)
class RuntimeOutputEvidence:
    name: str
    media_type: str
    size_bytes: int


@dataclass(frozen=True)
class Pdl1RuntimeResult:
    status: str
    exit_code: int | None
    cleanup: str
    output_bytes: int
    outputs: tuple[RuntimeOutputEvidence, ...]
    inflight_output_limit_enforced: bool
    resources_enforced: bool


@dataclass(frozen=True)
class Pdl1ExecutionPlan:
    """Typed plan. The readiness argv_template is not process argv."""

    run_id: str
    attempt: int
    argv_policy_version: str
    directory_policy: str
    environment_policy: str
    environment: tuple[tuple[str, str], ...]
    timeout_seconds: int
    max_output_bytes: int
    cpu_count: int
    memory_mib: int
    gpu_count: int
    image_digest: str
    request_digest: str
    target_structure_sha256: str
    settings_sha256: str
    advanced_profile_sha256: str


class Pdl1Runtime(Protocol):
    def capabilities(self) -> RuntimeCapabilities: ...

    def execute(self, plan: Pdl1ExecutionPlan) -> Pdl1RuntimeResult: ...


@dataclass(frozen=True)
class Pdl1ValidatedInputIdentity:
    """Manifest-declared identity that matches one execution specification.

    This is not proof that those files exist, that their bytes were read,
    or that an approved scientific request selected them.
    """

    manifest_digest: str
    target_structure_sha256: str
    settings_sha256: str
    advanced_profile_sha256: str
    argv_policy_version: str
    directory_policy: str
    environment_policy: str

    def __post_init__(self) -> None:
        digests = (
            self.manifest_digest,
            self.target_structure_sha256,
            self.settings_sha256,
            self.advanced_profile_sha256,
        )
        policies = (
            self.argv_policy_version == ARGV_POLICY_VERSION
            and self.directory_policy == DIRECTORY_POLICY
            and self.environment_policy == ENVIRONMENT_POLICY
        )
        if not policies or not all(_hex_digest(item) for item in digests):
            raise ApplicationContractError("pdl1_input_identity_invalid")


@dataclass(frozen=True)
class Pdl1Registration:
    code: str
    field: str | None = None
    missing: tuple[str, ...] = ()
    executor: object | None = None
    input_identity: Pdl1ValidatedInputIdentity | None = None

    @property
    def accepted(self) -> bool:
        return self.executor is not None


def _capability_gap(runtime: object) -> str | None:
    capabilities = getattr(runtime, "capabilities", None)
    if not callable(capabilities):
        return "pdl1_runtime_invalid"
    try:
        declared = capabilities()
    except Exception:  # noqa: BLE001 — capability lookup must not leak
        return "pdl1_runtime_invalid"
    if type(declared) is not RuntimeCapabilities:
        return "pdl1_runtime_invalid"
    if declared.enforces_timeout is not True:
        return "pdl1_timeout_unenforceable"
    if declared.cleanup != "process_group":
        return "pdl1_process_cleanup_uncertain"
    if declared.output_limit != "inflight":
        return "pdl1_output_limit_unenforced"
    if type(declared.resources) is not frozenset or not _RESOURCES <= declared.resources:
        return "pdl1_resource_enforcement_unavailable"
    if declared.explicit_environment is not True:
        return "pdl1_environment_unrestricted"
    return None


def _policy_field(
    spec: Pdl1ExecutionSpecification, manifest: _Manifest, snapshot: ReadinessReply
) -> str | None:
    if manifest.digest != spec.manifest_digest:
        return "manifest_digest"
    compared = (
        ("profile_id", manifest.profile_id, spec.profile_id),
        ("target_scope", manifest.target_scope, spec.target_scope),
        ("tool_name", manifest.tool_name, spec.tool_name),
        ("tool_version", manifest.tool_version, spec.tool_version),
        ("runtime_name", manifest.runtime_name, spec.runtime_name),
        ("runtime_version", manifest.runtime_version, spec.runtime_version),
        ("image_digest", manifest.image_digest, spec.image_digest),
        ("entrypoint", manifest.entrypoint, spec.entrypoint),
        ("recorded_argv_template", manifest.argv_template, spec.recorded_argv_template),
        ("timeout_seconds", manifest.timeout_seconds, spec.timeout_seconds),
        ("max_output_bytes", manifest.max_output_bytes, spec.max_output_bytes),
    )
    for name, got, want in compared:
        if got != want:
            return name
    envelope = manifest.envelope
    if envelope is None:
        return "resource_envelope"
    envelope_fields = (
        ("cpu_count", envelope.cpu_count, spec.cpu_count),
        ("memory_mib", envelope.memory_mib, spec.memory_mib),
        ("gpu_count", envelope.gpu_count, spec.gpu_count),
        ("timeout_seconds", envelope.timeout_seconds, spec.timeout_seconds),
        ("max_output_bytes", envelope.max_output_bytes, spec.max_output_bytes),
    )
    for name, got, want in envelope_fields:
        if got != want:
            return name
    if manifest.inputs != _PDL1_INPUTS:
        return "expected_inputs"
    if manifest.outputs != _PDL1_OUTPUTS:
        return "expected_outputs"
    identity = manifest.input_identity
    if type(identity) is _InputIdentity:
        declared = (
            (
                "target_structure_sha256",
                identity.target_structure_sha256,
                spec.target_structure_sha256,
            ),
            ("settings_sha256", identity.settings_sha256, spec.settings_sha256),
            (
                "advanced_profile_sha256",
                identity.advanced_profile_sha256,
                spec.advanced_profile_sha256,
            ),
            ("argv_policy_version", identity.argv_policy_version, spec.argv_policy_version),
            ("directory_policy", identity.directory_policy, spec.directory_policy),
            ("environment_policy", identity.environment_policy, ENVIRONMENT_POLICY),
        )
        for name, got, want in declared:
            if got != want:
                return name
    if snapshot.status != "ready":
        return "readiness_status"
    if (
        snapshot.execution_authorized is not False
        or snapshot.scientific_execution is not False
        or snapshot.scientific_validation is not False
    ):
        return "readiness_flags"
    snapshot_fields = (
        ("profile_id", snapshot.profile_id),
        ("target_scope", snapshot.target_scope),
        ("tool_name", snapshot.tool_name),
        ("tool_version", snapshot.tool_version),
        ("runtime_name", snapshot.runtime_name),
        ("runtime_version", snapshot.runtime_version),
        ("image_digest", snapshot.image_digest),
        ("manifest_digest", snapshot.manifest_digest),
        ("readiness_observed_at", snapshot.observed_at),
    )
    for name, got in snapshot_fields:
        if got != getattr(spec, name):
            return name
    return None


def _binding_matches(spec: Pdl1ExecutionSpecification, binding: object) -> bool:
    if type(binding) is not Pdl1InputBinding:
        return False
    return (
        binding.target_structure_sha256 == spec.target_structure_sha256
        and binding.settings_sha256 == spec.settings_sha256
        and binding.advanced_profile_sha256 == spec.advanced_profile_sha256
    )


def register_pdl1_executor(
    specification: object,
    manifest_path: str,
    snapshot: object,
    runtime: object,
    input_binding: object | None = None,
    *,
    docker: object | None = None,
) -> Pdl1Registration:
    """Trusted composition seam.

    Historical ``pdl1-bindcraft-historical-v1`` stays unbound or deferred.
    ``pdl1-bindcraft-docker-v1`` returns an executor only when the approved
    revision, verified bytes, and parsed Docker facts match. A missing fact
    keeps registration rejected. Production startup passes no bundle.
    """

    if type(specification) is not Pdl1ExecutionSpecification:
        return Pdl1Registration("pdl1_execution_specification_invalid")
    if type(snapshot) is not ReadinessReply:
        return Pdl1Registration("pdl1_readiness_snapshot_invalid")
    if not _trusted_manifest_path(manifest_path):
        return Pdl1Registration("pdl1_execution_specification_invalid")
    try:
        document, digest = _read_manifest(manifest_path)
        manifest = _parse_manifest(document, digest)
    except _ManifestRejected as error:
        return Pdl1Registration(error.code)
    except (OSError, ValueError):
        return Pdl1Registration("pdl1_execution_specification_invalid")
    field = _policy_field(specification, manifest, snapshot)
    if field is not None:
        return Pdl1Registration("pdl1_execution_policy_mismatch", field=field)
    if specification.profile_id == PDL1_PROFILE_ID:
        gap = _capability_gap(runtime)
        if gap is not None:
            return Pdl1Registration(gap)
    if input_binding is not None and not _binding_matches(specification, input_binding):
        return Pdl1Registration("pdl1_input_binding_mismatch")
    identity = manifest.input_identity
    if identity is None:
        return Pdl1Registration("pdl1_input_identity_unbound", missing=UNBOUND_INPUT_FIELDS)
    validated = validate_pdl1_input_identity(specification, identity, manifest.digest)
    if specification.profile_id != DOCKER_PROFILE_ID:
        if docker is not None:
            return Pdl1Registration("pdl1_profile_not_docker")
        return Pdl1Registration(
            "pdl1_production_registration_deferred",
            input_identity=validated,
        )
    from cellertz.application.pdl1_docker import build_docker_executor

    code, executor, missing = build_docker_executor(specification, docker)
    if executor is None:
        return Pdl1Registration(code, missing=missing, input_identity=validated)
    return Pdl1Registration(code, executor=executor, input_identity=validated)


def open_registered_executor(bundle: object) -> object:
    """Production entry. ``None`` keeps execution unconfigured.

    A bundle is accepted only when ``register_pdl1_executor`` returns an
    executor. This function does not invent an image, entrypoint, or argv.
    """

    from cellertz.application.scientific_run_config import ScientificRunConfigurationError

    if bundle is None:
        raise ScientificRunConfigurationError("scientific_run_executor_unconfigured")
    registration = register_pdl1_executor(
        getattr(bundle, "specification", None),
        getattr(bundle, "manifest_path", ""),
        getattr(bundle, "snapshot", None),
        None,
        docker=getattr(bundle, "docker", None),
    )
    if registration.executor is None:
        if registration.code == "pdl1_worker_loss_deadline_unresolved":
            raise ScientificRunConfigurationError(registration.code)
        raise ScientificRunConfigurationError("scientific_run_executor_unconfigured")
    return registration.executor


def validate_pdl1_input_identity(
    specification: Pdl1ExecutionSpecification,
    identity: _InputIdentity,
    manifest_digest: str,
) -> Pdl1ValidatedInputIdentity:
    """Agree a v2 declaration with a specification. Not file or request proof."""

    if (
        type(identity) is not _InputIdentity
        or type(specification) is not Pdl1ExecutionSpecification
    ):
        raise ApplicationContractError("pdl1_input_identity_mismatch")
    declared = (
        identity.target_structure_sha256 == specification.target_structure_sha256
        and identity.settings_sha256 == specification.settings_sha256
        and identity.advanced_profile_sha256 == specification.advanced_profile_sha256
        and identity.argv_policy_version == specification.argv_policy_version
        and identity.directory_policy == specification.directory_policy
        and identity.environment_policy == ENVIRONMENT_POLICY
        and manifest_digest == specification.manifest_digest
    )
    if type(identity) is not _InputIdentity or not declared:
        raise ApplicationContractError("pdl1_input_identity_mismatch")
    return Pdl1ValidatedInputIdentity(
        manifest_digest=manifest_digest,
        target_structure_sha256=identity.target_structure_sha256,
        settings_sha256=identity.settings_sha256,
        advanced_profile_sha256=identity.advanced_profile_sha256,
        argv_policy_version=identity.argv_policy_version,
        directory_policy=identity.directory_policy,
        environment_policy=identity.environment_policy,
    )


class Pdl1ScientificRunExecutor:
    """Test and prototype seam. Direct construction is not production registration."""

    def __init__(
        self,
        specification: Pdl1ExecutionSpecification,
        runtime: Pdl1Runtime,
        input_binding: Pdl1InputBinding | None,
    ) -> None:
        if type(specification) is not Pdl1ExecutionSpecification:
            raise ApplicationContractError("pdl1_execution_specification_invalid")
        self.specification = specification
        self.runtime = runtime
        self.input_binding = input_binding

    def execute(self, run: ScientificRun) -> ScientificExecutionResult:
        if not _eligible(run) or not _same_identity(self.specification, run):
            return _FAILED
        if not _inputs_match(self.specification, self.input_binding, run):
            return _FAILED
        if _capability_gap(self.runtime) is not None:
            return _FAILED
        plan = _plan(self.specification, self.input_binding, run)
        try:
            result = self.runtime.execute(plan)
        except Exception:  # noqa: BLE001 — no command, path, or runtime text
            return _FAILED
        if not _output_accepted(self.specification, result):
            return _FAILED
        return ScientificExecutionResult(
            "completed",
            (
                ExecutorArtifact(
                    "execution_record",
                    "application/json",
                    _record(run),
                ),
            ),
        )


def _eligible(run: object) -> bool:
    if type(run) is not ScientificRun or not run.confirmed or run.status != "running":
        return False
    return (
        run.profile_id == PDL1_PROFILE_ID
        and run.tool_name == "bindcraft"
        and run.target_scope == "PDL1"
        and run.budget.get("authorizes_execution") is False
    )


def _same_identity(spec: Pdl1ExecutionSpecification, run: ScientificRun) -> bool:
    return (
        run.profile_id == spec.profile_id
        and run.target_scope == spec.target_scope
        and run.tool_name == spec.tool_name
        and run.tool_version == spec.tool_version
        and run.runtime_name == spec.runtime_name
        and run.runtime_version == spec.runtime_version
        and run.image_digest == spec.image_digest
        and run.manifest_digest == spec.manifest_digest
        and run.readiness_observed_at == spec.readiness_observed_at
    )


def _inputs_match(
    spec: Pdl1ExecutionSpecification,
    binding: Pdl1InputBinding | None,
    run: ScientificRun,
) -> bool:
    if not _binding_matches(spec, binding):
        return False
    assert isinstance(binding, Pdl1InputBinding)
    return binding.request_digest == run.request_digest


def _plan(
    spec: Pdl1ExecutionSpecification,
    binding: Pdl1InputBinding | None,
    run: ScientificRun,
) -> Pdl1ExecutionPlan:
    assert isinstance(binding, Pdl1InputBinding)
    return Pdl1ExecutionPlan(
        run_id=run.run_id,
        attempt=run.attempt,
        argv_policy_version=spec.argv_policy_version,
        directory_policy=spec.directory_policy,
        environment_policy=ENVIRONMENT_POLICY,
        environment=(),
        timeout_seconds=spec.timeout_seconds,
        max_output_bytes=spec.max_output_bytes,
        cpu_count=spec.cpu_count,
        memory_mib=spec.memory_mib,
        gpu_count=spec.gpu_count,
        image_digest=spec.image_digest,
        request_digest=binding.request_digest,
        target_structure_sha256=binding.target_structure_sha256,
        settings_sha256=binding.settings_sha256,
        advanced_profile_sha256=binding.advanced_profile_sha256,
    )


def _output_accepted(spec: Pdl1ExecutionSpecification, result: object) -> bool:
    if type(result) is not Pdl1RuntimeResult:
        return False
    if (
        result.status != "completed"
        or type(result.exit_code) is not int
        or result.exit_code != 0
        or result.cleanup != "stopped"
        or result.inflight_output_limit_enforced is not True
        or result.resources_enforced is not True
    ):
        return False
    if type(result.output_bytes) is not int or isinstance(result.output_bytes, bool):
        return False
    if not 1 <= result.output_bytes <= spec.max_output_bytes:
        return False
    outputs = result.outputs
    if type(outputs) is not tuple or len(outputs) != 1:
        return False
    item = outputs[0]
    if type(item) is not RuntimeOutputEvidence:
        return False
    return (
        item.name == "design_records"
        and item.media_type == "text/csv"
        and type(item.size_bytes) is int
        and item.size_bytes == result.output_bytes
    )


def _record(run: ScientificRun) -> bytes:
    return _canonical(
        {
            "attempt": run.attempt,
            "authorization_id": run.authorization_id,
            "run_id": run.run_id,
            "schema": _EXECUTION_SCHEMA,
            "scientific_validation": False,
            "session_id": run.session_id,
            "status": "completed",
        }
    ).encode("utf-8")
