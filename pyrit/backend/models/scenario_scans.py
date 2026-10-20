# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""REST envelopes for scenario scan definition endpoints."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from pyrit.models.catalog.scenario_preset import PresetIssue
from pyrit.models.catalog.scenario_scan import ScenarioScan

__all__ = [
    "ScenarioScanEntryStatus",
    "ScenarioScanListResponse",
    "ScenarioScanResponse",
    "UpdateScenarioScanRequest",
]


class ScenarioScanEntryStatus(BaseModel):
    """Current deployment-specific status for one stored scan entry."""

    entry_id: UUID = Field(..., description="Stable identifier of the scan entry this status describes")
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
        description="Advisory issues for this entry; never used to reject saving the scan",
    )


class ScenarioScanResponse(BaseModel):
    """A stored scenario scan, its document version, and current entry statuses."""

    scan: ScenarioScan = Field(..., description="The stored scenario scan definition")
    version: str = Field(..., description="Opaque version required to update the scan")
    entry_statuses: list[ScenarioScanEntryStatus] = Field(
        default_factory=list,
        description="Preset origin, version, and advisory issues in the scan's entry order",
    )


class ScenarioScanListResponse(BaseModel):
    """The configured scan storage source and every readable scan."""

    source: str = Field(..., description="Credential-free configured scan source")
    items: list[ScenarioScanResponse] = Field(..., description="Stored scans, sorted by name")


class UpdateScenarioScanRequest(BaseModel):
    """Request body for replacing a scenario scan the caller has read."""

    scan: ScenarioScan = Field(..., description="The replacement scan")
    expected_version: str = Field(..., description="Version returned when the scan being edited was read")
