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

from pydantic import AliasChoices, BaseModel, Field, computed_field, field_validator, model_validator

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
_DatasetCapIdentity = tuple[str, int, str, str | None, tuple[str, ...]]
_DatasetCapKey = tuple[_DatasetCapIdentity, int]


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


def _validate_dataset_name_selection(value: list[str] | None) -> list[str] | None:
    """
    Validate an explicit ordered dataset selection.

    Returns:
        list[str] | None: The validated selection.

    Raises:
        ValueError: If an explicit selection is empty or contains duplicate names.
    """
    if value is None:
        return None
    if not value:
        raise ValueError("dataset_names must contain at least one dataset when provided")
    if len(set(value)) != len(value):
        raise ValueError("dataset_names cannot contain duplicates")
    return value


class ScenarioRunSizeEstimateStatus(str, Enum):
    """Confidence level for a scenario run-size estimate."""

    Exact = "exact"
    Conditional = "conditional"
    Unavailable = "unavailable"


class ScenarioRunSizeEstimateCondition(str, Enum):
    """Reason an estimate remains conditional until launch."""

    TargetCapabilities = "target_capabilities"
    LaunchConfiguration = "launch_configuration"
    PriorExecutionResults = "prior_execution_results"


class ScenarioDatasetPopulationStatus(str, Enum):
    """Whether a dataset's full logical population is known."""

    Known = "known"
    Unknown = "unknown"


class ScenarioDatasetSizeLimitDefaultScope(str, Enum):
    """How a scenario's default dataset-size limit is applied."""

    None_ = "none"
    PerDataset = "per_dataset"
    Combined = "combined"
    Heterogeneous = "heterogeneous"


class ScenarioDatasetSizeLimitOverrideScope(str, Enum):
    """How a scenario interprets an explicit dataset-size override."""

    PerDataset = "per_dataset"
    Combined = "combined"
    Unsupported = "unsupported"


class ScenarioDatasetSelectionOverrideScope(str, Enum):
    """Which explicit dataset-name selections a scenario accepts."""

    Any = "any"
    Fixed = "fixed"
    FixedSet = "fixed_set"
    OneOf = "one_of"
    Unsupported = "unsupported"


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


class ScenarioDatasetSizeCap(BaseModel):
    """One configured cap affecting a dataset or compound population."""

    label: str = Field(..., min_length=1)
    count: int = Field(..., ge=1)
    configured_on: Literal["dataset", "configuration", "compound"] = "dataset"
    dataset_name: str | None = None
    dataset_names: list[str] = Field(
        default_factory=list,
        description="Ordered datasets sharing this cap; one entry for a per-dataset cap.",
    )

    @model_validator(mode="after")
    def validate_dataset_names(self) -> "ScenarioDatasetSizeCap":
        """
        Normalize the legacy singular dataset name into ordered provenance.

        Returns:
            ScenarioDatasetSizeCap: The validated cap.

        Raises:
            ValueError: If contributor names are duplicated or contradict the singular name.
        """
        if len(set(self.dataset_names)) != len(self.dataset_names):
            raise ValueError("dataset_names cannot contain duplicates")
        if self.dataset_name is not None:
            if self.dataset_names and self.dataset_name not in self.dataset_names:
                raise ValueError("dataset_name must be included in dataset_names")
            if not self.dataset_names:
                self.dataset_names = [self.dataset_name]
        if self.configured_on == "dataset" and len(self.dataset_names) == 1 and self.dataset_name is None:
            self.dataset_name = self.dataset_names[0]
        if self.configured_on == "dataset" and len(self.dataset_names) > 1:
            raise ValueError("A per-dataset cap must identify at most one dataset")
        return self


