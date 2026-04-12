# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.models import Message, MessagePiece
from pyrit.prompt_converter import RandomTranslationConverter
from pyrit.prompt_target.common.prompt_target import PromptTarget


def test_random_translation_converter_raises_when_converter_target_is_none():
    with pytest.raises(ValueError, match="converter_target is required"):
        RandomTranslationConverter(converter_target=None)


@pytest.fixture
def mock_target() -> PromptTarget:
    target = MagicMock()
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


@pytest.mark.asyncio
async def test_random_translation_converter_sets_system_prompt(mock_target) -> None:
    converter = RandomTranslationConverter(converter_target=mock_target)
    await converter.convert_async(prompt="being awesome")

    mock_target.set_system_prompt.assert_called_once()

    system_arg = mock_target.set_system_prompt.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "Each word is associated with a target language on the same line." in system_arg


def test_random_translation_converter_default_languages() -> None:
    target = MagicMock()
    converter = RandomTranslationConverter(converter_target=target)
    assert len(converter.languages) == 37
    assert "Javanese" in converter.languages


def test_random_translation_converter_custom_languages() -> None:
    target = MagicMock()
    converter = RandomTranslationConverter(converter_target=target, languages=["French", "German", "Spanish"])
    assert len(converter.languages) == 3
    assert "French" in converter.languages
    assert "Javanese" not in converter.languages
