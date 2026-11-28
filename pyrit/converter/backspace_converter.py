# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Portions Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# Garak-derived portions are licensed under Apache-2.0 and modified by Microsoft Corporation.
# See THIRD_PARTY_NOTICES.txt for attribution and source details.

from collections.abc import Sequence
from typing import ClassVar

from pyrit.common.text_helper import graphemes
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class BackspaceConverter(Converter):
    """
    Inserts characters that are each followed by a backspace, so a renderer that honors backspaces shows the
    original text.

    Each insertion is one character and then BACKSPACE (U+0008). Where the backspace deletes the character
    before it, the text looks unchanged, but a model reading the code points sees the extra character. This
    is the deletion perturbation from Boucher et al., "Bad Characters: Imperceptible NLP Attacks"
    (arXiv:2106.09898), as implemented by garak's BadCharacters probe.

    Characters are grapheme clusters, so insertions never split combining marks, Indic vowel signs or
    emoji sequences.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    #: The backspace control character placed after each inserted character.
    BACKSPACE: ClassVar[str] = "\b"
    #: The printable ASCII characters (U+0020 to U+007E) that are inserted by default.
    ASCII_PRINTABLE: ClassVar[tuple[str, ...]] = tuple(chr(code_point) for code_point in range(0x20, 0x7F))

    def __init__(
        self,
        *,
        budget: int = 1,
        characters: Sequence[str] | None = None,
        seed: int | None = None,
    ) -> None:
        """
        Initialize the converter.

        Args:
            budget (int): How many character-and-backspace pairs to insert. Each goes at a distinct grapheme
                boundary, including the start and end of the text, chosen at random. If the text has fewer
                boundaries than the budget, every boundary gets one. Defaults to 1.
            characters (Sequence[str] | None): The characters to insert before each backspace. Each insertion
                picks one at random. Defaults to None, which uses ``ASCII_PRINTABLE``.
            seed (int | None): Optional seed for reproducible positions and characters. Defaults to None.

        Raises:
            ValueError: If ``budget`` is not a positive integer, or ``characters`` is empty or contains
                anything other than a single character that is not a backspace.
        """
        super().__init__()

        if isinstance(budget, bool) or budget < 1:
            raise ValueError(f"budget must be a positive integer, got {budget!r}")
        if characters is not None and (
            not characters or any(len(character) != 1 or character == self.BACKSPACE for character in characters)
        ):
            raise ValueError("characters must be a non-empty sequence of single characters other than backspace")

        self._budget = budget
        self._characters = tuple(characters) if characters is not None else None
        self._seed = seed

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the backspace parameters.

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
        Convert the given prompt by inserting characters that are each followed by a backspace.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the modified prompt, or the prompt unchanged if it is empty.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Only 'text' input type is supported.")

        clusters = graphemes(prompt)
        if not clusters:
            return ConverterResult(output_text=prompt, output_type="text")

        characters = self._characters or self.ASCII_PRINTABLE
        rng = self._get_random_generator(stream="backspace-insertions")
        boundaries = range(len(clusters) + 1)
        positions = set(rng.sample(boundaries, min(self._budget, len(boundaries))))

        pieces: list[str] = []
        for boundary in boundaries:
            if boundary in positions:
                pieces.append(f"{rng.choice(characters)}{self.BACKSPACE}")
            if boundary < len(clusters):
                pieces.append(clusters[boundary])

        return ConverterResult(output_text="".join(pieces), output_type="text")
