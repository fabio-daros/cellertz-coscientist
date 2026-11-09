"""Versioned PDL1 input selection. Digests only. No filesystem paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cellertz.application.contracts import ApplicationContractError

SELECTION_SCHEMA = "cellertz.pdl1_input_selection.v1"
_DIGEST_LENGTH = 64


def _hex(value: object) -> bool:
    return type(value) is str and len(value) == _DIGEST_LENGTH and all(
        char in "0123456789abcdef" for char in value
    )


@dataclass(frozen=True)
class Pdl1InputSelection:
    """Inputs named by an approved revision. Not a caller-supplied path list."""

    manifest_digest: str
    target_structure_sha256: str
    settings_sha256: str
    advanced_profile_sha256: str
    schema: str = SELECTION_SCHEMA

    def __post_init__(self) -> None:
        digests = (
            self.manifest_digest,
            self.target_structure_sha256,
            self.settings_sha256,
            self.advanced_profile_sha256,
        )
        if self.schema != SELECTION_SCHEMA or not all(_hex(item) for item in digests):
            raise ApplicationContractError("pdl1_input_selection_invalid")

    @staticmethod
    def from_verified(verified: object) -> Pdl1InputSelection:
        from cellertz.application.pdl1_input_verification import Pdl1VerifiedInputs

        if type(verified) is not Pdl1VerifiedInputs:
            raise ApplicationContractError("pdl1_input_selection_invalid")
        return Pdl1InputSelection(
            manifest_digest=verified.manifest_digest,
            target_structure_sha256=verified.target_structure_sha256,
            settings_sha256=verified.settings_sha256,
            advanced_profile_sha256=verified.advanced_profile_sha256,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "advanced_profile_sha256": self.advanced_profile_sha256,
            "manifest_digest": self.manifest_digest,
            "schema": self.schema,
            "settings_sha256": self.settings_sha256,
            "target_structure_sha256": self.target_structure_sha256,
        }

    def matches_verified(self, verified: object) -> bool:
        from cellertz.application.pdl1_input_verification import Pdl1VerifiedInputs

        if type(verified) is not Pdl1VerifiedInputs:
            return False
        return self.to_dict() == Pdl1InputSelection.from_verified(verified).to_dict()


def selection_document(selection: Pdl1InputSelection | None) -> dict[str, Any] | None:
    if selection is None:
        return None
    if type(selection) is not Pdl1InputSelection:
        raise ApplicationContractError("pdl1_input_selection_invalid")
    return selection.to_dict()
