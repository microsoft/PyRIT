# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Async SDK interpretation of persisted AttackResult outcomes."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, ClassVar, Self, TypeVar
from weakref import WeakKeyDictionary

from pydantic import BaseModel

from pyrit.analytics._execution import AnalyticsExecution
from pyrit.analytics._profile_aggregation import ProfileAggregation
from pyrit.analytics.outcome_statistics import compute_outcome_statistics
from pyrit.exceptions.analytics_exception import AnalyticsBusyException, AnalyticsDataException
from pyrit.memory import CentralMemory
from pyrit.memory.attack_analytics import AttackAnalyticsReader, RawAnalyticsOption, RawAnalyticsReport
from pyrit.models import (
    AttackAnalyticsCell,
    AttackAnalyticsDimension,
    AttackAnalyticsDimensionName,
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFacets,
    AttackAnalyticsFilter,
    AttackAnalyticsFilters,
    AttackAnalyticsGroup,
    AttackAnalyticsOption,
    AttackAnalyticsQuery,
    AttackAnalyticsReport,
    AttackAnalyticsResults,
    AttackAnalyticsResultsQuery,
    AttackAnalyticsStatistics,
    AttackAnalyticsValue,
    AttackAnalyticsValueKind,
)

if TYPE_CHECKING:
    from types import TracebackType

    from pyrit.memory import MemoryInterface
    from pyrit.memory.query_control import QueryControl

QueryT = TypeVar("QueryT", bound=BaseModel)


