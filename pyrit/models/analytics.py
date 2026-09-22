# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Lightweight contracts for querying persisted attack outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from enum import Enum
from typing import ClassVar, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from pyrit.models.results.attack_result import AttackOutcome


@dataclass
class AttackStats:
    """Statistics for attack analysis results."""

    success_rate: float | None
    total_decided: int
    successes: int
    failures: int
    undetermined: int
    errors: int


@dataclass
class AttackAnalyticsStatistics(AttackStats):
    """
    Outcome statistics for one cohort, group, or heatmap cell.

    ``success_rate`` uses successes / decided results; errors and undetermined
    outcomes do not enter that denominator. ``decided_share`` and ``outcome_shares``
    instead use all results. Rates with no applicable denominator are ``None``;
    shares for an empty cohort are zero. All proportions are in the range 0 through 1.
    """

    total_results: int
    decided_share: float | None
    outcome_shares: dict[AttackOutcome, float]


class AttackResultSelection(str, Enum):
    """
    The identity used when selecting persisted results.

    ``ALL_RESULTS`` keeps different result IDs distinct even when they share a
    conversation. ``LATEST_PER_CONVERSATION`` represents the legacy
    newest-matching-result-per-conversation behavior. Defining these modes does
    not change any caller's selection policy.
    """

    ALL_RESULTS = "all_results"
    LATEST_PER_CONVERSATION = "latest_per_conversation"


class AttackAnalyticsDimensionName(str, Enum):
    """
    Supported dimensions from saved result metadata, not current registry state.

    Target and scenario keys are persisted identities, not their display names.
    Harm categories describe the attack's intended coverage, not detected harms.
    Converter membership describes recorded usage, not an ordered per-turn pipeline.
    """

    OPERATION = "operation"
    OPERATOR = "operator"
    TARGETED_HARM_CATEGORY = "targeted_harm_category"
    ATTACK_TYPE = "attack_type"
    CONVERTER_TYPE = "converter_type"
    OBJECTIVE_TARGET = "objective_target"
    MODEL = "model"
    SCENARIO = "scenario"
    LABEL = "label"


class AttackAnalyticsConverterDirection(str, Enum):
    """The converter pipeline recorded on an attack."""

    REQUEST = "request"
    RESPONSE = "response"


class AttackAnalyticsMatchMode(str, Enum):
    """How values within one dimension predicate are combined."""

    ANY = "any"
    ALL = "all"


class AttackAnalyticsValueKind(str, Enum):
    """Disambiguate real metadata from missing values and empty pipelines."""

    VALUE = "value"
    MISSING = "missing"
    NO_CONVERTERS = "no_converters"


class _AnalyticsModel(BaseModel):
    """Reject unknown fields rather than silently broadening a misspelled query."""

    model_config = ConfigDict(extra="forbid")


class AttackAnalyticsDimension(_AnalyticsModel):
    """
    A metadata dimension, optionally selecting a literal label key or pipeline side.

    Label keys containing dots are still literal keys, not nested JSON paths.
    The dedicated operation/operator dimensions must be used for attribution.
    """

    name: AttackAnalyticsDimensionName
    label_key: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    converter_direction: AttackAnalyticsConverterDirection = AttackAnalyticsConverterDirection.REQUEST

    @model_validator(mode="after")
    def _validate_options(self) -> Self:
        """
        Reject options that do not belong to the selected dimension.

        Returns:
            Self: The validated dimension.

        Raises:
            ValueError: If a label key or converter direction is used on the wrong dimension.
        """
        if (self.name is AttackAnalyticsDimensionName.LABEL) != (self.label_key is not None):
            raise ValueError("label_key is required only for the label dimension")
        if (
            self.name is not AttackAnalyticsDimensionName.CONVERTER_TYPE
            and self.converter_direction is not AttackAnalyticsConverterDirection.REQUEST
        ):
            raise ValueError("converter_direction applies only to converter_type")
        if self.label_key in {"operator", "operation"}:
            raise ValueError("Use the dedicated operator or operation dimension")
        return self


