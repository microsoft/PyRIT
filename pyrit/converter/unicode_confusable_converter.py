# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import re
from typing import TYPE_CHECKING, Literal, assert_never

from confusable_homoglyphs.confusables import is_confusable
from confusables import confusable_characters

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

logger = logging.getLogger(__name__)


class UnicodeConfusableConverter(Converter):
    """
    Applies substitutions to words in the prompt to test adversarial textual robustness
    by replacing characters with visually similar ones.

    By default every character that has a look-alike is replaced. With a ``budget``, only that many
    characters are replaced, at randomly chosen positions, matching the homoglyph perturbation from
    Boucher et al., "Bad Characters: Imperceptible NLP Attacks" (arXiv:2106.09898).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    # Latin a, c, e, o, p, x, y (both cases) -> their Cyrillic look-alikes.
    _CYRILLIC_HOMOGLYPHS = str.maketrans(
        "aceopxyACEOPXY",
        "\u0430\u0441\u0435\u043e\u0440\u0445\u0443\u0410\u0421\u0415\u041e\u0420\u0425\u0423",
    )

    def __init__(
        self,
        *,
        source_package: Literal["confusable_homoglyphs", "confusables", "cyrillic_homoglyph"] = "confusable_homoglyphs",
        deterministic: bool = False,
        budget: int | None = None,
    ) -> None:
        """
        Initialize the converter with the specified source package for homoglyph generation.

        Args:
            source_package (Literal["confusable_homoglyphs", "confusables", "cyrillic_homoglyph"]):
                The package to use for homoglyph generation.

                Can be one of:
                    - "confusable_homoglyphs" (https://pypi.org/project/confusable-homoglyphs/):
                        Used by default as it is more regularly maintained and up to date with the latest
                        Unicode-provided confusables found here:
                        https://www.unicode.org/Public/security/latest/confusables.txt
                    - "confusables" (https://pypi.org/project/confusables/):
                        Provides additional methods of matching characters (not just Unicode list),
                        so each character has more possible substitutions.
                    - "cyrillic_homoglyph":
                        Replaces Latin a, c, e, o, p, x, y (both cases) with their Cyrillic look-alikes
                        using a fixed table, so the output is always the same.
            deterministic (bool): This argument is for unittesting only.
            budget (int | None): How many characters to replace. Each goes at a distinct position that has a
                look-alike different from the original, chosen at random. If fewer positions qualify, all of
                them are replaced. Defaults to None, which replaces every character that has a look-alike.

        Raises:
            ValueError: If an invalid source package is provided, or ``budget`` is not a positive integer.
        """
        if source_package not in ["confusable_homoglyphs", "confusables", "cyrillic_homoglyph"]:
            raise ValueError(
                f"Invalid source package: {source_package}. Please choose 'confusable_homoglyphs', "
                "'confusables', or 'cyrillic_homoglyph'"
            )
        if budget is not None and (isinstance(budget, bool) or budget < 1):
            raise ValueError(f"budget must be a positive integer or None, got {budget!r}")
        self._source_package: Literal["confusable_homoglyphs", "confusables", "cyrillic_homoglyph"] = source_package
        self._deterministic = deterministic
        self._budget = budget

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with unicode confusable parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "source_package": self._source_package,
                "deterministic": self._deterministic,
                "budget": self._budget,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by applying confusable substitutions. This leads to a prompt that looks similar,
        but is actually different (e.g., replacing a Latin 'a' with a Cyrillic 'а').

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the prompt with confusable substitutions applied.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        if self._budget is not None:
            converted_prompt = self._substitute_with_budget(prompt=prompt, budget=self._budget)
        elif self._source_package == "cyrillic_homoglyph":
            converted_prompt = prompt.translate(self._CYRILLIC_HOMOGLYPHS)
        elif self._source_package == "confusable_homoglyphs":
            converted_prompt = self._generate_perturbed_prompts(prompt)
        else:
            if TYPE_CHECKING and self._source_package != "confusables":  # pragma: no cover
                assert_never(self._source_package)
            converted_prompt = "".join(self._confusable(c) for c in prompt)

        return ConverterResult(output_text=converted_prompt, output_type="text")

    def _get_homoglyph_variants(self, word: str) -> list[str]:
        """
        Retrieve homoglyph variants for a given word using the "confusable_homoglyphs" package.

        Args:
            word (str): The word to find homoglyphs for.

        Returns:
            list: A list of homoglyph variants for the word.
        """
        try:
            # Check for confusable homoglyphs in the word
            confusables = is_confusable(word, greedy=True)
            if confusables:
                # Return a list of all homoglyph variants instead of only the first one
                return [homoglyph["c"] for item in confusables for homoglyph in item["homoglyphs"]]
        except UnicodeDecodeError:
            logger.error(f"Cannot process word '{word}' due to UnicodeDecodeError. Returning empty list.")
            return []

        # Default return if no homoglyphs are found
        return []

    def _generate_perturbed_prompts(self, prompt: str) -> str:
        """
        Generate a perturbed prompt by substituting characters with their homoglyph variants using the
        "confusable_homoglyphs" package.

        Args:
            prompt (str): The original prompt.

        Returns:
            str: A perturbed prompt with character-level substitutions.
        """
        perturbed_words = []
        rng = self._get_random_generator(stream="homoglyphs")

        # Split the prompt into words and non-word tokens
        word_list = re.findall(r"\w+|\W+", prompt)

        for word in word_list:
            perturbed_chars = []
            for char in word:
                homoglyph_variants = self._get_homoglyph_variants(char)
                if homoglyph_variants:
                    # Randomly choose a homoglyph variant
                    variant = rng.choice(homoglyph_variants) if not self._deterministic else homoglyph_variants[-1]
                    logger.debug(f"Replacing character '{char}' with '{variant}'")
                    perturbed_chars.append(variant)
                else:
                    perturbed_chars.append(char)
            perturbed_words.append("".join(perturbed_chars))

        # Join the perturbed words back into a string
        new_prompt = "".join(perturbed_words)
        logger.info(f"Final perturbed prompt: {new_prompt}")

        return new_prompt

    def _confusable(self, char: str) -> str:
        """
        Pick a confusable character for the given character using the "confusables" package.

        Args:
            char (str): The character to be replaced.

        Returns:
            str: The confusable character to replace the given character.
        """
        confusable_options = confusable_characters(char)
        if not confusable_options or char == " ":
            return char
        if self._deterministic or len(confusable_options) == 1:
            return str(confusable_options[-1])
        return str(self._get_random_generator(stream="confusables").choice(confusable_options))

    def _substitute_with_budget(self, *, prompt: str, budget: int) -> str:
        """
        Replace up to ``budget`` randomly chosen characters with a look-alike from the selected source package.

        Args:
            prompt (str): The original prompt.
            budget (int): The most characters to replace.

        Returns:
            str: The prompt with at most ``budget`` characters replaced.
        """
        candidates = {index: options for index, char in enumerate(prompt) if (options := self._substitutes(char))}
        rng = self._get_random_generator(stream="budgeted-substitutions")
        positions = rng.sample(sorted(candidates), min(budget, len(candidates)))

        characters = list(prompt)
        for index in sorted(positions):
            options = candidates[index]
            characters[index] = options[-1] if self._deterministic else rng.choice(options)
        return "".join(characters)

    def _substitutes(self, char: str) -> list[str]:
        """
        List the look-alikes for a character that differ from it, using the selected source package.

        Args:
            char (str): The character to find look-alikes for.

        Returns:
            list[str]: The look-alikes, or an empty list if the character has none.
        """
        if self._source_package == "cyrillic_homoglyph":
            options = [char.translate(self._CYRILLIC_HOMOGLYPHS)]
        elif self._source_package == "confusable_homoglyphs":
            options = self._get_homoglyph_variants(char)
        else:
            if TYPE_CHECKING and self._source_package != "confusables":  # pragma: no cover
                assert_never(self._source_package)
            options = [] if char == " " else [str(option) for option in confusable_characters(char) or []]
        return [option for option in dict.fromkeys(options) if option != char]