class AttackResultAnalytics:
    """
    Analyze every distinct saved result ID, not conversations or scenario execution units.

    Facades sharing a memory instance share one loop-bound execution budget.
    Reuse a long-lived SDK owner, then await ``close_async`` on that same loop
    before disposing or replacing memory. Closing drains and terminates that
    shared controller, including work submitted through other facades.
    No authorization, in-flight reuse, or persistent result caching is performed.
    """

    _EXECUTIONS: ClassVar[WeakKeyDictionary[MemoryInterface, AnalyticsExecution]] = WeakKeyDictionary()
    _EXECUTION_LOCK: ClassVar[threading.Lock] = threading.Lock()
    _MULTIVALUED: ClassVar[frozenset[AttackAnalyticsDimensionName]] = frozenset(
        {AttackAnalyticsDimensionName.TARGETED_HARM_CATEGORY, AttackAnalyticsDimensionName.CONVERTER_TYPE}
    )

    def __init__(self, *, memory: MemoryInterface | None = None) -> None:
        """
        Retain an initialized backend without querying it or changing its schema/settings.

        Args:
            memory (MemoryInterface | None): Backend to read, or the configured
                CentralMemory instance. Its resource lifecycle remains caller-owned.
        """
        self._memory = memory if memory is not None else CentralMemory.get_memory_instance()
        self._reader = AttackAnalyticsReader(memory=self._memory)
        self._controller: AnalyticsExecution | None = None
        self._closed = False

    async def __aenter__(self) -> Self:
        """
        Acquire the shared execution lifetime without opening a database session.

        Returns:
            Self: The loop-bound SDK facade.
        """
        self._execution()._check_loop()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Drain the shared analytics lifetime before leaving the context."""
        await self.close_async()

    async def query_async(self, *, query: AttackAnalyticsQuery | None = None) -> AttackAnalyticsReport:
        """
        Compute a coherent report and its first lightweight result page.

        Args:
            query (AttackAnalyticsQuery | None): Cohort, dimensions, and output limits.
                None selects all saved result IDs grouped by operation. A deep,
                revalidated snapshot is taken before admission or queueing.

        Returns:
            AttackAnalyticsReport: Caller-owned counts, rates, annotations and exact
                additional drill-down predicates. No snapshot is retained for later calls.

        Raises:
            AnalyticsBusyException: If admission is full/expired or this lifetime is closing.
            AnalyticsTimeoutException: If the shared database/SDK execution budget expires.
            AnalyticsDataException: If stored data cannot be represented faithfully.
            RuntimeError: If used from a different event loop.
        """
        request = self._copy(query if query is not None else AttackAnalyticsQuery())
        return await self._execution().run_async(
            report=True, task=lambda control: self._report_async(query=request, control=control)
        )

    async def results_async(self, *, query: AttackAnalyticsResultsQuery | None = None) -> AttackAnalyticsResults:
        """
        Fetch a fresh metadata page without recalculating any report.

        Args:
            query (AttackAnalyticsResultsQuery | None): Cohort and opaque cursor.
                None requests the first page of all saved results.

        Returns:
            AttackAnalyticsResults: Caller-owned result projections with their own freshness timestamp.

        Raises:
            ValueError: If the cursor is malformed or belongs to different filters.
            AnalyticsBusyException: If quick-query admission fails or shutdown has begun.
            AnalyticsTimeoutException: If the operation exceeds its execution budget.
        """
        request = self._copy(query if query is not None else AttackAnalyticsResultsQuery())
        return await self._execution().run_async(
            report=False, task=lambda control: self._reader.results_async(query=request, control=control)
        )

    async def facets_async(self, *, query: AttackAnalyticsFacetQuery) -> AttackAnalyticsFacets:
        """
        Look up one facet page without calculating a report or fetching other facets.

        Args:
            query (AttackAnalyticsFacetQuery): Dimension, search, page, and cohort.
                Only this exact dimension's predicates are omitted when finding alternatives.

        Returns:
            AttackAnalyticsFacets: Typed options and the next offset, if any.
        """
        request = self._copy(query)
        return await self._execution().run_async(
            report=False, task=lambda control: self._facets_async(query=request, control=control)
        )

    async def close_async(self) -> None:
        """
        Drain the backend's shared controller; never dispose caller-owned memory.

        Close on the owning loop, after all users of this memory's analytics
        lifetime have finished. Cancellation of this await is delayed until actual
        operation/session cleanup completes. New controllers remain forbidden
        during draining. Used facades cannot reopen; construct a new SDK instance
        after closing to begin another lifetime. Closing an unused facade is a no-op
        for other facades and still permanently closes that unused facade.
        """
        if self._controller is None:
            self._closed = True
            return
        execution = self._controller
        try:
            await execution.close_async()
        finally:
            if execution.is_closed:
                self._closed = True
                with self._EXECUTION_LOCK:
                    if self._EXECUTIONS.get(self._memory) is execution:
                        del self._EXECUTIONS[self._memory]

    def _execution(self) -> AnalyticsExecution:
        if self._closed:
            raise AnalyticsBusyException
        if self._controller is None:
            with self._EXECUTION_LOCK:
                execution = self._EXECUTIONS.get(self._memory)
                if execution is None:
                    execution = AnalyticsExecution()
                    self._EXECUTIONS[self._memory] = execution
                self._controller = execution
        return self._controller

    async def _report_async(self, *, query: AttackAnalyticsQuery, control: QueryControl) -> AttackAnalyticsReport:
        raw = await self._reader.report_async(query=query, control=control, use_compact_profiles=True)
        summary = self._statistics(raw.counts)
        await ProfileAggregation.populate_async(report=raw, query=query, control=control)
        report = AttackAnalyticsReport(
            filters=query.filters,
            group_by=query.group_by,
            compare_by=query.compare_by,
            summary=summary,
            outcome_filter_applied=bool(query.filters.outcomes),
            groups_overlap=query.group_by.name in self._MULTIVALUED
            or (query.compare_by is not None and query.compare_by.name in self._MULTIVALUED),
            drilldown_unavailable_reason=self._drilldown_unavailable_reason(query),
            groups=[
                AttackAnalyticsGroup(
                    **self._option(raw=group.option, dimension=query.group_by).model_dump(),
                    statistics=self._statistics(group.counts),
                    drilldown_filters=[self._drilldown(dimension=query.group_by, key=group.option.key)],
                )
                for group in raw.groups
            ],
            has_more_groups=raw.has_more_groups,
            next_group_offset=query.group_offset + len(raw.groups) if raw.has_more_groups else None,
            rows=[self._option(raw=option, dimension=query.group_by) for option in raw.rows],
            columns=[self._option(raw=option, dimension=query.compare_by) for option in raw.columns]
            if query.compare_by is not None
            else [],
            cells=self._cells(query=query, raw=raw),
            axes_truncated=raw.axes_truncated,
            results=raw.results,
            computed_at=raw.results.computed_at,
            warnings=raw.warnings,
        )
        control.check()
        return report

    async def _facets_async(self, *, query: AttackAnalyticsFacetQuery, control: QueryControl) -> AttackAnalyticsFacets:
        raw = await self._reader.facets_async(query=query, control=control)
        return AttackAnalyticsFacets(
            items=[self._option(raw=option, dimension=query.dimension) for option in raw.items],
            has_more=raw.has_more,
            next_offset=query.offset + len(raw.items) if raw.has_more else None,
            computed_at=raw.computed_at,
        )

    @classmethod
    def _cells(cls, *, query: AttackAnalyticsQuery, raw: RawAnalyticsReport) -> list[AttackAnalyticsCell]:
        """
        Fill the bounded matrix, including empty cells with unavailable rather than zero ASR.

        Returns:
            list[AttackAnalyticsCell]: One cell per visible axis pair, with two additional predicates.
        """
        if query.compare_by is None:
            return []
        counts = {
            (cls._value_key(cell.option.key), cls._value_key(cell.column.key)): cell.counts
            for cell in raw.cells
            if cell.column is not None
        }
        return [
            AttackAnalyticsCell(
                row=row.key,
                column=column.key,
                statistics=cls._statistics(counts.get((cls._value_key(row.key), cls._value_key(column.key)), {})),
                drilldown_filters=[
                    cls._drilldown(dimension=query.group_by, key=row.key),
                    cls._drilldown(dimension=query.compare_by, key=column.key),
                ],
            )
            for row in raw.rows
            for column in raw.columns
        ]

    @staticmethod
    def _statistics(counts: dict[str, int]) -> AttackAnalyticsStatistics:
        """
        Apply shared outcome statistics, retaining the SDK's stored-data error contract.

        Returns:
            AttackAnalyticsStatistics: Both success rates and whole-cohort outcome shares.

        Raises:
            AnalyticsDataException: If saved outcomes or their counts are invalid.
        """
        try:
            return compute_outcome_statistics(counts)
        except ValueError as error:
            raise AnalyticsDataException(str(error)) from error

    @staticmethod
    def _option(*, raw: RawAnalyticsOption, dimension: AttackAnalyticsDimension) -> AttackAnalyticsOption:
        """
        Keep stored typed identity independent of absence labels or distinguishing display suffixes.

        Returns:
            AttackAnalyticsOption: The unchanged key and its SDK display label.
        """
        if raw.key.kind is AttackAnalyticsValueKind.MISSING:
            label = "Not recorded"
        elif raw.key.kind is AttackAnalyticsValueKind.NO_CONVERTERS:
            label = "No converters"
        else:
            label = raw.label if raw.label is not None else raw.key.value or ""
            if not label.strip():
                label = "(Blank)"
            elif (
                dimension.name in {AttackAnalyticsDimensionName.OBJECTIVE_TARGET, AttackAnalyticsDimensionName.SCENARIO}
                and raw.key.value is not None
                and label != raw.key.value
            ):
                label = f"{label} ({raw.key.value[-8:]})"
        return AttackAnalyticsOption(key=raw.key, label=label)

    @staticmethod
    def _drilldown_unavailable_reason(query: AttackAnalyticsQuery) -> str | None:
        """
        Allow a final legal click to reach the budget; only disable the following click.

        Returns:
            str | None: An explanation when this chart's additional predicates no longer fit.
        """
        additional = 2 if query.compare_by is not None else 1
        if len(query.filters.dimensions) + additional > AttackAnalyticsFilters.MAX_PREDICATES:
            return (
                "Remove a dimension filter before drilling down further "
                f"(maximum {AttackAnalyticsFilters.MAX_PREDICATES} predicates)."
            )
        if (
            sum(len(predicate.values) for predicate in query.filters.dimensions) + additional
            > AttackAnalyticsFilters.MAX_VALUES
        ):
            return (
                "Select fewer dimension values before drilling down further "
                f"(maximum {AttackAnalyticsFilters.MAX_VALUES} values)."
            )
        return None

    @staticmethod
    def _drilldown(*, dimension: AttackAnalyticsDimension, key: AttackAnalyticsValue) -> AttackAnalyticsFilter:
        return AttackAnalyticsFilter(dimension=dimension, values=[key])

    @staticmethod
    def _value_key(value: AttackAnalyticsValue) -> tuple[AttackAnalyticsValueKind, str | None]:
        return value.kind, value.value

    @staticmethod
    def _copy(query: QueryT) -> QueryT:
        """
        Snapshot and revalidate nested mutable queries before consuming any admission capacity.

        Returns:
            QueryT: Independent normalized data, including UTC bounds and nested filter values.
        """
        return type(query).model_validate(query.model_dump())
