# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass
from typing import Optional

from pyrit.models import Message
from pyrit.prompt_normalizer.prompt_converter_configuration import (
    PromptConverterConfiguration,
)


@dataclass
class NormalizerRequest:
    """
    Represents a single request sent to normalizer.
    """

    message: Message
    request_converter_configurations: list[PromptConverterConfiguration]
    response_converter_configurations: list[PromptConverterConfiguration]
    conversation_id: str | None

    def __init__(
        self,
        *,
        message: Message,
        request_converter_configurations: list[PromptConverterConfiguration] = None,
        response_converter_configurations: list[PromptConverterConfiguration] = None,
        conversation_id: Optional[str] = None,
    ):
        """
        Initialize a normalizer request.

        Args:
            message (Message): The message to be normalized.
            request_converter_configurations (list[PromptConverterConfiguration]): Configurations for converting
                the request. Defaults to an empty list.
            response_converter_configurations (list[PromptConverterConfiguration]): Configurations for converting
                the response. Defaults to an empty list.
            conversation_id (Optional[str]): The ID of the conversation. Defaults to None.
        """
        if response_converter_configurations is None:
            response_converter_configurations = []
        if request_converter_configurations is None:
            request_converter_configurations = []
        self.message = message
        self.request_converter_configurations = request_converter_configurations
        self.response_converter_configurations = response_converter_configurations
        self.conversation_id = conversation_id
