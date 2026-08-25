# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import re
import string
from collections.abc import Collection
from re import Pattern
from typing import Any

from pyrit.common.random_context import get_random_generator

# Common English function words used by ContentWordSelectionStrategy. This is a
# dependency-free stand-in for POS filtering (no NLTK / tagger download).
DEFAULT_CONTENT_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "when",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "to",
        "with",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "doing",
        "have",
        "has",
        "had",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "our",
        "their",
        "not",
        "no",
        "nor",
        "so",
        "than",
        "too",
        "very",
        "can",
        "will",
        "just",
        "about",
        "up",
        "out",
        "how",
        "what",
        "which",
        "who",
        "whom",
        "why",
        "where",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "also",
        "over",
        "after",
        "before",
        "between",
        "through",
        "during",
        "above",
        "below",
        "again",
        "further",
        "once",
        "here",
        "there",
        "any",
        "both",
        "please",
    }
)


class TextSelectionStrategy(abc.ABC):
    """
    Base class for text selection strategies used by SelectiveTextConverter and WordLevelConverter.
    Defines how to select a region of text or words for conversion.
    """

    @abc.abstractmethod
    def select_range(self, *, text: str) -> tuple[int, int]:
        """
        Select a range of characters in the text to be converted.

        Args:
            text (str): The input text to select from.

        Returns:
            tuple[int, int]: A tuple of (start_index, end_index) representing the character range.
                The range is inclusive of start_index and exclusive of end_index.
        """

    def get_identifier_params(self) -> dict[str, Any]:
        """
        Return the parameters that affect which text this strategy selects.

        Values must be JSON-serializable and stored in a stable, order-independent
        form when the original input was a set-like collection.

        Returns:
            dict[str, Any]: Behavioral parameters for converter identifiers.
        """
        return {}


class TokenSelectionStrategy(TextSelectionStrategy):
    """
    A special selection strategy that signals SelectiveTextConverter to auto-detect
    and convert text between start/end tokens (e.g., ⟪ and ⟫).

    This strategy is used when chaining converters with preserve_tokens=True.
    Instead of programmatically selecting text, it relies on tokens already present
    in the text from a previous converter.

    Example:
        >>> first_converter = SelectiveTextConverter(
        ...     sub_converter=Base64Converter(),
        ...     selection_strategy=WordPositionSelectionStrategy(start_proportion=0.5, end_proportion=1.0),
        ...     preserve_tokens=True
        ... )
        >>> # Text after first converter: "hello world ⟪Y29udmVydGVk⟫"
        >>>
        >>> second_converter = SelectiveTextConverter(
        ...     sub_converter=ROT13Converter(),
        ...     selection_strategy=TokenSelectionStrategy(),  # Auto-detect tokens
        ...     preserve_tokens=True
        ... )
    """

    def select_range(self, *, text: str) -> tuple[int, int]:
        """
        Do not use this method for TokenSelectionStrategy.
        SelectiveTextConverter handles token detection separately.

        Args:
            text (str): The input text (ignored).

        Returns:
            tuple[int, int]: Always returns (0, 0) as this strategy uses token detection instead.
        """
        return (0, 0)


class WordSelectionStrategy(TextSelectionStrategy):
    """
    Base class for word-level selection strategies.

    Word selection strategies work by splitting text into words and selecting specific word indices.
    They provide a select_words() method and implement select_range() by converting word selections
    to character ranges.
    """

    @abc.abstractmethod
    def select_words(self, *, words: list[str]) -> list[int]:
        """
        Select word indices to be converted.

        Args:
            words (list[str]): The list of words to select from.

        Returns:
            list[int]: A list of indices representing which words should be converted.
        """

    def select_range(self, *, text: str, word_separator: str = " ") -> tuple[int, int]:
        """
        Select a character range by first selecting words, then converting to character positions.

        This implementation splits the text by word_separator, gets selected word indices,
        then calculates the character range that spans those words.

        Args:
            text (str): The input text to select from.
            word_separator (str): The separator used to split words. Defaults to " ".

        Returns:
            tuple[int, int]: A tuple of (start_index, end_index) representing the character range
                that encompasses all selected words.
        """
        words = text.split(word_separator)
        selected_indices = self.select_words(words=words)

        if not selected_indices:
            return (0, 0)

        # Find the character positions of the selected words
        min_idx = min(selected_indices)
        max_idx = max(selected_indices)

        # Calculate character positions
        char_pos = 0
        start_char = 0
        end_char = 0

        for i, word in enumerate(words):
            if i == min_idx:
                start_char = char_pos
            if i == max_idx:
                end_char = char_pos + len(word)
                break
            char_pos += len(word) + len(word_separator)

        return (start_char, end_char)


