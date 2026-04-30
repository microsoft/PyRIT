# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario API response models.

Scenarios are multi-attack security testing campaigns. These models represent
the metadata about available scenarios (listing), not scenario execution results.
"""

from typing import Optional

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo


class ScenarioSummary(BaseModel):
    """Summary of a registered scenario."""

    scenario_name: str = Field(..., description="Registry key (e.g., 'foundry.red_team_agent')")
    scenario_type: str = Field(..., description="Scenario type identifier (e.g., 'RedTeamAgentScenario')")
    description: str = Field(..., description="Human-readable description of the scenario")
    default_strategy: str = Field(..., description="Default strategy name used when none specified")
    aggregate_strategies: list[str] = Field(
        ..., description="Aggregate strategies that combine multiple attack approaches"
    )
    all_strategies: list[str] = Field(..., description="All available concrete strategy names")
    default_datasets: list[str] = Field(..., description="Default dataset names used by the scenario")
    max_dataset_size: Optional[int] = Field(None, description="Maximum items per dataset (None means unlimited)")


class ScenarioListResponse(BaseModel):
    """Response for listing scenarios."""

    items: list[ScenarioSummary] = Field(..., description="List of scenario summaries")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")
