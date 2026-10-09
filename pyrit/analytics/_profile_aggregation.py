# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Finish bounded SQLite metadata profiles using the complete SQL path's membership rules."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pydantic import ValidationError

from pyrit.exceptions.analytics_exception import AnalyticsDataException
from pyrit.memory.attack_analytics import RawAnalyticsGroup, RawAnalyticsOption
from pyrit.models import AttackAnalyticsDimensionName, AttackAnalyticsValue, AttackAnalyticsValueKind

if TYPE_CHECKING:
    from pyrit.memory.attack_analytics import RawAnalyticsProfile, RawAnalyticsReport
    from pyrit.memory.query_control import QueryControl
    from pyrit.models import AttackAnalyticsDimension, AttackAnalyticsQuery

ValueKey = tuple[str, str]


@dataclass
class _Profile:
    """One saved-outcome weight with deduplicated memberships, not hydrated results."""

    values: list[dict[ValueKey, RawAnalyticsOption]]
    outcome: str
    weight: int


class ProfileAggregation:
    """
    Expand only the reader-approved SQLite profiles, never an unbounded result set.

    Memory caps profile count and text size and falls back to complete SQL grouping
    on overflow. Converter sources already contain canonical names, including
    legacy-name precedence. This layer does not normalize identifier objects,
    select results, change totals, or recompute target evaluation identities.
    """

    _CHECK_INTERVAL: ClassVar[int] = 128

    @classmethod
    async def populate_async(
        cls, *, report: RawAnalyticsReport, query: AttackAnalyticsQuery, control: QueryControl
    ) -> None:
        """
        Populate chart projections while leaving overall counts and result rows untouched.

        ``profiles=None`` means SQL already supplied the chart; [] is a valid empty
        cohort. Decoded options are cached only within this call. Bounded batches
        yield to the event loop so cancellation and other callers can progress.

        Args:
            report (RawAnalyticsReport): Reader output whose profiles passed every cap.
            query (AttackAnalyticsQuery): The same grouping and limits used by the reader.
            control (QueryControl): Shared execution budget, including SDK CPU work.
        """
        if report.profiles is None:
            return
        dimensions = [query.group_by] + ([query.compare_by] if query.compare_by is not None else [])
        profiles: list[_Profile] = []
        axes: list[dict[ValueKey, RawAnalyticsOption]] = [{} for _ in dimensions]
        cache: list[dict[str | None, dict[ValueKey, RawAnalyticsOption]]] = [{} for _ in dimensions]
        for index, record in enumerate(report.profiles):
            if index % cls._CHECK_INTERVAL == 0:
                await cls._checkpoint_async(control)
            profile = cls._profile(record=record, dimensions=dimensions, cache=cache)
            profiles.append(profile)
            for axis, values in zip(axes, profile.values, strict=True):
                for key, option in values.items():
                    cls._add_option(axis=axis, key=key, option=option)
        if query.compare_by is None:
            await cls._groups_async(report=report, query=query, profiles=profiles, axis=axes[0], control=control)
        else:
            await cls._matrix_async(report=report, query=query, profiles=profiles, axes=axes, control=control)
        control.check()

    @classmethod
    def _profile(
        cls,
        *,
        record: RawAnalyticsProfile,
        dimensions: list[AttackAnalyticsDimension],
        cache: list[dict[str | None, dict[ValueKey, RawAnalyticsOption]]],
    ) -> _Profile:
        if record["oversized"]:
            raise AnalyticsDataException("Oversized metadata profiles require complete SQL aggregation.")
        sources = [record["source0"]]
        if len(dimensions) == 2:
            if "source1" not in record:
                raise AnalyticsDataException("A comparison profile is missing its second dimension.")
            sources.append(record["source1"])
        values = []
        for raw, dimension, options in zip(sources, dimensions, cache, strict=True):
            if raw not in options:
                options[raw] = cls._values(raw=raw, dimension=dimension)
            values.append(options[raw])
        return _Profile(values=values, outcome=record["outcome"], weight=record["weight"])

    @classmethod
    def _values(cls, *, raw: str | None, dimension: AttackAnalyticsDimension) -> dict[ValueKey, RawAnalyticsOption]:
        """
        Decode scalar attack names or canonical category/converter name arrays.

        JSON null and null members mean missing metadata. Empty harm arrays are
        missing; empty converter arrays mean a known pipeline with no converters.
        Scalar attack names and real blank strings are not JSON absence markers.

        Returns:
            dict[ValueKey, RawAnalyticsOption]: One representative option per typed key.

        Raises:
            AnalyticsDataException: If an unsupported dimension or malformed array reaches this path.
        """
        if dimension.name is AttackAnalyticsDimensionName.ATTACK_TYPE:
            return cls._options([raw])
        if dimension.name not in {
            AttackAnalyticsDimensionName.TARGETED_HARM_CATEGORY,
            AttackAnalyticsDimensionName.CONVERTER_TYPE,
        }:
            raise AnalyticsDataException("This dimension does not support compact profile aggregation.")
        if raw is None or raw == "null":
            return cls._options([None])
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as error:
            raise AnalyticsDataException("Stored category/converter metadata is invalid JSON.") from error
        if not isinstance(values, list):
            raise AnalyticsDataException("Stored category/converter metadata is not an array.")
        if not values:
            kind = (
                AttackAnalyticsValueKind.NO_CONVERTERS
                if dimension.name is AttackAnalyticsDimensionName.CONVERTER_TYPE
                else AttackAnalyticsValueKind.MISSING
            )
            return {(kind.value, ""): RawAnalyticsOption(key=AttackAnalyticsValue(kind=kind), label=None)}
        return cls._options(values)

    @classmethod
    def _options(cls, values: list[object]) -> dict[ValueKey, RawAnalyticsOption]:
        """
        Match SQLite's registered UnicodeLower function, which uses Python str.lower.

        Do not use ASCII translation or casefold. Keys fold Unicode but display
        labels retain the binary-smallest original spelling, matching SQL MIN.

        Returns:
            dict[ValueKey, RawAnalyticsOption]: Deduplicated memberships.

        Raises:
            AnalyticsDataException: If a member is not text/NULL or its folded key exceeds the contract.
        """
        options: dict[ValueKey, RawAnalyticsOption] = {}
        for label in values:
            if label is not None and not isinstance(label, str):
                raise AnalyticsDataException("Stored category/converter metadata contains a non-string value.")
            try:
                key = (
                    AttackAnalyticsValue(kind=AttackAnalyticsValueKind.MISSING)
                    if label is None
                    else AttackAnalyticsValue(value=label.lower())
                )
            except ValidationError as error:
                raise AnalyticsDataException(
                    f"Stored attack metadata exceeds the {AttackAnalyticsValue.MAX_VALUE_LENGTH:,}-character "
                    "analytics key limit. Choose another dimension or inspect the saved result."
                ) from error
            cls._add_option(
                axis=options, key=(key.kind.value, key.value or ""), option=RawAnalyticsOption(key=key, label=label)
            )
        return options

    @classmethod
    def _add_option(
        cls, *, axis: dict[ValueKey, RawAnalyticsOption], key: ValueKey, option: RawAnalyticsOption
    ) -> None:
        axis[key] = cls._minimum_option(current=axis.get(key), option=option)

    @staticmethod
    def _minimum_option(*, current: RawAnalyticsOption | None, option: RawAnalyticsOption) -> RawAnalyticsOption:
        if current is None or (option.label is not None and (current.label is None or option.label < current.label)):
            return option
        return current

    @classmethod
    async def _groups_async(
        cls,
        *,
        report: RawAnalyticsReport,
        query: AttackAnalyticsQuery,
        profiles: list[_Profile],
        axis: dict[ValueKey, RawAnalyticsOption],
        control: QueryControl,
    ) -> None:
        counts: dict[ValueKey, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for index, profile in enumerate(profiles):
            if index % cls._CHECK_INTERVAL == 0:
                await cls._checkpoint_async(control)
            for key in profile.values[0]:
                counts[key][profile.outcome] += profile.weight
        ordered = sorted(counts, key=lambda key: (-sum(counts[key].values()), key))
        end = query.group_offset + query.group_limit
        report.has_more_groups = len(ordered) > end
        report.groups = [
            RawAnalyticsGroup(option=axis[key], counts=dict(counts[key])) for key in ordered[query.group_offset : end]
        ]

    @classmethod
    async def _matrix_async(
        cls,
        *,
        report: RawAnalyticsReport,
        query: AttackAnalyticsQuery,
        profiles: list[_Profile],
        axes: list[dict[ValueKey, RawAnalyticsOption]],
        control: QueryControl,
    ) -> None:
        """Bound axes before expanding pairs; preserve cell-local labels as well as axis labels."""
        selected = [set(sorted(axis)[: query.axis_limit]) for axis in axes]
        report.axes_truncated = any(len(axis) > query.axis_limit for axis in axes)
        report.rows = [axes[0][key] for key in sorted(selected[0])]
        report.columns = [axes[1][key] for key in sorted(selected[1])]
        cells: dict[tuple[ValueKey, ValueKey], RawAnalyticsGroup] = {}
        for index, profile in enumerate(profiles):
            if index % cls._CHECK_INTERVAL == 0:
                await cls._checkpoint_async(control)
            for row in profile.values[0].keys() & selected[0]:
                for column in profile.values[1].keys() & selected[1]:
                    pair = row, column
                    if pair not in cells:
                        cells[pair] = RawAnalyticsGroup(
                            option=profile.values[0][row], column=profile.values[1][column], counts={}
                        )
                    cell = cells[pair]
                    cell.counts[profile.outcome] = cell.counts.get(profile.outcome, 0) + profile.weight
                    cell.option = cls._minimum_option(current=cell.option, option=profile.values[0][row])
                    cell.column = cls._minimum_option(current=cell.column, option=profile.values[1][column])
        report.cells = [cells[pair] for pair in sorted(cells)]

    @staticmethod
    async def _checkpoint_async(control: QueryControl) -> None:
        await asyncio.sleep(0)
        control.check()
