# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Lossless editor projections and source-pinned, run-local factory reconstruction."""

import asyncio
import base64
import codecs
import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.backend.services.scenario_service import ScenarioService
from pyrit.backend.services.technique_edit_service import TechniqueEditService
from pyrit.backend.services.technique_service import TechniqueService
from pyrit.common.apply_defaults import GlobalDefaultValues
from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH, EXECUTOR_SIMULATED_TARGET_PATH
from pyrit.converter import Base64Converter, CaesarConverter, ImageOverlayConverter, ROT13Converter
from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackExecutor,
    AttackParameters,
    AttackScoringConfig,
    AttackStrategy,
    PromptSendingAttack,
    RedTeamingAttack,
)
from pyrit.executor.attack.component import PrependedConversationConfig
from pyrit.executor.attack.multi_turn.simulated_conversation import SimulatedConversationResult
from pyrit.memory import CentralMemory
from pyrit.message_normalizer import ConversationContextNormalizer
from pyrit.models import (
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    Message,
    SeedGroup,
    SeedGroupRequirements,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
    load_next_message_prompt,
)
from pyrit.models.catalog.scenario import RunScenarioRequest, ScenarioRunSizeEstimateRequest
from pyrit.models.component_spec import RegisteredComponentReference, SourceInstanceSpec
from pyrit.models.technique_edit import (
    SimulationChanges,
    SimulationSettings,
    TechniqueChanges,
    TechniqueOverride,
    TechniqueSettings,
)
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, Registry, ScorerRegistry, TargetRegistry
from pyrit.scenario.core import AttackTechniqueFactory
from pyrit.scenario.core.technique_requirements import TechniqueRequirements
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


def test_temporary_converter_settings_survive_replay_without_registration(
    edit_catalog: AttackTechniqueRegistry,
) -> None:
    registry = ConverterRegistry.get_registry_singleton()
    converter = CaesarConverter(caesar_offset=1)
    registry.instances.register(converter, name="caesar")
    spec = SourceInstanceSpec(
        source_name="caesar", source_hash=converter.get_identifier().hash, params={"caesar_offset": 7}
    )
    changes = TechniqueChanges(request_converters=[spec, "base64", "caesar"], response_converters=[spec])
    admitted = TechniqueEditService.validate(_specification(changes=changes))
    assert admitted.configuration.settings.request_converters == changes.request_converters
    assert "request_converters" in admitted.configuration.editable_fields
    assert "response_converters" in admitted.configuration.editable_fields
    assert "request_converters" not in admitted.configuration.read_only
    replay = TechniqueOverride.model_validate_json(admitted.override.model_dump_json())
    copied = TechniqueEditService.resolve(replay)
    chain = copied.get_converter_config().request_converters
    assert chain[0].converters[0].get_identifier().params["caesar_offset"] == 7
    assert chain[2].converters[0] is converter
    assert registry.instances.get_names() == ["base64", "caesar", "rot13"]
    assert converter.get_identifier().params["caesar_offset"] == 1
    assert edit_catalog.get_factories()["role_play_movie_script"].get_converter_config().request_converters == []
    registry.instances.register(CaesarConverter(caesar_offset=2), name="caesar", replace=True)
    with pytest.raises(ValueError):
        TechniqueEditService.resolve(replay)


def test_temporary_converter_effective_hash_is_checked(edit_catalog: AttackTechniqueRegistry) -> None:
    converter = CaesarConverter(caesar_offset=1)
    ConverterRegistry.get_registry_singleton().instances.register(converter, name="caesar")
    spec = SourceInstanceSpec(
        source_name="caesar",
        source_hash=converter.get_identifier().hash,
        params={"caesar_offset": 3},
        effective_hash="stale",
    )
    with pytest.raises(ValueError, match="Temporary converter configuration has changed"):
        TechniqueEditService.validate(_specification(changes=TechniqueChanges(request_converters=[spec])))


