# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class TaskFramingConverter(Converter):
    """
    Frames the input prompt as a task using a configurable template.

    Wraps arbitrary input text in a task-framing template. Any template containing a
    ``{{ prompt }}`` placeholder is accepted; the placeholder marks where the input is
    inserted.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    #: Default template framing the input as a quoted task.
    DEFAULT_TASK_TEMPLATE = "TASK is '{{ prompt }}'"

    _PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*prompt\s*\}\}")

    def __init__(
        self,
        *,
        task_template: str = DEFAULT_TASK_TEMPLATE,
        strip_characters: str = "",
    ) -> None:
        """
        Initialize the converter with a task-framing template.

        Args:
            task_template (str): A template containing a ``{{ prompt }}`` placeholder
                marking where the input is inserted. Defaults to ``TASK is '{{ prompt }}'``.
            strip_characters (str): Characters removed from the input before it is
                inserted into the template. Defaults to no stripping. Useful when the
                template delimits the input (e.g. with quotes) and matching characters in
                the input would otherwise collide with those delimiters.

        Raises:
            ValueError: If ``task_template`` is missing the ``{{ prompt }}`` placeholder.
        """
        if not self._PLACEHOLDER_PATTERN.search(task_template):
            raise ValueError(f"task_template must contain a '{{{{ prompt }}}}' placeholder: {task_template!r}")

        self._task_template = task_template
        self._strip_characters = strip_characters

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the template parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "task_template": self._task_template,
                "strip_characters": self._strip_characters,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by framing it as a task.

        Args:
            prompt (str): The prompt to be framed.
            input_type (PromptDataType): Type of input data. Defaults to "text".

        Returns:
            ConverterResult: The prompt inserted into a task-framing template.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"Input type {input_type} not supported")

        cleaned = prompt.translate(str.maketrans("", "", self._strip_characters)) if self._strip_characters else prompt

        # Use a replacement function so backslashes in ``cleaned`` are inserted literally.
        framed = self._PLACEHOLDER_PATTERN.sub(lambda _: cleaned, self._task_template)
        return ConverterResult(output_text=framed, output_type="text")
