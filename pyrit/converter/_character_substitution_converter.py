# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter.text_selection_strategy import WordSelectionStrategy
from pyrit.converter.word_level_converter import WordLevelConverter


class _CharacterSubstitutionConverter(WordLevelConverter):
    """
    Shared base for converters that replace each character with fixed replacement(s).

    A substitution table maps a (case-folded) character to a non-empty list of replacement strings.
    In deterministic mode the first replacement is always used; otherwise one is chosen at random.
    Characters absent from the table are left unchanged. Word selection is inherited from
    ``WordLevelConverter``.

    Members of this family are ``LeetspeakConverter`` (look-alike leet glyphs) and
    ``PigpenConverter`` (Pigpen cipher glyphs). This base is intentionally *not* for whole-string
    transforms (e.g. mirror/upside-down, which also reverse the text).
    """

    def __init__(
        self,
        *,
        substitutions: dict[str, list[str]],
        deterministic: bool = True,
        word_selection_strategy: WordSelectionStrategy | None = None,
    ) -> None:
        """
        Initialize the converter.

        Args:
            substitutions (dict[str, list[str]]): Map of single characters to a non-empty list of
                replacement strings. Assumed already validated/normalized by the subclass.
            deterministic (bool): If True, always use the first replacement for each character;
                if False, choose one at random.
            word_selection_strategy (WordSelectionStrategy | None): Strategy for selecting which words
                to convert. If None, all words are converted.
        """
        super().__init__(word_selection_strategy=word_selection_strategy)
        self._substitutions = substitutions
        self._deterministic = deterministic

    @staticmethod
    def _validate_substitutions(
        substitutions: dict[str, list[str]], *, param_name: str = "substitutions"
    ) -> dict[str, list[str]]:
        """
        Validate a substitution table and key it by lowercase character.

        ``convert_word_async`` looks characters up in lowercase, so an uppercase or multi-character
        key would never match, and an empty replacement list would fail on the first matching character.

        Args:
            substitutions (dict[str, list[str]]): The substitution table to validate.
            param_name (str): Name used in error messages (e.g. ``"custom_substitutions"``).

        Returns:
            dict[str, list[str]]: The substitutions keyed by lowercase character.

        Raises:
            ValueError: If a key is not a single character, two keys differ only in case, or a value
                is not a non-empty list of strings.
        """
        normalized: dict[str, list[str]] = {}
        for char, replacements in substitutions.items():
            if not isinstance(char, str) or len(char) != 1:
                raise ValueError(f"{param_name} keys must be single characters, got {char!r}.")
            if (
                not isinstance(replacements, list)
                or not replacements
                or not all(isinstance(replacement, str) for replacement in replacements)
            ):
                raise ValueError(f"{param_name}[{char!r}] must be a non-empty list of strings, got {replacements!r}.")
            lower_char = char.lower()
            if lower_char in normalized:
                raise ValueError(f"{param_name} has more than one key for {lower_char!r}.")
            normalized[lower_char] = list(replacements)
        return normalized

    def _substitute_word(self, word: str) -> str:
        """
        Substitute each character in a word per the table.

        Concrete subclasses call this from ``convert_word_async``. This class stays abstract (it does
        not implement ``convert_word_async``) so only the named members (Leetspeak, Pigpen) are
        discoverable converters.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        converted_word = []
        rng = self._get_random_generator(stream="character-substitutions")
        for char in word:
            lower_char = char.lower()
            if lower_char in self._substitutions:
                if self._deterministic:
                    converted_word.append(self._substitutions[lower_char][0])
                else:
                    converted_word.append(rng.choice(self._substitutions[lower_char]))
            else:
                converted_word.append(char)
        return "".join(converted_word)
