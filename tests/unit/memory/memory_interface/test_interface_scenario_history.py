# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for lightweight scenario-history memory queries."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from unit.mocks import get_mock_target_identifier, make_scenario_result

from pyrit.common.utils import to_sha256
from pyrit.memory import MemoryInterface, ScenarioHistoryKeysetCursor
from pyrit.memory.memory_models import ScenarioResultEntry
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunState,
)


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        ("_get_scenario_registry_name_condition", {"scenario_names": ["test.scenario"]}),
        ("_get_scenario_history_plan_expressions", {}),
        ("_get_scenario_attempt_unit_expressions", {}),
        ("_get_scenario_plan_unit_subqueries", {"scenario_result_ids": [uuid.uuid4()]}),
    ],
)
def test_scenario_history_dialect_hooks_are_optional_until_used(
    method_name: str,
    kwargs: dict[str, object],
) -> None:
    assert method_name not in MemoryInterface.__abstractmethods__

    with pytest.raises(NotImplementedError, match=method_name):
        getattr(MemoryInterface, method_name)(MagicMock(), **kwargs)


def _make_scenario(
    *,
    result_id: uuid.UUID,
    timestamp: datetime,
    name: str,
    state: ScenarioRunState,
    labels: dict[str, str],
    registry_name: str | None = None,
):
    metadata = {}
    if registry_name:
        metadata[SCENARIO_RUN_PLAN_METADATA_KEY] = ScenarioRunPlan(
            scenario_registry_name=registry_name,
            atomic_groups=[
                ScenarioRunPlanAtomicGroup(
                    id="group-1",
                    atomic_attack_name="attack",
                    display_group="Attack",
                    technique_eval_hash="eval-1",
                    seed_group_ids=["seed-1"],
                )
            ],
            seed_groups=[
                ScenarioRunPlanSeedGroup(
                    id="seed-1",
                    objective_sha256="objective-hash",
                    objective="objective",
                )
            ],
        ).model_dump(mode="json", exclude_none=True)
    return make_scenario_result(
        id=result_id,
        scenario_name=name,
        scenario_run_state=state,
        labels=labels,
        creation_time=timestamp,
        completion_time=timestamp + timedelta(minutes=1),
        metadata=metadata,
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )


def test_history_pages_descending_equal_timestamps_by_id(sqlite_instance: MemoryInterface) -> None:
    timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
    scenarios = [
        _make_scenario(
            result_id=uuid.UUID(int=value),
            timestamp=timestamp,
            name=f"Scenario{value}",
            state=ScenarioRunState.COMPLETED,
            labels={},
        )
        for value in (1, 2, 3)
    ]
    sqlite_instance.add_scenario_results_to_memory(scenario_results=scenarios)
    entries = sqlite_instance._query_entries(ScenarioResultEntry)
    for entry in entries:
        entry.timestamp = timestamp
        sqlite_instance._update_entry(entry)

    first_page, _, has_more = sqlite_instance.get_scenario_run_history_page(limit=2)
    second_page, _, second_has_more = sqlite_instance.get_scenario_run_history_page(
        cursor=ScenarioHistoryKeysetCursor(
            timestamp=first_page[-1].created_at,
            scenario_result_id=first_page[-1].scenario_result_id,
        ),
        limit=2,
    )

    assert [row.scenario_result_id for row in first_page] == [str(uuid.UUID(int=3)), str(uuid.UUID(int=2))]
    assert has_more is True
    assert [row.scenario_result_id for row in second_page] == [str(uuid.UUID(int=1))]
    assert second_has_more is False


def test_history_creation_order_is_stable_when_scenario_entry_is_rebuilt(
    sqlite_instance: MemoryInterface,
) -> None:
    first_created_at = datetime(2026, 8, 7, tzinfo=timezone.utc)
    first = _make_scenario(
        result_id=uuid.UUID(int=1),
        timestamp=first_created_at,
        name="First",
        state=ScenarioRunState.IN_PROGRESS,
        labels={},
    )
    second = _make_scenario(
        result_id=uuid.UUID(int=2),
        timestamp=first_created_at + timedelta(minutes=1),
        name="Second",
        state=ScenarioRunState.COMPLETED,
        labels={},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[first, second])

    rebuilt_first = sqlite_instance.get_scenario_results(scenario_result_ids=[str(first.id)])[0]
    rebuilt_first.number_tries += 1
    sqlite_instance._update_entry(ScenarioResultEntry(entry=rebuilt_first))

    first_page, _, has_more = sqlite_instance.get_scenario_run_history_page(limit=1)
    second_page, _, second_has_more = sqlite_instance.get_scenario_run_history_page(
        cursor=ScenarioHistoryKeysetCursor(
            timestamp=first_page[-1].created_at,
            scenario_result_id=first_page[-1].scenario_result_id,
        ),
        limit=1,
    )

    assert first_page[0].scenario_result_id == str(second.id)
    assert first_page[0].created_at == second.creation_time
    assert has_more is True
    assert second_page[0].scenario_result_id == str(first.id)
    assert second_page[0].created_at == first.creation_time
    assert second_has_more is False


