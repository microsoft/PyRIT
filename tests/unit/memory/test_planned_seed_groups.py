# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import ScenarioPlanSeedGroupEntry, planned_media_values
from pyrit.models import AttackSeedGroup, SeedObjective, SeedPrompt
from unit.mocks import get_mock_target_identifier, make_scenario_result


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


def test_planned_seed_group_entry_flags_media() -> None:
    result_id = make_scenario_result(objective_target_identifier=get_mock_target_identifier(), attack_results={}).id
    text = ScenarioPlanSeedGroupEntry(
        scenario_result_id=result_id, dataset_name="a", position=0, seed_group=_group("t")
    )
    image = ScenarioPlanSeedGroupEntry(
        scenario_result_id=result_id,
        dataset_name="a",
        position=1,
        seed_group=_group("i", SeedPrompt(value="/media/cat.png", data_type="image_path")),
    )

    assert (text.has_media, image.has_media) == (False, True)
    assert planned_media_values(image.seed_group) == ["/media/cat.png"]
    assert image.get_seed_group() == image.get_seed_group()


def test_planned_media_values_reads_simulated_conversation_prompts() -> None:
    seed_group = {
        "seeds": [
            {
                "seed_type": "simulated_conversation",
                "adversarial_chat_system_prompt": {"value": "/media/a.png", "data_type": "image_path"},
                "simulated_target_system_prompt": {"value": "text", "data_type": "text"},
                "next_message_system_prompt": None,
            }
        ]
    }

    assert planned_media_values(seed_group) == ["/media/a.png"]
