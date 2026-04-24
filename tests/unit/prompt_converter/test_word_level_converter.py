# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter.text_selection_strategy import (
    AllWordsSelectionStrategy,
    WordIndexSelectionStrategy,
    WordKeywordSelectionStrategy,
    WordProportionSelectionStrategy,
    WordRegexSelectionStrategy,
)
from pyrit.prompt_converter.word_level_converter import WordLevelConverter


class SimpleWordLevelConverter(WordLevelConverter):
    """Simple implementation of WordLevelConverter for testing purposes"""

    async def convert_word_async(self, word: str) -> str:
        return word.upper()


class CustomSplitWordLevelConverter(WordLevelConverter):
    """Custom split implementation of WordLevelConverter for testing purposes"""

    def __init__(self, *, word_selection_strategy=None):
        super().__init__(word_selection_strategy=word_selection_strategy, word_split_separator=None)

    async def convert_word_async(self, word: str) -> str:
        return word.upper()


class TestWordLevelConverter:
    @pytest.mark.asyncio
    async def test_convert_async_all_mode(self):
        converter = SimpleWordLevelConverter()
        result = await converter.convert_async(prompt="hello world this is a test")
        assert result.output_text == "HELLO WORLD THIS IS A TEST"

    @pytest.mark.asyncio
    async def test_convert_async_indices_strategy(self):
        converter = SimpleWordLevelConverter(word_selection_strategy=WordIndexSelectionStrategy(indices=[0, 2, 4]))
        result = await converter.convert_async(prompt="hello world this is a test")
        assert result.output_text == "HELLO world THIS is A test"

    @pytest.mark.asyncio
    async def test_convert_async_keywords_strategy(self):
        converter = SimpleWordLevelConverter(
            word_selection_strategy=WordKeywordSelectionStrategy(keywords=["hello", "test"])
        )
        result = await converter.convert_async(prompt="hello world this is a test")
        assert result.output_text == "HELLO world this is a TEST"

    @pytest.mark.asyncio
    async def test_convert_async_regex_strategy(self):
        converter = SimpleWordLevelConverter(word_selection_strategy=WordRegexSelectionStrategy(pattern=r"^[aeiou]"))
        result = await converter.convert_async(prompt="hello awesome interesting text")
        assert result.output_text == "hello AWESOME INTERESTING text"

    @pytest.mark.asyncio
    async def test_convert_async_proportion_strategy(self):
        converter = SimpleWordLevelConverter(
            word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5, seed=42)
        )
        result = await converter.convert_async(prompt="hello world this is")
        # With seed=42, should produce consistent results
        assert result.output_text != "hello world this is"  # Some words should be converted
        assert result.output_text != "HELLO WORLD THIS IS"  # Not all words should be converted

    @pytest.mark.asyncio
    async def test_split_separator_default(self):
        converter = SimpleWordLevelConverter()
        result = await converter.convert_async(prompt="hello\tworld\ntest")
        assert result.output_text == "HELLO\tWORLD\nTEST"

    @pytest.mark.asyncio
    async def test_split_separator_override(self):
        converter = CustomSplitWordLevelConverter()
        result = await converter.convert_async(prompt="hello\tworld\ntest")
        assert result.output_text == "HELLO WORLD TEST"

    @pytest.mark.asyncio
    async def test_join_words_override(self):
        class CustomJoinConverter(SimpleWordLevelConverter):
            def join_words(self, words: list[str]) -> str:
                return "#".join(words)

        converter = CustomJoinConverter()
        result = await converter.convert_async(prompt="hello world test")
        assert result.output_text == "HELLO#WORLD#TEST"

    @pytest.mark.asyncio
    async def test_empty_prompt(self):
        converter = SimpleWordLevelConverter()
        result = await converter.convert_async(prompt="")
        assert result.output_text == ""

    @pytest.mark.asyncio
    async def test_single_word(self):
        converter = SimpleWordLevelConverter()
        result = await converter.convert_async(prompt="hello")
        assert result.output_text == "HELLO"

    @pytest.mark.asyncio
    async def test_indices_out_of_range(self):
        """Test that out of range indices raise ValueError"""
        converter = SimpleWordLevelConverter(word_selection_strategy=WordIndexSelectionStrategy(indices=[0, 10]))
        with pytest.raises(ValueError, match="Invalid word indices"):
            await converter.convert_async(prompt="hello world")

    @pytest.mark.asyncio
    async def test_indices_valid_only(self):
        """Test that only valid indices are used"""
        converter = SimpleWordLevelConverter(word_selection_strategy=WordIndexSelectionStrategy(indices=[0]))
        result = await converter.convert_async(prompt="hello world")
        assert result.output_text == "HELLO world"

    @pytest.mark.asyncio
    async def test_proportion_edge_cases(self):
        # Proportion 0.0 - no words converted
        converter = SimpleWordLevelConverter(word_selection_strategy=WordProportionSelectionStrategy(proportion=0.0))
        result = await converter.convert_async(prompt="hello world test")
        assert result.output_text == "hello world test"

        # Proportion 1.0 - all words converted
        converter = SimpleWordLevelConverter(word_selection_strategy=WordProportionSelectionStrategy(proportion=1.0))
        result = await converter.convert_async(prompt="hello world test")
        assert result.output_text == "HELLO WORLD TEST"

    @pytest.mark.asyncio
    async def test_default_is_all_words(self):
        """Test that default behavior converts all words"""
        converter = SimpleWordLevelConverter()
        assert isinstance(converter._word_selection_strategy, AllWordsSelectionStrategy)
        result = await converter.convert_async(prompt="test prompt")
        assert result.output_text == "TEST PROMPT"
