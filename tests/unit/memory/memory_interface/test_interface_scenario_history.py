# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for lightweight scenario-history memory queries."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from unit.mocks import get_mock_target_identifier, make_scenario_result

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

    rows, units, has_more = sqlite_instance.get_scenario_run_history_page(
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
        }
    ]
    compact_seed_map = (
        json.loads(rows[0].plan_seed_id_map) if isinstance(rows[0].plan_seed_id_map, str) else rows[0].plan_seed_id_map
    )
    assert compact_seed_map == [{"id": "seed-1", "objective_sha256": "objective-hash"}]
    assert len(units[str(included.id)]) == 1
    assert units[str(included.id)][0].latest_outcome == AttackOutcome.SUCCESS.value
    assert units[str(included.id)][0].error_count == 1
    assert units[str(included.id)][0].total_retries == 3
    assert has_more is False


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