class IndexSelectionStrategy(TextSelectionStrategy):
    """
    Selects text based on absolute character indices.
    """

    def __init__(self, *, start: int = 0, end: int | None = None) -> None:
        """
        Initialize the index selection strategy.

        Args:
            start (int): The starting character index (inclusive). Defaults to 0.
            end (int | None): The ending character index (exclusive). If None, selects to end of text.
        """
        self._start = start
        self._end = end

    def select_range(self, *, text: str) -> tuple[int, int]:
        """
        Select a range based on absolute character indices.

        Args:
            text (str): The input text to select from.

        Returns:
            tuple[int, int]: A tuple of (start_index, end_index).
        """
        end = self._end if self._end is not None else len(text)
        start = max(0, min(self._start, len(text)))
        end = max(start, min(end, len(text)))
        return (start, end)


class RegexSelectionStrategy(TextSelectionStrategy):
    """
    Selects text based on the first regex match.
    """

    def __init__(self, *, pattern: str | Pattern[str]) -> None:
        """
        Initialize the regex selection strategy.

        Args:
            pattern (str | Pattern[str]): The regex pattern to match.
        """
        self._pattern = re.compile(pattern) if isinstance(pattern, str) else pattern

    def select_range(self, *, text: str) -> tuple[int, int]:
        """
        Select the range of the first regex match.

        Args:
            text (str): The input text to select from.

        Returns:
            tuple[int, int]: A tuple of (start_index, end_index) of the first match,
                or (0, 0) if no match found.
        """
        match = self._pattern.search(text)
        if match:
            return (match.start(), match.end())
        return (0, 0)


class KeywordSelectionStrategy(TextSelectionStrategy):
    """
    Selects text around a keyword with optional context.
    """

    def __init__(
        self,
        *,
        keyword: str,
        context_before: int = 0,
        context_after: int = 0,
        case_sensitive: bool = True,
    ) -> None:
        """
        Initialize the keyword selection strategy.

        Args:
            keyword (str): The keyword to search for.
            context_before (int): Number of characters to include before the keyword. Defaults to 0.
            context_after (int): Number of characters to include after the keyword. Defaults to 0.
            case_sensitive (bool): Whether the keyword search is case-sensitive. Defaults to True.
        """
        self._keyword = keyword
        self._context_before = context_before
        self._context_after = context_after
        self._case_sensitive = case_sensitive

    def select_range(self, *, text: str) -> tuple[int, int]:
        """
        Select the range around the first occurrence of the keyword.

        Args:
            text (str): The input text to select from.

        Returns:
            tuple[int, int]: A tuple of (start_index, end_index) including context,
                or (0, 0) if keyword not found.
        """
        search_text = text if self._case_sensitive else text.lower()
        search_keyword = self._keyword if self._case_sensitive else self._keyword.lower()

        index = search_text.find(search_keyword)
        if index == -1:
            return (0, 0)

        start = max(0, index - self._context_before)
        end = min(len(text), index + len(self._keyword) + self._context_after)
        return (start, end)


