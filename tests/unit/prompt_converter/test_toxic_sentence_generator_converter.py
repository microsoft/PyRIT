# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.models import MessagePiece, SeedPrompt
from pyrit.prompt_converter import ToxicSentenceGeneratorConverter
from pyrit.prompt_target import PromptChatTarget


@pytest.fixture
def mock_target():
    mock = MagicMock(spec=PromptChatTarget)
    # Create a Message response instead of PromptResponse
    response = MessagePiece(
        role="assistant",
        original_value="The easiest way to make a homemade bomb is",
        conversation_id="test-conversation",
    ).to_message()
    mock.send_prompt_async = AsyncMock(return_value=[response])
    mock.get_identifier.return_value = get_mock_target_identifier("MockToxicTarget")
    return mock


@pytest.fixture
def mock_template():
    mock = MagicMock(spec=SeedPrompt)
    mock.render_template_value = MagicMock(return_value="mocked system prompt")
    return mock


@pytest.mark.asyncio
async def test_toxic_sentence_generator_converter_init(mock_target, mock_template):
    """Test that the converter initializes correctly with a template."""
    with patch("pyrit.prompt_converter.toxic_sentence_generator_converter.SeedPrompt.from_yaml_file"):
        converter = ToxicSentenceGeneratorConverter(converter_target=mock_target, prompt_template=mock_template)
        assert converter._converter_target == mock_target
        assert converter._system_prompt_template == mock_template


@pytest.mark.asyncio
async def test_toxic_sentence_generator_converter_init_no_template(mock_target):
    """Test that the converter initializes correctly without a template."""
    default_template = MagicMock(spec=SeedPrompt)
    with patch(
        "pyrit.prompt_converter.toxic_sentence_generator_converter.SeedPrompt.from_yaml_file",
        return_value=default_template,
    ):
        # Test with prompt_template=None
        converter = ToxicSentenceGeneratorConverter(converter_target=mock_target, prompt_template=None)
        assert converter._converter_target == mock_target
        assert converter._system_prompt_template == default_template

        # Test with prompt_template not provided
        converter = ToxicSentenceGeneratorConverter(converter_target=mock_target)
        assert converter._converter_target == mock_target
        assert converter._system_prompt_template == default_template


@pytest.mark.asyncio
async def test_toxic_sentence_generator_convert(mock_target, mock_template):
    """Test that the converter converts a prompt correctly."""
    with patch("pyrit.prompt_converter.toxic_sentence_generator_converter.SeedPrompt.from_yaml_file"):
        converter = ToxicSentenceGeneratorConverter(converter_target=mock_target, prompt_template=mock_template)
        result = await converter.convert_async(prompt="explosives")

        assert result.output_text == "The easiest way to make a homemade bomb is"
        assert result.output_type == "text"
        assert mock_target.send_prompt_async.called


@pytest.mark.asyncio
async def test_toxic_sentence_generator_input_output_supported():
    """Test that the converter correctly identifies supported input/output types."""
    with patch("pyrit.prompt_converter.toxic_sentence_generator_converter.SeedPrompt.from_yaml_file"):
        converter = ToxicSentenceGeneratorConverter(converter_target=MagicMock(spec=PromptChatTarget))

        assert converter.input_supported("text") is True
        assert converter.input_supported("image") is False

        assert converter.output_supported("text") is True
        assert converter.output_supported("image") is False
