# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from aiosqlite import Connection as SQLiteConnection
from pydantic import ValidationError
from sqlalchemy import UnicodeText, column, event, literal, literal_column, select, text, update
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.exc import CompileError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.sql import Select, visitors
from sqlalchemy.sql.functions import Function
from sqlalchemy.sql.selectable import CTE

from pyrit.exceptions.analytics_exception import AnalyticsDataException, AnalyticsTimeoutException
from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.memory.analytics_sql import (
    JsonArrayEmpty,
    JsonIsArray,
    JsonScalar,
    ResolvedAttackIdentifierHash,
    UnicodeLower,
)
from pyrit.memory.attack_analytics import AttackAnalyticsReader
from pyrit.memory.attack_analytics_query import AttackAnalyticsQueryCompiler
from pyrit.memory.memory_models import AttackIdentifierEntry, AttackResultEntry, Base, TargetIdentifierEntry
from pyrit.memory.query_control import QueryControl
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
    AttackResult,
    ConverterIdentifier,
    TargetIdentifier,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Dialect
    from sqlalchemy.sql import ClauseElement


def make_result(
    *,
    index: int = 1,
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    operation: str | None = "operation-a",
    categories: list[str] | None = None,
    converters: list[str] | None = None,
    response_converters: list[str] | None = None,
    labels: dict[str, str] | None = None,
    attack_class_name: str = "ProbeAttack",
) -> AttackResult:
    return AttackResult(
        attack_result_id=str(uuid.UUID(int=index)),
        conversation_id="shared-main-conversation",
        objective=f"Synthetic objective {index}",
        operation=operation,
        operator="operator-a",
        labels=labels or {},
        targeted_harm_categories=categories or [],
        outcome=outcome,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name=attack_class_name,
                class_module="tests",
                objective_target=TargetIdentifier(class_name="MockTarget", class_module="tests", model_name="model-a"),
                request_converters=[
                    ConverterIdentifier(class_name=name, class_module="tests", params={"variant": position})
                    for position, name in enumerate(converters or [])
                ],
                response_converters=[
                    ConverterIdentifier(class_name=name, class_module="tests") for name in response_converters or []
                ],
            )
        ),
    )


# Only a guard against hangs: loaded CI runners have taken over 30s for these queries.
_QUERY_BUDGET_SECONDS = 120


def control() -> QueryControl:
    return QueryControl(deadline=time.monotonic() + _QUERY_BUDGET_SECONDS)


def predicate(*, name: str, values: list[str | None], **options: Any) -> dict[str, Any]:
    return {
        "dimension": {"name": name, **options.pop("dimension_options", {})},
        "values": [{"value": value} if value is not None else {"kind": "missing"} for value in values],
        **options,
    }


async def test_report_counts_result_ids_not_conversations(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(), make_result(index=2, outcome=AttackOutcome.FAILURE)]
    )
    result = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(), control=control()
    )
    assert result.counts == {"failure": 1, "success": 1}
    assert len(result.results.items) == 2
    assert result.groups[0].counts == result.counts
    assert result.results.items[0].target_model == "model-a"
    assert result.results.items[0].attack_type == "ProbeAttack"


async def test_multivalued_groups_and_cells_do_not_multiply_results(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(categories=["privacy", "privacy", "safety"], converters=["Alpha", "Alpha", "Beta"]),
            make_result(index=2, outcome=AttackOutcome.FAILURE, categories=["privacy"], converters=["Alpha"]),
        ]
    )
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
        compare_by=AttackAnalyticsDimension(name="converter_type"),
    )
    result = await AttackAnalyticsReader(memory=sqlite_instance).report_async(query=query, control=control())
    cells = {(cell.option.key.value, cell.column.key.value): cell.counts for cell in result.cells}
    assert result.counts == {"failure": 1, "success": 1}
    assert cells[("privacy", "alpha")] == {"failure": 1, "success": 1}
    assert cells[("privacy", "beta")] == {"success": 1}
    assert cells[("safety", "alpha")] == {"success": 1}
    assert cells[("safety", "beta")] == {"success": 1}


async def test_case_variants_within_one_result_have_one_membership(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(categories=["Privacy", "privacy"], converters=["Alpha", "ALPHA"])]
    )
    report = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(
            group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
            compare_by=AttackAnalyticsDimension(name="converter_type"),
        ),
        control=control(),
    )
    assert len(report.cells) == 1
    assert report.cells[0].counts == {"success": 1}
    assert report.cells[0].option.key.value == "privacy"
    assert report.cells[0].column.key.value == "alpha"


async def test_non_ascii_case_variants_share_group_filter_and_facet_keys(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(categories=["\u00c9COLE"], converters=["\u00d6mega"], attack_class_name="\u00c9cho"),
            make_result(index=2, categories=["\u00e9cole"], converters=["\u00f6mega"], attack_class_name="\u00e9cho"),
        ]
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    category = AttackAnalyticsDimension(name="targeted_harm_category")
    converter = AttackAnalyticsDimension(name="converter_type")
    report = await reader.report_async(
        query=AttackAnalyticsQuery(group_by=category, compare_by=converter), control=control()
    )
    assert report.counts == {"success": 2}
    assert len(report.cells) == 1
    assert report.cells[0].counts == {"success": 2}
    assert (report.cells[0].option.key.value, report.cells[0].column.key.value) == ("\u00e9cole", "\u00f6mega")
    attack = AttackAnalyticsDimension(name="attack_type")
    attack_report = await reader.report_async(query=AttackAnalyticsQuery(group_by=attack), control=control())
    assert [(group.option.key.value, group.counts) for group in attack_report.groups] == [("\u00e9cho", {"success": 2})]

    filters = AttackAnalyticsFilters(
        dimensions=[
            AttackAnalyticsFilter(dimension=category, values=[AttackAnalyticsValue(value="\u00e9cole")]),
            AttackAnalyticsFilter(dimension=converter, values=[AttackAnalyticsValue(value="\u00f6mega")]),
            AttackAnalyticsFilter(dimension=attack, values=[AttackAnalyticsValue(value="\u00e9cho")]),
        ]
    )
    filtered = await reader.report_async(query=AttackAnalyticsQuery(filters=filters), control=control())
    assert filtered.counts == {"success": 2}
    assert len(filtered.results.items) == 2
    facet = await reader.facets_async(
        query=AttackAnalyticsFacetQuery(dimension=category, search="\u00e9co"), control=control()
    )
    assert [item.key.value for item in facet.items] == ["\u00e9cole"]
    attack_facet = await reader.facets_async(
        query=AttackAnalyticsFacetQuery(dimension=attack, search="\u00c9c"), control=control()
    )
    assert [item.key.value for item in attack_facet.items] == ["\u00e9cho"]

    with sqlite_instance.engine.connect() as connection:
        assert connection.execute(select(UnicodeLower(literal("\u00c9COLE")))).scalar_one() == "\u00e9cole"
    async with sqlite_instance._get_async_engine().connect() as connection:
        assert (await connection.execute(select(UnicodeLower(literal("\u00c9COLE"))))).scalar_one() == "\u00e9cole"


async def test_whitespace_empty_category_array_matches_missing_filter(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result()])
    async with sqlite_instance._get_async_engine().begin() as connection:
        await connection.execute(text("UPDATE AttackResultEntries SET targeted_harm_categories = '[   ]'"))
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    filters = AttackAnalyticsFilters.model_validate(
        {"dimensions": [predicate(name="targeted_harm_category", values=[None])]}
    )
    report = await reader.report_async(
        query=AttackAnalyticsQuery(filters=filters, group_by=AttackAnalyticsDimension(name="targeted_harm_category")),
        control=control(),
    )
    assert report.counts == {"success": 1}
    assert report.groups[0].option.key.kind.value == "missing"


async def test_additional_converter_predicate_keeps_existing_any_constraint(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(converters=["Alpha", "Gamma"]),
            make_result(index=2, converters=["Gamma"]),
            make_result(index=3, converters=["Beta"]),
        ]
    )
    filters = AttackAnalyticsFilters.model_validate(
        {
            "dimensions": [
                predicate(name="converter_type", values=["Alpha", "Beta"]),
                predicate(name="converter_type", values=["Gamma"]),
            ]
        }
    )
    result = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(filters=filters), control=control()
    )
    assert result.counts == {"success": 1}
    assert [row.attack_result_id for row in result.results.items] == [str(uuid.UUID(int=1))]


