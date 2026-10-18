# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import AttackResultEntry, ScenarioResultEntry
from pyrit.models import AttackResult, ComponentIdentifier, Operation, ScenarioResult
from unit.mocks import make_scenario_result

REVISION = "c8d3e5f7a901"
PARENT = "901e6c7bf9d4"


def _config(connection: sa.Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    config.attributes["connection"] = connection
    config.attributes["version_table"] = "pyrit_memory_alembic_version"
    return config


def _scenario() -> ScenarioResult:
    return make_scenario_result(
        scenario_name="Attribution",
        objective_target_identifier=ComponentIdentifier(class_name="Target", class_module="test"),
        attack_results={},
    )


@pytest.mark.parametrize("assigned", [False, True])
async def test_activity_operation_id_round_trip_async(sqlite_instance: MemoryInterface, assigned: bool) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Engagement"))
    operation_id = operation.id if assigned else None
    attack = AttackResult(conversation_id="conversation", objective="objective", operation_id=operation_id)
    scenario = _scenario()
    scenario.operation_id = operation_id
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scenario])
    attacks = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    scenarios = await sqlite_instance.get_scenario_results_async(scenario_result_ids=[str(scenario.id)])
    assert attacks[0].operation_id == operation_id
    assert scenarios[0].operation_id == operation_id


@pytest.mark.parametrize("activity", ["attack", "scenario"])
async def test_unknown_operation_id_rejected_on_direct_insert_async(
    sqlite_instance: MemoryInterface, activity: str
) -> None:
    with pytest.raises(LookupError, match="Operation"):
        if activity == "attack":
            await sqlite_instance.add_attack_results_to_memory_async(
                attack_results=[AttackResult(conversation_id="unknown", objective="objective", operation_id=uuid4())]
            )
        else:
            scenario = _scenario()
            scenario.operation_id = uuid4()
            await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scenario])
    assert not await sqlite_instance.get_attack_results_async()
    assert not await sqlite_instance.get_scenario_results_async()


async def test_require_operation_does_not_create_async(sqlite_instance: MemoryInterface) -> None:
    with pytest.raises(LookupError, match="Operation"):
        await sqlite_instance.require_operation_async(operation_id=uuid4())
    assert await sqlite_instance.get_operations_async() == []
    operation = await sqlite_instance.add_operation_async(Operation(name="Saved"))
    assert (await sqlite_instance.require_operation_async(operation_id=operation.id)).id == operation.id


async def test_resolve_operation_id_prefers_id_and_matches_deprecated_names_async(
    sqlite_instance: MemoryInterface,
) -> None:
    first = await sqlite_instance.add_operation_async(Operation(name="First"))
    second = await sqlite_instance.add_operation_async(Operation(name="Second"))
    assert await sqlite_instance.resolve_operation_id_async(operation_id=None) is None
    assert await sqlite_instance.resolve_operation_id_async(operation_id=str(first.id).upper()) == first.id
    with pytest.warns(DeprecationWarning, match="operation_id"):
        assert (
            await sqlite_instance.resolve_operation_id_async(operation_id=None, operation_name=" SECOND ") == second.id
        )
        assert (
            await sqlite_instance.resolve_operation_id_async(operation_id=first.id, operation_name="Second") == first.id
        )
    with pytest.warns(DeprecationWarning, match="operation_id"):
        assert await sqlite_instance.resolve_operation_id_async(operation_id=None, operation_name="missing") is None
    assert len(await sqlite_instance.get_operations_async()) == 2


async def test_scenario_history_id_filter_excludes_legacy_name_match_async(sqlite_instance: MemoryInterface) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Engagement"))
    assigned = _scenario()
    assigned.operation_id = operation.id
    legacy = _scenario()
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[assigned, legacy])
    with sqlite_instance._get_session() as session:
        session.execute(
            sa.update(ScenarioResultEntry)
            .where(ScenarioResultEntry.id == legacy.id)
            .values(labels={"operation": operation.name})
        )
        session.commit()

    records, _, has_more = await sqlite_instance.get_scenario_run_history_page_async(
        operation_ids=[operation.id], limit=1
    )
    assert [record.scenario_result_id for record in records] == [str(assigned.id)]
    assert records[0].operation_id == operation.id
    assert not has_more
    all_records, _, _ = await sqlite_instance.get_scenario_run_history_page_async()
    assert {record.scenario_result_id for record in all_records} == {str(assigned.id), str(legacy.id)}
    assert next(record for record in all_records if record.scenario_result_id == str(legacy.id)).labels == {
        "operation": operation.name
    }


