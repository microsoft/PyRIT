# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the converter initializer."""

import logging
from collections.abc import Iterator
from unittest.mock import call, patch

import pytest

from pyrit.converter import Base64Converter, LeetspeakConverter, ROT13Converter, VariationConverter
from pyrit.registry import ConverterRegistry, InitializerRegistry, TargetRegistry
from pyrit.setup.initializers import ConverterInitializer
from pyrit.setup.initializers.converters import ConverterConfig
from tests.unit.mocks import MockPromptTarget


@pytest.fixture(autouse=True)
def reset_registries() -> Iterator[None]:
    """Reset the component registries around each test."""
    ConverterRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    yield
    ConverterRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()


def _get_configs(*names: str) -> tuple[ConverterConfig, ...]:
    configs_by_name = {config.registry_name: config for config in ConverterInitializer.CONFIGS}
    return tuple(configs_by_name[name] for name in names)


async def test_initialize_registers_core_converter_presets() -> None:
    registry = ConverterRegistry.get_registry_singleton()

    with patch.object(
        ConverterInitializer,
        "CONFIGS",
        _get_configs("base64", "leetspeak", "rot13"),
    ):
        await ConverterInitializer().initialize_async()

    assert isinstance(registry.instances.get("base64"), Base64Converter)
    assert isinstance(registry.instances.get("leetspeak"), LeetspeakConverter)
    assert isinstance(registry.instances.get("rot13"), ROT13Converter)


@pytest.mark.usefixtures("patch_central_database")
async def test_initialize_registers_variation_with_declared_target() -> None:
    converter_registry = ConverterRegistry.get_registry_singleton()
    target_registry = TargetRegistry.get_registry_singleton()
    adversarial_chat = MockPromptTarget()
    target_registry.instances.register(adversarial_chat, name="adversarial_chat")

    with patch.object(ConverterInitializer, "CONFIGS", _get_configs("variation")):
        await ConverterInitializer().initialize_async()

    converter = converter_registry.instances.get("variation")
    assert isinstance(converter, VariationConverter)
    assert converter._converter_target is adversarial_chat


async def test_initialize_skips_converter_without_declared_target() -> None:
    registry = ConverterRegistry.get_registry_singleton()

    with patch.object(ConverterInitializer, "CONFIGS", _get_configs("variation")):
        await ConverterInitializer().initialize_async()

    assert "variation" not in registry.instances


async def test_initialize_uses_explicit_converter_configs() -> None:
    converter_registry = ConverterRegistry.get_registry_singleton()

    with patch.object(converter_registry, "create_instance", return_value=Base64Converter()) as create_instance:
        await ConverterInitializer().initialize_async()

    assert converter_registry.instances.get_names() == sorted(
        {
            "add_image_text",
            "add_text_image",
            "azure_speech_audio_to_text",
            "base64",
            "binary",
            "char_swap",
            "ecoji",
            "image_color_saturation",
            "image_compression",
            "image_rotation",
            "insert_punctuation",
            "leetspeak",
            "malicious_question_generator",
            "math_prompt",
            "noise",
            "qr_code",
            "rot13",
            "search_replace",
            "string_join",
            "tense_future",
            "tense_past",
            "text_jailbreak",
            "tone_professional",
            "tone_sarcastic",
            "translation_spanish",
            "transparency_attack",
            "variation",
            "zalgo",
        }
    )
    assert create_instance.call_count == len(ConverterInitializer.CONFIGS)
    assert call("Base64Converter") in create_instance.call_args_list
    assert (
        call(
            "SearchReplaceConverter",
            pattern=r"\s+",
            replace="_",
        )
        in create_instance.call_args_list
    )
    assert (
        call(
            "TenseConverter",
            converter_target="adversarial_chat",
            tense="future",
        )
        in create_instance.call_args_list
    )
    assert (
        call(
            "ToneConverter",
            converter_target="adversarial_chat",
            tone="sarcastic",
        )
        in create_instance.call_args_list
    )
    assert (
        call(
            "TranslationConverter",
            converter_target="adversarial_chat",
            language="Spanish",
        )
        in create_instance.call_args_list
    )
    assert (
        call(
            "VariationConverter",
            converter_target="adversarial_chat",
        )
        in create_instance.call_args_list
    )


@pytest.mark.usefixtures("patch_central_database")
async def test_explicit_converter_configs_can_be_constructed() -> None:
    converter_registry = ConverterRegistry.get_registry_singleton()
    target_registry = TargetRegistry.get_registry_singleton()
    target_registry.instances.register(MockPromptTarget(), name="adversarial_chat")

    await ConverterInitializer().initialize_async()

    expected_names = {config.registry_name for config in ConverterInitializer.CONFIGS}
    expected_names.remove("azure_speech_audio_to_text")
    assert expected_names <= set(converter_registry.instances.get_names())


def test_configs_have_unique_names() -> None:
    names = [config.registry_name for config in ConverterInitializer.CONFIGS]

    assert len(names) == len(set(names))


async def test_initialize_continues_after_expected_construction_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = ConverterRegistry.get_registry_singleton()
    configs = _get_configs("base64", "leetspeak", "rot13")

    with (
        patch.object(ConverterInitializer, "CONFIGS", configs),
        patch.object(
            registry,
            "create_instance",
            side_effect=[
                ValueError("missing configuration"),
                LeetspeakConverter(),
                ROT13Converter(),
            ],
        ),
        caplog.at_level(logging.WARNING, logger="pyrit.setup.initializers.converters"),
    ):
        await ConverterInitializer().initialize_async()

    assert "base64" not in registry.instances
    assert isinstance(registry.instances.get("leetspeak"), LeetspeakConverter)
    assert isinstance(registry.instances.get("rot13"), ROT13Converter)
    assert "Skipping converter 'base64': missing configuration" in caplog.text


def test_initializer_is_discovered() -> None:
    registry = InitializerRegistry()
    assert "converter" in registry.get_class_names()
