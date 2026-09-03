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
from enum import Enum
from math import prod
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from pyrit.models.parameter import Parameter
from pyrit.models.results.scenario_result import ScenarioRunState
from pyrit.models.retry_event import RetryEvent

# Authoritative set of dataset seed filters exposed over the run request surface. Each entry
# is used verbatim as a ``MemoryInterface.get_seeds`` keyword argument, so a filter key IS the
# get_seeds kwarg. Every exposed filter must be a list-valued (Sequence) get_seeds parameter.
# Adding a filterable field is a one-line change here; the CLI ``--dataset-filters`` help text
# describes these keys, and this request model validates them server-side (covering the GUI too).
#
# Comma-list semantics differ per key because ``get_seeds`` treats each field differently, and
# that behavior lives in ``pyrit.memory`` (this layer cannot import it). As of today
# (see ``MemoryInterface.get_seeds`` / ``_add_list_conditions``):
#   - harm_categories -> AND + substring: a seed must be tagged with EVERY value, and each value
#     is a substring match (``cyber`` matches ``cyber_harm``). So ``harm_categories=cyber,violence``
#     is an intersection, not a union.
#   - data_types -> OR + exact: a seed matches ANY value, compared for exact equality. So
#     ``data_types=text,image_path`` is a union.
DATASET_FILTERS: frozenset[str] = frozenset({"harm_categories", "data_types"})


def _validate_dataset_filter_mapping(
    value: dict[str, list[str]] | None,
) -> dict[str, list[str]] | None:
    """
    Validate dataset filter keys shared by launch and estimate requests.

    Returns:
        dict[str, list[str]] | None: Validated filters.

    Raises:
        ValueError: If a filter key is not supported.
    """
    for key in value or {}:
        if key not in DATASET_FILTERS:
            raise ValueError(f"Unknown dataset filter '{key}'. Allowed: {', '.join(sorted(DATASET_FILTERS))}.")
    return value


class ScenarioRunSizeEstimateStatus(str, Enum):
    """Confidence level for a catalog default-run size estimate."""

    Exact = "exact"
    Conditional = "conditional"
    Unavailable = "unavailable"


class ScenarioRunSizeEstimateCondition(str, Enum):
    """Reason an estimate remains conditional until launch."""

    TargetCapabilities = "target_capabilities"
    LaunchConfiguration = "launch_configuration"


class ScenarioRunSizeFactor(BaseModel):
    """One labeled multiplicative factor in a run-size component."""

    label: str = Field(..., min_length=1)
    count: int = Field(..., ge=0)


class ScenarioRunSizeComponent(BaseModel):
    """One additive component of a default-run size estimate."""

    label: str = Field(..., min_length=1)
    count: int = Field(..., ge=0)
    factors: list[ScenarioRunSizeFactor] = Field(default_factory=list)
    is_baseline: bool = False
    condition: ScenarioRunSizeEstimateCondition | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_factor_product(self) -> "ScenarioRunSizeComponent":
        """
        Require known component totals to equal their ordered factor product.

        Returns:
            ScenarioRunSizeComponent: The validated component.

        Raises:
            ValueError: If a component with factors has an inconsistent count.
        """
        if self.factors:
            factor_product = prod(factor.count for factor in self.factors)
            if self.count != factor_product:
                raise ValueError(
                    f"Component '{self.label}' count ({self.count}) must equal its factor product ({factor_product})"
                )
        return self


