# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy import literal, select, update
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.sql import Select, visitors

from pyrit.memory.alembic.versions.e5f7a9c1b3d2_add_identifiers_tables import IdentifierGraphInserter
from pyrit.memory.analytics_sql import JsonClassNamePresent, JsonScalar
from pyrit.memory.attack_analytics import AttackAnalyticsReader, RawAnalyticsReport
from pyrit.memory.attack_analytics_query import AttackAnalyticsQueryCompiler
from pyrit.memory.memory_models import (
    AttackIdentifierEntry,
    AttackRequestConverterIdentifierEntry,
    AttackResponseConverterIdentifierEntry,
    AttackResultEntry,
    ConverterIdentifierEntry,
    ScenarioResultEntry,
    TargetIdentifierEntry,
)
from pyrit.memory.query_control import QueryControl
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackAnalyticsDimension,
    AttackAnalyticsDimensionName,
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFilter,
    AttackAnalyticsFilters,
    AttackAnalyticsQuery,
    AttackAnalyticsValue,
    AttackAnalyticsValueKind,
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    ConverterIdentifier,
    TargetIdentifier,
)

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory


def _result(
    *,
    index: int = 1,
    request: tuple[str, ...] = (),
    response: tuple[str, ...] = (),
    labels: dict[str, str] | None = None,
) -> AttackResult:
    return AttackResult(
        attack_result_id=str(uuid.UUID(int=index)),
        conversation_id="metadata-conversation",
        objective="Metadata coverage",
        outcome=AttackOutcome.SUCCESS,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        labels=labels or {},
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="ProbeAttack",
                class_module="tests",
                objective_target=TargetIdentifier(class_name="ProbeTarget", class_module="tests", model_name="model-a"),
                request_converters=[ConverterIdentifier(class_name=name, class_module="tests") for name in request],
                response_converters=[ConverterIdentifier(class_name=name, class_module="tests") for name in response],
            )
        ),
    )


def _control() -> QueryControl:
    return QueryControl(deadline=time.monotonic() + 30)


def _filters(*, values: list[tuple[AttackAnalyticsDimension, str]]) -> AttackAnalyticsFilters:
    return AttackAnalyticsFilters(
        dimensions=[
            AttackAnalyticsFilter(dimension=dimension, values=[AttackAnalyticsValue(value=value)])
            for dimension, value in values
        ]
    )


def _keys(report: RawAnalyticsReport) -> dict[str | None, dict[str, int]]:
    return {group.option.key.value: group.counts for group in report.groups}


