# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class PromptTemplateConverter(Converter):
    """
    Inserts the input prompt into a template at a ``{{ prompt }}`` placeholder.

    Any template containing a ``{{ prompt }}`` placeholder is accepted; every
    occurrence of the placeholder is replaced with the input. This covers task
    framing (e.g. ``TASK is '{{ prompt }}'``) as well as wrapping the prompt in
    surrounding content, such as hiding it in an HTML comment for indirect prompt
    injection (e.g. ``<p>Visible text</p><!-- {{ prompt }} -->``).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    _PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*prompt\s*\}\}")

    def __init__(
        self,
        *,
        template: str,
        strip_characters: str = "",
    ) -> None:
        """
        Initialize the converter with a template.

        Args:
            template (str): A template containing a ``{{ prompt }}`` placeholder
                marking where the input is inserted.
            strip_characters (str): Characters removed from the input before it is
                inserted into the template. Defaults to no stripping. Useful when the
                template delimits the input (e.g. with quotes) and matching characters in
                the input would otherwise collide with those delimiters.

        Raises:
            ValueError: If ``template`` is missing the ``{{ prompt }}`` placeholder.
        """
        if not self._PLACEHOLDER_PATTERN.search(template):
            raise ValueError(f"template must contain a '{{{{ prompt }}}}' placeholder: {template!r}")

        self._template = template
        self._strip_characters = strip_characters

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the template parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "template": self._template,
                "strip_characters": self._strip_characters,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by inserting it into the template.

        Args:
            prompt (str): The prompt to insert.
            input_type (PromptDataType): Type of input data. Defaults to "text".

        Returns:
            ConverterResult: The template with the prompt inserted at the placeholder.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"Input type {input_type} not supported")

        cleaned = prompt.translate(str.maketrans("", "", self._strip_characters)) if self._strip_characters else prompt

        # Use a replacement function so backslashes in ``cleaned`` are inserted literally.
        output = self._PLACEHOLDER_PATTERN.sub(lambda _: cleaned, self._template)
        return ConverterResult(output_text=output, output_type="text")