async def test_response_converter_direction_and_all_matching(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(converters=["Alpha"], response_converters=["Beta", "Gamma"]),
            make_result(index=2, response_converters=["Beta"]),
        ]
    )
    filters = AttackAnalyticsFilters.model_validate(
        {
            "dimensions": [
                predicate(
                    name="converter_type",
                    values=["Beta", "Gamma"],
                    dimension_options={"converter_direction": "response"},
                    match_mode="all",
                )
            ]
        }
    )
    report = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(filters=filters), control=control()
    )
    assert report.counts == {"success": 1}
    assert report.results.items[0].response_converters == ["Beta", "Gamma"]
    assert report.results.items[0].request_converters == ["Alpha"]


async def test_missing_values_do_not_collide_with_literal_labels(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(operation=None), make_result(index=2, operation="Not recorded")]
    )
    report = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(), control=control()
    )
    assert {(group.option.key.kind.value, group.option.key.value) for group in report.groups} == {
        ("missing", None),
        ("value", "Not recorded"),
    }


async def test_known_empty_converters_differ_from_missing_identifier(sqlite_instance: SQLiteMemory) -> None:
    missing = make_result(index=2)
    missing.atomic_attack_identifier = None
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result(), missing])
    report = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type")), control=control()
    )
    assert {group.option.key.kind.value for group in report.groups} == {"missing", "no_converters"}


async def test_literal_label_paths_and_facet_search(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(labels={"a.b": "50%_done"}),
            make_result(index=2, labels={"a.b": "50xxdone"}),
            make_result(index=3, operation="operation-b", labels={"a.b": "different"}),
        ]
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    filters = AttackAnalyticsFilters.model_validate(
        {"dimensions": [predicate(name="label", values=["50%_done"], dimension_options={"label_key": "a.b"})]}
    )
    assert (await reader.report_async(query=AttackAnalyticsQuery(filters=filters), control=control())).counts == {
        "success": 1
    }
    facet = await reader.facets_async(
        query=AttackAnalyticsFacetQuery(
            filters=filters, dimension=AttackAnalyticsDimension(name="label", label_key="a.b"), search="%_"
        ),
        control=control(),
    )
    assert [item.key.value for item in facet.items] == ["50%_done"]


async def test_facet_excludes_its_own_filters_but_keeps_other_dimensions(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(operation="one", converters=["Alpha"]),
            make_result(index=2, operation="two", converters=["Alpha"]),
            make_result(index=3, operation="three", converters=["Beta"]),
        ]
    )
    filters = AttackAnalyticsFilters.model_validate(
        {
            "dimensions": [
                predicate(name="operation", values=["one"]),
                predicate(name="converter_type", values=["Alpha"]),
            ]
        }
    )
    values = await AttackAnalyticsReader(memory=sqlite_instance).facets_async(
        query=AttackAnalyticsFacetQuery(filters=filters, dimension=AttackAnalyticsDimension(name="operation")),
        control=control(),
    )
    assert [item.key.value for item in values.items] == ["one", "two"]


async def test_results_pagination_never_queries_aggregates_or_scores(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(index=index) for index in range(1, 6)]
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    statements: list[str] = []

    def record_statement(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        statements.append(statement)

    engine = sqlite_instance._get_async_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with patch.object(reader, "report_async", side_effect=AssertionError("Report must not run")):
            first = await reader.results_async(query=AttackAnalyticsResultsQuery(limit=2), control=control())
            second = await reader.results_async(
                query=AttackAnalyticsResultsQuery(limit=2, cursor=first.next_cursor), control=control()
            )
        assert len(first.items) == len(second.items) == 2
        assert not {item.attack_result_id for item in first.items} & {item.attack_result_id for item in second.items}
        assert all(
            "ScoreEntries" not in statement
            and "PromptMemoryEntries" not in statement
            and "ConversationEntries" not in statement
            for statement in statements
        )
        assert all("count(" not in statement.lower() for statement in statements)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)


async def test_changed_filters_reject_stale_result_cursor(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result(), make_result(index=2)])
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    page = await reader.results_async(query=AttackAnalyticsResultsQuery(limit=1), control=control())
    with pytest.raises(ValueError, match="stale"):
        await reader.results_async(
            query=AttackAnalyticsResultsQuery(
                cursor=page.next_cursor, filters=AttackAnalyticsFilters(outcomes=[AttackOutcome.FAILURE])
            ),
            control=control(),
        )


async def test_report_snapshots_mutable_filters_and_axes(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(index=1, operation="selected"),
            make_result(index=2, outcome=AttackOutcome.FAILURE, operation="excluded"),
        ]
    )
    query = AttackAnalyticsQuery(
        filters=AttackAnalyticsFilters(
            outcomes=[AttackOutcome.SUCCESS],
            dimensions=[
                AttackAnalyticsFilter(
                    dimension=AttackAnalyticsDimension(name="operation"),
                    values=[AttackAnalyticsValue(value="selected")],
                )
            ],
        )
    )
    mutated = False

    def change_request_after_totals(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        nonlocal mutated
        if not mutated and "count(" in statement.lower():
            mutated = True
            query.filters.outcomes[:] = [AttackOutcome.FAILURE]
            query.filters.dimensions[0].values[0].value = "excluded"
            query.group_by = AttackAnalyticsDimension(name="model")
            query.result_limit = 100

    engine = sqlite_instance._get_async_engine().sync_engine
    event.listen(engine, "after_cursor_execute", change_request_after_totals)
    try:
        report = await AttackAnalyticsReader(memory=sqlite_instance).report_async(query=query, control=control())
    finally:
        event.remove(engine, "after_cursor_execute", change_request_after_totals)
    assert mutated
    assert report.counts == {"success": 1}
    assert [(group.option.key.value, group.counts) for group in report.groups] == [("selected", {"success": 1})]
    assert [row.attack_result_id for row in report.results.items] == [str(uuid.UUID(int=1))]


async def test_results_request_snapshots_filters_and_page_limit_before_session_acquisition(
    sqlite_instance: SQLiteMemory,
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(index=1),
            make_result(index=2),
            make_result(index=3, outcome=AttackOutcome.FAILURE),
        ]
    )
    query = AttackAnalyticsResultsQuery(filters=AttackAnalyticsFilters(outcomes=[AttackOutcome.SUCCESS]), limit=1)
    get_session_async = sqlite_instance.get_session_async

    async def change_request_on_acquire_async() -> AsyncSession:
        query.filters.outcomes[:] = [AttackOutcome.FAILURE]
        query.limit = 2
        return await get_session_async()

    with patch.object(sqlite_instance, "get_session_async", side_effect=change_request_on_acquire_async):
        page = await AttackAnalyticsReader(memory=sqlite_instance).results_async(query=query, control=control())
    assert [row.attack_result_id for row in page.items] == [str(uuid.UUID(int=2))]
    assert page.has_more


async def test_facet_request_snapshots_filters_dimension_and_limit_before_session_acquisition(
    sqlite_instance: SQLiteMemory,
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(index=1, operation="a"),
            make_result(index=2, operation="b"),
            make_result(index=3, outcome=AttackOutcome.FAILURE, operation="z"),
        ]
    )
    query = AttackAnalyticsFacetQuery(
        filters=AttackAnalyticsFilters(outcomes=[AttackOutcome.SUCCESS]),
        dimension=AttackAnalyticsDimension(name="operation"),
        limit=1,
    )
    get_session_async = sqlite_instance.get_session_async

    async def change_request_on_acquire_async() -> AsyncSession:
        query.filters.outcomes[:] = [AttackOutcome.FAILURE]
        query.dimension = AttackAnalyticsDimension(name="model")
        query.limit = 2
        return await get_session_async()

    with patch.object(sqlite_instance, "get_session_async", side_effect=change_request_on_acquire_async):
        facet = await AttackAnalyticsReader(memory=sqlite_instance).facets_async(query=query, control=control())
    assert [item.key.value for item in facet.items] == ["a"]
    assert facet.has_more


