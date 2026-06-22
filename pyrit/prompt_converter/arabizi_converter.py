# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)

# Arabic script mapped to Arabizi (Latin-script "chat Arabic"), using the widely documented Arabic
# chat alphabet with Gulf-leaning conventions. Keys are built from code points so the source file
# stays pure ASCII. The mapping is intentionally lossy (for example THEH and THAL both map to "th"),
# which mirrors how Arabizi is actually written.
_ARABIC_TO_ARABIZI: dict[str, str] = {
    chr(0x0627): "a",  # ALEF
    chr(0x0628): "b",  # BEH
    chr(0x062A): "t",  # TEH
    chr(0x062B): "th",  # THEH
    chr(0x062C): "j",  # JEEM
    chr(0x062D): "7",  # HAH
    chr(0x062E): "5",  # KHAH
    chr(0x062F): "d",  # DAL
    chr(0x0630): "th",  # THAL
    chr(0x0631): "r",  # REH
    chr(0x0632): "z",  # ZAIN
    chr(0x0633): "s",  # SEEN
    chr(0x0634): "sh",  # SHEEN
    chr(0x0635): "9",  # SAD
    chr(0x0636): "d",  # DAD
    chr(0x0637): "6",  # TAH
    chr(0x0638): "z",  # ZAH
    chr(0x0639): "3",  # AIN
    chr(0x063A): "gh",  # GHAIN
    chr(0x0641): "f",  # FEH
    chr(0x0642): "8",  # QAF
    chr(0x0643): "k",  # KAF
    chr(0x0644): "l",  # LAM
    chr(0x0645): "m",  # MEEM
    chr(0x0646): "n",  # NOON
    chr(0x0647): "h",  # HEH
    chr(0x0648): "w",  # WAW
    chr(0x064A): "y",  # YEH
    chr(0x0621): "2",  # HAMZA
    chr(0x0622): "2a",  # ALEF WITH MADDA ABOVE
    chr(0x0623): "a",  # ALEF WITH HAMZA ABOVE
    chr(0x0625): "a",  # ALEF WITH HAMZA BELOW
    chr(0x0624): "2",  # WAW WITH HAMZA ABOVE
    chr(0x0626): "2",  # YEH WITH HAMZA ABOVE
    chr(0x0629): "a",  # TEH MARBUTA
    chr(0x0649): "a",  # ALEF MAKSURA
    chr(0x0640): "",  # TATWEEL (connector, dropped)
    chr(0x064B): "",  # FATHATAN (short-vowel marks are dropped)
    chr(0x064C): "",  # DAMMATAN
    chr(0x064D): "",  # KASRATAN
    chr(0x064E): "",  # FATHA
    chr(0x064F): "",  # DAMMA
    chr(0x0650): "",  # KASRA
    chr(0x0651): "",  # SHADDA
    chr(0x0652): "",  # SUKUN
}


class ArabiziConverter(PromptConverter):
    """
    Transliterates Arabic script into Arabizi (Latin-script "chat Arabic").

    Arabizi is the everyday Latin-script encoding of Arabic used in chat and social media, where
    letters that have no Latin equivalent are written with digits that resemble their shape (for
    example HAH becomes 7, AIN becomes 3, and QAF becomes 8). This converter applies a deterministic
    per-character mapping with Gulf-leaning conventions: no language model is involved, so the same
    input always produces the same output. The attack surface targeted is tokenizer and safety
    classifier handling of transliterated Arabic, not the language itself.

    Short-vowel diacritics and the tatweel connector are dropped, and characters outside the Arabic
    block (Latin text, digits, punctuation) are left unchanged. The mapping is intentionally lossy,
    mirroring how Arabizi is actually written.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by transliterating Arabic script into Arabizi.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the transliterated text.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        converted_text = "".join(_ARABIC_TO_ARABIZI.get(char, char) for char in prompt)
        return ConverterResult(output_text=converted_text, output_type="text")
