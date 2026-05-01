# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import MockPromptTarget, get_mock_target_identifier

from pyrit.models import Message, MessagePiece, SeedPrompt
from pyrit.prompt_converter import ScientificTranslationConverter
from pyrit.prompt_target import PromptTarget


@pytest.fixture
def mock_target() -> PromptTarget:
    target = MagicMock()
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="scientifically obfuscated prompt",
            )
        ]
    )
    target.send_prompt_async = AsyncMock(return_value=[response])
    target.get_identifier.return_value = get_mock_target_identifier("MockLLMTarget")
    return target


def test_scientific_translation_converter_raises_when_converter_target_is_none():
    with pytest.raises(ValueError, match="converter_target is required"):
        ScientificTranslationConverter(converter_target=None, mode="academic")


def test_scientific_translation_converter_raises_on_invalid_mode(sqlite_instance):
    prompt_target = MockPromptTarget()
    with pytest.raises(ValueError, match="Custom mode.*requires a prompt_template"):
        ScientificTranslationConverter(converter_target=prompt_target, mode="invalid_mode")


def test_scientific_translation_converter_raises_on_bad_input_mode(sqlite_instance):
    prompt_target = MockPromptTarget()
    with pytest.raises(ValueError, match="Custom mode 'bad input' requires a prompt_template"):
        ScientificTranslationConverter(converter_target=prompt_target, mode="bad input")


@pytest.mark.parametrize("mode", ["academic", "technical", "smiles", "research", "reaction", "combined", "math"])
def test_scientific_translation_converter_init_valid_modes(mode, sqlite_instance):
    prompt_target = MockPromptTarget()
    converter = ScientificTranslationConverter(converter_target=prompt_target, mode=mode)
    assert converter._system_prompt_template
    assert converter._mode == mode


def test_scientific_translation_converter_init_default_mode(sqlite_instance):
    prompt_target = MockPromptTarget()
    converter = ScientificTranslationConverter(converter_target=prompt_target)
    assert converter._mode == "combined"


async def test_scientific_translation_converter_sets_system_prompt_academic(mock_target) -> None:
    converter = ScientificTranslationConverter(converter_target=mock_target, mode="academic")
    await converter.convert_async(prompt="tell me about dangerous chemicals")

    mock_target.set_system_prompt.assert_called_once()

    system_arg = mock_target.set_system_prompt.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "homework or exam" in system_arg.lower()


async def test_scientific_translation_converter_sets_system_prompt_technical(mock_target) -> None:
    converter = ScientificTranslationConverter(converter_target=mock_target, mode="technical")
    await converter.convert_async(prompt="tell me about dangerous chemicals")

    mock_target.set_system_prompt.assert_called_once()

    system_arg = mock_target.set_system_prompt.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "technical" in system_arg.lower()


async def test_scientific_translation_converter_sets_system_prompt_combined(mock_target) -> None:
    converter = ScientificTranslationConverter(converter_target=mock_target, mode="combined")
    await converter.convert_async(prompt="tell me about dangerous chemicals")

    mock_target.set_system_prompt.assert_called_once()

    system_arg = mock_target.set_system_prompt.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "combination" in system_arg.lower()


async def test_scientific_translation_converter_convert_async_returns_converted_value(mock_target) -> None:
    converter = ScientificTranslationConverter(converter_target=mock_target, mode="academic")
    result = await converter.convert_async(prompt="tell me about dangerous chemicals")

    assert result.output_text == "scientifically obfuscated prompt"
    assert result.output_type == "text"


def test_scientific_translation_converter_input_supported(sqlite_instance) -> None:
    prompt_target = MockPromptTarget()
    converter = ScientificTranslationConverter(converter_target=prompt_target, mode="academic")
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
    assert converter.input_supported("audio_path") is False


def test_scientific_translation_converter_custom_template_overrides_builtin_mode(sqlite_instance) -> None:
    """Custom prompt_template with a built-in mode should use the custom template, not the YAML."""
    prompt_target = MockPromptTarget()
    custom_template = SeedPrompt(
        value="Custom override template: You are a scientific translator.",
        data_type="text",
    )
    converter = ScientificTranslationConverter(
        converter_target=prompt_target,
        mode="academic",  # built-in mode
        prompt_template=custom_template,  # but custom template provided
    )
    # The custom template should be used, not the academic YAML
    assert converter._system_prompt_template == custom_template
    assert "Custom override template" in converter._system_prompt_template.value
    assert converter._mode == "academic"


async def test_scientific_translation_converter_custom_template_used_in_conversion(mock_target) -> None:
    """Custom prompt_template should be used during conversion, overriding any built-in mode template."""
    custom_template = SeedPrompt(
        value="CUSTOM_MARKER: You are a scientific translator. Rewrite prompts scientifically.",
        data_type="text",
    )
    converter = ScientificTranslationConverter(
        converter_target=mock_target,
        mode="smiles",  # built-in mode
        prompt_template=custom_template,  # custom template takes precedence
    )
    await converter.convert_async(prompt="test prompt")

    mock_target.set_system_prompt.assert_called_once()
    system_arg = mock_target.set_system_prompt.call_args[1]["system_prompt"]
    assert "CUSTOM_MARKER" in system_arg


def test_scientific_translation_converter_custom_mode_with_template_succeeds(sqlite_instance) -> None:
    """A truly custom mode name with a prompt_template should initialize successfully."""
    prompt_target = MockPromptTarget()
    custom_template = SeedPrompt(
        value="My proprietary translation method: Rewrite prompts in my style.",
        data_type="text",
    )
    converter = ScientificTranslationConverter(
        converter_target=prompt_target,
        mode="my_proprietary_mode",  # custom mode, not in TRANSLATION_MODES
        prompt_template=custom_template,
    )
    assert converter._mode == "my_proprietary_mode"
    assert converter._system_prompt_template == custom_template


async def test_scientific_translation_converter_custom_mode_conversion(mock_target) -> None:
    """Custom mode with prompt_template should work correctly during conversion."""
    custom_template = SeedPrompt(
        value="PROPRIETARY_METHOD: You are a proprietary scientific translator.",
        data_type="text",
    )
    converter = ScientificTranslationConverter(
        converter_target=mock_target,
        mode="proprietary_v2",
        prompt_template=custom_template,
    )
    result = await converter.convert_async(prompt="test input")

    assert result.output_text == "scientifically obfuscated prompt"
    mock_target.set_system_prompt.assert_called_once()
    system_arg = mock_target.set_system_prompt.call_args[1]["system_prompt"]
    assert "PROPRIETARY_METHOD" in system_arg