class AttackAnalyticsValue(_AnalyticsModel):
    """
    An exact metadata value or an explicit absence bucket.

    A real empty string or a label named "Unknown" remains a ``VALUE``.
    ``MISSING`` and ``NO_CONVERTERS`` carry no string, so neither can collide with
    user metadata; an absent identifier is different from a known empty pipeline.
    """

    kind: AttackAnalyticsValueKind = AttackAnalyticsValueKind.VALUE
    value: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def _validate_value(self) -> Self:
        """
        Enforce the exclusive real-value versus absence representation.

        Returns:
            Self: The validated key.

        Raises:
            ValueError: If a real value has no string or an absence key carries a string.
        """
        if (self.kind is AttackAnalyticsValueKind.VALUE) != (self.value is not None):
            raise ValueError("Only a value bucket has a string value")
        return self


class AttackAnalyticsFilter(_AnalyticsModel):
    """
    One predicate, with ANY/ALL applied only to the values inside this predicate.

    Separate predicates are AND-combined, including repeated predicates for the
    same dimension. A drill-down must append its predicate instead of replacing
    an existing converter ANY filter and unintentionally broadening the cohort.
    """

    dimension: AttackAnalyticsDimension
    values: list[AttackAnalyticsValue] = Field(min_length=1, max_length=100)
    match_mode: AttackAnalyticsMatchMode = AttackAnalyticsMatchMode.ANY

    @model_validator(mode="after")
    def _validate_values(self) -> Self:
        """
        Restrict ALL/empty-pipeline semantics to converters and bound attribution values.

        Returns:
            Self: The validated predicate.

        Raises:
            ValueError: If matching semantics or attribution lengths are incompatible.
        """
        converter = self.dimension.name is AttackAnalyticsDimensionName.CONVERTER_TYPE
        if self.match_mode is AttackAnalyticsMatchMode.ALL and not converter:
            raise ValueError("ALL matching is supported only for converter_type")
        for value in self.values:
            if value.kind is AttackAnalyticsValueKind.NO_CONVERTERS and not converter:
                raise ValueError("The no_converters bucket applies only to converter_type")
            if (
                self.dimension.name
                in {
                    AttackAnalyticsDimensionName.OPERATION,
                    AttackAnalyticsDimensionName.OPERATOR,
                }
                and value.value is not None
                and len(value.value) > 128
            ):
                raise ValueError("Operator and operation values must not exceed 128 characters")
        return self


class AttackAnalyticsFilters(_AnalyticsModel):
    """
    The shared cohort selection for reports, facets, and result pages.

    No predicates and no outcomes means unrestricted saved results. All four
    outcomes normalize to the same unrestricted representation. Updated bounds
    form a half-open interval [after, before) over last-modified timestamps, not
    attack execution time, and are compared as UTC instants across timezone changes.
    Request-size limits bound work without sampling results.
    """

    MAX_PREDICATES: ClassVar[int] = 16
    MAX_VALUES: ClassVar[int] = 500

    dimensions: list[AttackAnalyticsFilter] = Field(default_factory=list, max_length=MAX_PREDICATES)
    outcomes: list[AttackOutcome] = Field(default_factory=list, max_length=4)
    updated_after: AwareDatetime | None = None
    updated_before: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_filters(self) -> Self:
        """
        Bound the combined predicates, validate the interval, and canonicalize outcomes.

        Returns:
            Self: The normalized filters used for queries and cursor fingerprints.

        Raises:
            ValueError: If the combined value limit or timestamp ordering is invalid.
        """
        if sum(len(predicate.values) for predicate in self.dimensions) > self.MAX_VALUES:
            raise ValueError(f"At most {self.MAX_VALUES} dimension values may be selected")
        if (
            self.updated_after is not None
            and self.updated_before is not None
            and self.updated_after.astimezone(UTC) >= self.updated_before.astimezone(UTC)
        ):
            raise ValueError("updated_after must be before updated_before")
        self.outcomes = sorted(set(self.outcomes), key=lambda outcome: outcome.value)
        if set(self.outcomes) == set(AttackOutcome):
            self.outcomes = []
        return self


