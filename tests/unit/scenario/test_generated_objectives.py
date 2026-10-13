# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.datasets import SeedDatasetProvider, TargetObjectiveProvider
from pyrit.executor.attack import PromptSendingAttack
from pyrit.memory import MemoryInterface
from pyrit.models import Message, MessagePiece, ScenarioRunState, SeedObjective, SeedOrigin
from pyrit.registry import AttackTechniqueRegistry
from pyrit.scenario import AttackTechniqueFactory, DatasetAttackConfiguration, DatasetSource
from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse, _build_rapid_response_technique
from pyrit.score import SubStringScorer


@pytest.fixture
def generation_technique_registry() -> Generator[None, None, None]:
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_rapid_response_technique.cache_clear()
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
        [AttackTechniqueFactory(name="prompt_sending", attack_class=PromptSendingAttack, technique_tags=["light"])]
    )
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_rapid_response_technique.cache_clear()


@pytest.mark.usefixtures("patch_central_database", "generation_technique_registry")
async def test_generated_dataset_runs_existing_scenario_async(sqlite_instance: MemoryInterface) -> None:
    generation_target = MockPromptTarget()
    provider = TargetObjectiveProvider(
        dataset_name="generated_workflow", target=generation_target, instructions="Test", count=10
    )

    async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
        return [
            MessagePiece(
                role="assistant",
                original_value=json.dumps({"objectives": [f"Objective {index}" for index in range(10)]}),
                conversation_id=normalized_conversation[-1].message_pieces[0].conversation_id,
            ).to_message()
        ]

    assert not await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)
    with patch.object(generation_target, "_send_prompt_to_target_async", side_effect=respond_async) as generate:
        dataset = await provider.fetch_dataset_async()
    generate.assert_called_once()
    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="operator")
    stored = await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name, origin=SeedOrigin.GENERATED)
    assert len(stored) == 10

    config = DatasetAttackConfiguration(
        dataset_names=[provider.dataset_name], max_per_dataset="all", max_total="all", auto_fetch=False
    )
    target = MockPromptTarget()
    scenario = RapidResponse(objective_scorer=SubStringScorer(substring="default"))
    scenario.set_params_from_args(
        args={"objective_target": target, "dataset_config": config, "include_baseline": False, "max_concurrency": 1}
    )
    with (
        patch.object(generation_target, "_send_prompt_to_target_async", side_effect=respond_async) as generate,
        patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as fetch,
    ):
        await scenario.initialize_async()
        result = await scenario.run_async()
    generate.assert_not_called()
    fetch.assert_not_called()
    assert len(await config.get_attack_seed_groups_async()) == 10
    assert result.scenario_run_state is ScenarioRunState.COMPLETED
    assert sum(len(results) for results in result.attack_results.values()) == 10
    assert len(target.prompt_sent) == 10


@pytest.mark.usefixtures("patch_central_database", "generation_technique_registry")
async def test_configured_generation_runs_then_reuses_manual_edits_async(sqlite_instance: MemoryInterface) -> None:
    generation_target = MockPromptTarget()
    provider = TargetObjectiveProvider(
        dataset_name="automatic_workflow", target=generation_target, instructions="First request", count=10
    )
    config = DatasetAttackConfiguration(sources=[DatasetSource(name=provider.dataset_name, provider=provider)])

    async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
        return [
            MessagePiece(
                role="assistant",
                original_value=json.dumps({"objectives": [f"Objective {index}" for index in range(10)]}),
                conversation_id=normalized_conversation[-1].message_pieces[0].conversation_id,
            ).to_message()
        ]

    target = MockPromptTarget()
    scenario = RapidResponse(objective_scorer=SubStringScorer(substring="default"))
    scenario.set_params_from_args(args={"objective_target": target, "dataset_config": config, "max_concurrency": 1})
    with (
        patch.object(generation_target, "_send_prompt_to_target_async", side_effect=respond_async) as generate,
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", side_effect=AssertionError("No lookup")),
    ):
        await scenario.get_run_size_estimate_async(target_is_configured=True)
        generate.assert_not_called()
        assert not await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)
        await scenario.initialize_async()
        result = await scenario.run_async()
    generate.assert_called_once()
    stored = await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)
    assert len(stored) == 10
    assert all(seed.origin is SeedOrigin.GENERATED and seed.added_by == "DatasetConfiguration" for seed in stored)
    conversation_ids = {seed.metadata["generation_conversation_id"] for seed in stored}
    assert len(conversation_ids) == 1
    assert await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_ids.pop())
    baseline, technique = scenario._atomic_attacks
    assert baseline.seed_groups == technique.seed_groups
    assert len(technique.seed_groups) == 5
    assert result.scenario_run_state is ScenarioRunState.COMPLETED
    assert sum(len(results) for results in result.attack_results.values()) == 10
    assert len(target.prompt_sent) == 10

    await sqlite_instance.remove_seeds_from_memory_async(dataset_name=provider.dataset_name, value=stored[0].value)
    edited = SeedObjective(
        value="Manually edited objective", dataset_name=provider.dataset_name, origin=SeedOrigin.USER
    )
    await sqlite_instance.add_seeds_to_memory_async(seeds=[edited], added_by="operator")
    changed_provider = TargetObjectiveProvider(
        dataset_name=provider.dataset_name, target=generation_target, instructions="Different request", count=10
    )
    changed_config = config.with_overrides(
        sources=[DatasetSource(name=provider.dataset_name, max_size=10, provider=changed_provider)]
    )
    reused = RapidResponse(objective_scorer=SubStringScorer(substring="default"))
    reused.set_params_from_args(
        args={
            "objective_target": target,
            "dataset_config": changed_config,
            "include_baseline": False,
            "max_concurrency": 1,
        }
    )
    with (
        patch.object(
            generation_target, "_send_prompt_to_target_async", side_effect=AssertionError("Reuse must not generate")
        ) as generate,
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", side_effect=AssertionError("No lookup")),
    ):
        await reused.initialize_async()
        reused_result = await reused.run_async()
    generate.assert_not_called()
    assert reused_result.scenario_run_state is ScenarioRunState.COMPLETED
    assert sum(len(results) for results in reused_result.attack_results.values()) == 10
    persisted = await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)
    assert len(persisted) == 10
    manual = next(seed for seed in persisted if seed.value == edited.value)
    assert manual.origin is SeedOrigin.USER
    assert manual.added_by == "operator"
    assert edited.value in target.prompt_sent
