# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import ClassVar, Literal

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class BidiConverter(PromptConverter):
    """
    Wraps text in Unicode bidirectional control characters.

    The converter surrounds the prompt with a matched pair of bidirectional formatting code points
    so that the logical (stored) code point order can differ from the order a human reader sees
    rendered. This is the family of manipulation behind the "Trojan Source" findings
    (CVE-2021-42574). The transformation is deterministic: no language model or randomness is
    involved, so the same input and scheme always produce the same output.

    Schemes (per the Unicode Bidirectional Algorithm, UAX #9):
        - ``"override"``: RIGHT-TO-LEFT OVERRIDE (U+202E) ... POP DIRECTIONAL FORMATTING (U+202C).
        - ``"embedding"``: RIGHT-TO-LEFT EMBEDDING (U+202B) ... POP DIRECTIONAL FORMATTING (U+202C).
        - ``"isolate"``: RIGHT-TO-LEFT ISOLATE (U+2067) ... POP DIRECTIONAL ISOLATE (U+2069).

    References:
        - Boucher and Anderson, "Trojan Source: Invisible Vulnerabilities" (CVE-2021-42574),
          https://trojansource.codes/
        - Unicode Standard Annex #9, "Unicode Bidirectional Algorithm",
          https://www.unicode.org/reports/tr9/
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    # Scheme name mapped to its (opening, closing) control characters, built from code points to
    # keep the source file pure ASCII
    _SCHEMES: ClassVar[dict[str, tuple[str, str]]] = {
        "override": (chr(0x202E), chr(0x202C)),
        "embedding": (chr(0x202B), chr(0x202C)),
        "isolate": (chr(0x2067), chr(0x2069)),
    }

    def __init__(self, *, scheme: Literal["override", "embedding", "isolate"] = "override") -> None:
        """
        Initialize the converter with the bidirectional control scheme.

        Args:
            scheme (Literal["override", "embedding", "isolate"]): The bidirectional control scheme
                used to wrap the prompt. Defaults to ``"override"``.

        Raises:
            ValueError: If ``scheme`` is not recognized.
        """
        super().__init__()

        if scheme not in self._SCHEMES:
            raise ValueError(f"Scheme '{scheme}' not recognized. Choose from {list(self._SCHEMES)}.")

        self._scheme = scheme

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the bidi scheme parameter.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(params={"scheme": self._scheme})

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by wrapping it in bidirectional control characters.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the wrapped text, or an empty string if the
            prompt is empty.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        if not prompt:
            return ConverterResult(output_text="", output_type="text")

        prefix, suffix = self._SCHEMES[self._scheme]
        return ConverterResult(output_text=f"{prefix}{prompt}{suffix}", output_type="text")