def test_migrated_hashless_converters_keep_both_directions_in_all_projections(sqlite_instance: SQLiteMemory) -> None:
    result = _result(request=("Alpha", "Beta"), response=("Gamma", "Delta"))
    assert result.atomic_attack_identifier is not None
    document = AtomicAttackIdentifier.from_component_identifier(result.atomic_attack_identifier).model_dump()
    attack = document["children"]["attack_technique"]["children"]["attack"]
    for direction in ("request", "response"):
        attack["children"][f"{direction}_converters"][1].pop("hash")

    with sqlite_instance.engine.begin() as connection:
        assert IdentifierGraphInserter(bind=connection).insert_atomic_attack(identifier=document) == document["hash"]
        for table in (AttackRequestConverterIdentifierEntry, AttackResponseConverterIdentifierEntry):
            assert connection.execute(
                select(table.position).where(table.attack_identifier_hash == attack["hash"])
            ).scalars().all() == [0]
        connection.execute(
            AttackResultEntry.__table__.insert().values(
                id=uuid.UUID(result.attack_result_id),
                conversation_id=result.conversation_id,
                objective=result.objective,
                outcome=result.outcome.value,
                timestamp=result.timestamp,
                atomic_attack_identifier=document,
                atomic_attack_identifier_hash=document["hash"],
            )
        )

    with sqlite_instance.get_session() as session:
        restored = session.get(AttackResultEntry, uuid.UUID(result.attack_result_id)).get_attack_result()
    assert restored.atomic_attack_identifier is not None
    restored_atomic = AtomicAttackIdentifier.from_component_identifier(restored.atomic_attack_identifier)
    assert restored_atomic.attack_technique is not None
    restored_attack = restored_atomic.attack_technique.attack
    assert restored_attack is not None
    assert [item.class_name for item in restored_attack.request_converters] == ["Alpha", "Beta"]
    assert [item.class_name for item in restored_attack.response_converters] == ["Gamma", "Delta"]

    reader = AttackAnalyticsReader(memory=sqlite_instance)
    request = AttackAnalyticsDimension(name="converter_type")
    response = AttackAnalyticsDimension(name="converter_type", converter_direction="response")
    for dimension, expected in ((request, {"alpha", "beta"}), (response, {"gamma", "delta"})):
        report = reader.report(query=AttackAnalyticsQuery(group_by=dimension), control=_control())
        assert _keys(report) == {name: {"success": 1} for name in expected}

    query = AttackAnalyticsQuery(group_by=request, compare_by=response)
    report = reader.report(query=query, control=_control())
    assert {(cell.option.key.value, cell.column.key.value) for cell in report.cells} == {
        (first, second) for first in ("alpha", "beta") for second in ("gamma", "delta")
    }
    assert all(cell.counts == {"success": 1} for cell in report.cells)

    filters = _filters(values=[(request, "Beta"), (response, "Delta")])
    filtered = reader.report(query=AttackAnalyticsQuery(filters=filters, group_by=request), control=_control())
    assert filtered.counts == {"success": 1}
    assert filtered.results.items[0].request_converters == ["Alpha", "Beta"]
    assert filtered.results.items[0].response_converters == ["Delta", "Gamma"]
    for dimension, expected in ((request, {"alpha", "beta"}), (response, {"delta", "gamma"})):
        facet = reader.facets(query=AttackAnalyticsFacetQuery(filters=filters, dimension=dimension), control=_control())
        assert {item.key.value for item in facet.items} == expected

    profiles = reader.report(query=query, control=_control(), use_compact_profiles=True)
    assert profiles.profiles is not None
    assert len(profiles.profiles) == 1
    assert set(json.loads(profiles.profiles[0]["source0"])) == {"Alpha", "Beta"}
    assert set(json.loads(profiles.profiles[0]["source1"])) == {"Gamma", "Delta"}
    with patch.object(reader, "MAX_COMPACT_VALUE_LENGTH", 1):
        fallback = reader.report(query=query, control=_control(), use_compact_profiles=True)
    assert fallback.profiles is None
    assert fallback.cells == report.cells


