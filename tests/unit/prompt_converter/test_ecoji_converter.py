# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import EcojiConverter


@pytest.mark.asyncio
async def test_ecoji_converter_encode_simple_text() -> None:
    """Test encoding simple text to Ecoji format."""
    converter = EcojiConverter()
    prompt = "hello"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert result.output_text
    assert result.output_type == "text"
    # Ecoji uses emojis, so output should be different from input
    assert result.output_text != prompt


@pytest.mark.asyncio
async def test_ecoji_converter_encode_empty_string() -> None:
    """Test encoding an empty string."""
    converter = EcojiConverter()
    prompt = ""

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_ecoji_converter_encode_with_special_characters() -> None:
    """Test encoding text with special characters."""
    converter = EcojiConverter()
    prompt = "Hello, World! 123 @#$"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert result.output_text
    assert result.output_type == "text"
    assert result.output_text != prompt


@pytest.mark.asyncio
async def test_ecoji_converter_encode_unicode() -> None:
    """Test encoding Unicode text."""
    converter = EcojiConverter()
    prompt = "Hello 世界 🌍"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert result.output_text
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_ecoji_converter_encode_multiline() -> None:
    """Test encoding multiline text."""
    converter = EcojiConverter()
    prompt = "Line 1\nLine 2\nLine 3"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert result.output_text
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_ecoji_converter_unsupported_input_type() -> None:
    """Test that unsupported input types raise ValueError."""
    converter = EcojiConverter()
    prompt = "test"

    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt=prompt, input_type="image_path")


def test_ecoji_converter_input_supported_text() -> None:
    """Test that text input type is supported."""
    converter = EcojiConverter()

    assert converter.input_supported(input_type="text") is True


def test_ecoji_converter_input_supported_image() -> None:
    """Test that image input type is not supported."""
    converter = EcojiConverter()

    assert converter.input_supported(input_type="image_path") is False


def test_ecoji_converter_input_supported_audio() -> None:
    """Test that audio input type is not supported."""
    converter = EcojiConverter()

    assert converter.input_supported(input_type="audio_path") is False


@pytest.mark.asyncio
async def test_ecoji_converter_deterministic() -> None:
    """Test that encoding the same text produces the same result."""
    converter = EcojiConverter()
    prompt = "test message"

    result1 = await converter.convert_async(prompt=prompt, input_type="text")
    result2 = await converter.convert_async(prompt=prompt, input_type="text")

    assert result1.output_text == result2.output_text


@pytest.mark.asyncio
async def test_ecoji_converter_different_inputs_produce_different_outputs() -> None:
    """Test that different inputs produce different outputs."""
    converter = EcojiConverter()

    result1 = await converter.convert_async(prompt="hello", input_type="text")
    result2 = await converter.convert_async(prompt="world", input_type="text")

    assert result1.output_text != result2.output_text
