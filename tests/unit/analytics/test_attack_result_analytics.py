# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from pyrit.analytics import AttackResultAnalytics, compute_scenario_statistics
from pyrit.common.task_utils import gather_with_cleanup_async
from pyrit.exceptions.analytics_exception import AnalyticsBusyException, AnalyticsDataException
from pyrit.memory import CentralMemory, MemoryInterface, SQLiteMemory
from pyrit.memory.analytics_identity_v1 import ObjectiveTargetAnalyticsIdentityV1
from pyrit.memory.attack_analytics import RawAnalyticsOption
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackAnalyticsDimension,
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFilter,
    AttackAnalyticsFilters,
    AttackAnalyticsQuery,
    AttackAnalyticsResultsQuery,
    AttackAnalyticsValue,
    AttackIdentifier,
    AttackOutcome,
    AttackResultMetadata,
    AttackResultRole,
    TargetIdentifier,
)
from unit.memory.test_attack_analytics import make_result, predicate
from unit.mocks import make_scenario_result

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

    from pyrit.memory.query_control import QueryControl


@pytest.fixture
async def analytics(sqlite_instance: SQLiteMemory) -> AsyncGenerator[AttackResultAnalytics, None]:
    async with AttackResultAnalytics(memory=sqlite_instance) as analytics:
        yield analytics


@pytest.fixture
async def mixed_results(sqlite_instance: SQLiteMemory) -> None:
    outcomes = [AttackOutcome.SUCCESS] * 4 + [AttackOutcome.FAILURE] * 2
    outcomes += [AttackOutcome.UNDETERMINED] * 3 + [AttackOutcome.ERROR]
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(index=index, outcome=outcome) for index, outcome in enumerate(outcomes, 1)]
    )


@pytest.mark.usefixtures("mixed_results")
async def test_all_saved_ids_and_outcomes_use_raw_decided_denominator(analytics: AttackResultAnalytics) -> None:
    report = await analytics.query_async()
    assert report.summary.total_results == 10
    assert report.summary.total_decided == 6
    assert report.summary.success_rate == pytest.approx(4 / 6)
    assert report.summary.success_rate_all == 0.4
    assert report.summary.decided_share == 0.6
    assert report.summary.outcome_shares == {
        AttackOutcome.SUCCESS: 0.4,
        AttackOutcome.FAILURE: 0.2,
        AttackOutcome.UNDETERMINED: 0.3,
        AttackOutcome.ERROR: 0.1,
    }
    assert not report.outcome_filter_applied
    assert not report.groups_overlap
    assert report.groups[0].statistics == report.summary
    assert len(report.results.items) == 10
    assert report.computed_at == report.results.computed_at


async def test_empty_report_has_unavailable_rates(analytics: AttackResultAnalytics) -> None:
    report = await analytics.query_async()
    assert report.summary.total_results == report.summary.total_decided == 0
    assert report.summary.success_rate is None
    assert report.summary.success_rate_all is None
    assert report.summary.decided_share is None
    assert set(report.summary.outcome_shares.values()) == {0.0}
    assert report.groups == report.cells == report.results.items == []


@pytest.mark.usefixtures("mixed_results")
@pytest.mark.parametrize(
    ("outcome", "count", "rate"),
    [
        (AttackOutcome.SUCCESS, 4, 1.0),
        (AttackOutcome.FAILURE, 2, 0.0),
        (AttackOutcome.ERROR, 1, None),
        (AttackOutcome.UNDETERMINED, 3, None),
    ],
)
async def test_outcome_filter_annotates_and_restricts_entire_report(
    analytics: AttackResultAnalytics, outcome: AttackOutcome, count: int, rate: float | None
) -> None:
    report = await analytics.query_async(query=AttackAnalyticsQuery(filters=AttackAnalyticsFilters(outcomes=[outcome])))
    assert report.outcome_filter_applied
    assert report.summary.total_results == count
    assert report.summary.success_rate == rate
    assert report.summary.success_rate_all == (1.0 if outcome is AttackOutcome.SUCCESS else 0.0)
    assert report.groups[0].statistics == report.summary
    assert all(row.outcome == outcome for row in report.results.items)


@pytest.mark.usefixtures("mixed_results")
async def test_all_outcomes_normalize_to_unrestricted(analytics: AttackResultAnalytics) -> None:
    report = await analytics.query_async(
        query=AttackAnalyticsQuery(filters=AttackAnalyticsFilters(outcomes=list(AttackOutcome)))
    )
    assert not report.outcome_filter_applied
    assert report.filters.outcomes == []
    assert report.summary.total_results == 10


