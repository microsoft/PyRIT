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
from pyrit.models.catalog.scenario import RegisteredScenario, ScenarioRunSummary

__all__ = [
    "ListRegisteredScenariosResponse",
    "ScenarioRunListResponse",
]


class ListRegisteredScenariosResponse(BaseModel):
    """Response for listing scenarios."""

    items: list[RegisteredScenario] = Field(..., description="List of scenario summaries")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class ScenarioRunListResponse(BaseModel):
    """Response for listing scenario runs."""

    items: list[ScenarioRunSummary] = Field(..., description="List of scenario runs")