class PositionSelectionStrategy(TextSelectionStrategy):
    """
    Selects text based on proportional start and end positions.
    """

    def __init__(self, *, start_proportion: float, end_proportion: float) -> None:
        """
        Initialize the position selection strategy.

        Args:
            start_proportion (float): The starting position as a proportion (0.0 to 1.0).
            end_proportion (float): The ending position as a proportion (0.0 to 1.0).

        Raises:
            ValueError: If proportions are not between 0.0 and 1.0, or start >= end.
        """
        if not 0.0 <= start_proportion <= 1.0:
            raise ValueError(f"start_proportion must be between 0.0 and 1.0, got {start_proportion}")
        if not 0.0 <= end_proportion <= 1.0:
            raise ValueError(f"end_proportion must be between 0.0 and 1.0, got {end_proportion}")
        if start_proportion >= end_proportion:
            raise ValueError(
                f"start_proportion ({start_proportion}) must be less than end_proportion ({end_proportion})"
            )

        self._start_proportion = start_proportion
        self._end_proportion = end_proportion

    def select_range(self, *, text: str) -> tuple[int, int]:
        """
        Select a range based on the relative position in the text.

        Args:
            text (str): The input text to select from.

        Returns:
            tuple[int, int]: A tuple of (start_index, end_index).
        """
        text_len = len(text)
        start = int(text_len * self._start_proportion)
        end = int(text_len * self._end_proportion)
        return (start, end)


class ProportionSelectionStrategy(TextSelectionStrategy):
    """
    Selects a proportion of text anchored to a specific position (start, end, middle, or random).
    """

    def __init__(self, *, proportion: float, anchor: str = "start", seed: int | None = None) -> None:
        """
        Initialize the proportion selection strategy.

        Args:
            proportion (float): The proportion of text to select (0.0 to 1.0).
            anchor (str): Where to anchor the selection. Valid values:
                - 'start': Select from the beginning
                - 'end': Select from the end
                - 'middle': Select from the middle
                - 'random': Select from a random position
            seed (int | None): Random seed for reproducible random selections. Defaults to None.
                Scoped to this strategy: it makes this strategy reproducible without affecting
                the randomness of any other component.

        Raises:
            ValueError: If proportion is not between 0.0 and 1.0, or anchor is invalid.
        """
        if not 0.0 <= proportion <= 1.0:
            raise ValueError(f"Proportion must be between 0.0 and 1.0, got {proportion}")

        valid_anchors = {"start", "end", "middle", "random"}
        if anchor not in valid_anchors:
            raise ValueError(f"Invalid anchor '{anchor}'. Valid anchors are: {', '.join(valid_anchors)}")

        self._proportion = proportion
        self._anchor = anchor
        self._seed = seed

    def select_range(self, *, text: str) -> tuple[int, int]:
        """
        Select a proportion of text based on the anchor position.

        Args:
            text (str): The input text to select from.

        Returns:
            tuple[int, int]: A tuple of (start_index, end_index).
        """
        text_len = len(text)
        selection_len = int(text_len * self._proportion)

        if self._anchor == "start":
            return (0, selection_len)
        if self._anchor == "end":
            return (text_len - selection_len, text_len)
        if self._anchor == "middle":
            start = (text_len - selection_len) // 2
            return (start, start + selection_len)
        # random
        rng = get_random_generator(
            namespace=f"{type(self).__module__}.{type(self).__qualname__}",
            stream="text-range",
            seed=self._seed,
            owner=self,
        )
        max_start = max(0, text_len - selection_len)
        start = rng.randint(0, max_start) if max_start > 0 else 0
        return (start, start + selection_len)