class ScenarioAdaptiveRunSizeDetails(BaseModel):
    """Structured work bounds for an adaptive scenario estimate."""

    objective_count: int = Field(..., ge=0)
    selected_candidate_technique_count: int = Field(..., ge=1)
    candidate_technique_count: int = Field(..., ge=1)
    max_attempts_per_objective: int = Field(..., ge=1)
    techniques_per_objective_upper_bound: int = Field(..., ge=1)
    technique_attempt_count_upper_bound: int = Field(..., ge=0)
    stop_on_first_success: Literal[True] = True
    compatibility_may_reduce_attempts: Literal[True] = True

    @model_validator(mode="before")
    @classmethod
    def default_selected_candidate_count(cls, data: Any) -> Any:
        """
        Preserve version-1 payload compatibility when the selected count is absent.

        Returns:
            Any: Input data with the selected count defaulted to the compatible count.
        """
        if (
            isinstance(data, dict)
            and "selected_candidate_technique_count" not in data
            and "candidate_technique_count" in data
        ):
            return {
                **data,
                "selected_candidate_technique_count": data["candidate_technique_count"],
            }
        return data

    @model_validator(mode="after")
    def validate_attempt_bounds(self) -> "ScenarioAdaptiveRunSizeDetails":
        """
        Ensure the serialized adaptive attempt bounds match the configured pool.

        Returns:
            ScenarioAdaptiveRunSizeDetails: The validated details.

        Raises:
            ValueError: If either derived upper bound is inconsistent.
        """
        if self.candidate_technique_count > self.selected_candidate_technique_count:
            raise ValueError("candidate_technique_count cannot exceed selected_candidate_technique_count")
        expected_per_objective = min(self.candidate_technique_count, self.max_attempts_per_objective)
        if self.techniques_per_objective_upper_bound != expected_per_objective:
            raise ValueError(
                "techniques_per_objective_upper_bound must equal "
                "min(candidate_technique_count, max_attempts_per_objective)"
            )
        expected_total = self.objective_count * expected_per_objective
        if self.technique_attempt_count_upper_bound != expected_total:
            raise ValueError(
                "technique_attempt_count_upper_bound must equal objective_count * techniques_per_objective_upper_bound"
            )
        return self


class ScenarioDatasetSizeCap(BaseModel):
    """One configured cap affecting a dataset or compound population."""

    label: str = Field(..., min_length=1)
    count: int = Field(..., ge=1)
    configured_on: Literal["dataset", "configuration", "compound"] = "dataset"
    dataset_name: str | None = None


class ScenarioDatasetSummary(BaseModel):
    """Logical seed-group counts for one default dataset or synthesized population."""

    name: str = Field(..., min_length=1)
    kind: Literal["dataset", "synthesized"] = "dataset"
    logical_seed_group_count: int = Field(
        ...,
        ge=0,
        validation_alias=AliasChoices("logical_seed_group_count", "seed_group_count"),
    )
    selected_seed_group_count: int = Field(..., ge=0)
    configured_caps: list[ScenarioDatasetSizeCap] = Field(default_factory=list)
    selection_note: str | None = None


class ScenarioTechniqueSummary(BaseModel):
    """One concrete attack technique available to a scenario."""

    name: str = Field(..., min_length=1)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class ScenarioDatasetSizeLimit(BaseModel):
    """Structured default and override semantics for a scenario's dataset-size limit."""

    default_scope: Literal["none", "per_dataset", "combined", "heterogeneous"] = "none"
    default_count: int | None = Field(default=None, ge=1)
    override_scope: Literal["per_dataset", "combined", "unsupported"] = "per_dataset"

    @model_validator(mode="after")
    def validate_default_count(self) -> "ScenarioDatasetSizeLimit":
        """
        Require a count exactly when the default has one representable scope.

        Returns:
            ScenarioDatasetSizeLimit: The validated limit metadata.

        Raises:
            ValueError: If the count does not match the declared default scope.
        """
        has_representable_default = self.default_scope in {"per_dataset", "combined"}
        if has_representable_default != (self.default_count is not None):
            raise ValueError("default_count must be set exactly for per_dataset or combined defaults")
        return self


