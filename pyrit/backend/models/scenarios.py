# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario API response models.

Scenarios are multi-attack security testing campaigns. These models represent
the metadata about available scenarios (listing) and scenario execution (runs).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from pyrit.backend.models.attacks import AttackSummary
from pyrit.backend.models.common import PaginationInfo


class RegisteredScenario(BaseModel):
    """Summary of a registered scenario."""

    scenario_name: str = Field(..., description="Scenario name  (e.g., 'foundry.red_team_agent')")
    scenario_type: str = Field(..., description="Scenario type identifier (e.g., 'RedTeamAgentScenario')")
    description: str = Field(..., description="Human-readable description of the scenario")
    default_strategy: str = Field(..., description="Default strategy name used when none specified")
    aggregate_strategies: list[str] = Field(
        ..., description="Aggregate strategies that combine multiple attack approaches"
    )
    all_strategies: list[str] = Field(..., description="All available concrete strategy names")
    default_datasets: list[str] = Field(..., description="Default dataset names used by the scenario")
    max_dataset_size: Optional[int] = Field(None, description="Maximum items per dataset (None means unlimited)")


class ListRegisteredScenariosResponse(BaseModel):
    """Response for listing scenarios."""

    items: list[RegisteredScenario] = Field(..., description="List of scenario summaries")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


# ============================================================================
# Scenario Run Models
# ============================================================================


class ScenarioRunStatus(str, Enum):
    """Status of a scenario run, aligned with core ScenarioRunState."""

    CREATED = "CREATED"
    INITIALIZING = "INITIALIZING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunScenarioRequest(BaseModel):
    """Request body for starting a scenario run."""

    scenario_name: str = Field(..., description="Scenario name (e.g., 'foundry.red_team_agent')")
    target_name: str = Field(..., description="Name of a registered target from the TargetRegistry")
    initializers: list[str] | None = Field(
        None, description="Initializer names to run before scenario (e.g., ['target', 'load_default_datasets'])"
    )
    strategies: list[str] | None = Field(None, description="Strategy names to use (uses scenario default if omitted)")
    dataset_names: list[str] | None = Field(None, description="Dataset names to use (uses scenario default if omitted)")
    max_dataset_size: int | None = Field(None, ge=1, description="Maximum items per dataset")
    max_concurrency: int = Field(10, ge=1, le=100, description="Maximum concurrent operations")
    max_retries: int = Field(0, ge=0, le=20, description="Maximum retry attempts on failure")
    labels: dict[str, str] | None = Field(None, description="Labels to attach to memory entries")
    scenario_params: dict[str, Any] | None = Field(
        None,
        description="Custom parameters for the scenario (passed to scenario.set_params_from_args). "
        "Keys are parameter names declared by the scenario's supported_parameters().",
    )
    initializer_args: dict[str, dict[str, Any]] | None = Field(
        None,
        description="Per-initializer arguments keyed by initializer name. "
        "Each value is a dict of args passed to that initializer's set_params_from_args(). "
        "Example: {'target': {'endpoint': 'https://...'}}.",
    )
    scenario_result_id: str | None = Field(
        None,
        description="Optional ID of an existing ScenarioResult to resume. "
        "If provided, the scenario will resume from prior progress instead of starting fresh.",
    )


class ScenarioRunSummary(BaseModel):
    """Response for a scenario run (status + result details)."""

    scenario_result_id: str = Field(..., description="UUID of the ScenarioResult in memory")
    scenario_name: str = Field(..., description="Registry key of the scenario being run")
    scenario_version: int = Field(0, ge=0, description="Version of the scenario")
    status: ScenarioRunStatus = Field(..., description="Current run status")
    created_at: datetime = Field(..., description="When the run was created")
    updated_at: datetime = Field(..., description="When the run status last changed")
    error: str | None = Field(None, description="Error message if status is FAILED")
    strategies_used: list[str] = Field(default_factory=list, description="Strategy names that were executed")
    total_attacks: int = Field(0, ge=0, description="Total number of atomic attacks")
    completed_attacks: int = Field(0, ge=0, description="Number of attacks that completed")
    objective_achieved_rate: int = Field(0, ge=0, le=100, description="Success rate as percentage (0-100)")
    labels: dict[str, str] = Field(default_factory=dict, description="Labels attached to this run")
    completed_at: datetime | None = Field(None, description="When the scenario finished")


class ScenarioRunListResponse(BaseModel):
    """Response for listing scenario runs."""

    items: list[ScenarioRunSummary] = Field(..., description="List of scenario runs")


# ============================================================================
# Scenario Results Detail Models
# ============================================================================


class AtomicAttackResults(BaseModel):
    """Results grouped by atomic attack name."""

    atomic_attack_name: str = Field(..., description="Name of the atomic attack (strategy)")
    display_group: str | None = Field(None, description="Display group label for UI grouping")
    results: list[AttackSummary] = Field(..., description="Individual attack results")
    success_count: int = Field(0, ge=0, description="Number of successful attacks")
    failure_count: int = Field(0, ge=0, description="Number of failed attacks")
    total_count: int = Field(0, ge=0, description="Total number of attack results")


class ScenarioRunDetail(BaseModel):
    """Full detailed results of a scenario run."""

    run: ScenarioRunSummary = Field(..., description="The scenario run summary")
    attacks: list[AtomicAttackResults] = Field(..., description="Results grouped by atomic attack")
