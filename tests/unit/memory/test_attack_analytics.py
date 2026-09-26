# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import UnicodeText, column, create_engine, event, literal, literal_column, select, text, update
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.exc import CompileError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select, visitors
from sqlalchemy.sql.functions import Function

from pyrit.exceptions.analytics_exception import AnalyticsDataException, AnalyticsTimeoutException
from pyrit.memory import SQLiteMemory
from pyrit.memory.analytics_sql import JsonArrayEmpty, JsonIsArray, JsonScalar, ResolvedAttackIdentifierHash
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
                class_name="ProbeAttack",
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


def control() -> QueryControl:
    return QueryControl(deadline=time.monotonic() + 10)


def predicate(*, name: str, values: list[str | None], **options: Any) -> dict[str, Any]:
    return {
        "dimension": {"name": name, **options.pop("dimension_options", {})},
        "values": [{"value": value} if value is not None else {"kind": "missing"} for value in values],
        **options,
    }


def test_report_counts_result_ids_not_conversations(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[make_result(), make_result(index=2, outcome=AttackOutcome.FAILURE)]
    )
    result = AttackAnalyticsReader(memory=sqlite_instance).report(query=AttackAnalyticsQuery(), control=control())
    assert result.counts == {"failure": 1, "success": 1}
    assert len(result.results.items) == 2
    assert result.groups[0].counts == result.counts
    assert result.results.items[0].target_model == "model-a"
    assert result.results.items[0].attack_type == "ProbeAttack"


def test_multivalued_groups_and_cells_do_not_multiply_results(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            make_result(categories=["privacy", "privacy", "safety"], converters=["Alpha", "Alpha", "Beta"]),
            make_result(index=2, outcome=AttackOutcome.FAILURE, categories=["privacy"], converters=["Alpha"]),
        ]
    )
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
        compare_by=AttackAnalyticsDimension(name="converter_type"),
    )
    result = AttackAnalyticsReader(memory=sqlite_instance).report(query=query, control=control())
    cells = {(cell.option.key.value, cell.column.key.value): cell.counts for cell in result.cells}
    assert result.counts == {"failure": 1, "success": 1}
    assert cells[("privacy", "alpha")] == {"failure": 1, "success": 1}
    assert cells[("privacy", "beta")] == {"success": 1}
    assert cells[("safety", "alpha")] == {"success": 1}
    assert cells[("safety", "beta")] == {"success": 1}


