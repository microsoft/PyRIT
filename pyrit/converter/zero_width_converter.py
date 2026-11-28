# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Sequence

from pyrit.common.text_helper import graphemes
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class ZeroWidthConverter(Converter):
    """
    Injects zero-width characters into the provided text to bypass content safety mechanisms.

    By default a zero-width space goes between every pair of characters. With a ``budget``, only that many
    zero-width characters are inserted, at randomly chosen positions, matching the invisible-character
    perturbation from Boucher et al., "Bad Characters: Imperceptible NLP Attacks" (arXiv:2106.09898).

    Characters are grapheme clusters, so combining marks, Indic vowel signs and emoji sequences stay whole.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    #: Constant for zero-width space character.
    ZERO_WIDTH_SPACE = "\u200b"
    #: Constant for zero-width non-joiner character.
    ZERO_WIDTH_NON_JOINER = chr(0x200C)
    #: Constant for zero-width joiner character.
    ZERO_WIDTH_JOINER = chr(0x200D)

    def __init__(
        self,
        *,
        budget: int | None = None,
        characters: Sequence[str] | None = None,
        seed: int | None = None,
    ) -> None:
        """
        Initialize the converter.

        Args:
            budget (int | None): How many zero-width characters to insert. Each goes at a distinct grapheme
                boundary, including the start and end of the text, chosen at random. If the text has fewer
                boundaries than the budget, every boundary gets one. Defaults to None, which inserts one
                between every pair of characters.
            characters (Sequence[str] | None): The zero-width characters to insert. Each insertion picks one at
                random. Defaults to None, which uses only the zero-width space. Pass ``ZERO_WIDTH_SPACE``,
                ``ZERO_WIDTH_NON_JOINER`` and ``ZERO_WIDTH_JOINER`` for the set garak's BadCharacters probe uses.
            seed (int | None): Optional seed for reproducible positions and characters. Defaults to None.

        Raises:
            ValueError: If ``budget`` is not a positive integer, or ``characters`` is empty or contains an
                empty string.
        """
        super().__init__()

        if budget is not None and (isinstance(budget, bool) or budget < 1):
            raise ValueError(f"budget must be a positive integer or None, got {budget!r}")
        if characters is not None and (not characters or not all(characters)):
            raise ValueError("characters must be a non-empty sequence of non-empty strings")

        self._budget = budget
        self._characters = tuple(characters) if characters is not None else None
        self._seed = seed

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the zero-width parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "budget": self._budget,
                "characters": list(self._characters) if self._characters is not None else None,
                "seed": self._seed,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by injecting zero-width characters.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the modified prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Only 'text' input type is supported.")

        clusters = graphemes(prompt)
        characters = self._characters or (self.ZERO_WIDTH_SPACE,)

        if self._budget is None:
            modified_text = self._insert_between_all(clusters=clusters, characters=characters)
        else:
            modified_text = self._insert_with_budget(clusters=clusters, characters=characters, budget=self._budget)

        return ConverterResult(output_text=modified_text, output_type="text")

    def _insert_between_all(self, *, clusters: list[str], characters: tuple[str, ...]) -> str:
        """
        Insert a zero-width character between every pair of grapheme clusters.

        Returns:
            str: The text with a zero-width character in every gap.
        """
        if len(characters) == 1:
            return characters[0].join(clusters)

        rng = self._get_random_generator(stream="zero-width-characters")
        pieces = clusters[:1]
        for cluster in clusters[1:]:
            pieces.extend((rng.choice(characters), cluster))
        return "".join(pieces)

    def _insert_with_budget(self, *, clusters: list[str], characters: tuple[str, ...], budget: int) -> str:
        """
        Insert up to ``budget`` zero-width characters at distinct, randomly chosen grapheme boundaries.

        Returns:
            str: The text with the zero-width characters inserted, or the text unchanged if it is empty.
        """
        if not clusters:
            return ""

        rng = self._get_random_generator(stream="zero-width-positions")
        boundaries = range(len(clusters) + 1)
        positions = set(rng.sample(boundaries, min(budget, len(boundaries))))

        pieces: list[str] = []
        for boundary in boundaries:
            if boundary in positions:
                pieces.append(rng.choice(characters))
            if boundary < len(clusters):
                pieces.append(clusters[boundary])
        return "".join(pieces)
