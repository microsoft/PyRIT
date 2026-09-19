# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes for the scenario endpoints.

Canonical scenario catalog/run types (``RegisteredScenario``,
``ScenarioRunSummary``, ``RunScenarioRequest``) live in
``pyrit.models.catalog.scenario`` and should be imported from there directly.
Scenario parameters are described by the shared ``pyrit.models.Parameter``.
"""

from pydantic import BaseModel, ConfigDict, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models.catalog.scenario import RegisteredScenario, ScenarioRunListItem

__all__ = [
    "ListRegisteredScenariosResponse",
    "ResumeScenarioRunRequest",
    "ScenarioResumeOptions",
    "ScenarioRunListResponse",
]


class ListRegisteredScenariosResponse(BaseModel):
    """Response for listing scenarios."""

    items: list[RegisteredScenario] = Field(..., description="List of scenario summaries")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class ScenarioRunListResponse(BaseModel):
    """Response for listing scenario runs."""

    items: list[ScenarioRunListItem] = Field(..., description="List of scenario runs")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class ScenarioResumeOptions(BaseModel):
    """Whether an older run needs explicit execution settings before resuming."""

    requires_execution_options: bool


class ResumeScenarioRunRequest(BaseModel):
    """User-selected execution settings only for runs that did not persist them."""

    model_config = ConfigDict(extra="forbid")

    max_concurrency: int = Field(..., ge=1, le=100)
    max_retries: int = Field(..., ge=0, le=20)