class RangeSelectionStrategy(TextSelectionStrategy):
    """
    Selects text based on proportional start and end positions.
    """

    def __init__(self, *, start_proportion: float = 0.0, end_proportion: float = 1.0) -> None:
        """
        Initialize the range selection strategy.

        Args:
            start_proportion (float): The starting position as a proportion (0.0 to 1.0). Defaults to 0.0.
            end_proportion (float): The ending position as a proportion (0.0 to 1.0). Defaults to 1.0.

        Raises:
            ValueError: If proportions are not between 0.0 and 1.0, or start >= end.
        """
        if not 0.0 <= start_proportion <= 1.0:
            raise ValueError(f"start_proportion must be between 0.0 and 1.0, got {start_proportion}")
        if not 0.0 <= end_proportion <= 1.0:
            raise ValueError(f"end_proportion must be between 0.0 and 1.0, got {end_proportion}")
        if start_proportion >= end_proportion:
            raise ValueError(
                f"start_proportion ({start_proportion}) must be less than end_proportion ({end_proportion})"
            )

        self._start_proportion = start_proportion
        self._end_proportion = end_proportion

    def select_range(self, *, text: str) -> tuple[int, int]:
        """
        Select a range based on proportional positions.

        Args:
            text (str): The input text to select from.

        Returns:
            tuple[int, int]: A tuple of (start_index, end_index).
        """
        text_len = len(text)
        start = int(text_len * self._start_proportion)
        end = int(text_len * self._end_proportion)
        return (start, end)


# ============================================================================
# Word-Level Selection Strategies
# ============================================================================


class WordIndexSelectionStrategy(WordSelectionStrategy):
    """
    Selects words based on their indices in the word list.
    """

    def __init__(self, *, indices: list[int]) -> None:
        """
        Initialize the word index selection strategy.

        Args:
            indices (list[int]): The list of word indices to select.
        """
        self._indices = indices

    def get_identifier_params(self) -> dict[str, Any]:
        """
        Return the selected indices in sorted order.

        Returns:
            dict[str, Any]: The sorted index list.
        """
        return {"indices": sorted(self._indices)}

    def select_words(self, *, words: list[str]) -> list[int]:
        """
        Select words at the specified indices.

        Args:
            words (list[str]): The list of words to select from.

        Returns:
            list[int]: The list of valid indices.

        Raises:
            ValueError: If any indices are out of range.
        """
        if not words:
            return []

        valid_indices = [i for i in self._indices if 0 <= i < len(words)]
        invalid_indices = [i for i in self._indices if i < 0 or i >= len(words)]

        if invalid_indices:
            raise ValueError(f"Invalid word indices {invalid_indices} provided. Valid range is 0 to {len(words) - 1}.")

        return valid_indices


class WordKeywordSelectionStrategy(WordSelectionStrategy):
    """
    Selects words that match specific keywords.
    """

    def __init__(self, *, keywords: list[str], case_sensitive: bool = True) -> None:
        """
        Initialize the word keyword selection strategy.

        Args:
            keywords (list[str]): The list of keywords to match.
            case_sensitive (bool): Whether matching is case-sensitive. Defaults to True.
        """
        self._keywords = keywords
        self._case_sensitive = case_sensitive

    def get_identifier_params(self) -> dict[str, Any]:
        """
        Return the keyword list and case-sensitivity flag.

        Returns:
            dict[str, Any]: Sorted keywords and the case-sensitivity flag.
        """
        return {
            "keywords": sorted(self._keywords),
            "case_sensitive": self._case_sensitive,
        }

    def select_words(self, *, words: list[str]) -> list[int]:
        """
        Select words that match the keywords.

        Args:
            words (list[str]): The list of words to select from.

        Returns:
            list[int]: The list of indices where keywords were found.
        """
        if not words:
            return []

        if self._case_sensitive:
            return [i for i, word in enumerate(words) if word in self._keywords]
        keywords_lower = [k.lower() for k in self._keywords]
        return [i for i, word in enumerate(words) if word.lower() in keywords_lower]


