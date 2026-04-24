# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter.text_selection_strategy import (
    AllWordsSelectionStrategy,
    IndexSelectionStrategy,
    KeywordSelectionStrategy,
    PositionSelectionStrategy,
    ProportionSelectionStrategy,
    RangeSelectionStrategy,
    RegexSelectionStrategy,
    WordIndexSelectionStrategy,
    WordKeywordSelectionStrategy,
    WordPositionSelectionStrategy,
    WordProportionSelectionStrategy,
    WordRegexSelectionStrategy,
)


class TestIndexSelectionStrategy:
    def test_select_range_full_text(self):
        strategy = IndexSelectionStrategy(start=0, end=None)
        result = strategy.select_range(text="Hello World")
        assert result == (0, 11)

    def test_select_range_partial(self):
        strategy = IndexSelectionStrategy(start=0, end=5)
        result = strategy.select_range(text="Hello World")
        assert result == (0, 5)

    def test_select_range_middle(self):
        strategy = IndexSelectionStrategy(start=6, end=11)
        result = strategy.select_range(text="Hello World")
        assert result == (6, 11)

    def test_select_range_beyond_length(self):
        strategy = IndexSelectionStrategy(start=0, end=100)
        result = strategy.select_range(text="Hello World")
        assert result == (0, 11)

    def test_select_range_negative_start(self):
        strategy = IndexSelectionStrategy(start=-5, end=5)
        result = strategy.select_range(text="Hello World")
        assert result == (0, 5)

    def test_select_range_start_after_end(self):
        strategy = IndexSelectionStrategy(start=10, end=5)
        result = strategy.select_range(text="Hello World")
        assert result == (10, 10)


class TestRegexSelectionStrategy:
    def test_select_range_simple_match(self):
        strategy = RegexSelectionStrategy(pattern=r"World")
        result = strategy.select_range(text="Hello World")
        assert result == (6, 11)

    def test_select_range_pattern_match(self):
        strategy = RegexSelectionStrategy(pattern=r"\d+")
        result = strategy.select_range(text="The number is 12345 here")
        assert result == (14, 19)

    def test_select_range_no_match(self):
        strategy = RegexSelectionStrategy(pattern=r"xyz")
        result = strategy.select_range(text="Hello World")
        assert result == (0, 0)

    def test_select_range_first_match_only(self):
        strategy = RegexSelectionStrategy(pattern=r"\d+")
        result = strategy.select_range(text="First 123 and second 456")
        assert result == (6, 9)

    def test_select_range_complex_pattern(self):
        strategy = RegexSelectionStrategy(pattern=r"password:\s*\w+")
        result = strategy.select_range(text="The password: secret123 is here")
        assert result == (4, 23)


class TestKeywordSelectionStrategy:
    def test_select_range_exact_keyword(self):
        strategy = KeywordSelectionStrategy(keyword="secret")
        result = strategy.select_range(text="The secret is here")
        assert result == (4, 10)

    def test_select_range_with_context_before(self):
        strategy = KeywordSelectionStrategy(keyword="secret", context_before=4)
        result = strategy.select_range(text="The secret is here")
        assert result == (0, 10)

    def test_select_range_with_context_after(self):
        strategy = KeywordSelectionStrategy(keyword="secret", context_after=3)
        result = strategy.select_range(text="The secret is here")
        assert result == (4, 13)

    def test_select_range_with_both_contexts(self):
        strategy = KeywordSelectionStrategy(keyword="secret", context_before=4, context_after=3)
        result = strategy.select_range(text="The secret is here")
        assert result == (0, 13)

    def test_select_range_case_insensitive(self):
        strategy = KeywordSelectionStrategy(keyword="SECRET", case_sensitive=False)
        result = strategy.select_range(text="The secret is here")
        assert result == (4, 10)

    def test_select_range_case_sensitive_no_match(self):
        strategy = KeywordSelectionStrategy(keyword="SECRET", case_sensitive=True)
        result = strategy.select_range(text="The secret is here")
        assert result == (0, 0)

    def test_select_range_keyword_not_found(self):
        strategy = KeywordSelectionStrategy(keyword="xyz")
        result = strategy.select_range(text="The secret is here")
        assert result == (0, 0)

    def test_select_range_context_at_boundaries(self):
        strategy = KeywordSelectionStrategy(keyword="secret", context_before=100, context_after=100)
        result = strategy.select_range(text="The secret is here")
        assert result == (0, 18)