def test_temporary_file_converter_preserves_its_file_and_rejects_replacement(
    edit_catalog: AttackTechniqueRegistry,
) -> None:
    source = ImageOverlayConverter(base_image="registered-image.png")
    ConverterRegistry.get_registry_singleton().instances.register(source, name="overlay")
    spec = SourceInstanceSpec(source_name="overlay", source_hash=source.get_identifier().hash, params={"opacity": 0.5})
    admitted = TechniqueEditService.validate(_specification(changes=TechniqueChanges(request_converters=[spec])))
    factory = TechniqueEditService.resolve(admitted.override)
    edited = factory.get_converter_config().request_converters[0].converters[0].get_identifier()
    assert edited.params["base_image"] == "registered-image.png"
    assert edited.params["opacity"] == 0.5
    spec.params["base_image"] = "data:image/png;base64,AAAA"
    with pytest.raises(ValueError, match="Create a registered converter"):
        TechniqueEditService.validate(_specification(changes=TechniqueChanges(request_converters=[spec])))


async def test_temporary_converter_is_used_by_the_attack_async(edit_catalog: AttackTechniqueRegistry) -> None:
    source = CaesarConverter(caesar_offset=1)
    ConverterRegistry.get_registry_singleton().instances.register(source, name="caesar")
    config = TechniqueEditService.configuration("plain")
    admitted = TechniqueEditService.validate(
        TechniqueOverride(
            source_name=config.source_name,
            source_identity=config.source_identity,
            changes=TechniqueChanges(
                request_converters=[
                    SourceInstanceSpec(
                        source_name="caesar", source_hash=source.get_identifier().hash, params={"caesar_offset": 3}
                    ),
                ]
            ),
        )
    )
    factory = TechniqueEditService.resolve(admitted.override)
    target = MockPromptTarget()
    technique = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
    await technique.attack.execute_async(objective="abc")
    assert target.prompt_sent == ["def"]
    assert source.get_identifier().params["caesar_offset"] == 1


@pytest.mark.parametrize("value", [0, 2, -1, True, "2", 1.5, None, [], {}])
async def test_creation_and_editing_share_strict_input_and_attack_validation_async(
    edit_catalog: AttackTechniqueRegistry, value: Any
) -> None:
    configuration = TechniqueEditService.configuration("plain")
    specification = TechniqueOverride(
        source_name="plain",
        source_identity=configuration.source_identity,
        changes=TechniqueChanges(params={"max_attempts_on_failure": value}),
    )
    request = CreateTechniqueRequest(
        name="parity_created",
        type="PromptSendingAttack",
        params={"max_attempts_on_failure": value},
    )
    if type(value) is int and value >= 0:
        created = await TechniqueService().create_async(request)
        admitted = TechniqueEditService.validate(specification)
        copied = TechniqueEditService.resolve(admitted.override)
        assert (
            copied.get_effective_settings().attack_kwargs
            == edit_catalog.instances.get(created.name).get_effective_settings().attack_kwargs
        )
    else:
        revision = edit_catalog.catalog_revision
        with pytest.raises(ValueError):
            await TechniqueService().create_async(request)
        with pytest.raises(ValueError):
            TechniqueEditService.validate(specification)
        assert edit_catalog.catalog_revision == revision


def test_snapshot_and_patch_preserve_existing_presence_and_null_serialization(
    edit_catalog: AttackTechniqueRegistry,
) -> None:
    configuration = TechniqueEditService.configuration("role_play_movie_script")
    assert isinstance(configuration.settings, TechniqueSettings)
    assert isinstance(configuration.settings.simulation, SimulationSettings)
    payload = configuration.model_dump(mode="json")
    assert set(payload) == {
        "source_name",
        "source_identity",
        "attack_type",
        "settings",
        "parameters",
        "editable_fields",
        "supplied_parameters",
        "read_only",
        "supports_prepended_conversation",
        "supports_prompt",
    }
    assert payload["settings"] == {
        "description": "Write a movie script.",
        "params": {"max_attempts_on_failure": 0},
        "adversarial_chat": None,
        "request_converters": [],
        "response_converters": [],
        "simulation": {
            "num_turns": 2,
            "adversarial_chat_system_prompt": "Write a script.\nObjective: {{ objective }}\nTurns: {{ num_turns }}",
            "simulated_target_system_prompt": "Continue {{ objective }} for {{ num_turns }} turns.",
            "next_message_system_prompt": "Next {{ objective }}: {{ conversation_context }}",
            "final_user_message": None,
        },
    }
    assert TechniqueChanges().model_dump(mode="json") == {}
    assert TechniqueChanges(description=None).model_dump(mode="json") == {"description": None}
    assert TechniqueChanges(simulation=SimulationChanges(next_message_system_prompt=None)).model_dump(mode="json") == {
        "simulation": {"next_message_system_prompt": None},
    }
    admitted = TechniqueEditService.validate(_specification())
    assert admitted.configuration.model_dump(mode="json") == payload
    assert admitted.override.model_dump(mode="json")["changes"] == {}


