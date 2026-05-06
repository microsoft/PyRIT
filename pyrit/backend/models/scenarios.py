# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario API response models.

Scenarios are multi-attack security testing campaigns. These models represent
the metadata about available scenarios (listing) and scenario execution (runs).
"""

from datetime import datetime
from enum import StrEnum
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


# ============================================================================
# Scenario Run Models
# ============================================================================


class ScenarioRunStatus(StrEnum):
    """Status of a scenario run."""

    PENDING = "pending"
    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunScenarioRequest(BaseModel):
    """Request body for starting a scenario run."""

    scenario_name: str = Field(..., description="Registry key of the scenario to run")
    target_name: str = Field(..., description="Name of a registered target from the TargetRegistry")
    initializers: list[str] | None = Field(
        None, description="Initializer names to run before scenario (e.g., ['target', 'load_default_datasets'])"
    )
    strategies: list[str] | None = Field(None, description="Strategy names to use (uses scenario default if omitted)")
    dataset_names: list[str] | None = Field(
        None, description="Dataset names to use (uses scenario default if omitted)"
    )
    max_dataset_size: int | None = Field(None, ge=1, description="Maximum items per dataset")
    max_concurrency: int = Field(10, ge=1, le=100, description="Maximum concurrent operations")
    max_retries: int = Field(0, ge=0, le=10, description="Maximum retry attempts on failure")
    memory_labels: dict[str, str] | None = Field(None, description="Labels to attach to memory entries")


class ScenarioRunResult(BaseModel):
    """Summary of a completed scenario run's results."""

    scenario_result_id: str = Field(..., description="UUID of the ScenarioResult in memory")
    run_state: str = Field(..., description="Final scenario run state (COMPLETED, FAILED)")
    strategies_used: list[str] = Field(..., description="Strategy names that were executed")
    total_attacks: int = Field(..., ge=0, description="Total number of atomic attacks")
    completed_attacks: int = Field(..., ge=0, description="Number of attacks that completed")
    number_tries: int = Field(..., ge=0, description="Number of execution attempts")
    completion_time: datetime | None = Field(None, description="When the scenario finished")


class ScenarioRunResponse(BaseModel):
    """Response for a scenario run (status + optional result)."""

    run_id: str = Field(..., description="Unique identifier for this run")
    scenario_name: str = Field(..., description="Registry key of the scenario being run")
    status: ScenarioRunStatus = Field(..., description="Current run status")
    created_at: datetime = Field(..., description="When the run was created")
    updated_at: datetime = Field(..., description="When the run status last changed")
    error: str | None = Field(None, description="Error message if status is FAILED")
    result: ScenarioRunResult | None = Field(None, description="Result details if status is COMPLETED")


class ScenarioRunListResponse(BaseModel):
    """Response for listing scenario runs."""

    items: list[ScenarioRunResponse] = Field(..., description="List of scenario runs")


# ============================================================================
# Scenario Results Detail Models
# ============================================================================


class AttackResultDetail(BaseModel):
    """Detailed result of a single attack within a scenario."""

    attack_result_id: str = Field(..., description="Unique ID of this attack result")
    conversation_id: str = Field(..., description="Conversation ID that produced this result")
    objective: str = Field(..., description="Natural-language description of the attacker's objective")
    outcome: str = Field(..., description="Attack outcome: success, failure, or undetermined")
    outcome_reason: str | None = Field(None, description="Reason for the outcome")
    last_response: str | None = Field(None, description="Model response from the final turn")
    score_value: str | None = Field(None, description="Score value from the objective scorer")
    executed_turns: int = Field(0, ge=0, description="Number of turns executed")
    execution_time_ms: int = Field(0, ge=0, description="Execution time in milliseconds")
    timestamp: datetime | None = Field(None, description="When the result was created")


class AtomicAttackResults(BaseModel):
    """Results grouped by atomic attack name."""

    atomic_attack_name: str = Field(..., description="Name of the atomic attack (strategy)")
    display_group: str | None = Field(None, description="Display group label for UI grouping")
    results: list[AttackResultDetail] = Field(..., description="Individual attack results")
    success_count: int = Field(0, ge=0, description="Number of successful attacks")
    failure_count: int = Field(0, ge=0, description="Number of failed attacks")
    total_count: int = Field(0, ge=0, description="Total number of attack results")


class ScenarioResultDetailResponse(BaseModel):
    """Full detailed results of a scenario run."""

    scenario_result_id: str = Field(..., description="UUID of the ScenarioResult")
    scenario_name: str = Field(..., description="Name of the scenario")
    scenario_version: int = Field(..., description="Version of the scenario")
    run_state: str = Field(..., description="Final run state (COMPLETED, FAILED, etc.)")
    objective_achieved_rate: int = Field(..., ge=0, le=100, description="Success rate as percentage (0-100)")
    number_tries: int = Field(..., ge=0, description="Number of execution attempts")
    completion_time: datetime | None = Field(None, description="When the scenario finished")
    labels: dict[str, str] = Field(default_factory=dict, description="Labels attached to this run")
    attacks: list[AtomicAttackResults] = Field(..., description="Results grouped by atomic attack")
