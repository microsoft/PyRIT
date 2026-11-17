# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import ScenarioResultEntry
from pyrit.models import AttackSeedGroup, SeedObjective, SeedPrompt, SeedSimulatedConversation
from unit.mocks import get_mock_target_identifier, make_scenario_result, run_memory_session_async


def _group(objective: str, *prompts: SeedPrompt) -> AttackSeedGroup:
    return AttackSeedGroup(
        seeds=[SeedObjective(value=objective, description="kept", authors=["a"], harm_categories=["x"]), *prompts]
    )


async def test_planned_seed_groups_round_trip_in_order_with_duplicates(sqlite_instance: MemoryInterface) -> None:
    duplicate = _group("dup")
    population = {
        "a": [duplicate, duplicate.model_copy(deep=True), _group("a2", SeedPrompt(value="context", role="system"))],
        "b": [_group("b1")],
    }
    result = make_scenario_result(objective_target_identifier=get_mock_target_identifier(), attack_results={})
    await sqlite_instance.add_planned_scenario_result_async(scenario_result=result, seed_groups_by_dataset=population)

    stored = await sqlite_instance.get_planned_seed_groups_async(scenario_result_id=str(result.id))

    assert stored == population
    assert list(stored) == ["a", "b"]
    assert (await sqlite_instance.get_scenario_results_async(scenario_result_ids=[str(result.id)]))[0].id == result.id


async def test_planned_seed_groups_are_empty_for_a_result_without_them(sqlite_instance: MemoryInterface) -> None:
    result = make_scenario_result(objective_target_identifier=get_mock_target_identifier(), attack_results={})
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[result])

    assert await sqlite_instance.get_planned_seed_groups_async(scenario_result_id=str(result.id)) == {}


async def test_planned_seed_groups_restore_already_prepared_conversation_prompts(
    sqlite_instance: MemoryInterface,
) -> None:
    prompt = SeedPrompt(value="{% raw %}{{ 3 + 4 }}{% endraw %}", is_jinja_template=True)
    conversation = SeedSimulatedConversation(
        adversarial_chat_system_prompt=prompt,
        simulated_target_system_prompt=prompt.model_copy(),
        next_message_system_prompt=prompt.model_copy(),
    )
    group = AttackSeedGroup(seeds=[SeedObjective(value="objective"), conversation])
    result = make_scenario_result(objective_target_identifier=get_mock_target_identifier(), attack_results={})
    await sqlite_instance.add_planned_scenario_result_async(
        scenario_result=result, seed_groups_by_dataset={"a": [group]}
    )

    stored = await sqlite_instance.get_planned_seed_groups_async(scenario_result_id=str(result.id))

    assert prompt.value == "{{ 3 + 4 }}"
    assert stored["a"][0].logical_id == group.logical_id


@pytest.mark.parametrize("bulk", [False, True])
async def test_deleting_scenario_removes_its_planned_groups_with_sqlite_foreign_keys_off(
    sqlite_instance: MemoryInterface, bulk: bool
) -> None:
    result = make_scenario_result(objective_target_identifier=get_mock_target_identifier(), attack_results={})
    retained = make_scenario_result(objective_target_identifier=get_mock_target_identifier(), attack_results={})
    population = {"a": [_group("keep the other run")]}
    for run in (result, retained):
        await sqlite_instance.add_planned_scenario_result_async(scenario_result=run, seed_groups_by_dataset=population)

    def remove(session: Session) -> None:
        assert session.scalar(text("PRAGMA foreign_keys")) == 0
        if bulk:
            session.execute(delete(ScenarioResultEntry).where(ScenarioResultEntry.id == result.id))
        else:
            entry = session.get(ScenarioResultEntry, result.id)
            assert entry is not None
            session.delete(entry)
        session.commit()

    await run_memory_session_async(memory=sqlite_instance, operation=remove)

    assert await sqlite_instance.get_planned_seed_groups_async(scenario_result_id=str(result.id)) == {}
    assert await sqlite_instance.get_planned_seed_groups_async(scenario_result_id=str(retained.id)) == population
