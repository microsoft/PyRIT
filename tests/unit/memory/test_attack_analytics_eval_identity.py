# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Result-ID counting with frozen, indexed objective-target evaluation groups."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.schema import CreateIndex

from pyrit.memory.analytics_identity_v1 import ObjectiveTargetAnalyticsIdentityV1
from pyrit.memory.attack_analytics import AttackAnalyticsReader
from pyrit.memory.attack_analytics_query import AttackAnalyticsQueryCompiler
from pyrit.memory.memory_models import (
    AtomicAttackIdentifierEntry,
    AttackIdentifierEntry,
    AttackResultEntry,
    AttackTechniqueIdentifierEntry,
    TargetIdentifierEntry,
)
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
    AttackResultSelection,
    ObjectiveTargetEvaluationIdentifier,
    TargetIdentifier,
)

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory


def _target(*, deployment: str, endpoint: str, temperature: float = 0.3) -> TargetIdentifier:
    return TargetIdentifier(
        class_name="MockTarget",
        class_module="tests",
        model_name=deployment,
        underlying_model_name="model-x",
        endpoint=endpoint,
        temperature=temperature,
    )


def _result(*, index: int, target: TargetIdentifier, outcome: AttackOutcome, operation: str) -> AttackResult:
    return AttackResult(
        attack_result_id=str(uuid.UUID(int=index)),
        conversation_id="same-conversation",
        objective=f"Objective {index}",
        outcome=outcome,
        operation=operation,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="ProbeAttack",
                class_module="tests",
                objective_target=target,
            )
        ),
    )


def _control() -> QueryControl:
    return QueryControl(deadline=time.monotonic() + 20)


def test_target_evaluation_v1_is_frozen_to_the_current_marker_rules() -> None:
    first = _target(deployment="deploy-a", endpoint="https://one.example")
    second = _target(deployment="deploy-b", endpoint="https://two.example")
    changed = _target(deployment="deploy-c", endpoint="https://three.example", temperature=0.5)
    wrapped = TargetIdentifier(class_name="RoundRobinTarget", class_module="tests", targets=[first])
    expected = "32e7c2bf2a31f21d91dc8bebca280a5ffecf149df474052c40889f7c77b84e81"

    assert first.hash != second.hash
    assert ObjectiveTargetAnalyticsIdentityV1.VERSION == "v1"
    assert ObjectiveTargetAnalyticsIdentityV1.hash(identifier=first) == expected
    assert ObjectiveTargetAnalyticsIdentityV1.hash(identifier=second) == expected
    assert ObjectiveTargetAnalyticsIdentityV1.hash(identifier=wrapped) == expected
    assert ObjectiveTargetAnalyticsIdentityV1.hash(identifier=changed) != expected
    assert (
        ObjectiveTargetAnalyticsIdentityV1.hash(identifier=first)
        == ObjectiveTargetEvaluationIdentifier(first).eval_hash
    )
    assert (
        ObjectiveTargetAnalyticsIdentityV1.hash(identifier=wrapped)
        == ObjectiveTargetEvaluationIdentifier(wrapped).eval_hash
    )


