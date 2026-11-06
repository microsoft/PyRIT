# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.common.random_context import configure_random_seed
from pyrit.converter import (
    RandomTranslationConverter,
    WordPositionSelectionStrategy,
    WordProportionSelectionStrategy,
)
from pyrit.models import Message, MessagePiece, PromptDataType
from pyrit.prompt_target.common.prompt_target import PromptTarget


def test_random_translation_converter_raises_when_converter_target_is_none():
    with pytest.raises(ValueError, match="converter_target is required"):
        RandomTranslationConverter(converter_target=None)


@pytest.fixture
def mock_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="prompt value",
            )
        ]
    )
    target.send_prompt_async = AsyncMock(return_value=[response])
    target.get_identifier.return_value = get_mock_target_identifier("MockTranslationTarget")
    return target


async def test_random_translation_converter_sets_system_prompt(mock_target) -> None:
    converter = RandomTranslationConverter(converter_target=mock_target)
    await converter.convert_async(prompt="being awesome")

    mock_target.set_system_prompt_async.assert_called_once()

    system_arg = mock_target.set_system_prompt_async.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "Each word is associated with a target language on the same line." in system_arg


@pytest.mark.parametrize("input_type", [data_type for data_type in get_args(PromptDataType) if data_type != "text"])
async def test_random_translation_converter_input_not_supported(mock_target, input_type) -> None:
    converter = RandomTranslationConverter(converter_target=mock_target)

    with pytest.raises(ValueError, match=f"Input type {input_type} not supported"):
        await converter.convert_async(prompt="being awesome", input_type=input_type)

    mock_target.set_system_prompt_async.assert_not_called()
    mock_target.send_prompt_async.assert_not_called()


def test_random_translation_converter_default_languages() -> None:
    target = MagicMock(spec=PromptTarget)
    converter = RandomTranslationConverter(converter_target=target)
    assert len(converter.languages) == 37
    assert "Javanese" in converter.languages


def test_random_translation_converter_custom_languages() -> None:
    target = MagicMock(spec=PromptTarget)
    converter = RandomTranslationConverter(converter_target=target, languages=["French", "German", "Spanish"])
    assert len(converter.languages) == 3
    assert "French" in converter.languages
    assert "Javanese" not in converter.languages


def test_random_translation_converter_identifier_canonicalizes_language_order(mock_target) -> None:
    first = RandomTranslationConverter(converter_target=mock_target, languages=["French", "Spanish"])
    reordered = RandomTranslationConverter(converter_target=mock_target, languages=["Spanish", "French"])

    assert first.get_identifier().hash == reordered.get_identifier().hash


async def test_random_translation_converter_language_order_does_not_change_seeded_choices(mock_target) -> None:
    first = RandomTranslationConverter(converter_target=mock_target, languages=["Spanish", "French", "German"])
    reordered = RandomTranslationConverter(converter_target=mock_target, languages=["German", "Spanish", "French"])

    try:
        configure_random_seed(seed=7)
        await first.convert_async(prompt="one two three four five six")
        await reordered.convert_async(prompt="one two three four five six")
    finally:
        configure_random_seed(seed=None)

    first_request, reordered_request = (
        call.kwargs["message"].get_value() for call in mock_target.send_prompt_async.call_args_list
    )
    assert first.get_identifier().hash == reordered.get_identifier().hash
    assert first_request == reordered_request


def test_random_translation_converter_identifier_distinguishes_language_pools(mock_target) -> None:
    first = RandomTranslationConverter(converter_target=mock_target, languages=["French", "Spanish"])
    different = RandomTranslationConverter(converter_target=mock_target, languages=["German", "Japanese"])

    assert first.get_identifier().hash != different.get_identifier().hash


def test_random_translation_converter_identifier_includes_word_selection_strategy(mock_target) -> None:
    strategy = WordPositionSelectionStrategy(start_proportion=0.0, end_proportion=0.5)
    converter = RandomTranslationConverter(converter_target=mock_target, word_selection_strategy=strategy)
    identifier = converter.get_identifier()

    assert identifier.params["word_selection_strategy"] == "WordPositionSelectionStrategy"
    assert identifier.params["word_selection_strategy_params"] == {"start_proportion": 0.0, "end_proportion": 0.5}


def test_random_translation_converter_identifier_distinguishes_word_selection_strategies(mock_target) -> None:
    first = RandomTranslationConverter(
        converter_target=mock_target,
        word_selection_strategy=WordPositionSelectionStrategy(start_proportion=0.0, end_proportion=0.5),
    )
    second = RandomTranslationConverter(
        converter_target=mock_target,
        word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5),
    )

    assert first.get_identifier().hash != second.get_identifier().hash