class TestPositionSelectionStrategy:
    def test_select_range_first_half(self):
        strategy = PositionSelectionStrategy(start_proportion=0.0, end_proportion=0.5)
        result = strategy.select_range(text="0123456789")
        assert result == (0, 5)

    def test_select_range_second_half(self):
        strategy = PositionSelectionStrategy(start_proportion=0.5, end_proportion=1.0)
        result = strategy.select_range(text="0123456789")
        assert result == (5, 10)

    def test_select_range_first_third(self):
        strategy = PositionSelectionStrategy(start_proportion=0.0, end_proportion=1 / 3)
        result = strategy.select_range(text="012345678")
        assert result == (0, 3)

    def test_select_range_second_third(self):
        strategy = PositionSelectionStrategy(start_proportion=1 / 3, end_proportion=2 / 3)
        result = strategy.select_range(text="012345678")
        assert result == (3, 6)

    def test_select_range_last_third(self):
        strategy = PositionSelectionStrategy(start_proportion=2 / 3, end_proportion=1.0)
        result = strategy.select_range(text="012345678")
        assert result == (6, 9)

    def test_select_range_first_quarter(self):
        strategy = PositionSelectionStrategy(start_proportion=0.0, end_proportion=0.25)
        result = strategy.select_range(text="0123456789ABCDEF")
        assert result == (0, 4)

    def test_select_range_last_quarter(self):
        strategy = PositionSelectionStrategy(start_proportion=0.75, end_proportion=1.0)
        result = strategy.select_range(text="0123456789ABCDEF")
        assert result == (12, 16)

    def test_select_range_middle_half(self):
        strategy = PositionSelectionStrategy(start_proportion=0.25, end_proportion=0.75)
        result = strategy.select_range(text="0123456789")
        assert result == (2, 7)

    def test_invalid_start_proportion_too_high_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion must be between 0.0 and 1.0"):
            PositionSelectionStrategy(start_proportion=1.5, end_proportion=1.0)

    def test_invalid_end_proportion_too_high_raises_error(self):
        with pytest.raises(ValueError, match="end_proportion must be between 0.0 and 1.0"):
            PositionSelectionStrategy(start_proportion=0.0, end_proportion=1.5)

    def test_invalid_start_proportion_negative_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion must be between 0.0 and 1.0"):
            PositionSelectionStrategy(start_proportion=-0.1, end_proportion=1.0)

    def test_invalid_end_proportion_negative_raises_error(self):
        with pytest.raises(ValueError, match="end_proportion must be between 0.0 and 1.0"):
            PositionSelectionStrategy(start_proportion=0.0, end_proportion=-0.1)

    def test_invalid_start_equals_end_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion .* must be less than end_proportion"):
            PositionSelectionStrategy(start_proportion=0.5, end_proportion=0.5)

    def test_invalid_start_greater_than_end_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion .* must be less than end_proportion"):
            PositionSelectionStrategy(start_proportion=0.75, end_proportion=0.25)

    def test_select_range_custom_range(self):
        strategy = PositionSelectionStrategy(start_proportion=0.1, end_proportion=0.9)
        result = strategy.select_range(text="0123456789")
        assert result == (1, 9)

    def test_select_range_boundary_values(self):
        strategy = PositionSelectionStrategy(start_proportion=0.0, end_proportion=1.0)
        result = strategy.select_range(text="0123456789")
        assert result == (0, 10)