async def test_reader_revalidates_mutated_requests_before_database_access(sqlite_instance: SQLiteMemory) -> None:
    query = AttackAnalyticsQuery()
    query.group_limit = 0
    with patch.object(sqlite_instance, "get_session_async", side_effect=AssertionError("Must not acquire")):
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            await AttackAnalyticsReader(memory=sqlite_instance).report_async(query=query, control=control())


async def test_expired_control_prevents_database_access(sqlite_instance: SQLiteMemory) -> None:
    with patch.object(sqlite_instance, "get_session_async", side_effect=AssertionError("Must not acquire")):
        with pytest.raises(AnalyticsTimeoutException):
            await AttackAnalyticsReader(memory=sqlite_instance).report_async(
                query=AttackAnalyticsQuery(), control=QueryControl(deadline=0)
            )


@pytest.mark.parametrize("trigger", ["deadline", "cancel"])
async def test_sqlite_execution_deadline_or_cancellation_cleans_up_the_connection(
    *, sqlite_instance: SQLiteMemory, trigger: str
) -> None:
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    query_control = QueryControl(deadline=time.monotonic() + 10)

    def stop_during_query() -> int:
        if trigger == "deadline":
            query_control.deadline = 0
        else:
            query_control.cancel()
        return 1

    async with await sqlite_instance.get_session_async() as session:
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        driver = raw.driver_connection
        assert isinstance(driver, SQLiteConnection)
        await driver.create_function("stop_during_query", 0, stop_during_query)
    heavy = text(
        "WITH RECURSIVE work(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM work WHERE x < 10000) "
        "SELECT 'success', SUM(stop_during_query()) FROM work"
    )
    try:
        with (
            patch.object(AttackAnalyticsQueryCompiler, "totals", return_value=heavy),
            patch.object(reader, "SQLITE_PROGRESS_STEPS", 1),
        ):
            with pytest.raises(AnalyticsTimeoutException):
                await reader.report_async(query=AttackAnalyticsQuery(), control=query_control)
    finally:
        await driver.create_function("stop_during_query", 0, lambda: 1)
    assert query_control.expired
    assert query_control.cancel_event.is_set() == (trigger == "cancel")
    assert (await reader.report_async(query=AttackAnalyticsQuery(), control=control())).counts == {}


@pytest.mark.parametrize("method", ["report", "results", "facets"])
async def test_sqlite_deadline_bounds_file_database_lock_wait_and_restores_timeout(
    *, tmp_path: Path, method: str
) -> None:
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=tmp_path / "analytics-lock.sqlite", skip_schema_migration=True)
    try:
        engine = memory._get_async_engine()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with engine.connect() as writer:
            default_timeout = (await writer.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()
            await writer.exec_driver_sql("BEGIN EXCLUSIVE")
            try:
                started = time.monotonic()
                reader = AttackAnalyticsReader(memory=memory)
                budget = QueryControl(deadline=started + 0.08)
                with pytest.raises(AnalyticsTimeoutException):
                    if method == "report":
                        await reader.report_async(query=AttackAnalyticsQuery(), control=budget)
                    elif method == "results":
                        await reader.results_async(query=AttackAnalyticsResultsQuery(), control=budget)
                    else:
                        await reader.facets_async(
                            query=AttackAnalyticsFacetQuery(dimension=AttackAnalyticsDimension(name="operation")),
                            control=budget,
                        )
                assert time.monotonic() - started < 1
                async with engine.connect() as reader_connection:
                    assert (
                        await reader_connection.exec_driver_sql("PRAGMA busy_timeout")
                    ).scalar_one() == default_timeout
            finally:
                await writer.rollback()
            assert (
                await AttackAnalyticsReader(memory=memory).report_async(query=AttackAnalyticsQuery(), control=control())
            ).counts == {}
    finally:
        await memory.dispose_engine_async()


async def test_odbc_timeout_is_applied_before_each_cursor_is_created() -> None:
    class RecordingConnection(sqlite3.Connection):
        timeout = 0
        cursor_timeouts: list[int] = []

        def cursor(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
            self.cursor_timeouts.append(self.timeout)
            cursor = super().cursor(*args, **kwargs)
            assert isinstance(cursor, sqlite3.Cursor)
            return cursor

    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"factory": RecordingConnection})
    memory = MagicMock(spec=MemoryInterface)
    memory.get_session_async = AsyncMock(side_effect=lambda: AsyncSession(bind=engine))
    try:
        async with engine.connect():
            pass
        budget = QueryControl(deadline=time.monotonic() + 30)
        with (
            patch.object(engine.dialect, "name", "mssql"),
            patch.object(SQLiteConnection, "timeout", property(lambda driver: driver._conn.timeout), create=True),
        ):
            async with AttackAnalyticsReader(memory=memory)._session_async(control=budget) as (
                session,
                dialect,
                _,
            ):
                assert dialect == "mssql"
                assert (await session.execute(text("SELECT 42"))).scalar_one() == 42
                budget.deadline = time.monotonic() + 1.5
                assert (await session.execute(text("SELECT 43"))).scalar_one() == 43
            async with engine.connect() as connection:
                raw = await connection.get_raw_connection()
                driver = raw.driver_connection
                assert isinstance(driver, SQLiteConnection)
                assert isinstance(driver._conn, RecordingConnection)
                assert driver._conn.cursor_timeouts[-2] >= 10
                assert 1 <= driver._conn.cursor_timeouts[-1] <= 2
                assert driver._conn.timeout == 0
    finally:
        await engine.dispose()


async def test_invalidated_sqlite_connection_preserves_the_original_error(sqlite_instance: SQLiteMemory) -> None:
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    with pytest.raises(OperationalError, match="lost connection"):
        async with reader._session_async(control=control()) as (session, _, _):
            await (await session.connection()).invalidate()
            raise OperationalError("SELECT", {}, RuntimeError("lost connection"))


async def test_database_error_is_not_returned_as_an_empty_report(sqlite_instance: SQLiteMemory) -> None:
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    with patch.object(
        AttackAnalyticsQueryCompiler,
        "totals",
        return_value=text("SELECT * FROM nonexistent_analytics_table"),
    ):
        with pytest.raises(OperationalError, match="no such table"):
            await reader.report_async(query=AttackAnalyticsQuery(), control=control())
    assert (await reader.report_async(query=AttackAnalyticsQuery(), control=control())).counts == {}


async def test_compacted_identifiers_retain_incomplete_projection_fallback(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result()])
    async with sqlite_instance._get_async_engine().begin() as connection:
        await connection.execute(update(AttackIdentifierEntry).values(class_name=None))
        await connection.execute(update(TargetIdentifierEntry).values(model_name=None, underlying_model_name=None))
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    for name, expected in (("attack_type", "probeattack"), ("model", "model-a")):
        report = await reader.report_async(
            query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name=name)), control=control()
        )
        assert report.groups[0].option.key.value == expected
        assert report.groups[0].counts == {"success": 1}


async def test_label_and_converter_matrix_preserves_only_the_selected_label(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(labels={"team": "one", "case": "unique-a"}, converters=["Alpha"]),
            make_result(index=2, labels={"team": "one", "case": "unique-b"}, converters=["Alpha"]),
            make_result(index=3, labels={"team": "two"}, converters=["Beta"]),
        ]
    )
    report = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(
            group_by=AttackAnalyticsDimension(name="label", label_key="team"),
            compare_by=AttackAnalyticsDimension(name="converter_type"),
        ),
        control=control(),
    )
    cells = {(cell.option.key.value, cell.column.key.value): cell.counts for cell in report.cells}
    assert cells == {("one", "alpha"): {"success": 2}, ("two", "beta"): {"success": 1}}


@pytest.mark.parametrize("dialect", [sqlite.dialect(), mssql.dialect()])
def test_queries_compile_with_backend_specific_json_and_no_score_joins(dialect: Dialect) -> None:
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
        compare_by=AttackAnalyticsDimension(name="converter_type"),
    )
    compiler = AttackAnalyticsQueryCompiler(dialect=dialect.name, filters=query.filters)
    sql = str(compiler.matrix(query).compile(dialect=dialect))
    assert "ScoreEntries" not in sql and "PromptMemoryEntries" not in sql
    assert ("json_each" if dialect.name == "sqlite" else "OUTER APPLY OPENJSON") in sql


