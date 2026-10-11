# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy import text, update

from pyrit.analytics import AttackResultAnalytics
from pyrit.analytics._profile_aggregation import ProfileAggregation
from pyrit.exceptions.analytics_exception import AnalyticsDataException, AnalyticsTimeoutException
from pyrit.memory.attack_analytics import AttackAnalyticsReader, RawAnalyticsProfile, RawAnalyticsReport
from pyrit.memory.memory_models import AttackResultEntry
from pyrit.models import (
    AttackAnalyticsDimension,
    AttackAnalyticsQuery,
    AttackAnalyticsResults,
    AttackOutcome,
)
from unit.memory.test_attack_analytics import control, make_result

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory


@pytest.fixture
async def mixed_reader(sqlite_instance: SQLiteMemory) -> AttackAnalyticsReader:
    missing = make_result(index=5, outcome=AttackOutcome.ERROR, categories=["Unknown", ""])
    missing.atomic_attack_identifier = None
    results = [
        make_result(
            index=index,
            categories=["Privacy", "privacy", "", "Unknown"],
            converters=["Alpha", "ALPHA", ""],
            response_converters=["Beta"],
        )
        for index in (1, 2)
    ]
    results.extend(
        [
            make_result(
                index=3,
                outcome=AttackOutcome.FAILURE,
                categories=["privacy"],
                converters=["Alpha", "Gamma"],
                response_converters=["Beta", "BETA"],
            ),
            make_result(index=4, outcome=AttackOutcome.UNDETERMINED),
            missing,
            make_result(
                index=6,
                categories=["\u00c9vasion", "\u00e9vasion", "\u00df", "SS", "\u0130", "i\u0307"],
                converters=["\u00dcnicode", "\u00fcnicode"],
                response_converters=["Other"],
                attack_class_name="\u00c9cho",
            ),
            make_result(
                index=7,
                categories=["\u00e9vasion", "privacy"],
                converters=["\u00fcnicode", "Beta"],
                response_converters=["Gamma"],
                attack_class_name="\u00e9cho",
            ),
            make_result(index=8, categories=["legacy"]),
            make_result(index=9, categories=["privacy"], converters=["Beta"]),
        ]
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    legacy = {
        "children": {
            "attack": {
                "__type__": "LegacyAttack",
                "children": {
                    "request_converters": [
                        {"__type__": "\u00c9vasion"},
                        {"class_name": "\u00e9vasion", "__type__": "Ignored"},
                        {"class_name": None, "__type__": "AlsoIgnored"},
                        {},
                    ],
                    "response_converters": [],
                },
            }
        }
    }
    async with sqlite_instance._get_async_engine().begin() as connection:
        await connection.execute(
            update(AttackResultEntry)
            .where(AttackResultEntry.id == results[-2].attack_result_id)
            .values(atomic_attack_identifier_hash=None, atomic_attack_identifier=legacy)
        )
        await connection.execute(
            update(AttackResultEntry)
            .where(AttackResultEntry.id == results[-1].attack_result_id)
            .values(atomic_attack_identifier_hash=None)
        )
    return AttackAnalyticsReader(memory=sqlite_instance)


def _assert_same_chart(*, actual: RawAnalyticsReport, expected: RawAnalyticsReport) -> None:
    assert actual.counts == expected.counts
    assert actual.groups == expected.groups
    assert actual.rows == expected.rows
    assert actual.columns == expected.columns
    assert actual.cells == expected.cells
    assert actual.has_more_groups == expected.has_more_groups
    assert actual.axes_truncated == expected.axes_truncated
    assert actual.results.items == expected.results.items


@pytest.mark.parametrize(
    "options",
    [
        {"group_by": {"name": "targeted_harm_category"}, "group_limit": 3, "group_offset": 1},
        {"group_by": {"name": "targeted_harm_category"}, "group_offset": 50},
        {"group_by": {"name": "converter_type"}, "group_limit": 3, "group_offset": 1},
        {"group_by": {"name": "attack_type"}, "group_limit": 1, "group_offset": 1},
        {
            "group_by": {"name": "targeted_harm_category"},
            "compare_by": {"name": "converter_type"},
            "axis_limit": 2,
        },
        {
            "group_by": {"name": "targeted_harm_category"},
            "compare_by": {"name": "attack_type"},
            "axis_limit": 2,
        },
        {"group_by": {"name": "attack_type"}, "compare_by": {"name": "converter_type"}},
        {
            "group_by": {"name": "converter_type"},
            "compare_by": {"name": "converter_type", "converter_direction": "response"},
        },
        {
            "group_by": {"name": "targeted_harm_category"},
            "compare_by": {"name": "converter_type"},
            "filters": {
                "dimensions": [
                    {"dimension": {"name": "converter_type"}, "values": [{"value": "Alpha"}, {"value": "Other"}]},
                    {"dimension": {"name": "converter_type"}, "values": [{"value": "Gamma"}]},
                ]
            },
        },
        {
            "group_by": {"name": "targeted_harm_category"},
            "compare_by": {"name": "converter_type"},
            "filters": {"outcomes": ["error"]},
        },
        {"group_by": {"name": "converter_type"}, "filters": {"outcomes": ["failure", "undetermined"]}},
    ],
)
async def test_weighted_unicode_and_legacy_profiles_equal_actual_sql(
    mixed_reader: AttackAnalyticsReader, options: dict[str, Any]
) -> None:
    query = AttackAnalyticsQuery.model_validate(options)
    sql = await mixed_reader.report_async(query=query, control=control())
    compact = await mixed_reader.report_async(query=query, control=control(), use_compact_profiles=True)
    assert compact.profiles is not None
    sources = deepcopy(compact.profiles)
    await ProfileAggregation.populate_async(report=compact, query=query, control=control())
    _assert_same_chart(actual=compact, expected=sql)
    assert compact.profiles == sources


@pytest.mark.parametrize("compare", [None, "converter_type", "attack_type"])
@pytest.mark.parametrize("cap", ["MAX_COMPACT_PROFILES", "MAX_COMPACT_VALUE_LENGTH", "MAX_COMPACT_TOTAL_LENGTH"])
@pytest.mark.parametrize("overflow", [False, True], ids=["at_cap", "overflow"])
async def test_exact_reader_cap_and_overflow_keep_complete_sql_parity(
    mixed_reader: AttackAnalyticsReader, compare: str | None, cap: str, overflow: bool
) -> None:
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
        compare_by=AttackAnalyticsDimension(name=compare) if compare else None,
        axis_limit=2,
        group_limit=2,
    )
    sql = await mixed_reader.report_async(query=query, control=control())
    probe = await mixed_reader.report_async(query=query, control=control(), use_compact_profiles=True)
    assert probe.profiles
    boundaries = {
        "MAX_COMPACT_PROFILES": len(probe.profiles),
        "MAX_COMPACT_VALUE_LENGTH": max(
            max(len(profile["source0"] or ""), len(profile.get("source1") or "")) for profile in probe.profiles
        ),
        "MAX_COMPACT_TOTAL_LENGTH": sum(
            len(value) for profile in probe.profiles for value in profile.values() if isinstance(value, str)
        ),
    }
    with patch.object(AttackAnalyticsReader, cap, boundaries[cap] - int(overflow)):
        result = await mixed_reader.report_async(query=query, control=control(), use_compact_profiles=True)
    assert (result.profiles is None) == overflow
    await ProfileAggregation.populate_async(report=result, query=query, control=control())
    _assert_same_chart(actual=result, expected=sql)