async def test_attack_id_filter_excludes_legacy_name_match_async(sqlite_instance: MemoryInterface) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Engagement"))
    assigned = AttackResult(conversation_id="assigned", objective="objective", operation_id=operation.id)
    legacy = AttackResult(conversation_id="legacy", objective="objective", operation=operation.name)
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[assigned])
    with sqlite_instance._get_session() as session:
        session.add(AttackResultEntry(entry=legacy))
        session.commit()

    by_id = await sqlite_instance.get_attack_results_async(operation_ids=[operation.id])
    by_name = await sqlite_instance.get_attack_results_async(operation=[operation.name])
    assert [result.attack_result_id for result in by_id] == [assigned.attack_result_id]
    assert [result.attack_result_id for result in by_name] == [legacy.attack_result_id]


async def test_get_operations_by_ids_async(sqlite_instance: MemoryInterface) -> None:
    first, _, third = [await sqlite_instance.add_operation_async(Operation(name=name)) for name in "ABC"]
    found = await sqlite_instance.get_operations_async(operation_ids=[third.id, first.id, uuid4()])
    assert [operation.name for operation in found] == ["A", "C"]


async def test_scenario_history_id_filter_matches_multiple_operations_async(sqlite_instance: MemoryInterface) -> None:
    operations = [await sqlite_instance.add_operation_async(Operation(name=name)) for name in ("First", "Second")]
    scenarios = [_scenario(), _scenario(), _scenario()]
    for scenario, operation in zip(scenarios, operations, strict=False):
        scenario.operation_id = operation.id
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=scenarios)
    records, _, _ = await sqlite_instance.get_scenario_run_history_page_async(
        operation_ids=[operation.id for operation in operations]
    )
    assert {record.operation_id for record in records} == {operation.id for operation in operations}
    assert len(records) == 2


async def test_persisted_operation_id_cannot_be_changed_async(sqlite_instance: MemoryInterface) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Saved"))
    attack = AttackResult(conversation_id="immutable", objective="objective", operation_id=operation.id)
    scenario = _scenario()
    scenario.operation_id = operation.id
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scenario])
    with pytest.raises(ValueError, match="immutable"):
        await sqlite_instance.update_attack_result_by_id_async(
            attack_result_id=attack.attack_result_id, update_fields={"operation_id": None}
        )
    scenario.operation_id = None
    with pytest.raises(ValueError, match="immutable"):
        await sqlite_instance.update_scenario_result_async(scenario_result=scenario)


async def test_new_name_only_activity_is_saved_with_deprecation_warning_async(sqlite_instance: MemoryInterface) -> None:
    attack = AttackResult(conversation_id="name", objective="objective", operation="Old name")
    scenario = _scenario()
    scenario.labels = {"operation": "Old name"}
    with pytest.warns(DeprecationWarning, match="operation_id"):
        await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    with pytest.warns(DeprecationWarning, match="operation_id"):
        await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scenario])
    found = await sqlite_instance.get_attack_results_async(operation=["Old name"])
    assert [(result.attack_result_id, result.operation_id) for result in found] == [(attack.attack_result_id, None)]
    assert (await sqlite_instance.get_scenario_results_async())[0].labels == {"operation": "Old name"}


