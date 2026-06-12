# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario catalog and run-summary models.

These describe canonical PyRIT entities exposed over the REST catalog and
scenario-run endpoints; both the backend and external REST clients (the CLI
today) consume them. REST envelopes (pagination, list wrappers) stay in
``pyrit.backend.models``.

Per-field documentation strings (``Field(..., description=...)``) deliberately
live in the backend layer rather than here — see ``pyrit.models.MessagePiece``
vs ``pyrit.backend.models.attacks.MessagePieceView`` for the same split.
Validators that affect runtime behavior (``ge``, ``le``) remain on the
canonical models.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from pyrit.models.scenario_result import ScenarioRunState


class ScenarioParameterSummary(BaseModel):
    """Summary of a scenario-declared parameter."""

    name: str
    description: str
    default: str | None = None
    param_type: str
    choices: list[str] | None = None
    is_list: bool = False


class RegisteredScenario(BaseModel):
    """Summary of a registered scenario."""

    scenario_name: str
    scenario_type: str
    description: str
    default_strategy: str
    aggregate_strategies: list[str]
    all_strategies: list[str]
    default_datasets: list[str]
    max_dataset_size: int | None = None
    supported_parameters: list[ScenarioParameterSummary] = Field(default_factory=list)


class RunScenarioRequest(BaseModel):
    """Request body for starting a scenario run."""

    scenario_name: str
    target_name: str
    initializers: list[str] | None = None
    strategies: list[str] | None = None
    dataset_names: list[str] | None = None
    max_dataset_size: int | None = Field(None, ge=1)
    max_concurrency: int = Field(10, ge=1, le=100)
    max_retries: int = Field(0, ge=0, le=20)
    labels: dict[str, str] | None = None
    scenario_params: dict[str, Any] | None = None
    initializer_args: dict[str, dict[str, Any]] | None = None
    scenario_result_id: str | None = None


class ScenarioRunSummary(BaseModel):
    """Response for a scenario run (status + result details)."""

    scenario_result_id: str
    scenario_name: str
    scenario_version: int = Field(0, ge=0)
    status: ScenarioRunState
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    error_type: str | None = None
    strategies_used: list[str] = Field(default_factory=list)
    total_attacks: int = Field(0, ge=0)
    completed_attacks: int = Field(0, ge=0)
    objective_achieved_rate: int = Field(0, ge=0, le=100)
    labels: dict[str, str] = Field(default_factory=dict)
    completed_at: datetime | None = None
