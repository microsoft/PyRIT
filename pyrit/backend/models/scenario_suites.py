# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""REST envelopes for scenario suite definition endpoints."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from pyrit.models.catalog.scenario_preset import PresetIssue
from pyrit.models.catalog.scenario_suite import ScenarioSuite

__all__ = [
    "ScenarioSuiteEntryStatus",
    "ScenarioSuiteListResponse",
    "ScenarioSuiteResponse",
    "UpdateScenarioSuiteRequest",
]


class ScenarioSuiteEntryStatus(BaseModel):
    """Current deployment-specific status for one stored suite entry."""

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
    """A stored scenario suite, its document version, and current entry statuses."""

    suite: ScenarioSuite = Field(..., description="The stored scenario suite definition")
    version: str = Field(..., description="Opaque version required to update the suite")
    entry_statuses: list[ScenarioSuiteEntryStatus] = Field(
        default_factory=list,
        description="Preset origin, version, and advisory issues in the suite's entry order",
    )


class ScenarioSuiteListResponse(BaseModel):
    """The configured suite storage source and every readable suite."""

    source: str = Field(..., description="Credential-free configured suite source")
    items: list[ScenarioSuiteResponse] = Field(..., description="Stored suites, sorted by name")


class UpdateScenarioSuiteRequest(BaseModel):
    """Request body for replacing a scenario suite the caller has read."""

    suite: ScenarioSuite = Field(..., description="The replacement suite")
    expected_version: str = Field(..., description="Version returned when the suite being edited was read")