def test_domain_copy_validates_final_merged_inputs_without_constructing_an_attack(
    edit_catalog: AttackTechniqueRegistry,
) -> None:
    source = edit_catalog.instances.get("plain")
    before = source.get_configuration_identity()
    settings = source.get_effective_settings()
    settings.attack_kwargs["max_attempts_on_failure"] = -1
    with pytest.raises(ValueError, match="non-negative"):
        source.with_settings(settings=settings)
    assert source.get_configuration_identity() == before


def test_red_team_snapshot_retains_explicit_null_prompts_without_simulation(
    edit_catalog: AttackTechniqueRegistry,
) -> None:
    source = AttackTechniqueFactory(name="red_teaming", attack_class=RedTeamingAttack, description="Red team")
    edit_catalog.register_from_factories([source])
    configuration = TechniqueEditService.configuration(source.name)
    settings = configuration.model_dump(mode="json")["settings"]
    assert settings == {
        "description": "Red team",
        "params": {"max_turns": 10, "score_last_turn_only": False},
        "request_converters": [],
        "response_converters": [],
        "adversarial_chat": None,
        "adversarial_system_prompt": None,
        "adversarial_seed_prompt": None,
        "adversarial_prompt_template": None,
    }
    specification = TechniqueOverride(source_name=source.name, source_identity=configuration.source_identity)
    assert (
        TechniqueEditService.resolve(TechniqueEditService.validate(specification).override).get_configuration_identity()
        == source.get_configuration_identity()
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


def test_history_support_projection_uses_source_contracts_without_constructing_attacks(
    edit_catalog: AttackTechniqueRegistry,
) -> None:
    plain = edit_catalog.instances.get("plain")
    simulated = edit_catalog.instances.get("role_play_movie_script")
    assert plain is not None and simulated is not None
    factories = [
        plain,
        simulated,
        AttackTechniqueFactory(
            name="objective_only",
            attack_class=PromptSendingAttack,
            requirements=TechniqueRequirements(
                seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True),
            ),
        ),
        AttackTechniqueFactory(
            name="no_history",
            attack_class=PromptSendingAttack,
            attack_kwargs={
                "params_type": AttackParameters.excluding("prepended_conversation"),
            },
        ),
        AttackTechniqueFactory(name="red_team", attack_class=RedTeamingAttack),
    ]
    edit_catalog.register_from_factories(factories[2:])
    for factory, supported in zip(factories, (True, False, False, False, True), strict=True):
        with patch.object(factory, "create", side_effect=AssertionError("Must not construct an attack")):
            projection = TechniqueEditService.configuration(factory.name)
        assert projection.supports_prepended_conversation is supported
        assert projection.model_dump(mode="json")["supports_prepended_conversation"] is supported


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