class ScenarioDefaultRunSizeEstimate(BaseModel):
    """
    Structured estimate of default planned scenario execution units.

    Counts use the same outer unit as ``ScenarioRunPlan``: one atomic-attack and
    logical-seed-group pair. Retries and internal attack turns are excluded.
    """

    version: Literal[1] = 1
    status: ScenarioRunSizeEstimateStatus
    total_attack_count: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("total_attack_count", "total", "estimated_attack_count"),
    )
    minimum_attack_count: int | None = Field(default=None, ge=0)
    maximum_attack_count: int | None = Field(default=None, ge=0)
    condition: ScenarioRunSizeEstimateCondition | None = None
    components: list[ScenarioRunSizeComponent] = Field(default_factory=list)
    datasets: list[ScenarioDatasetSummary] = Field(default_factory=list)
    adaptive_details: ScenarioAdaptiveRunSizeDetails | None = None
    effective_parameters: dict[str, bool | int | float | str | list[str]] = Field(
        default_factory=dict,
        description="Scenario parameter values used by this estimate, including implicit runtime defaults.",
    )
    note: str | None = Field(default=None, validation_alias=AliasChoices("note", "caveat"))
    retries_included: Literal[False] = False

    @property
    def total(self) -> int | None:
        """The legacy Python attribute for total_attack_count."""
        return self.total_attack_count

    @property
    def estimated_attack_count(self) -> int | None:
        """The legacy Python attribute for total_attack_count."""
        return self.total_attack_count

    @property
    def caveat(self) -> str | None:
        """The legacy Python attribute for note."""
        return self.note

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_estimate(cls, data: Any) -> Any:
        """
        Default an omitted status from the available total, then explain
        component-less legacy exact totals in the canonical shape.

        Callers that predate the ``status`` field (e.g. ``estimated_attack_count``-only
        constructors) are treated as ``Exact`` when they supply a total and
        ``Conditional`` otherwise.

        Returns:
            Any: The normalized input.
        """
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "status" not in normalized:
            has_total = any(
                normalized.get(key) is not None for key in ("total_attack_count", "total", "estimated_attack_count")
            )
            normalized["status"] = (
                ScenarioRunSizeEstimateStatus.Exact if has_total else ScenarioRunSizeEstimateStatus.Conditional
            )
        if "total" in normalized and "components" not in normalized:
            status = normalized.get("status")
            if status == ScenarioRunSizeEstimateStatus.Exact or status == "exact":
                normalized["components"] = [
                    {
                        "label": "Legacy total",
                        "count": normalized["total"],
                        "note": "Normalized from a legacy component-less estimate.",
                    }
                ]
        return normalized

    @model_validator(mode="after")
    def validate_total(self) -> "ScenarioDefaultRunSizeEstimate":
        """
        Ensure exact estimates expose and explain their complete total.

        Returns:
            ScenarioDefaultRunSizeEstimate: The validated estimate.

        Raises:
            ValueError: If an exact estimate omits or misstates its total.
        """
        if (
            self.minimum_attack_count is not None
            and self.maximum_attack_count is not None
            and self.minimum_attack_count > self.maximum_attack_count
        ):
            raise ValueError("minimum_attack_count must be less than or equal to maximum_attack_count")

        if self.status is not ScenarioRunSizeEstimateStatus.Exact:
            return self

        if self.total_attack_count is None:
            raise ValueError("Exact default-run estimates require total_attack_count")
        for field_name, bound in (
            ("minimum_attack_count", self.minimum_attack_count),
            ("maximum_attack_count", self.maximum_attack_count),
        ):
            if bound is not None and bound != self.total_attack_count:
                raise ValueError(f"Exact default-run estimates require {field_name} to equal total_attack_count")

        component_total = sum(component.count for component in self.components)
        if component_total != self.total_attack_count:
            raise ValueError(
                f"Exact default-run estimate components total {component_total}, not {self.total_attack_count}"
            )
        return self

    @classmethod
    def unavailable(
        cls, *, note: str = "Default-run size estimate is unavailable."
    ) -> "ScenarioDefaultRunSizeEstimate":
        """
        Build an unavailable estimate without presenting a guessed total.

        Returns:
            ScenarioDefaultRunSizeEstimate: An unavailable estimate.
        """
        return cls(status=ScenarioRunSizeEstimateStatus.Unavailable, note=note)


# Backward-compatible catalog name from the initial run-size DTO.
ScenarioRunSizeEstimate = ScenarioDefaultRunSizeEstimate


