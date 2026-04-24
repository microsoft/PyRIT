# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass
from typing import Optional

from pyrit.models import PromptDataType
from pyrit.prompt_converter import PromptConverter


@dataclass
class PromptConverterConfiguration:
    """
    Represents the configuration for a prompt response converter.

    The list of converters are applied to a response, which can have multiple response pieces.
    indexes_to_apply are which pieces to apply to. By default, all indexes are applied.
    prompt_data_types_to_apply are the types of the responses to apply the converters.
    """

    converters: list[PromptConverter]
    indexes_to_apply: Optional[list[int]] = None
    prompt_data_types_to_apply: Optional[list[PromptDataType]] = None

    @classmethod
    def from_converters(cls, *, converters: list[PromptConverter]) -> list["PromptConverterConfiguration"]:
        """
        Convert a list of converters into a list of PromptConverterConfiguration objects.
        Each converter gets its own configuration with default settings.

        Args:
            converters: List of PromptConverters

        Returns:
            List[PromptConverterConfiguration]: List of configurations, one per converter
        """
        if not converters:
            return []
        return [cls(converters=[converter]) for converter in converters]
