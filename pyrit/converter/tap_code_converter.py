# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class TapCodeConverter(Converter):
    """
    Encodes text using the tap code (also known as the knock code).

    The tap code is a Polybius-style code historically used by prisoners of war to
    communicate by tapping on walls or pipes. Each letter is placed on a 5x5 grid and
    encoded as two groups of taps giving its ``(row, column)`` coordinate::

            1   2   3   4   5
        1   A   B   C   D   E
        2   F   G   H   I   J
        3   L   M   N   O   P
        4   Q   R   S   T   U
        5   V   W   X   Y   Z

    Because the grid holds only 25 cells, the letters C and K share a single cell
    (row 1, column 3); a ``K`` in the input is encoded exactly like a ``C``. For
    example, the letter ``A`` is ``(1, 1)`` -> ``". ."`` and ``W`` is ``(5, 2)`` ->
    ``"..... .."``. The code is case-insensitive and any character that is not an
    ASCII letter (digits, whitespace, punctuation) is preserved verbatim so the
    encoded prompt keeps its structure.

    The tap code is in the public domain. See the standard reference,
    Wikipedia, "Tap code" (https://en.wikipedia.org/wiki/Tap_code). Encoding a
    prompt into an uncommon cipher to probe a model's safety behavior follows the
    CipherChat jailbreak framing (Yuan et al., "GPT-4 Is Too Smart To Be Safe",
    arXiv:2308.06463).

    Note: This converter is useful for testing how AI systems handle tap-code-encoded
    text, which can be used to obfuscate potentially harmful content.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    # The 25-letter tap-code alphabet laid out row by row (K is omitted and shares C's cell).
    _GRID = "ABCDEFGHIJLMNOPQRSTUVWXYZ"

    def __init__(
        self,
        *,
        tap_symbol: str = ".",
        group_separator: str = " ",
        letter_separator: str = "  ",
    ) -> None:
        """
        Initialize the converter with configurable tap and separator symbols.

        Args:
            tap_symbol (str): The symbol used to represent a single tap. Defaults to ".".
            group_separator (str): The separator placed between the row-tap group and the
                column-tap group of a single letter. Defaults to " " (a single space).
            letter_separator (str): The separator placed between encoded letters. Defaults
                to "  " (two spaces), chosen to stay visually distinct from the
                ``group_separator``.
        """
        self.tap_symbol = tap_symbol
        self.group_separator = group_separator
        self.letter_separator = letter_separator

        # Precompute the (row, column) coordinate for each supported letter.
        self._coordinates: dict[str, tuple[int, int]] = {}
        for index, letter in enumerate(self._GRID):
            self._coordinates[letter] = (index // 5 + 1, index % 5 + 1)
        # K shares C's cell.
        self._coordinates["K"] = self._coordinates["C"]

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with tap-code converter parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "tap_symbol": self.tap_symbol,
                "group_separator": self.group_separator,
                "letter_separator": self.letter_separator,
            }
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to tap code.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType, optional): Type of input data. Defaults to "text".

        Returns:
            ConverterResult: The result containing the tap-code representation of the prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        return ConverterResult(output_text=self._tap_code(prompt), output_type="text")

    def _tap_code(self, text: str) -> str:
        """
        Encode the input text into tap code.

        Each ASCII letter is replaced with its row-tap and column-tap groups joined by the
        ``group_separator``; encoded letters are joined by the ``letter_separator``. Any
        character that is not an ASCII letter is preserved verbatim so surrounding structure
        (spaces, digits, punctuation) survives the transformation.

        Args:
            text (str): The input text to encode.

        Returns:
            str: The tap-code representation of the input text.
        """
        tokens: list[str] = []
        for char in text:
            coordinate = self._coordinates.get(char.upper())
            if coordinate is None:
                # Preserve non-letters verbatim.
                tokens.append(char)
                continue
            row, column = coordinate
            tokens.append(f"{self.tap_symbol * row}{self.group_separator}{self.tap_symbol * column}")
        return self.letter_separator.join(tokens)
