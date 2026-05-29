# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass, field

from pyrit.prompt_normalizer import (
    PromptConverterConfiguration,
)


@dataclass
class StrategyConverterConfig:
    """
    Configuration for prompt converters used in strategies.

    This class defines the converter configurations that transform prompts
    during the strategy process, both for requests and responses.
    """

    # List of converter configurations to apply to target requests/prompts
    request_converters: list[PromptConverterConfiguration] = field(default_factory=list)

    # List of converter configurations to apply to target responses
    response_converters: list[PromptConverterConfiguration] = field(default_factory=list)