async def test_overall_rate_is_not_an_average_of_groups(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(operation="one"),
            *[make_result(index=index, operation="two", outcome=AttackOutcome.FAILURE) for index in (2, 3, 4)],
        ]
    )
    with patch.object(
        sqlite_instance, "get_attack_results_async", side_effect=AssertionError("Must not hydrate saved results")
    ):
        report = await analytics.query_async()
    assert report.summary.success_rate == 0.25
    assert {group.statistics.success_rate for group in report.groups} == {0.0, 1.0}
    assert len(report.results.items) == 4


async def test_retry_results_keep_raw_asr_distinct_from_scenario_unit_success(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    results = [make_result(outcome=AttackOutcome.FAILURE), make_result(index=2)]
    for result in results:
        result.objective = "One objective, retried"
    scenario = make_scenario_result(attack_results={"attack": results})
    statistics = compute_scenario_statistics(scenario)
    assert statistics.overall.completed == statistics.overall.succeeded == 1
    assert statistics.overall.success_percentage == 100
    assert statistics.overall.outcomes.success_rate == statistics.overall.outcomes.success_rate_all == 1.0
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    report = await analytics.query_async()
    assert report.summary.total_results == report.summary.total_decided == 2
    assert report.summary.success_rate == 0.5
    assert report.summary.success_rate_all == 0.5
    assert {row.attack_result_id for row in report.results.items} == {result.attack_result_id for result in results}


@pytest.mark.parametrize(
    "outcomes",
    [
        [],
        [AttackOutcome.SUCCESS, AttackOutcome.ERROR],
        [AttackOutcome.FAILURE, AttackOutcome.UNDETERMINED],
        list(AttackOutcome),
        [AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.ERROR, AttackOutcome.ERROR],
    ],
)
@pytest.mark.parametrize("compare", [False, True])
async def test_same_population_has_identical_shared_statistics_in_attacks_and_scenarios(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory, outcomes: list[AttackOutcome], compare: bool
) -> None:
    results = [make_result(index=index, outcome=outcome) for index, outcome in enumerate(outcomes, 1)]
    if results:
        await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    scenario = make_scenario_result(attack_results={"attack": results})
    expected = compute_scenario_statistics(scenario).overall.outcomes
    report = await analytics.query_async(
        query=AttackAnalyticsQuery(compare_by=AttackAnalyticsDimension(name="converter_type") if compare else None)
    )
    assert report.summary == expected
    assert all(group.statistics == expected for group in report.groups)
    assert all(cell.statistics == expected for cell in report.cells)


async def test_result_roles_do_not_silently_remove_saved_ids(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    results = [make_result(index=index) for index, _ in enumerate(AttackResultRole, 1)]
    for result, role in zip(results, AttackResultRole, strict=True):
        result.attribution_data = AttackResultMetadata(result_role=role).to_metadata()
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    report = await analytics.query_async()
    assert report.summary.total_results == len(AttackResultRole)
    assert {row.attack_result_id for row in report.results.items} == {result.attack_result_id for result in results}


async def test_drilldown_appends_to_existing_any_and_response_all_predicates(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(converters=["Alpha", "Gamma"], response_converters=["X", "Y"]),
            make_result(index=2, converters=["Gamma"], response_converters=["X", "Y"]),
            make_result(index=3, converters=["Beta"], response_converters=["X", "Y"]),
            make_result(index=4, converters=["Alpha", "Gamma"], response_converters=["X"]),
        ]
    )
    filters = AttackAnalyticsFilters.model_validate(
        {
            "dimensions": [
                predicate(name="converter_type", values=["Alpha", "Beta"]),
                predicate(
                    name="converter_type",
                    values=["X", "Y"],
                    dimension_options={"converter_direction": "response"},
                    match_mode="all",
                ),
            ]
        }
    )
    report = await analytics.query_async(
        query=AttackAnalyticsQuery(filters=filters, group_by=AttackAnalyticsDimension(name="converter_type"))
    )
    assert report.summary.total_results == 2
    assert report.groups_overlap
    assert sum(group.statistics.total_results for group in report.groups) == 3
    gamma = next(group for group in report.groups if group.key.value == "gamma")
    narrowed = filters.model_copy(update={"dimensions": [*filters.dimensions, *gamma.drilldown_filters]})
    results = await analytics.results_async(query=AttackAnalyticsResultsQuery(filters=narrowed))
    assert len(results.items) == gamma.statistics.total_results == 1
    assert results.items[0].request_converters == ["Alpha", "Gamma"]
    assert len(gamma.drilldown_filters) == 1
    assert filters == report.filters


@pytest.mark.parametrize("compare", [False, True])
@pytest.mark.parametrize("budget", ["predicates", "values"])
@pytest.mark.parametrize("remaining", [0, 1, 2])
async def test_terminal_filter_budget_preserves_current_report_and_final_legal_click(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory, compare: bool, budget: str, remaining: int
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result(categories=["privacy"])])
    sizes = [1] * (16 - remaining) if budget == "predicates" else [100, 100, 100, 100, 100 - remaining]
    filters = AttackAnalyticsFilters.model_validate(
        {
            "dimensions": [
                {
                    "dimension": {"name": "label", "label_key": f"absent-{index}"},
                    "values": [{"kind": "missing"}, *({"value": f"value-{item}"} for item in range(size - 1))],
                }
                for index, size in enumerate(sizes)
            ]
        }
    )
    query = AttackAnalyticsQuery(
        filters=filters,
        compare_by=AttackAnalyticsDimension(name="targeted_harm_category") if compare else None,
    )
    report = await analytics.query_async(query=query)
    assert report.summary.total_results == len(report.results.items) == 1
    assert report.filters == filters
    extra = report.cells[0].drilldown_filters if compare else report.groups[0].drilldown_filters
    assert len(extra) == (2 if compare else 1)
    narrowed = filters.model_copy(update={"dimensions": [*filters.dimensions, *extra]})
    if remaining < len(extra):
        assert f"maximum {16 if budget == 'predicates' else 500}" in report.drilldown_unavailable_reason
        with pytest.raises(ValidationError):
            await analytics.results_async(query=AttackAnalyticsResultsQuery(filters=narrowed))
    else:
        assert report.drilldown_unavailable_reason is None
        terminal = await analytics.query_async(query=query.model_copy(update={"filters": narrowed}))
        assert terminal.summary.total_results == 1
        assert (terminal.drilldown_unavailable_reason is not None) == (remaining < len(extra) * 2)
        assert terminal.filters.dimensions[: len(filters.dimensions)] == filters.dimensions
        assert len((await analytics.results_async(query=AttackAnalyticsResultsQuery(filters=narrowed))).items) == 1


