# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared ScenarioSuite launch, execution, and child-attribution contracts."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Self, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.catalog.scenario import (
    ScenarioLaunchConfiguration,
    ScenarioRunSummary,
)
from pyrit.models.request_limits import MAX_ITEMS
from pyrit.models.results.scenario_result import ScenarioRunState

SCENARIO_SUITE_EXECUTION_METADATA_KEY = "scenario_suite_execution"
SCENARIO_SUITE_EXECUTION_METADATA_VERSION = 1


class ScenarioSuiteLaunchEntrySnapshot(BaseModel):
    """The preset state the operator reviewed for one suite entry."""

    model_config = ConfigDict(extra="forbid")

    entry_id: UUID = Field(..., description="Stable suite-entry identity")
    preset_name: str = Field(..., description="Preset name shown for this entry")
    origin: Literal["stored", "registered"] | None = Field(
        ...,
        description="Reviewed preset origin, or None when the entry was unresolved",
    )
    preset_version: str | None = Field(
        ...,
        description="Reviewed stored preset version, or None for registered or unresolved presets",
    )


class LaunchScenarioSuiteRequest(ScenarioLaunchConfiguration):
    """Launch configuration and optimistic preconditions for one suite execution."""

    model_config = ConfigDict(extra="forbid")

    expected_version: str | None = Field(
        None,
        description="Reviewed stored suite version; None only for a suite registered in code",
    )
    entries: list[ScenarioSuiteLaunchEntrySnapshot] = Field(
        ...,
        min_length=1,
        max_length=MAX_ITEMS,
        description="Reviewed preset snapshots in the suite's declared order",
    )


class ScenarioSuiteExecutionStatus(str, Enum):
    """Process-local lifecycle of one accepted suite execution."""

    QUEUING = "QUEUING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ScenarioSuiteSubmissionState(str, Enum):
    """Suite-owned state before a child becomes an ordinary scenario run."""

    PENDING = "PENDING"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"


ScenarioSuiteExecutionEntryState: TypeAlias = ScenarioSuiteSubmissionState | ScenarioRunState


class ScenarioSuiteExecutionEntrySummary(BaseModel):
    """Current process-local state for one ordered suite entry."""

    entry_id: UUID = Field(..., description="Stable suite-entry identity")
    entry_position: int = Field(..., ge=1, description="One-based position in the resolved suite")
    preset_name: str = Field(..., description="Resolved preset name")
    preset_origin: Literal["stored", "registered"] = Field(..., description="Resolved preset origin")
    preset_version: str | None = Field(
        None,
        description="Resolved stored preset version, or None for a registered preset",
    )
    state: ScenarioSuiteExecutionEntryState = Field(..., description="Submission or child-run state")
    run: ScenarioRunSummary | None = Field(None, description="Current child run summary after successful submission")
    error: str | None = Field(
        None,
        description="User-safe submission or status failure message when no child run can be shown",
    )
    error_type: str | None = Field(
        None,
        description="Internal exception class name for diagnostics; clients should not display it",
    )


class ScenarioSuiteExecutionSummary(BaseModel):
    """Transient progress and grouped child results for one suite launch."""

    suite_execution_id: UUID = Field(..., description="Server-owned identity for this suite launch")
    suite_name: str = Field(..., description="Resolved suite name")
    suite_origin: Literal["stored", "registered"] = Field(..., description="Resolved suite origin")
    suite_version: str | None = Field(
        None,
        description="Resolved stored suite version, or None for a suite registered in code",
    )
    status: ScenarioSuiteExecutionStatus = Field(..., description="Aggregate process-local execution state")
    created_at: datetime = Field(..., description="When the suite execution was accepted")
    updated_at: datetime = Field(..., description="Latest submission or child-run update")
    entries: list[ScenarioSuiteExecutionEntrySummary] = Field(
        ...,
        description="Entry states in the suite's declared order",
    )


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
