# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.converter import ImagePromptStyleConverter
from pyrit.models import Message, MessagePiece
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
    converter = ImagePromptStyleConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
        variation="bodycam_footage",
    )
    assert converter._filter_name == "gritty_documentary"
    assert converter._variation == "bodycam_footage"
    assert "bodycam_footage" in converter._variation_map


def test_init_no_filter_picks_random(mock_target) -> None:
    converter = ImagePromptStyleConverter(
        converter_target=mock_target,
    )
    available = ImagePromptStyleConverter.list_available_filters()
    assert converter._filter_name in available
    assert converter._variation is None


def test_init_filter_path_custom_yaml(mock_target, tmp_path) -> None:
    custom_yaml = tmp_path / "custom_filter.yaml"
    custom_yaml.write_text("style_instructions: custom style\nvariations:\n  my_variation: description of variation\n")
    converter = ImagePromptStyleConverter(
        converter_target=mock_target,
        filter_path=custom_yaml,
        variation="my_variation",
    )
    assert converter._filter_name == "custom_filter"
    assert "my_variation" in converter._variation_map


def test_init_filter_path_nonexistent_raises(mock_target) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        ImagePromptStyleConverter(
            converter_target=mock_target,
            filter_path="/nonexistent/path.yaml",
        )


def test_init_both_filter_name_and_path_raises(mock_target, tmp_path) -> None:
    custom_yaml = tmp_path / "custom.yaml"
    custom_yaml.write_text("style_instructions: style\nvariations:\n  V1: desc\n")
    with pytest.raises(ValueError, match="Only one of"):
        ImagePromptStyleConverter(
            converter_target=mock_target,
            filter_name="gritty_documentary",
            filter_path=custom_yaml,
        )


def test_init_variation_none_is_valid(mock_target) -> None:
    converter = ImagePromptStyleConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
    )
    assert converter._variation is None


def test_init_variation_not_case_sensitive(mock_target) -> None:
    converter = ImagePromptStyleConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
        variation="BODYCAM_FOOTAGE",
    )
    assert converter._variation == "BODYCAM_FOOTAGE"
    assert "bodycam_footage" in converter._variation_map


def test_init_invalid_filter_name_raises(mock_target) -> None:
    with pytest.raises(ValueError, match="not found"):
        ImagePromptStyleConverter(
            converter_target=mock_target,
            filter_name="nonexistent_filter",
        )


def test_init_invalid_variation_raises(mock_target) -> None:
    with pytest.raises(ValueError, match="not found in filter"):
        ImagePromptStyleConverter(
            converter_target=mock_target,
            filter_name="gritty_documentary",
            variation="Nonexistent Variation",
        )


def test_init_filter_missing_style_instructions_raises(mock_target, tmp_path) -> None:
    bad_yaml = tmp_path / "missing_style.yaml"
    bad_yaml.write_text("variations:\n  v1: desc\n")
    with pytest.raises(ValueError, match="missing required key 'style_instructions'"):
        ImagePromptStyleConverter(converter_target=mock_target, filter_path=bad_yaml)


def test_init_filter_missing_variations_raises(mock_target, tmp_path) -> None:
    bad_yaml = tmp_path / "missing_variations.yaml"
    bad_yaml.write_text("style_instructions: some style\n")
    with pytest.raises(ValueError, match="missing required key 'variations'"):
        ImagePromptStyleConverter(converter_target=mock_target, filter_path=bad_yaml)


def test_init_filter_empty_variations_raises(mock_target, tmp_path) -> None:
    bad_yaml = tmp_path / "empty_variations.yaml"
    bad_yaml.write_text("style_instructions: some style\nvariations: {}\n")
    with pytest.raises(ValueError, match="non-empty mapping"):
        ImagePromptStyleConverter(converter_target=mock_target, filter_path=bad_yaml)


def test_init_filter_non_mapping_top_level_raises(mock_target, tmp_path) -> None:
    bad_yaml = tmp_path / "list_top_level.yaml"
    bad_yaml.write_text("- not\n- a\n- mapping\n")
    with pytest.raises(ValueError, match="expected a YAML mapping at the top level"):
        ImagePromptStyleConverter(converter_target=mock_target, filter_path=bad_yaml)


def test_init_filter_style_instructions_wrong_type_raises(mock_target, tmp_path) -> None:
    bad_yaml = tmp_path / "bad_style_type.yaml"
    bad_yaml.write_text("style_instructions:\n  - not\n  - a\n  - string\nvariations:\n  v1: desc\n")
    with pytest.raises(ValueError, match="must be a string"):
        ImagePromptStyleConverter(converter_target=mock_target, filter_path=bad_yaml)


def test_list_available_filters() -> None:
    filters = ImagePromptStyleConverter.list_available_filters()
    assert isinstance(filters, list)
    assert "gritty_documentary" in filters
    assert len(filters) > 0


@pytest.mark.asyncio
async def test_convert_async_with_specific_variation(mock_target) -> None:
    converter = ImagePromptStyleConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
        variation="bodycam_footage",
    )
    result = await converter.convert_async(prompt="person walking through a dark alley")

    mock_target.set_system_prompt.assert_called_once()
    system_arg = mock_target.set_system_prompt.call_args[1]["system_prompt"]
    assert "bodycam_footage" in system_arg
    assert "style_instructions" not in system_arg or "CRITICAL INSTRUCTION" in system_arg

    mock_target.send_prompt_async.assert_called_once()
    assert result.output_text == "A blurry bodycam shot of a figure in a dark alley"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_convert_async_with_random_variation(mock_target) -> None:
    converter = ImagePromptStyleConverter(
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
    converter = ImagePromptStyleConverter(
        converter_target=mock_target,
        filter_name="gritty_documentary",
    )
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="/tmp/image.png", input_type="image_path")


def test_duplicate_variation_prefix_logs_warning(mock_target, caplog) -> None:
    """Duplicate variation keys (case-insensitive) should log a warning but not raise."""
    from unittest.mock import MagicMock, mock_open, patch

    duplicate_yaml = {
        "style_instructions": "test style",
        "variations": {
            "bodycam_footage": "first version",
            "BODYCAM_FOOTAGE": "second version",
        },
    }

    mock_seed_prompt = MagicMock()

    with (
        caplog.at_level("WARNING", logger="pyrit.converter.image_prompt_style_converter"),
        patch(
            "pyrit.converter.image_prompt_style_converter.SeedPrompt.from_yaml_file",
            return_value=mock_seed_prompt,
        ),
        patch("pathlib.Path.exists", return_value=True),
        patch("builtins.open", mock_open()),
        patch("yaml.safe_load", return_value=duplicate_yaml),
    ):
        converter = ImagePromptStyleConverter(
            converter_target=mock_target,
            filter_name="gritty_documentary",
        )

    assert "Duplicate variation key" in caplog.text
    assert converter._variation_map["bodycam_footage"] == "BODYCAM_FOOTAGE"