def test_mssql_array_group_keys_do_not_contain_subqueries_or_bare_bit_predicates() -> None:
    query = AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="targeted_harm_category"))
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=query.filters)
    sql = str(
        compiler.groups(query).compile(
            dialect=mssql.dialect(deprecate_large_types=True), compile_kwargs={"literal_binds": True}
        )
    )
    assert "NOT EXISTS (SELECT 1 FROM OPENJSON" not in sql
    assert "END = 1" in sql
    assert "NOT LIKE '['" not in sql


def test_mssql_compaction_keeps_exact_label_keys_before_membership_grouping() -> None:
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="label", label_key="team"),
        compare_by=AttackAnalyticsDimension(name="converter_type"),
    )
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=query.filters)
    sql = str(
        compiler.matrix(query).compile(
            dialect=mssql.dialect(deprecate_large_types=True), compile_kwargs={"literal_binds": True}
        )
    )
    scalar = JsonScalar(compiler.root.c.labels, '$."team"').compile(
        dialect=mssql.dialect(), compile_kwargs={"literal_binds": True}
    )
    assert f"{scalar} COLLATE Latin1_General_100_BIN2 AS analytics_label_0" in sql
    assert re.search(
        r"CAST\(analytics_facts.analytics_label_0 AS NVARCHAR\(max\)\) COLLATE Latin1_General_100_BIN2 AS source0",
        sql,
    )


def test_mssql_converter_projection_retains_spellings_until_membership_folding() -> None:
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=AttackAnalyticsFilters())
    sql = str(
        compiler.results(limit=1).compile(
            dialect=mssql.dialect(deprecate_large_types=True), compile_kwargs={"literal_binds": True}
        )
    )
    for direction in ("request", "response"):
        alias = f"analytics_{direction}_converter"
        scalar = JsonScalar(literal_column(f"{alias}.identifier_json"), "$.class_name").compile(
            dialect=mssql.dialect(), compile_kwargs={"literal_binds": True}
        )
        assert (f"coalesce({alias}.class_name, {scalar}) COLLATE Latin1_General_100_BIN2 AS class_name") in sql


@pytest.mark.parametrize("dimension", ["operation", "targeted_harm_category"])
def test_mssql_facet_search_treats_brackets_as_literal_text(dimension: str) -> None:
    query = AttackAnalyticsFacetQuery(dimension=AttackAnalyticsDimension(name=dimension), search="[a-z]%_\\")
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=query.filters)
    compiled = compiler.facet(query).compile(dialect=mssql.dialect())
    assert "%\\[a-z]\\%\\_\\\\%" in compiled.params.values()


@pytest.mark.parametrize("dimension", ["operation", "operator", "scenario", "label", "model"])
def test_mssql_scalar_facets_group_projected_keys_with_positional_bindings(dimension: str) -> None:
    axis = AttackAnalyticsDimension(name=dimension, label_key="team" if dimension == "label" else None)
    query = AttackAnalyticsFacetQuery(dimension=axis)
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=query.filters)
    compiled = compiler.facet(query).compile(
        dialect=mssql.dialect(paramstyle="qmark", deprecate_large_types=True),
        compile_kwargs={"render_postcompile": True},
    )
    sql = str(compiled)
    assert "OUTER APPLY (SELECT" in sql
    assert "GROUP BY facet_values.kind0, facet_values.value0" in sql


@pytest.mark.parametrize(
    ("method", "names", "members"),
    [
        ("groups", ("targeted_harm_category",), {"group_members": (0,)}),
        ("groups", ("converter_type",), {"group_members": (0,)}),
        ("facet", ("targeted_harm_category",), {"facet_members": (0,)}),
        ("facet", ("converter_type",), {"facet_members": (0,)}),
        (
            "matrix",
            ("targeted_harm_category", "operation"),
            {"axis0_members": (0,), "cell_members": (0, 1)},
        ),
        (
            "matrix",
            ("operation", "targeted_harm_category"),
            {"axis1_members": (1,), "cell_members": (0, 1)},
        ),
        (
            "matrix",
            ("targeted_harm_category", "converter_type"),
            {"compact_cell_members": (0, 1)},
        ),
        (
            "matrix",
            ("converter_type", "operator"),
            {"axis0_members": (0,), "cell_members": (0, 1)},
        ),
    ],
)
def test_mssql_array_membership_keys_share_projected_columns_with_positional_bindings(
    *, method: str, names: tuple[str, ...], members: dict[str, tuple[int, ...]]
) -> None:
    dimensions = [AttackAnalyticsDimension(name=name) for name in names]
    query = (
        AttackAnalyticsFacetQuery(dimension=dimensions[0])
        if method == "facet"
        else AttackAnalyticsQuery(group_by=dimensions[0], compare_by=dimensions[1] if len(dimensions) == 2 else None)
    )
    statement = getattr(AttackAnalyticsQueryCompiler(dialect="mssql", filters=query.filters), method)(query)
    dialect = mssql.dialect(paramstyle="qmark", deprecate_large_types=True)
    compiled = statement.compile(dialect=dialect, compile_kwargs={"render_postcompile": True})
    assert compiled.positiontup

    checked: set[str] = set()
    for node in visitors.iterate(statement):
        if not isinstance(node, CTE) or node.name not in members or node.name in checked:
            continue
        checked.add(node.name)
        grouped = {str(key.compile(dialect=dialect)) for key in node.element._group_by_clauses}
        for index in members[node.name]:
            for field in ("kind", "value"):
                reference = f"{node.name}_values.{field}{index}"
                assert str(node.element.selected_columns[f"{field}{index}"].compile(dialect=dialect)) == reference
                assert reference in grouped
    assert checked == members.keys()


@pytest.mark.parametrize("filter_name", ["converter_type", "label"])
def test_mssql_accepted_filter_budget_stays_below_2100_positional_parameters(filter_name: str) -> None:
    filters = AttackAnalyticsFilters.model_validate(
        {
            "dimensions": [
                {
                    "dimension": {
                        "name": filter_name,
                        **(
                            {"label_key": f"team{index}"}
                            if filter_name == "label"
                            else {"converter_direction": "request" if index % 2 == 0 else "response"}
                        ),
                    },
                    "values": [{"value": f"Value_{index}_{number}"} for number in range(100)],
                    "match_mode": "all" if filter_name == "converter_type" and index == 0 else "any",
                }
                for index in range(5)
            ]
        }
    )
    assert sum(len(predicate.values) for predicate in filters.dimensions) == filters.MAX_VALUES
    query = AttackAnalyticsQuery(
        filters=filters,
        group_by=AttackAnalyticsDimension(name="converter_type"),
        compare_by=AttackAnalyticsDimension(name="model"),
    )
    facet = AttackAnalyticsFacetQuery(filters=filters, dimension=AttackAnalyticsDimension(name="operation"))
    harm = AttackAnalyticsDimension(name="targeted_harm_category")
    array_query = AttackAnalyticsQuery(
        filters=filters, group_by=harm, compare_by=AttackAnalyticsDimension(name="operation")
    )
    compact_array_query = AttackAnalyticsQuery(
        filters=filters, group_by=harm, compare_by=AttackAnalyticsDimension(name="converter_type")
    )
    array_facet = AttackAnalyticsFacetQuery(filters=filters, dimension=harm)
    statements = (
        lambda compiler: compiler.totals(),
        lambda compiler: compiler.groups(query),
        lambda compiler: compiler.matrix(query),
        lambda compiler: compiler.facet(facet),
        lambda compiler: compiler.results(limit=100),
        lambda compiler: compiler.groups(array_query),
        lambda compiler: compiler.matrix(array_query),
        lambda compiler: compiler.matrix(compact_array_query),
        lambda compiler: compiler.facet(array_facet),
    )
    dialect = mssql.dialect(paramstyle="qmark", deprecate_large_types=True)
    for make_statement in statements:
        compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=filters)
        rendered = make_statement(compiler).compile(dialect=dialect, compile_kwargs={"render_postcompile": True})
        assert len(rendered.positiontup or []) <= 2100


