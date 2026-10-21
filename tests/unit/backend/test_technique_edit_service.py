# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Lossless editor projections and source-pinned, run-local factory reconstruction."""

import asyncio
import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.backend.services.scenario_service import ScenarioService
from pyrit.backend.services.technique_edit_service import TechniqueEditService
from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH, EXECUTOR_SIMULATED_TARGET_PATH
from pyrit.converter import Base64Converter, ROT13Converter
from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import AttackTechniqueSeedGroup, SeedPrompt, SeedSimulatedConversation, load_next_message_prompt
from pyrit.models.catalog.scenario import RunScenarioRequest, ScenarioRunSizeEstimateRequest
from pyrit.models.technique_edit import SimulationChanges, TechniqueChanges, TechniqueOverride
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, Registry, TargetRegistry
from pyrit.scenario.core import AttackTechniqueFactory
from pyrit.scenario.scenarios.technique.execute import Execute
from pyrit.score import SubStringScorer
from unit.mocks import MockPromptTarget


class _TextAttack(PromptSendingAttack):
    def __init__(self, *, objective_target: MockPromptTarget, text_setting: str | None = None) -> None:
        super().__init__(objective_target=objective_target)
        self.text_setting = text_setting


class _ChangedTarget(MockPromptTarget):
    pass


@pytest.fixture
def edit_catalog(patch_central_database: Any) -> Iterator[AttackTechniqueRegistry]:
    with patch.dict(Registry._singletons, {}, clear=True):
        targets = TargetRegistry.get_registry_singleton().instances
        targets.register(MockPromptTarget(), name="adversarial_chat")
        targets.register(MockPromptTarget(), name="objective")
        targets.register(MockPromptTarget(), name="objective_scorer_chat")
        converters = ConverterRegistry.get_registry_singleton().instances
        converters.register(Base64Converter(), name="base64")
        converters.register(ROT13Converter(), name="rot13")
        factory = AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_movie_script",
            description="Write a movie script.",
            adversarial_chat_system_prompt=SeedPrompt(
                value="Write a script.\nObjective: {{ objective }}\nTurns: {{ num_turns }}",
                data_type="text",
                parameters=["objective", "num_turns"],
                is_jinja_template=True,
                metadata={"preserve": "user prompt metadata"},
            ),
            simulated_target_system_prompt=SeedPrompt(
                value="Continue {{ objective }} for {{ num_turns }} turns.",
                data_type="text",
                parameters=["objective", "num_turns"],
            ),
            next_message_system_prompt=SeedPrompt(
                value="Next {{ objective }}: {{ conversation_context }}",
                data_type="text",
                parameters=["objective", "conversation_context"],
            ),
            num_turns=2,
        )
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_factories(
            [factory, AttackTechniqueFactory(name="plain", attack_class=PromptSendingAttack)]
        )
        with patch.object(Execute, "_get_default_objective_scorer", return_value=SubStringScorer(substring="done")):
            yield registry


def _specification(*, changes: TechniqueChanges | None = None) -> TechniqueOverride:
    config = TechniqueEditService.configuration("role_play_movie_script")
    return TechniqueOverride(
        source_name=config.source_name, source_identity=config.source_identity, changes=changes or TechniqueChanges()
    )


def test_effective_projection_noop_and_original_immutability(edit_catalog: AttackTechniqueRegistry) -> None:
    source = edit_catalog.instances.get("role_play_movie_script")
    assert source is not None
    before = source.seed_technique.model_dump()
    revision = edit_catalog.catalog_revision
    with patch.object(source, "create", side_effect=AssertionError("Must not construct attacks")):
        config = TechniqueEditService.configuration(source.name)
        assert config.settings.description == "Write a movie script."
        assert config.settings.simulation.num_turns == 2
        assert "\nObjective: {{ objective }}" in config.settings.simulation.adversarial_chat_system_prompt
        assert config.settings.simulation.simulated_target_system_prompt.startswith("Continue")
        admitted = TechniqueEditService.validate(_specification())
        copy = TechniqueEditService.resolve(admitted.override)
        assert copy is not source
        assert copy.get_identifier() == source.get_identifier()
        assert copy.get_configuration_identity() == source.get_configuration_identity()
    assert source.seed_technique.model_dump() == before
    assert edit_catalog.catalog_revision == revision


