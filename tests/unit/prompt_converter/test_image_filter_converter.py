# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.models import Message, MessagePiece
from pyrit.prompt_converter import ImageFilterConverter
from pyrit.prompt_target.common.prompt_target import PromptTarget


@pytest.fixture
def mock_target() -> PromptTarget:
    target = MagicMock()
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="A blurry bodycam shot of a figure in a dark alley",
            )
        ]
    )
    target.send_prompt_async = AsyncMock(return_value=[response])
    target.get_identifier.return_value = get_mock_target_identifier("MockLLMTarget")
    return target


def test_init_valid_filter_and_variation(mock_target) -> None:
    converter = ImageFilterConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
        variation="Bodycam Footage",
    )
    assert converter._filter_name == "gritty_documentary"
    assert converter._variation == "Bodycam Footage"
    assert "bodycam footage" in converter._variation_map


def test_init_variation_none_is_valid(mock_target) -> None:
    converter = ImageFilterConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
    )
    assert converter._variation is None


def test_init_variation_not_case_sensitive(mock_target) -> None:
    converter = ImageFilterConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
        variation="bodycam footage",
    )
    assert converter._variation == "bodycam footage"
    assert "bodycam footage" in converter._variation_map


def test_init_invalid_filter_name_raises(mock_target) -> None:
    with pytest.raises(ValueError, match="not found"):
        ImageFilterConverter(
            converter_target=mock_target,
            filter_name="nonexistent_filter",
        )


def test_init_invalid_variation_raises(mock_target) -> None:
    with pytest.raises(ValueError, match="not found in filter"):
        ImageFilterConverter(
            converter_target=mock_target,
            filter_name="gritty_documentary",
            variation="Nonexistent Variation",
        )


def test_list_available_filters() -> None:
    filters = ImageFilterConverter.list_available_filters()
    assert isinstance(filters, list)
    assert "gritty_documentary" in filters
    assert len(filters) > 0


@pytest.mark.asyncio
async def test_convert_async_with_specific_variation(mock_target) -> None:
    converter = ImageFilterConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
        variation="Bodycam Footage",
    )
    result = await converter.convert_async(prompt="person walking through a dark alley")

    mock_target.set_system_prompt.assert_called_once()
    system_arg = mock_target.set_system_prompt.call_args[1]["system_prompt"]
    assert "Bodycam Footage" in system_arg
    assert "style_instructions" not in system_arg or "CRITICAL INSTRUCTION" in system_arg

    mock_target.send_prompt_async.assert_called_once()
    assert result.output_text == "A blurry bodycam shot of a figure in a dark alley"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_convert_async_with_random_variation(mock_target) -> None:
    converter = ImageFilterConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
    )
    result = await converter.convert_async(prompt="person in a park")

    mock_target.set_system_prompt.assert_called_once()
    system_arg = mock_target.set_system_prompt.call_args[1]["system_prompt"]
    # Should contain one of the variation names
    assert any(name in system_arg for name in converter._variations)

    assert result.output_text == "A blurry bodycam shot of a figure in a dark alley"


@pytest.mark.asyncio
async def test_convert_async_unsupported_input_type_raises(mock_target) -> None:
    converter = ImageFilterConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
    )
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="/tmp/image.png", input_type="image_path")


def test_duplicate_variation_prefix_logs_warning(mock_target, caplog) -> None:
    """Duplicate prefixes should log a warning but not raise."""
    from unittest.mock import mock_open, patch

    duplicate_yaml = {
        "style_instructions": "test style",
        "variations": {
            "Bodycam Footage": "first version",
            "bodycam footage": "second version",
        },
    }

    with (
        caplog.at_level("WARNING", logger="pyrit.prompt_converter.image_filter_converter"),
        patch("yaml.safe_load", return_value=duplicate_yaml),
        patch("builtins.open", mock_open()),
        patch(
            "pyrit.prompt_converter.image_filter_converter.ImageFilterConverter.list_available_filters",
            return_value=["gritty_documentary"],
        ),
    ):
        converter = ImageFilterConverter(
            converter_target=mock_target,
            filter_name="gritty_documentary",
        )

    assert "Duplicate variation key" in caplog.text
    assert converter._variation_map["bodycam footage"] == "bodycam footage"

    assert "Duplicate variation prefix" in caplog.text
    assert converter._variation_map["bodycam footage"] == "Bodycam Footage: second version"
