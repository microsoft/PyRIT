# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario catalog and run-summary models.

These describe canonical PyRIT entities exposed over the REST catalog and
scenario-run endpoints; both the backend and external REST clients (the CLI
today) consume them. REST envelopes (pagination, list wrappers) stay in
``pyrit.backend.models``.

Validators that affect runtime behavior (``ge``, ``le``) remain on the
canonical models.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from pyrit.models.parameter import Parameter
from pyrit.models.results.scenario_result import ScenarioRunState


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
    max_dataset_size: int | None = Field(None, description="Maximum items per dataset (None means unlimited)")
    supported_parameters: list[Parameter] = Field(
        default_factory=list, description="Scenario-declared custom parameters"
    )


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
    status: ScenarioRunState = Field(..., description="Current run status")
    created_at: datetime = Field(..., description="When the run was created")
    updated_at: datetime = Field(..., description="When the run status last changed")
    error: str | None = Field(None, description="Error message if status is FAILED")
    error_type: str | None = Field(None, description="Exception class name if status is FAILED")
    strategies_used: list[str] = Field(default_factory=list, description="Strategy names that were executed")
    total_attacks: int = Field(0, ge=0, description="Total number of attack results persisted for this run")
    completed_attacks: int = Field(0, ge=0, description="Number of attacks that reached a terminal outcome")
    objective_achieved_rate: int = Field(0, ge=0, le=100, description="Success rate as percentage (0-100)")
    labels: dict[str, str] = Field(default_factory=dict, description="Labels attached to this run")
    completed_at: datetime | None = Field(None, description="When the scenario finished")