class RegisteredScenario(BaseModel):
    """Summary of a registered scenario."""

    scenario_name: str = Field(..., description="Scenario name  (e.g., 'foundry.red_team_agent')")
    scenario_type: str = Field(..., description="Scenario type identifier (e.g., 'RedTeamAgentScenario')")
    scenario_version: int = Field(1, ge=1, description="Scenario definition version used for default metadata")
    description: str = Field(..., description="Human-readable description of the scenario")
    description_markdown: str = Field(
        "",
        description=(
            "Dedented Markdown source preserving the scenario docstring structure. "
            "Clients must treat embedded HTML as untrusted text."
        ),
    )
    default_technique: str = Field(..., description="Default technique name used when none specified")
    default_techniques: list[str] = Field(
        default_factory=list,
        description="Ordered concrete techniques selected by the scenario's default technique policy",
    )
    aggregate_techniques: list[str] = Field(
        ..., description="Aggregate techniques that combine multiple attack approaches"
    )
    aggregate_technique_expansions: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Concrete ordered technique expansion for every aggregate selector",
    )
    all_techniques: list[str] = Field(..., description="All available concrete technique names")
    technique_summaries: list[ScenarioTechniqueSummary] = Field(
        default_factory=list,
        description="Descriptions and tags for the available concrete techniques",
    )
    default_datasets: list[str] = Field(..., description="Default dataset names used by the scenario")
    dataset_size_limit: ScenarioDatasetSizeLimit = Field(
        default_factory=ScenarioDatasetSizeLimit,
        description="Structured scenario-default and explicit-override dataset-size limit semantics",
    )
    baseline_policy: Literal["enabled", "disabled", "forbidden"] = Field(
        "enabled", description="Whether baseline execution is enabled, disabled, or forbidden"
    )
    include_baseline_by_default: bool = Field(True, description="Whether an omitted baseline flag includes it")
    supported_parameters: list[Parameter] = Field(
        default_factory=list, description="Scenario-declared custom parameters"
    )
    default_run_size: ScenarioRunSizeEstimate = Field(
        default_factory=ScenarioRunSizeEstimate.unavailable,
        description="Scenario-owned structured estimate of the default planned execution units",
    )


