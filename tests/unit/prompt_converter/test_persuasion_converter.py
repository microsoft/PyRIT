# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, MessagePiece
from pyrit.prompt_converter import PersuasionConverter


def test_persuasion_converter_raises_when_converter_target_is_none():
    with pytest.raises(ValueError, match="converter_target is required"):
        PersuasionConverter(converter_target=None, persuasion_technique="authority_endorsement")


def test_prompt_persuasion_init_authority_endorsement_template_not_null(sqlite_instance):
    prompt_target = MockPromptTarget()
    prompt_persuasion = PersuasionConverter(
        converter_target=prompt_target, persuasion_technique="authority_endorsement"
    )
    assert prompt_persuasion.system_prompt


def test_prompt_persuasion_init_evidence_based_template_not_null(sqlite_instance):
    prompt_target = MockPromptTarget()
    prompt_persuasion = PersuasionConverter(converter_target=prompt_target, persuasion_technique="evidence_based")
    assert prompt_persuasion.system_prompt


def test_prompt_persuasion_init_expert_endorsement_template_not_null(sqlite_instance):
    prompt_target = MockPromptTarget()
    prompt_persuasion = PersuasionConverter(converter_target=prompt_target, persuasion_technique="expert_endorsement")
    assert prompt_persuasion.system_prompt


def test_prompt_persuasion_init_logical_appeal_template_not_null(sqlite_instance):
    prompt_target = MockPromptTarget()
    prompt_persuasion = PersuasionConverter(converter_target=prompt_target, persuasion_technique="logical_appeal")
    assert prompt_persuasion.system_prompt


def test_prompt_persuasion_init_misrepresentation_template_not_null(sqlite_instance):
    prompt_target = MockPromptTarget()
    prompt_persuasion = PersuasionConverter(converter_target=prompt_target, persuasion_technique="misrepresentation")
    assert prompt_persuasion.system_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "converted_value",
    [
        "Invalid Json",
        "{'str' : 'json not formatted correctly'}",
    ],
)
async def test_persuasion_converter_send_prompt_async_bad_json_exception_retries(converted_value, sqlite_instance):
    prompt_target = MockPromptTarget()

    prompt_persuasion = PersuasionConverter(
        converter_target=prompt_target, persuasion_technique="authority_endorsement"
    )

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
            await prompt_persuasion.convert_async(prompt="testing", input_type="text")
            # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
            assert mock_create.call_count == 2


def test_persuasion_converter_input_supported():
    prompt_target = MockPromptTarget()
    prompt_persuasion = PersuasionConverter(
        converter_target=prompt_target, persuasion_technique="authority_endorsement"
    )
    assert prompt_persuasion.input_supported("text") is True
    assert prompt_persuasion.input_supported("image_path") is False