class ScenarioDatasetSummary(BaseModel):
    """Logical seed-group counts for one default dataset or synthesized population."""

    name: str = Field(..., min_length=1)
    kind: Literal["dataset", "synthesized"] = "dataset"
    population_status: ScenarioDatasetPopulationStatus = ScenarioDatasetPopulationStatus.Known
    logical_seed_group_count: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("logical_seed_group_count", "seed_group_count"),
        description="Full logical seed-group population, or null when it has not been materialized.",
    )
    selected_seed_group_count: int | None = Field(
        default=None,
        ge=0,
        description="Selected logical seed groups, or null when the population is unknown.",
    )
    effective_cap: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Independent cap for this dataset; null when several children contribute to one population. "
            "Shared caps are reported at estimate level."
        ),
    )
    configured_caps: list[ScenarioDatasetSizeCap] = Field(default_factory=list)
    selection_note: str | None = None

    @model_validator(mode="before")
    @classmethod
    def infer_population_status(cls, data: Any) -> Any:
        """
        Preserve existing known-summary construction while allowing explicit unknowns.

        Returns:
            Any: The normalized summary input.
        """
        if not isinstance(data, dict) or "population_status" in data:
            return data
        normalized = dict(data)
        full_count = normalized.get("logical_seed_group_count", normalized.get("seed_group_count"))
        normalized["population_status"] = (
            ScenarioDatasetPopulationStatus.Known if full_count is not None else ScenarioDatasetPopulationStatus.Unknown
        )
        return normalized

    @model_validator(mode="after")
    def validate_population(self) -> "ScenarioDatasetSummary":
        """
        Keep known and unknown population states internally consistent.

        Returns:
            ScenarioDatasetSummary: The validated summary.

        Raises:
            ValueError: If population state, counts, or the effective cap conflict.
        """
        if self.population_status is ScenarioDatasetPopulationStatus.Known:
            if self.logical_seed_group_count is None or self.selected_seed_group_count is None:
                raise ValueError("Known dataset populations require full and selected seed-group counts")
        elif self.logical_seed_group_count is not None:
            raise ValueError("Unknown dataset populations cannot include a full seed-group count")

        if any(cap.dataset_names and self.name not in cap.dataset_names for cap in self.configured_caps):
            raise ValueError("configured_caps must include the summarized dataset in their provenance")
        if (
            self.logical_seed_group_count is not None
            and self.selected_seed_group_count is not None
            and self.selected_seed_group_count > self.logical_seed_group_count
        ):
            raise ValueError("selected_seed_group_count cannot exceed logical_seed_group_count")
        independent_caps = [
            cap.count
            for cap in self.configured_caps
            if cap.configured_on == "dataset" and (not cap.dataset_names or self.name in cap.dataset_names)
        ]
        if len(independent_caps) > 1 and self.effective_cap is not None:
            raise ValueError("effective_cap cannot represent multiple child caps on one population")
        if len(independent_caps) == 1:
            configured_effective_cap = independent_caps[0]
            if self.effective_cap is None and "effective_cap" not in self.model_fields_set:
                self.effective_cap = configured_effective_cap
            elif self.effective_cap is not None and self.effective_cap != configured_effective_cap:
                raise ValueError("effective_cap must match the per-dataset configured cap")
        if (
            self.effective_cap is not None
            and self.selected_seed_group_count is not None
            and self.selected_seed_group_count > self.effective_cap
        ):
            raise ValueError("selected_seed_group_count cannot exceed effective_cap")
        return self


class ScenarioDatasetSizeLimit(BaseModel):
    """Structured default and override semantics for a scenario's dataset-size limit."""

    default_scope: ScenarioDatasetSizeLimitDefaultScope = ScenarioDatasetSizeLimitDefaultScope.None_
    default_count: int | None = Field(default=None, ge=1)
    override_scope: ScenarioDatasetSizeLimitOverrideScope = ScenarioDatasetSizeLimitOverrideScope.PerDataset

    @model_validator(mode="after")
    def validate_default_count(self) -> "ScenarioDatasetSizeLimit":
        """
        Require a count exactly when the default has one representable scope.

        Returns:
            ScenarioDatasetSizeLimit: The validated limit metadata.

        Raises:
            ValueError: If the count does not match the declared default scope.
        """
        has_representable_default = self.default_scope in {
            ScenarioDatasetSizeLimitDefaultScope.PerDataset,
            ScenarioDatasetSizeLimitDefaultScope.Combined,
        }
        if has_representable_default != (self.default_count is not None):
            raise ValueError("default_count must be set exactly for per_dataset or combined defaults")
        return self


