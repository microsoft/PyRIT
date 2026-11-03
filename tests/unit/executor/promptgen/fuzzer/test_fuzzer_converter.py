# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
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
from pyrit.models import Message, MessagePiece, SeedPrompt
from pyrit.prompt_target import OpenAIChatTarget


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
def test_fuzzer_converter_identifier_differs_by_prompt_template(converter_class, sqlite_instance) -> None:
    prompt_target = MockPromptTarget()
    template_1 = SeedPrompt(value="Shorten this sentence: {{ prompt }}")
    template_2 = SeedPrompt(value="Rephrase this sentence: {{ prompt }}")

    converter_1 = converter_class(converter_target=prompt_target, prompt_template=template_1)
    converter_2 = converter_class(converter_target=prompt_target, prompt_template=template_2)

    assert converter_1.get_identifier().unique_name != converter_2.get_identifier().unique_name
    assert (
        converter_1.get_identifier().params["prompt_template_hash"]
        != converter_2.get_identifier().params["prompt_template_hash"]
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
def test_fuzzer_converter_identifier_differs_by_target(converter_class, sqlite_instance) -> None:
    target_a = OpenAIChatTarget(endpoint="https://example.invalid/v1", api_key="unused", model_name="model-a")
    target_b = OpenAIChatTarget(endpoint="https://example.invalid/v1", api_key="unused", model_name="model-b")
    template = SeedPrompt(value="Rewrite: {{ prompt }}")

    converter_a = converter_class(converter_target=target_a, prompt_template=template)
    converter_b = converter_class(converter_target=target_b, prompt_template=template)

    assert converter_a.get_identifier().unique_name != converter_b.get_identifier().unique_name
    assert converter_a.get_identifier().converter_target != converter_b.get_identifier().converter_target


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
def test_fuzzer_converter_identifier_deterministic_and_matches_expected_hash(converter_class, sqlite_instance) -> None:
    target = OpenAIChatTarget(endpoint="https://example.invalid/v1", api_key="unused", model_name="model-a")
    template_val = "Rewrite: {{ prompt }}"
    template = SeedPrompt(value=template_val)

    converter_1 = converter_class(converter_target=target, prompt_template=template)
    converter_2 = converter_class(converter_target=target, prompt_template=template)

    expected_hash = hashlib.sha256(template_val.encode("utf-8")).hexdigest()[:16]
    ident_1 = converter_1.get_identifier()
    ident_2 = converter_2.get_identifier()

    assert ident_1.unique_name == ident_2.unique_name
    assert ident_1.params["prompt_template_hash"] == expected_hash
    assert ident_1.converter_target == target.get_identifier()
