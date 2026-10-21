# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from pyrit.models.results.attack_result import AttackResultRole


@dataclass(frozen=True, slots=True, kw_only=True)
class AttackResultMetadata:
    """Producer-recorded role and immediate-parent position stored in ``AttackResult.attribution_data``."""

    RESULT_ROLE_KEY: ClassVar[str] = "result_role"
    ATTEMPT_INDEX_KEY: ClassVar[str] = "attempt_index"

    result_role: AttackResultRole = AttackResultRole.UNKNOWN
    attempt_index: int | None = None

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize the semantic metadata fragment without parent linkage.

        Returns:
            dict[str, Any]: The role and optional child position using the existing storage keys.
        """
        metadata: dict[str, Any] = {self.RESULT_ROLE_KEY: self.result_role.value}
        if self.attempt_index is not None:
            metadata[self.ATTEMPT_INDEX_KEY] = self.attempt_index
        return metadata

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any] | None) -> AttackResultMetadata:
        """
        Read semantic metadata without inferring a role from conversations or parent linkage.

        Args:
            metadata (dict[str, Any] | None): Stored attribution data, including legacy records.

        Returns:
            AttackResultMetadata: Unknown for unrecognized roles and None for absent or invalid child positions.
        """
        metadata = metadata or {}
        try:
            result_role = AttackResultRole(metadata.get(cls.RESULT_ROLE_KEY))
        except ValueError:
            result_role = AttackResultRole.UNKNOWN
        attempt_index = metadata.get(cls.ATTEMPT_INDEX_KEY)
        if not isinstance(attempt_index, int) or isinstance(attempt_index, bool) or attempt_index < 1:
            attempt_index = None
        return cls(result_role=result_role, attempt_index=attempt_index)