@pytest.mark.parametrize("compare", [None, "converter_type", "attack_type"])
async def test_sdk_report_matches_sql_fallback(
    mixed_reader: AttackAnalyticsReader, sqlite_instance: SQLiteMemory, compare: str | None
) -> None:
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
        compare_by=AttackAnalyticsDimension(name=compare) if compare else None,
    )
    async with AttackResultAnalytics(memory=sqlite_instance) as analytics:
        fast = await analytics.query_async(query=query)
        with patch.object(AttackAnalyticsReader, "MAX_COMPACT_PROFILES", 0):
            sql = await analytics.query_async(query=query)
    fields = {
        "summary",
        "groups",
        "rows",
        "columns",
        "cells",
        "groups_overlap",
        "axes_truncated",
        "has_more_groups",
        "next_group_offset",
        "outcome_filter_applied",
        "drilldown_unavailable_reason",
    }
    assert fast.model_dump(include=fields) == sql.model_dump(include=fields)


@pytest.mark.parametrize("compare", [False, True])
async def test_empty_profile_cohort_is_not_sql_fallback(sqlite_instance: SQLiteMemory, compare: bool) -> None:
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="converter_type"),
        compare_by=AttackAnalyticsDimension(name="attack_type") if compare else None,
    )
    raw = await reader.report_async(query=query, control=control(), use_compact_profiles=True)
    assert raw.profiles == []
    await ProfileAggregation.populate_async(report=raw, query=query, control=control())
    assert raw.groups == raw.rows == raw.columns == raw.cells == []
    assert not raw.has_more_groups and not raw.axes_truncated


