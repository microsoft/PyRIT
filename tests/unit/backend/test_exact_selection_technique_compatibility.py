# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exact selections must not be changed by technique compatibility skips or adaptation."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from pyrit.backend.services.dataset_example_fingerprint import dataset_example_fingerprint
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.memory import MemoryInterface, SeedEntry
from pyrit.models import (
    DatasetExampleReference,
    DatasetExampleSelection,
    DatasetExampleSelectionError,
    Seed,
    SeedObjective,
    SeedPrompt,
    TargetIdentifier,
)
from pyrit.prompt_target import PromptTarget
from pyrit.registry import AttackTechniqueRegistry, TargetRegistry
from pyrit.scenario import IncompatibleTechniquePolicy
from pyrit.scenario.core.technique_requirements import filter_seed_groups, prepare_seed_group
from pyrit.scenario.scenarios.airt import RapidResponse
from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_technique
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.techniques import build_technique_factories

DATASET = "exact-technique"
NAMED = f"dataset:named:{DATASET}"


@pytest.fixture(autouse=True)
def rapid_response_catalog() -> Iterator[None]:
    """Register the real technique catalog with a mock adversarial target and default scorer."""
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_rapid_response_technique.cache_clear()
    adversarial = MagicMock(spec=PromptTarget)
    adversarial.capabilities.includes.return_value = True
    adversarial.get_identifier.return_value = TargetIdentifier(class_name="MockAdversarial", class_module="test")
    TargetRegistry.get_registry_singleton().instances.register(adversarial, name="adversarial_chat")
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    with patch.object(RapidResponse, "_get_default_objective_scorer", return_value=MagicMock(spec=TrueFalseScorer)):
        yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_rapid_response_technique.cache_clear()


async def _store(memory: MemoryInterface, *seeds: Seed[Any]) -> None:
    async with await memory.get_session_async() as session:
        session.add_all(SeedEntry(entry=seed) for seed in seeds)
        await session.commit()


async def _selection(memory: MemoryInterface, *, example_ids: list[UUID]) -> DatasetExampleSelection:
    found = await memory.get_seed_examples_by_ids_async(dataset_name=DATASET, example_ids=example_ids)
    return DatasetExampleSelection(
        examples=[
            DatasetExampleReference(
                selection_key=NAMED,
                example_id=example_id,
                fingerprint=dataset_example_fingerprint(
                    selection_key=NAMED, example_id=example_id, members=found[example_id]
                ),
            )
            for example_id in example_ids
        ]
    )


async def _conversation_and_objective(memory: MemoryInterface) -> tuple[UUID, UUID]:
    """Store one real conversation group and one separate objective."""
    group = uuid4()
    common = {"dataset_name": DATASET, "added_by": "test", "prompt_group_id": group}
    lone = SeedObjective(value="separate goal", dataset_name=DATASET, added_by="test")
    await _store(
        memory,
        SeedObjective(value="conversation goal", **common),
        SeedPrompt(value="first turn", sequence=0, role="user", **common),
        SeedPrompt(value="second turn", sequence=1, role="user", **common),
        lone,
    )
    return group, lone.id


async def _resolve(memory: MemoryInterface, *, technique: str, example_ids: list[UUID]) -> dict[str, Any]:
    return await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="airt.rapid_response",
        scenario_class=RapidResponse,
        techniques=[technique],
        example_selection=await _selection(memory, example_ids=example_ids),
        include_baseline=False,
        memory=memory,
    )


@pytest.mark.parametrize("technique", ["role_play_movie_script", "many_shot"])
@pytest.mark.usefixtures("patch_central_database")
async def test_technique_that_would_skip_or_adapt_a_selected_example_is_unsupported(
    sqlite_instance: MemoryInterface, technique: str
) -> None:
    group, lone = await _conversation_and_objective(sqlite_instance)
    with pytest.raises(DatasetExampleSelectionError, match="unsupported") as caught:
        await _resolve(sqlite_instance, technique=technique, example_ids=[group, lone])
    assert caught.value.code == "unsupported"
    assert caught.value.example_id == group
    assert technique in str(caught.value)
    assert "first turn" not in str(caught.value)


@pytest.mark.usefixtures("patch_central_database")
async def test_skip_policy_alone_would_change_the_selection(sqlite_instance: MemoryInterface) -> None:
    """The scenario's skip policy drops the conversation, and many_shot adaptation strips its prompts."""
    group, lone = await _conversation_and_objective(sqlite_instance)
    config = (await _resolve(sqlite_instance, technique="crescendo", example_ids=[group, lone]))["dataset_config"]
    groups = config._build_selected_groups()
    factories = AttackTechniqueRegistry.get_registry_singleton().get_factories()

    role_play = factories["role_play_movie_script"]
    retained = filter_seed_groups(
        seed_groups=groups,
        requirements=role_play.requirements.seed_group,
        seed_technique=role_play.seed_technique,
        technique_name=role_play.name,
        dataset_name="inline",
        policy=IncompatibleTechniquePolicy.SKIP,
        validate_group=role_play.validate_group_adversarial_prompts,
    )
    assert [seed_group.objective.value for seed_group in retained] == ["separate goal"]

    many_shot = factories["many_shot"]
    prepared = prepare_seed_group(
        seed_group=groups[0], requirements=many_shot.requirements.seed_group, seed_technique=many_shot.seed_technique
    )
    assert prepared.adaptation == "objective_only"
    assert len(groups[0].prompts) == 2
    assert prepared.seed_group.prompts == []


@pytest.mark.parametrize("technique", ["role_play_movie_script", "many_shot"])
@pytest.mark.usefixtures("patch_central_database")
async def test_objective_only_selection_still_runs(sqlite_instance: MemoryInterface, technique: str) -> None:
    _, lone = await _conversation_and_objective(sqlite_instance)
    resolved = await _resolve(sqlite_instance, technique=technique, example_ids=[lone])
    groups = await resolved["dataset_config"].get_attack_seed_groups_async()
    assert [seed_group.objective.value for seed_group in groups] == ["separate goal"]


@pytest.mark.usefixtures("patch_central_database")
async def test_compatible_technique_keeps_the_conversation(sqlite_instance: MemoryInterface) -> None:
    group, lone = await _conversation_and_objective(sqlite_instance)
    resolved = await _resolve(sqlite_instance, technique="crescendo", example_ids=[group, lone])
    groups = await resolved["dataset_config"].get_attack_seed_groups_async()
    assert [len(seed_group.prompts) for seed_group in groups] == [2, 0]


@pytest.mark.usefixtures("patch_central_database")
async def test_default_techniques_are_checked_when_none_are_selected(sqlite_instance: MemoryInterface) -> None:
    group, lone = await _conversation_and_objective(sqlite_instance)
    with pytest.raises(DatasetExampleSelectionError, match="unsupported") as caught:
        await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="airt.rapid_response",
            scenario_class=RapidResponse,
            example_selection=await _selection(sqlite_instance, example_ids=[group, lone]),
            include_baseline=False,
            memory=sqlite_instance,
        )
    assert caught.value.example_id == group