def test_simulation_roundtrip_preserves_templates_and_metadata(edit_catalog: AttackTechniqueRegistry) -> None:
    source = edit_catalog.instances.get("role_play_movie_script")
    assert source is not None
    before = source.seed_technique.model_dump()
    original = source.seed_technique.seeds[0]
    text = "New movie.\n{{ objective }} {{ num_turns }} {% if objective %}literal{% endif %}"
    admitted = TechniqueEditService.validate(
        _specification(
            changes=TechniqueChanges(
                description="Edited movie",
                simulation=SimulationChanges(num_turns=4, adversarial_chat_system_prompt=text),
            )
        )
    )
    copy = TechniqueEditService.resolve(TechniqueOverride.model_validate_json(admitted.override.model_dump_json()))
    updated = copy.seed_technique.seeds[0]
    assert isinstance(updated, SeedSimulatedConversation)
    assert updated.num_turns == 4
    assert updated.adversarial_chat_system_prompt.value == text
    assert updated.adversarial_chat_system_prompt.parameters == original.adversarial_chat_system_prompt.parameters
    assert updated.adversarial_chat_system_prompt.metadata == original.adversarial_chat_system_prompt.metadata
    assert updated.adversarial_chat_system_prompt.is_jinja_template
    assert json.loads(updated.value)["num_turns"] == 4
    restored = SeedSimulatedConversation.model_validate(json.loads(updated.value))
    assert restored.get_identifier() == updated.get_identifier()
    assert copy.get_identifier() != source.get_identifier()
    assert copy.requirements is source.requirements
    assert source.seed_technique.model_dump() == before


def test_real_movie_script_projection_and_lossless_roundtrip(edit_catalog: AttackTechniqueRegistry) -> None:
    source = AttackTechniqueFactory.with_simulated_conversation(
        name="role_play_movie_script",
        description="Frames the objective as a movie beat by casting the adversarial chat as a screenwriter.",
        adversarial_chat_system_prompt=SeedPrompt.from_yaml_file(
            EXECUTOR_SEED_PROMPT_PATH / "red_teaming" / "role_play" / "role_play_movie_script.yaml"
        ),
        next_message_system_prompt=load_next_message_prompt(
            EXECUTOR_SIMULATED_TARGET_PATH / "role_play_next_message.yaml"
        ),
        num_turns=2,
    ).with_attack_kwargs(attack_kwargs={"max_attempts_on_failure": 4})
    edit_catalog.instances.unregister(source.name)
    edit_catalog.register_from_factories([source])
    original = source.seed_technique.seeds[0]
    config = TechniqueEditService.configuration(source.name)
    assert config.settings.params["max_attempts_on_failure"] == 4
    assert "max_attempts_on_failure" in config.supplied_parameters
    assert config.settings.simulation.num_turns == 2
    assert config.settings.simulation.simulated_target_system_prompt == original.simulated_target_system_prompt.value
    assert "You are a screenwriter" in config.settings.simulation.adversarial_chat_system_prompt
    noop = TechniqueEditService.resolve(TechniqueEditService.validate(_specification()).override)
    assert noop.get_identifier() == source.get_identifier()
    edited = TechniqueEditService.resolve(
        TechniqueEditService.validate(
            _specification(
                changes=TechniqueChanges(
                    simulation=SimulationChanges(
                        num_turns=3,
                        adversarial_chat_system_prompt=original.adversarial_chat_system_prompt.value
                        + "\n{{ objective }}",
                    )
                )
            )
        ).override
    )
    prompt = edited.seed_technique.seeds[0].adversarial_chat_system_prompt
    assert prompt.parameters == original.adversarial_chat_system_prompt.parameters
    assert prompt.response_json_schema == original.adversarial_chat_system_prompt.response_json_schema
    assert prompt.response_json_schema is not None
    assert prompt.is_jinja_template == original.adversarial_chat_system_prompt.is_jinja_template
    assert source.seed_technique.seeds[0].num_turns == 2
    assert noop.get_effective_settings().attack_kwargs == source.get_effective_settings().attack_kwargs


def test_missing_edited_reference_fails_and_unedited_defaults_stay_omitted(
    edit_catalog: AttackTechniqueRegistry,
) -> None:
    admitted = TechniqueEditService.validate(_specification(changes=TechniqueChanges(request_converters=["base64"])))
    ConverterRegistry.get_registry_singleton().instances.unregister("base64")
    with pytest.raises(ValueError, match="not found"):
        TechniqueEditService.resolve(admitted.override)
    source = edit_catalog.instances.get("plain")
    config = TechniqueEditService.configuration("plain")
    assert config.settings.params
    assert config.supplied_parameters == []
    spec = TechniqueOverride(source_name="plain", source_identity=config.source_identity)
    restored = TechniqueOverride.model_validate_json(TechniqueEditService.validate(spec).override.model_dump_json())
    assert restored.changes.model_fields_set == set()
    assert TechniqueEditService.resolve(restored).get_effective_settings().attack_kwargs == {}
    assert source.get_effective_settings().attack_kwargs == {}


