"""Deterministic manifest serialization and hashing.

execution_id, run_id, timestamps, and the hash itself are metadata.
They do not change the scientific identity of a manifest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from cellertz.execution.contracts import ScientificExecutionManifest


def canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def identity_payload(manifest: ScientificExecutionManifest) -> dict[str, Any]:
    """Scientific / operational identity. No attempt metadata."""
    return {
        "adapter_name": manifest.adapter_name,
        "adapter_version": manifest.adapter_version,
        "checkpoint_hash": manifest.checkpoint_hash,
        "checkpoint_name": manifest.checkpoint_name,
        "container_digest": manifest.container_digest,
        "container_image": manifest.container_image,
        "data_access": manifest.data_access.to_dict(),
        "domain": manifest.domain,
        "execution_mode": manifest.execution_mode.value,
        "input_hashes": _jsonable(dict(manifest.input_hashes)),
        "operation": manifest.operation,
        "parameters": _jsonable(dict(manifest.parameters)),
        "runtime_backend": manifest.runtime_backend.value,
        "seed": manifest.seed,
        "tool_name": manifest.tool_name,
        "tool_version": manifest.tool_version,
    }


def manifest_sha256(manifest: ScientificExecutionManifest) -> str:
    encoded = canonical_json(identity_payload(manifest)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def freeze_manifest(
    manifest: ScientificExecutionManifest,
) -> ScientificExecutionManifest:
    return replace(manifest, manifest_sha256=manifest_sha256(manifest))
