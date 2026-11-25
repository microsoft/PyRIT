# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes for the scenario endpoints.

Canonical scenario catalog/run types (``RegisteredScenario``,
``ScenarioRunSummary``, ``RunScenarioRequest``) live in
``pyrit.models.catalog.scenario`` and should be imported from there directly.
Scenario parameters are described by the shared ``pyrit.models.Parameter``.
"""

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import ScenarioModuleSummary, ScenarioRunListItem
from pyrit.models.catalog import RegisteredScenario

__all__ = [
    "ListRegisteredScenariosResponse",
    "ListScenarioModulesResponse",
    "ScenarioRunListResponse",
]


class ListRegisteredScenariosResponse(BaseModel):
    """Response for listing scenarios."""

    items: list[RegisteredScenario] = Field(..., description="List of scenario summaries")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class ListScenarioModulesResponse(BaseModel):
    """Response for listing the built-in scenario packages."""

    items: list[ScenarioModuleSummary] = Field(..., description="Scenario packages, sorted by display name")


class ScenarioRunListResponse(BaseModel):
    """Response for listing scenario runs."""

    items: list[ScenarioRunListItem] = Field(..., description="List of scenario runs")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")