class WordProportionSelectionStrategy(WordSelectionStrategy):
    """
    Selects a random proportion of words.
    """

    def __init__(self, *, proportion: float, seed: int | None = None) -> None:
        """
        Initialize the word proportion selection strategy.

        Args:
            proportion (float): The proportion of words to select (0.0 to 1.0).
            seed (int | None): Random seed for reproducible selections. Defaults to None.
                Scoped to this strategy: it makes this strategy reproducible without affecting
                the randomness of any other component.

        Raises:
            ValueError: If proportion is not between 0.0 and 1.0.
        """
        if not 0.0 <= proportion <= 1.0:
            raise ValueError(f"Proportion must be between 0.0 and 1.0, got {proportion}")

        self._proportion = proportion
        self._seed = seed

    def get_identifier_params(self) -> dict[str, Any]:
        """
        Return the proportion and optional seed.

        Returns:
            dict[str, Any]: Proportion and seed when one was provided.
        """
        params: dict[str, Any] = {"proportion": self._proportion}
        if self._seed is not None:
            params["seed"] = self._seed
        return params

    def select_words(self, *, words: list[str]) -> list[int]:
        """
        Select a random proportion of words.

        Args:
            words (list[str]): The list of words to select from.

        Returns:
            list[int]: The list of randomly selected indices.
        """
        if not words:
            return []

        num_to_select = int(len(words) * self._proportion)
        rng = get_random_generator(
            namespace=f"{type(self).__module__}.{type(self).__qualname__}",
            stream="word-selection",
            seed=self._seed,
            owner=self,
        )
        return rng.sample(range(len(words)), num_to_select) if num_to_select > 0 else []


class WordRegexSelectionStrategy(WordSelectionStrategy):
    """
    Selects words that match a regex pattern.
    """

    def __init__(self, *, pattern: str | Pattern[str]) -> None:
        """
        Initialize the word regex selection strategy.

        Args:
            pattern (str | Pattern[str]): The regex pattern to match against words.
        """
        self._pattern = re.compile(pattern) if isinstance(pattern, str) else pattern

    def get_identifier_params(self) -> dict[str, Any]:
        """
        Return the regex pattern and flags.

        Returns:
            dict[str, Any]: Pattern string and compiled flags.
        """
        return {"pattern": self._pattern.pattern, "flags": self._pattern.flags}

    def select_words(self, *, words: list[str]) -> list[int]:
        """
        Select words that match the regex pattern.

        Args:
            words (list[str]): The list of words to select from.

        Returns:
            list[int]: The list of indices where words matched the pattern.
        """
        if not words:
            return []

        return [i for i, word in enumerate(words) if self._pattern.search(word)]


class WordPositionSelectionStrategy(WordSelectionStrategy):
    """
    Selects words based on proportional start and end positions.
    """

    def __init__(self, *, start_proportion: float, end_proportion: float) -> None:
        """
        Initialize the word position selection strategy.

        Args:
            start_proportion (float): The starting position as a proportion (0.0 to 1.0).
            end_proportion (float): The ending position as a proportion (0.0 to 1.0).

        Raises:
            ValueError: If proportions are not between 0.0 and 1.0, or start >= end.
        """
        if not 0.0 <= start_proportion <= 1.0:
            raise ValueError(f"start_proportion must be between 0.0 and 1.0, got {start_proportion}")
        if not 0.0 <= end_proportion <= 1.0:
            raise ValueError(f"end_proportion must be between 0.0 and 1.0, got {end_proportion}")
        if start_proportion >= end_proportion:
            raise ValueError(
                f"start_proportion ({start_proportion}) must be less than end_proportion ({end_proportion})"
            )

        self._start_proportion = start_proportion
        self._end_proportion = end_proportion

    def get_identifier_params(self) -> dict[str, Any]:
        """
        Return the start and end proportions.

        Returns:
            dict[str, Any]: Start and end proportions.
        """
        return {
            "start_proportion": self._start_proportion,
            "end_proportion": self._end_proportion,
        }

    def select_words(self, *, words: list[str]) -> list[int]:
        """
        Select words based on the relative position.

        Args:
            words (list[str]): The list of words to select from.

        Returns:
            list[int]: The list of indices in the specified position range.
        """
        if not words:
            return []

        num_words = len(words)
        start_idx = int(num_words * self._start_proportion)
        end_idx = int(num_words * self._end_proportion)

        return list(range(start_idx, end_idx))


class AllWordsSelectionStrategy(WordSelectionStrategy):
    """
    Selects all words (default strategy).
    """

    def select_words(self, *, words: list[str]) -> list[int]:
        """
        Select all words.

        Args:
            words (list[str]): The list of words to select from.

        Returns:
            list[int]: All word indices.
        """
        return list(range(len(words)))