class AttackAnalyticsQuery(_AnalyticsModel):
    """
    A report request, including the initial lightweight result page.

    ``compare_by=None`` requests one-dimensional groups; otherwise the response
    contains a bounded heatmap. Group offset/limits paginate groups, not individual
    attacks. Result and axis limits affect displayed output, never the cohort totals.
    """

    filters: AttackAnalyticsFilters = Field(default_factory=AttackAnalyticsFilters)
    group_by: AttackAnalyticsDimension = Field(
        default_factory=lambda: AttackAnalyticsDimension(name=AttackAnalyticsDimensionName.OPERATION)
    )
    compare_by: AttackAnalyticsDimension | None = None
    group_limit: int = Field(default=15, ge=1, le=50)
    group_offset: int = Field(default=0, ge=0, le=100_000)
    axis_limit: int = Field(default=20, ge=1, le=20)
    result_limit: int = Field(default=25, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_dimensions(self) -> Self:
        """
        Reject a matrix whose axes select the identical dimension.

        Returns:
            Self: The validated report query.

        Raises:
            ValueError: If both axes are identical.
        """
        if self.compare_by == self.group_by:
            raise ValueError("Heatmap dimensions must be different")
        return self


class AttackAnalyticsResultsQuery(_AnalyticsModel):
    """
    A results-only request that does not recalculate aggregates.

    The opaque cursor belongs to these filters and must be discarded when the
    cohort changes. Each page is a new read, not a frozen snapshot of an old report.
    """

    filters: AttackAnalyticsFilters = Field(default_factory=AttackAnalyticsFilters)
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=25, ge=1, le=100)


class AttackAnalyticsFacetQuery(_AnalyticsModel):
    """
    A bounded lookup for one opened filter control.

    Other dimension predicates and outcome/date restrictions remain active.
    Predicates on this exact dimension are omitted so alternatives remain
    discoverable. Search narrows option labels, not attack objectives.
    """

    filters: AttackAnalyticsFilters = Field(default_factory=AttackAnalyticsFilters)
    dimension: AttackAnalyticsDimension
    search: str = Field(default="", max_length=128)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=50, ge=1, le=100)


class AttackAnalyticsOption(_AnalyticsModel):
    """A filter or axis option with a stable, typed key."""

    key: AttackAnalyticsValue
    label: str


class AttackAnalyticsGroup(AttackAnalyticsOption):
    """One grouped outcome aggregate and its additional drill-down predicate."""

    statistics: AttackAnalyticsStatistics
    drilldown_filters: list[AttackAnalyticsFilter]


class AttackAnalyticsCell(_AnalyticsModel):
    """A heatmap cell and the predicates that select its exact cohort."""

    row: AttackAnalyticsValue
    column: AttackAnalyticsValue
    statistics: AttackAnalyticsStatistics
    drilldown_filters: list[AttackAnalyticsFilter]


class AttackAnalyticsResultRow(_AnalyticsModel):
    """A result projection without conversation, score, or media hydration."""

    attack_result_id: str
    objective_preview: str
    outcome: AttackOutcome
    updated_at: AwareDatetime
    operation: str | None
    operator: str | None
    attack_type: str | None
    target_model: str | None
    target_identifier_hash: str | None
    scenario_result_id: str | None
    targeted_harm_categories: list[str]
    request_converters: list[str]
    response_converters: list[str]
    labels: dict[str, str]


class AttackAnalyticsResults(_AnalyticsModel):
    """One fresh result page, independent of the last report's refresh time."""

    items: list[AttackAnalyticsResultRow]
    has_more: bool
    next_cursor: str | None
    computed_at: AwareDatetime


class AttackAnalyticsFacets(_AnalyticsModel):
    """A single page of facet options."""

    items: list[AttackAnalyticsOption]
    has_more: bool
    next_offset: int | None
    computed_at: AwareDatetime


class AttackAnalyticsReport(_AnalyticsModel):
    """
    A coherent saved-result report; all numeric analytics come from the SDK.

    ``computed_at`` belongs to this report and its included first result page.
    ``outcome_filter_applied`` requests the ASR asterisk but never changes the rate
    formula. ``groups_overlap`` warns that multi-valued group/cell totals cannot
    be summed to recover the overall result count. A non-null
    ``drilldown_unavailable_reason`` means appending this chart's predicates would
    exceed the shared filter budget; the current report and result pages remain valid.
    """

    filters: AttackAnalyticsFilters
    group_by: AttackAnalyticsDimension
    compare_by: AttackAnalyticsDimension | None
    summary: AttackAnalyticsStatistics
    outcome_filter_applied: bool
    groups_overlap: bool
    drilldown_unavailable_reason: str | None = None
    groups: list[AttackAnalyticsGroup]
    has_more_groups: bool
    next_group_offset: int | None
    rows: list[AttackAnalyticsOption]
    columns: list[AttackAnalyticsOption]
    cells: list[AttackAnalyticsCell]
    axes_truncated: bool
    results: AttackAnalyticsResults
    computed_at: AwareDatetime
    warnings: list[str] = Field(default_factory=list)
