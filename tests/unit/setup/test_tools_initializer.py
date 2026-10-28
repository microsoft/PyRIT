# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.converter import RolePlayToolCallConverter, ROT13Converter
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, InitializerRegistry, TargetRegistry
from pyrit.registry.registry import Registry
from pyrit.setup.initializers import ToolsInitializer
from pyrit.setup.initializers.techniques import build_technique_factories


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    with patch.dict(Registry._singletons, {}, clear=True):
        yield


def test_tools_initializer_is_discoverable() -> None:
    registry = InitializerRegistry()
    assert "tools" in registry.get_class_names()
    assert type(registry.create_and_configure("tools")).__name__ == "ToolsInitializer"


def test_general_catalog_does_not_include_tool_technique() -> None:
    assert "role_play_tool_calls" not in {factory.name for factory in build_technique_factories()}


async def test_tools_initializer_requires_registered_adversarial_chat_async() -> None:
    with pytest.raises(ValueError, match="'adversarial_chat' not found"):
        await ToolsInitializer().initialize_async()
    assert "role_play_tool_calls" not in AttackTechniqueRegistry.get_registry_singleton().instances


@pytest.mark.usefixtures("patch_central_database")
async def test_tools_initializer_registers_configured_technique_async() -> None:
    target = MockPromptTarget()
    TargetRegistry.get_registry_singleton().instances.register(target, name="adversarial_chat")

    initializer = ToolsInitializer()
    await initializer.initialize_async()
    registry = AttackTechniqueRegistry.get_registry_singleton()
    factory = registry.get_factories()["role_play_tool_calls"]

    assert factory.attack_class is PromptSendingAttack
    assert factory.technique_tags == ["single_turn", "tools"]
    assert factory.supports_additional_request_converters
    assert not factory.requirements.seed_group.objective_only
    assert not factory.requirements.seed_group.try_adapt
    assert factory.get_effective_attack_kwargs()["max_attempts_on_failure"] == 0
    converter = factory.get_converter_config().request_converters[0].converters[0]
    assert isinstance(converter, RolePlayToolCallConverter)
    assert converter._converter_target is target
    assert ConverterRegistry.get_registry_singleton().instances.get("role_play_tool_calls") is converter
    assert registry.get_editable_settings(factory)["request_converters"] == ["role_play_tool_calls"]

    technique = factory.create(
        objective_target=MockPromptTarget(),
        attack_scoring_config=AttackScoringConfig(),
        extra_request_converters=ConverterConfiguration.from_converters(converters=[ROT13Converter()]),
    )
    groups = technique.attack.get_request_converters()
    assert isinstance(groups[0].converters[0], RolePlayToolCallConverter)
    assert isinstance(groups[1].converters[0], ROT13Converter)

    await initializer.initialize_async()
    assert registry.get_factories()["role_play_tool_calls"] is factory
    assert ConverterRegistry.get_registry_singleton().instances.get("role_play_tool_calls") is converter
    assert registry.get_editable_settings(factory)["request_converters"] == ["role_play_tool_calls"]