async def test_non_categorical_dimension_uses_complete_sql(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result()])
    raw = await AttackAnalyticsReader(memory=sqlite_instance).report_async(
        query=AttackAnalyticsQuery(), control=control(), use_compact_profiles=True
    )
    assert raw.profiles is None
    original = deepcopy(raw)
    await ProfileAggregation.populate_async(report=raw, query=AttackAnalyticsQuery(), control=control())
    assert raw == original


@pytest.mark.parametrize("raw", ["null", " \t null\n"])
async def test_profile_null_classification_matches_sql_not_json_decoder_defaults(
    sqlite_instance: SQLiteMemory, raw: str
) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[make_result()])
    async with sqlite_instance._get_async_engine().begin() as connection:
        await connection.execute(text("UPDATE AttackResultEntries SET targeted_harm_categories = :raw"), {"raw": raw})
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    query = AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="targeted_harm_category"))
    compact = await reader.report_async(query=query, control=control(), use_compact_profiles=True)
    if raw == "null":
        sql = await reader.report_async(query=query, control=control())
        await ProfileAggregation.populate_async(report=compact, query=query, control=control())
        _assert_same_chart(actual=compact, expected=sql)
    else:
        with pytest.raises(AnalyticsDataException):
            await reader.report_async(query=query, control=control())
        with pytest.raises(AnalyticsDataException):
            await ProfileAggregation.populate_async(report=compact, query=query, control=control())


@pytest.mark.parametrize(
    ("dimension", "raw", "expected"),
    [
        ("converter_type", None, {("missing", ""): None}),
        ("converter_type", "null", {("missing", ""): None}),
        ("converter_type", " \t[\r\n ] ", {("no_converters", ""): None}),
        ("targeted_harm_category", "[]", {("missing", ""): None}),
        ("converter_type", "[null, null]", {("missing", ""): None}),
        ("converter_type", '["", "Unknown", "unknown"]', {("value", ""): "", ("value", "unknown"): "Unknown"}),
        ("attack_type", "", {("value", ""): ""}),
        ("attack_type", "null", {("value", "null"): "null"}),
        (
            "targeted_harm_category",
            '["\u00c9vasion", "\u00e9vasion", "SS", "\u00df", "\u0130", "i\u0307"]',
            {
                ("value", "\u00e9vasion"): "\u00c9vasion",
                ("value", "ss"): "SS",
                ("value", "\u00df"): "\u00df",
                ("value", "i\u0307"): "i\u0307",
            },
        ),
    ],
)
def test_profile_keys_use_unicode_lower_not_ascii_or_casefold(
    dimension: str, raw: str | None, expected: dict[tuple[str, str], str | None]
) -> None:
    values = ProfileAggregation._values(raw=raw, dimension=AttackAnalyticsDimension(name=dimension))
    assert {key: option.label for key, option in values.items()} == expected


@pytest.mark.parametrize(
    "raw", ['{"class_name": "Alpha"}', '"Alpha"', "true", "[1]", "[true]", "not json", '[{"__type__": "Alpha"}]']
)
def test_profile_decoder_rejects_noncanonical_arrays(raw: str) -> None:
    with pytest.raises(AnalyticsDataException):
        ProfileAggregation._values(raw=raw, dimension=AttackAnalyticsDimension(name="converter_type"))