async def test_matrix_empty_cells_and_additional_predicates_are_exact(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(operation="one", categories=["privacy"]),
            make_result(index=2, operation="two", categories=["safety"], outcome=AttackOutcome.FAILURE),
        ]
    )
    report = await analytics.query_async(
        query=AttackAnalyticsQuery(compare_by=AttackAnalyticsDimension(name="targeted_harm_category"))
    )
    assert len(report.cells) == 4
    assert report.groups_overlap
    for cell in report.cells:
        selected = report.filters.model_copy(
            update={"dimensions": [*report.filters.dimensions, *cell.drilldown_filters]}
        )
        page = await analytics.results_async(query=AttackAnalyticsResultsQuery(filters=selected))
        assert len(page.items) == cell.statistics.total_results
        if not page.items:
            assert cell.statistics.success_rate is None
            assert cell.statistics.success_rate_all is None
            assert set(cell.statistics.outcome_shares.values()) == {0.0}


@pytest.mark.parametrize(
    ("kind", "value", "stored_label", "dimension", "label"),
    [
        ("missing", None, "ignored", "operation", "Not recorded"),
        ("no_converters", None, None, "converter_type", "No converters"),
        ("value", "", "", "operation", "(Blank)"),
        ("value", " \t", None, "label", "(Blank)"),
        ("value", "Unknown", "Unknown", "operation", "Unknown"),
        ("value", "Not recorded", "Not recorded", "operation", "Not recorded"),
        ("value", "content-independent-eval-hash", "model", "objective_target", "model (val-hash)"),
        ("value", "scenario-12345678", "run", "scenario", "run (12345678)"),
        ("value", "scenario-12345678", None, "scenario", "scenario-12345678"),
    ],
)
def test_display_labels_do_not_change_typed_keys(
    kind: str, value: str | None, stored_label: str | None, dimension: str, label: str
) -> None:
    raw = RawAnalyticsOption(key=AttackAnalyticsValue(kind=kind, value=value), label=stored_label)
    option = AttackResultAnalytics._option(
        raw=raw,
        dimension=AttackAnalyticsDimension(name=dimension, label_key="custom" if dimension == "label" else None),
    )
    assert option.label == label
    assert option.key == raw.key