def test_history_filters_names_statuses_and_labels_without_hydration(
    sqlite_instance: MemoryInterface,
    monkeypatch,
) -> None:
    timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
    included = _make_scenario(
        result_id=uuid.UUID(int=10),
        timestamp=timestamp,
        name="ImplementationClass",
        registry_name="registered.scenario",
        state=ScenarioRunState.IN_PROGRESS,
        labels={"operator": "alice", "operation": "nightly", "team.name": "safety"},
    )
    excluded = _make_scenario(
        result_id=uuid.UUID(int=11),
        timestamp=timestamp - timedelta(minutes=1),
        name="OtherScenario",
        state=ScenarioRunState.COMPLETED,
        labels={"operator": "bob", "operation": "nightly", "team.name": "safety"},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[included, excluded])
    attacks = [
        AttackResult(
            attack_result_id=str(uuid.UUID(int=12)),
            conversation_id="conversation-12",
            objective="objective",
            outcome=AttackOutcome.ERROR,
            execution_time_ms=1,
            timestamp=timestamp,
            attribution_parent_id=str(included.id),
            attribution_data={
                "parent_collection": "attack",
                "parent_eval_hash": "eval-1",
                "seed_group_id": "seed-1",
            },
            error_type="RuntimeError",
            error_message="failed",
        ),
        AttackResult(
            attack_result_id=str(uuid.UUID(int=13)),
            conversation_id="conversation-13",
            objective="objective",
            outcome=AttackOutcome.SUCCESS,
            execution_time_ms=1,
            timestamp=timestamp + timedelta(seconds=1),
            total_retries=2,
            attribution_parent_id=str(included.id),
            attribution_data={
                "parent_collection": "attack",
                "parent_eval_hash": "eval-1",
                "seed_group_id": "seed-1",
            },
        ),
    ]
    sqlite_instance.add_attack_results_to_memory(attack_results=attacks)
    monkeypatch.setattr(
        "pyrit.memory.memory_models.AttackResultEntry.get_attack_result",
        MagicMock(side_effect=AssertionError("history hydrated an AttackResult")),
    )

    rows, aggregates, has_more = sqlite_instance.get_scenario_run_history_page(
        scenario_names=["registered.scenario"],
        statuses=[ScenarioRunState.IN_PROGRESS.value],
        labels={
            "operator": ["alice", "carol"],
            "operation": "nightly",
            "team.name": ["safety"],
        },
        limit=25,
    )

    assert [row.scenario_result_id for row in rows] == [str(included.id)]
    assert rows[0].scenario_identifier["class_name"] == "ImplementationClass"
    assert rows[0].scenario_registry_name == "registered.scenario"
    compact_groups = (
        json.loads(rows[0].plan_atomic_groups)
        if isinstance(rows[0].plan_atomic_groups, str)
        else rows[0].plan_atomic_groups
    )
    assert compact_groups == [
        {
            "id": "group-1",
            "atomic_attack_name": "attack",
            "display_group": "Attack",
            "technique_eval_hash": "eval-1",
            "seed_group_ids": ["seed-1"],
            "tags": [],
        }
    ]
    compact_seed_map = (
        json.loads(rows[0].plan_seed_id_map) if isinstance(rows[0].plan_seed_id_map, str) else rows[0].plan_seed_id_map
    )
    assert compact_seed_map == [{"id": "seed-1", "objective_sha256": "objective-hash"}]
    aggregate = aggregates[str(included.id)]
    assert aggregate.unit_count == 1
    assert aggregate.completed_units == 1
    assert aggregate.successful_units == 1
    assert aggregate.error_attempts == 1
    assert aggregate.total_retries == 3
    assert aggregate.atomic_attack_names == ("attack",)
    assert aggregate.latest_attempt_timestamp == timestamp + timedelta(seconds=1)
    assert has_more is False