class ScenarioDatasetSelection(BaseModel):
    """Dataset-name override shape, independent of the dataset-size cap scope."""

    override_scope: ScenarioDatasetSelectionOverrideScope = ScenarioDatasetSelectionOverrideScope.Any
    allowed_names: list[str] | None = Field(
        default=None,
        description=(
            "Exact required names for fixed scopes, or alternatives for one_of; null means unrestricted or unsupported."
        ),
    )

    @model_validator(mode="after")
    def validate_allowed_names(self) -> "ScenarioDatasetSelection":
        """
        Require explicit allowed names exactly when the scope needs them.

        Returns:
            ScenarioDatasetSelection: The validated selection contract.

        Raises:
            ValueError: If allowed names are missing, duplicated, or incompatible with the scope.
        """
        has_named_scope = self.override_scope in {
            ScenarioDatasetSelectionOverrideScope.Fixed,
            ScenarioDatasetSelectionOverrideScope.FixedSet,
            ScenarioDatasetSelectionOverrideScope.OneOf,
        }
        if has_named_scope != (self.allowed_names is not None):
            raise ValueError("allowed_names must be set exactly for fixed, fixed_set, or one_of selections")
        if self.allowed_names is not None:
            _validate_dataset_name_selection(self.allowed_names)
        return self


