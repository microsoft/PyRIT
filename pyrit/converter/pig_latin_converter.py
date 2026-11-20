# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

from pyrit.converter.text_selection_strategy import (
    AllWordsSelectionStrategy,
    WordSelectionStrategy,
)
from pyrit.converter.word_level_converter import WordLevelConverter
from pyrit.models import ComponentIdentifier

# Split a whitespace-delimited token into leading non-letters, an alphabetic core
# (from the first letter to the last letter, interior punctuation included), and
# trailing non-letters. Leading/trailing punctuation is preserved verbatim so that
# marks like ``"`` ``(`` ``)`` ``.`` ``,`` stay where they were.
_TOKEN_PATTERN = re.compile(r"^([^A-Za-z]*)(.*?)([^A-Za-z]*)$")

_VOWELS = frozenset("aeiou")
_VALID_VOWEL_SUFFIXES = ("way", "yay", "ay")


class PigLatinConverter(WordLevelConverter):
    """
    Converts text into Pig Latin, the English schoolyard language game.

    Pig Latin is a folk/word game with NO formal academic source; the ruleset below follows the
    conventional description (e.g. the Wikipedia "Pig Latin" article). Because usage varies between
    speakers, the exact handling of edge cases (the vowel-initial suffix, the letter ``y``, words
    with no vowels) is a documented choice rather than a canonical standard.

    Ruleset applied to each whitespace-delimited token:

    1. Leading and trailing non-letter characters (punctuation, digits, brackets, quotes) are split
       off and re-attached unchanged. Only the alphabetic core is transformed.
    2. Consonant-initial words: the leading consonant cluster is moved to the end and ``"ay"`` is
       appended. Example: ``"hello"`` -> ``"ellohay"``, ``"string"`` -> ``"ingstray"``.
    3. Vowel-initial words (first letter in ``a e i o u``): a suffix is appended. The default suffix
       is ``"way"`` (``"apple"`` -> ``"appleway"``); ``"yay"`` and ``"ay"`` are also selectable.
    4. The letter ``y`` is treated as a consonant when it is the first letter (so it can lead a
       cluster: ``"yellow"`` -> ``"ellowyay"``) but as a vowel elsewhere, which lets it terminate a
       consonant cluster (``"my"`` -> ``"ymay"``, ``"rhythm"`` -> ``"ythmrhay"``).
    5. Words with no vowel at all keep their whole core as the "cluster" (``"nth"`` -> ``"nthay"``).
    6. Capitalization is reapplied to the result: an all-uppercase core (more than one letter)
       yields an uppercase result (``"HELLO"`` -> ``"ELLOHAY"``); a title-cased core capitalizes the
       first letter of the result (``"Hello"`` -> ``"Ellohay"``); otherwise the result is lowercase.

    The transform is intentionally lossy/ambiguous (several inputs can map to the same output), so it
    is registered as an ALL-only encoding technique in the garak Encoding scenario.
    """

    def __init__(
        self,
        *,
        vowel_initial_suffix: str = "way",
        word_selection_strategy: WordSelectionStrategy | None = None,
    ) -> None:
        """
        Initialize the Pig Latin converter.

        Args:
            vowel_initial_suffix (str): Suffix appended to vowel-initial words. One of ``"way"``
                (default), ``"yay"``, or ``"ay"``.
            word_selection_strategy (WordSelectionStrategy | None): Strategy for selecting which words
                to convert. If None, all words will be converted.

        Raises:
            ValueError: If ``vowel_initial_suffix`` is not one of the supported values.
        """
        super().__init__(word_selection_strategy=word_selection_strategy)

        if vowel_initial_suffix not in _VALID_VOWEL_SUFFIXES:
            raise ValueError(
                f"vowel_initial_suffix must be one of {_VALID_VOWEL_SUFFIXES}, got {vowel_initial_suffix!r}."
            )
        self._vowel_initial_suffix = vowel_initial_suffix

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Pig Latin parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        selection_params = (
            {}
            if type(self._word_selection_strategy) is AllWordsSelectionStrategy
            else super()._build_identifier().params
        )
        return self._create_identifier(
            params={
                **selection_params,
                "vowel_initial_suffix": self._vowel_initial_suffix,
            },
        )

    def _pig_latin_core(self, core_lower: str) -> str:
        """
        Apply the Pig Latin transform to the lowercased alphabetic core of a single word.

        Args:
            core_lower (str): The word core, already lowercased, beginning with a letter.

        Returns:
            str: The transformed (still lowercase) core.
        """
        if core_lower[0] in _VOWELS:
            return core_lower + self._vowel_initial_suffix

        # Consonant-initial: find where the leading consonant cluster ends. The first letter is a
        # consonant, so scan from index 1; ``y`` counts as a vowel here (it terminates the cluster).
        split_index = 1
        while split_index < len(core_lower):
            char = core_lower[split_index]
            if char in _VOWELS or char == "y":
                break
            split_index += 1

        cluster = core_lower[:split_index]
        rest = core_lower[split_index:]
        return rest + cluster + "ay"

    @staticmethod
    def _apply_case(*, core: str, transformed: str) -> str:
        """
        Reapply the original word's capitalization to the (lowercase) transformed word.

        Args:
            core (str): The original, un-lowercased word core.
            transformed (str): The transformed word, in lowercase.

        Returns:
            str: The transformed word with capitalization restored.
        """
        letters = [char for char in core if char.isalpha()]
        if len(letters) > 1 and all(char.isupper() for char in letters):
            return transformed.upper()
        if letters and letters[0].isupper():
            return transformed[:1].upper() + transformed[1:]
        return transformed

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single whitespace-delimited token into Pig Latin.

        Args:
            word (str): The token to convert (may carry leading/trailing punctuation).

        Returns:
            str: The Pig Latin token with punctuation and capitalization preserved.
        """
        match = _TOKEN_PATTERN.match(word)
        # The pattern matches any string, so ``match`` is never None; the guard satisfies type checkers.
        if match is None:  # pragma: no cover
            return word
        # ``re.Match.group`` is typed ``str | Any``; wrap in ``str`` so the concatenated return
        # below is definitively ``str`` (the groups always participate, so this never changes a value).
        prefix, core, suffix = str(match.group(1)), str(match.group(2)), str(match.group(3))
        if not core:
            # Token has no letters (e.g. pure punctuation or digits); leave it unchanged.
            return word
        transformed = self._pig_latin_core(core.lower())
        transformed = self._apply_case(core=core, transformed=transformed)
        return prefix + transformed + suffix