def test_history_aggregate_uses_latest_attempt_outcome(sqlite_instance: MemoryInterface) -> None:
    """History uses the same latest-attempt semantics as scenario run details."""
    timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
    scenario = _make_scenario(
        result_id=uuid.UUID(int=18),
        timestamp=timestamp,
        name="LatestOutcomeScenario",
        registry_name="registered.scenario",
        state=ScenarioRunState.COMPLETED,
        labels={},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario])
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            AttackResult(
                attack_result_id=str(uuid.UUID(int=19)),
                conversation_id="conversation-19",
                objective="objective",
                outcome=AttackOutcome.SUCCESS,
                execution_time_ms=1,
                timestamp=timestamp,
                attribution_parent_id=str(scenario.id),
                attribution_data={
                    "parent_collection": "attack",
                    "parent_eval_hash": "eval-1",
                    "seed_group_id": "seed-1",
                },
            ),
            AttackResult(
                attack_result_id=str(uuid.UUID(int=20)),
                conversation_id="conversation-20",
                objective="objective",
                outcome=AttackOutcome.ERROR,
                execution_time_ms=1,
                timestamp=timestamp + timedelta(seconds=1),
                attribution_parent_id=str(scenario.id),
                attribution_data={
                    "parent_collection": "attack",
                    "parent_eval_hash": "eval-1",
                    "seed_group_id": "seed-1",
                },
            ),
        ]
    )

    _, aggregates, _ = sqlite_instance.get_scenario_run_history_page(limit=25)

    aggregate = aggregates[str(scenario.id)]
    assert aggregate.unit_count == 1
    assert aggregate.completed_units == 1
    assert aggregate.successful_units == 0
    assert aggregate.error_attempts == 1
    assert aggregate.latest_attempt_timestamp == timestamp + timedelta(seconds=1)


def test_history_aggregates_ignore_unplanned_units_and_remap_hash_seeds(
    sqlite_instance: MemoryInterface,
) -> None:
    """Plan-aware aggregation folds hash-attributed attempts into their planned unit."""
    timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
    planned = make_scenario_result(
        id=uuid.UUID(int=20),
        scenario_name="PlannedScenario",
        scenario_run_state=ScenarioRunState.IN_PROGRESS,
        labels={},
        creation_time=timestamp,
        completion_time=timestamp + timedelta(minutes=1),
        metadata={
            SCENARIO_RUN_PLAN_METADATA_KEY: ScenarioRunPlan(
                scenario_registry_name="registered.scenario",
                atomic_groups=[
                    ScenarioRunPlanAtomicGroup(
                        id="group-1",
                        atomic_attack_name="attack",
                        display_group="Attack",
                        technique_eval_hash="eval-1",
                        seed_group_ids=["seed-1"],
                    )
                ],
                seed_groups=[
                    ScenarioRunPlanSeedGroup(
                        id="seed-1",
                        objective_sha256=to_sha256("objective"),
                        objective="objective",
                    )
                ],
            ).model_dump(mode="json", exclude_none=True)
        },
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )
    unplanned = _make_scenario(
        result_id=uuid.UUID(int=21),
        timestamp=timestamp - timedelta(minutes=1),
        name="LegacyScenario",
        state=ScenarioRunState.COMPLETED,
        labels={},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[planned, unplanned])
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            AttackResult(
                attack_result_id=str(uuid.UUID(int=22)),
                conversation_id="conversation-22",
                objective="objective",
                outcome=AttackOutcome.FAILURE,
                execution_time_ms=1,
                timestamp=timestamp,
                attribution_parent_id=str(planned.id),
                attribution_data={"parent_collection": "attack", "parent_eval_hash": "eval-1"},
            ),
            AttackResult(
                attack_result_id=str(uuid.UUID(int=23)),
                conversation_id="conversation-23",
                objective="unplanned",
                outcome=AttackOutcome.SUCCESS,
                execution_time_ms=1,
                timestamp=timestamp + timedelta(seconds=1),
                attribution_parent_id=str(planned.id),
                attribution_data={"parent_collection": "other-attack", "seed_group_id": "seed-9"},
            ),
            AttackResult(
                attack_result_id=str(uuid.UUID(int=24)),
                conversation_id="conversation-24",
                objective="objective",
                outcome=AttackOutcome.SUCCESS,
                execution_time_ms=1,
                timestamp=timestamp,
                attribution_parent_id=str(unplanned.id),
                attribution_data={"parent_collection": "legacy-attack", "seed_group_id": "seed-legacy"},
            ),
        ]
    )

    _, aggregates, _ = sqlite_instance.get_scenario_run_history_page(limit=25)

    planned_aggregate = aggregates[str(planned.id)]
    assert planned_aggregate.unit_count == 1
    assert planned_aggregate.successful_units == 0
    assert planned_aggregate.atomic_attack_names == ("attack", "other-attack")
    assert planned_aggregate.latest_attempt_timestamp == timestamp + timedelta(seconds=1)
    legacy_aggregate = aggregates[str(unplanned.id)]
    assert legacy_aggregate.unit_count == 1
    assert legacy_aggregate.successful_units == 1