def test_profile_decoder_rejects_lowercase_expansion_beyond_key_limit() -> None:
    with pytest.raises(AnalyticsDataException, match="4,096-character"):
        ProfileAggregation._values(raw="\u0130" * 4096, dimension=AttackAnalyticsDimension(name="attack_type"))


def _report(profiles: list[RawAnalyticsProfile]) -> RawAnalyticsReport:
    return RawAnalyticsReport(
        counts={},
        groups=[],
        rows=[],
        columns=[],
        cells=[],
        has_more_groups=False,
        axes_truncated=False,
        results=AttackAnalyticsResults(items=[], has_more=False, next_cursor=None, computed_at=datetime.now(tz=UTC)),
        warnings=[],
        profiles=profiles,
    )


async def test_decoder_cache_is_call_local_and_never_caches_counts() -> None:
    records: list[RawAnalyticsProfile] = [
        {"source0": '["Alpha", "Alpha"]', "weight": 5, "outcome": "success", "oversized": False},
        {"source0": '["Alpha", "Alpha"]', "weight": 2, "outcome": "failure", "oversized": False},
        {"source0": '["ALPHA"]', "weight": 3, "outcome": "success", "oversized": False},
    ]
    query = AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type"))
    report = _report(records)
    with patch.object(ProfileAggregation, "_values", wraps=ProfileAggregation._values) as decode:
        await ProfileAggregation.populate_async(report=report, query=query, control=control())
        assert decode.call_count == 2
        assert report.groups[0].counts == {"success": 8, "failure": 2}
        assert report.groups[0].option.label == "ALPHA"
        records[0]["weight"] = 9
        await ProfileAggregation.populate_async(report=report, query=query, control=control())
        assert decode.call_count == 4
        assert report.groups[0].counts == {"success": 12, "failure": 2}


async def test_cancelled_control_stops_before_decoding() -> None:
    query_control = control()
    query_control.cancel()
    with patch.object(ProfileAggregation, "_profile", side_effect=AssertionError("Must not decode")):
        with pytest.raises(AnalyticsTimeoutException):
            await ProfileAggregation.populate_async(
                report=_report([{"source0": "[]", "weight": 1, "outcome": "success", "oversized": False}]),
                query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type")),
                control=query_control,
            )


@pytest.mark.parametrize("oversized", [False, True])
async def test_incomplete_or_oversized_profiles_are_not_partial_charts(oversized: bool) -> None:
    with pytest.raises(AnalyticsDataException, match="Oversized|second dimension"):
        await ProfileAggregation.populate_async(
            report=_report([{"source0": "[]", "weight": 1, "outcome": "success", "oversized": oversized}]),
            query=AttackAnalyticsQuery(
                group_by=AttackAnalyticsDimension(name="converter_type"),
                compare_by=AttackAnalyticsDimension(name="attack_type"),
            ),
            control=control(),
        )


async def test_full_profile_budget_yields_and_keeps_weighted_totals() -> None:
    records: list[RawAnalyticsProfile] = [
        {
            "source0": json.dumps([f"category-{index % 64}", "shared", "SHARED"]),
            "source1": json.dumps([f"converter-{index // 64}", "shared", "shared"]),
            "weight": 25,
            "outcome": list(AttackOutcome)[index % 4].value,
            "oversized": False,
        }
        for index in range(AttackAnalyticsReader.MAX_COMPACT_PROFILES)
    ]
    query = AttackAnalyticsQuery(
        group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
        compare_by=AttackAnalyticsDimension(name="converter_type"),
    )
    done = asyncio.Event()
    pulses = 0

    async def heartbeat_async() -> None:
        nonlocal pulses
        while not done.is_set():
            pulses += 1
            await asyncio.sleep(0)

    heartbeat = asyncio.create_task(heartbeat_async())
    report = _report(records)
    report.counts = {outcome.value: 25_600 for outcome in AttackOutcome}
    try:
        await ProfileAggregation.populate_async(report=report, query=query, control=control())
    finally:
        done.set()
        await heartbeat
    assert pulses > 1
    assert report.counts == {outcome.value: 25_600 for outcome in AttackOutcome}
    assert report.axes_truncated
    assert len(report.rows) == len(report.columns) == 20
    assert len(report.cells) == 400
    assert all(sum(cell.counts.values()) == 25 for cell in report.cells)