class ScenarioTechniqueSummary(BaseModel):
    """One concrete attack technique available to a scenario."""

    name: str = Field(..., min_length=1)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class ScenarioRunSizeEstimate(BaseModel):
    """
    Structured estimate of default planned scenario execution units.

    Counts use the same outer unit as ``ScenarioRunPlan``: one atomic-attack and
    logical-seed-group pair. Retries and internal attack turns are excluded.
    """

    status: ScenarioRunSizeEstimateStatus = ScenarioRunSizeEstimateStatus.Conditional
    total_attack_count: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("total_attack_count", "estimated_attack_count", "total"),
    )
    minimum_attack_count: int | None = Field(default=None, ge=0)
    maximum_attack_count: int | None = Field(default=None, ge=0)
    condition: ScenarioRunSizeEstimateCondition | None = None
    components: list[ScenarioRunSizeComponent] = Field(default_factory=list)
    datasets: list[ScenarioDatasetSummary] = Field(default_factory=list)
    dataset_cap_provenance: list[ScenarioDatasetSizeCap] = Field(
        default_factory=list,
        description="Ordered unique cap provenance; shared configuration and compound caps appear once.",
    )
    effective_parameters: dict[str, bool | int | float | str | list[str]] = Field(
        default_factory=dict,
        description="Scenario parameter values used by this estimate, including implicit runtime defaults.",
    )
    note: str | None = Field(default=None, validation_alias=AliasChoices("note", "caveat"))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def estimated_attack_count(self) -> int | None:
        """Compatibility projection of ``total_attack_count``."""
        return self.total_attack_count

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_estimate(cls, data: Any) -> Any:
        """
        Infer status for callers using the original additive estimate fields.

        Returns:
            Any: The normalized estimate input.

        Raises:
            ValueError: If duplicate total fields disagree.
        """
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        total_field_names = ("total_attack_count", "estimated_attack_count", "total")
        present_total_fields = [key for key in total_field_names if key in normalized]
        total_values = [normalized[key] for key in present_total_fields if normalized[key] is not None]
        if total_values and any(value != total_values[0] for value in total_values[1:]):
            raise ValueError("total_attack_count and compatibility total fields must match")
        if present_total_fields:
            normalized["total_attack_count"] = total_values[0] if total_values else None
            normalized.pop("estimated_attack_count", None)
            normalized.pop("total", None)
        if "status" not in normalized:
            normalized["status"] = (
                ScenarioRunSizeEstimateStatus.Exact if total_values else ScenarioRunSizeEstimateStatus.Conditional
            )
        status = normalized["status"]
        if total_values and (
            status == ScenarioRunSizeEstimateStatus.Exact or status == ScenarioRunSizeEstimateStatus.Exact.value
        ):
            total = total_values[0]
            if normalized.get("minimum_attack_count") is None:
                normalized["minimum_attack_count"] = total
            if normalized.get("maximum_attack_count") is None:
                normalized["maximum_attack_count"] = total
        return normalized

    @model_validator(mode="after")
    def validate_estimate(self) -> "ScenarioRunSizeEstimate":
        """
        Ensure status, bounds, and additive components describe one estimate.

        Returns:
            ScenarioRunSizeEstimate: The validated estimate.

        Raises:
            ValueError: If the estimate contains contradictory values.
        """
        self._populate_dataset_cap_provenance()
        component_total = sum(component.count for component in self.components)
        if self.status is not ScenarioRunSizeEstimateStatus.Conditional and self.condition is not None:
            raise ValueError(f"{self.status.value.capitalize()} run-size estimates cannot include condition")

        if self.status is ScenarioRunSizeEstimateStatus.Exact:
            if self.total_attack_count is None:
                raise ValueError("Exact run-size estimates require total_attack_count")
            for field_name, bound in (
                ("minimum_attack_count", self.minimum_attack_count),
                ("maximum_attack_count", self.maximum_attack_count),
            ):
                if bound is not None and bound != self.total_attack_count:
                    raise ValueError(f"Exact run-size estimates require {field_name} to equal total_attack_count")
            if component_total != self.total_attack_count:
                raise ValueError(f"Run-size estimate components total {component_total}, not {self.total_attack_count}")
            return self

        if (
            self.minimum_attack_count is not None
            and self.maximum_attack_count is not None
            and self.minimum_attack_count > self.maximum_attack_count
        ):
            raise ValueError("minimum_attack_count must be less than or equal to maximum_attack_count")

        if self.total_attack_count is not None:
            raise ValueError(f"{self.status.value.capitalize()} run-size estimates cannot include total_attack_count")

        if self.status is ScenarioRunSizeEstimateStatus.Unavailable:
            if self.minimum_attack_count is not None or self.maximum_attack_count is not None:
                raise ValueError("Unavailable run-size estimates cannot include numeric bounds")
            if self.components:
                raise ValueError("Unavailable run-size estimates cannot include numeric components")
            return self

        if self.components:
            if self.minimum_attack_count is not None and component_total < self.minimum_attack_count:
                raise ValueError(
                    f"Run-size estimate components total {component_total}, below minimum_attack_count "
                    f"{self.minimum_attack_count}"
                )
            if self.maximum_attack_count is not None and component_total > self.maximum_attack_count:
                raise ValueError(
                    f"Run-size estimate components total {component_total}, above maximum_attack_count "
                    f"{self.maximum_attack_count}"
                )
        return self

    def _populate_dataset_cap_provenance(self) -> None:
        """
        Derive cap provenance in an order consistent with each dataset's application order.

        Raises:
            ValueError: If dataset summaries describe conflicting cap application orders.
        """
        if self.dataset_cap_provenance:
            return

        caps_by_key: dict[_DatasetCapKey, ScenarioDatasetSizeCap] = {}
        predecessors: dict[_DatasetCapKey, set[_DatasetCapKey]] = {}
        for dataset in self.datasets:
            previous_key: _DatasetCapKey | None = None
            cap_occurrences: dict[_DatasetCapIdentity, int] = {}
            for configured_cap in dataset.configured_caps:
                dataset_name = (
                    configured_cap.dataset_name or dataset.name if configured_cap.configured_on == "dataset" else None
                )
                declared_names = (
                    tuple(configured_cap.dataset_names) if configured_cap.configured_on != "dataset" else ()
                )
                identity: _DatasetCapIdentity = (
                    configured_cap.label,
                    configured_cap.count,
                    configured_cap.configured_on,
                    dataset_name,
                    declared_names,
                )
                occurrence = cap_occurrences.get(identity, 0)
                cap_occurrences[identity] = occurrence + 1
                key = (identity, occurrence)
                predecessors.setdefault(key, set())
                if previous_key is not None and previous_key != key:
                    predecessors[key].add(previous_key)
                previous_key = key
                existing = caps_by_key.get(key)
                if existing is None:
                    names = configured_cap.dataset_names or [dataset.name]
                    caps_by_key[key] = configured_cap.model_copy(
                        update={
                            "dataset_name": dataset_name,
                            "dataset_names": list(names),
                        }
                    )
                    continue
                for name in configured_cap.dataset_names or [dataset.name]:
                    if name not in existing.dataset_names:
                        existing.dataset_names.append(name)

        remaining = list(caps_by_key)
        placed: set[_DatasetCapKey] = set()
        while remaining:
            ready = next((key for key in remaining if predecessors[key] <= placed), None)
            if ready is None:
                raise ValueError("Dataset summaries have conflicting cap application order")
            self.dataset_cap_provenance.append(caps_by_key[ready])
            placed.add(ready)
            remaining.remove(ready)

    @classmethod
    def unavailable(cls, *, note: str = "Default-run size estimate is unavailable.") -> "ScenarioRunSizeEstimate":
        """
        Build an unavailable estimate without presenting a guessed total.

        Returns:
            ScenarioRunSizeEstimate: An unavailable estimate.
        """
        return cls(status=ScenarioRunSizeEstimateStatus.Unavailable, note=note)