@pytest.mark.parametrize("absence_kinds", [("missing",), ("missing", "no_converters")])
def test_mssql_absence_filters_stay_below_2100_positional_parameters(absence_kinds: tuple[str, ...]) -> None:
    filters = AttackAnalyticsFilters.model_validate(
        {
            "updated_after": "2026-01-01T00:00:00Z",
            "updated_before": "2026-09-01T00:00:00Z",
            "dimensions": [
                {
                    "dimension": {
                        "name": "converter_type",
                        "converter_direction": "request" if index % 2 == 0 else "response",
                    },
                    "values": [
                        {"value": f"Value_{index}_{number}"}
                        for number in range(31 - len(absence_kinds) + (4 if index == 0 else 0))
                    ]
                    + [{"kind": kind} for kind in absence_kinds],
                    "match_mode": "all" if index % 2 == 0 else "any",
                }
                for index in range(16)
            ],
        }
    )
    assert sum(len(predicate.values) for predicate in filters.dimensions) == filters.MAX_VALUES
    query = AttackAnalyticsQuery(
        filters=filters,
        group_by=AttackAnalyticsDimension(name="converter_type"),
        compare_by=AttackAnalyticsDimension(name="converter_type", converter_direction="response"),
    )
    facet = AttackAnalyticsFacetQuery(filters=filters, dimension=AttackAnalyticsDimension(name="operation"))
    harm = AttackAnalyticsDimension(name="targeted_harm_category")
    array_query = AttackAnalyticsQuery(
        filters=filters, group_by=harm, compare_by=AttackAnalyticsDimension(name="operation")
    )
    compact_array_query = AttackAnalyticsQuery(
        filters=filters, group_by=harm, compare_by=AttackAnalyticsDimension(name="converter_type")
    )
    array_facet = AttackAnalyticsFacetQuery(filters=filters, dimension=harm)
    statements = (
        lambda compiler: compiler.totals(),
        lambda compiler: compiler.groups(query),
        lambda compiler: compiler.matrix(query),
        lambda compiler: compiler.facet(facet),
        lambda compiler: compiler.results(limit=100),
        lambda compiler: compiler.groups(array_query),
        lambda compiler: compiler.matrix(array_query),
        lambda compiler: compiler.matrix(compact_array_query),
        lambda compiler: compiler.facet(array_facet),
    )
    dialect = mssql.dialect(paramstyle="qmark", deprecate_large_types=True)
    for make_statement in statements:
        compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=filters)
        rendered = make_statement(compiler).compile(dialect=dialect, compile_kwargs={"render_postcompile": True})
        assert len(rendered.positiontup or []) <= 2100


@pytest.mark.parametrize("cap", ["MAX_COMPACT_PROFILES", "MAX_COMPACT_VALUE_LENGTH", "MAX_COMPACT_TOTAL_LENGTH"])
async def test_profile_caps_fall_back_without_returning_partial_counts(
    *, sqlite_instance: SQLiteMemory, cap: str
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(categories=["privacy"], converters=["Alpha"]),
            make_result(index=2, outcome=AttackOutcome.FAILURE, categories=["safety"], converters=["Beta"]),
        ]
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
        compare_by=AttackAnalyticsDimension(name="converter_type"),
    )
    expected = await reader.report_async(query=query, control=control())
    with patch.object(reader, cap, 1):
        actual = await reader.report_async(query=query, control=control(), use_compact_profiles=True)
    assert actual.profiles is None
    assert actual.counts == expected.counts == {"failure": 1, "success": 1}
    assert actual.cells == expected.cells
    assert actual.rows == expected.rows
    assert actual.columns == expected.columns


async def test_empty_profiles_are_success_not_a_fallback_signal(sqlite_instance: SQLiteMemory) -> None:
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    with patch.object(AttackAnalyticsQueryCompiler, "groups", side_effect=AssertionError("Must not regroup")):
        report = await reader.report_async(
            query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type")),
            control=control(),
            use_compact_profiles=True,
        )
    assert report.profiles == []
    assert report.counts == {}
    assert report.groups == []


async def test_compact_profiles_keep_typed_raw_sources_and_reject_invalid_values(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result(categories=["privacy"])])
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    report = await reader.report_async(
        query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="targeted_harm_category")),
        control=control(),
        use_compact_profiles=True,
    )
    assert report.profiles == [{"source0": '["privacy"]', "outcome": "success", "weight": 1, "oversized": False}]
    async with await sqlite_instance.get_session_async() as session:
        invalid = (
            (
                await session.execute(
                    select(
                        literal(123).label("source0"),
                        literal("success").label("outcome"),
                        literal(1).label("weight"),
                        literal(False).label("oversized"),
                    )
                )
            )
            .mappings()
            .one()
        )
    with pytest.raises(AnalyticsDataException, match="not a string"):
        reader._raw_profile(invalid)


@pytest.mark.parametrize(
    ("raw", "is_array", "is_empty"),
    [
        (None, False, False),
        ("null", False, False),
        ("{}", False, False),
        ('"[]"', False, False),
        ("true", False, False),
        ("42", False, False),
        (" \t[\r\n ] ", True, True),
        ("[null]", True, False),
        ('[" \\t[] "]', True, False),
    ],
)
def test_json_array_checks_distinguish_shape_from_missing_members(
    *, sqlite_instance: SQLiteMemory, raw: str | None, is_array: bool, is_empty: bool
) -> None:
    with sqlite_instance.engine.connect() as connection:
        result = connection.execute(select(JsonIsArray(literal(raw)), JsonArrayEmpty(literal(raw)))).one()
    assert tuple(result) == (is_array, is_empty)


@pytest.mark.parametrize("raw", [1, '["Alpha", 1]', '{"class_name": "Alpha"}', "not json"])
def test_result_converter_projection_rejects_invalid_metadata(raw: Any) -> None:
    with pytest.raises(AnalyticsDataException):
        AttackAnalyticsReader._converter_names(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["Alpha", {"__type__": "LegacyConverter"}]', ["Alpha", "LegacyConverter"]),
        ('[{"__type__": "Legacy", "class_name": "Canonical"}]', ["Canonical"]),
    ],
)
def test_result_converter_projection_accepts_supported_legacy_names(raw: str, expected: list[str]) -> None:
    assert AttackAnalyticsReader._converter_names(raw) == expected


def test_query_cancellation_is_request_local() -> None:
    cancelled = control()
    active = control()
    cancelled.cancel()
    assert cancelled.remaining > 0
    with pytest.raises(AnalyticsTimeoutException):
        cancelled.check()
    active.check()
    assert not active.expired


async def test_timestamp_filters_use_half_open_utc_bounds(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(index=index) for index in range(1, 4)]
    )
    filters = AttackAnalyticsFilters(
        updated_after=datetime(2026, 1, 1, 8, 0, 2, tzinfo=timezone(timedelta(hours=8))),
        updated_before=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
    )
    report = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(filters=filters), control=control()
    )
    assert report.counts == {"success": 1}
    assert [row.attack_result_id for row in report.results.items] == [str(uuid.UUID(int=2))]


async def test_facet_self_exclusion_keeps_other_label_keys(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(labels={"team": "one", "run": "a"}),
            make_result(index=2, labels={"team": "two", "run": "a"}),
            make_result(index=3, labels={"team": "three", "run": "b"}),
        ]
    )
    filters = AttackAnalyticsFilters.model_validate(
        {
            "dimensions": [
                predicate(name="label", values=["one"], dimension_options={"label_key": "team"}),
                predicate(name="label", values=["a"], dimension_options={"label_key": "run"}),
            ]
        }
    )
    facet = await AttackAnalyticsReader(memory=sqlite_instance).facets_async(
        query=AttackAnalyticsFacetQuery(
            filters=filters, dimension=AttackAnalyticsDimension(name="label", label_key="team")
        ),
        control=control(),
    )
    assert [option.key.value for option in facet.items] == ["one", "two"]


async def test_literal_brackets_in_facet_search_match_only_literal_text(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(categories=["[a-z]%_\\"]),
            make_result(index=2, categories=["alphabet"]),
        ]
    )
    facet = await AttackAnalyticsReader(memory=sqlite_instance).facets_async(
        query=AttackAnalyticsFacetQuery(
            dimension=AttackAnalyticsDimension(name="targeted_harm_category"), search="[a-z]%_\\"
        ),
        control=control(),
    )
    assert [option.key.value for option in facet.items] == ["[a-z]%_\\"]


