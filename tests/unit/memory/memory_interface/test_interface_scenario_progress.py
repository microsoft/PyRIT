# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for lightweight scenario progress memory queries."""

import uuid
from datetime import datetime, timezone

from unit.mocks import get_mock_target_identifier, make_scenario_result

from pyrit.memory import AttackResultKeysetCursor, MemoryInterface
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    ComponentIdentifier,
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