@pytest.mark.parametrize("config_source", ["inherited", "explicit_none", "explicit", "empty"])
@pytest.mark.parametrize("field", ["request_converters", "response_converters"])
@pytest.mark.parametrize("clear", [False, True])
def test_converter_edits_preserve_effective_unedited_chain(
    edit_catalog: AttackTechniqueRegistry, config_source: str, field: str, clear: bool
) -> None:
    converters = ConverterRegistry.get_registry_singleton().instances
    base64 = converters.get("base64")
    rot13 = converters.get("rot13")
    inherited = AttackConverterConfig(
        request_converters=ConverterConfiguration.from_converters(converters=[base64]),
        response_converters=ConverterConfiguration.from_converters(converters=[rot13]),
    )
    explicit = AttackConverterConfig(
        request_converters=ConverterConfiguration.from_converters(converters=[rot13]),
        response_converters=ConverterConfiguration.from_converters(converters=[base64]),
    )
    kwargs = {
        "inherited": {},
        "explicit_none": {"attack_converter_config": None},
        "explicit": {"attack_converter_config": explicit},
        "empty": {"attack_converter_config": AttackConverterConfig()},
    }[config_source]
    source = AttackTechniqueFactory(name="converter_defaults", attack_class=PromptSendingAttack, attack_kwargs=kwargs)
    edit_catalog.register_from_factories([source])
    defaults = GlobalDefaultValues()
    defaults.set_default_value(class_type=AttackStrategy, parameter_name="attack_converter_config", value=inherited)
    with patch("pyrit.common.apply_defaults._global_default_values", defaults):
        expected = kwargs.get("attack_converter_config") or inherited
        before = source.get_configuration_identity()
        revision = edit_catalog.catalog_revision
        configuration = TechniqueEditService.configuration(source.name)
        noop = TechniqueEditService.validate(
            TechniqueOverride(source_name=source.name, source_identity=configuration.source_identity)
        )
        assert TechniqueEditService.resolve(noop.override).get_effective_settings().attack_kwargs == kwargs
        for key in ("request_converters", "response_converters"):
            assert getattr(configuration.settings, key) == [
                "base64" if converter is base64 else "rot13"
                for entry in getattr(expected, key)
                for converter in entry.converters
            ]
        changes = TechniqueChanges.model_validate({field: [] if clear else ["base64", "rot13"]})
        admitted = TechniqueEditService.validate(
            TechniqueOverride(source_name=source.name, source_identity=configuration.source_identity, changes=changes)
        )
        saved = TechniqueOverride.model_validate_json(admitted.override.model_dump_json())
        restored = TechniqueEditService.resolve(saved)
        config = restored.get_effective_settings().attack_kwargs["attack_converter_config"]
        other = "response_converters" if field == "request_converters" else "request_converters"
        assert getattr(config, other) is getattr(expected, other)
        assert [converter for entry in getattr(config, field) for converter in entry.converters] == (
            [] if clear else [base64, rot13]
        )
        assert source.get_configuration_identity() == before
        assert edit_catalog.catalog_revision == revision
        technique = restored.create(
            objective_target=TargetRegistry.get_registry_singleton().instances.get("objective"),
            attack_scoring_config=AttackScoringConfig(),
        )
        assert technique.attack._request_converters is config.request_converters
        assert technique.attack._response_converters is config.response_converters
        assert inherited.request_converters[0].converters == [base64]
        assert inherited.response_converters[0].converters == [rot13]
        defaults.set_default_value(
            class_type=PromptSendingAttack, parameter_name="attack_converter_config", value=AttackConverterConfig()
        )
        with pytest.raises(ValueError, match="Source technique configuration changed"):
            TechniqueEditService.resolve(saved)


def test_inherited_conditional_converter_chain_is_read_only(edit_catalog: AttackTechniqueRegistry) -> None:
    converter = ConverterRegistry.get_registry_singleton().instances.get("base64")
    inherited = AttackConverterConfig(
        request_converters=[ConverterConfiguration(converters=[converter], indexes_to_apply=[1])]
    )
    defaults = GlobalDefaultValues()
    defaults.set_default_value(class_type=AttackStrategy, parameter_name="attack_converter_config", value=inherited)
    with patch("pyrit.common.apply_defaults._global_default_values", defaults):
        configuration = TechniqueEditService.configuration("plain")
        assert "request_converters" in configuration.read_only
        specification = TechniqueOverride(
            source_name="plain",
            source_identity=configuration.source_identity,
            changes=TechniqueChanges(response_converters=["rot13"]),
        )
        restored = TechniqueEditService.resolve(TechniqueEditService.validate(specification).override)
        config = restored.get_effective_settings().attack_kwargs["attack_converter_config"]
        assert config.request_converters is inherited.request_converters
        assert config.request_converters[0].indexes_to_apply == [1]
        with pytest.raises(ValueError, match="read-only"):
            TechniqueEditService.validate(
                specification.model_copy(update={"changes": TechniqueChanges(request_converters=[])})
            )