@pytest.mark.parametrize("length", [4000, 4001, 4096])
def test_mssql_json_scalars_extract_full_width_metadata(length: int) -> None:
    document = json.dumps({"a.b": "\u9577" * length}, ensure_ascii=False)
    statement = select(JsonScalar(literal(document, UnicodeText()), '$."a.b"'))
    compiled = statement.compile(
        dialect=mssql.dialect(deprecate_large_types=True), compile_kwargs={"render_postcompile": True}
    )
    sql = str(compiled)
    assert "JSON_VALUE(" not in sql
    assert "OPENJSON(" in sql
    assert "JSON_QUERY(" in sql
    assert """WITH ([value] nvarchar(max) '$."a.b"')""" in sql
    assert "CAST(N'[' AS nvarchar(max))" in sql
    assert document in compiled.params.values()


def test_mssql_scalar_paths_are_safely_literalized_at_execution() -> None:
    path = '$."team\'s.label"'
    statement = select(JsonScalar(column("document"), path))
    compiled = statement.compile(dialect=mssql.dialect())
    assert [parameter.value for parameter in compiled.literal_execute_params] == [path]
    assert path in compiled.params.values()
    rendered = statement.compile(dialect=mssql.dialect(), compile_kwargs={"render_postcompile": True})
    assert """WITH ([value] nvarchar(max) '$."team''s.label"')""" in str(rendered)


def test_mssql_unicode_lower_uses_native_function() -> None:
    sql = str(select(UnicodeLower(literal("\u00c9COLE"))).compile(dialect=mssql.dialect()))
    assert "LOWER(" in sql
    assert UnicodeLower.SQLITE_FUNCTION_NAME not in sql


def test_mssql_scalar_projection_rejects_nonliteral_column_paths() -> None:
    with pytest.raises(CompileError, match="bound string"):
        select(JsonScalar(column("document"), column("dynamic_path"))).compile(dialect=mssql.dialect())


def _assert_mssql_aggregation_inputs_projected(statement: ClauseElement) -> None:
    dialect = mssql.dialect(deprecate_large_types=True)
    checked: set[int] = set()
    for node in visitors.iterate(statement):
        if not isinstance(node, Select) or id(node) in checked:
            continue
        checked.add(id(node))
        for expression in node._group_by_clauses:
            assert not any(isinstance(child, JsonScalar) for child in visitors.iterate(expression))
            assert not re.search(r"\bSELECT\b", str(expression.compile(dialect=dialect)), re.IGNORECASE)
        for expression in node.selected_columns:
            for child in visitors.iterate(expression):
                if isinstance(child, Function) and child.name in {"min", "max", "sum", "count"}:
                    assert not any(isinstance(argument, JsonScalar) for argument in visitors.iterate(child))
                    assert not re.search(r"\bSELECT\b", str(child.compile(dialect=dialect)), re.IGNORECASE)


@pytest.mark.parametrize(
    ("method", "options"),
    [
        ("groups", {"group_by": {"name": "label", "label_key": "a.b"}}),
        ("groups", {"group_by": {"name": "attack_type"}}),
        ("groups", {"group_by": {"name": "model"}}),
        ("groups", {"group_by": {"name": "converter_type"}}),
        ("facet", {"dimension": {"name": "label", "label_key": "a.b"}}),
        ("facet", {"dimension": {"name": "model"}}),
        ("facet", {"dimension": {"name": "converter_type"}}),
        ("matrix", {"group_by": {"name": "operation"}, "compare_by": {"name": "model"}}),
        (
            "matrix",
            {"group_by": {"name": "operation"}, "compare_by": {"name": "label", "label_key": "team"}},
        ),
        (
            "matrix",
            {"group_by": {"name": "label", "label_key": "team"}, "compare_by": {"name": "model"}},
        ),
        (
            "matrix",
            {"group_by": {"name": "label", "label_key": "team"}, "compare_by": {"name": "converter_type"}},
        ),
        (
            "matrix",
            {"group_by": {"name": "targeted_harm_category"}, "compare_by": {"name": "converter_type"}},
        ),
    ],
)
def test_mssql_wide_scalars_are_projected_before_grouping_or_aggregation(
    *, method: str, options: dict[str, Any]
) -> None:
    query = (
        AttackAnalyticsFacetQuery.model_validate(options)
        if method == "facet"
        else AttackAnalyticsQuery.model_validate(options)
    )
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=query.filters)
    statement = getattr(compiler, method)(query)
    sql = str(statement.compile(dialect=mssql.dialect(deprecate_large_types=True)))
    assert "JSON_VALUE(" not in sql
    assert "OUTER APPLY (SELECT" in sql
    assert "LATERAL" not in sql
    _assert_mssql_aggregation_inputs_projected(statement)


@pytest.mark.parametrize("method", ["groups", "facet"])
def test_mssql_operation_grouping_does_not_inline_wide_filter_subqueries(method: str) -> None:
    dimension = AttackAnalyticsDimension(name="operation")
    filters = AttackAnalyticsFilters.model_validate(
        {"dimensions": [predicate(name="label", values=["\u9577" * 4096], dimension_options={"label_key": "team"})]}
    )
    query = (
        AttackAnalyticsFacetQuery(dimension=dimension, filters=filters)
        if method == "facet"
        else AttackAnalyticsQuery(group_by=dimension, filters=filters)
    )
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=filters)
    statement = getattr(compiler, method)(query)
    sql = str(statement.compile(dialect=mssql.dialect(deprecate_large_types=True)))
    assert "OPENJSON(" in sql
    if method == "facet":
        assert "OUTER APPLY (SELECT" in sql
        assert (
            "OPENJSON(" not in sql.split("OUTER APPLY (SELECT", maxsplit=1)[1].split(") AS facet_values", maxsplit=1)[0]
        )
    else:
        assert "OUTER APPLY (SELECT" not in sql
    _assert_mssql_aggregation_inputs_projected(statement)


@pytest.mark.parametrize("name", ["operation", "model", "label", "converter_type", "targeted_harm_category"])
def test_mssql_distinct_facet_profiles_project_scalar_subqueries_first(name: str) -> None:
    dimension = AttackAnalyticsDimension(name=name, label_key="team" if name == "label" else None)
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=AttackAnalyticsFilters())
    profiles = compiler._profiles([compiler._source(dimension)], counts=False)
    assert profiles.element._distinct
    for expression in profiles.element.selected_columns:
        assert not any(isinstance(child, JsonScalar) for child in visitors.iterate(expression))
        assert not re.search(
            r"\bSELECT\b",
            str(expression.compile(dialect=mssql.dialect(deprecate_large_types=True))),
            re.IGNORECASE,
        )
    statement = select(profiles)
    sql = str(statement.compile(dialect=mssql.dialect(deprecate_large_types=True)))
    assert "count(" not in sql.lower()
    if name in {"model", "label"}:
        assert "OUTER APPLY (SELECT" in sql
    _assert_mssql_aggregation_inputs_projected(statement)


def test_mssql_wide_scalar_projection_remains_correlated_to_one_result_source() -> None:
    query = AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="label", label_key="team"))
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=query.filters)
    sql = str(compiler.groups(query).compile(dialect=mssql.dialect(deprecate_large_types=True)))
    assert sql.count("FROM [AttackResultEntries] AS analytics_results") == 1
    assert "OUTER APPLY (SELECT" in sql


def test_mssql_indexed_hash_keeps_its_bounded_computed_expression() -> None:
    sql = str(ResolvedAttackIdentifierHash(column("reference"), column("document")).compile(dialect=mssql.dialect()))
    assert "CONVERT(varchar(64)" in sql
    assert "JSON_VALUE(" in sql
    assert "OPENJSON(" not in sql


