# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from dataclasses import asdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from pyrit.analytics import AttackStats as AnalyticsAttackStats
from pyrit.analytics.result_analysis import AttackStats as ResultAnalysisAttackStats
from pyrit.models import (
    AttackAnalyticsDimension,
    AttackAnalyticsDimensionName,
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFilter,
    AttackAnalyticsFilters,
    AttackAnalyticsQuery,
    AttackAnalyticsReport,
    AttackAnalyticsResults,
    AttackAnalyticsResultsQuery,
    AttackAnalyticsStatistics,
    AttackAnalyticsValue,
    AttackAnalyticsValueKind,
    AttackOutcome,
    AttackResultSelection,
    AttackStats,
)


def test_attack_stats_preserves_existing_import_and_constructor() -> None:
    assert AnalyticsAttackStats is ResultAnalysisAttackStats is AttackStats
    stats = AttackStats(0.5, 2, 1, 1, 0, 0)
    expected = {
        "success_rate": 0.5,
        "total_decided": 2,
        "successes": 1,
        "failures": 1,
        "undetermined": 0,
        "errors": 0,
    }
    assert asdict(stats) == expected
    assert stats == AttackStats(**expected)


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "missing", "value": "Unknown"},
        {"kind": "value", "value": None},
        {"kind": "no_converters", "value": "none"},
    ],
)
def test_analytics_value_rejects_ambiguous_keys(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AttackAnalyticsValue.model_validate(data)


@pytest.mark.parametrize("label", ["", "Unknown", "missing", "no_converters"])
def test_real_labels_are_distinct_from_absence_buckets(label: str) -> None:
    value = AttackAnalyticsValue(value=label)
    missing = AttackAnalyticsValue(kind=AttackAnalyticsValueKind.MISSING)
    no_converters = AttackAnalyticsValue(kind=AttackAnalyticsValueKind.NO_CONVERTERS)
    assert value != missing
    assert value != no_converters
    assert missing != no_converters
    assert value.model_dump(mode="json") == {"kind": "value", "value": label}


@pytest.mark.parametrize(
    "data",
    [
        {"name": "label"},
        {"name": "label", "label_key": "operation"},
        {"name": "label", "label_key": 'unsafe"]'},
        {"name": "operation", "label_key": "team"},
        {"name": "operator", "converter_direction": "response"},
        {"name": "arbitrary_sql"},
    ],
)
def test_analytics_dimension_rejects_invalid_options(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AttackAnalyticsDimension.model_validate(data)


def test_analytics_dimension_preserves_literal_label_key() -> None:
    dimension = AttackAnalyticsDimension(name=AttackAnalyticsDimensionName.LABEL, label_key="team.project-name")
    assert dimension.label_key == "team.project-name"


def test_all_outcomes_normalizes_to_unrestricted() -> None:
    filters = AttackAnalyticsFilters(outcomes=list(AttackOutcome))
    assert filters == AttackAnalyticsFilters()


def test_outcome_filter_normalizes_duplicates_and_order() -> None:
    filters = AttackAnalyticsFilters(outcomes=[AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.SUCCESS])
    assert filters.outcomes == [AttackOutcome.FAILURE, AttackOutcome.SUCCESS]


def test_analytics_filter_keeps_additional_membership_constraints() -> None:
    filters = AttackAnalyticsFilters.model_validate(
        {
            "dimensions": [
                {
                    "dimension": {"name": "converter_type"},
                    "values": [{"value": "A"}, {"value": "B"}],
                },
                {
                    "dimension": {"name": "converter_type"},
                    "values": [{"value": "C"}],
                },
            ]
        }
    )
    assert [[value.value for value in predicate.values] for predicate in filters.dimensions] == [["A", "B"], ["C"]]
    assert AttackAnalyticsFilters.model_validate_json(filters.model_dump_json()) == filters


@pytest.mark.parametrize(
    "data",
    [
        {"updated_after": "2026-01-02T00:00:00Z", "updated_before": "2026-01-01T00:00:00Z"},
        {"updated_after": "2026-01-01T00:00:00Z", "updated_before": "2026-01-01T00:00:00Z"},
        {"updated_after": "2026-01-01"},
        {"updated_before": "2026-01-01T00:00:00"},
        {
            "dimensions": [
                {
                    "dimension": {"name": "operation"},
                    "values": [{"value": "x" * 129}],
                }
            ]
        },
        {
            "dimensions": [
                {
                    "dimension": {"name": "targeted_harm_category"},
                    "values": [{"value": "privacy"}],
                    "match_mode": "all",
                }
            ]
        },
        {
            "dimensions": [
                {
                    "dimension": {"name": "operation"},
                    "values": [{"kind": "no_converters"}],
                }
            ]
        },
    ],
)
def test_analytics_filters_reject_invalid_requests(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AttackAnalyticsFilters.model_validate(data)


@pytest.mark.parametrize("after_minute, before_minute", [(45, 15), (30, 30)])
def test_analytics_filters_accept_forward_daylight_saving_interval(*, after_minute: int, before_minute: int) -> None:
    zone = ZoneInfo("America/New_York")
    bounds = {
        "updated_after": datetime(2026, 11, 1, 1, after_minute, tzinfo=zone, fold=0),
        "updated_before": datetime(2026, 11, 1, 1, before_minute, tzinfo=zone, fold=1),
    }
    json_bounds = json.dumps({name: value.isoformat() for name, value in bounds.items()})

    from_json = AttackAnalyticsFilters.model_validate_json(json_bounds)
    from_python = AttackAnalyticsFilters.model_validate(bounds)

    assert from_python.model_dump(mode="json") == from_json.model_dump(mode="json")


@pytest.mark.parametrize("after_minute, before_minute", [(15, 45), (30, 30)])
def test_analytics_filters_reject_reversed_daylight_saving_interval(*, after_minute: int, before_minute: int) -> None:
    zone = ZoneInfo("America/New_York")
    bounds = {
        "updated_after": datetime(2026, 11, 1, 1, after_minute, tzinfo=zone, fold=1),
        "updated_before": datetime(2026, 11, 1, 1, before_minute, tzinfo=zone, fold=0),
    }
    json_bounds = json.dumps({name: value.isoformat() for name, value in bounds.items()})

    with pytest.raises(ValidationError, match="updated_after must be before updated_before"):
        AttackAnalyticsFilters.model_validate_json(json_bounds)
    with pytest.raises(ValidationError, match="updated_after must be before updated_before"):
        AttackAnalyticsFilters.model_validate(bounds)


def _filters_with_sizes(sizes: list[int]) -> AttackAnalyticsFilters:
    return AttackAnalyticsFilters(
        dimensions=[
            AttackAnalyticsFilter(
                dimension=AttackAnalyticsDimension(name=AttackAnalyticsDimensionName.LABEL, label_key=f"label-{index}"),
                values=[AttackAnalyticsValue(value=str(value)) for value in range(size)],
            )
            for index, size in enumerate(sizes)
        ]
    )


@pytest.mark.parametrize("sizes, limit", [([1] * 17, "16"), ([100] * 5 + [1], "500")])
def test_analytics_filters_reject_effective_query_over_budget(*, sizes: list[int], limit: str) -> None:
    with pytest.raises(ValidationError, match=limit):
        _filters_with_sizes(sizes)


@pytest.mark.parametrize("sizes", [[1] * 16, [100] * 5, [100] * 4 + [89] + [1] * 11])
def test_analytics_filters_accept_effective_query_at_budget(sizes: list[int]) -> None:
    filters = _filters_with_sizes(sizes)
    assert len(filters.dimensions) == len(sizes)
    assert sum(len(predicate.values) for predicate in filters.dimensions) == sum(sizes)


@pytest.mark.parametrize("size", [0, 101])
def test_analytics_filter_bounds_each_predicate(size: int) -> None:
    with pytest.raises(ValidationError):
        _filters_with_sizes([size])


@pytest.mark.parametrize(
    "field, minimum, maximum",
    [
        ("group_limit", 1, 50),
        ("group_offset", 0, 100_000),
        ("axis_limit", 1, 20),
        ("result_limit", 1, 100),
    ],
)
def test_report_limits_are_bounded(*, field: str, minimum: int, maximum: int) -> None:
    for value in (minimum, maximum):
        assert getattr(AttackAnalyticsQuery.model_validate({field: value}), field) == value
    for value in (minimum - 1, maximum + 1):
        with pytest.raises(ValidationError):
            AttackAnalyticsQuery.model_validate({field: value})


def test_results_request_does_not_accept_chart_configuration() -> None:
    with pytest.raises(ValidationError):
        AttackAnalyticsResultsQuery.model_validate({"group_by": {"name": "operation"}})


@pytest.mark.parametrize("limit", [0, 101])
def test_results_request_bounds_page_size(limit: int) -> None:
    with pytest.raises(ValidationError):
        AttackAnalyticsResultsQuery(limit=limit)


def test_results_request_accepts_maximum_page_and_cursor_lengths() -> None:
    query = AttackAnalyticsResultsQuery(limit=100, cursor="x" * 2048)
    assert query.limit == 100
    assert query.cursor == "x" * 2048
    with pytest.raises(ValidationError):
        AttackAnalyticsResultsQuery(cursor="x" * 2049)


@pytest.mark.parametrize("field, minimum, maximum", [("limit", 1, 100), ("offset", 0, 100_000)])
def test_facet_request_limits_are_bounded(*, field: str, minimum: int, maximum: int) -> None:
    for value in (minimum, maximum):
        query = AttackAnalyticsFacetQuery.model_validate({"dimension": {"name": "operation"}, field: value})
        assert getattr(query, field) == value
    for value in (minimum - 1, maximum + 1):
        with pytest.raises(ValidationError):
            AttackAnalyticsFacetQuery.model_validate({"dimension": {"name": "operation"}, field: value})


def test_facet_request_bounds_search_length() -> None:
    dimension = AttackAnalyticsDimension(name=AttackAnalyticsDimensionName.OPERATION)
    assert AttackAnalyticsFacetQuery(dimension=dimension, search="x" * 128).search == "x" * 128
    with pytest.raises(ValidationError):
        AttackAnalyticsFacetQuery(dimension=dimension, search="x" * 129)


def test_heatmap_requires_distinct_dimensions() -> None:
    with pytest.raises(ValidationError, match="different"):
        AttackAnalyticsQuery.model_validate({"compare_by": {"name": "operation"}})


def test_heatmap_accepts_distinct_converter_directions() -> None:
    query = AttackAnalyticsQuery.model_validate(
        {
            "group_by": {"name": "converter_type"},
            "compare_by": {"name": "converter_type", "converter_direction": "response"},
        }
    )
    assert query.group_by != query.compare_by


@pytest.mark.parametrize("reason", [None, "The drill-down would exceed the filter budget."])
def test_report_round_trips_statistics_and_drilldown_availability(reason: str | None) -> None:
    computed_at = datetime(2026, 9, 21, tzinfo=UTC)
    statistics = AttackAnalyticsStatistics(
        success_rate=None,
        total_decided=0,
        successes=0,
        failures=0,
        undetermined=0,
        errors=0,
        total_results=0,
        decided_share=None,
        outcome_shares=dict.fromkeys(AttackOutcome, 0.0),
    )
    report = AttackAnalyticsReport(
        filters=AttackAnalyticsFilters(),
        group_by=AttackAnalyticsDimension(name=AttackAnalyticsDimensionName.OPERATION),
        compare_by=None,
        summary=statistics,
        outcome_filter_applied=False,
        groups_overlap=False,
        drilldown_unavailable_reason=reason,
        groups=[],
        has_more_groups=False,
        next_group_offset=None,
        rows=[],
        columns=[],
        cells=[],
        axes_truncated=False,
        results=AttackAnalyticsResults(items=[], has_more=False, next_cursor=None, computed_at=computed_at),
        computed_at=computed_at,
    )
    assert isinstance(report.summary, AttackStats)
    assert report.model_dump(mode="json")["summary"] == asdict(statistics)
    assert report.drilldown_unavailable_reason == reason
    assert AttackAnalyticsReport.model_validate_json(report.model_dump_json()) == report


def test_attack_result_selection_preserves_explicit_modes() -> None:
    assert {selection.value for selection in AttackResultSelection} == {"all_results", "latest_per_conversation"}