async def test_evaluation_groups_filters_facets_and_pages_share_result_identity(sqlite_instance: SQLiteMemory) -> None:
    first = _target(deployment="deploy-a", endpoint="https://one.example")
    second = _target(deployment="deploy-b", endpoint="https://two.example")
    different = _target(deployment="deploy-c", endpoint="https://three.example", temperature=0.5)
    results = [
        _result(index=1, target=first, outcome=AttackOutcome.SUCCESS, operation="night"),
        _result(index=2, target=second, outcome=AttackOutcome.FAILURE, operation="day"),
        _result(index=3, target=different, outcome=AttackOutcome.UNDETERMINED, operation="night"),
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)

    shared = ObjectiveTargetAnalyticsIdentityV1.hash(identifier=first)
    distinct = ObjectiveTargetAnalyticsIdentityV1.hash(identifier=different)
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    axis = AttackAnalyticsDimension(name="objective_target")
    report = await reader.report_async(query=AttackAnalyticsQuery(group_by=axis, result_limit=1), control=_control())

    assert report.counts == {"failure": 1, "success": 1, "undetermined": 1}
    assert {group.option.key.value: group.counts for group in report.groups} == {
        shared: {"failure": 1, "success": 1},
        distinct: {"undetermined": 1},
    }
    assert report.results.items[0].attack_result_id == results[2].attack_result_id
    assert report.results.has_more
    next_page = await reader.results_async(
        query=AttackAnalyticsResultsQuery(cursor=report.results.next_cursor, limit=2), control=_control()
    )
    assert [item.attack_result_id for item in next_page.items] == [
        results[1].attack_result_id,
        results[0].attack_result_id,
    ]
    assert {item.target_identifier_hash for item in next_page.items} == {first.hash, second.hash}

    filters = AttackAnalyticsFilters(
        dimensions=[
            AttackAnalyticsFilter(
                dimension=axis,
                values=[next(group.option.key for group in report.groups if group.option.key.value == shared)],
            )
        ]
    )
    filtered = await reader.report_async(
        query=AttackAnalyticsQuery(
            filters=filters, group_by=axis, compare_by=AttackAnalyticsDimension(name="operation")
        ),
        control=_control(),
    )
    assert filtered.counts == {"failure": 1, "success": 1}
    assert {cell.column.key.value: cell.counts for cell in filtered.cells if cell.column} == {
        "day": {"failure": 1},
        "night": {"success": 1},
    }
    assert {item.attack_result_id for item in filtered.results.items} == {
        results[0].attack_result_id,
        results[1].attack_result_id,
    }
    facets = await reader.facets_async(
        query=AttackAnalyticsFacetQuery(filters=filters, dimension=axis), control=_control()
    )
    assert {item.key.value for item in facets.items} == {shared, distinct}

    all_results = await sqlite_instance.get_attack_results_async(result_selection=AttackResultSelection.ALL_RESULTS)
    assert {result.attack_result_id for result in all_results} == {result.attack_result_id for result in results}
    assert len(await sqlite_instance.get_attack_results_async()) == 1


