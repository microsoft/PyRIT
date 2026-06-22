# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from textwrap import dedent
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.models import Message, MessagePiece
from pyrit.prompt_converter import TranslationConverter


def test_translation_converter_raises_when_converter_target_is_none():
    with pytest.raises(ValueError, match="converter_target is required"):
        TranslationConverter(converter_target=None, language="en")


def test_prompt_translation_init_templates_not_null(sqlite_instance):
    prompt_target = MockPromptTarget()
    translation_converter = TranslationConverter(converter_target=prompt_target, language="en")
    assert translation_converter.system_prompt


@pytest.mark.parametrize("languages", [None, ""])
def test_translator_converter_languages_validation_throws(languages, sqlite_instance):
    prompt_target = MockPromptTarget()
    with pytest.raises(ValueError):
        TranslationConverter(converter_target=prompt_target, language=languages)


async def test_translation_converter_returns_stripped_response(sqlite_instance):
    prompt_target = MockPromptTarget()

    translation_converter = TranslationConverter(converter_target=prompt_target, language="spanish")
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                conversation_id="test-id",
                original_value="  hola  \n",
                original_value_data_type="text",
                sequence=1,
            )
        ]
    )
    with patch.object(prompt_target, "send_prompt_async", new=AsyncMock(return_value=[response])):
        result = await translation_converter.convert_async(prompt="hello")

    assert result.output_text == "hola"
    assert result.output_type == "text"


async def test_translation_converter_user_prompt_byte_for_byte_equivalent(sqlite_instance):
    """Regression: the SeedPrompt-rendered user prompt must match the previous f-string output exactly."""
    prompt_target = MockPromptTarget()
    translation_converter = TranslationConverter(converter_target=prompt_target, language="Spanish")

    raw_prompt = "tell me about the history of the internet"
    expected = dedent(
        f"Translate the following to {translation_converter.language} between the begin and end tags:"
        "=== begin ===\n"
        f"{raw_prompt}\n"
        "=== end ===\n"
    )

    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                conversation_id="test-id",
                original_value="hola",
                original_value_data_type="text",
                sequence=1,
            )
        ]
    )
    with patch.object(prompt_target, "send_prompt_async", new=AsyncMock(return_value=[response])) as mock_send:
        await translation_converter.convert_async(prompt=raw_prompt)

    sent_message = mock_send.call_args[1]["message"]
    piece = sent_message.message_pieces[0]
    assert piece.original_value == raw_prompt
    assert piece.converted_value == expected


async def test_translation_converter_retries_on_exception(sqlite_instance):
    prompt_target = MockPromptTarget()
    max_retries = 3
    translation_converter = TranslationConverter(
        converter_target=prompt_target, language="spanish", max_retries=max_retries
    )

    mock_send_prompt = AsyncMock(side_effect=Exception("Test failure"))
    with patch.object(prompt_target, "send_prompt_async", mock_send_prompt):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(Exception):
                await translation_converter.convert_async(prompt="hello")

            assert mock_send_prompt.call_count == max_retries


async def test_translation_converter_succeeds_after_retries(sqlite_instance):
    """Test that TranslationConverter succeeds if a retry attempt works."""
    prompt_target = MockPromptTarget()
    max_retries = 3
    translation_converter = TranslationConverter(
        converter_target=prompt_target, language="spanish", max_retries=max_retries
    )

    success_response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                conversation_id="test-id",
                original_value="hello",
                converted_value="hola",
                original_value_data_type="text",
                converted_value_data_type="text",
                sequence=1,
            )
        ]
    )

    # fail twice, then succeed
    mock_send_prompt = AsyncMock()
    mock_send_prompt.side_effect = [Exception("First failure"), Exception("Second failure"), [success_response]]

    with patch.object(prompt_target, "send_prompt_async", mock_send_prompt):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await translation_converter.convert_async(prompt="hello")

            assert mock_send_prompt.call_count == max_retries
            assert result.output_text == "hola"
            assert result.output_type == "text"


def test_translation_converter_input_supported(sqlite_instance):
    prompt_target = MockPromptTarget()
    translation_converter = TranslationConverter(converter_target=prompt_target, language="spanish")
    assert translation_converter.input_supported("text") is True
    assert translation_converter.input_supported("image_path") is False


def test_translation_converter_identifier_includes_language(sqlite_instance):
    prompt_target = MockPromptTarget()
    translation_converter = TranslationConverter(converter_target=prompt_target, language="Spanish")
    identifier = translation_converter.get_identifier()
    assert identifier.params["language"] == "spanish"