async def test_operation_name_changes_only_on_activity_without_an_id_async(sqlite_instance: MemoryInterface) -> None:
    legacy = _scenario()
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[legacy])
    with sqlite_instance.engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE ScenarioResultEntries SET labels = :labels WHERE id = :id"),
            {"labels": '{"operation": "Legacy"}', "id": str(legacy.id)},
        )
    stored = (await sqlite_instance.get_scenario_results_async())[0]
    await sqlite_instance.update_scenario_result_async(scenario_result=stored)
    stored.labels = {"operation": "Changed"}
    with pytest.warns(DeprecationWarning, match="operation_id"):
        await sqlite_instance.update_scenario_result_async(scenario_result=stored)
    assert (await sqlite_instance.get_scenario_results_async())[0].labels == {"operation": "Changed"}

    operation = await sqlite_instance.add_operation_async(Operation(name="Linked"))
    attack = AttackResult(
        conversation_id="linked", objective="objective", operation="Linked", operation_id=operation.id
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    with pytest.raises(ValueError, match="immutable"):
        await sqlite_instance.update_attack_result_by_id_async(
            attack_result_id=attack.attack_result_id, update_fields={"operation": "Renamed"}
        )
    assert (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[
        0
    ].operation == "Linked"


def _insert_legacy_attack(connection: sa.Connection, *, index: int, operation: str | None, timestamp: str) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO AttackResultEntries (id, conversation_id, objective, objective_sha256, "
            "executed_turns, execution_time_ms, outcome, operation, timestamp) "
            "VALUES (:id, :conversation_id, 'objective', 'hash', 0, 0, 'undetermined', :operation, :timestamp)"
        ),
        {
            "id": str(UUID(int=index)),
            "conversation_id": f"legacy-{index}",
            "operation": operation,
            "timestamp": timestamp,
        },
    )


def test_upgrade_registers_legacy_names_without_changing_activity() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PARENT)
            for index, (operation, timestamp) in enumerate(
                [
                    ("Old name", "2026-10-12 12:00:00.000000"),
                    (" old NAME ", "2026-10-12 11:00:00.000000"),
                    (None, "2026-10-12 12:00:00.000000"),
                    ("   ", "2026-10-12 12:00:00.000000"),
                    ("x" * 129, "2026-10-12 12:00:00.000000"),
                    # One emoji is two UTF-16 units, the unit Operation names are bounded in.
                    ("\U0001f600" * 64, "2026-10-12 12:00:00.000000"),
                    ("\U0001f600" * 65, "2026-10-12 12:00:00.000000"),
                ],
                start=1,
            ):
                _insert_legacy_attack(connection, index=index, operation=operation, timestamp=timestamp)
            connection.execute(
                sa.text(
                    "INSERT INTO ScenarioResultEntries (id, scenario_name, scenario_version, pyrit_version, "
                    "scenario_identifier, objective_target_identifier, scenario_run_state, number_tries, "
                    "completion_time, timestamp, labels) "
                    "VALUES ('00000000-0000-0000-0000-000000000005', 'Legacy scenario', 1, 'test', '{}', "
                    "'{}', 'CREATED', 0, '2026-10-12 12:00:00.000000', '2026-10-12 12:00:00.000000', "
                    '\'{"operation":"Old scenario"}\')'
                )
            )
            attacks_before = connection.execute(
                sa.text("SELECT id, operation FROM AttackResultEntries ORDER BY id")
            ).all()

            command.upgrade(config, REVISION)

            registered = connection.execute(
                sa.text("SELECT name, name_key, created_at FROM OperationEntries ORDER BY name_key")
            ).all()
            assert [(name, key) for name, key, _ in registered] == [
                ("old NAME", "old name"),
                ("Old scenario", "old scenario"),
                ("\U0001f600" * 64, "\U0001f600" * 64),
            ]
            assert all(Operation(name=name).name == name for name, _, _ in registered)
            assert registered[0][2].startswith("2026-10-12 11:00:00")
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM OperationEntries WHERE description <> ''")) == 0
            assert (
                connection.execute(sa.text("SELECT id, operation FROM AttackResultEntries ORDER BY id")).all()
                == attacks_before
            )
            assert (
                connection.scalar(sa.text("SELECT COUNT(*) FROM AttackResultEntries WHERE operation_id IS NOT NULL"))
                == 0
            )
            assert connection.execute(sa.text("SELECT labels, operation_id FROM ScenarioResultEntries")).one() == (
                '{"operation":"Old scenario"}',
                None,
            )
            for table in ("AttackResultEntries", "ScenarioResultEntries"):
                assert any(
                    fk["referred_table"] == "OperationEntries" for fk in sa.inspect(connection).get_foreign_keys(table)
                )
            with pytest.raises(ValueError, match="operations"):
                command.downgrade(config, PARENT)
    finally:
        engine.dispose()
