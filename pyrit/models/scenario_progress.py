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
from pyrit.models.score.score import ScoreStatus

SCENARIO_RUN_PLAN_METADATA_KEY = "run_plan"
SCENARIO_RUN_PLAN_VERSION = 1


class ScenarioRunPlanSeedGroup(BaseModel):
    """A de-duplicated logical seed group in a scenario run plan."""

    id: str
    objective_sha256: str
    objective: str
    prompts: list["ScenarioRunPlanSeedPrompt"] = Field(default_factory=list)


class ScenarioRunPlanSeedPrompt(BaseModel):
    """One non-objective prompt persisted with a logical seed group."""

    value: str
    data_type: str | None = None
    role: str | None = None
    sequence: int
    parameters: list[str] = Field(default_factory=list)


class ScenarioRunPlanAtomicGroup(BaseModel):
    """A planned atomic-attack group and its ordered units of work."""

    id: str
    atomic_attack_name: str
    display_group: str
    technique_name: str | None = None
    technique_eval_hash: str
    seed_group_ids: list[str]
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


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


class ScenarioProgressScore(BaseModel):
    """The objective score attached to one persisted scenario attack result."""

    scorer_name: str
    score_type: Literal["true_false", "float_scale", "unknown"]
    status: ScoreStatus
    score_value: str | None = None
    score_rationale: str | None = None


class ScenarioComponentIdentity(BaseModel):
    """Display-safe projection of a component's behavioral identity."""

    component_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    children: dict[str, list["ScenarioComponentIdentity"]] = Field(default_factory=dict)


class ScenarioAttackTechniqueDetails(ScenarioComponentIdentity):
    """REST details for the technique used by one scenario attack attempt."""


class ScenarioProgressResult(BaseModel):
    """One persisted attack attempt in ascending progress order."""

    attack_result_id: str
    conversation_id: str
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
    score: ScenarioProgressScore | None = None


class ScenarioProgressCounts(BaseModel):
    """Canonical progress counts for a set of scenario execution units."""

    completed: int = Field(..., ge=0)
    planned: int | None = Field(default=None, ge=0)
    succeeded: int = Field(..., ge=0)
    success_percentage: int | None = Field(default=None, ge=0, le=100)
    errors: int = Field(..., ge=0)
    retries: int = Field(..., ge=0)


class ScenarioTechniqueProgress(ScenarioProgressCounts):
    """Progress for one scenario technique."""

    id: str
    display_group: str
    atomic_attack_names: list[str]
    #: Member atomic group IDs, so clients can attribute attempts without matching display text.
    atomic_group_ids: list[str] = Field(default_factory=list)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class ScenarioDisplayGroupProgress(ScenarioProgressCounts):
    """Progress for one scenario-defined display group."""

    id: str
    display_group: str
    atomic_attack_names: list[str]
    #: Member atomic group IDs, so clients can attribute attempts without matching display text.
    atomic_group_ids: list[str] = Field(default_factory=list)


class ScenarioSeedGroupProgress(ScenarioProgressCounts):
    """Progress for one logical seed group across atomic attacks."""

    id: str
    objective: str | None = None


class ScenarioAtomicGroupProgress(ScenarioProgressCounts):
    """Progress for one planned atomic-attack group."""

    id: str
    atomic_attack_name: str
    display_group: str
    status: Literal["RUNNING", "PENDING", "INCOMPLETE", "COMPLETED"]
    technique_details: ScenarioAttackTechniqueDetails | None = None


class ScenarioObjectiveScorerMetrics(BaseModel):
    """Official evaluation metrics for an objective scorer configuration."""

    accuracy: float = Field(..., ge=0, le=1)
    accuracy_standard_error: float | None = Field(default=None, ge=0)
    f1_score: float | None = Field(default=None, ge=0, le=1)
    precision: float | None = Field(default=None, ge=0, le=1)
    recall: float | None = Field(default=None, ge=0, le=1)
    average_score_time_seconds: float | None = Field(default=None, ge=0)


class ScenarioScorerIdentity(ScenarioComponentIdentity):
    """Display identity for a scorer and its nested sub-scorers."""


class ScenarioObjectiveScorer(ScenarioScorerIdentity):
    """Objective scorer identity and official evaluation metrics."""

    metrics: ScenarioObjectiveScorerMetrics | None = None


class ScenarioProgressSummary(BaseModel):
    """Backend-owned progress rollups for a scenario run."""

    overall: ScenarioProgressCounts
    objective_scorer: ScenarioObjectiveScorer | None = None
    display_groups: list[ScenarioDisplayGroupProgress] = Field(default_factory=list)
    techniques: list[ScenarioTechniqueProgress] = Field(default_factory=list)
    seed_groups: list[ScenarioSeedGroupProgress] = Field(default_factory=list)
    atomic_groups: list[ScenarioAtomicGroupProgress] = Field(default_factory=list)
    #: Persisted attempts that matched no planned execution unit and are therefore absent
    #: from every rollup above. Non-zero means the rollups understate what actually ran.
    unattributed_attempts: int = Field(default=0, ge=0)


class ScenarioRunProgress(BaseModel):
    """Canonical rollups and an incremental page of scenario progress results."""

    run: ScenarioProgressHeader
    plan: ScenarioRunPlan | None = None
    results: list[ScenarioProgressResult] = Field(default_factory=list)
    summary: ScenarioProgressSummary
    next_cursor: str | None = None
    has_more: bool = False
    plan_complete: bool


class ScenarioAttackResultDelta(BaseModel):
    """Lightweight memory projection used to map one scenario progress delta."""

    attack_result_id: str
    conversation_id: str
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
    score: ScenarioProgressScore | None = None