class TestProportionSelectionStrategy:
    def test_select_range_start_anchor(self):
        strategy = ProportionSelectionStrategy(proportion=0.5, anchor="start")
        result = strategy.select_range(text="0123456789")
        assert result == (0, 5)

    def test_select_range_end_anchor(self):
        strategy = ProportionSelectionStrategy(proportion=0.5, anchor="end")
        result = strategy.select_range(text="0123456789")
        assert result == (5, 10)

    def test_select_range_middle_anchor(self):
        strategy = ProportionSelectionStrategy(proportion=0.4, anchor="middle")
        result = strategy.select_range(text="0123456789")
        assert result == (3, 7)

    def test_select_range_random_anchor_with_seed(self):
        strategy = ProportionSelectionStrategy(proportion=0.3, anchor="random", seed=42)
        result1 = strategy.select_range(text="0123456789")
        strategy2 = ProportionSelectionStrategy(proportion=0.3, anchor="random", seed=42)
        result2 = strategy2.select_range(text="0123456789")
        assert result1 == result2  # Same seed should give same result

    def test_select_range_random_anchor_different_seeds(self):
        strategy1 = ProportionSelectionStrategy(proportion=0.3, anchor="random", seed=42)
        strategy2 = ProportionSelectionStrategy(proportion=0.3, anchor="random", seed=43)
        result1 = strategy1.select_range(text="0123456789")
        result2 = strategy2.select_range(text="0123456789")
        # Different seeds may give different results (not guaranteed, but highly likely)
        # Just ensure both are valid ranges
        assert 0 <= result1[0] <= result1[1] <= 10
        assert 0 <= result2[0] <= result2[1] <= 10

    def test_invalid_proportion_too_high_raises_error(self):
        with pytest.raises(ValueError, match="Proportion must be between 0.0 and 1.0"):
            ProportionSelectionStrategy(proportion=1.5, anchor="start")

    def test_invalid_proportion_negative_raises_error(self):
        with pytest.raises(ValueError, match="Proportion must be between 0.0 and 1.0"):
            ProportionSelectionStrategy(proportion=-0.1, anchor="start")

    def test_invalid_anchor_raises_error(self):
        with pytest.raises(ValueError, match="Invalid anchor 'invalid'"):
            ProportionSelectionStrategy(proportion=0.5, anchor="invalid")

    def test_proportion_zero(self):
        strategy = ProportionSelectionStrategy(proportion=0.0, anchor="start")
        result = strategy.select_range(text="0123456789")
        assert result == (0, 0)

    def test_proportion_one(self):
        strategy = ProportionSelectionStrategy(proportion=1.0, anchor="start")
        result = strategy.select_range(text="0123456789")
        assert result == (0, 10)


class TestRangeSelectionStrategy:
    def test_select_range_full_range(self):
        strategy = RangeSelectionStrategy(start_proportion=0.0, end_proportion=1.0)
        result = strategy.select_range(text="0123456789")
        assert result == (0, 10)

    def test_select_range_middle_half(self):
        strategy = RangeSelectionStrategy(start_proportion=0.25, end_proportion=0.75)
        result = strategy.select_range(text="0123456789")
        assert result == (2, 7)

    def test_select_range_first_quarter(self):
        strategy = RangeSelectionStrategy(start_proportion=0.0, end_proportion=0.25)
        result = strategy.select_range(text="0123456789ABCDEF")
        assert result == (0, 4)

    def test_select_range_last_quarter(self):
        strategy = RangeSelectionStrategy(start_proportion=0.75, end_proportion=1.0)
        result = strategy.select_range(text="0123456789ABCDEF")
        assert result == (12, 16)

    def test_invalid_start_proportion_too_high_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion must be between 0.0 and 1.0"):
            RangeSelectionStrategy(start_proportion=1.5, end_proportion=1.0)

    def test_invalid_end_proportion_too_high_raises_error(self):
        with pytest.raises(ValueError, match="end_proportion must be between 0.0 and 1.0"):
            RangeSelectionStrategy(start_proportion=0.0, end_proportion=1.5)

    def test_invalid_start_proportion_negative_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion must be between 0.0 and 1.0"):
            RangeSelectionStrategy(start_proportion=-0.1, end_proportion=1.0)

    def test_invalid_start_equals_end_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion .* must be less than end_proportion"):
            RangeSelectionStrategy(start_proportion=0.5, end_proportion=0.5)

    def test_invalid_start_greater_than_end_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion .* must be less than end_proportion"):
            RangeSelectionStrategy(start_proportion=0.75, end_proportion=0.25)


# ============================================================================
# Word-Level Selection Strategy Tests
# ============================================================================


