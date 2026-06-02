# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.models import ComponentIdentifier, Message, MessagePiece
from pyrit.prompt_converter import VariationConverter


def test_variation_converter_raises_when_converter_target_is_none():
    with pytest.raises(ValueError, match="converter_target is required"):
        VariationConverter(converter_target=None)


def test_prompt_variation_init_templates_not_null(sqlite_instance):
    prompt_target = MockPromptTarget()
    prompt_variation = VariationConverter(converter_target=prompt_target)
    assert prompt_variation.system_prompt


@pytest.mark.parametrize(
    "converted_value",
    [
        "Invalid Json",
        "{'str' : 'json not formatted correctly'}",
    ],
)
async def test_variation_converter_send_prompt_async_bad_json_exception_retries(converted_value, sqlite_instance):
    prompt_target = MockPromptTarget()

    prompt_variation = VariationConverter(converter_target=prompt_target)

    with patch("unit.mocks.MockPromptTarget.send_prompt_async", new_callable=AsyncMock) as mock_create:
        message = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    conversation_id="12345679",
                    original_value="test input",
                    converted_value=converted_value,
                    original_value_data_type="text",
                    converted_value_data_type="text",
                    prompt_target_identifier=ComponentIdentifier(class_name="target-identifier", class_module="test"),
                    attack_identifier=ComponentIdentifier(class_name="test", class_module="test"),
                    labels={"test": "test"},
                )
            ]
        )

        mock_create.return_value = [message]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(InvalidJsonException):
                await prompt_variation.convert_async(prompt="testing", input_type="text")

        # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
        assert mock_create.call_count == 2


async def test_variation_converter_extracts_first_element_from_json_list(sqlite_instance):
    prompt_target = MockPromptTarget()
    prompt_variation = VariationConverter(converter_target=prompt_target)

    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                conversation_id="test-id",
                original_value='["first variation", "second variation"]',
                original_value_data_type="text",
                prompt_target_identifier=ComponentIdentifier(class_name="test", class_module="test"),
                sequence=1,
            )
        ]
    )
    with patch.object(prompt_target, "send_prompt_async", new=AsyncMock(return_value=[response])):
        result = await prompt_variation.convert_async(prompt="testing")
    assert result.output_text == "first variation"


async def test_variation_converter_preserves_original_and_converted_values(sqlite_instance):
    prompt_target = MockPromptTarget()
    prompt_variation = VariationConverter(converter_target=prompt_target)

    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                conversation_id="test-id",
                original_value='["variation"]',
                original_value_data_type="text",
                prompt_target_identifier=ComponentIdentifier(class_name="test", class_module="test"),
                sequence=1,
            )
        ]
    )
    with patch.object(prompt_target, "send_prompt_async", new=AsyncMock(return_value=[response])) as mock_send:
        await prompt_variation.convert_async(prompt="hello world")

    sent_message = mock_send.call_args[1]["message"]
    piece = sent_message.message_pieces[0]
    assert piece.original_value == "hello world"
    assert "hello world" in piece.converted_value
    assert "=== begin ===" in piece.converted_value
    assert "=== end ===" in piece.converted_value


def test_variation_converter_input_supported(sqlite_instance):
    prompt_target = MockPromptTarget()
    converter = VariationConverter(converter_target=prompt_target)
    assert converter.input_supported("audio_path") is False
    assert converter.input_supported("text") is True


async def test_send_variation_prompt_async_emits_deprecation_warning_and_delegates(sqlite_instance):
    """``send_variation_prompt_async`` is a deprecated shim that warns and delegates to the retry helper."""
    prompt_target = MockPromptTarget()
    prompt_variation = VariationConverter(converter_target=prompt_target)

    request = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="conv-1",
                original_value="test input",
                original_value_data_type="text",
                prompt_target_identifier=ComponentIdentifier(class_name="test", class_module="test"),
            )
        ]
    )

    with patch.object(
        prompt_variation, "_send_with_retries_async", new=AsyncMock(return_value="shim response")
    ) as mock_send:
        with pytest.warns(DeprecationWarning, match="send_variation_prompt_async"):
            result = await prompt_variation.send_variation_prompt_async(request)

    assert result == "shim response"
    mock_send.assert_awaited_once_with(request)
