# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass

from pyrit.converter import Converter
from pyrit.models import PromptDataType


@dataclass
class ConverterConfiguration:
    """
    Represents the configuration for a converter.

    The list of converters are applied to a response, which can have multiple response pieces.
    indexes_to_apply are which pieces to apply to. By default, all indexes are applied.
    prompt_data_types_to_apply are the types of the responses to apply the converters.
    """

    converters: list[Converter]
    indexes_to_apply: list[int] | None = None
    prompt_data_types_to_apply: list[PromptDataType] | None = None

    @classmethod
    def from_converters(cls, *, converters: list[Converter]) -> list["ConverterConfiguration"]:
        """
        Convert a list of converters into a list of ConverterConfiguration objects.
        Each converter gets its own configuration with default settings.

        Args:
            converters: List of Converters

        Returns:
            list[ConverterConfiguration]: List of configurations, one per converter
        """
        if not converters:
            return []
        return [cls(converters=[converter]) for converter in converters]
