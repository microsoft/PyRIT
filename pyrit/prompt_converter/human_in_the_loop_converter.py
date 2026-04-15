# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Optional

from pyrit.common.deprecation import print_deprecation_message
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class HumanInTheLoopConverter(PromptConverter):
    """
    Allows review of each prompt sent to a target before sending it.

    Users can choose to send the prompt as is, modify the prompt,
    or run the prompt through one of the passed-in converters before sending it.

    .. deprecated::
        This converter is deprecated and will be removed in v0.13.0.
        Use the React-based GUI (CoPyRIT) instead.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        converters: Optional[list[PromptConverter]] = None,
    ):
        """
        Initialize the converter with a list of possible converters to run input through.

        Args:
            converters (List[PromptConverter], Optional): List of possible converters to run input through.
        """
        print_deprecation_message(
            old_item="HumanInTheLoopConverter",
            new_item="the React-based GUI (CoPyRIT); see https://microsoft.github.io/PyRIT/code/gui/0_gui.html",
            removed_in="0.13.0",
        )

        self._converters = converters or []

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with sub-converters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            children={
                "sub_converters": [converter.get_identifier() for converter in self._converters],
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by allowing user interaction before sending it to a target.

        User is given three options to choose from:
            (1) Proceed with sending the prompt as is.
            (2) Manually modify the prompt.
            (3) Run the prompt through a converter before sending it.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the modified prompt.

        Raises:
            ValueError: If no converters are provided and the user chooses to run a converter.
        """
        user_input = ""
        if self._converters:
            while user_input not in ["1", "2", "3"]:
                user_input = input(
                    f"This is the next {input_type} prompt to be sent: [{prompt}] \
                                Select an option: (1) Proceed with sending the prompt as is. \
                                (2) Manually modify the prompt. (3) Run the prompt through a \
                                converter before sending it. Please enter the number of the \
                                choice you'd like."
                ).strip()
        else:
            while user_input not in ["1", "2"]:
                user_input = input(
                    f"This is the next {input_type} prompt to be sent: [{prompt}] \
                                Select an option: (1) Proceed with sending the prompt as is. \
                                (2) Manually modify the prompt. \
                                Please enter the number of the choice you'd like."
                ).strip()
        if user_input == "1":
            return ConverterResult(output_text=prompt, output_type=input_type)
        if user_input == "2":
            new_input = input("Enter new prompt to send: ")
            return await self.convert_async(prompt=new_input, input_type=input_type)
        if user_input == "3":
            if self._converters:
                converters_str = str([converter.__class__.__name__ for converter in self._converters])
                converter_index = -1
                while not 0 <= converter_index < len(self._converters):
                    converter_index = int(
                        input(
                            f"The available converters are {converters_str}. \
                                                Enter the index of the converter you would like to run on \
                                                the input (0 to {len(self._converters) - 1})"
                        ).strip()
                    )
                converter = self._converters[converter_index]
                new_result = await converter.convert_async(prompt=prompt, input_type=input_type)
                return await self.convert_async(prompt=new_result.output_text, input_type=new_result.output_type)
            raise ValueError("No converters were passed into the HumanInTheLoopConverter")
        return ConverterResult(output_text=prompt, output_type=input_type)
