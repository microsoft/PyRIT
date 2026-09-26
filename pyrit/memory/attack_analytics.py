# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Execute analytics projections in a request-owned database session.

Memory supplies saved outcome counts and metadata; the analytics SDK calculates
rates and display labels. A report shares one read transaction across totals,
chart inputs, and its first result page. Facets and later result pages are separate
fresh reads. No path reconstructs AttackResult objects, scores, or conversations.
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, NotRequired, TypedDict

from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from pyrit.common.pagination import decode_keyset_cursor, encode_keyset_cursor, fingerprint_filters
from pyrit.exceptions.analytics_exception import AnalyticsDataException, AnalyticsTimeoutException
from pyrit.memory.attack_analytics_query import AttackAnalyticsQueryCompiler
from pyrit.models import (
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFilters,
    AttackAnalyticsQuery,
    AttackAnalyticsResultRow,
    AttackAnalyticsResults,
    AttackAnalyticsResultsQuery,
    AttackAnalyticsValue,
    AttackAnalyticsValueKind,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.orm import Session

    from pyrit.memory.memory_interface import MemoryInterface
    from pyrit.memory.query_control import QueryControl


@dataclass
class RawAnalyticsOption:
    """A typed stored key and optional source label, before SDK absence labels are applied."""

    key: AttackAnalyticsValue
    label: str | None


@dataclass
class RawAnalyticsGroup:
    """
    Raw saved-outcome counts for one group, or one cell when ``column`` is present.

    Each distinct result ID contributes once per membership, not once per matching
    array element. Different groups may overlap; their totals need not sum to the cohort.
    """

    option: RawAnalyticsOption
    counts: dict[str, int]
    column: RawAnalyticsOption | None = None


class RawAnalyticsProfile(TypedDict):
    """One pre-counted, bounded metadata tuple for the optional SQLite fast path."""

    source0: str | None
    outcome: str
    weight: int
    oversized: bool
    source1: NotRequired[str | None]
    display0: NotRequired[str | None]
    display1: NotRequired[str | None]


@dataclass
class RawAnalyticsReport:
    """
    Cohort counts, chart inputs, and the first result page from one read transaction.

    ``profiles=None`` means SQL already supplied groups or cells. A non-None profile
    list means the SDK still needs to populate those fields from bounded weighted
    metadata; an empty list is a genuine empty cohort, not a failed query or fallback.
    Counts retain all saved outcomes without calculating a decided-result denominator.
    """

    counts: dict[str, int]
    groups: list[RawAnalyticsGroup]
    rows: list[RawAnalyticsOption]
    columns: list[RawAnalyticsOption]
    cells: list[RawAnalyticsGroup]
    has_more_groups: bool
    axes_truncated: bool
    results: AttackAnalyticsResults
    warnings: list[str]
    profiles: list[RawAnalyticsProfile] | None = None


@dataclass
class RawAnalyticsFacets:
    """One searchable facet page, with lookahead status and its own read-completion timestamp."""

    items: list[RawAnalyticsOption]
    has_more: bool
    computed_at: datetime


class AttackAnalyticsReader:
    """
    Read metadata without hydrating AttackResult object graphs or caching reports.

    The profile probe bounds returned rows and individual metadata strings, not SQL
    scans or intermediate work. Its combined-text cap is checked after fetching
    that probe, so it is not a peak-memory or network-byte guarantee. Exceeding any
    cap selects complete SQL aggregation, never sampling or partial chart counts.
    All text limits below are character counts.
    """

    # Frequent Python callbacks serialize concurrent SQLite readers on the GIL.
    SQLITE_PROGRESS_STEPS: ClassVar[int] = 100_000
    MAX_COMPACT_PROFILES: ClassVar[int] = 4096
    MAX_COMPACT_VALUE_LENGTH: ClassVar[int] = 4096
    MAX_COMPACT_TOTAL_LENGTH: ClassVar[int] = 1_000_000

    def __init__(self, *, memory: MemoryInterface) -> None:
        """Retain the memory backend; each operation acquires and closes its own session."""
        self._memory = memory

    def report(
        self, *, query: AttackAnalyticsQuery, control: QueryControl, use_compact_profiles: bool = False
    ) -> RawAnalyticsReport:
        """
        Read the report and its first result page in a short consistent view.

        Args:
            query (AttackAnalyticsQuery): Validated cohort, dimensions, and output limits.
            control (QueryControl): Shared deadline/cancellation signal for the whole operation.
            use_compact_profiles (bool): Allow the SDK to finish aggregation from bounded
                SQLite profiles. False keeps chart aggregation entirely in SQL.

        Returns:
            RawAnalyticsReport: Raw counts and bounded projections, or profiles that
                still need SDK aggregation. Query failures propagate; they are not empty reports.
        """
        query = AttackAnalyticsQuery.model_validate(query.model_dump())
        with self._session(control=control, consistent=True) as (session, dialect, warnings):
            compiler = AttackAnalyticsQueryCompiler(dialect=dialect, filters=query.filters)
            counts = dict(session.execute(compiler.totals()).tuples().all())
            groups: list[RawAnalyticsGroup] = []
            rows: list[RawAnalyticsOption] = []
            columns: list[RawAnalyticsOption] = []
            cells: list[RawAnalyticsGroup] = []
            has_more = False
            truncated = False
            profiles = (
                self._read_compact_profiles(session=session, compiler=compiler, query=query)
                if use_compact_profiles
                else None
            )
            if profiles is None:
                if query.compare_by is None:
                    records = session.execute(compiler.groups(query)).mappings().all()
                    has_more = len(records) > query.group_limit
                    groups = [self._group(record) for record in records[: query.group_limit]]
                else:
                    for record in session.execute(compiler.matrix(query)).mappings():
                        truncated = bool(record["truncated"])
                        if record["record"] == "row":
                            rows.append(self._option(record, index=0))
                        elif record["record"] == "column":
                            columns.append(self._option(record, index=1))
                        else:
                            cells.append(self._group(record, matrix=True))
            results = self._results(
                session=session,
                dialect=dialect,
                query=AttackAnalyticsResultsQuery(filters=query.filters, limit=query.result_limit),
            )
            control.check()
            return RawAnalyticsReport(
                counts=counts,
                groups=groups,
                rows=rows,
                columns=columns,
                cells=cells,
                has_more_groups=has_more,
                axes_truncated=truncated,
                results=results,
                warnings=warnings,
                profiles=profiles,
            )

    def results(self, *, query: AttackAnalyticsResultsQuery, control: QueryControl) -> AttackAnalyticsResults:
        """
        Read only a result page, without any report/count query.

        Args:
            query (AttackAnalyticsResultsQuery): Cohort filters, cursor, and page size.
            control (QueryControl): Deadline/cancellation signal for this fresh read.

        Returns:
            AttackAnalyticsResults: A page with its own freshness timestamp.
        """
        query = AttackAnalyticsResultsQuery.model_validate(query.model_dump())
        with self._session(control=control) as (session, dialect, _):
            result = self._results(session=session, dialect=dialect, query=query)
            control.check()
            return result

    def facets(self, *, query: AttackAnalyticsFacetQuery, control: QueryControl) -> RawAnalyticsFacets:
        """
        Read one requested facet, excluding its own predicates to allow alternatives.

        Self-exclusion compares the full dimension, not just its name. Opening
        one label key or the response converter facet keeps filters on other
        label keys and the request converter pipeline.

        Args:
            query (AttackAnalyticsFacetQuery): Filters, full facet dimension, and search/page limits.
            control (QueryControl): Deadline/cancellation signal for this fresh read.

        Returns:
            RawAnalyticsFacets: The bounded facet page.
        """
        query = AttackAnalyticsFacetQuery.model_validate(query.model_dump())
        filters = query.filters.model_copy(
            update={
                "dimensions": [
                    predicate for predicate in query.filters.dimensions if predicate.dimension != query.dimension
                ]
            }
        )
        with self._session(control=control) as (session, dialect, _):
            compiler = AttackAnalyticsQueryCompiler(dialect=dialect, filters=filters)
            records = session.execute(compiler.facet(query)).mappings().all()
            control.check()
            return RawAnalyticsFacets(
                items=[self._option(record, index=0) for record in records[: query.limit]],
                has_more=len(records) > query.limit,
                computed_at=datetime.now(tz=UTC),
            )

    def _read_compact_profiles(
        self, *, session: Session, compiler: AttackAnalyticsQueryCompiler, query: AttackAnalyticsQuery
    ) -> list[RawAnalyticsProfile] | None:
        """
        Accept only a complete, size-bounded profile projection for SDK aggregation.

        SQL limits this probe to the profile cap plus one row and replaces
        individually oversized strings with a flagged NULL. The combined text
        check happens after fetching the bounded probe and before JSON decoding;
        it does not cap peak memory or transferred bytes. None means the caller
        must run the general SQL query, not use a partially accepted list.

        Returns:
            list[RawAnalyticsProfile] | None: All profiles, including [] for an empty cohort,
                or None for an ineligible backend/dimension or any exceeded limit.
        """
        statement = compiler.compact_profiles(
            query=query,
            limit=self.MAX_COMPACT_PROFILES + 1,
            max_value_length=self.MAX_COMPACT_VALUE_LENGTH,
        )
        if statement is None:
            return None
        records = session.execute(statement).mappings().all()
        if len(records) > self.MAX_COMPACT_PROFILES or any(record["oversized"] for record in records):
            return None
        text_length = sum(len(value) for record in records for value in record.values() if isinstance(value, str))
        if text_length > self.MAX_COMPACT_TOTAL_LENGTH:
            return None
        return [self._raw_profile(record) for record in records]

    @classmethod
    def _raw_profile(cls, record: RowMapping) -> RawAnalyticsProfile:
        """
        Validate the bounded SQL projection without decoding its metadata arrays.

        Returns:
            RawAnalyticsProfile: Typed raw sources and their saved-outcome weight.

        Raises:
            AnalyticsDataException: If the database returns a malformed profile.
        """
        outcome, weight, oversized = record["outcome"], record["weight"], record["oversized"]
        if not isinstance(outcome, str) or type(weight) is not int or weight < 0 or type(oversized) is not bool:
            raise AnalyticsDataException("Stored categorical profiles contain invalid counts.")
        profile: RawAnalyticsProfile = {
            "source0": cls._raw_profile_text(record["source0"]),
            "outcome": outcome,
            "weight": weight,
            "oversized": oversized,
        }
        if "source1" in record:
            profile["source1"] = cls._raw_profile_text(record["source1"])
        if "display0" in record:
            profile["display0"] = cls._raw_profile_text(record["display0"])
        if "display1" in record:
            profile["display1"] = cls._raw_profile_text(record["display1"])
        return profile

    @staticmethod
    def _raw_profile_text(value: Any) -> str | None:
        """
        Reject non-text profile sources before passing them to the SDK.

        Returns:
            str | None: A raw metadata string or absent value.

        Raises:
            AnalyticsDataException: If a stored source is not text or NULL.
        """
        if value is not None and not isinstance(value, str):
            raise AnalyticsDataException("A compact metadata profile is not a string.")
        return value

    @contextmanager
    def _session(self, *, control: QueryControl, consistent: bool = False) -> Iterator[tuple[Session, str, list[str]]]:
        """
        Own a session and its cancellation hooks until all database work has finished.

        SQLite's progress handler interrupts long statements cooperatively; its
        busy timeout is bounded per statement so lock waits share the deadline.
        ODBC receives the remaining whole-second timeout before each cursor is
        created. Cancellation never returns a pooled connection while its
        statement is still running.

        Consistent reports explicitly start SQLite's read transaction (including
        with legacy sqlite3 transaction control) or request SQL Server SNAPSHOT.
        This does not enable WAL or change server settings. Missing snapshot support
        or ordinary database failures propagate rather than silently weakening the view.

        Args:
            control (QueryControl): Shared budget, also used for memory's session-acquisition wait.
            consistent (bool): Keep a report's several projections in one consistent read.

        Yields:
            tuple[Session, str, list[str]]: Owned session, dialect name, and advisory warnings.

        Raises:
            AnalyticsTimeoutException: If acquisition or execution outlasts the budget,
                or cancellation is observed.
            AnalyticsDataException: If no live DBAPI connection is available.
            NotImplementedError: If the driver cannot supply the required interruption/timeout hook.
            OperationalError: If a database error occurs before expiry.
        """
        control.check()
        try:
            session = self._memory.get_session(timeout=control.remaining)
        except TimeoutError as error:
            raise AnalyticsTimeoutException from error
        with closing(session):
            dialect = session.get_bind().dialect.name
            options = {"isolation_level": "SNAPSHOT"} if consistent and dialect == "mssql" else {}
            connection = session.connection(execution_options=options)
            driver = connection.connection.driver_connection
            if driver is None:
                raise AnalyticsDataException("The analytics database connection is closed.")
            warnings: list[str] = []
            old_timeout: int | None = None
            old_busy_timeout: int | None = None

            def before_statement(
                conn: Any, clauseelement: Any, multiparams: Any, params: Any, execution_options: Any
            ) -> None:
                """Apply ODBC's per-cursor timeout before SQLAlchemy creates the cursor."""
                control.check()
                if dialect == "mssql":
                    driver.timeout = max(1, math.ceil(control.remaining))

            def before_cursor_execute(
                conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
            ) -> None:
                """Recheck the budget and bound SQLite waits at the statement boundary."""
                control.check()
                if old_busy_timeout is not None:
                    # Direct DBAPI execution avoids recursively firing this listener.
                    driver.execute(
                        f"PRAGMA busy_timeout = {min(old_busy_timeout, max(1, math.ceil(control.remaining * 1000)))}"
                    )

            event.listen(connection, "before_execute", before_statement)
            event.listen(connection, "before_cursor_execute", before_cursor_execute)
            try:
                control.check()
                if dialect == "sqlite":
                    if not isinstance(driver, sqlite3.Connection):
                        raise NotImplementedError("SQLite analytics requires a sqlite3 connection")
                    old_busy_timeout = driver.execute("PRAGMA busy_timeout").fetchone()[0]
                    driver.execute(
                        f"PRAGMA busy_timeout = {min(old_busy_timeout, max(1, math.ceil(control.remaining * 1000)))}"
                    )
                    driver.set_progress_handler(lambda: int(control.expired), self.SQLITE_PROGRESS_STEPS)
                    if consistent:
                        mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
                        if mode not in {"wal", "memory"}:
                            warnings.append(
                                "SQLite rollback journaling can slow analytics during concurrent writes. "
                                "Consider explicitly configured WAL mode for concurrent workloads. "
                                "Analytics has not changed database settings."
                            )
                        if not driver.in_transaction:
                            connection.exec_driver_sql("BEGIN")
                elif dialect == "mssql":
                    old_timeout = getattr(driver, "timeout", None)
                    if not isinstance(old_timeout, int):
                        raise NotImplementedError("Azure SQL analytics requires an ODBC query-timeout property")
                    driver.timeout = max(1, math.ceil(control.remaining))
                yield session, dialect, warnings
            except OperationalError as error:
                if control.expired:
                    raise AnalyticsTimeoutException from error
                raise
            finally:
                event.remove(connection, "before_execute", before_statement)
                event.remove(connection, "before_cursor_execute", before_cursor_execute)
                if not connection.invalidated and not connection.closed:
                    if isinstance(driver, sqlite3.Connection):
                        driver.set_progress_handler(None, 0)
                        if old_busy_timeout is not None:
                            driver.execute(f"PRAGMA busy_timeout = {old_busy_timeout}")
                    if old_timeout is not None:
                        driver.timeout = old_timeout

    def _results(self, *, session: Session, dialect: str, query: AttackAnalyticsResultsQuery) -> AttackAnalyticsResults:
        """
        Validate a filter-bound seek cursor and materialize at most one visible metadata page.

        Returns:
            AttackAnalyticsResults: Visible rows plus a cursor made from the last visible
                row, never the extra lookahead row. No totals query is needed for has_more.

        Raises:
            ValueError: If a supplied cursor is malformed or belongs to different filters.
        """
        fingerprint = self._fingerprint(query.filters)
        after = decode_keyset_cursor(cursor=query.cursor, fingerprint=fingerprint)
        if query.cursor is not None and after is None:
            raise ValueError("Invalid or stale analytics cursor. Reload results with the current filters.")
        compiler = AttackAnalyticsQueryCompiler(dialect=dialect, filters=query.filters)
        records = session.execute(compiler.results(limit=query.limit, after=after)).mappings().all()
        items = [self._result_row(record) for record in records[: query.limit]]
        has_more = len(records) > query.limit
        cursor = (
            encode_keyset_cursor(
                timestamp=items[-1].updated_at,
                identifier=items[-1].attack_result_id,
                fingerprint=fingerprint,
            )
            if has_more and items
            else None
        )
        return AttackAnalyticsResults(
            items=items,
            has_more=has_more,
            next_cursor=cursor,
            computed_at=datetime.now(tz=UTC),
        )

    @staticmethod
    def _fingerprint(filters: AttackAnalyticsFilters) -> str:
        """
        Bind pagination to the validated filters and distinct-result selection mode.

        Returns:
            str: A cursor fingerprint that cannot be reused for History's legacy
                newest-result-per-conversation selection.
        """
        return fingerprint_filters(
            filters={"filters": filters.model_dump(mode="json"), "result_selection": "all_results"}
        )

    @staticmethod
    def _option(record: RowMapping, *, index: int) -> RawAnalyticsOption:
        """
        Decode a SQL typed key without mistaking its display text for its identity.

        Returns:
            RawAnalyticsOption: A validated key; internal empty-text placeholders are
                discarded for absence kinds, but retained for real blank values.

        Raises:
            AnalyticsDataException: If SQL reports an invalid kind or stored value.
        """
        try:
            kind = AttackAnalyticsValueKind(record[f"kind{index}"])
            key = AttackAnalyticsValue(
                kind=kind,
                value=record[f"value{index}"] if kind is AttackAnalyticsValueKind.VALUE else None,
            )
        except ValueError as error:
            raise AnalyticsDataException("Stored attack metadata contains an invalid dimension value.") from error
        return RawAnalyticsOption(key=key, label=record[f"label{index}"])

    @classmethod
    def _group(cls, record: RowMapping, *, matrix: bool = False) -> RawAnalyticsGroup:
        """
        Decode a group/cell's raw counts, rejecting malformed or non-integral aggregates.

        Returns:
            RawAnalyticsGroup: One row-axis option and optional column-axis option,
                with nonnegative integer counts and no derived statistics.

        Raises:
            AnalyticsDataException: If the aggregate JSON or any count is invalid.
        """
        try:
            counts = json.loads(record["counts"])
        except (json.JSONDecodeError, TypeError) as error:
            raise AnalyticsDataException("Stored attack outcomes could not be aggregated.") from error
        if not isinstance(counts, dict) or any(
            not isinstance(key, str) or type(count) is not int or count < 0 for key, count in counts.items()
        ):
            raise AnalyticsDataException("Stored attack outcomes contain invalid counts.")
        return RawAnalyticsGroup(
            option=cls._option(record, index=0),
            counts=counts,
            column=cls._option(record, index=1) if matrix else None,
        )

    @classmethod
    def _result_row(cls, record: RowMapping) -> AttackAnalyticsResultRow:
        """
        Validate a lightweight projection, without constructing an AttackResult graph.

        UUIDs become API strings. Native JSON columns are already decoded by
        SQLAlchemy, while converter expressions still need decoding. Table rows
        use empty lists/maps for absent display collections; report keys preserve
        the stronger missing-versus-empty distinction independently.

        Returns:
            AttackAnalyticsResultRow: A bounded row with attribution, preview, and metadata.

        Raises:
            AnalyticsDataException: If persisted fields fail the projection contract.
        """
        data = dict(record)
        data["attack_result_id"] = str(data["attack_result_id"])
        if data["scenario_result_id"] is not None:
            data["scenario_result_id"] = str(data["scenario_result_id"])
        if data["targeted_harm_categories"] is None:
            data["targeted_harm_categories"] = []
        if data["labels"] is None:
            data["labels"] = {}
        data["request_converters"] = cls._converter_names(data["request_converters"])
        data["response_converters"] = cls._converter_names(data["response_converters"])
        try:
            return AttackAnalyticsResultRow.model_validate(data)
        except ValidationError as error:
            raise AnalyticsDataException("A stored result contains invalid analytics metadata.") from error

    @staticmethod
    def _converter_names(raw: str | None) -> list[str]:
        """
        Read names from normalized string arrays or historical converter-object arrays.

        Returns:
            list[str]: Sorted, distinct recorded spellings. Missing names are omitted
                from this display list; no identifier reconstruction or registry lookup occurs.

        Raises:
            AnalyticsDataException: If the serialized collection or a recorded name is invalid.
        """
        if raw is None:
            return []
        try:
            values = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as error:
            raise AnalyticsDataException("Stored converter metadata is not valid JSON.") from error
        if values is None:
            return []
        if not isinstance(values, list):
            raise AnalyticsDataException("Stored converter metadata is not a list.")
        names: set[str] = set()
        for value in values:
            if isinstance(value, dict):
                name = value["class_name"] if "class_name" in value else value.get("__type__")
            else:
                name = value
            if name is None:
                continue
            if not isinstance(name, str):
                raise AnalyticsDataException("A recorded converter type is not a string.")
            names.add(name)
        return sorted(names)