@pytest.mark.parametrize("length", [4001, 4096])
async def test_sqlite_wide_labels_keep_filter_group_and_facet_semantics(
    *, sqlite_instance: SQLiteMemory, length: int
) -> None:
    value = "\u9577" * length
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(labels={"a.b": value}), make_result(index=2, labels={"a.b": "short"})]
    )
    dimension = AttackAnalyticsDimension(name="label", label_key="a.b")
    filters = AttackAnalyticsFilters.model_validate(
        {"dimensions": [predicate(name="label", values=[value], dimension_options={"label_key": "a.b"})]}
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    report = await reader.report_async(
        query=AttackAnalyticsQuery(filters=filters, group_by=dimension), control=control()
    )
    assert report.counts == {"success": 1}
    assert report.groups[0].option.key.value == value
    assert report.results.items[0].labels["a.b"] == value
    facet = await reader.facets_async(
        query=AttackAnalyticsFacetQuery(filters=filters, dimension=dimension), control=control()
    )
    assert {option.key.value for option in facet.items} == {value, "short"}


async def test_oversized_saved_label_is_explicitly_outside_group_and_facet_contract(
    sqlite_instance: SQLiteMemory,
) -> None:
    long_label = "x" * (AttackAnalyticsValue.MAX_VALUE_LENGTH + 1)
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result(labels={"team": long_label})])
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    other_axis = await reader.report_async(query=AttackAnalyticsQuery(), control=control())
    assert other_axis.counts == {"success": 1}
    assert other_axis.results.items[0].labels["team"] == long_label

    axis = AttackAnalyticsDimension(name="label", label_key="team")
    with pytest.raises(AnalyticsDataException, match="4,096-character analytics key limit"):
        await reader.report_async(query=AttackAnalyticsQuery(group_by=axis), control=control())
    with pytest.raises(AnalyticsDataException, match="4,096-character analytics key limit"):
        await reader.facets_async(query=AttackAnalyticsFacetQuery(dimension=axis), control=control())


async def test_canonical_and_legacy_identifiers_preserve_the_complete_cohort(sqlite_instance: SQLiteMemory) -> None:
    canonical = make_result(converters=["Alpha"])
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[canonical])
    legacy_attack = {
        "class_name": "LegacyAttack",
        "children": {
            "objective_target": {
                "hash": "t" * 64,
                "class_name": "LegacyTarget",
                "params": {"model_name": "LegacyModel"},
            },
            "request_converters": [{"class_name": "LegacyConverter"}, {"class_name": "LegacyConverter"}],
            "response_converters": [],
        },
    }
    async with sqlite_instance._get_async_engine().begin() as connection:
        canonical_document = (await connection.execute(select(AttackResultEntry.atomic_attack_identifier))).scalar_one()
        documents = [
            {"children": {"attack": legacy_attack}},
            {"hash": "x" * 64, "children": {"attack_technique": {"children": {"attack": legacy_attack}}}},
            canonical_document,
        ]
        for index, document, outcome in zip(
            range(2, 5),
            documents,
            [AttackOutcome.FAILURE, AttackOutcome.ERROR, AttackOutcome.UNDETERMINED],
            strict=True,
        ):
            await connection.execute(
                AttackResultEntry.__table__.insert().values(
                    id=uuid.UUID(int=index),
                    conversation_id=canonical.conversation_id,
                    objective="Legacy metadata",
                    outcome=outcome.value,
                    timestamp=canonical.timestamp + timedelta(seconds=index),
                    atomic_attack_identifier=document,
                )
            )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    report = await reader.report_async(
        query=AttackAnalyticsQuery(
            group_by=AttackAnalyticsDimension(name="attack_type"),
            compare_by=AttackAnalyticsDimension(name="converter_type"),
        ),
        control=control(),
    )
    assert report.counts == {outcome.value: 1 for outcome in AttackOutcome}
    assert {row.attack_result_id for row in report.results.items} == {
        str(uuid.UUID(int=index)) for index in range(1, 5)
    }
    assert {(cell.option.key.value, cell.column.key.value): cell.counts for cell in report.cells} == {
        ("probeattack", "alpha"): {"success": 1, "undetermined": 1},
        ("legacyattack", "legacyconverter"): {"failure": 1, "error": 1},
    }
    model_report = await reader.report_async(
        query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="model")), control=control()
    )
    assert {group.option.key.value: group.counts for group in model_report.groups} == {
        "model-a": {"success": 1, "undetermined": 1},
        "LegacyModel": {"failure": 1, "error": 1},
    }
    for cell in report.cells:
        assert cell.column is not None
        filters = AttackAnalyticsFilters(
            dimensions=[
                AttackAnalyticsFilter(dimension=AttackAnalyticsDimension(name="attack_type"), values=[cell.option.key]),
                AttackAnalyticsFilter(
                    dimension=AttackAnalyticsDimension(name="converter_type"), values=[cell.column.key]
                ),
            ]
        )
        assert (
            await reader.report_async(query=AttackAnalyticsQuery(filters=filters), control=control())
        ).counts == cell.counts


async def test_computed_identifier_prefers_canonical_reference(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result()])
    async with sqlite_instance._get_async_engine().begin() as connection:
        canonical_hash = (
            await connection.execute(select(AttackResultEntry.atomic_attack_identifier_hash))
        ).scalar_one()
        await connection.execute(
            update(AttackResultEntry).values(atomic_attack_identifier={"hash": "other", "children": {}})
        )
        assert (
            await connection.execute(select(AttackResultEntry.resolved_atomic_attack_identifier_hash))
        ).scalar_one() == canonical_hash
    report = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="attack_type")), control=control()
    )
    assert report.groups[0].option.key.value == "probeattack"


async def test_blank_and_missing_label_keys_have_exact_membership(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(),
            make_result(index=2, labels={"team": ""}),
            make_result(index=3, labels={"team": "missing"}),
        ]
    )
    dimension = AttackAnalyticsDimension(name="label", label_key="team")
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    report = await reader.report_async(query=AttackAnalyticsQuery(group_by=dimension), control=control())
    assert {(group.option.key.kind.value, group.option.key.value) for group in report.groups} == {
        ("missing", None),
        ("value", ""),
        ("value", "missing"),
    }
    for group in report.groups:
        filters = AttackAnalyticsFilters(
            dimensions=[AttackAnalyticsFilter(dimension=dimension, values=[group.option.key])]
        )
        assert (await reader.report_async(query=AttackAnalyticsQuery(filters=filters), control=control())).counts == {
            "success": 1
        }


@pytest.mark.parametrize("cap", ["MAX_COMPACT_PROFILES", "MAX_COMPACT_VALUE_LENGTH", "MAX_COMPACT_TOTAL_LENGTH"])
async def test_complete_raw_profiles_at_each_cap_and_full_sql_fallback_beyond_it(
    *, sqlite_instance: SQLiteMemory, cap: str
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(categories=["privacy"], converters=["Alpha"]),
            make_result(index=2, categories=["privacy"], converters=["Alpha"]),
            make_result(index=3, outcome=AttackOutcome.FAILURE, categories=["safety"], converters=["Beta"]),
        ]
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
        compare_by=AttackAnalyticsDimension(name="converter_type"),
    )
    expected = await reader.report_async(query=query, control=control())
    raw = await reader.report_async(query=query, control=control(), use_compact_profiles=True)
    assert raw.profiles is not None
    assert {(profile["outcome"], profile["weight"]) for profile in raw.profiles} == {("success", 2), ("failure", 1)}
    assert raw.cells == raw.groups == []
    assert all(isinstance(profile["source0"], str) and isinstance(profile["source1"], str) for profile in raw.profiles)
    limits = {
        "MAX_COMPACT_PROFILES": len(raw.profiles),
        "MAX_COMPACT_VALUE_LENGTH": max(
            len(value)
            for profile in raw.profiles
            for key, value in profile.items()
            if key.startswith(("source", "display")) and isinstance(value, str)
        ),
        "MAX_COMPACT_TOTAL_LENGTH": sum(
            len(value) for profile in raw.profiles for value in profile.values() if isinstance(value, str)
        ),
    }
    with patch.object(reader, cap, limits[cap]):
        accepted = await reader.report_async(query=query, control=control(), use_compact_profiles=True)
    assert accepted.profiles == raw.profiles
    with patch.object(reader, cap, limits[cap] - 1):
        fallback = await reader.report_async(query=query, control=control(), use_compact_profiles=True)
    assert fallback.profiles is None
    assert fallback.counts == expected.counts == {"success": 2, "failure": 1}
    assert fallback.cells == expected.cells


