# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exact selections must not be changed by technique compatibility skips or adaptation."""

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

import pyrit.models.seeds.seed_simulated_conversation as simulated_conversation_module
from pyrit.backend.services import ScenarioService
from pyrit.backend.services.dataset_example_fingerprint import dataset_example_fingerprint
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.executor.attack import RTASystemPromptPaths
from pyrit.memory import MemoryInterface, SeedEntry
from pyrit.models import (
    DatasetExampleReference,
    DatasetExampleSelection,
    DatasetExampleSelectionError,
    ScenarioRunSizeEstimateRequest,
    Seed,
    SeedGroupRequirements,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
    SimulatedTargetSystemPromptPaths,
    TargetIdentifier,
)
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.prompt_target import PromptTarget
from pyrit.registry import AttackTechniqueRegistry, ScorerRegistry, TargetRegistry
from pyrit.scenario import AttackTechniqueFactory, IncompatibleTechniquePolicy, Scenario, TechniqueRequirements
from pyrit.scenario.core.technique_requirements import (
    filter_seed_groups,
    prepare_seed_group,
)
from pyrit.scenario.scenarios.adaptive import TextAdaptive
from pyrit.scenario.scenarios.airt import RapidResponse
from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_technique
from pyrit.score import SubStringScorer, TrueFalseScorer
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
    lone_id = uuid4()
    lone = SeedObjective(id=lone_id, value="separate goal", dataset_name=DATASET, added_by="test")
    await _store(
        memory,
        SeedObjective(value="conversation goal", **common),
        SeedPrompt(value="first turn", sequence=0, role="user", **common),
        SeedPrompt(value="second turn", sequence=1, role="user", **common),
        lone,
    )
    return group, lone_id


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


def _objective_only_crescendo() -> AttackTechniqueFactory:
    """A registered ``crescendo`` override that, unlike the catalog default, accepts only an objective."""
    catalog = {factory.name: factory for factory in build_technique_factories()}["crescendo"]
    return AttackTechniqueFactory(
        name="crescendo",
        attack_class=catalog.attack_class,
        technique_tags=list(catalog.technique_tags),
        requirements=TechniqueRequirements(
            objective_target=catalog.requirements.objective_target,
            seed_group=SeedGroupRequirements(objective_only=True),
        ),
    )


@pytest.fixture
def registered_crescendo_override() -> Iterator[AttackTechniqueFactory]:
    """Register the catalog with ``crescendo`` replaced by an objective-only override."""
    override = _objective_only_crescendo()
    AttackTechniqueRegistry.reset_registry_singleton()
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
        [override if factory.name == "crescendo" else factory for factory in build_technique_factories()]
    )
    TextAdaptive._cached_technique_class = None
    with patch.object(TextAdaptive, "_get_default_objective_scorer", return_value=MagicMock(spec=TrueFalseScorer)):
        yield override
    TextAdaptive._cached_technique_class = None


@pytest.mark.usefixtures("patch_central_database")
async def test_exact_selection_checks_the_registered_override_text_adaptive_runs(
    sqlite_instance: MemoryInterface, registered_crescendo_override: AttackTechniqueFactory
) -> None:
    """TextAdaptive runs a registered factory over its catalog default, so the check must use it too."""
    group, lone = await _conversation_and_objective(sqlite_instance)
    assert TextAdaptive()._get_attack_technique_factories()["crescendo"] is registered_crescendo_override

    with pytest.raises(DatasetExampleSelectionError, match="unsupported") as caught:
        await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="adaptive.text",
            scenario_class=TextAdaptive,
            techniques=["crescendo"],
            example_selection=await _selection(sqlite_instance, example_ids=[group, lone]),
            include_baseline=False,
            memory=sqlite_instance,
        )
    assert caught.value.code == "unsupported"
    assert caught.value.example_id == group
    assert "crescendo" in str(caught.value)

    resolved = await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="adaptive.text",
        scenario_class=TextAdaptive,
        techniques=["crescendo"],
        example_selection=await _selection(sqlite_instance, example_ids=[lone]),
        include_baseline=False,
        memory=sqlite_instance,
    )
    groups = await resolved["dataset_config"].get_attack_seed_groups_async()
    assert [seed_group.objective.value for seed_group in groups] == ["separate goal"]