class ScenarioRunSizeEstimateRequest(BaseModel):
    """Request-specific scenario run-size configuration."""

    target_name: str | None = Field(
        None,
        description="Optional registered objective target used to resolve target-capability-dependent estimates",
    )
    techniques: list[str] | None = Field(
        None, description="Technique names to estimate (uses scenario default if omitted)"
    )
    dataset_names: list[str] | None = Field(
        None, description="Dataset names to estimate (uses scenario default if omitted)"
    )
    max_dataset_size: int | None = Field(None, ge=1, description="Maximum selected logical seed groups")
    dataset_filters: dict[str, list[str]] | None = Field(
        None,
        description="Dataset seed filters keyed by field. Accepted keys: harm_categories, data_types.",
    )
    include_baseline: bool | None = Field(
        None,
        description="Override the scenario baseline default; forbidden scenarios reject true",
    )
    scenario_params: dict[str, Any] | None = Field(
        None,
        description="Scenario-declared parameters such as Jailbreak template and attempt counts",
    )

    @field_validator("dataset_filters")
    @classmethod
    def _validate_dataset_filters(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        """
        Validate estimate dataset filters against the shared allow-list.

        Returns:
            dict[str, list[str]] | None: Validated filters.
        """
        return _validate_dataset_filter_mapping(value)


class RunScenarioRequest(BaseModel):
    """Request body for starting a scenario run."""

    scenario_name: str = Field(..., description="Scenario name (e.g., 'foundry.red_team_agent')")
    target_name: str = Field(..., description="Name of a registered target from the TargetRegistry")
    initializers: list[str] | None = Field(
        None, description="Initializer names to run before scenario (e.g., ['target', 'load_default_datasets'])"
    )
    techniques: list[str] | None = Field(None, description="Technique names to use (uses scenario default if omitted)")
    dataset_names: list[str] | None = Field(None, description="Dataset names to use (uses scenario default if omitted)")
    max_dataset_size: int | None = Field(None, ge=1, description="Maximum items per dataset")
    dataset_filters: dict[str, list[str]] | None = Field(
        None,
        description=(
            "Dataset seed filters keyed by field, applied before sampling. Accepted keys: harm_categories, data_types."
        ),
    )
    max_concurrency: int = Field(10, ge=1, le=100, description="Maximum concurrent operations")
    max_retries: int = Field(0, ge=0, le=20, description="Maximum retry attempts on failure")
    include_baseline: bool | None = Field(
        None, description="Override the scenario baseline default; forbidden scenarios reject true"
    )
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

    @field_validator("dataset_filters")
    @classmethod
    def _validate_dataset_filters(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        """
        Reject any dataset-filter key not in the exposed ``DATASET_FILTERS`` allow-list.

        Returns:
            dict[str, list[str]] | None: The validated filters, unchanged.
        """
        return _validate_dataset_filter_mapping(value)


class AttackErrorSummary(BaseModel):
    """A single errored attack result surfaced in a run summary."""

    atomic_attack_name: str = Field(..., description="Atomic-attack cell that errored")
    objective: str = Field("", description="Objective that was being attempted")
    error_type: str | None = Field(None, description="Exception class name")
    error_message: str | None = Field(None, description="Exception message")
    total_retries: int = Field(0, ge=0, description="Retry attempts recorded for this attack")


class AttackRetrySummary(BaseModel):
    """Retry events recorded for one attack result, for near-real-time CLI display."""

    attack_result_id: str = Field(..., description="Stable ID of the attack result (used to de-duplicate)")
    atomic_attack_name: str = Field(..., description="Atomic-attack cell that retried")
    retries: list[RetryEvent] = Field(
        default_factory=list, description="Retry attempts, each with component role/name, endpoint, and exception"
    )


class ScenarioOverloadSummary(BaseModel):
    """Recent structured overload signals grouped by component role."""

    component_role: str = Field(..., description="Role of the component that observed overload")
    count: int = Field(..., ge=1, description="Recent HTTP 429 and 5xx retry signals")
    rate_limit_count: int = Field(0, ge=0, description="Recent HTTP 429 retry signals")
    server_error_count: int = Field(0, ge=0, description="Recent HTTP 5xx retry signals")
    status_codes: list[int] = Field(default_factory=list, description="Observed overload status codes")
    latest_timestamp: datetime = Field(..., description="Latest overload signal timestamp")


class ScenarioRunSummary(BaseModel):
    """Response for a scenario run (status + result details)."""

    scenario_result_id: str = Field(..., description="UUID of the ScenarioResult in memory")
    scenario_name: str = Field(..., description="Registry key of the scenario being run")
    scenario_registry_name: str | None = Field(None, description="Requested scenario registry key when available")
    scenario_version: int = Field(0, ge=0, description="Version of the scenario")
    status: ScenarioRunState = Field(..., description="Current run status")
    created_at: datetime = Field(..., description="When the run was created")
    started_at: datetime | None = Field(None, description="When active scenario execution started")
    updated_at: datetime = Field(..., description="When the run status last changed")
    error: str | None = Field(None, description="Error message if status is FAILED")
    error_type: str | None = Field(None, description="Exception class name if status is FAILED")
    techniques_used: list[str] = Field(default_factory=list, description="Technique names that were executed")
    total_attacks: int = Field(0, ge=0, description="Total number of attack results persisted for this run")
    completed_attacks: int = Field(0, ge=0, description="Number of attacks that reached a terminal outcome")
    objective_achieved_rate: int = Field(0, ge=0, le=100, description="Success rate as percentage (0-100)")
    failed_attacks: list[AttackErrorSummary] = Field(
        default_factory=list,
        description="Individual attack results that errored, surfaced regardless of overall run status",
    )
    attack_retries: list[AttackRetrySummary] = Field(
        default_factory=list,
        description="Per-attack retry events, surfaced as each attack result lands so the CLI can stream warnings",
    )
    total_retries: int = Field(
        0,
        ge=0,
        description="Total retry work beyond each logical unit's initial attempt, including inner retries "
        "and additional scenario attempts",
    )
    labels: dict[str, str] = Field(default_factory=dict, description="Labels attached to this run")
    completed_at: datetime | None = Field(None, description="When the scenario finished")
    pyrit_version: str | None = Field(None, description="PyRIT version that created the run")
    target: "ScenarioTargetSummary | None" = Field(None, description="Safe objective-target identity")
    datasets_used: list[str] = Field(default_factory=list, description="Resolved datasets selected for the run")
    scenario_parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Safe resolved scenario parameters; sensitive fields are removed",
    )
    planned_total_available: bool = Field(
        True,
        description="Whether total_attacks comes from a complete persisted run plan",
    )
    successful_attacks: int = Field(0, ge=0, description="Latest successful planned units")
    error_attacks: int = Field(0, ge=0, description="Persisted error attempts")
    attack_details_available: bool = Field(
        True,
        description="Whether failed_attacks and attack_retries contain per-attempt details",
    )
    queue_position: int | None = Field(None, ge=1, description="Current 1-based waiting position")
    active_scenario_result_id: str | None = Field(None, description="Currently executing scenario result ID")
    overload_summaries: list[ScenarioOverloadSummary] = Field(
        default_factory=list,
        description="Bounded recent HTTP 429 and 5xx retry evidence grouped by component role",
    )


class ScenarioRunListItem(BaseModel):
    """Lightweight scenario run metadata returned by the history endpoint."""

    scenario_result_id: str = Field(..., description="UUID of the ScenarioResult in memory")
    scenario_name: str = Field(..., description="Registry key of the scenario being run")
    scenario_registry_name: str | None = Field(None, description="Requested scenario registry key when available")
    scenario_version: int = Field(0, ge=0, description="Version of the scenario")
    status: ScenarioRunState = Field(..., description="Current run status")
    created_at: datetime = Field(..., description="When the run was created")
    started_at: datetime | None = Field(None, description="When active scenario execution started")
    updated_at: datetime = Field(..., description="When the run status last changed")
    error: str | None = Field(None, description="Persisted run-level error message")
    error_type: str | None = Field(None, description="Persisted run-level exception class")
    techniques_used: list[str] = Field(default_factory=list, description="Planned technique display groups")
    total_attacks: int | None = Field(None, ge=0, description="Number of planned execution units when known")
    completed_attacks: int = Field(0, ge=0, description="Latest completed planned units")
    objective_achieved_rate: int = Field(0, ge=0, le=100, description="Success rate as percentage (0-100)")
    total_retries: int = Field(0, ge=0, description="Retry attempts recorded across projected work units")
    labels: dict[str, str] = Field(default_factory=dict, description="Labels attached to this run")
    completed_at: datetime | None = Field(None, description="When the scenario finished")
    pyrit_version: str | None = Field(None, description="PyRIT version that created the run")
    target: "ScenarioTargetSummary | None" = Field(None, description="Safe objective-target identity")
    datasets_used: list[str] = Field(default_factory=list, description="Resolved datasets selected for the run")
    scenario_parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Safe resolved scenario parameters; sensitive fields are removed",
    )
    planned_total_available: bool = Field(
        True,
        description="Whether total_attacks comes from a complete persisted run plan",
    )
    successful_attacks: int = Field(0, ge=0, description="Latest successful planned units")
    error_attacks: int = Field(0, ge=0, description="Persisted error attempts")
    attack_details_available: bool = Field(
        True,
        description="Whether failed_attacks and attack_retries contain per-attempt details",
    )


class ScenarioTargetSummary(BaseModel):
    """Safe target identity suitable for scenario history and run headers."""

    target_type: str = Field(..., description="Target implementation type")
    endpoint: str | None = Field(None, description="Configured endpoint, when present")
    model_name: str | None = Field(None, description="Configured model or deployment name")
    identifier_hash: str | None = Field(None, description="Canonical target identifier hash")


ScenarioRunSummary.model_rebuild()
ScenarioRunListItem.model_rebuild()