class TestWordIndexSelectionStrategy:
    def test_select_words_specific_indices(self):
        strategy = WordIndexSelectionStrategy(indices=[0, 2, 4])
        words = ["The", "quick", "brown", "fox", "jumps"]
        result = strategy.select_words(words=words)
        assert result == [0, 2, 4]

    def test_select_words_single_index(self):
        strategy = WordIndexSelectionStrategy(indices=[3])
        words = ["The", "quick", "brown", "fox", "jumps"]
        result = strategy.select_words(words=words)
        assert result == [3]

    def test_select_words_empty_list(self):
        strategy = WordIndexSelectionStrategy(indices=[0, 1])
        result = strategy.select_words(words=[])
        assert result == []

    def test_select_words_out_of_range_raises_error(self):
        strategy = WordIndexSelectionStrategy(indices=[0, 10])
        words = ["The", "quick", "brown"]
        with pytest.raises(ValueError, match="Invalid word indices"):
            strategy.select_words(words=words)

    def test_select_words_negative_index_raises_error(self):
        strategy = WordIndexSelectionStrategy(indices=[-1])
        words = ["The", "quick", "brown"]
        with pytest.raises(ValueError, match="Invalid word indices"):
            strategy.select_words(words=words)

    def test_select_range_converts_words_to_chars(self):
        strategy = WordIndexSelectionStrategy(indices=[1, 2])
        result = strategy.select_range(text="The quick brown fox")
        # "quick brown" starts at index 4 and ends at index 15
        assert result == (4, 15)


class TestWordKeywordSelectionStrategy:
    def test_select_words_exact_matches(self):
        strategy = WordKeywordSelectionStrategy(keywords=["quick", "fox"])
        words = ["The", "quick", "brown", "fox", "jumps"]
        result = strategy.select_words(words=words)
        assert result == [1, 3]

    def test_select_words_case_sensitive(self):
        strategy = WordKeywordSelectionStrategy(keywords=["Quick"], case_sensitive=True)
        words = ["The", "quick", "brown", "fox"]
        result = strategy.select_words(words=words)
        assert result == []

    def test_select_words_case_insensitive(self):
        strategy = WordKeywordSelectionStrategy(keywords=["QUICK", "FOX"], case_sensitive=False)
        words = ["The", "quick", "brown", "fox"]
        result = strategy.select_words(words=words)
        assert result == [1, 3]

    def test_select_words_no_matches(self):
        strategy = WordKeywordSelectionStrategy(keywords=["dog", "cat"])
        words = ["The", "quick", "brown", "fox"]
        result = strategy.select_words(words=words)
        assert result == []

    def test_select_words_empty_list(self):
        strategy = WordKeywordSelectionStrategy(keywords=["test"])
        result = strategy.select_words(words=[])
        assert result == []


class TestWordProportionSelectionStrategy:
    def test_select_words_half_proportion(self):
        strategy = WordProportionSelectionStrategy(proportion=0.5, seed=42)
        words = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        result = strategy.select_words(words=words)
        assert len(result) == 5
        assert all(0 <= idx < 10 for idx in result)

    def test_select_words_reproducible_with_seed(self):
        strategy1 = WordProportionSelectionStrategy(proportion=0.3, seed=42)
        strategy2 = WordProportionSelectionStrategy(proportion=0.3, seed=42)
        words = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        result1 = strategy1.select_words(words=words)
        result2 = strategy2.select_words(words=words)
        assert result1 == result2

    def test_select_words_zero_proportion(self):
        strategy = WordProportionSelectionStrategy(proportion=0.0, seed=42)
        words = ["a", "b", "c", "d", "e"]
        result = strategy.select_words(words=words)
        assert result == []

    def test_select_words_full_proportion(self):
        strategy = WordProportionSelectionStrategy(proportion=1.0, seed=42)
        words = ["a", "b", "c", "d", "e"]
        result = strategy.select_words(words=words)
        assert len(result) == 5

    def test_select_words_empty_list(self):
        strategy = WordProportionSelectionStrategy(proportion=0.5, seed=42)
        result = strategy.select_words(words=[])
        assert result == []

    def test_invalid_proportion_too_high_raises_error(self):
        with pytest.raises(ValueError, match="Proportion must be between 0.0 and 1.0"):
            WordProportionSelectionStrategy(proportion=1.5)

    def test_invalid_proportion_negative_raises_error(self):
        with pytest.raises(ValueError, match="Proportion must be between 0.0 and 1.0"):
            WordProportionSelectionStrategy(proportion=-0.1)