ScenarioDefaultRunSizeEstimate = ScenarioRunSizeEstimate


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
    dataset_selection: ScenarioDatasetSelection = Field(
        default_factory=ScenarioDatasetSelection,
        description="Which explicit dataset-name selections are valid for this scenario",
    )
    dataset_size_limit: ScenarioDatasetSizeLimit = Field(
        default_factory=ScenarioDatasetSizeLimit,
        description="Structured scenario-default and explicit-override dataset-size limit semantics",
    )
    baseline_policy: Literal["enabled", "disabled", "forbidden"] = Field(
        "enabled", description="Whether baseline execution is enabled, disabled, or forbidden"
    )
    include_baseline_by_default: bool = Field(True, description="Whether an omitted baseline flag includes it")
    uses_default_adversarial_target: bool = Field(
        False, description="Whether any available technique uses the shared adversarial target"
    )
    supported_parameters: list[Parameter] = Field(
        default_factory=list, description="Scenario-declared custom parameters"
    )
    default_run_size: ScenarioRunSizeEstimate = Field(
        default_factory=ScenarioRunSizeEstimate.unavailable,
        description="Scenario-owned structured estimate of the default planned execution units",
    )


class ScenarioRunSizeEstimateRequest(BaseModel):
    """Request-specific scenario run-size configuration."""

    adversarial_target_name: str | None = Field(
        None,
        min_length=1,
        description="Registered multi-turn target overriding only the adversarial fallback for this request",
    )
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
    max_dataset_size: int | None = Field(
        None,
        ge=1,
        description="Dataset cap interpreted according to the scenario catalog's dataset_size_limit.override_scope",
    )
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

    @field_validator("dataset_names")
    @classmethod
    def _validate_dataset_names(cls, value: list[str] | None) -> list[str] | None:
        """
        Validate explicit estimate dataset selections.

        Returns:
            list[str] | None: Validated names.
        """
        return _validate_dataset_name_selection(value)

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
    adversarial_target_name: str | None = Field(
        None,
        min_length=1,
        description="Registered multi-turn target overriding only the adversarial fallback for this run",
    )
    initializers: list[str] | None = Field(
        None, description="Initializer names to run before scenario (e.g., ['target', 'load_default_datasets'])"
    )
    techniques: list[str] | None = Field(None, description="Technique names to use (uses scenario default if omitted)")
    dataset_names: list[str] | None = Field(None, description="Dataset names to use (uses scenario default if omitted)")
    max_dataset_size: int | None = Field(
        None,
        ge=1,
        description="Dataset cap interpreted according to the scenario catalog's dataset_size_limit.override_scope",
    )
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

    @field_validator("dataset_names")
    @classmethod
    def _validate_dataset_names(cls, value: list[str] | None) -> list[str] | None:
        """
        Validate explicit launch dataset selections.

        Returns:
            list[str] | None: Validated names.
        """
        return _validate_dataset_name_selection(value)

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
    total_attacks: int = Field(
        0, ge=0, description="Planned execution units, or the observed units when no plan is persisted"
    )
    completed_attacks: int = Field(0, ge=0, description="Planned execution units that reached a terminal outcome")
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
    techniques_used: list[str] = Field(
        default_factory=list,
        description="Canonical technique names, with display-group or attack-name fallbacks for legacy runs",
    )
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
