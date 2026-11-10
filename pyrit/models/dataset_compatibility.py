# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read-only dataset compatibility projections shared by the SDK and API."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DatasetCompatibilityStatus(str, Enum):
    """Whether stored contents have passed the selected scenario's checks."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    NOT_VERIFIED = "not_verified"


class DatasetRequirementDescription(BaseModel):
    """A public requirement, without its executable validator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str


class DatasetCompatibilityReason(BaseModel):
    """One unmet requirement, optionally aggregated across source groups."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    requirement: str = "scenario"
    count: int | None = Field(default=None, ge=0)


class DatasetTechniqueCompatibility(BaseModel):
    """Independent coverage of one delivery method before selection limits."""

    technique_name: str
    is_baseline: bool = False
    usable_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    reasons: list[DatasetCompatibilityReason] = Field(default_factory=list)


class DatasetCompatibility(BaseModel):
    """Compatibility of one named dataset for the current scenario configuration."""

    name: str
    status: DatasetCompatibilityStatus
    selected: bool = False
    group_count: int | None = Field(default=None, ge=0)
    reasons: list[DatasetCompatibilityReason] = Field(default_factory=list)
    techniques: list[DatasetTechniqueCompatibility] = Field(default_factory=list)


class ScenarioDatasetCompatibility(BaseModel):
    """Candidate hints and fully checked selected populations, never a launch authorization."""

    requirements: list[DatasetRequirementDescription] = Field(default_factory=list)
    items: list[DatasetCompatibility] = Field(default_factory=list)
    selection_compatible: bool | None = None
    selection_reasons: list[DatasetCompatibilityReason] = Field(default_factory=list)