@pytest.mark.parametrize(
    ("attack_document", "target_document"),
    [
        (None, None),
        ({}, {}),
        ({"class_name": "ProbeAttack"}, {"__type__": "PartialTarget"}),
    ],
    ids=["json-null", "empty-object", "partial-object"],
)
def test_compaction_keeps_embedded_attack_target_and_converter_metadata(
    *, sqlite_instance: SQLiteMemory, attack_document: dict[str, Any] | None, target_document: dict[str, Any] | None
) -> None:
    result = _result(request=("Alpha",), response=("Beta",), labels={"team": "blue"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[result])
    scenario_id = uuid.UUID(int=101)
    with sqlite_instance.engine.begin() as connection:
        connection.execute(
            ScenarioResultEntry.__table__.insert().values(
                id=scenario_id,
                scenario_name="Metadata scenario",
                pyrit_version="test",
                scenario_identifier={},
                objective_target_identifier={},
                completion_time=result.timestamp,
                timestamp=result.timestamp,
            )
        )
        connection.execute(update(AttackResultEntry).values(attribution_parent_id=scenario_id))
        connection.execute(update(AttackIdentifierEntry).values(identifier_json=attack_document, class_name=None))
        connection.execute(
            update(TargetIdentifierEntry).values(
                identifier_json=target_document, class_name=None, model_name=None, underlying_model_name=None
            )
        )

    reader = AttackAnalyticsReader(memory=sqlite_instance)
    attack = AttackAnalyticsDimension(name="attack_type")
    target = AttackAnalyticsDimension(name="objective_target")
    model = AttackAnalyticsDimension(name="model")
    request = AttackAnalyticsDimension(name="converter_type")
    response = AttackAnalyticsDimension(name="converter_type", converter_direction="response")
    assert result.atomic_attack_identifier is not None
    atomic = AtomicAttackIdentifier.from_component_identifier(result.atomic_attack_identifier)
    assert atomic.attack_technique is not None
    assert atomic.attack_technique.attack is not None
    assert atomic.attack_technique.attack.objective_target is not None
    target_hash = atomic.attack_technique.attack.objective_target.hash
    for dimension, value in (
        (attack, "probeattack"),
        (target, target_hash),
        (model, "model-a"),
        (request, "alpha"),
        (response, "beta"),
    ):
        report = reader.report(query=AttackAnalyticsQuery(group_by=dimension), control=_control())
        assert _keys(report) == {value: {"success": 1}}
        if dimension == target:
            assert report.groups[0].option.label == "model-a"
        matching = reader.report(
            query=AttackAnalyticsQuery(filters=_filters(values=[(dimension, value)])), control=_control()
        )
        assert matching.counts == {"success": 1}

    label = AttackAnalyticsDimension(name="label", label_key="team")
    scenario = AttackAnalyticsDimension(name="scenario")
    for first, second, first_value, second_value in (
        (scenario, request, str(scenario_id), "alpha"),
        (label, model, "blue", "model-a"),
        (label, target, "blue", target_hash),
    ):
        matrix = reader.report(query=AttackAnalyticsQuery(group_by=first, compare_by=second), control=_control())
        assert [(cell.option.key.value, cell.column.key.value, cell.counts) for cell in matrix.cells] == [
            (first_value, second_value, {"success": 1})
        ]
    facet = reader.facets(query=AttackAnalyticsFacetQuery(dimension=response), control=_control())
    assert [item.key.value for item in facet.items] == ["beta"]
    converter_query = AttackAnalyticsQuery(group_by=request)
    profiles = reader.report(query=converter_query, control=_control(), use_compact_profiles=True)
    assert profiles.profiles is not None
    assert json.loads(profiles.profiles[0]["source0"]) == ["Alpha"]
    with patch.object(reader, "MAX_COMPACT_VALUE_LENGTH", 1):
        fallback = reader.report(query=converter_query, control=_control(), use_compact_profiles=True)
    assert fallback.profiles is None
    assert _keys(fallback) == {"alpha": {"success": 1}}
    row = reader.report(query=AttackAnalyticsQuery(), control=_control()).results.items[0]
    assert (row.attack_type, row.target_model, row.request_converters, row.response_converters) == (
        "ProbeAttack",
        "model-a",
        ["Alpha"],
        ["Beta"],
    )


@pytest.mark.parametrize("wrapped", [False, True], ids=["direct", "technique-wrapped"])
def test_legacy_type_key_resolves_filters_groups_facets_and_results(
    *, sqlite_instance: SQLiteMemory, wrapped: bool
) -> None:
    attack = {
        "__type__": "LegacyAttack",
        "children": {
            "objective_target": {"hash": "t" * 64, "__type__": "LegacyTarget"},
            "request_converters": [{"__type__": "LegacyRequest"}],
            "response_converters": [
                {"class_name": "CanonicalResponse", "__type__": "IgnoredResponse"},
                {"__type__": "LegacyResponse"},
            ],
        },
    }
    document = (
        {"children": {"attack_technique": {"children": {"attack": attack}}}}
        if wrapped
        else {"children": {"attack": attack}}
    )
    with sqlite_instance.engine.begin() as connection:
        connection.execute(
            AttackResultEntry.__table__.insert().values(
                id=uuid.UUID(int=1),
                conversation_id="legacy-metadata",
                objective="Legacy metadata",
                outcome="success",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                atomic_attack_identifier=document,
                atomic_attack_identifier_hash=None,
            )
        )

    reader = AttackAnalyticsReader(memory=sqlite_instance)
    attack_dimension = AttackAnalyticsDimension(name="attack_type")
    target_dimension = AttackAnalyticsDimension(name="objective_target")
    request = AttackAnalyticsDimension(name="converter_type")
    response = AttackAnalyticsDimension(name="converter_type", converter_direction="response")
    matrix = reader.report(
        query=AttackAnalyticsQuery(group_by=attack_dimension, compare_by=request), control=_control()
    )
    assert [(cell.option.key.value, cell.column.key.value, cell.counts) for cell in matrix.cells] == [
        ("legacyattack", "legacyrequest", {"success": 1})
    ]
    assert _keys(reader.report(query=AttackAnalyticsQuery(group_by=target_dimension), control=_control())) == {
        "t" * 64: {"success": 1}
    }
    target_group = reader.report(query=AttackAnalyticsQuery(group_by=target_dimension), control=_control()).groups[0]
    assert target_group.option.label == "LegacyTarget"
    facet = reader.facets(query=AttackAnalyticsFacetQuery(dimension=response), control=_control())
    assert {item.key.value for item in facet.items} == {"canonicalresponse", "legacyresponse"}
    filters = _filters(
        values=[(attack_dimension, "LegacyAttack"), (request, "LegacyRequest"), (response, "LegacyResponse")]
    )
    report = reader.report(query=AttackAnalyticsQuery(filters=filters, group_by=request), control=_control())
    assert report.counts == {"success": 1}
    assert (report.results.items[0].attack_type, report.results.items[0].target_model) == (
        "LegacyAttack",
        None,
    )
    assert report.results.items[0].request_converters == ["LegacyRequest"]
    assert report.results.items[0].response_converters == ["CanonicalResponse", "LegacyResponse"]
    raw_profiles = reader.report(
        query=AttackAnalyticsQuery(group_by=request, compare_by=response),
        control=_control(),
        use_compact_profiles=True,
    )
    assert raw_profiles.profiles is not None
    assert json.loads(raw_profiles.profiles[0]["source0"]) == ["LegacyRequest"]
    assert set(json.loads(raw_profiles.profiles[0]["source1"])) == {"CanonicalResponse", "LegacyResponse"}


def test_edge_names_use_canonical_key_before_legacy_type_without_retained_lists(
    sqlite_instance: SQLiteMemory,
) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[_result(request=("OriginalRequest",), response=("OriginalResponse",))]
    )
    with sqlite_instance.engine.begin() as connection:
        connection.execute(update(AttackResultEntry).values(atomic_attack_identifier={"hash": "a" * 64}))
        connection.execute(
            update(AttackIdentifierEntry).values(class_name=None, identifier_json={"__type__": "NormalizedAttack"})
        )
        connection.execute(
            update(ConverterIdentifierEntry)
            .where(ConverterIdentifierEntry.class_name == "OriginalRequest")
            .values(class_name=None, identifier_json={"__type__": "LegacyEdge"})
        )
        connection.execute(
            update(ConverterIdentifierEntry)
            .where(ConverterIdentifierEntry.class_name == "OriginalResponse")
            .values(class_name=None, identifier_json={"class_name": "CanonicalEdge", "__type__": "IgnoredEdge"})
        )
        connection.execute(
            update(TargetIdentifierEntry).values(
                class_name=None,
                model_name=None,
                underlying_model_name=None,
                identifier_json={"__type__": "LegacyTarget"},
            )
        )

    reader = AttackAnalyticsReader(memory=sqlite_instance)
    request = AttackAnalyticsDimension(name="converter_type")
    response = AttackAnalyticsDimension(name="converter_type", converter_direction="response")
    filters = _filters(values=[(request, "LegacyEdge"), (response, "CanonicalEdge")])
    report = reader.report(
        query=AttackAnalyticsQuery(filters=filters, group_by=request, compare_by=response), control=_control()
    )
    assert report.counts == {"success": 1}
    assert [(cell.option.key.value, cell.column.key.value) for cell in report.cells] == [
        ("legacyedge", "canonicaledge")
    ]
    assert report.results.items[0].request_converters == ["LegacyEdge"]
    assert report.results.items[0].response_converters == ["CanonicalEdge"]
    assert report.results.items[0].attack_type == "NormalizedAttack"
    attack_dimension = AttackAnalyticsDimension(name="attack_type")
    assert _keys(reader.report(query=AttackAnalyticsQuery(group_by=attack_dimension), control=_control())) == {
        "normalizedattack": {"success": 1}
    }
    assert reader.report(
        query=AttackAnalyticsQuery(filters=_filters(values=[(attack_dimension, "NormalizedAttack")])),
        control=_control(),
    ).counts == {"success": 1}
    target_group = reader.report(
        query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="objective_target")), control=_control()
    ).groups[0]
    assert target_group.option.label == "LegacyTarget"


