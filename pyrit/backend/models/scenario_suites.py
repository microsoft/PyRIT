# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""REST envelopes for scenario suite definition and execution endpoints."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.catalog.scenario import ScenarioRunSummary
from pyrit.models.catalog.scenario_preset import PresetIssue
from pyrit.models.catalog.scenario_suite import ScenarioSuite
from pyrit.models.request_limits import MAX_ITEMS

__all__ = [
    "LaunchScenarioSuiteRequest",
    "ScenarioSuiteExecutionEntryState",
    "ScenarioSuiteExecutionEntrySummary",
    "ScenarioSuiteExecutionStatus",
    "ScenarioSuiteExecutionSummary",
    "ScenarioSuiteLaunchEntrySnapshot",
    "ScenarioSuiteEntryStatus",
    "ScenarioSuiteListResponse",
    "ScenarioSuiteResponse",
    "UpdateScenarioSuiteRequest",
]


class ScenarioSuiteEntryStatus(BaseModel):
    """Current deployment-specific status for one suite entry."""

    entry_id: UUID = Field(..., description="Stable identifier of the suite entry this status describes")
    preset_name: str = Field(..., description="Referenced scenario preset name")
    origin: Literal["stored", "registered"] | None = Field(
        None,
        description="Current preset origin, or None when the preset cannot be resolved",
    )
    preset_version: str | None = Field(
        None,
        description="Current stored preset version, or None for registered or unresolved presets",
    )
    issues: list[PresetIssue] = Field(
        default_factory=list,
        description="Advisory issues for this entry; never used to reject saving the suite",
    )


class ScenarioSuiteResponse(BaseModel):
    """A scenario suite, its origin, optional document version, and current entry statuses."""

    suite: ScenarioSuite = Field(..., description="The scenario suite definition")
    origin: Literal["stored", "registered"] = Field(
        "stored",
        description=(
            "Whether a stored document or registering code contributed this suite. A registered suite "
            "is held in memory by the code that registered it and cannot be updated or deleted here."
        ),
    )
    version: str | None = Field(
        ...,
        description=(
            "Opaque version required to update a stored suite. None for a registered suite, "
            "which has no document to guard."
        ),
    )
    entry_statuses: list[ScenarioSuiteEntryStatus] = Field(
        default_factory=list,
        description="Preset origin, version, and advisory issues in the suite's entry order",
    )


class ScenarioSuiteListResponse(BaseModel):
    """The configured suite storage source and every readable suite from either origin."""

    source: str = Field(..., description="Credential-free configured suite source")
    items: list[ScenarioSuiteResponse] = Field(..., description="Stored and registered suites, sorted by name")


class UpdateScenarioSuiteRequest(BaseModel):
    """Request body for replacing a scenario suite the caller has read."""

    suite: ScenarioSuite = Field(..., description="The replacement suite")
    expected_version: str = Field(..., description="Version returned when the suite being edited was read")


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


class LaunchScenarioSuiteRequest(BaseModel):
    """Launch-owned fields and optimistic preconditions for one suite execution."""

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
    target_name: str = Field(..., min_length=1, description="Registered objective target used by every entry")
    adversarial_target_name: str | None = Field(
        None,
        min_length=1,
        description="Registered adversarial target used by entries that require one",
    )
    initializers: list[str] | None = Field(
        None,
        max_length=MAX_ITEMS,
        description="Initializer names applied to every child scenario run",
    )
    initializer_args: dict[str, dict[str, Any]] | None = Field(
        None,
        max_length=MAX_ITEMS,
        description="Per-initializer parameter overrides keyed by initializer name",
    )
    max_concurrency: int | None = Field(
        None,
        ge=1,
        le=100,
        description="Maximum concurrency applied independently to every child run",
    )
    max_retries: int | None = Field(
        None,
        ge=0,
        le=20,
        description="Maximum retries applied independently to every child run",
    )
    operation_id: UUID | None = Field(None, description="Saved Operation attributed to every child result")
    labels: dict[str, str] | None = Field(None, description="Labels attached to every child result")


class ScenarioSuiteExecutionStatus(str, Enum):
    """Process-local lifecycle of one accepted suite execution."""

    QUEUING = "QUEUING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ScenarioSuiteExecutionEntryState(str, Enum):
    """Submission or scenario-run state for one suite entry."""

    PENDING = "PENDING"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


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
