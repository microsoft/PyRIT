# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import IO, ClassVar

from pyrit.common import AuthMode
from pyrit.models import Message
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


class TextTarget(PromptTarget):
    """
    The TextTarget takes prompts, adds them to memory and writes them to io
    which is sys.stdout by default.

    This can be useful in various situations, for example, if operators want to generate prompts
    but enter them manually.
    """

    # Writes to a local stream and never contacts a service, so it takes no credentials.
    supported_auth_modes: ClassVar[tuple[AuthMode, ...]] = ()

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(supports_responses=False)
    )

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
                this target instance. Defaults to None. The target never replies, so
                ``supports_responses`` is always False.
        """
        super().__init__(custom_configuration=custom_configuration)
        if self.capabilities.supports_responses:
            # A custom configuration defaults supports_responses to True; apply_capabilities clears it.
            self.apply_capabilities(capabilities=self.capabilities)
        self._text_stream = text_stream

    def apply_capabilities(self, *, capabilities: TargetCapabilities) -> None:
        """Replace capabilities, keeping ``supports_responses`` False because writing to a stream never replies."""
        super().apply_capabilities(capabilities=self._without_responses(capabilities))

    @contextmanager
    def capabilities_scope(self, capabilities: TargetCapabilities) -> Generator[None, None, None]:
        """
        Use saved capabilities for this task, keeping ``supports_responses`` False.

        A snapshot saved before ``supports_responses`` existed loads with its default of True.

        Args:
            capabilities: Capabilities used for normalization, validation, and provider serialization.

        Yields:
            None: The task-local configuration scope.
        """
        with super().capabilities_scope(self._without_responses(capabilities)):
            yield

    @staticmethod
    def _without_responses(capabilities: TargetCapabilities) -> TargetCapabilities:
        return capabilities.model_copy(update={"supports_responses": False})

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