def test_case_variants_within_one_result_have_one_membership(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[make_result(categories=["Privacy", "privacy"], converters=["Alpha", "ALPHA"])]
    )
    report = AttackAnalyticsReader(memory=sqlite_instance).report(
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


def test_whitespace_empty_category_array_matches_missing_filter(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[make_result()])
    with sqlite_instance.engine.begin() as connection:
        connection.execute(text("UPDATE AttackResultEntries SET targeted_harm_categories = '[   ]'"))
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    filters = AttackAnalyticsFilters.model_validate(
        {"dimensions": [predicate(name="targeted_harm_category", values=[None])]}
    )
    report = reader.report(
        query=AttackAnalyticsQuery(filters=filters, group_by=AttackAnalyticsDimension(name="targeted_harm_category")),
        control=control(),
    )
    assert report.counts == {"success": 1}
    assert report.groups[0].option.key.kind.value == "missing"


def test_additional_converter_predicate_keeps_existing_any_constraint(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
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
    result = AttackAnalyticsReader(memory=sqlite_instance).report(
        query=AttackAnalyticsQuery(filters=filters), control=control()
    )
    assert result.counts == {"success": 1}
    assert [row.attack_result_id for row in result.results.items] == [str(uuid.UUID(int=1))]


def test_response_converter_direction_and_all_matching(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
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
    report = AttackAnalyticsReader(memory=sqlite_instance).report(
        query=AttackAnalyticsQuery(filters=filters), control=control()
    )
    assert report.counts == {"success": 1}
    assert report.results.items[0].response_converters == ["Beta", "Gamma"]
    assert report.results.items[0].request_converters == ["Alpha"]


def test_missing_values_do_not_collide_with_literal_labels(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[make_result(operation=None), make_result(index=2, operation="Not recorded")]
    )
    report = AttackAnalyticsReader(memory=sqlite_instance).report(query=AttackAnalyticsQuery(), control=control())
    assert {(group.option.key.kind.value, group.option.key.value) for group in report.groups} == {
        ("missing", None),
        ("value", "Not recorded"),
    }


def test_known_empty_converters_differ_from_missing_identifier(sqlite_instance: SQLiteMemory) -> None:
    missing = make_result(index=2)
    missing.atomic_attack_identifier = None
    sqlite_instance.add_attack_results_to_memory(attack_results=[make_result(), missing])
    report = AttackAnalyticsReader(memory=sqlite_instance).report(
        query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type")), control=control()
    )
    assert {group.option.key.kind.value for group in report.groups} == {"missing", "no_converters"}


def test_literal_label_paths_and_facet_search(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
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
    assert reader.report(query=AttackAnalyticsQuery(filters=filters), control=control()).counts == {"success": 1}
    facet = reader.facets(
        query=AttackAnalyticsFacetQuery(
            filters=filters, dimension=AttackAnalyticsDimension(name="label", label_key="a.b"), search="%_"
        ),
        control=control(),
    )
    assert [item.key.value for item in facet.items] == ["50%_done"]


def test_facet_excludes_its_own_filters_but_keeps_other_dimensions(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
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
    values = AttackAnalyticsReader(memory=sqlite_instance).facets(
        query=AttackAnalyticsFacetQuery(filters=filters, dimension=AttackAnalyticsDimension(name="operation")),
        control=control(),
    )
    assert [item.key.value for item in values.items] == ["one", "two"]


def test_results_pagination_never_queries_aggregates_or_scores(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[make_result(index=index) for index in range(1, 6)])
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    statements: list[str] = []

    def record_statement(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        statements.append(statement)

    event.listen(sqlite_instance.engine, "before_cursor_execute", record_statement)
    try:
        with patch.object(reader, "report", side_effect=AssertionError("Report must not run")):
            first = reader.results(query=AttackAnalyticsResultsQuery(limit=2), control=control())
            second = reader.results(
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
        event.remove(sqlite_instance.engine, "before_cursor_execute", record_statement)


def test_changed_filters_reject_stale_result_cursor(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[make_result(), make_result(index=2)])
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    page = reader.results(query=AttackAnalyticsResultsQuery(limit=1), control=control())
    with pytest.raises(ValueError, match="stale"):
        reader.results(
            query=AttackAnalyticsResultsQuery(
                cursor=page.next_cursor, filters=AttackAnalyticsFilters(outcomes=[AttackOutcome.FAILURE])
            ),
            control=control(),
        )


def test_report_snapshots_mutable_filters_and_axes(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
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

    event.listen(sqlite_instance.engine, "after_cursor_execute", change_request_after_totals)
    try:
        report = AttackAnalyticsReader(memory=sqlite_instance).report(query=query, control=control())
    finally:
        event.remove(sqlite_instance.engine, "after_cursor_execute", change_request_after_totals)
    assert mutated
    assert report.counts == {"success": 1}
    assert [(group.option.key.value, group.counts) for group in report.groups] == [("selected", {"success": 1})]
    assert [row.attack_result_id for row in report.results.items] == [str(uuid.UUID(int=1))]


def test_results_request_snapshots_filters_and_page_limit_before_session_acquisition(
    sqlite_instance: SQLiteMemory,
) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            make_result(index=1),
            make_result(index=2),
            make_result(index=3, outcome=AttackOutcome.FAILURE),
        ]
    )
    query = AttackAnalyticsResultsQuery(filters=AttackAnalyticsFilters(outcomes=[AttackOutcome.SUCCESS]), limit=1)
    get_session = sqlite_instance.get_session

    def change_request_on_acquire(*, timeout: float | None) -> Session:
        query.filters.outcomes[:] = [AttackOutcome.FAILURE]
        query.limit = 2
        return get_session(timeout=timeout)

    with patch.object(sqlite_instance, "get_session", side_effect=change_request_on_acquire):
        page = AttackAnalyticsReader(memory=sqlite_instance).results(query=query, control=control())
    assert [row.attack_result_id for row in page.items] == [str(uuid.UUID(int=2))]
    assert page.has_more


def test_facet_request_snapshots_filters_dimension_and_limit_before_session_acquisition(
    sqlite_instance: SQLiteMemory,
) -> None:
    sqlite_instance.add_attack_results_to_memory(
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
    get_session = sqlite_instance.get_session

    def change_request_on_acquire(*, timeout: float | None) -> Session:
        query.filters.outcomes[:] = [AttackOutcome.FAILURE]
        query.dimension = AttackAnalyticsDimension(name="model")
        query.limit = 2
        return get_session(timeout=timeout)

    with patch.object(sqlite_instance, "get_session", side_effect=change_request_on_acquire):
        facet = AttackAnalyticsReader(memory=sqlite_instance).facets(query=query, control=control())
    assert [item.key.value for item in facet.items] == ["a"]
    assert facet.has_more


def test_reader_revalidates_mutated_requests_before_database_access(sqlite_instance: SQLiteMemory) -> None:
    query = AttackAnalyticsQuery()
    query.group_limit = 0
    with patch.object(sqlite_instance, "get_session", side_effect=AssertionError("Must not acquire")):
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            AttackAnalyticsReader(memory=sqlite_instance).report(query=query, control=control())


def test_expired_control_prevents_database_access(sqlite_instance: SQLiteMemory) -> None:
    with patch.object(sqlite_instance, "get_session", side_effect=AssertionError("Must not acquire")):
        with pytest.raises(AnalyticsTimeoutException):
            AttackAnalyticsReader(memory=sqlite_instance).report(
                query=AttackAnalyticsQuery(), control=QueryControl(deadline=0)
            )


@pytest.mark.parametrize("trigger", ["deadline", "cancel"])
def test_sqlite_execution_deadline_or_cancellation_cleans_up_the_connection(
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

    with sqlite_instance.engine.connect() as connection:
        driver = connection.connection.driver_connection
        assert isinstance(driver, sqlite3.Connection)
        driver.create_function("stop_during_query", 0, stop_during_query)
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
                reader.report(query=AttackAnalyticsQuery(), control=query_control)
    finally:
        driver.create_function("stop_during_query", 0, None)
    assert query_control.expired
    assert query_control.cancel_event.is_set() == (trigger == "cancel")
    assert reader.report(query=AttackAnalyticsQuery(), control=control()).counts == {}


@pytest.mark.parametrize("method", ["report", "results", "facets"])
def test_sqlite_deadline_bounds_file_database_lock_wait_and_restores_timeout(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path, method: str
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'analytics-lock.sqlite'}", connect_args={"timeout": 2})
    try:
        Base.metadata.create_all(engine)
        with (
            patch.object(sqlite_instance, "engine", engine),
            patch.object(sqlite_instance, "SessionFactory", sessionmaker(bind=engine)),
            patch.object(sqlite_instance, "_connection_lock", None),
            engine.connect() as writer,
        ):
            writer.exec_driver_sql("BEGIN EXCLUSIVE")
            try:
                started = time.monotonic()
                reader = AttackAnalyticsReader(memory=sqlite_instance)
                budget = QueryControl(deadline=started + 0.08)
                with pytest.raises(AnalyticsTimeoutException):
                    if method == "report":
                        reader.report(query=AttackAnalyticsQuery(), control=budget)
                    elif method == "results":
                        reader.results(query=AttackAnalyticsResultsQuery(), control=budget)
                    else:
                        reader.facets(
                            query=AttackAnalyticsFacetQuery(dimension=AttackAnalyticsDimension(name="operation")),
                            control=budget,
                        )
                assert time.monotonic() - started < 1
                with engine.connect() as reader_connection:
                    assert reader_connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 2000
            finally:
                writer.rollback()
            assert (
                AttackAnalyticsReader(memory=sqlite_instance)
                .report(query=AttackAnalyticsQuery(), control=control())
                .counts
                == {}
            )
    finally:
        engine.dispose()


def test_odbc_timeout_is_applied_before_each_cursor_is_created() -> None:
    class RecordingConnection(sqlite3.Connection):
        timeout = 0
        cursor_timeouts: list[int] = []

        def cursor(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
            self.cursor_timeouts.append(self.timeout)
            cursor = super().cursor(*args, **kwargs)
            assert isinstance(cursor, sqlite3.Cursor)
            return cursor

    driver = sqlite3.connect(":memory:", factory=RecordingConnection)
    engine = create_engine("sqlite://", creator=lambda: driver)
    memory = SQLiteMemory.__new__(SQLiteMemory)
    memory.engine = engine
    memory.SessionFactory = sessionmaker(bind=engine)
    memory._connection_lock = None
    try:
        with engine.connect():
            pass
        budget = QueryControl(deadline=time.monotonic() + 30)
        with patch.object(engine.dialect, "name", "mssql"):
            with AttackAnalyticsReader(memory=memory)._session(control=budget) as (session, dialect, _):
                assert dialect == "mssql"
                assert session.execute(text("SELECT 42")).scalar_one() == 42
                budget.deadline = time.monotonic() + 1.5
                assert session.execute(text("SELECT 43")).scalar_one() == 43
        assert driver.cursor_timeouts[-2] >= 10
        assert 1 <= driver.cursor_timeouts[-1] <= 2
        assert driver.timeout == 0
    finally:
        engine.dispose()
        driver.close()


def test_invalidated_sqlite_connection_preserves_the_original_error(sqlite_instance: SQLiteMemory) -> None:
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    with pytest.raises(OperationalError, match="lost connection"):
        with reader._session(control=control()) as (session, _, _):
            session.connection().invalidate()
            raise OperationalError("SELECT", {}, RuntimeError("lost connection"))


def test_database_error_is_not_returned_as_an_empty_report(sqlite_instance: SQLiteMemory) -> None:
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    with patch.object(
        AttackAnalyticsQueryCompiler,
        "totals",
        return_value=text("SELECT * FROM nonexistent_analytics_table"),
    ):
        with pytest.raises(OperationalError, match="no such table"):
            reader.report(query=AttackAnalyticsQuery(), control=control())
    assert reader.report(query=AttackAnalyticsQuery(), control=control()).counts == {}


def test_compacted_identifiers_retain_incomplete_projection_fallback(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[make_result()])
    with sqlite_instance.engine.begin() as connection:
        connection.execute(update(AttackIdentifierEntry).values(class_name=None))
        connection.execute(update(TargetIdentifierEntry).values(model_name=None, underlying_model_name=None))
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    for name, expected in (("attack_type", "probeattack"), ("model", "model-a")):
        report = reader.report(
            query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name=name)), control=control()
        )
        assert report.groups[0].option.key.value == expected
        assert report.groups[0].counts == {"success": 1}


def test_label_and_converter_matrix_preserves_only_the_selected_label(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            make_result(labels={"team": "one", "case": "unique-a"}, converters=["Alpha"]),
            make_result(index=2, labels={"team": "one", "case": "unique-b"}, converters=["Alpha"]),
            make_result(index=3, labels={"team": "two"}, converters=["Beta"]),
        ]
    )
    report = AttackAnalyticsReader(memory=sqlite_instance).report(
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


@pytest.mark.parametrize("cap", ["MAX_COMPACT_PROFILES", "MAX_COMPACT_VALUE_LENGTH", "MAX_COMPACT_TOTAL_LENGTH"])
def test_profile_caps_fall_back_without_returning_partial_counts(*, sqlite_instance: SQLiteMemory, cap: str) -> None:
    sqlite_instance.add_attack_results_to_memory(
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
    expected = reader.report(query=query, control=control())
    with patch.object(reader, cap, 1):
        actual = reader.report(query=query, control=control(), use_compact_profiles=True)
    assert actual.profiles is None
    assert actual.counts == expected.counts == {"failure": 1, "success": 1}
    assert actual.cells == expected.cells
    assert actual.rows == expected.rows
    assert actual.columns == expected.columns


def test_empty_profiles_are_success_not_a_fallback_signal(sqlite_instance: SQLiteMemory) -> None:
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    with patch.object(AttackAnalyticsQueryCompiler, "groups", side_effect=AssertionError("Must not regroup")):
        report = reader.report(
            query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type")),
            control=control(),
            use_compact_profiles=True,
        )
    assert report.profiles == []
    assert report.counts == {}
    assert report.groups == []


def test_compact_profiles_keep_typed_raw_sources_and_reject_invalid_values(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[make_result(categories=["privacy"])])
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    report = reader.report(
        query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="targeted_harm_category")),
        control=control(),
        use_compact_profiles=True,
    )
    assert report.profiles == [{"source0": '["privacy"]', "outcome": "success", "weight": 1, "oversized": False}]
    with closing(sqlite_instance.get_session()) as session:
        invalid = (
            session.execute(
                select(
                    literal(123).label("source0"),
                    literal("success").label("outcome"),
                    literal(1).label("weight"),
                    literal(False).label("oversized"),
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


def test_timestamp_filters_use_half_open_utc_bounds(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[make_result(index=index) for index in range(1, 4)])
    filters = AttackAnalyticsFilters(
        updated_after=datetime(2026, 1, 1, 8, 0, 2, tzinfo=timezone(timedelta(hours=8))),
        updated_before=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
    )
    report = AttackAnalyticsReader(memory=sqlite_instance).report(
        query=AttackAnalyticsQuery(filters=filters), control=control()
    )
    assert report.counts == {"success": 1}
    assert [row.attack_result_id for row in report.results.items] == [str(uuid.UUID(int=2))]


def test_facet_self_exclusion_keeps_other_label_keys(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
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
    facet = AttackAnalyticsReader(memory=sqlite_instance).facets(
        query=AttackAnalyticsFacetQuery(
            filters=filters, dimension=AttackAnalyticsDimension(name="label", label_key="team")
        ),
        control=control(),
    )
    assert [option.key.value for option in facet.items] == ["one", "two"]


def test_literal_brackets_in_facet_search_match_only_literal_text(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            make_result(categories=["[a-z]%_\\"]),
            make_result(index=2, categories=["alphabet"]),
        ]
    )
    facet = AttackAnalyticsReader(memory=sqlite_instance).facets(
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
def test_sqlite_wide_labels_keep_filter_group_and_facet_semantics(
    *, sqlite_instance: SQLiteMemory, length: int
) -> None:
    value = "\u9577" * length
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[make_result(labels={"a.b": value}), make_result(index=2, labels={"a.b": "short"})]
    )
    dimension = AttackAnalyticsDimension(name="label", label_key="a.b")
    filters = AttackAnalyticsFilters.model_validate(
        {"dimensions": [predicate(name="label", values=[value], dimension_options={"label_key": "a.b"})]}
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    report = reader.report(query=AttackAnalyticsQuery(filters=filters, group_by=dimension), control=control())
    assert report.counts == {"success": 1}
    assert report.groups[0].option.key.value == value
    assert report.results.items[0].labels["a.b"] == value
    facet = reader.facets(query=AttackAnalyticsFacetQuery(filters=filters, dimension=dimension), control=control())
    assert {option.key.value for option in facet.items} == {value, "short"}


def test_canonical_and_legacy_identifiers_preserve_the_complete_cohort(sqlite_instance: SQLiteMemory) -> None:
    canonical = make_result(converters=["Alpha"])
    sqlite_instance.add_attack_results_to_memory(attack_results=[canonical])
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
    with sqlite_instance.engine.begin() as connection:
        canonical_document = connection.execute(select(AttackResultEntry.atomic_attack_identifier)).scalar_one()
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
            connection.execute(
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
    report = reader.report(
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
    model_report = reader.report(
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
        assert reader.report(query=AttackAnalyticsQuery(filters=filters), control=control()).counts == cell.counts


def test_computed_identifier_prefers_canonical_reference(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[make_result()])
    with sqlite_instance.engine.begin() as connection:
        canonical_hash = connection.execute(select(AttackResultEntry.atomic_attack_identifier_hash)).scalar_one()
        connection.execute(update(AttackResultEntry).values(atomic_attack_identifier={"hash": "other", "children": {}}))
        assert (
            connection.execute(select(AttackResultEntry.resolved_atomic_attack_identifier_hash)).scalar_one()
            == canonical_hash
        )
    report = AttackAnalyticsReader(memory=sqlite_instance).report(
        query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="attack_type")), control=control()
    )
    assert report.groups[0].option.key.value == "probeattack"


def test_blank_and_missing_label_keys_have_exact_membership(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            make_result(),
            make_result(index=2, labels={"team": ""}),
            make_result(index=3, labels={"team": "missing"}),
        ]
    )
    dimension = AttackAnalyticsDimension(name="label", label_key="team")
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    report = reader.report(query=AttackAnalyticsQuery(group_by=dimension), control=control())
    assert {(group.option.key.kind.value, group.option.key.value) for group in report.groups} == {
        ("missing", None),
        ("value", ""),
        ("value", "missing"),
    }
    for group in report.groups:
        filters = AttackAnalyticsFilters(
            dimensions=[AttackAnalyticsFilter(dimension=dimension, values=[group.option.key])]
        )
        assert reader.report(query=AttackAnalyticsQuery(filters=filters), control=control()).counts == {"success": 1}


@pytest.mark.parametrize("cap", ["MAX_COMPACT_PROFILES", "MAX_COMPACT_VALUE_LENGTH", "MAX_COMPACT_TOTAL_LENGTH"])
def test_complete_raw_profiles_at_each_cap_and_full_sql_fallback_beyond_it(
    *, sqlite_instance: SQLiteMemory, cap: str
) -> None:
    sqlite_instance.add_attack_results_to_memory(
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
    expected = reader.report(query=query, control=control())
    raw = reader.report(query=query, control=control(), use_compact_profiles=True)
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
        accepted = reader.report(query=query, control=control(), use_compact_profiles=True)
    assert accepted.profiles == raw.profiles
    with patch.object(reader, cap, limits[cap] - 1):
        fallback = reader.report(query=query, control=control(), use_compact_profiles=True)
    assert fallback.profiles is None
    assert fallback.counts == expected.counts == {"success": 2, "failure": 1}
    assert fallback.cells == expected.cells


def test_report_is_consistent_and_later_result_pages_are_fresh(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'analytics-snapshot.sqlite'}")
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one() == "wal"
        with (
            patch.object(sqlite_instance, "engine", engine),
            patch.object(sqlite_instance, "SessionFactory", sessionmaker(bind=engine)),
            patch.object(sqlite_instance, "_connection_lock", None),
        ):
            sqlite_instance.add_attack_results_to_memory(attack_results=[make_result(), make_result(index=2)])
            inserted = False

            def insert_after_totals(
                conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
            ) -> None:
                nonlocal inserted
                if inserted or "count(" not in statement.lower():
                    return
                inserted = True
                with engine.begin() as writer:
                    writer.execute(
                        AttackResultEntry.__table__.insert().values(
                            id=uuid.UUID(int=0),
                            conversation_id="shared-main-conversation",
                            objective="Concurrent saved result",
                            outcome="failure",
                            operation="operation-a",
                            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                        )
                    )

            event.listen(engine, "after_cursor_execute", insert_after_totals)
            try:
                reader = AttackAnalyticsReader(memory=sqlite_instance)
                report = reader.report(query=AttackAnalyticsQuery(result_limit=1), control=control())
            finally:
                event.remove(engine, "after_cursor_execute", insert_after_totals)
            assert inserted
            assert report.counts == report.groups[0].counts == {"success": 2}
            assert [row.attack_result_id for row in report.results.items] == [str(uuid.UUID(int=2))]
            assert report.results.has_more
            page = reader.results(
                query=AttackAnalyticsResultsQuery(cursor=report.results.next_cursor), control=control()
            )
            assert [row.attack_result_id for row in page.items] == [str(uuid.UUID(int=1)), str(uuid.UUID(int=0))]
            assert not page.has_more
    finally:
        engine.dispose()


@pytest.mark.parametrize("read_report", [False, True])
def test_sqlite_shared_connection_acquisition_timeout_releases_waiting_session(
    *, sqlite_instance: SQLiteMemory, read_report: bool
) -> None:
    pending = sqlite_instance.SessionFactory()
    with closing(sqlite_instance.get_session()):
        with (
            patch.object(sqlite_instance, "SessionFactory", return_value=pending),
            patch.object(pending, "close", wraps=pending.close) as close,
            ThreadPoolExecutor(max_workers=1) as workers,
        ):
            if read_report:
                future = workers.submit(
                    AttackAnalyticsReader(memory=sqlite_instance).report,
                    query=AttackAnalyticsQuery(),
                    control=QueryControl(deadline=time.monotonic() + 0.05),
                )
                error = AnalyticsTimeoutException
            else:
                future = workers.submit(sqlite_instance.get_session, timeout=0)
                error = TimeoutError
            with pytest.raises(error):
                future.result(timeout=5)
            close.assert_called_once()
    assert (
        AttackAnalyticsReader(memory=sqlite_instance).report(query=AttackAnalyticsQuery(), control=control()).counts
        == {}
    )


def test_query_bounds_keep_daylight_saving_fold_instants(sqlite_instance: SQLiteMemory) -> None:
    zone = ZoneInfo("America/New_York")
    results = [make_result(index=index) for index in range(1, 5)]
    for result, (hour, minute) in zip(results, [(5, 44), (5, 45), (6, 0), (6, 15)], strict=True):
        result.timestamp = datetime(2026, 11, 1, hour, minute, tzinfo=UTC)
    sqlite_instance.add_attack_results_to_memory(attack_results=results)
    filters = AttackAnalyticsFilters(
        updated_after=datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0),
        updated_before=datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1),
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    report = reader.report(query=AttackAnalyticsQuery(filters=filters, result_limit=1), control=control())
    assert report.counts == {"success": 2}
    assert report.results.items[0].attack_result_id == results[2].attack_result_id
    equivalent = AttackAnalyticsFilters.model_validate_json(filters.model_dump_json())
    page = reader.results(
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


def test_group_matrix_and_facet_limits_keep_complete_counts(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            make_result(index=index + 1, operation=f"op-{index % 3}", converters=[f"Converter{index % 2}"])
            for index in range(6)
        ]
    )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    dimension = AttackAnalyticsDimension(name="operation")
    query = AttackAnalyticsQuery(group_by=dimension, group_limit=1, result_limit=1)
    first = reader.report(query=query, control=control())
    assert first.counts == {"success": 6}
    assert len(first.groups) == 1
    assert first.groups[0].counts == {"success": 2}
    assert first.has_more_groups
    assert len(first.results.items) == 1
    assert first.results.has_more
    last = reader.report(query=query.model_copy(update={"group_offset": 2}), control=control())
    assert last.counts == first.counts
    assert last.groups[0].option.key.value == "op-2"
    assert not last.has_more_groups
    matrix = reader.report(
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
    facet = reader.facets(query=AttackAnalyticsFacetQuery(dimension=dimension, limit=2), control=control())
    assert [item.key.value for item in facet.items] == ["op-0", "op-1"]
    assert facet.has_more
    last_facet = reader.facets(
        query=AttackAnalyticsFacetQuery(dimension=dimension, limit=2, offset=2), control=control()
    )
    assert [item.key.value for item in last_facet.items] == ["op-2"]
    assert not last_facet.has_more


def test_response_converter_facet_keeps_request_converter_filter(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
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
    facet = AttackAnalyticsReader(memory=sqlite_instance).facets(
        query=AttackAnalyticsFacetQuery(
            filters=filters,
            dimension=AttackAnalyticsDimension(name="converter_type", converter_direction="response"),
        ),
        control=control(),
    )
    assert {item.key.value for item in facet.items} == {"beta", "gamma"}


def test_sqlite_group_query_uses_the_computed_identifier_index(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[make_result(converters=["Alpha"])])
    query = AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type"))
    statement = AttackAnalyticsQueryCompiler(dialect="sqlite", filters=query.filters).groups(query)
    sql = str(statement.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    with sqlite_instance.engine.connect() as connection:
        plan = connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {sql}").all()
    assert any("ix_AttackResultEntries_analytics_facts_sqlite" in row[-1] for row in plan)
