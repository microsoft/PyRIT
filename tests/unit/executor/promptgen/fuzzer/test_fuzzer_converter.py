# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.executor.promptgen.fuzzer import (
    FuzzerCrossOverConverter,
    FuzzerExpandConverter,
    FuzzerRephraseConverter,
    FuzzerShortenConverter,
    FuzzerSimilarConverter,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, MessagePiece


@pytest.mark.parametrize(
    "converter_class",
    [
        FuzzerExpandConverter,
        FuzzerShortenConverter,
        FuzzerRephraseConverter,
        FuzzerCrossOverConverter,
        FuzzerSimilarConverter,
    ],
)
def test_fuzzer_converter_raises_when_converter_target_is_none(converter_class) -> None:
    with pytest.raises(ValueError, match="converter_target is required"):
        converter_class(converter_target=None)


@pytest.mark.parametrize(
    "converter_class",
    [
        FuzzerExpandConverter,
        FuzzerShortenConverter,
        FuzzerRephraseConverter,
        FuzzerCrossOverConverter,
        FuzzerSimilarConverter,
    ],
)
def test_converter_init_templates_not_null(converter_class, sqlite_instance) -> None:
    prompt_target = MockPromptTarget()
    converter = converter_class(converter_target=prompt_target)
    assert converter.system_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "converted_value",
    [
        "Invalid Json",
        "{'str' : 'json not formatted correctly'}",
    ],
)
@pytest.mark.parametrize(
    "converter_class",
    [
        FuzzerExpandConverter,
        FuzzerShortenConverter,
        FuzzerRephraseConverter,
        FuzzerCrossOverConverter,
        FuzzerSimilarConverter,
    ],
)
@pytest.mark.parametrize(
    "update",
    [True, False],
)
async def test_converter_send_prompt_async_bad_json_exception_retries(
    converted_value, converter_class, update, sqlite_instance
):
    prompt_target = MockPromptTarget()

    if converter_class != FuzzerCrossOverConverter:
        converter = converter_class(converter_target=prompt_target)
    else:
        converter = converter_class(converter_target=prompt_target, prompt_templates=["testing 1"])

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

        if update:
            converter.update(prompt_templates=["testing 2"])

        if converter_class == FuzzerCrossOverConverter:
            if update:
                assert converter.prompt_templates == ["testing 2"]
            else:
                assert converter.prompt_templates == ["testing 1"]

        with pytest.raises(InvalidJsonException):
            await converter.convert_async(prompt="testing", input_type="text")
            # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
            assert mock_create.call_count == 2


@pytest.mark.parametrize(
    "converter_class",
    [
        FuzzerExpandConverter,
        FuzzerShortenConverter,
        FuzzerRephraseConverter,
        FuzzerCrossOverConverter,
        FuzzerSimilarConverter,
    ],
)
def test_fuzzer_converter_input_supported(converter_class, sqlite_instance) -> None:
    prompt_target = MockPromptTarget()
    converter = converter_class(converter_target=prompt_target)
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