@pytest.mark.parametrize("by_id", [False, True], ids=["conversation", "result_id"])
async def test_replacing_atomic_identifier_updates_evaluation_groups_and_graph(
    sqlite_instance: SQLiteMemory, by_id: bool
) -> None:
    original = _target(deployment="deploy-a", endpoint="https://one.example")
    equivalent = _target(deployment="deploy-b", endpoint="https://two.example")
    replacement_target = _target(deployment="deploy-c", endpoint="https://three.example", temperature=0.5)
    changed_result = _result(index=1, target=original, outcome=AttackOutcome.SUCCESS, operation="night").model_copy(
        update={"conversation_id": "updated-conversation"}
    )
    other_result = _result(index=2, target=equivalent, outcome=AttackOutcome.FAILURE, operation="day")
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[changed_result, other_result])

    replacement = AtomicAttackIdentifier.build(
        attack_identifier=AttackIdentifier(
            class_name="ProbeAttack",
            class_module="tests",
            objective_target=replacement_target,
        )
    )
    replacement_document = replacement.model_dump()
    replacement_document["hash"] = "0" * 64
    replacement_document["eval_hash"] = "0" * 64
    update_fields = {"atomic_attack_identifier": replacement_document}
    if by_id:
        updated = await sqlite_instance.update_attack_result_by_id_async(
            attack_result_id=changed_result.attack_result_id, update_fields=update_fields
        )
    else:
        updated = await sqlite_instance.update_attack_result_async(
            conversation_id=changed_result.conversation_id, update_fields=update_fields
        )
    assert updated

    async with await sqlite_instance.get_session_async() as session:
        saved = await session.get(AttackResultEntry, uuid.UUID(changed_result.attack_result_id))
        assert saved is not None
        assert saved.atomic_attack_identifier_hash == replacement.hash
        assert saved.atomic_attack_identifier is not None
        assert saved.atomic_attack_identifier["hash"] == replacement.hash
        assert saved.atomic_attack_identifier["eval_hash"] != "0" * 64
        assert saved.objective_target_eval_hash_v1 == ObjectiveTargetAnalyticsIdentityV1.hash(
            identifier=replacement_target
        )
        atomic_row = await session.get(AtomicAttackIdentifierEntry, replacement.hash)
        assert atomic_row is not None
        assert replacement.attack_technique is not None
        technique_row = await session.get(AttackTechniqueIdentifierEntry, replacement.attack_technique.hash)
        assert technique_row is not None
        assert atomic_row.attack_technique_identifier_hash == technique_row.hash
        assert replacement.attack_technique.attack is not None
        attack_row = await session.get(AttackIdentifierEntry, replacement.attack_technique.attack.hash)
        assert attack_row is not None
        assert technique_row.attack_identifier_hash == attack_row.hash
        assert attack_row.objective_target_hash == replacement_target.hash
        assert await session.get(TargetIdentifierEntry, replacement_target.hash) is not None

    old_key = ObjectiveTargetAnalyticsIdentityV1.hash(identifier=original)
    new_key = ObjectiveTargetAnalyticsIdentityV1.hash(identifier=replacement_target)
    reader = AttackAnalyticsReader(memory=sqlite_instance)
    axis = AttackAnalyticsDimension(name="objective_target")
    report = await reader.report_async(query=AttackAnalyticsQuery(group_by=axis), control=_control())
    assert report.counts == {"failure": 1, "success": 1}
    assert {group.option.key.value: group.counts for group in report.groups} == {
        old_key: {"failure": 1},
        new_key: {"success": 1},
    }
    for key, expected_result, expected_target in [
        (old_key, other_result, equivalent),
        (new_key, changed_result, replacement_target),
    ]:
        filters = AttackAnalyticsFilters(
            dimensions=[AttackAnalyticsFilter(dimension=axis, values=[AttackAnalyticsValue(value=key)])]
        )
        filtered = await reader.report_async(
            query=AttackAnalyticsQuery(filters=filters, group_by=axis), control=_control()
        )
        assert [item.attack_result_id for item in filtered.results.items] == [expected_result.attack_result_id]
        assert filtered.results.items[0].target_identifier_hash == expected_target.hash
        assert {group.option.key.value for group in filtered.groups} == {key}
    facets = await reader.facets_async(query=AttackAnalyticsFacetQuery(dimension=axis), control=_control())
    assert {item.key.value for item in facets.items} == {old_key, new_key}

    clear_fields = {"atomic_attack_identifier": None}
    if by_id:
        cleared = await sqlite_instance.update_attack_result_by_id_async(
            attack_result_id=changed_result.attack_result_id, update_fields=clear_fields
        )
    else:
        cleared = await sqlite_instance.update_attack_result_async(
            conversation_id=changed_result.conversation_id, update_fields=clear_fields
        )
    assert cleared

    async with await sqlite_instance.get_session_async() as session:
        saved = await session.get(AttackResultEntry, uuid.UUID(changed_result.attack_result_id))
        assert saved is not None
        assert saved.atomic_attack_identifier is None
        assert saved.atomic_attack_identifier_hash is None
        assert saved.objective_target_eval_hash_v1 is None
    report_after_clear = await reader.report_async(query=AttackAnalyticsQuery(group_by=axis), control=_control())
    assert {
        (group.option.key.kind.value, group.option.key.value): group.counts for group in report_after_clear.groups
    } == {
        ("value", old_key): {"failure": 1},
        ("missing", None): {"success": 1},
    }
    missing_filter = AttackAnalyticsFilters(
        dimensions=[AttackAnalyticsFilter(dimension=axis, values=[AttackAnalyticsValue(kind="missing")])]
    )
    missing = await reader.report_async(query=AttackAnalyticsQuery(filters=missing_filter), control=_control())
    assert [item.attack_result_id for item in missing.results.items] == [changed_result.attack_result_id]
    assert missing.results.items[0].target_identifier_hash is None
    facets_after_clear = await reader.facets_async(query=AttackAnalyticsFacetQuery(dimension=axis), control=_control())
    assert {(item.key.kind.value, item.key.value) for item in facets_after_clear.items} == {
        ("value", old_key),
        ("missing", None),
    }


async def test_evaluation_filter_uses_a_bounded_indexed_result_key(sqlite_instance: SQLiteMemory) -> None:
    target = _target(deployment="deploy-a", endpoint="https://one.example")
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[_result(index=1, target=target, outcome=AttackOutcome.SUCCESS, operation="night")]
    )
    eval_hash = ObjectiveTargetAnalyticsIdentityV1.hash(identifier=target)
    axis = AttackAnalyticsDimension(name="objective_target")
    filters = AttackAnalyticsFilters(
        dimensions=[AttackAnalyticsFilter(dimension=axis, values=[AttackAnalyticsValue(value=eval_hash)])]
    )
    compiler = AttackAnalyticsQueryCompiler(dialect="sqlite", filters=filters)
    statement = compiler.totals()
    sql = str(statement.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    async with await sqlite_instance.get_session_async() as session:
        plan = (await session.execute(text(f"EXPLAIN QUERY PLAN {sql}"))).all()
    assert any("ix_AttackResultEntries_objective_target_eval_v1" in row[-1] for row in plan)

    indexes = {index.name: index for index in AttackResultEntry.__table__.indexes}
    index = indexes["ix_AttackResultEntries_objective_target_eval_v1"]
    assert [column.name for column in index.columns] == ["objective_target_eval_hash_v1", "outcome"]
    assert index.columns[0].type.length == 64
    assert "CREATE INDEX" in str(CreateIndex(index).compile(dialect=mssql.dialect()))
