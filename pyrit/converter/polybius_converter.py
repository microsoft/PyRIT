# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class PolybiusConverter(Converter):
    """
    Encodes text using the Polybius square.

    The Polybius square is a classical cipher that maps each letter of the alphabet to a
    pair of coordinates (row, column) on a 5x5 grid. Because the Latin alphabet has 26
    letters but the grid holds only 25 cells, two letters are merged into a single cell
    (traditionally ``I`` and ``J``). The standard square is::

           1 2 3 4 5
        1  A B C D E
        2  F G H I K      (J shares I's cell)
        3  L M N O P
        4  Q R S T U
        5  V W X Y Z

    With this square ``A`` encodes to ``11``, ``B`` to ``12``, ``I`` (and ``J``) to ``24``,
    and ``Z`` to ``55``. The two merged letters, the separator placed between the row and
    column digits of a pair, and the separator placed between pairs are all configurable.
    Non-letter characters (digits, punctuation, whitespace) are preserved unchanged so the
    encoded prompt keeps its structure. Encoding is case-insensitive.

    Reference: https://en.wikipedia.org/wiki/Polybius_square (public domain cipher).

    Like other classical cipher converters, this is useful for probing whether a model will
    decode and act on obfuscated content, following the cipher-based jailbreak framing of
    CipherChat ("GPT-4 Is Too Smart To Be Safe", Yuan et al., arXiv:2308.06463).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        merge_letters: tuple[str, str] = ("I", "J"),
        coordinate_separator: str = "",
        letter_separator: str = " ",
    ) -> None:
        """
        Initialize the Polybius square converter.

        Args:
            merge_letters (tuple[str, str]): The two letters that share a single cell on the
                grid. The second letter is omitted from the grid and encodes to the same
                coordinate as the first. Defaults to ``("I", "J")``.
            coordinate_separator (str): String inserted between the row and column digits of a
                single letter's coordinate pair (e.g. ``"-"`` yields ``1-1`` for ``A``).
                Defaults to ``""``.
            letter_separator (str): String inserted between the coordinate pairs of consecutive
                letters. Defaults to ``" "``.

        Raises:
            ValueError: If ``merge_letters`` does not contain two distinct ASCII letters.
        """
        first, second = merge_letters
        first = first.upper()
        second = second.upper()
        if first not in _ALPHABET or second not in _ALPHABET:
            raise ValueError("merge_letters must both be ASCII letters A-Z")
        if first == second:
            raise ValueError("merge_letters must be two distinct letters")

        self._merge_letters = (first, second)
        self._coordinate_separator = coordinate_separator
        self._letter_separator = letter_separator

        # Build the 5x5 grid from the alphabet with the second merged letter removed, then
        # point the removed letter at the cell of the first merged letter.
        grid_letters = [letter for letter in _ALPHABET if letter != second]
        self._encoding: dict[str, str] = {}
        for index, letter in enumerate(grid_letters):
            row = index // 5 + 1
            column = index % 5 + 1
            self._encoding[letter] = f"{row}{coordinate_separator}{column}"
        self._encoding[second] = self._encoding[first]

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with the Polybius converter parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "merge_letters": list(self._merge_letters),
                "coordinate_separator": self._coordinate_separator,
                "letter_separator": self._letter_separator,
            }
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into Polybius square coordinates.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType, optional): Type of input data. Defaults to "text".

        Returns:
            ConverterResult: The result containing the Polybius-encoded representation of the prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        output_text = self._polybius(prompt)
        return ConverterResult(output_text=output_text, output_type="text")

    def _polybius(self, text: str) -> str:
        """
        Encode letters as coordinate pairs; preserve every non-letter character unchanged.

        Runs of consecutive encoded letters are joined by ``letter_separator``; a non-letter
        character ends such a run and passes through verbatim.

        Returns:
            str: The Polybius-encoded text with non-letter characters preserved.
        """
        parts: list[str] = []
        pending: list[str] = []

        def flush() -> None:
            if pending:
                parts.append(self._letter_separator.join(pending))
                pending.clear()

        for char in text:
            upper = char.upper()
            if upper in self._encoding:
                pending.append(self._encoding[upper])
            else:
                flush()
                parts.append(char)
        flush()
        return "".join(parts)