def test_present_canonical_names_block_embedded_legacy_fallback(sqlite_instance: SQLiteMemory) -> None:
    document = {
        "children": {
            "attack": {
                "class_name": "CanonicalAttack",
                "__type__": "IgnoredAttack",
                "children": {
                    "objective_target": {
                        "hash": "t" * 64,
                        "class_name": "CanonicalTarget",
                        "__type__": "IgnoredTarget",
                    },
                    "request_converters": [{"class_name": None, "__type__": "IgnoredRequest"}],
                    "response_converters": [{"class_name": "CanonicalResponse", "__type__": "IgnoredResponse"}],
                },
            }
        }
    }
    with sqlite_instance.engine.begin() as connection:
        connection.execute(
            AttackResultEntry.__table__.insert().values(
                id=uuid.UUID(int=1),
                conversation_id="canonical-presence",
                objective="Canonical names",
                outcome="success",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                atomic_attack_identifier=document,
            )
        )
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    request = AttackAnalyticsDimension(name="converter_type")
    response = AttackAnalyticsDimension(name="converter_type", converter_direction="response")
    attack = AttackAnalyticsDimension(name="attack_type")
    assert _keys(reader.report(query=AttackAnalyticsQuery(group_by=attack), control=_control())) == {
        "canonicalattack": {"success": 1}
    }
    target = reader.report(
        query=AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="objective_target")), control=_control()
    ).groups[0]
    assert target.option.label == "CanonicalTarget"
    missing = reader.report(query=AttackAnalyticsQuery(group_by=request), control=_control()).groups[0]
    assert missing.option.key.kind is AttackAnalyticsValueKind.MISSING
    assert _keys(reader.report(query=AttackAnalyticsQuery(group_by=response), control=_control())) == {
        "canonicalresponse": {"success": 1}
    }
    ignored = reader.report(
        query=AttackAnalyticsQuery(filters=_filters(values=[(request, "IgnoredRequest")])), control=_control()
    )
    assert ignored.counts == {}
    row = reader.report(query=AttackAnalyticsQuery(), control=_control()).results.items[0]
    assert row.request_converters == []
    assert row.response_converters == ["CanonicalResponse"]


