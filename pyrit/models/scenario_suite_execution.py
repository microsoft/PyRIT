# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Versioned metadata linking ordinary scenario results to one suite execution."""

from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SCENARIO_SUITE_EXECUTION_METADATA_KEY = "scenario_suite_execution_v1"
SCENARIO_SUITE_EXECUTION_METADATA_VERSION = 1


class ScenarioSuiteExecutionMetadata(BaseModel):
    """Identity stamped on every scenario result successfully submitted by a suite launch."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = SCENARIO_SUITE_EXECUTION_METADATA_VERSION
    suite_execution_id: UUID = Field(..., description="Server-owned identity for one suite launch")
    suite_name: str = Field(..., description="Resolved suite name")
    suite_version: str | None = Field(
        None,
        description="Resolved stored suite version, or None for a suite registered in code",
    )
    suite_entry_id: UUID = Field(..., description="Stable identity of the launched suite entry")
    entry_position: int = Field(..., ge=1, description="One-based position in the resolved suite")
    preset_name: str = Field(..., description="Resolved preset name")
    preset_version: str | None = Field(
        None,
        description="Resolved stored preset version, or None for a preset registered in code",
    )

    def to_metadata_fields(self) -> dict[str, Any]:
        """
        Return the namespaced scenario metadata entry owned by this model.

        Returns:
            dict[str, Any]: JSON-compatible fields for ``ScenarioResult.metadata``.
        """
        return {SCENARIO_SUITE_EXECUTION_METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> Self | None:
        """
        Read suite execution metadata from a scenario result.

        Returns:
            Self | None: The validated envelope, or None for a direct or legacy run.
        """
        raw = metadata.get(SCENARIO_SUITE_EXECUTION_METADATA_KEY)
        return None if raw is None else cls.model_validate(raw)
