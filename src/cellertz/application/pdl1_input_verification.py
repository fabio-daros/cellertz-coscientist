"""Read-only verification of PDL1 inputs declared by manifest v2.

Verification proves the identity of bytes captured during the call.
It does not prove that an approved scientific request selected those
inputs, and it does not authorize execution. A later reopen of the
same path is not the verified input. Future execution must consume
the returned snapshot or an independently verified staged copy.

Opening requires POSIX ``O_NOFOLLOW``, ``O_NONBLOCK``, and
``O_CLOEXEC`` (Linux and macOS). The final path component is not
followed. Intermediate directory symlinks are ordinary path
resolution. If those flags are unavailable, verification is rejected.

Each read stops after limit + 1 bytes. ``stat`` size is not the
ceiling. The server ceiling is ``MAX_INPUT_BYTES``. Advanced-profile
JSON nesting is capped at ``MAX_JSON_DEPTH``. Errors are fixed codes
and do not include paths or file bytes.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field
from typing import Any

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.pdl1_executor import (
    ARGV_POLICY_VERSION,
    DIRECTORY_POLICY,
    ENVIRONMENT_POLICY,
    Pdl1ValidatedInputIdentity,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    canonical_advanced_profile_bytes,
)

MAX_INPUT_BYTES = 1_048_576
MAX_JSON_DEPTH = 32
_READ_CHUNK = 65536
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class _Rejected(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _flags() -> int | None:
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")):
        return None
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC  # type: ignore[attr-defined]


def _hex(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _captured(payload: object, size: object) -> bool:
    if type(payload) is not bytes or type(size) is not int:
        return False
    if size < 1 or size > MAX_INPUT_BYTES:
        return False
    return len(payload) == size


def _positive(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_INPUT_BYTES


def _absolute(value: object) -> bool:
    return type(value) is str and "\0" not in value and os.path.isabs(value)


@dataclass(frozen=True)
class Pdl1InputLocations:
    """Absolute paths supplied by trusted server composition."""

    target_structure: str
    settings: str
    advanced_profile: str

    def __post_init__(self) -> None:
        if not all(
            _absolute(item)
            for item in (self.target_structure, self.settings, self.advanced_profile)
        ):
            raise ApplicationContractError("pdl1_input_location_invalid")


@dataclass(frozen=True)
class Pdl1InputLimits:
    """Per-input ceilings. Each value is a positive exact integer."""

    target_structure_bytes: int
    settings_bytes: int
    advanced_profile_bytes: int
    canonical_advanced_profile_bytes: int

    def __post_init__(self) -> None:
        if not all(
            _positive(item)
            for item in (
                self.target_structure_bytes,
                self.settings_bytes,
                self.advanced_profile_bytes,
                self.canonical_advanced_profile_bytes,
            )
        ):
            raise ApplicationContractError("pdl1_input_limit_invalid")


@dataclass(frozen=True)
class Pdl1VerifiedInputs:
    """Captured bytes whose hashes match one validated v2 declaration.

    Paths are not stored. Replacing a source file does not change this
    object. Request compatibility and external execution are not claimed.
    """

    manifest_digest: str
    target_structure_sha256: str
    settings_sha256: str
    advanced_profile_sha256: str
    argv_policy_version: str
    directory_policy: str
    environment_policy: str
    target_structure_size: int
    settings_size: int
    advanced_profile_canonical_size: int
    target_structure: bytes = field(repr=False)
    settings: bytes = field(repr=False)
    advanced_profile_canonical: bytes = field(repr=False)

    def __post_init__(self) -> None:
        captured = (
            (self.target_structure, self.target_structure_size, self.target_structure_sha256),
            (self.settings, self.settings_size, self.settings_sha256),
            (
                self.advanced_profile_canonical,
                self.advanced_profile_canonical_size,
                self.advanced_profile_sha256,
            ),
        )
        if not all(_captured(payload, size) for payload, size, _digest in captured):
            raise ApplicationContractError("pdl1_verified_inputs_invalid")
        digests = (
            hashlib.sha256(self.target_structure).hexdigest(),
            hashlib.sha256(self.settings).hexdigest(),
            hashlib.sha256(self.advanced_profile_canonical).hexdigest(),
        )
        matched = (
            digests
            == (
                self.target_structure_sha256,
                self.settings_sha256,
                self.advanced_profile_sha256,
            )
            and _hex(self.manifest_digest)
            and self.argv_policy_version == ARGV_POLICY_VERSION
            and self.directory_policy == DIRECTORY_POLICY
            and self.environment_policy == ENVIRONMENT_POLICY
        )
        if not matched:
            raise ApplicationContractError("pdl1_verified_inputs_invalid")


def verify_pdl1_inputs(
    identity: object,
    locations: object,
    limits: object,
) -> Pdl1VerifiedInputs:
    """Read the three inputs and compare them with a v2 declaration."""

    try:
        return _verify(identity, locations, limits)
    except _Rejected as rejected:
        raise ApplicationContractError(rejected.code) from None


def _verify(identity: object, locations: object, limits: object) -> Pdl1VerifiedInputs:
    if type(identity) is not Pdl1ValidatedInputIdentity:
        raise _Rejected("pdl1_input_identity_invalid")
    if type(locations) is not Pdl1InputLocations:
        raise _Rejected("pdl1_input_location_invalid")
    if type(limits) is not Pdl1InputLimits:
        raise _Rejected("pdl1_input_limit_invalid")
    if _flags() is None:
        raise _Rejected("pdl1_input_verification_unsupported")
    target = _read_regular(locations.target_structure, limits.target_structure_bytes)
    settings = _read_regular(locations.settings, limits.settings_bytes)
    profile = _read_regular(locations.advanced_profile, limits.advanced_profile_bytes)
    if hashlib.sha256(target).hexdigest() != identity.target_structure_sha256:
        raise _Rejected("pdl1_input_digest_mismatch")
    if hashlib.sha256(settings).hexdigest() != identity.settings_sha256:
        raise _Rejected("pdl1_input_digest_mismatch")
    canonical = _canonical_profile(profile, limits.canonical_advanced_profile_bytes)
    if hashlib.sha256(canonical).hexdigest() != identity.advanced_profile_sha256:
        raise _Rejected("pdl1_input_digest_mismatch")
    return Pdl1VerifiedInputs(
        manifest_digest=identity.manifest_digest,
        target_structure_sha256=identity.target_structure_sha256,
        settings_sha256=identity.settings_sha256,
        advanced_profile_sha256=identity.advanced_profile_sha256,
        argv_policy_version=identity.argv_policy_version,
        directory_policy=identity.directory_policy,
        environment_policy=identity.environment_policy,
        target_structure_size=len(target),
        settings_size=len(settings),
        advanced_profile_canonical_size=len(canonical),
        target_structure=target,
        settings=settings,
        advanced_profile_canonical=canonical,
    )


def _read_regular(path: str, limit: int) -> bytes:
    flags = _flags()
    if flags is None:
        raise _Rejected("pdl1_input_verification_unsupported")
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise _Rejected("pdl1_input_not_regular") from None
        raise _Rejected("pdl1_input_unreadable") from None
    rejected: _Rejected | None = None
    completed = False
    try:
        try:
            mode = os.fstat(descriptor).st_mode
        except OSError:
            raise _Rejected("pdl1_input_unreadable") from None
        if not stat.S_ISREG(mode):
            raise _Rejected("pdl1_input_not_regular")
        payload = _read_bounded(descriptor, limit)
        completed = True
        return payload
    except _Rejected as error:
        rejected = error
        raise
    except OSError:
        rejected = _Rejected("pdl1_input_unreadable")
        raise rejected from None
    finally:
        close_failed = False
        try:
            os.close(descriptor)
        except OSError:
            close_failed = True
        if close_failed and rejected is None and completed:
            raise _Rejected("pdl1_input_unreadable") from None


def _read_bounded(descriptor: int, limit: int) -> bytes:
    remaining = limit + 1
    chunks: list[bytes] = []
    while remaining > 0:
        try:
            block = os.read(descriptor, min(_READ_CHUNK, remaining))
        except OSError:
            raise _Rejected("pdl1_input_unreadable") from None
        if not block:
            break
        chunks.append(block)
        remaining -= len(block)
    payload = b"".join(chunks)
    if not payload:
        raise _Rejected("pdl1_input_empty")
    if len(payload) > limit:
        raise _Rejected("pdl1_input_too_large")
    return payload


def _canonical_profile(payload: bytes, canonical_limit: int) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        raise _Rejected("pdl1_advanced_profile_invalid") from None
    if not _depth_within(text, MAX_JSON_DEPTH):
        raise _Rejected("pdl1_advanced_profile_invalid")
    try:
        parsed = json.loads(text, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except _Rejected:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise _Rejected("pdl1_advanced_profile_invalid") from None
    if type(parsed) is not dict:
        raise _Rejected("pdl1_advanced_profile_invalid")
    _finite(parsed)
    try:
        canonical = canonical_advanced_profile_bytes(parsed)
    except (TypeError, ValueError):
        raise _Rejected("pdl1_advanced_profile_invalid") from None
    if len(canonical) > canonical_limit:
        raise _Rejected("pdl1_canonical_profile_too_large")
    return canonical


def _pairs(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _value in pairs:
        if type(key) is not str or key in seen:
            raise _Rejected("pdl1_advanced_profile_invalid")
        seen.add(key)
    return dict(pairs)


def _reject_constant(_value: str) -> None:
    raise _Rejected("pdl1_advanced_profile_invalid")


def _finite(value: object) -> None:
    if type(value) is float and not math.isfinite(value):
        raise _Rejected("pdl1_advanced_profile_invalid")
    if type(value) is dict:
        for item in value.values():
            _finite(item)
    elif type(value) is list:
        for item in value:
            _finite(item)


def _depth_within(text: str, limit: int) -> bool:
    depth = 0
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            if depth > limit:
                return False
        elif char in "}]":
            depth -= 1
    return True