@pytest.mark.parametrize("counts", [{"unexpected": 1}, {"success": True}, {"error": -1}, {"failure": 1.5}])
def test_statistics_reject_invalid_raw_data(counts: dict[str, int]) -> None:
    with pytest.raises(AnalyticsDataException):
        AttackResultAnalytics._statistics(counts)


async def test_pages_and_facets_are_fresh_reads_without_reports(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(), make_result(index=2, operation="operation-b")]
    )
    report = await analytics.query_async(query=AttackAnalyticsQuery(result_limit=1))
    assert report.results.has_more
    with patch.object(analytics._reader, "report_async", new_callable=AsyncMock) as read_report:
        page = await analytics.results_async(query=AttackAnalyticsResultsQuery(cursor=report.results.next_cursor))
        facet = await analytics.facets_async(
            query=AttackAnalyticsFacetQuery(
                dimension=AttackAnalyticsDimension(name="operation"),
                filters=AttackAnalyticsFilters.model_validate(
                    {"dimensions": [predicate(name="operation", values=["operation-a"])]}
                ),
                limit=1,
            )
        )
        continuation = await analytics.facets_async(
            query=AttackAnalyticsFacetQuery(
                dimension=AttackAnalyticsDimension(name="operation"), offset=facet.next_offset, limit=1
            )
        )
    read_report.assert_not_awaited()
    assert len(page.items) == 1
    assert page.items[0].attack_result_id != report.results.items[0].attack_result_id
    assert page.computed_at >= report.computed_at
    assert facet.has_more and facet.next_offset == 1
    assert not continuation.has_more and continuation.next_offset is None
    assert {facet.items[0].key.value, continuation.items[0].key.value} == {"operation-a", "operation-b"}
    with pytest.raises(ValueError, match="cursor"):
        await analytics.results_async(query=AttackAnalyticsResultsQuery(cursor="invalid"))


async def test_reports_neither_share_mutable_results_nor_cache_outcomes(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    result = make_result(categories=["privacy"])
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[result])
    query = AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="targeted_harm_category"))
    first = await analytics.query_async(query=query)
    second = await analytics.query_async(query=query)
    first.summary.successes = 999
    first.results.items.clear()
    first.group_by.name = "operation"
    assert second.summary.successes == 1
    assert len(second.results.items) == 1
    assert second.group_by == query.group_by
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=result.attack_result_id, update_fields={"outcome": AttackOutcome.FAILURE.value}
    )
    updated = await analytics.query_async(query=query)
    assert updated.summary.total_results == 1
    assert updated.groups[0].statistics.failures == 1
    assert updated.summary.success_rate == 0.0
    assert updated.summary.success_rate_all == 0.0


async def test_native_report_and_quick_calls_use_independent_sessions(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(), make_result(index=2, outcome=AttackOutcome.FAILURE)]
    )
    sessions: list[AsyncSession] = []
    acquire = sqlite_instance.get_session_async
    execution = analytics._execution()
    # This exercises ownership under concurrency, not a machine-speed latency target.
    for lane in execution._lanes.values():
        lane.timeout = 10

    async def acquire_async() -> AsyncSession:
        session = await acquire()
        sessions.append(session)
        return session

    async with AttackResultAnalytics(memory=sqlite_instance) as second:
        assert second._execution() is execution
        with patch.object(sqlite_instance, "get_session_async", side_effect=acquire_async):
            results = await gather_with_cleanup_async(
                [
                    *(analytics.query_async() for _ in range(5)),
                    second.results_async(),
                    second.facets_async(
                        query=AttackAnalyticsFacetQuery(dimension=AttackAnalyticsDimension(name="operation"))
                    ),
                ]
            )
        reports, page, facet = results[:5], results[5], results[6]
        assert all(report.summary.total_results == 2 for report in reports)
        assert all(report.summary.success_rate == 0.5 for report in reports)
        assert len(page.items) == 2
        assert len(facet.items) == 1
        reports[0].summary.successes = 999
        assert all(report.summary.successes == 1 for report in reports[1:])
        assert len({id(session) for session in sessions}) == 7
        assert not any(session.in_transaction() for session in sessions)
        assert all(lane.active == 0 for lane in execution._lanes.values())