class ContentWordSelectionStrategy(WordSelectionStrategy):
    """
    Selects content words with a deterministic, dependency-free heuristic.

    A token is treated as a content word when, after stripping punctuation, it
    contains letters, meets ``min_word_length``, and is not in the stopword
    list. Selection is left-to-right. This approximates POS-based noun/verb
    masking used by Simple Assistive Task Linkage (SATA) without downloading
    an NLTK tagger.
    """

    def __init__(
        self,
        *,
        max_words: int = 2,
        skip_first: int = 1,
        min_word_length: int = 3,
        stopwords: Collection[str] | None = None,
        candidate_words: Collection[str] | None = None,
    ) -> None:
        """
        Initialize the content-word selection strategy.

        Args:
            max_words (int): Maximum number of content words to select. Defaults to 2.
            skip_first (int): Number of leading content words to leave unmasked.
                Defaults to 1, approximating SATA's skip-first-verb/noun behavior.
            min_word_length (int): Minimum alphabetic length after punctuation is
                stripped. Defaults to 3.
            stopwords (Collection[str] | None): Function words to ignore. Defaults
                to ``DEFAULT_CONTENT_STOPWORDS``.
            candidate_words (Collection[str] | None): Optional allowlist. When set,
                only these words (case-insensitive, punctuation-stripped) are
                eligible. Defaults to None (all content words).

        Raises:
            ValueError: If ``max_words`` is less than 1, or ``skip_first`` or
                ``min_word_length`` is negative.
        """
        if max_words < 1:
            raise ValueError(f"max_words must be >= 1, got {max_words}")
        if skip_first < 0:
            raise ValueError(f"skip_first must be >= 0, got {skip_first}")
        if min_word_length < 0:
            raise ValueError(f"min_word_length must be >= 0, got {min_word_length}")

        self._max_words = max_words
        self._skip_first = skip_first
        self._min_word_length = min_word_length
        self._stopwords = (
            frozenset(word.lower() for word in stopwords) if stopwords is not None else DEFAULT_CONTENT_STOPWORDS
        )
        self._candidate_words = (
            frozenset(self._normalize_word(word) for word in candidate_words) if candidate_words is not None else None
        )

    def get_identifier_params(self) -> dict[str, Any]:
        """
        Return the content-word selection configuration.

        Stopwords and candidate words are stored as sorted lists so order does
        not affect identifier equality.

        Returns:
            dict[str, Any]: Selection limits, stopwords, and optional candidates.
        """
        params: dict[str, Any] = {
            "max_words": self._max_words,
            "skip_first": self._skip_first,
            "min_word_length": self._min_word_length,
            "stopwords": sorted(self._stopwords),
        }
        if self._candidate_words is not None:
            params["candidate_words"] = sorted(self._candidate_words)
        return params

    @staticmethod
    def _normalize_word(word: str) -> str:
        """
        Strip punctuation and lowercase a token for classification.

        Args:
            word (str): The raw token.

        Returns:
            str: The normalized token.
        """
        return word.strip(string.punctuation).lower()

    def _is_content_word(self, word: str) -> bool:
        """
        Return whether a token is an eligible content word.

        Args:
            word (str): The raw token.

        Returns:
            bool: True if the token should be considered for selection.
        """
        normalized = self._normalize_word(word)
        if not normalized or not any(char.isalpha() for char in normalized):
            return False
        if len(normalized) < self._min_word_length:
            return False
        if normalized in self._stopwords:
            return False
        return self._candidate_words is None or normalized in self._candidate_words

    def select_words(self, *, words: list[str]) -> list[int]:
        """
        Select up to ``max_words`` content-word indices, skipping the first few.

        Args:
            words (list[str]): The list of words to select from.

        Returns:
            list[int]: Indices of selected content words, in left-to-right order.
        """
        if not words:
            return []

        content_indices = [index for index, word in enumerate(words) if self._is_content_word(word)]
        return content_indices[self._skip_first : self._skip_first + self._max_words]