class TestWordRegexSelectionStrategy:
    def test_select_words_matching_pattern(self):
        strategy = WordRegexSelectionStrategy(pattern=r"\d+")
        words = ["The", "number", "123", "and", "456", "here"]
        result = strategy.select_words(words=words)
        assert result == [2, 4]

    def test_select_words_partial_match(self):
        strategy = WordRegexSelectionStrategy(pattern=r"^[A-Z]")
        words = ["The", "quick", "Brown", "fox"]
        result = strategy.select_words(words=words)
        assert result == [0, 2]

    def test_select_words_complex_pattern(self):
        strategy = WordRegexSelectionStrategy(pattern=r"^[a-z]+ing$")
        words = ["running", "jump", "walking", "sit", "thinking"]
        result = strategy.select_words(words=words)
        assert result == [0, 2, 4]

    def test_select_words_no_matches(self):
        strategy = WordRegexSelectionStrategy(pattern=r"\d+")
        words = ["The", "quick", "brown", "fox"]
        result = strategy.select_words(words=words)
        assert result == []

    def test_select_words_empty_list(self):
        strategy = WordRegexSelectionStrategy(pattern=r"\w+")
        result = strategy.select_words(words=[])
        assert result == []


class TestWordPositionSelectionStrategy:
    def test_select_words_first_half(self):
        strategy = WordPositionSelectionStrategy(start_proportion=0.0, end_proportion=0.5)
        words = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        result = strategy.select_words(words=words)
        assert result == [0, 1, 2, 3, 4]

    def test_select_words_second_half(self):
        strategy = WordPositionSelectionStrategy(start_proportion=0.5, end_proportion=1.0)
        words = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        result = strategy.select_words(words=words)
        assert result == [5, 6, 7, 8, 9]

    def test_select_words_first_third(self):
        strategy = WordPositionSelectionStrategy(start_proportion=0.0, end_proportion=1 / 3)
        words = ["a", "b", "c", "d", "e", "f", "g", "h", "i"]
        result = strategy.select_words(words=words)
        assert result == [0, 1, 2]

    def test_select_words_middle_third(self):
        strategy = WordPositionSelectionStrategy(start_proportion=1 / 3, end_proportion=2 / 3)
        words = ["a", "b", "c", "d", "e", "f", "g", "h", "i"]
        result = strategy.select_words(words=words)
        assert result == [3, 4, 5]

    def test_select_words_last_third(self):
        strategy = WordPositionSelectionStrategy(start_proportion=2 / 3, end_proportion=1.0)
        words = ["a", "b", "c", "d", "e", "f", "g", "h", "i"]
        result = strategy.select_words(words=words)
        assert result == [6, 7, 8]

    def test_select_words_custom_range(self):
        strategy = WordPositionSelectionStrategy(start_proportion=0.2, end_proportion=0.8)
        words = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        result = strategy.select_words(words=words)
        assert result == [2, 3, 4, 5, 6, 7]

    def test_select_words_empty_list(self):
        strategy = WordPositionSelectionStrategy(start_proportion=0.0, end_proportion=0.5)
        result = strategy.select_words(words=[])
        assert result == []

    def test_invalid_start_proportion_too_high_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion must be between 0.0 and 1.0"):
            WordPositionSelectionStrategy(start_proportion=1.5, end_proportion=1.0)

    def test_invalid_end_proportion_too_high_raises_error(self):
        with pytest.raises(ValueError, match="end_proportion must be between 0.0 and 1.0"):
            WordPositionSelectionStrategy(start_proportion=0.0, end_proportion=1.5)

    def test_invalid_start_proportion_negative_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion must be between 0.0 and 1.0"):
            WordPositionSelectionStrategy(start_proportion=-0.1, end_proportion=1.0)

    def test_invalid_start_equals_end_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion .* must be less than end_proportion"):
            WordPositionSelectionStrategy(start_proportion=0.5, end_proportion=0.5)

    def test_invalid_start_greater_than_end_raises_error(self):
        with pytest.raises(ValueError, match="start_proportion .* must be less than end_proportion"):
            WordPositionSelectionStrategy(start_proportion=0.75, end_proportion=0.25)


class TestAllWordsSelectionStrategy:
    def test_select_all_words(self):
        strategy = AllWordsSelectionStrategy()
        words = ["The", "quick", "brown", "fox", "jumps"]
        result = strategy.select_words(words=words)
        assert result == [0, 1, 2, 3, 4]

    def test_select_all_words_single_word(self):
        strategy = AllWordsSelectionStrategy()
        words = ["Hello"]
        result = strategy.select_words(words=words)
        assert result == [0]

    def test_select_all_words_empty_list(self):
        strategy = AllWordsSelectionStrategy()
        result = strategy.select_words(words=[])
        assert result == []