def test_present_null_name_in_normalized_edge_does_not_select_legacy_type(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[_result(request=("Request",), response=("Response",))])
    with sqlite_instance.engine.begin() as connection:
        connection.execute(update(AttackResultEntry).values(atomic_attack_identifier={"hash": "other"}))
        connection.execute(update(AttackIdentifierEntry).values(identifier_json={"class_name": "ProbeAttack"}))
        connection.execute(
            update(ConverterIdentifierEntry)
            .where(ConverterIdentifierEntry.class_name == "Request")
            .values(class_name=None, identifier_json={"class_name": None, "__type__": "IgnoredRequest"})
        )
        connection.execute(
            update(ConverterIdentifierEntry)
            .where(ConverterIdentifierEntry.class_name == "Response")
            .values(class_name=None, identifier_json={"__type__": "LegacyResponse"})
        )

    reader = AttackAnalyticsReader(memory=sqlite_instance)
    request = AttackAnalyticsDimension(name="converter_type")
    response = AttackAnalyticsDimension(name="converter_type", converter_direction="response")
    report = reader.report(query=AttackAnalyticsQuery(group_by=request), control=_control())
    assert report.groups[0].option.key.kind == AttackAnalyticsValueKind.MISSING
    assert _keys(reader.report(query=AttackAnalyticsQuery(group_by=response), control=_control())) == {
        "legacyresponse": {"success": 1}
    }
    ignored = reader.report(
        query=AttackAnalyticsQuery(filters=_filters(values=[(request, "IgnoredRequest")])), control=_control()
    )
    assert ignored.counts == {}
    assert reader.report(
        query=AttackAnalyticsQuery(filters=_filters(values=[(response, "LegacyResponse")])), control=_control()
    ).counts == {"success": 1}
    row = report.results.items[0]
    assert row.request_converters == []
    assert row.response_converters == ["LegacyResponse"]


