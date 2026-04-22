# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import Mock

import pytest

from pyrit.datasets import TextJailBreak
from pyrit.prompt_converter import TextJailbreakConverter


@pytest.fixture
def mock_jailbreak():
    jailbreak = Mock(spec=TextJailBreak)
    jailbreak.get_jailbreak.return_value = "Modified prompt: {prompt}"
    return jailbreak


@pytest.fixture
def converter(mock_jailbreak):
    return TextJailbreakConverter(jailbreak_template=mock_jailbreak)


@pytest.mark.asyncio
async def test_convert_async_basic(converter, mock_jailbreak):
    """Test basic conversion functionality"""
    prompt = "test prompt"
    result = await converter.convert_async(prompt=prompt, input_type="text")

    mock_jailbreak.get_jailbreak.assert_called_once_with(prompt=prompt)
    assert result.output_text == "Modified prompt: {prompt}"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_convert_async_unsupported_input_type(converter):
    """Test that unsupported input types raise ValueError"""
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")


def test_input_supported(converter):
    """Test input type support validation"""
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
    assert converter.input_supported("audio_path") is False


def test_output_supported(converter):
    """Test output type support validation"""
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
    assert converter.output_supported("audio_path") is False


@pytest.mark.asyncio
async def test_convert_async_with_empty_prompt(converter, mock_jailbreak):
    """Test conversion with empty prompt"""
    prompt = ""
    result = await converter.convert_async(prompt=prompt, input_type="text")

    mock_jailbreak.get_jailbreak.assert_called_once_with(prompt=prompt)
    assert result.output_text == "Modified prompt: {prompt}"
    assert result.output_type == "text"