@pytest.mark.parametrize("editable_history", [False, True])
async def test_gui_converter_edit_converts_simulated_history_on_replay_only_async(
    edit_catalog: AttackTechniqueRegistry, editable_history: bool
) -> None:
    source = edit_catalog.instances.get("role_play_movie_script")
    assert source is not None and source.seed_technique is not None
    source_identity = source.get_configuration_identity()
    revision = edit_catalog.catalog_revision
    admitted = TechniqueEditService.validate(
        _specification(changes=TechniqueChanges(request_converters=["base64"], response_converters=["rot13"]))
    )
    factory = TechniqueEditService.resolve(TechniqueOverride.model_validate_json(admitted.override.model_dump_json()))
    target = TargetRegistry.get_registry_singleton().instances.get("objective")
    assert isinstance(target, MockPromptTarget)
    target.apply_capabilities(
        capabilities=TargetCapabilities(supports_multi_turn=True, supports_editable_history=editable_history)
    )
    technique = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
    group = AttackSeedGroup(seeds=[SeedObjective(value="Test replay")]).with_technique(technique=source.seed_technique)
    generated = [
        SeedPrompt(value=text, role=role, sequence=sequence, data_type="text")
        for sequence, (role, text) in enumerate(
            [
                ("user", "first request"),
                ("assistant", "first reply"),
                ("user", "second request"),
                ("assistant", "second reply"),
                ("user", "final request"),
            ]
        )
    ]
    SeedGroup(seeds=generated)
    original_seeds = [seed.model_dump() for seed in generated]
    request_converter = ConverterRegistry.get_registry_singleton().instances.get("base64")
    response_converter = ConverterRegistry.get_registry_singleton().instances.get("rot13")
    with (
        patch(
            "pyrit.executor.attack.multi_turn.simulated_conversation.generate_simulated_conversation_async",
            return_value=SimulatedConversationResult(seed_prompts=generated, related_conversations=frozenset()),
        ) as generate,
        patch.object(target, "_send_prompt_to_target_async", wraps=target._send_prompt_to_target_async) as send,
        patch.object(request_converter, "convert_async", wraps=request_converter.convert_async) as request_conversion,
        patch.object(
            response_converter, "convert_async", wraps=response_converter.convert_async
        ) as response_conversion,
    ):
        execution = await AttackExecutor().execute_attack_from_seed_groups_async(
            attack=technique.attack,
            seed_groups=[group],
            adversarial_chat=TargetRegistry.get_registry_singleton().instances.get("adversarial_chat"),
            objective_scorer=SubStringScorer(substring="done"),
        )
    generate.assert_awaited_once()
    assert generate.call_args.kwargs.get("attack_converter_config") is None
    send.assert_awaited_once()
    received = send.call_args.kwargs["normalized_conversation"]
    encoded = [base64.b64encode(seed.value.encode()).decode() for seed in generated]
    if editable_history:
        assert [message.get_value() for message in received] == encoded
    else:
        assert all(text in received[0].get_value() for text in encoded)
    result = execution.completed_results[0]
    pieces = await CentralMemory.get_memory_instance().get_message_pieces_async(conversation_id=result.conversation_id)
    replayed = {piece.original_value: piece for piece in pieces}
    for seed, text in zip(generated, encoded, strict=True):
        assert replayed[seed.value].converted_value == text
        assert [identifier.class_name for identifier in replayed[seed.value].converter_identifiers] == [
            "Base64Converter"
        ]
    assert replayed["default"].converted_value == codecs.encode("default", "rot_13")
    assert request_conversion.await_count == 5
    assert response_conversion.await_count == 1
    assert [seed.model_dump() for seed in generated] == original_seeds
    assert source.get_configuration_identity() == source_identity
    assert edit_catalog.catalog_revision == revision


async def test_gui_created_technique_converts_both_history_roles_but_not_system_prompt_async(
    edit_catalog: AttackTechniqueRegistry,
) -> None:
    created = await TechniqueService().create_async(
        CreateTechniqueRequest(name="gui_replay", type="PromptSendingAttack", request_converters=["base64"])
    )
    factory = edit_catalog.instances.get(created.name)
    assert factory is not None
    target = TargetRegistry.get_registry_singleton().instances.get("objective")
    assert isinstance(target, MockPromptTarget)
    technique = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
    with patch.object(target, "_send_prompt_to_target_async", wraps=target._send_prompt_to_target_async) as send:
        await technique.attack.execute_async(
            objective="Test replay",
            prepended_conversation=[
                Message.from_prompt(prompt="system prompt", role="system"),
                Message.from_prompt(prompt="history request", role="user"),
                Message.from_prompt(prompt="history reply", role="assistant"),
            ],
            next_message=Message.from_prompt(prompt="live request", role="user"),
        )
    received = send.call_args.kwargs["normalized_conversation"]
    assert [message.get_value() for message in received] == [
        "system prompt",
        *[base64.b64encode(text.encode()).decode() for text in ("history request", "history reply", "live request")],
    ]