def test_empty_pipelines_remain_distinct_from_missing_in_both_directions(sqlite_instance: SQLiteMemory) -> None:
    missing = _result(index=2)
    missing.atomic_attack_identifier = None
    sqlite_instance.add_attack_results_to_memory(attack_results=[_result(), missing])
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    for direction in ("request", "response"):
        dimension = AttackAnalyticsDimension(name="converter_type", converter_direction=direction)
        report = reader.report(query=AttackAnalyticsQuery(group_by=dimension), control=_control())
        assert {group.option.key.kind.value: group.counts for group in report.groups} == {
            "missing": {"success": 1},
            "no_converters": {"success": 1},
        }
        profiled = reader.report(
            query=AttackAnalyticsQuery(group_by=dimension), control=_control(), use_compact_profiles=True
        )
        assert profiled.profiles is not None
        assert {profile["source0"] for profile in profiled.profiles} == {None, "[]"}
        for kind, expected_id in (
            (AttackAnalyticsValueKind.MISSING, missing.attack_result_id),
            (AttackAnalyticsValueKind.NO_CONVERTERS, str(uuid.UUID(int=1))),
        ):
            filters = AttackAnalyticsFilters(
                dimensions=[AttackAnalyticsFilter(dimension=dimension, values=[AttackAnalyticsValue(kind=kind)])]
            )
            selected = reader.report(query=AttackAnalyticsQuery(filters=filters), control=_control())
            assert selected.counts == {"success": 1}
            assert [row.attack_result_id for row in selected.results.items] == [expected_id]


def test_complete_normalized_facts_compact_without_copying_result_identifiers(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            _result(index=1, request=("Alpha",), labels={"team": "blue"}),
            _result(index=2, request=("Alpha",), labels={"team": "blue"}),
        ]
    )
    dimensions = [
        AttackAnalyticsDimension(name="label", label_key="team"),
        AttackAnalyticsDimension(name="converter_type"),
    ]

    def facts() -> list[tuple[int, Any]]:
        compiler = AttackAnalyticsQueryCompiler(dialect="sqlite", filters=AttackAnalyticsFilters())
        root = compiler._group_compiler(dimensions).root
        with sqlite_instance.engine.connect() as connection:
            rows = connection.execute(select(root.c.analytics_weight, root.c.atomic_attack_identifier)).tuples().all()
            return list(rows)

    assert facts() == [(2, None)]
    with sqlite_instance.engine.begin() as connection:
        connection.execute(update(AttackIdentifierEntry).values(identifier_json={}, class_name=None))
    retained = facts()
    assert len(retained) == 1
    assert retained[0][0] == 2
    retained_attack = json.loads(retained[0][1])["children"]["attack_technique"]["children"]["attack"]
    assert retained_attack["class_name"] == "ProbeAttack"


def test_converter_profiles_canonicalize_each_profile_without_rescanning_results(sqlite_instance: SQLiteMemory) -> None:
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[_result(index=1, request=("Alpha",)), _result(index=2, request=("Beta",))]
    )
    query = AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type"))
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    profiles = reader.report(query=query, control=_control(), use_compact_profiles=True).profiles
    assert profiles is not None
    assert {tuple(json.loads(profile["source0"])): profile["weight"] for profile in profiles} == {
        ("Alpha",): 1,
        ("Beta",): 1,
    }
    statement = AttackAnalyticsQueryCompiler(dialect="sqlite", filters=query.filters).compact_profiles(
        query=query, limit=10, max_value_length=4096
    )
    assert statement is not None
    sql = str(statement.compile(dialect=sqlite.dialect()))
    assert len(re.findall(r'FROM "?AttackResultEntries"? AS analytics_results', sql)) == 3


