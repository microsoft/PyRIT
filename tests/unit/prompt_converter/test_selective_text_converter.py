# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import (
    Base64Converter,
    LeetspeakConverter,
    ROT13Converter,
    SelectiveTextConverter,
)
from pyrit.prompt_converter.text_selection_strategy import (
    IndexSelectionStrategy,
    KeywordSelectionStrategy,
    PositionSelectionStrategy,
    ProportionSelectionStrategy,
    RangeSelectionStrategy,
    RegexSelectionStrategy,
    WordIndexSelectionStrategy,
    WordProportionSelectionStrategy,
)


class TestSelectiveTextConverter:
    async def test_initialization_valid(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        assert converter is not None

    async def test_initialization_with_preserve_tokens(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
            preserve_tokens=True,
            start_token="<<",
            end_token=">>",
        )
        assert converter is not None

    async def test_initialization_invalid_converter_input_type(self):
        # Create a mock converter that doesn't support text input
        class NonTextConverter(Base64Converter):
            def input_supported(self, input_type):
                return False

        with pytest.raises(ValueError, match="does not support text input"):
            SelectiveTextConverter(
                converter=NonTextConverter(),
                selection_strategy=IndexSelectionStrategy(start=0, end=5),
            )

    async def test_initialization_invalid_converter_output_type(self):
        # Create a mock converter that doesn't support text output
        class NonTextConverter(Base64Converter):
            def output_supported(self, output_type):
                return False

        with pytest.raises(ValueError, match="does not support text output"):
            SelectiveTextConverter(
                converter=NonTextConverter(),
                selection_strategy=IndexSelectionStrategy(start=0, end=5),
            )

    async def test_convert_async_with_index_strategy(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        result = await converter.convert_async(prompt="Hello World", input_type="text")
        # "Hello" in base64 is "SGVsbG8="  # noqa: ERA001
        assert result.output_text == "SGVsbG8= World"
        assert result.output_type == "text"

    async def test_convert_async_with_regex_strategy(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=RegexSelectionStrategy(pattern=r"\d+"),
        )
        result = await converter.convert_async(prompt="The code is 12345 here", input_type="text")
        # "12345" in base64 is "MTIzNDU="  # noqa: ERA001
        assert result.output_text == "The code is MTIzNDU= here"
        assert result.output_type == "text"

    async def test_convert_async_with_keyword_strategy(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=KeywordSelectionStrategy(keyword="secret"),
        )
        result = await converter.convert_async(prompt="The secret is here", input_type="text")
        # "secret" in base64 is "c2VjcmV0"  # noqa: ERA001
        assert result.output_text == "The c2VjcmV0 is here"
        assert result.output_type == "text"

    async def test_convert_async_with_position_strategy(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=PositionSelectionStrategy(start_proportion=0.0, end_proportion=0.5),
        )
        result = await converter.convert_async(prompt="0123456789", input_type="text")
        # "01234" in base64 is "MDEyMzQ="  # noqa: ERA001
        assert result.output_text == "MDEyMzQ=56789"
        assert result.output_type == "text"

    async def test_convert_async_with_proportion_strategy(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=ProportionSelectionStrategy(proportion=0.5, anchor="start"),
        )
        result = await converter.convert_async(prompt="0123456789", input_type="text")
        # "01234" in base64 is "MDEyMzQ="  # noqa: ERA001
        assert result.output_text == "MDEyMzQ=56789"
        assert result.output_type == "text"

    async def test_convert_async_with_range_strategy(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=RangeSelectionStrategy(start_proportion=0.0, end_proportion=0.5),
        )
        result = await converter.convert_async(prompt="0123456789", input_type="text")
        # "01234" in base64 is "MDEyMzQ="  # noqa: ERA001
        assert result.output_text == "MDEyMzQ=56789"
        assert result.output_type == "text"

    async def test_convert_async_with_preserve_tokens(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
            preserve_tokens=True,
        )
        result = await converter.convert_async(prompt="Hello World", input_type="text")
        # "Hello" in base64 is "SGVsbG8="  # noqa: ERA001
        assert result.output_text == "⟪SGVsbG8=⟫ World"
        assert result.output_type == "text"

    async def test_convert_async_with_custom_tokens(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
            preserve_tokens=True,
            start_token="<<",
            end_token=">>",
        )
        result = await converter.convert_async(prompt="Hello World", input_type="text")
        # "Hello" in base64 is "SGVsbG8="  # noqa: ERA001
        assert result.output_text == "<<SGVsbG8=>> World"
        assert result.output_type == "text"

    async def test_convert_async_no_match_returns_original(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=RegexSelectionStrategy(pattern=r"\d+"),
        )
        result = await converter.convert_async(prompt="No numbers here", input_type="text")
        assert result.output_text == "No numbers here"
        assert result.output_type == "text"

    async def test_convert_async_invalid_input_type(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        with pytest.raises(ValueError, match="only supports text input"):
            await converter.convert_async(prompt="Hello", input_type="image_path")

    async def test_convert_async_chaining_with_preserved_tokens(self):
        # First converter: convert first half with preserve_tokens
        converter1 = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=PositionSelectionStrategy(start_proportion=0.0, end_proportion=0.5),
            preserve_tokens=True,
        )
        result1 = await converter1.convert_async(prompt="HelloWorld", input_type="text")

        # Second converter: convert second half with preserve_tokens
        converter2 = SelectiveTextConverter(
            converter=ROT13Converter(),
            selection_strategy=PositionSelectionStrategy(start_proportion=0.5, end_proportion=1.0),
            preserve_tokens=True,
        )
        result2 = await converter2.convert_async(prompt="HelloWorld", input_type="text")

        # Verify both conversions can be identified
        assert "⟪" in result1.output_text
        assert "⟫" in result1.output_text
        assert "⟪" in result2.output_text
        assert "⟫" in result2.output_text

    async def test_convert_async_middle_section(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=4, end=10),
        )
        result = await converter.convert_async(prompt="The secret code", input_type="text")
        # "secret" in base64 is "c2VjcmV0"  # noqa: ERA001
        assert result.output_text == "The c2VjcmV0 code"
        assert result.output_type == "text"

    async def test_convert_async_end_section(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=11, end=None),
        )
        result = await converter.convert_async(prompt="Hello World", input_type="text")
        # "" (empty string) in base64 is ""  # noqa: ERA001
        assert result.output_text == "Hello World"
        assert result.output_type == "text"

    async def test_input_supported(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        assert converter.input_supported("text") is True
        assert converter.input_supported("image_path") is False

    async def test_output_supported(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        assert converter.output_supported("text") is True
        assert converter.output_supported("image_path") is False

    async def test_convert_async_with_keyword_and_context(self):
        converter = SelectiveTextConverter(
            converter=Base64Converter(),
            selection_strategy=KeywordSelectionStrategy(keyword="secret", context_before=4, context_after=3),
        )
        result = await converter.convert_async(prompt="The secret is here", input_type="text")
        # "The secret is" in base64 is "VGhlIHNlY3JldCBpcw=="  # noqa: ERA001
        assert result.output_text == "VGhlIHNlY3JldCBpcw== here"
        assert result.output_type == "text"

    async def test_convert_async_entire_text_with_range(self):
        converter = SelectiveTextConverter(
            converter=ROT13Converter(),
            selection_strategy=RangeSelectionStrategy(start_proportion=0.0, end_proportion=1.0),
        )
        result = await converter.convert_async(prompt="Hello", input_type="text")
        assert result.output_text == "Uryyb"
        assert result.output_type == "text"

    async def test_initialization_word_level_strategy_with_word_level_converter_raises(self):
        """Test that using a word-level selection strategy with a WordLevelConverter
        that has a non-default word_selection_strategy raises ValueError."""
        with pytest.raises(ValueError, match="Cannot use a WordSelectionStrategy"):
            SelectiveTextConverter(
                converter=LeetspeakConverter(word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5)),
                selection_strategy=WordIndexSelectionStrategy(indices=[0, 1]),
            )

    async def test_initialization_word_level_strategy_with_default_word_level_converter_allowed(self):
        """Test that using a word-level selection strategy with a WordLevelConverter
        that has the default (AllWordsSelectionStrategy) is allowed."""
        # This should NOT raise - LeetspeakConverter with no explicit strategy uses AllWordsSelectionStrategy
        converter = SelectiveTextConverter(
            converter=LeetspeakConverter(),
            selection_strategy=WordIndexSelectionStrategy(indices=[0]),
        )
        assert converter is not None

    async def test_initialization_char_level_strategy_with_word_level_converter_allowed(self):
        """Test that using a character-level selection strategy with a WordLevelConverter
        that has a non-default word_selection_strategy is allowed (this is meaningful)."""
        # This should NOT raise - character-level strategy passes a substring to the converter,
        # so the converter's word selection strategy can meaningfully operate on it
        converter = SelectiveTextConverter(
            converter=LeetspeakConverter(word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5)),
            selection_strategy=IndexSelectionStrategy(start=0, end=20),
        )
        assert converter is not None
