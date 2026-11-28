# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._character_substitution_converter import _CharacterSubstitutionConverter
from pyrit.converter.text_selection_strategy import AllWordsSelectionStrategy, WordSelectionStrategy
from pyrit.models import ComponentIdentifier


class LeetspeakConverter(_CharacterSubstitutionConverter):
    """
    Converts a string to a leetspeak version.

    Leetspeak is the look-alike member of the character-substitution family
    (see ``_CharacterSubstitutionConverter``): by default it uses the standard leet table, and a
    ``custom_substitutions`` map can override it.
    """

    _DEFAULT_SUBSTITUTIONS = {
        "a": ["4", "@", "/\\", "@", "^", "/-\\"],
        "b": ["8", "6", "13", "|3", "/3", "!3"],
        "c": ["(", "[", "<", "{"],
        "e": ["3"],
        "g": ["9"],
        "i": ["1", "!"],
        "l": ["1", "|"],
        "o": ["0"],
        "s": ["5", "$"],
        "t": ["7"],
        "z": ["2"],
    }

    def __init__(
        self,
        *,
        deterministic: bool = True,
        custom_substitutions: dict[str, list[str]] | None = None,
        word_selection_strategy: WordSelectionStrategy | None = None,
    ) -> None:
        """
        Initialize the converter with optional deterministic mode and custom substitutions.

        Args:
            deterministic (bool): If True, use the first substitution for each character.
                If False, randomly choose a substitution for each character.
            custom_substitutions (dict | None): A dictionary of custom substitutions to override the defaults.
                Each key is a single character, matched case-insensitively, and maps to a non-empty
                list of replacement strings.
            word_selection_strategy (WordSelectionStrategy | None): Strategy for selecting which words to convert.
                If None, all words will be converted.

        Raises:
            ValueError: If ``custom_substitutions`` has a key that is not a single character, two keys
                that differ only in case, or a value that is not a non-empty list of strings.
        """
        if custom_substitutions is not None:
            custom_substitutions = self._validate_substitutions(custom_substitutions, param_name="custom_substitutions")

        # Use custom substitutions if provided, otherwise default to the standard ones
        substitutions = custom_substitutions if custom_substitutions else self._DEFAULT_SUBSTITUTIONS
        super().__init__(
            substitutions=substitutions,
            deterministic=deterministic,
            word_selection_strategy=word_selection_strategy,
        )
        self._has_custom_substitutions = custom_substitutions is not None

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with leetspeak parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        import hashlib
        import json

        # Hash custom substitutions if provided
        substitutions_hash = None
        if self._has_custom_substitutions:
            substitutions_str = json.dumps(self._substitutions, sort_keys=True)
            substitutions_hash = hashlib.sha256(substitutions_str.encode("utf-8")).hexdigest()[:16]

        selection_params = (
            {}
            if type(self._word_selection_strategy) is AllWordsSelectionStrategy
            else super()._build_identifier().params
        )
        return self._create_identifier(
            params={
                **selection_params,
                "deterministic": self._deterministic,
                "custom_substitutions_hash": substitutions_hash,
            },
        )

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into leetspeak.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        return self._substitute_word(word)