@pytest.mark.parametrize("members", [[42], [{"class_name": {"invalid": True}}]])
def test_converter_profiles_leave_unsupported_members_unmodified(
    *, sqlite_instance: SQLiteMemory, members: list[Any]
) -> None:
    sqlite_instance.add_attack_results_to_memory(attack_results=[_result(request=("Original",))])
    with sqlite_instance.engine.begin() as connection:
        connection.execute(
            update(AttackIdentifierEntry).values(identifier_json={"children": {"request_converters": members}})
        )
    query = AttackAnalyticsQuery(group_by=AttackAnalyticsDimension(name="converter_type"))
    statement = AttackAnalyticsQueryCompiler(dialect="sqlite", filters=query.filters).compact_profiles(
        query=query, limit=10, max_value_length=4096
    )
    assert statement is not None
    with sqlite_instance.engine.connect() as connection:
        raw = connection.execute(statement).mappings().one()["source0"]
    assert json.loads(raw) == members


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (AttackAnalyticsDimension(name="scenario"), AttackAnalyticsDimension(name="converter_type")),
        (AttackAnalyticsDimension(name="label", label_key="team"), AttackAnalyticsDimension(name="model")),
    ],
)
def test_mssql_compaction_projects_metadata_without_ungroupable_json_or_extra_result_scans(
    *, first: AttackAnalyticsDimension, second: AttackAnalyticsDimension
) -> None:
    query = AttackAnalyticsQuery(group_by=first, compare_by=second)
    compiler = AttackAnalyticsQueryCompiler(dialect="mssql", filters=query.filters)
    statement = compiler.matrix(query)
    compiled = statement.compile(dialect=mssql.dialect(deprecate_large_types=True))
    sql = str(compiled)
    assert sql.count("FROM [AttackResultEntries] AS analytics_results") == 3
    assert "OUTER APPLY (SELECT" in sql
    assert "attribution_parent_id COLLATE" not in sql
    assert "LATERAL" not in sql
    if second.name is AttackAnalyticsDimensionName.CONVERTER_TYPE:
        assert any("__type__" in value for value in compiled.params.values() if isinstance(value, str))
    for node in visitors.iterate(statement):
        if isinstance(node, Select):
            for expression in node._group_by_clauses:
                assert not any(
                    isinstance(child, (JsonScalar, JsonClassNamePresent)) for child in visitors.iterate(expression)
                )


@pytest.mark.parametrize(
    ("document", "path", "present"),
    [
        ('{"class_name": null, "__type__": "Legacy"}', "$", True),
        ('{"__type__": "Legacy"}', "$", False),
        ('{"children": {"attack": {"class_name": null}}}', "$.children.attack", True),
        ('{"children": {"attack": {"__type__": "Legacy"}}}', "$.children.attack", False),
    ],
)
def test_json_class_name_presence_distinguishes_null_from_missing(
    *, sqlite_instance: SQLiteMemory, document: str, path: str, present: bool
) -> None:
    with sqlite_instance.engine.connect() as connection:
        assert connection.execute(select(JsonClassNamePresent(literal(document), path))).scalar_one() is present


def test_mssql_json_class_name_presence_uses_bound_object_path() -> None:
    statement = select(JsonClassNamePresent(literal('{"class_name": null}'), "$.children.attack"))
    compiled = statement.compile(dialect=mssql.dialect(), compile_kwargs={"render_postcompile": True})
    assert "EXISTS (SELECT 1 FROM OPENJSON(" in str(compiled)
    assert "WHERE [key] = N'class_name'" in str(compiled)
    assert "'$.children.attack'" in str(compiled)
