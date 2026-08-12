# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Canonical models for durable scenario run plans and incremental progress."""

from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from pyrit.models.identifiers.atomic_attack_identifier import AtomicAttackIdentifier
from pyrit.models.results.attack_result import AttackOutcome
from pyrit.models.results.scenario_result import ScenarioRunState
from pyrit.models.retry_event import RetryEvent

SCENARIO_RUN_PLAN_METADATA_KEY = "run_plan"
SCENARIO_RUN_PLAN_VERSION = 1


class ScenarioRunPlanSeedGroup(BaseModel):
    """A de-duplicated logical seed group in a scenario run plan."""

    id: str
    objective_sha256: str
    objective: str


class ScenarioRunPlanAtomicGroup(BaseModel):
    """A planned atomic-attack group and its ordered units of work."""

    id: str
    atomic_attack_name: str
    display_group: str
    technique_eval_hash: str
    seed_group_ids: list[str]


class ScenarioRunPlan(BaseModel):
    """Versioned normalized execution plan persisted in ScenarioResult metadata."""

    version: Literal[1] = 1
    scenario_registry_name: str | None = None
    atomic_groups: list[ScenarioRunPlanAtomicGroup]
    seed_groups: list[ScenarioRunPlanSeedGroup]

    @model_validator(mode="after")
    def _validate_normalized_plan(self) -> "ScenarioRunPlan":
        """
        Reject ambiguous IDs and invalid normalized references.

        Returns:
            ScenarioRunPlan: The validated normalized plan.

        Raises:
            ValueError: If IDs are duplicated or a group references an unknown seed.
        """
        atomic_group_ids = [group.id for group in self.atomic_groups]
        if len(atomic_group_ids) != len(set(atomic_group_ids)):
            raise ValueError("Scenario run plan contains duplicate atomic group IDs.")

        seed_group_ids = [seed.id for seed in self.seed_groups]
        if len(seed_group_ids) != len(set(seed_group_ids)):
            raise ValueError("Scenario run plan contains duplicate seed group IDs.")

        known_seed_group_ids = set(seed_group_ids)
        for group in self.atomic_groups:
            if len(group.seed_group_ids) != len(set(group.seed_group_ids)):
                raise ValueError(f"Scenario run plan atomic group '{group.id}' contains duplicate seed group IDs.")
            missing_seed_group_ids = set(group.seed_group_ids) - known_seed_group_ids
            if missing_seed_group_ids:
                raise ValueError(
                    f"Scenario run plan atomic group '{group.id}' references unknown seed group IDs: "
                    f"{', '.join(sorted(missing_seed_group_ids))}."
                )
        return self


class ScenarioProgressHeader(BaseModel):
    """Compact persisted run header returned by the progress endpoint."""

    scenario_result_id: str
    scenario_name: str
    scenario_registry_name: str | None = None
    scenario_version: int
    status: ScenarioRunState
    created_at: datetime
    completed_at: datetime | None = None


class ScenarioProgressResult(BaseModel):
    """One persisted attack attempt in ascending progress order."""

    attack_result_id: str
    atomic_group_id: str
    atomic_attack_name: str
    seed_group_id: str
    outcome: AttackOutcome
    execution_time_ms: int
    timestamp: AwareDatetime
    total_retries: int = 0
    retries: list[RetryEvent] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None


class ScenarioRunProgress(BaseModel):
    """Incremental scenario progress response."""

    run: ScenarioProgressHeader
    plan: ScenarioRunPlan | None = None
    reset: bool = False
    active_atomic_group_ids: list[str] = Field(default_factory=list)
    results: list[ScenarioProgressResult] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    plan_complete: bool


class ScenarioAttackResultDelta(BaseModel):
    """Lightweight memory projection used to map one scenario progress delta."""

    attack_result_id: str
    objective: str
    objective_sha256: str | None = None
    atomic_attack_identifier: AtomicAttackIdentifier | None = None
    outcome: AttackOutcome
    execution_time_ms: int
    timestamp: AwareDatetime
    retry_events: list[RetryEvent] = Field(default_factory=list)
    total_retries: int = 0
    error_type: str | None = None
    error_message: str | None = None
    attribution_data: dict[str, Any] = Field(default_factory=dict)