@pytest.mark.parametrize("inherited", [False, True])
def test_gui_request_converters_keep_history_formatting_and_existing_role_opt_ins(
    edit_catalog: AttackTechniqueRegistry, inherited: bool
) -> None:
    normalizer = ConversationContextNormalizer()
    config = PrependedConversationConfig(message_normalizer=normalizer, apply_converters_to_roles=["system", "user"])
    source = edit_catalog.instances.get("role_play_movie_script")
    assert source is not None
    if not inherited:
        source = source.with_attack_kwargs(attack_kwargs={"prepended_conversation_config": config})
        edit_catalog.instances.unregister(source.name)
        edit_catalog.register_from_factories([source])
    defaults = GlobalDefaultValues()
    defaults.set_default_value(class_type=AttackStrategy, parameter_name="prepended_conversation_config", value=config)
    with patch("pyrit.common.apply_defaults._global_default_values", defaults):
        identity = source.get_configuration_identity()
        admitted = TechniqueEditService.validate(
            _specification(changes=TechniqueChanges(request_converters=["base64"]))
        )
        restored = TechniqueEditService.resolve(admitted.override)
        copied = restored.get_effective_attack_kwargs()["prepended_conversation_config"]
        assert copied is not config
        assert copied.apply_converters_to_roles == ["system", "user", "assistant"]
        assert copied.message_normalizer is normalizer
        assert config.apply_converters_to_roles == ["system", "user"]
        assert source.get_configuration_identity() == identity


@pytest.mark.parametrize(
    "changes",
    [
        TechniqueChanges(),
        TechniqueChanges(description="Only description"),
        TechniqueChanges(request_converters=[]),
        TechniqueChanges(response_converters=["rot13"]),
    ],
)
def test_other_gui_edits_do_not_change_history_conversion_policy(
    edit_catalog: AttackTechniqueRegistry, changes: TechniqueChanges
) -> None:
    admitted = TechniqueEditService.validate(_specification(changes=changes))
    restored = TechniqueEditService.resolve(admitted.override)
    assert "prepended_conversation_config" not in restored.get_effective_settings().attack_kwargs


@pytest.mark.parametrize("request_converters", [None, []])
async def test_gui_creation_without_request_converters_keeps_default_history_policy_async(
    edit_catalog: AttackTechniqueRegistry, request_converters: list[str] | None
) -> None:
    created = await TechniqueService().create_async(
        CreateTechniqueRequest(
            name="without_requests",
            type="PromptSendingAttack",
            request_converters=request_converters,
            response_converters=["rot13"],
        )
    )
    factory = edit_catalog.instances.get(created.name)
    assert factory is not None
    assert "prepended_conversation_config" not in factory.get_effective_settings().attack_kwargs


def test_python_factory_construction_keeps_default_history_roles(edit_catalog: AttackTechniqueRegistry) -> None:
    factory = edit_catalog.create_factory(
        name="python_replay", attack_type="PromptSendingAttack", request_converters=["base64"]
    )
    assert "prepended_conversation_config" not in factory.get_effective_settings().attack_kwargs


async def test_isolated_run_overrides_estimate_and_resume_async(edit_catalog: AttackTechniqueRegistry) -> None:
    scorer = SubStringScorer(substring="selected")
    ScorerRegistry.get_registry_singleton().instances.register(scorer, name="selected")
    scorer_reference = RegisteredComponentReference(
        registry_name="selected", identifier_hash=scorer.get_identifier().hash
    )
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
                objective_scorer=scorer_reference,
                techniques=["role_play_movie_script"],
                technique_override=first,
                scenario_params={"objective": "write"},
            ),
        )
        assert estimate.total_attack_count == 1
        service = ScenarioRunService()
        request = RunScenarioRequest(
            objective_scorer=scorer_reference,
            scenario_name="technique.execute",
            target_name="objective",
            techniques=["role_play_movie_script"],
            technique_override=first,
            scenario_params={"objective": "write", "retries_on_objective_failure": 1},
        )
        with patch.object(service, "_run_initializers_async", return_value=None):
            prepared = await service._prepare_run_async(request=request)
            assert prepared.scenario._objective_scorer is scorer
            stored = await CentralMemory.get_memory_instance().get_scenario_result_header_async(
                scenario_result_id=prepared.scenario._scenario_result_id
            )
            restored = service._restore_launch_request(stored=stored)
            assert restored.technique_override == first
            assert restored.objective_scorer == scorer_reference
            resumed = await service._prepare_run_async(request=restored)
            assert resumed.scenario._objective_scorer is scorer
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