def test_fixed_unregistered_attack_class_can_edit_and_clear_supported_values(
    edit_catalog: AttackTechniqueRegistry,
) -> None:
    source = AttackTechniqueFactory(name="custom", attack_class=_TextAttack, attack_kwargs={"text_setting": "source"})
    edit_catalog.register_from_factories([source])
    configuration = TechniqueEditService.configuration(source.name)
    admitted = TechniqueEditService.validate(
        TechniqueOverride(
            source_name=source.name,
            source_identity=configuration.source_identity,
            changes=TechniqueChanges(params={"text_setting": ""}),
        )
    )
    copy = TechniqueEditService.resolve(admitted.override)
    assert copy.attack_class is _TextAttack
    assert copy.get_effective_settings().attack_kwargs["text_setting"] == ""
    assert source.get_effective_settings().attack_kwargs["text_setting"] == "source"


@pytest.mark.parametrize("mode", ["fixed", "none"])
def test_simulation_final_message_modes(edit_catalog: AttackTechniqueRegistry, mode: str) -> None:
    group = AttackTechniqueSeedGroup(
        seeds=[
            SeedSimulatedConversation(
                num_turns=2,
                adversarial_chat_system_prompt=SeedPrompt(value="Simulate {{ objective }}", data_type="text"),
                simulated_target_system_prompt=SeedPrompt(value="Continue", data_type="text"),
            ),
            *(
                [SeedPrompt(value="yes.", data_type="text", role="user", sequence=4, is_general_technique=True)]
                if mode == "fixed"
                else []
            ),
        ]
    )
    source = AttackTechniqueFactory(name=mode, attack_class=PromptSendingAttack, seed_technique=group)
    edit_catalog.register_from_factories([source])
    configuration = TechniqueEditService.configuration(mode)
    changes = SimulationChanges(num_turns=5, **({"final_user_message": "continue."} if mode == "fixed" else {}))
    admitted = TechniqueEditService.validate(
        TechniqueOverride(
            source_name=mode,
            source_identity=configuration.source_identity,
            changes=TechniqueChanges(simulation=changes),
        )
    )
    copy = TechniqueEditService.resolve(admitted.override)
    assert copy.seed_technique.seeds[0].next_message_system_prompt is None
    if mode == "fixed":
        assert copy.seed_technique.prompts[0].value == "continue."
        assert copy.seed_technique.prompts[0].sequence == 10
        assert group.prompts[0].sequence == 4
    else:
        assert not copy.seed_technique.prompts
    with pytest.raises(ValueError, match="message mode"):
        group.with_simulation_changes(SimulationChanges(next_message_system_prompt="Not a mode switch"))


def test_edit_pins_source_and_changed_dependencies(edit_catalog: AttackTechniqueRegistry) -> None:
    spec = _specification(
        changes=TechniqueChanges(request_converters=["rot13", "base64"], adversarial_chat="objective")
    )
    admitted = TechniqueEditService.validate(spec)
    copy = TechniqueEditService.resolve(admitted.override)
    config = copy.get_effective_settings()
    target = TargetRegistry.get_registry_singleton().instances.get("objective")
    assert config.adversarial_chat is target
    assert [
        type(entry.converters[0]).__name__
        for entry in config.attack_kwargs["attack_converter_config"].request_converters
    ] == ["ROT13Converter", "Base64Converter"]
    assert copy.seed_technique is edit_catalog.instances.get(spec.source_name).seed_technique
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="other")
    # An unrelated catalog addition does not invalidate the edit.
    assert TechniqueEditService.resolve(admitted.override)
    altered = admitted.override.model_copy(update={"dependencies": {}})
    with pytest.raises(ValueError, match="dependencies changed"):
        TechniqueEditService.resolve(altered)
    source = edit_catalog.instances.get(spec.source_name)
    edit_catalog.instances.unregister(source.name)
    edit_catalog.instances.register(
        source.with_attack_kwargs(attack_kwargs={"max_attempts_on_failure": 3}), name=source.name
    )
    with pytest.raises(ValueError, match="Source technique configuration changed"):
        TechniqueEditService.resolve(admitted.override)


