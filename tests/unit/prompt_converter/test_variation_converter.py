# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, MessagePiece
from pyrit.prompt_converter import VariationConverter


def test_variation_converter_raises_when_converter_target_is_none():
    with pytest.raises(ValueError, match="converter_target is required"):
        VariationConverter(converter_target=None)


def test_prompt_variation_init_templates_not_null(sqlite_instance):
    prompt_target = MockPromptTarget()
    prompt_variation = VariationConverter(converter_target=prompt_target)
    assert prompt_variation.system_prompt


@pytest.mark.asyncio
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

        with pytest.raises(InvalidJsonException):
            await prompt_variation.convert_async(prompt="testing", input_type="text")

        # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
        assert mock_create.call_count == 2


def test_variation_converter_input_supported(sqlite_instance):
    prompt_target = MockPromptTarget()
    converter = VariationConverter(converter_target=prompt_target)
    assert converter.input_supported("audio_path") is False
    assert converter.input_supported("text") is True