@pytest.mark.parametrize("operation", ["query", "results", "facets"])
async def test_query_snapshot_precedes_admission(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory, operation: str
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result()])
    execution = analytics._execution()
    lane = execution._lanes[operation == "query"]
    lane.limit, lane.timeout = 1, 10
    entered, release = asyncio.Event(), asyncio.Event()

    async def occupy_async(control: QueryControl) -> None:
        entered.set()
        await release.wait()

    occupying = asyncio.create_task(execution.run_async(report=operation == "query", task=occupy_async))
    await entered.wait()
    filters = AttackAnalyticsFilters.model_validate({"dimensions": [predicate(name="operator", values=["operator-a"])]})
    filters.updated_after = datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=2)))
    if operation == "query":
        request = AttackAnalyticsQuery(filters=filters)
        pending = asyncio.create_task(analytics.query_async(query=request))
    elif operation == "results":
        request = AttackAnalyticsResultsQuery(filters=filters)
        pending = asyncio.create_task(analytics.results_async(query=request))
    else:
        request = AttackAnalyticsFacetQuery(filters=filters, dimension=AttackAnalyticsDimension(name="operation"))
        pending = asyncio.create_task(analytics.facets_async(query=request))
    try:
        await asyncio.sleep(0)
        assert len(lane.queued) == 1
        filters.dimensions[0].values[0].value = "mutated"
        filters.outcomes.append(AttackOutcome.ERROR)
        release.set()
        await occupying
        result = await pending
        if operation == "query":
            assert result.summary.total_results == 1
            assert result.filters.dimensions[0].values[0].value == "operator-a"
            assert result.filters.updated_after == datetime(2024, 12, 31, 22, tzinfo=UTC)
            assert result.filters.updated_after.tzinfo is UTC
        else:
            assert len(result.items) == 1
    finally:
        release.set()
        await asyncio.gather(occupying, pending, return_exceptions=True)


@pytest.mark.parametrize("operation", ["query", "results", "facets"])
async def test_mutated_models_are_revalidated_before_execution(
    analytics: AttackResultAnalytics, operation: str
) -> None:
    filters = AttackAnalyticsFilters(
        dimensions=[
            AttackAnalyticsFilter(
                dimension=AttackAnalyticsDimension(name="operation"), values=[AttackAnalyticsValue(value="operation-a")]
            )
        ]
    )
    filters.dimensions[0].values.clear()
    with patch.object(analytics._execution(), "run_async", new_callable=AsyncMock) as run:
        with pytest.raises(ValidationError):
            if operation == "query":
                await analytics.query_async(query=AttackAnalyticsQuery(filters=filters))
            elif operation == "results":
                await analytics.results_async(query=AttackAnalyticsResultsQuery(filters=filters))
            else:
                await analytics.facets_async(
                    query=AttackAnalyticsFacetQuery(
                        filters=filters, dimension=AttackAnalyticsDimension(name="operation")
                    )
                )
        run.assert_not_awaited()


async def test_frozen_target_groups_preserve_content_hashes_and_every_result_id(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    targets = [
        TargetIdentifier(
            class_name="MockTarget",
            class_module="tests",
            model_name=f"deploy-{index}",
            underlying_model_name="model-x",
            endpoint=f"https://example-{index}.test",
            temperature=0.3,
        )
        for index in (1, 2)
    ]
    results = [make_result(), make_result(index=2, outcome=AttackOutcome.FAILURE)]
    for result, target in zip(results, targets, strict=True):
        result.atomic_attack_identifier = AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(class_name="ProbeAttack", class_module="tests", objective_target=target)
        )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    with patch.object(ObjectiveTargetAnalyticsIdentityV1, "hash", side_effect=AssertionError("Must use stored keys")):
        report = await analytics.query_async(
            query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="objective_target"))
        )
        group = report.groups[0]
        page = await analytics.results_async(
            query=AttackAnalyticsResultsQuery(filters=AttackAnalyticsFilters(dimensions=group.drilldown_filters))
        )
    assert len(report.groups) == 1
    assert group.key.value == "32e7c2bf2a31f21d91dc8bebca280a5ffecf149df474052c40889f7c77b84e81"
    assert group.statistics.total_results == 2
    assert group.statistics.success_rate == 0.5
    assert {row.target_identifier_hash for row in page.items} == {target.hash for target in targets}
    assert {row.attack_result_id for row in page.items} == {result.attack_result_id for result in results}


