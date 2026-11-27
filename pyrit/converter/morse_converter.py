# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._cipher_description import CIPHER_EXAMPLE, maybe_render_description
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class MorseConverter(Converter):
    """
    Encodes prompts using morse code.

    Uses '-' and '.' characters, with ' ' to separate characters and '/' to separate words.
    Invalid or unsupported characters are replaced with an error sequence '........'.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, append_description: bool = False) -> None:
        """
        Initialize the converter with an option to append a description to the prompt.

        Args:
            append_description (bool): Append plaintext "expert" text to the prompt. Includes instructions to only
                communicate using the cipher, a description of the cipher, and an example encoded using cipher.
        """
        self.append_description = append_description

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with morse converter parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "append_description": self.append_description,
            }
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to morse code.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType, optional): Type of input data. Defaults to "text".

        Returns:
            ConverterResult: The result containing the morse code representation of the prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        output_text = maybe_render_description(
            self._morse(prompt),
            append_description=self.append_description,
            yaml_stem="morse",
            example_encoded=self._morse(CIPHER_EXAMPLE),
        )
        return ConverterResult(output_text=output_text, output_type="text")

    def _morse(self, text: str) -> str:
        text_clean = " ".join([line.strip() for line in str.splitlines(text)])
        morse_mapping = {
            "A": ".-",
            "B": "-...",
            "C": "-.-.",
            "D": "-..",
            "E": ".",
            "F": "..-.",
            "G": "--.",
            "H": "....",
            "I": "..",
            "J": ".---",
            "K": "-.-",
            "L": ".-..",
            "M": "--",
            "N": "-.",
            "O": "---",
            "P": ".--.",
            "Q": "--.-",
            "R": ".-.",
            "S": "...",
            "T": "-",
            "U": "..-",
            "V": "...-",
            "W": ".--",
            "X": "-..-",
            "Y": "-.--",
            "Z": "--..",
            "0": "-----",
            "1": ".----",
            "2": "..---",
            "3": "...--",
            "4": "....-",
            "5": ".....",
            "6": "-....",
            "7": "--...",
            "8": "---..",
            "9": "----.",
            "'": ".----.",
            '"': ".-..-.",
            ":": "---...",
            "@": ".--.-.",
            ",": "--..--",
            ".": ".-.-.-",
            "!": "-.-.--",
            "?": "..--..",
            "-": "-....-",
            "/": "-..-.",
            "+": ".-.-.",
            "=": "-...-",
            "(": "-.--.",
            ")": "-.--.-",
            "&": ".-...",
            " ": "/",
        }
        extended_mapping = {
            # Morse has no single code for "%"; ITU-R M.1677-1 sends it as 0/0.
            "%": "----- -..-. -----",
            "À": ".--.-",
            "Å": ".--.-",
            "Ä": ".-.-",
            "Ą": ".-.-",
            "Æ": ".-.-",
            "Ć": "-.-..",
            "Ĉ": "-.-..",
            "Ç": "-.-..",
            "Ĥ": "----",
            "Š": "----",
            "Đ": "..-..",
            "É": "..-..",
            "Ę": "..-..",
            "Ð": "..--.",
            "È": ".-..-",
            "Ł": ".-..-",
            "Ĝ": "--.-.",
            "Ĵ": ".---.",
            "Ń": "--.--",
            "Ñ": "--.--",
            "Ó": "---.",
            "Ö": "---.",
            "Ø": "---.",
            "Ś": "...-...",
            "Ŝ": "...-.",
            "Þ": ".--..",
            "Ü": "..--",
            "Ŭ": "..--",
            "Ź": "--..-.",
            "Ż": "--..-",
        }
        morse_mapping = {**morse_mapping, **extended_mapping}
        supported_charset = "".join(morse_mapping.keys())
        error_char = "........"
        return " ".join(
            [morse_mapping[char] if char in supported_charset else error_char for char in text_clean.upper()]
        )