async def _objective_and_legacy_simulated_conversation(memory: MemoryInterface) -> UUID:
    """Store an objective with a simulated conversation saved before prompt paths were inlined."""
    group = uuid4()
    common = {"dataset_name": DATASET, "added_by": "test", "prompt_group_id": group}
    conversation = SeedSimulatedConversation(
        num_turns=1,
        adversarial_chat_system_prompt=SeedPrompt(value="adversary", parameters=["objective"]),
        simulated_target_system_prompt=SeedPrompt(value="target", parameters=["objective", "num_turns"]),
        **common,
    )
    await _store(memory, SeedObjective(value="simulated goal", **common), conversation)
    async with await memory.get_session_async() as session:
        entry = await session.get(SeedEntry, conversation.id)
        assert entry is not None
        entry.value = json.dumps(
            {
                "num_turns": 1,
                "adversarial_chat_system_prompt_path": str(RTASystemPromptPaths.TEXT_GENERATION.value),
                "simulated_target_system_prompt_path": str(SimulatedTargetSystemPromptPaths.COMPLIANT.value),
            }
        )
        await session.commit()
    return group


@pytest.mark.usefixtures("patch_central_database")
async def test_configured_estimate_checks_techniques_without_loading_legacy_templates(
    sqlite_instance: MemoryInterface,
) -> None:
    """Estimate checks crescendo against a stored simulated conversation from inspection data alone."""
    group = await _objective_and_legacy_simulated_conversation(sqlite_instance)
    selection = await _selection(sqlite_instance, example_ids=[group])
    scorer = SubStringScorer(substring="expected")
    scorers = ScorerRegistry()
    scorers.instances.register(scorer, name="explicit")
    request = ScenarioRunSizeEstimateRequest(
        techniques=["crescendo"],
        example_selection=selection,
        include_baseline=False,
        objective_scorer=RegisteredComponentReference(
            registry_name="explicit", identifier_hash=scorer.get_identifier().hash
        ),
    )
    loader = MagicMock(side_effect=simulated_conversation_module.resolve_prompt_source)
    template = MagicMock(side_effect=SeedPrompt.from_yaml_with_required_parameters)
    with (
        patch.object(ScorerRegistry, "get_registry_singleton", return_value=scorers),
        patch.object(simulated_conversation_module, "resolve_prompt_source", loader),
        patch.object(SeedPrompt, "from_yaml_with_required_parameters", template),
        patch.object(PromptTarget, "send_prompt_async", side_effect=AssertionError("target call")),
        patch.object(Scenario, "initialize_async", side_effect=AssertionError("launch")),
    ):
        estimate = await ScenarioService().estimate_scenario_run_size_async(
            scenario_name="airt.rapid_response", request=request
        )
    assert estimate is not None
    assert estimate.dataset_size.model_dump() == {"kind": "bounded", "value": 1}
    assert estimate.total_attack_count == 1
    assert loader.call_count == 0
    assert template.call_count == 0

    resolved = await _resolve(sqlite_instance, technique="crescendo", example_ids=[group])
    groups = await resolved["dataset_config"].get_attack_seed_groups_async()
    assert groups[0].has_simulated_conversation
    assert groups[0].simulated_conversation_config.adversarial_chat_system_prompt is not None


@pytest.mark.usefixtures("patch_central_database")
async def test_configured_estimate_still_rejects_an_incompatible_simulated_conversation(
    sqlite_instance: MemoryInterface,
) -> None:
    """The inspection check keeps rejecting a technique that would skip or adapt the stored conversation."""
    group = await _objective_and_legacy_simulated_conversation(sqlite_instance)
    request = ScenarioRunSizeEstimateRequest(
        techniques=["many_shot"],
        example_selection=await _selection(sqlite_instance, example_ids=[group]),
        include_baseline=False,
    )
    loader = MagicMock(side_effect=simulated_conversation_module.resolve_prompt_source)
    with (
        patch.object(simulated_conversation_module, "resolve_prompt_source", loader),
        pytest.raises(DatasetExampleSelectionError, match="unsupported") as caught,
    ):
        await ScenarioService().estimate_scenario_run_size_async(scenario_name="airt.rapid_response", request=request)
    assert caught.value.example_id == group
    assert "many_shot" in str(caught.value)
    assert loader.call_count == 0