async def test_construction_and_unused_close_do_not_touch_database() -> None:
    memory = MagicMock(spec=MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        analytics = AttackResultAnalytics()
    memory.get_session_async.assert_not_awaited()
    await analytics.close_async()
    assert memory not in AttackResultAnalytics._EXECUTIONS
    with pytest.raises(AnalyticsBusyException):
        await analytics.query_async()


@pytest.mark.parametrize("cancel_close", [False, True])
async def test_same_backend_shares_controller_and_drain_blocks_replacement(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory, cancel_close: bool
) -> None:
    second = AttackResultAnalytics(memory=sqlite_instance)
    execution = analytics._execution()
    assert second._execution() is execution
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_async(control: QueryControl) -> None:
        entered.set()
        await release.wait()

    work = asyncio.create_task(execution.run_async(report=True, task=blocked_async))
    await entered.wait()
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    closing = asyncio.create_task(analytics.close_async())
    try:
        await asyncio.sleep(0)
        newcomer = AttackResultAnalytics(memory=sqlite_instance)
        assert newcomer._execution() is execution
        assert AttackResultAnalytics._EXECUTIONS[sqlite_instance] is execution
        assert not closing.done()
        if cancel_close:
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done()
        with pytest.raises(AnalyticsBusyException):
            await newcomer.results_async()
        with pytest.raises(AnalyticsBusyException):
            await second.query_async()
    finally:
        release.set()
        if cancel_close:
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            await closing
    assert sqlite_instance not in AttackResultAnalytics._EXECUTIONS
    with pytest.raises(AnalyticsBusyException):
        await second.results_async()
    async with AttackResultAnalytics(memory=sqlite_instance) as replacement:
        assert replacement._execution() is not execution
        await second.close_async()
        assert AttackResultAnalytics._EXECUTIONS[sqlite_instance] is replacement._execution()
        assert (await replacement.query_async()).summary.total_results == 0
    await gather_with_cleanup_async([second.close_async(), newcomer.close_async()])


async def test_foreign_loop_does_not_create_another_backend_budget_or_session(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    execution = analytics._execution()

    async def foreign_loop_async() -> None:
        other = AttackResultAnalytics(memory=sqlite_instance)
        with pytest.raises(RuntimeError, match="owning event loop"):
            await other.query_async()
        with pytest.raises(RuntimeError, match="owning event loop"):
            await other.close_async()
        assert other._controller is execution

    with patch.object(
        sqlite_instance, "get_session_async", side_effect=AssertionError("Must reject before using memory")
    ):
        await asyncio.to_thread(asyncio.run, foreign_loop_async())
    assert not execution.is_closed
    assert (await analytics.query_async()).summary.total_results == 0


async def test_different_memory_objects_have_independent_lifetimes() -> None:
    first_memory, second_memory = MagicMock(spec=MemoryInterface), MagicMock(spec=MemoryInterface)
    async with (
        AttackResultAnalytics(memory=first_memory) as first,
        AttackResultAnalytics(memory=second_memory) as second,
    ):
        assert first._execution() is not second._execution()
        unused = AttackResultAnalytics(memory=second_memory)
        await unused.close_async()
        await first.close_async()
        assert not second._execution().is_closed


async def test_native_session_cleanup_retains_capacity_after_caller_cancellation(
    analytics: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result()])
    execution = analytics._execution()
    execution._lanes[True].limit = 1
    entered, release = asyncio.Event(), asyncio.Event()
    session = await sqlite_instance.get_session_async()
    close = session.close

    async def delayed_close_async() -> None:
        entered.set()
        await release.wait()
        await close()

    with (
        patch.object(sqlite_instance, "get_session_async", new_callable=AsyncMock, return_value=session) as acquire,
        patch.object(session, "close", side_effect=delayed_close_async),
    ):
        caller = asyncio.create_task(analytics.query_async())
        try:
            await entered.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert execution._lanes[True].active == 1
            waiting = asyncio.create_task(analytics.query_async())
            await asyncio.sleep(0)
            acquire.assert_awaited_once()
            closing = asyncio.create_task(analytics.close_async())
            await asyncio.sleep(0)
            with pytest.raises(AnalyticsBusyException):
                await waiting
            assert not closing.done()
        finally:
            release.set()
            await analytics.close_async()
            await asyncio.gather(caller, return_exceptions=True)
        await closing
    assert execution.is_closed
    async with AttackResultAnalytics(memory=sqlite_instance) as replacement:
        assert (await replacement.query_async()).summary.total_results == 1
