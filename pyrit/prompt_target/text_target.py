# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys
from typing import IO

from pyrit.models import Message
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


class TextTarget(PromptTarget):
    """
    The TextTarget takes prompts, adds them to memory and writes them to io
    which is sys.stdout by default.

    This can be useful in various situations, for example, if operators want to generate prompts
    but enter them manually.
    """

    def __init__(
        self,
        *,
        text_stream: IO[str] = sys.stdout,
        custom_configuration: TargetConfiguration | None = None,
    ) -> None:
        """
        Initialize the TextTarget.

        Args:
            text_stream (IO[str]): The text stream to write prompts to. Defaults to sys.stdout.
            custom_configuration (TargetConfiguration, Optional): Override the default configuration for
                this target instance. Defaults to None.
        """
        super().__init__(custom_configuration=custom_configuration)
        self._text_stream = text_stream

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Asynchronously write a message to the text stream.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: An empty list (no response expected).
        """
        message = normalized_conversation[-1]

        self._text_stream.write(f"{str(message)}\n")
        self._text_stream.flush()

        return []

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        pass

    async def cleanup_target_async(self) -> None:
        """Target does not require cleanup."""
