# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType


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

    # The rendering is fixed so the output is uniquely decodable: a single tap symbol, a single space
    # between the row-tap and column-tap group of a letter, and two spaces between letters.
    _TAP_SYMBOL = "."
    _GROUP_SEPARATOR = " "
    _LETTER_SEPARATOR = "  "

    def __init__(self) -> None:
        """Initialize the converter and precompute the tap-code coordinate of each letter."""
        # Precompute the (row, column) coordinate for each supported letter.
        self._coordinates: dict[str, tuple[int, int]] = {}
        for index, letter in enumerate(self._GRID):
            self._coordinates[letter] = (index // 5 + 1, index % 5 + 1)
        # K shares C's cell.
        self._coordinates["K"] = self._coordinates["C"]

    @classmethod
    def to_ascii_equivalent(cls, *, text: str) -> str:
        """
        Return ``text`` with every ``K`` folded to ``C`` to match what a faithful tap-code decode yields.

        The grid has no cell for ``K`` (it shares ``C``'s cell), so a target that decodes the taps correctly
        answers with ``C`` wherever the seed had ``K``. The Encoding scenario attaches this value to the seed
        copies as ``decoded_text`` so ``DecodingScorer`` accepts the correct decode instead of failing it.

        Args:
            text (str): The original seed text.

        Returns:
            str: ``text`` with ``K``/``k`` replaced by ``C``/``c``.
        """
        return text.replace("K", "C").replace("k", "c")

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

        Each ASCII letter is replaced with its row-tap and column-tap groups joined by a single
        space; encoded letters are joined by two spaces. Any character that is not an ASCII letter
        is preserved verbatim so surrounding structure (spaces, digits, punctuation) survives the
        transformation.

        Args:
            text (str): The input text to encode.

        Returns:
            str: The tap-code representation of the input text.
        """
        tokens: list[str] = []
        for char in text:
            # Only ASCII letters have a tap-code cell. Guarding on ``isascii`` before uppercasing keeps
            # non-ASCII characters whose upper-case form is an ASCII letter (e.g. Turkish ``ı`` -> ``I``,
            # ``ſ`` -> ``S``) in the verbatim branch, so multilingual prompts are not silently re-encoded.
            coordinate = self._coordinates.get(char.upper()) if char.isascii() else None
            if coordinate is None:
                # Preserve non-letters verbatim.
                tokens.append(char)
                continue
            row, column = coordinate
            tokens.append(f"{self._TAP_SYMBOL * row}{self._GROUP_SEPARATOR}{self._TAP_SYMBOL * column}")
        return self._LETTER_SEPARATOR.join(tokens)