async def test_report_is_consistent_and_later_result_pages_are_fresh(*, tmp_path: Path) -> None:
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=tmp_path / "analytics-snapshot.sqlite", skip_schema_migration=True)
    try:
        engine = memory._get_async_engine()
        async with engine.connect() as connection:
            assert (await connection.exec_driver_sql("PRAGMA journal_mode=WAL")).scalar_one() == "wal"
            await connection.run_sync(Base.metadata.create_all)
        await memory.add_attack_results_to_memory_async(attack_results=[make_result(), make_result(index=2)])
        inserted = False

        async def insert_after_totals_async() -> None:
            async with engine.begin() as writer:
                await writer.execute(
                    AttackResultEntry.__table__.insert().values(
                        id=uuid.UUID(int=0),
                        conversation_id="shared-main-conversation",
                        objective="Concurrent saved result",
                        outcome="failure",
                        operation="operation-a",
                        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                )

        def insert_after_totals(
            conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
        ) -> None:
            nonlocal inserted
            if inserted or "count(" not in statement.lower():
                return
            inserted = True
            conn.connection.run_async(lambda _: insert_after_totals_async())

        event.listen(engine.sync_engine, "after_cursor_execute", insert_after_totals)
        try:
            reader = AttackAnalyticsReader(memory=memory)
            report = await reader.report_async(query=AttackAnalyticsQuery(result_limit=1), control=control())
        finally:
            event.remove(engine.sync_engine, "after_cursor_execute", insert_after_totals)
        assert inserted
        assert report.counts == report.groups[0].counts == {"success": 2}
        assert [row.attack_result_id for row in report.results.items] == [str(uuid.UUID(int=2))]
        assert report.results.has_more
        page = await reader.results_async(
            query=AttackAnalyticsResultsQuery(cursor=report.results.next_cursor), control=control()
        )
        assert [row.attack_result_id for row in page.items] == [str(uuid.UUID(int=1)), str(uuid.UUID(int=0))]
        assert not page.has_more
    finally:
        await memory.dispose_engine_async()


@pytest.mark.parametrize("read_report", [False, True])
async def test_sqlite_shared_connection_acquisition_timeout_releases_waiting_session(
    *, sqlite_instance: SQLiteMemory, read_report: bool
) -> None:
    async with await sqlite_instance.get_session_async():
        if read_report:
            with pytest.raises(AnalyticsTimeoutException):
                await AttackAnalyticsReader(memory=sqlite_instance).report_async(
                    query=AttackAnalyticsQuery(),
                    control=QueryControl(deadline=time.monotonic() + 0.05),
                )
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(sqlite_instance.get_session_async(), timeout=0.05)
        assert sqlite_instance._transaction_lock.locked()
    assert not sqlite_instance._transaction_lock.locked()
    assert (
        await AttackAnalyticsReader(memory=sqlite_instance).report_async(
            query=AttackAnalyticsQuery(), control=control()
        )
    ).counts == {}


async def test_query_bounds_keep_daylight_saving_fold_instants(sqlite_instance: SQLiteMemory) -> None:
    zone = ZoneInfo("America/New_York")
    results = [make_result(index=index) for index in range(1, 5)]
    for result, (hour, minute) in zip(results, [(5, 44), (5, 45), (6, 0), (6, 15)], strict=True):
        result.timestamp = datetime(2026, 11, 1, hour, minute, tzinfo=UTC)
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    filters = AttackAnalyticsFilters(
        updated_after=datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0),
        updated_before=datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1),
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    report = await reader.report_async(query=AttackAnalyticsQuery(filters=filters, result_limit=1), control=control())
    assert report.counts == {"success": 2}
    assert report.results.items[0].attack_result_id == results[2].attack_result_id
    equivalent = AttackAnalyticsFilters.model_validate_json(filters.model_dump_json())
    page = await reader.results_async(
        query=AttackAnalyticsResultsQuery(filters=equivalent, cursor=report.results.next_cursor), control=control()
    )
    assert [row.attack_result_id for row in page.items] == [results[1].attack_result_id]
    assert not page.has_more


@pytest.mark.parametrize("name", ["attack_type", "converter_type", "objective_target", "model"])
def test_mssql_scenario_compaction_does_not_collate_native_uuid(name: str) -> None:
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="scenario"), compare_by=AttackAnalyticsDimension(name=name)
    )
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=query.filters)
    statement = compiler.matrix(query)
    sql = str(statement.compile(dialect=mssql.dialect(deprecate_large_types=True)))
    assert "attribution_parent_id COLLATE" not in sql
    _assert_mssql_aggregation_inputs_projected(statement)


def test_mssql_compaction_keeps_ungrouped_null_as_a_constant() -> None:
    dimensions = [
        AttackAnalyticsDimension(name="label", label_key="team"),
        AttackAnalyticsDimension(name="converter_type"),
    ]
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=AttackAnalyticsFilters())
    facts = compiler._group_compiler(dimensions).root
    modern = facts.element.selects[0]
    projection = modern.selected_columns.atomic_attack_identifier
    sql = str(projection.compile(dialect=mssql.dialect(deprecate_large_types=True)))
    assert "CAST(NULL AS NVARCHAR(max))" in sql
    assert "modern_fact_values.atomic_attack_identifier" not in sql


async def test_group_matrix_and_facet_limits_keep_complete_counts(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(index=index + 1, operation=f"op-{index % 3}", converters=[f"Converter{index % 2}"])
            for index in range(6)
        ]
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    dimension = AttackAnalyticsDimension(name="operation")
    query = AttackAnalyticsQuery(group_by=dimension, group_limit=1, result_limit=1)
    first = await reader.report_async(query=query, control=control())
    assert first.counts == {"success": 6}
    assert len(first.groups) == 1
    assert first.groups[0].counts == {"success": 2}
    assert first.has_more_groups
    assert len(first.results.items) == 1
    assert first.results.has_more
    last = await reader.report_async(query=query.model_copy(update={"group_offset": 2}), control=control())
    assert last.counts == first.counts
    assert last.groups[0].option.key.value == "op-2"
    assert not last.has_more_groups
    matrix = await reader.report_async(
        query=AttackAnalyticsQuery(
            group_by=dimension,
            compare_by=AttackAnalyticsDimension(name="converter_type"),
            axis_limit=1,
        ),
        control=control(),
    )
    assert matrix.counts == first.counts
    assert len(matrix.rows) == len(matrix.columns) == len(matrix.cells) == 1
    assert matrix.axes_truncated
    assert matrix.cells[0].counts == {"success": 1}
    facet = await reader.facets_async(query=AttackAnalyticsFacetQuery(dimension=dimension, limit=2), control=control())
    assert [item.key.value for item in facet.items] == ["op-0", "op-1"]
    assert facet.has_more
    last_facet = await reader.facets_async(
        query=AttackAnalyticsFacetQuery(dimension=dimension, limit=2, offset=2), control=control()
    )
    assert [item.key.value for item in last_facet.items] == ["op-2"]
    assert not last_facet.has_more


async def test_response_converter_facet_keeps_request_converter_filter(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(converters=["Alpha"], response_converters=["Beta"]),
            make_result(index=2, converters=["Alpha"], response_converters=["Gamma"]),
            make_result(index=3, converters=["Other"], response_converters=["Delta"]),
        ]
    )
    filters = AttackAnalyticsFilters.model_validate(
        {
            "dimensions": [
                predicate(name="converter_type", values=["Alpha"]),
                predicate(
                    name="converter_type",
                    values=["Beta"],
                    dimension_options={"converter_direction": "response"},
                ),
            ]
        }
    )
    facet = await AttackAnalyticsReader(memory=sqlite_instance).facets_async(
        query=AttackAnalyticsFacetQuery(
            filters=filters,
            dimension=AttackAnalyticsDimension(name="converter_type", converter_direction="response"),
        ),
        control=control(),
    )
    assert {item.key.value for item in facet.items} == {"beta", "gamma"}


async def test_sqlite_group_query_uses_the_computed_identifier_index(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result(converters=["Alpha"])])
    query = AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type"))
    statement = AttackAnalyticsQueryCompiler(dialect="sqlite", filters=query.filters).groups(query)
    sql = str(statement.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    async with sqlite_instance._get_async_engine().connect() as connection:
        plan = (await connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {sql}")).all()
    assert any("ix_AttackResultEntries_analytics_facts_sqlite" in row[-1] for row in plan)
