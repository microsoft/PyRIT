# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for lightweight scenario progress memory queries."""

import uuid
from contextlib import closing
from datetime import datetime, timezone

import pytest
from unit.mocks import get_mock_target_identifier, make_scenario_result

from pyrit.memory import AttackResultKeysetCursor, MemoryInterface
from pyrit.memory.memory_models import ScenarioResultEntry
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    ComponentIdentifier,
    ScenarioRunState,
    Score,
    SeedObjective,
)


def _make_delta_result(
    *,
    scenario_result_id: str,
    attack_result_id: uuid.UUID,
    timestamp: datetime,
    objective: str,
) -> AttackResult:
    seed_group = AttackSeedGroup(seeds=[SeedObjective(value=objective)])
    identifier = AtomicAttackIdentifier.build(
        attack_identifier=ComponentIdentifier(class_name="TestAttack", class_module="tests"),
        seed_group=seed_group,
    )
    return AttackResult(
        attack_result_id=str(attack_result_id),
        conversation_id=f"conversation-{attack_result_id}",
        objective=objective,
        atomic_attack_identifier=identifier,
        outcome=AttackOutcome.SUCCESS,
        execution_time_ms=12,
        timestamp=timestamp,
        attribution_parent_id=scenario_result_id,
        attribution_data={"parent_collection": "attack", "parent_eval_hash": "eval"},
    )


def test_scenario_progress_deltas_page_equal_timestamps_by_id(
    sqlite_instance: MemoryInterface,
) -> None:
    scenario = make_scenario_result(
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )
    unrelated = make_scenario_result(
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario, unrelated])
    timestamp = datetime(2026, 8, 6, tzinfo=timezone.utc)
    first_id = uuid.UUID(int=1)
    second_id = uuid.UUID(int=2)
    rows = [
        _make_delta_result(
            scenario_result_id=str(scenario.id),
            attack_result_id=first_id,
            timestamp=timestamp,
            objective="first",
        ),
        _make_delta_result(
            scenario_result_id=str(scenario.id),
            attack_result_id=second_id,
            timestamp=timestamp,
            objective="second",
        ),
        _make_delta_result(
            scenario_result_id=str(unrelated.id),
            attack_result_id=uuid.UUID(int=3),
            timestamp=timestamp,
            objective="unrelated",
        ),
    ]
    score = Score(
        score_value="true",
        score_type="true_false",
        score_rationale="The objective was achieved.",
        scorer_class_identifier=ComponentIdentifier(
            class_name="TestScorer",
            class_module="tests",
        ),
        timestamp=timestamp,
    )
    rows[0].last_score = score
    sqlite_instance.add_scores_to_memory(scores=[score])
    sqlite_instance.add_attack_results_to_memory(attack_results=rows)

    first_page, has_more = sqlite_instance.get_scenario_attack_result_deltas(
        scenario_result_id=str(scenario.id),
        limit=1,
    )
    second_page, second_has_more = sqlite_instance.get_scenario_attack_result_deltas(
        scenario_result_id=str(scenario.id),
        cursor=AttackResultKeysetCursor(
            timestamp=first_page[0].timestamp,
            attack_result_id=first_page[0].attack_result_id,
        ),
        limit=1,
    )

    assert [row.attack_result_id for row in first_page] == [str(first_id)]
    assert first_page[0].conversation_id == f"conversation-{first_id}"
    assert first_page[0].score is not None
    assert first_page[0].score.scorer_name == "TestScorer"
    assert first_page[0].score.score_rationale == "The objective was achieved."
    assert has_more is True
    assert [row.attack_result_id for row in second_page] == [str(second_id)]
    assert second_has_more is False
    assert second_page[0].atomic_attack_identifier is not None
    source_identifier = AtomicAttackIdentifier.from_component_identifier(rows[1].atomic_attack_identifier)
    assert second_page[0].atomic_attack_identifier.logical_seed_group_id == source_identifier.logical_seed_group_id


def test_scenario_progress_delta_uses_unknown_for_empty_scorer_identifier(
    sqlite_instance: MemoryInterface,
) -> None:
    scenario = make_scenario_result(
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario])
    attack_result = _make_delta_result(
        scenario_result_id=str(scenario.id),
        attack_result_id=uuid.UUID(int=5),
        timestamp=datetime(2026, 8, 6, tzinfo=timezone.utc),
        objective="objective",
    )
    score = Score(
        score_value="true",
        score_type="true_false",
        scorer_class_identifier=None,
    )
    attack_result.last_score = score
    sqlite_instance.add_scores_to_memory(scores=[score])
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    deltas, has_more = sqlite_instance.get_scenario_attack_result_deltas(
        scenario_result_id=str(scenario.id),
        limit=1,
    )

    assert has_more is False
    assert deltas[0].score is not None
    assert deltas[0].score.scorer_name == "Unknown"


def test_scenario_result_header_does_not_hydrate_attack_results(
    sqlite_instance: MemoryInterface,
) -> None:
    scenario = make_scenario_result(
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario])
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            _make_delta_result(
                scenario_result_id=str(scenario.id),
                attack_result_id=uuid.UUID(int=4),
                timestamp=datetime(2026, 8, 6, tzinfo=timezone.utc),
                objective="objective",
            )
        ]
    )

    header = sqlite_instance.get_scenario_result_header(scenario_result_id=str(scenario.id))

    assert header is not None
    assert header.attack_results == {}


def test_scenario_result_headers_are_bounded_without_attack_results(
    sqlite_instance: MemoryInterface,
) -> None:
    scenarios = [
        make_scenario_result(
            scenario_name=f"scenario-{index}",
            attack_results={},
            objective_target_identifier=get_mock_target_identifier(),
        )
        for index in range(2)
    ]
    sqlite_instance.add_scenario_results_to_memory(scenario_results=scenarios)

    headers = sqlite_instance.get_scenario_result_headers(limit=1)

    assert len(headers) == 1
    assert headers[0].attack_results == {}
    with pytest.raises(ValueError, match="between 1 and 100"):
        sqlite_instance.get_scenario_result_headers(limit=101)


def test_scenario_result_headers_include_recent_active_runs(
    sqlite_instance: MemoryInterface,
) -> None:
    completed = make_scenario_result(
        scenario_name="completed",
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
        scenario_run_state=ScenarioRunState.COMPLETED,
        completion_time=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    active = make_scenario_result(
        scenario_name="active",
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
        scenario_run_state=ScenarioRunState.IN_PROGRESS,
        completion_time=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[completed, active])
    with closing(sqlite_instance.get_session()) as session:
        completed_entry = session.get(ScenarioResultEntry, completed.id)
        active_entry = session.get(ScenarioResultEntry, active.id)
        assert completed_entry is not None
        assert active_entry is not None
        completed_entry.timestamp = datetime(2026, 8, 1, tzinfo=timezone.utc)
        active_entry.timestamp = datetime(2026, 8, 10, tzinfo=timezone.utc)
        session.commit()

    headers = sqlite_instance.get_scenario_result_headers(limit=1)

    assert headers[0].scenario_name == "active"
