"""Versioned scientific-runner readiness. Observation only.

A ready snapshot is not execution authorization, not an execution, and
not scientific validation. This module does not call BindCraft, launch
a container, spawn a process, write scientific memory, or generate
candidates. Production startup records no probe. Tests may inject a
ReadinessProbe; a passing observation must name the manifest the
server parsed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.scientific_runner_readiness_config import (
    DISABLED,
    ScientificRunnerConfigurationError,
    ScientificRunnerSettings,
    resolve_scientific_runner_settings,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import CONTAINER_NO_FILTERS

SCHEMA_VERSION = "scientific-runner-manifest-v1"
SCHEMA_VERSION_V2 = "scientific-runner-manifest-v2"
ARGV_POLICY_VERSION = "server-constructed-argv-v1"
DIRECTORY_POLICY = "server-owned-per-attempt"
ENVIRONMENT_POLICY = "empty-explicit"
PDL1_PROFILE_ID = "pdl1-bindcraft-historical-v1"
DOCKER_PROFILE_ID = "pdl1-bindcraft-docker-v1"
CD22_PROFILE_ID = "cd22-mber-v1"
TACI_PROFILE_ID = "taci-mber-v1"
_EXECUTABLE_PROFILES = frozenset({PDL1_PROFILE_ID, DOCKER_PROFILE_ID})
_PDL1_PROFILES = frozenset({PDL1_PROFILE_ID, DOCKER_PROFILE_ID})
_BLOCKED_PROFILES = frozenset({CD22_PROFILE_ID, TACI_PROFILE_ID})
_KNOWN_PROFILES = _EXECUTABLE_PROFILES | _BLOCKED_PROFILES
_STATUSES = frozenset({"disabled", "blocked", "ready", "configuration_failure", "adapter_failure"})
_MAX_MANIFEST_BYTES = 65536
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_MANIFEST_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_OBSERVED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(?!000000)\d{6})?\+00:00$")
_UNSAFE_PUBLIC = re.compile(r"traceback|/users/|/tmp/|token=|sqlite|select\s", re.IGNORECASE)
_ARGV_TOKEN = re.compile(r"^[A-Za-z0-9_.:@+=,/|-]{1,128}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$")
_TOP_KEYS = frozenset(
    {
        "argv_template",
        "entrypoint",
        "expected_inputs",
        "expected_outputs",
        "image_digest",
        "max_output_bytes",
        "profile_id",
        "resource_envelope",
        "runtime_name",
        "runtime_version",
        "schema_version",
        "target_scope",
        "timeout_seconds",
        "tool_name",
        "tool_version",
    }
)
_ENVELOPE_KEYS = frozenset(
    {"cpu_count", "gpu_count", "max_output_bytes", "memory_mib", "timeout_seconds"}
)
_CONTRACT_KEYS = frozenset({"media_type", "name"})
_PDL1_INPUTS = (
    ("binder_specification", "application/json"),
    ("target_structure", "chemical/x-pdb"),
)
_PDL1_OUTPUTS = (("design_records", "text/csv"),)
_LIMITS = {
    "cpu_count": (1, 64),
    "memory_mib": (1, 262144),
    "gpu_count": (0, 8),
    "timeout_seconds": (1, 86400),
    "max_output_bytes": (1, 1_073_741_824),
}
_REJECTED_VERSIONS = frozenset({"latest", "current", "unknown"})
_PUBLIC_FIELDS = (
    "status",
    "text",
    "session_id",
    "profile_id",
    "tool_name",
    "tool_version",
    "runtime_name",
    "runtime_version",
    "image_digest",
    "target_scope",
    "manifest_digest",
    "observed_at",
    "checks",
    "execution_authorized",
    "scientific_execution",
    "scientific_validation",
)
_DISABLED_TEXT = (
    "Scientific runner readiness is disabled. Real scientific execution remains "
    "disabled. This is operational readiness, not scientific validation."
)
_BLOCKED_TEXT = (
    "Scientific runner readiness is blocked. Real scientific execution remains "
    "disabled. This is operational readiness, not scientific validation."
)
_READY_TEXT = (
    "Scientific runner readiness is recorded. Execution is not authorized and has "
    "not run. This is operational readiness, not scientific validation."
)
_CONFIG_TEXT = "Scientific runner configuration was rejected. No scientific action was taken."
_ADAPTER_TEXT = "The scientific runner readiness adapter failed. No scientific action was taken."
_CONFIG_DETAIL = {
    "manifest_invalid": "The scientific runner manifest was rejected.",
    "manifest_not_canonical": "The manifest is not in canonical form.",
    "manifest_too_large": "The manifest exceeds the size limit.",
    "manifest_unreadable": "The scientific runner manifest could not be read.",
    "scientific_runner_manifest_unreadable": "The scientific runner manifest could not be read.",
    "unsupported_schema": "The manifest schema is not supported.",
    "unsupported_profile": "The execution profile is not supported.",
    "unsupported_policy": "The execution policy version is not supported.",
    "invalid_manifest_type": "The manifest contains a value of the wrong type.",
}
_CHECK_CODES = frozenset(
    {
        "argv_template",
        "entrypoint",
        "execution_profile",
        "expected_inputs",
        "expected_outputs",
        "generator_version_not_recorded",
        "image_identity",
        "invalid_manifest_type",
        "manifest_invalid",
        "manifest_not_canonical",
        "manifest_too_large",
        "manifest_unreadable",
        "output_limit",
        "readiness_adapter_failed",
        "readiness_probe",
        "readiness_probe_failed",
        "readiness_probe_malformed",
        "readiness_probe_mismatch",
        "readiness_probe_not_recorded",
        "readiness_probe_rejected",
        "readiness_probe_timeout",
        "resource_envelope",
        "runner_disabled",
        "runtime_identity",
        "scientific_runner_manifest_unreadable",
        "target_execution_blocked",
        "target_scope",
        "timeout_limit",
        "tool_identity",
        "tool_version",
        "unsupported_policy",
        "unsupported_profile",
        "unsupported_schema",
    }
)
_READY_CODES = frozenset(
    {
        "argv_template",
        "entrypoint",
        "execution_profile",
        "expected_inputs",
        "expected_outputs",
        "image_identity",
        "output_limit",
        "readiness_probe",
        "resource_envelope",
        "runner_disabled",
        "runtime_identity",
        "target_scope",
        "timeout_limit",
        "tool_identity",
        "tool_version",
    }
)
_PROFILE_IDENTITY = {
    PDL1_PROFILE_ID: ("bindcraft", "PDL1"),
    DOCKER_PROFILE_ID: ("bindcraft", "PDL1"),
    CD22_PROFILE_ID: ("mber", "CD22"),
    TACI_PROFILE_ID: ("mber", "TACI"),
}


class _ManifestRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReadinessProbe(Protocol):
    def probe(self) -> ProbeObservation: ...


@dataclass(frozen=True)
class ProbeObservation:
    """Host observation. A passing result must equal the re-read manifest."""

    outcome: str
    ready: bool | None = None
    manifest_digest: str | None = None
    profile_id: str | None = None
    tool_name: str | None = None
    tool_version: str | None = None
    runtime_name: str | None = None
    runtime_version: str | None = None
    image_digest: str | None = None
    target_scope: str | None = None


@dataclass(frozen=True)
class ReadinessCheck:
    code: str
    status: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "status": self.status, "text": self.text}


@dataclass(frozen=True)
class ReadinessReply:
    status: str
    text: str
    session_id: str
    profile_id: str | None
    tool_name: str | None
    tool_version: str | None
    runtime_name: str | None
    runtime_version: str | None
    image_digest: str | None
    target_scope: str | None
    manifest_digest: str | None
    observed_at: str
    checks: tuple[ReadinessCheck, ...]
    execution_authorized: bool = False
    scientific_execution: bool = False
    scientific_validation: bool = False

    def __post_init__(self) -> None:
        _require_public_reply(self)

    @staticmethod
    def public_fields() -> tuple[str, ...]:
        return _PUBLIC_FIELDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "profile_id": self.profile_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "runtime_name": self.runtime_name,
            "runtime_version": self.runtime_version,
            "image_digest": self.image_digest,
            "target_scope": self.target_scope,
            "manifest_digest": self.manifest_digest,
            "observed_at": self.observed_at,
            "checks": [check.to_dict() for check in self.checks],
            "execution_authorized": False,
            "scientific_execution": False,
            "scientific_validation": False,
        }


@dataclass(frozen=True)
class _Envelope:
    cpu_count: int | None
    memory_mib: int | None
    gpu_count: int | None
    timeout_seconds: int | None
    max_output_bytes: int | None


@dataclass(frozen=True)
class _InputIdentity:
    """Digests declared by a v2 manifest. Not file-byte verification."""

    target_structure_sha256: str
    settings_sha256: str
    advanced_profile_sha256: str
    argv_policy_version: str
    directory_policy: str
    environment_policy: str


_V2_KEYS = _TOP_KEYS | {"input_identity"}
_INPUT_KEYS = frozenset(
    {
        "advanced_profile_sha256",
        "argv_policy_version",
        "directory_policy",
        "environment_policy",
        "settings_sha256",
        "target_structure_sha256",
    }
)


@dataclass(frozen=True)
class _Manifest:
    profile_id: str | None
    tool_name: str | None
    tool_version: str | None
    runtime_name: str | None
    runtime_version: str | None
    image_digest: str | None
    entrypoint: str | None
    argv_template: tuple[str, ...] | None
    envelope: _Envelope | None
    target_scope: str | None
    inputs: tuple[tuple[str, str], ...] | None
    outputs: tuple[tuple[str, str], ...] | None
    timeout_seconds: int | None
    max_output_bytes: int | None
    digest: str
    input_identity: _InputIdentity | None = None


class ScientificRunnerReadiness:
    """Immutable startup snapshot. read() does not probe."""

    def __init__(self, reply: ReadinessReply) -> None:
        self._reply = reply

    def read(self) -> ReadinessReply:
        return self._reply


def load_scientific_runner_readiness(
    session_id: str,
    environ: Mapping[str, str] | None = None,
    probe: ReadinessProbe | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ScientificRunnerReadiness:
    observed_at = _stamp(clock)
    try:
        settings = resolve_scientific_runner_settings(environ)
    except ScientificRunnerConfigurationError as error:
        raise ScientificRunnerConfigurationError(error.code) from None
    if settings.backend == DISABLED:
        return ScientificRunnerReadiness(_disabled_reply(session_id, observed_at))
    try:
        return ScientificRunnerReadiness(_from_manifest(session_id, settings, probe, observed_at))
    except _ManifestRejected as error:
        return ScientificRunnerReadiness(_configuration_reply(session_id, observed_at, error.code))
    except ScientificRunnerConfigurationError:
        raise
    except Exception:  # noqa: BLE001 — snapshot stays safe
        return ScientificRunnerReadiness(adapter_failure_reply(session_id, observed_at))


def adapter_failure_reply(session_id: str, observed_at: str) -> ReadinessReply:
    return _reply(
        status="adapter_failure",
        text=_ADAPTER_TEXT,
        session_id=session_id,
        observed_at=observed_at,
        checks=(
            ReadinessCheck(
                "readiness_adapter_failed",
                "blocked",
                _ADAPTER_TEXT,
            ),
        ),
    )


def _from_manifest(
    session_id: str,
    settings: ScientificRunnerSettings,
    probe: ReadinessProbe | None,
    observed_at: str,
) -> ReadinessReply:
    assert settings.manifest_path is not None
    document, digest = _read_manifest(settings.manifest_path)
    manifest = _parse_manifest(document, digest)
    blocked_target = manifest.profile_id in _BLOCKED_PROFILES
    if blocked_target:
        observation = ProbeObservation("not_recorded", None)
    else:
        observation = _observe(probe)
    checks = _checks(manifest, observation, blocked_target=blocked_target)
    status = "blocked" if any(item.status != "passed" for item in checks) else "ready"
    if blocked_target:
        status = "blocked"
    return _reply(
        status=status,
        text=_READY_TEXT if status == "ready" else _BLOCKED_TEXT,
        session_id=session_id,
        observed_at=observed_at,
        checks=checks,
        profile_id=_public_id(manifest.profile_id),
        tool_name=_public_token(manifest.tool_name),
        tool_version=None if blocked_target else _public_version(manifest.tool_version),
        runtime_name=_public_token(manifest.runtime_name),
        runtime_version=_public_version(manifest.runtime_version),
        image_digest=manifest.image_digest
        if _IMAGE_DIGEST.fullmatch(manifest.image_digest or "")
        else None,
        target_scope=manifest.target_scope
        if manifest.target_scope in {"PDL1", "CD22", "TACI"}
        else None,
        manifest_digest=digest,
    )


def _observe(probe: ReadinessProbe | None) -> ProbeObservation:
    if probe is None:
        return ProbeObservation("not_recorded", None)
    observation = probe.probe()
    if type(observation) is not ProbeObservation:
        return ProbeObservation("malformed", None)
    return observation


def _disabled_reply(session_id: str, observed_at: str) -> ReadinessReply:
    checks = (
        _check("runner_disabled", False, "Scientific runner readiness is disabled."),
        _check("execution_profile", True, "Execution profile is pdl1-bindcraft-historical-v1."),
        _check("tool_identity", True, "Tool identity is bindcraft."),
        _check("tool_version", False, "Tool version is not recorded."),
        _check("runtime_identity", False, "Container runtime identity is not recorded."),
        _check("image_identity", False, "Immutable image digest is not recorded."),
        _check("entrypoint", False, "Server-owned entrypoint is not recorded."),
        _check("argv_template", False, "Server-owned argv template is not recorded."),
        _check("resource_envelope", False, "Resource envelope is not recorded."),
        _check("target_scope", True, "Target scope is PDL1."),
        _check("expected_inputs", False, "Canonical expected inputs are not recorded."),
        _check("expected_outputs", False, "Canonical expected outputs are not recorded."),
        _check("timeout_limit", False, "Timeout limit is not recorded."),
        _check("output_limit", False, "Output limit is not recorded."),
        _check(
            "readiness_probe_not_recorded", False, "The readiness probe result is not recorded."
        ),
    )
    return _reply(
        status="disabled",
        text=_DISABLED_TEXT,
        session_id=session_id,
        observed_at=observed_at,
        checks=checks,
        profile_id=PDL1_PROFILE_ID,
        tool_name="bindcraft",
        target_scope="PDL1",
    )


def _configuration_reply(session_id: str, observed_at: str, code: str) -> ReadinessReply:
    detail = _CONFIG_DETAIL.get(code, _CONFIG_TEXT)
    return _reply(
        status="configuration_failure",
        text=_CONFIG_TEXT,
        session_id=session_id,
        observed_at=observed_at,
        checks=(ReadinessCheck(code, "blocked", detail),),
    )


def _reply(
    *,
    status: str,
    text: str,
    session_id: str,
    observed_at: str,
    checks: tuple[ReadinessCheck, ...],
    profile_id: str | None = None,
    tool_name: str | None = None,
    tool_version: str | None = None,
    runtime_name: str | None = None,
    runtime_version: str | None = None,
    image_digest: str | None = None,
    target_scope: str | None = None,
    manifest_digest: str | None = None,
) -> ReadinessReply:
    return ReadinessReply(
        status=status,
        text=text,
        session_id=session_id,
        profile_id=profile_id,
        tool_name=tool_name,
        tool_version=tool_version,
        runtime_name=runtime_name,
        runtime_version=runtime_version,
        image_digest=image_digest,
        target_scope=target_scope,
        manifest_digest=manifest_digest,
        observed_at=observed_at,
        checks=checks,
        execution_authorized=False,
        scientific_execution=False,
        scientific_validation=False,
    )


def _checks(
    manifest: _Manifest, observation: ProbeObservation, *, blocked_target: bool
) -> tuple[ReadinessCheck, ...]:
    profile_ok = manifest.profile_id in _KNOWN_PROFILES
    tool_ok = _tool_matches(manifest)
    version_ok = (not blocked_target) and _version(manifest.tool_version)
    runtime_ok = _version(manifest.runtime_name) and _version(manifest.runtime_version)
    image_ok = _IMAGE_DIGEST.fullmatch(manifest.image_digest or "") is not None
    entry_ok = _token(manifest.entrypoint)
    argv_ok = bool(manifest.argv_template)
    envelope_ok = _envelope_ok(manifest.envelope)
    scope_ok = _scope_matches(manifest)
    inputs_ok = manifest.profile_id in _PDL1_PROFILES and manifest.inputs == _PDL1_INPUTS
    outputs_ok = manifest.profile_id in _PDL1_PROFILES and manifest.outputs == _PDL1_OUTPUTS
    timeout_ok = _limit_matches(manifest, "timeout_seconds")
    output_ok = _limit_matches(manifest, "max_output_bytes")
    probe_code, probe_ok, probe_text = _probe_check(manifest, observation)
    checks = [
        _check("runner_disabled", True, "Scientific runner evaluation is enabled."),
        _check(
            "execution_profile",
            profile_ok,
            "Execution profile is recorded."
            if profile_ok
            else "Execution profile is not recorded.",
        ),
        _check(
            "tool_identity",
            tool_ok,
            "Tool identity matches the execution profile."
            if tool_ok
            else "Tool identity is not recorded.",
        ),
        _check(
            "tool_version" if not blocked_target else "generator_version_not_recorded",
            version_ok,
            "Tool version is recorded."
            if version_ok
            else "Tool version is not recorded."
            if not blocked_target
            else "The mBER version is not recorded for execution.",
        ),
        _check(
            "runtime_identity",
            bool(runtime_ok),
            "Container runtime identity and version are recorded."
            if runtime_ok
            else "Container runtime identity is not recorded.",
        ),
        _check(
            "image_identity",
            image_ok,
            "Immutable image digest is recorded."
            if image_ok
            else "Immutable image digest is not recorded.",
        ),
        _check(
            "entrypoint",
            entry_ok,
            "Server-owned entrypoint is recorded."
            if entry_ok
            else "Server-owned entrypoint is not recorded.",
        ),
        _check(
            "argv_template",
            argv_ok,
            "Server-owned argv template is recorded."
            if argv_ok
            else "Server-owned argv template is not recorded.",
        ),
        _check(
            "resource_envelope",
            envelope_ok,
            "Resource envelope is within bounds."
            if envelope_ok
            else "Resource envelope is not recorded.",
        ),
        _check(
            "target_scope",
            scope_ok,
            "Target scope matches the execution profile."
            if scope_ok
            else "Target scope is not recorded.",
        ),
        _check(
            "expected_inputs",
            inputs_ok,
            "Canonical expected inputs match the profile."
            if inputs_ok
            else "Canonical expected inputs are not recorded.",
        ),
        _check(
            "expected_outputs",
            outputs_ok,
            "Canonical expected outputs match the profile."
            if outputs_ok
            else "Canonical expected outputs are not recorded.",
        ),
        _check(
            "timeout_limit",
            timeout_ok,
            "Timeout limit is recorded." if timeout_ok else "Timeout limit is not recorded.",
        ),
        _check(
            "output_limit",
            output_ok,
            "Output limit is recorded." if output_ok else "Output limit is not recorded.",
        ),
        _check(probe_code, probe_ok, probe_text),
    ]
    if blocked_target:
        checks.append(
            _check(
                "target_execution_blocked",
                False,
                "CD22 and TACI remain blocked. The mBER version and other scientific requirements are not recorded.",
            )
        )
    return tuple(checks)


def _probe_check(manifest: _Manifest, observation: ProbeObservation) -> tuple[str, bool, str]:
    if observation.outcome == "passed":
        if _observation_matches(manifest, observation):
            return "readiness_probe", True, "The readiness probe succeeded."
        return (
            "readiness_probe_mismatch",
            False,
            "The readiness observation does not match the server manifest.",
        )
    texts = {
        "rejected": "The readiness probe reported that the runner is not ready.",
        "timeout": "The readiness probe timed out.",
        "malformed": "The readiness probe reply was rejected.",
        "failed": "The readiness probe failed.",
        "not_recorded": "The readiness probe result is not recorded.",
    }
    codes = {
        "rejected": "readiness_probe_rejected",
        "timeout": "readiness_probe_timeout",
        "malformed": "readiness_probe_malformed",
        "failed": "readiness_probe_failed",
        "not_recorded": "readiness_probe_not_recorded",
    }
    outcome = observation.outcome if observation.outcome in codes else "failed"
    return codes[outcome], False, texts[outcome]


def _observation_matches(manifest: _Manifest, observation: ProbeObservation) -> bool:
    if type(observation.ready) is not bool or observation.ready is not True:
        return False
    expected = {
        "manifest_digest": manifest.digest,
        "profile_id": manifest.profile_id,
        "tool_name": manifest.tool_name,
        "tool_version": manifest.tool_version,
        "runtime_name": manifest.runtime_name,
        "runtime_version": manifest.runtime_version,
        "image_digest": manifest.image_digest,
        "target_scope": manifest.target_scope,
    }
    for name, want in expected.items():
        got = getattr(observation, name)
        if type(got) is not str or type(want) is not str or got != want:
            return False
    return True


def _tool_matches(manifest: _Manifest) -> bool:
    expected = _PROFILE_IDENTITY.get(manifest.profile_id or "")
    return expected is not None and manifest.tool_name == expected[0]


def _scope_matches(manifest: _Manifest) -> bool:
    expected = _PROFILE_IDENTITY.get(manifest.profile_id or "")
    return expected is not None and manifest.target_scope == expected[1]


def _envelope_ok(envelope: _Envelope | None) -> bool:
    if envelope is None:
        return False
    values = {
        "cpu_count": envelope.cpu_count,
        "memory_mib": envelope.memory_mib,
        "gpu_count": envelope.gpu_count,
        "timeout_seconds": envelope.timeout_seconds,
        "max_output_bytes": envelope.max_output_bytes,
    }
    return all(_in_bounds(name, value) for name, value in values.items())


def _limit_matches(manifest: _Manifest, name: str) -> bool:
    if not _envelope_ok(manifest.envelope):
        return False
    assert manifest.envelope is not None
    top = manifest.timeout_seconds if name == "timeout_seconds" else manifest.max_output_bytes
    nested = (
        manifest.envelope.timeout_seconds
        if name == "timeout_seconds"
        else manifest.envelope.max_output_bytes
    )
    return _in_bounds(name, top) and top == nested


def _in_bounds(name: str, value: int | None) -> bool:
    if type(value) is not int:
        return False
    low, high = _LIMITS[name]
    return low <= value <= high


def _check(code: str, passed: bool, text: str) -> ReadinessCheck:
    return ReadinessCheck(code, "passed" if passed else "blocked", text)


def _version(value: str | None) -> bool:
    if value is None or not _token(value):
        return False
    return value.casefold() not in _REJECTED_VERSIONS


def _token(value: str | None) -> bool:
    return type(value) is str and _TOKEN.fullmatch(value) is not None


def _public_token(value: str | None) -> str | None:
    return value if _token(value) else None


def _public_version(value: str | None) -> str | None:
    return value if _version(value) else None


def _public_id(value: str | None) -> str | None:
    if value in _KNOWN_PROFILES:
        return value
    return None


def _read_manifest(path: str) -> tuple[dict[str, Any], str]:
    try:
        file = Path(path)
        info = file.stat()
        if not file.is_file() or info.st_size > _MAX_MANIFEST_BYTES:
            raise _ManifestRejected(
                "manifest_too_large"
                if info.st_size > _MAX_MANIFEST_BYTES
                else "manifest_unreadable"
            )
        raw = file.read_bytes()
    except _ManifestRejected:
        raise
    except OSError:
        raise _ManifestRejected("manifest_unreadable") from None
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise _ManifestRejected("manifest_too_large")
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(text, object_pairs_hook=_object_pairs)
    except _ManifestRejected:
        raise
    except (UnicodeError, json.JSONDecodeError):
        raise _ManifestRejected("manifest_invalid") from None
    if type(parsed) is not dict:
        raise _ManifestRejected("manifest_invalid")
    _reject_nonfinite(parsed)
    try:
        canonical = json.dumps(
            parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
    except ValueError:
        raise _ManifestRejected("manifest_invalid") from None
    if text != canonical:
        raise _ManifestRejected("manifest_not_canonical")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return parsed, digest


def _object_pairs(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _value in pairs:
        if type(key) is not str or key in seen:
            raise _ManifestRejected("manifest_not_canonical")
        seen.add(key)
    return dict(pairs)


def _parse_manifest(document: dict[str, Any], digest: str) -> _Manifest:
    schema = document.get("schema_version")
    if type(schema) is not str:
        raise _ManifestRejected(
            "invalid_manifest_type" if "schema_version" in document else "unsupported_schema"
        )
    if schema == SCHEMA_VERSION_V2:
        return _parse_v2(document, digest)
    if set(document) - _TOP_KEYS:
        raise _ManifestRejected("manifest_invalid")
    if schema != SCHEMA_VERSION:
        raise _ManifestRejected("unsupported_schema")
    profile_id = _optional_token(document, "profile_id")
    if profile_id is not None and profile_id not in _KNOWN_PROFILES:
        raise _ManifestRejected("unsupported_profile")
    return _Manifest(
        profile_id=profile_id,
        tool_name=_optional_token(document, "tool_name"),
        tool_version=_optional_token(document, "tool_version"),
        runtime_name=_optional_token(document, "runtime_name"),
        runtime_version=_optional_token(document, "runtime_version"),
        image_digest=_optional_digest(document),
        entrypoint=_optional_token(document, "entrypoint"),
        argv_template=_optional_argv(document),
        envelope=_optional_envelope(document),
        target_scope=_optional_scope(document),
        inputs=_optional_contracts(document, "expected_inputs"),
        outputs=_optional_contracts(document, "expected_outputs"),
        timeout_seconds=_optional_limit(document, "timeout_seconds"),
        max_output_bytes=_optional_limit(document, "max_output_bytes"),
        digest=digest,
    )


def _parse_v2(document: dict[str, Any], digest: str) -> _Manifest:
    if set(document) != _V2_KEYS:
        raise _ManifestRejected("manifest_invalid")
    _reject_nonfinite(document)
    profile_id = _optional_token(document, "profile_id")
    if profile_id not in _PDL1_PROFILES:
        raise _ManifestRejected("unsupported_profile")
    manifest = _Manifest(
        profile_id=profile_id,
        tool_name=_optional_token(document, "tool_name"),
        tool_version=_optional_token(document, "tool_version"),
        runtime_name=_optional_token(document, "runtime_name"),
        runtime_version=_optional_token(document, "runtime_version"),
        image_digest=_optional_digest(document),
        entrypoint=_optional_token(document, "entrypoint"),
        argv_template=_optional_argv(document),
        envelope=_optional_envelope(document),
        target_scope=_optional_scope(document),
        inputs=_optional_contracts(document, "expected_inputs"),
        outputs=_optional_contracts(document, "expected_outputs"),
        timeout_seconds=_optional_limit(document, "timeout_seconds"),
        max_output_bytes=_optional_limit(document, "max_output_bytes"),
        digest=digest,
        input_identity=_input_identity(document["input_identity"]),
    )
    if not _v2_complete(manifest):
        raise _ManifestRejected("manifest_invalid")
    return manifest


def _v2_complete(manifest: _Manifest) -> bool:
    identity = manifest.input_identity
    envelope = manifest.envelope
    return (
        identity is not None
        and manifest.tool_name == "bindcraft"
        and _version(manifest.tool_version)
        and _version(manifest.runtime_name)
        and _version(manifest.runtime_version)
        and _IMAGE_DIGEST.fullmatch(manifest.image_digest or "") is not None
        and _token(manifest.entrypoint)
        and bool(manifest.argv_template)
        and manifest.target_scope == "PDL1"
        and manifest.inputs == _PDL1_INPUTS
        and manifest.outputs == _PDL1_OUTPUTS
        and envelope is not None
        and _envelope_ok(envelope)
        and _limit_matches(manifest, "timeout_seconds")
        and _limit_matches(manifest, "max_output_bytes")
    )


def _input_identity(value: object) -> _InputIdentity:
    if type(value) is not dict:
        raise _ManifestRejected("invalid_manifest_type")
    if set(value) != _INPUT_KEYS:
        raise _ManifestRejected("manifest_invalid")
    digests = {}
    for name in ("target_structure_sha256", "settings_sha256", "advanced_profile_sha256"):
        item = value[name]
        if type(item) is not str:
            raise _ManifestRejected("invalid_manifest_type")
        if _MANIFEST_DIGEST.fullmatch(item) is None:
            raise _ManifestRejected("manifest_invalid")
        digests[name] = item
    policies = {
        "argv_policy_version": ARGV_POLICY_VERSION,
        "directory_policy": DIRECTORY_POLICY,
        "environment_policy": ENVIRONMENT_POLICY,
    }
    recorded = {}
    for name, expected in policies.items():
        item = value[name]
        if type(item) is not str:
            raise _ManifestRejected("invalid_manifest_type")
        if item != expected:
            raise _ManifestRejected("unsupported_policy")
        recorded[name] = item
    return _InputIdentity(
        target_structure_sha256=digests["target_structure_sha256"],
        settings_sha256=digests["settings_sha256"],
        advanced_profile_sha256=digests["advanced_profile_sha256"],
        argv_policy_version=recorded["argv_policy_version"],
        directory_policy=recorded["directory_policy"],
        environment_policy=recorded["environment_policy"],
    )


def _reject_nonfinite(value: object) -> None:
    if type(value) is float and not math.isfinite(value):
        raise _ManifestRejected("manifest_invalid")
    if type(value) is dict:
        for item in value.values():
            _reject_nonfinite(item)
    elif type(value) is list:
        for item in value:
            _reject_nonfinite(item)


def _optional_token(document: dict[str, Any], name: str) -> str | None:
    if name not in document or document[name] is None:
        return None
    value = document[name]
    if type(value) is not str:
        raise _ManifestRejected("invalid_manifest_type")
    if _unsafe(value) or not _token(value):
        raise _ManifestRejected("manifest_invalid")
    return value


def _optional_scope(document: dict[str, Any]) -> str | None:
    if "target_scope" not in document or document["target_scope"] is None:
        return None
    value = document["target_scope"]
    if type(value) is not str:
        raise _ManifestRejected("invalid_manifest_type")
    if value not in {"PDL1", "CD22", "TACI"}:
        raise _ManifestRejected("manifest_invalid")
    return value


def _optional_digest(document: dict[str, Any]) -> str | None:
    if "image_digest" not in document or document["image_digest"] is None:
        return None
    value = document["image_digest"]
    if type(value) is not str:
        raise _ManifestRejected("invalid_manifest_type")
    if _unsafe(value):
        raise _ManifestRejected("manifest_invalid")
    return value


def _argv_token_ok(item: str) -> bool:
    """Slash-free tokens, plus the one approved no_filters path.

    ``_unsafe`` still rejects every other slash. ``..`` stays rejected
    on that path too. This is not a general filesystem argv grammar.
    """

    if item == CONTAINER_NO_FILTERS:
        return ".." not in item.split("/")
    return (
        not _unsafe(item)
        and _ARGV_TOKEN.fullmatch(item) is not None
        and ".." not in item.split("/")
    )


def _optional_argv(document: dict[str, Any]) -> tuple[str, ...] | None:
    if "argv_template" not in document or document["argv_template"] is None:
        return None
    value = document["argv_template"]
    if type(value) is not list:
        raise _ManifestRejected("invalid_manifest_type")
    if any(type(item) is not str for item in value):
        raise _ManifestRejected("invalid_manifest_type")
    if any(not _argv_token_ok(item) for item in value):
        raise _ManifestRejected("manifest_invalid")
    if not value:
        return None
    if len(value) > 16:
        raise _ManifestRejected("manifest_invalid")
    return tuple(value)


def _optional_limit(document: dict[str, Any], name: str) -> int | None:
    if name not in document or document[name] is None:
        return None
    value = document[name]
    if type(value) is not int:
        raise _ManifestRejected("invalid_manifest_type")
    return value


def _optional_envelope(document: dict[str, Any]) -> _Envelope | None:
    if "resource_envelope" not in document or document["resource_envelope"] is None:
        return None
    value = document["resource_envelope"]
    if type(value) is not dict:
        raise _ManifestRejected("invalid_manifest_type")
    if set(value) - _ENVELOPE_KEYS:
        raise _ManifestRejected("manifest_invalid")
    numbers = {name: _optional_limit(value, name) for name in _ENVELOPE_KEYS}
    return _Envelope(
        cpu_count=numbers["cpu_count"],
        memory_mib=numbers["memory_mib"],
        gpu_count=numbers["gpu_count"],
        timeout_seconds=numbers["timeout_seconds"],
        max_output_bytes=numbers["max_output_bytes"],
    )


def _optional_contracts(document: dict[str, Any], name: str) -> tuple[tuple[str, str], ...] | None:
    if name not in document or document[name] is None:
        return None
    value = document[name]
    if type(value) is not list:
        raise _ManifestRejected("invalid_manifest_type")
    rows: list[tuple[str, str]] = []
    for item in value:
        if type(item) is not dict:
            raise _ManifestRejected("invalid_manifest_type")
        if set(item) != _CONTRACT_KEYS:
            raise _ManifestRejected("manifest_invalid")
        contract_name = item["name"]
        media_type = item["media_type"]
        if type(contract_name) is not str or type(media_type) is not str:
            raise _ManifestRejected("invalid_manifest_type")
        if (
            _unsafe(contract_name)
            or not _token(contract_name)
            or _MEDIA_TYPE.fullmatch(media_type) is None
        ):
            raise _ManifestRejected("manifest_invalid")
        rows.append((contract_name, media_type))
    if not rows:
        return None
    return tuple(rows)


def _unsafe(value: str) -> bool:
    return any(
        char in value for char in ("/", "\\", "\n", "\r", "\x00", " ", "$", "`", ";", "&", "|")
    )


def _stamp(clock: Callable[[], datetime] | None) -> str:
    moment = datetime.now(UTC) if clock is None else clock()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ApplicationContractError("naive_timestamp")
    recorded = moment.astimezone(UTC).replace(tzinfo=UTC)
    return recorded.isoformat()


def readiness_reply_is_public(reply: ReadinessReply) -> bool:
    try:
        _require_public_reply(reply)
    except ApplicationContractError:
        return False
    return True


def _require_public_reply(reply: ReadinessReply) -> None:
    if (
        reply.execution_authorized is not False
        or reply.scientific_execution is not False
        or reply.scientific_validation is not False
    ):
        raise ApplicationContractError("execution_not_authorized")
    if type(reply.status) is not str or reply.status not in _STATUSES:
        raise ApplicationContractError("invalid_readiness_status")
    if not _public_reply_shape(reply):
        raise ApplicationContractError("invalid_readiness_reply")


def _public_reply_shape(reply: ReadinessReply) -> bool:
    if not _safe_public_text(reply.text) or not _token(reply.session_id):
        return False
    if not _canonical_observed_at(reply.observed_at):
        return False
    if not _optional_identity(reply.profile_id, reply.tool_name, reply.target_scope):
        return False
    if not _optional_version(reply.tool_version):
        return False
    if not _optional_version(reply.runtime_name) or not _optional_version(reply.runtime_version):
        return False
    if not _optional_pattern(reply.image_digest, _IMAGE_DIGEST):
        return False
    if not _optional_pattern(reply.manifest_digest, _MANIFEST_DIGEST):
        return False
    if not _checks_match_status(reply.status, reply.checks):
        return False
    if reply.status != "ready":
        return True
    return (
        reply.profile_id in _PDL1_PROFILES
        and reply.tool_name == "bindcraft"
        and reply.target_scope == "PDL1"
        and _version(reply.tool_version)
        and _version(reply.runtime_name)
        and _version(reply.runtime_version)
        and _IMAGE_DIGEST.fullmatch(reply.image_digest or "") is not None
        and _MANIFEST_DIGEST.fullmatch(reply.manifest_digest or "") is not None
    )


def _checks_match_status(status: str, checks: object) -> bool:
    if type(checks) is not tuple or not checks:
        return False
    seen: set[str] = set()
    blocked = 0
    for check in checks:
        if type(check) is not ReadinessCheck:
            return False
        if check.code in seen or check.code not in _CHECK_CODES:
            return False
        if check.status not in {"passed", "blocked"} or not _safe_public_text(check.text):
            return False
        seen.add(check.code)
        if check.status == "blocked":
            blocked += 1
    if status == "ready":
        return blocked == 0 and seen == _READY_CODES
    if status == "blocked":
        return blocked > 0
    if status == "disabled":
        return any(
            check.code == "runner_disabled" and check.status == "blocked" for check in checks
        )
    return blocked == len(checks)


def _optional_identity(profile_id: object, tool_name: object, target_scope: object) -> bool:
    if profile_id is not None and (
        type(profile_id) is not str or profile_id not in _PROFILE_IDENTITY
    ):
        return False
    if tool_name is not None and (
        type(tool_name) is not str or tool_name not in {"bindcraft", "mber"}
    ):
        return False
    if target_scope is not None and (
        type(target_scope) is not str or target_scope not in {"PDL1", "CD22", "TACI"}
    ):
        return False
    if type(profile_id) is str:
        expected_tool, expected_target = _PROFILE_IDENTITY[profile_id]
        if tool_name not in {None, expected_tool} or target_scope not in {None, expected_target}:
            return False
    if tool_name == "bindcraft" and target_scope not in {None, "PDL1"}:
        return False
    if tool_name == "mber" and target_scope == "PDL1":
        return False
    if target_scope == "CD22" and tool_name not in {None, "mber"}:
        return False
    if target_scope == "TACI" and tool_name not in {None, "mber"}:
        return False
    if target_scope == "PDL1" and tool_name not in {None, "bindcraft"}:
        return False
    if tool_name == "mber" and target_scope == "CD22" and profile_id not in {None, CD22_PROFILE_ID}:
        return False
    return not (
        tool_name == "mber" and target_scope == "TACI" and profile_id not in {None, TACI_PROFILE_ID}
    )


def _optional_version(value: object) -> bool:
    return value is None or (type(value) is str and _version(value))


def _optional_pattern(value: object, pattern: re.Pattern[str]) -> bool:
    return value is None or (type(value) is str and pattern.fullmatch(value) is not None)


def _safe_public_text(value: object) -> bool:
    if type(value) is not str or not value or len(value) > 500:
        return False
    if any(char in value for char in "\n\r\x00/\\`"):
        return False
    return _UNSAFE_PUBLIC.search(value) is None


def _canonical_observed_at(value: object) -> bool:
    if type(value) is not str or _OBSERVED_AT.fullmatch(value) is None:
        return False
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return False
    return moment.utcoffset() == timedelta(0) and moment.isoformat() == value