def test_opaque_converter_settings_are_retained_and_rejected_for_edit(edit_catalog: AttackTechniqueRegistry) -> None:
    source = edit_catalog.instances.get("plain")
    converter = ConverterRegistry.get_registry_singleton().instances.get("base64")
    advanced = AttackConverterConfig(
        request_converters=[ConverterConfiguration(converters=[converter], indexes_to_apply=[1])]
    )
    source = source.with_attack_kwargs(attack_kwargs={"attack_converter_config": advanced})
    edit_catalog.instances.unregister(source.name)
    edit_catalog.instances.register(source, name=source.name)
    config = TechniqueEditService.configuration("plain")
    assert "request_converters" in config.read_only
    spec = TechniqueOverride(
        source_name="plain", source_identity=config.source_identity, changes=TechniqueChanges(description="local")
    )
    copy = TechniqueEditService.resolve(TechniqueEditService.validate(spec).override)
    assert copy.get_effective_settings().attack_kwargs["attack_converter_config"] is advanced
    with pytest.raises(ValueError, match="read-only"):
        TechniqueEditService.validate(spec.model_copy(update={"changes": TechniqueChanges(request_converters=[])}))


async def test_isolated_run_overrides_estimate_and_resume_async(edit_catalog: AttackTechniqueRegistry) -> None:
    first, second = [
        TechniqueEditService.validate(
            _specification(changes=TechniqueChanges(simulation=SimulationChanges(num_turns=turns)))
        ).override
        for turns in (3, 5)
    ]
    results = await asyncio.gather(
        *[
            asyncio.to_thread(
                ScenarioConfigurationResolver.resolve_configuration,
                scenario_name="technique.execute",
                scenario_class=Execute,
                techniques=["role_play_movie_script"],
                technique_override=spec,
            )
            for spec in (first, second)
        ]
    )
    assert [result["local_technique_factory"].seed_technique.seeds[0].num_turns for result in results] == [3, 5]
    assert edit_catalog.instances.get("role_play_movie_script").seed_technique.seeds[0].num_turns == 2
    with patch.object(Execute, "_get_default_objective_scorer", return_value=SubStringScorer(substring="done")):
        estimate = await ScenarioService().estimate_scenario_run_size_async(
            scenario_name="technique.execute",
            request=ScenarioRunSizeEstimateRequest(
                techniques=["role_play_movie_script"], technique_override=first, scenario_params={"objective": "write"}
            ),
        )
        assert estimate.total_attack_count == 1
        service = ScenarioRunService()
        request = RunScenarioRequest(
            scenario_name="technique.execute",
            target_name="objective",
            techniques=["role_play_movie_script"],
            technique_override=first,
            scenario_params={"objective": "write", "retries_on_objective_failure": 1},
        )
        with patch.object(service, "_run_initializers_async", return_value=None):
            prepared = await service._prepare_run_async(request=request)
            stored = await CentralMemory.get_memory_instance().get_scenario_result_header_async(
                scenario_result_id=prepared.scenario._scenario_result_id
            )
            restored = service._restore_launch_request(stored=stored)
            assert restored.technique_override == first
            resumed = await service._prepare_run_async(request=restored)
            assert resumed.scenario._scenario_result_id == prepared.scenario._scenario_result_id
            targets = TargetRegistry.get_registry_singleton().instances
            assert all(not entry.instance.prompt_sent for entry in targets.get_all_instances())
            targets.unregister("adversarial_chat")
            targets.register(_ChangedTarget(), name="adversarial_chat")
            with pytest.raises(ValueError, match="run-local technique does not match"):
                await service._prepare_run_async(request=restored)


def test_edit_routes_and_invalid_selection(
    edit_catalog: AttackTechniqueRegistry, compatibility_headers: dict[str, str]
) -> None:
    client = TestClient(app, headers=compatibility_headers)
    config = client.get("/api/techniques/role_play_movie_script/configuration")
    assert config.status_code == 200, config.text
    spec = _specification(changes=TechniqueChanges(simulation=SimulationChanges(num_turns=6)))
    response = client.post(
        "/api/techniques/role_play_movie_script/edit", json=spec.model_dump(mode="json", exclude_unset=True)
    )
    assert response.status_code == 200, response.text
    assert response.json()["configuration"]["settings"]["simulation"]["num_turns"] == 6
    with pytest.raises(ValueError, match="one selected"):
        ScenarioConfigurationResolver.resolve_configuration(
            scenario_name="technique.execute",
            scenario_class=Execute,
            techniques=["plain"],
            technique_override=TechniqueOverride.model_validate(response.json()["override"]),
        )