def test_history_aggregates_keep_explicitly_attributed_seed_groups_separate(
    sqlite_instance: MemoryInterface,
) -> None:
    """An attempt carrying an unplanned seed group ID is never remapped onto a planned unit."""
    timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
    scenario = make_scenario_result(
        id=uuid.UUID(int=40),
        scenario_name="AttributedScenario",
        scenario_run_state=ScenarioRunState.COMPLETED,
        labels={},
        creation_time=timestamp,
        completion_time=timestamp + timedelta(minutes=1),
        metadata={
            SCENARIO_RUN_PLAN_METADATA_KEY: ScenarioRunPlan(
                scenario_registry_name="registered.scenario",
                atomic_groups=[
                    ScenarioRunPlanAtomicGroup(
                        id="group-1",
                        atomic_attack_name="attack",
                        display_group="Attack",
                        technique_eval_hash="eval-1",
                        seed_group_ids=["planned-seed"],
                    )
                ],
                seed_groups=[
                    ScenarioRunPlanSeedGroup(
                        id="planned-seed",
                        objective_sha256=to_sha256("objective"),
                        objective="objective",
                    )
                ],
            ).model_dump(mode="json", exclude_none=True)
        },
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario])
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            AttackResult(
                attack_result_id=str(uuid.UUID(int=41)),
                conversation_id="conversation-41",
                objective="objective",
                outcome=AttackOutcome.SUCCESS,
                execution_time_ms=1,
                timestamp=timestamp,
                attribution_parent_id=str(scenario.id),
                attribution_data={
                    "parent_collection": "attack",
                    "parent_eval_hash": "eval-1",
                    "seed_group_id": "persisted-seed",
                },
            )
        ]
    )

    _, aggregates, _ = sqlite_instance.get_scenario_run_history_page(limit=25)

    assert aggregates[str(scenario.id)].unit_count == 0


def test_history_aggregates_tolerate_malformed_plan_shapes(sqlite_instance: MemoryInterface) -> None:
    """Malformed plan collections fall back to legacy aggregation instead of breaking history."""
    timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
    scenario = make_scenario_result(
        id=uuid.UUID(int=50),
        scenario_name="MalformedPlanScenario",
        scenario_run_state=ScenarioRunState.COMPLETED,
        labels={},
        creation_time=timestamp,
        completion_time=timestamp + timedelta(minutes=1),
        metadata={
            SCENARIO_RUN_PLAN_METADATA_KEY: {
                "scenario_registry_name": "registered.scenario",
                "atomic_groups": "malformed",
                "seed_groups": "malformed",
            }
        },
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario])
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            AttackResult(
                attack_result_id=str(uuid.UUID(int=51)),
                conversation_id="conversation-51",
                objective="objective",
                outcome=AttackOutcome.SUCCESS,
                execution_time_ms=1,
                timestamp=timestamp,
                attribution_parent_id=str(scenario.id),
                attribution_data={"parent_collection": "attack", "seed_group_id": "seed"},
            )
        ]
    )

    _, aggregates, _ = sqlite_instance.get_scenario_run_history_page(limit=25)

    assert aggregates[str(scenario.id)].unit_count == 0
    legacy_aggregates = sqlite_instance.get_scenario_history_aggregates(scenario_result_ids=[str(scenario.id)])
    assert legacy_aggregates[str(scenario.id)].unit_count == 1


def test_history_aggregates_fill_zero_for_runs_without_attempts(sqlite_instance: MemoryInterface) -> None:
    """Runs without persisted attempts still receive an aggregate entry."""
    scenario_result_id = str(uuid.UUID(int=30))

    aggregates = sqlite_instance.get_scenario_history_aggregates(scenario_result_ids=[scenario_result_id])

    assert aggregates[scenario_result_id].unit_count == 0
    assert aggregates[scenario_result_id].latest_attempt_timestamp is None


def test_unique_scenario_labels_are_grouped_for_filter_options(sqlite_instance: MemoryInterface) -> None:
    timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
    scenarios = [
        _make_scenario(
            result_id=uuid.UUID(int=index),
            timestamp=timestamp,
            name=f"Scenario{index}",
            state=ScenarioRunState.COMPLETED,
            labels={"operator": operator, "operation": "nightly"},
        )
        for index, operator in ((20, "alice"), (21, "bob"), (22, "alice"))
    ]
    sqlite_instance.add_scenario_results_to_memory(scenario_results=scenarios)

    assert sqlite_instance.get_unique_scenario_labels() == {
        "operation": ["nightly"],
        "operator": ["alice", "bob"],
    }
