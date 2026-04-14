# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
import json
import logging
from typing import Optional

from pyrit.common import net_utility
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, construct_response_from_request
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)


class GandalfLevel(enum.Enum):
    """
    Enumeration of Gandalf challenge levels.

    Each level represents a different difficulty of the Gandalf security challenge,
    from baseline to the most advanced levels.
    """

    LEVEL_1 = "baseline"
    LEVEL_2 = "do-not-tell"
    LEVEL_3 = "do-not-tell-and-block"
    LEVEL_4 = "gpt-is-password-encoded"
    LEVEL_5 = "word-blacklist"
    LEVEL_6 = "gpt-blacklist"
    LEVEL_7 = "gandalf"
    LEVEL_8 = "gandalf-the-white"
    LEVEL_9 = "adventure-1"
    LEVEL_10 = "adventure-2"


class GandalfTarget(PromptTarget):
    """A prompt target for the Gandalf security challenge."""

    def __init__(
        self,
        *,
        level: GandalfLevel,
        max_requests_per_minute: Optional[int] = None,
        custom_configuration: Optional[TargetConfiguration] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
    ) -> None:
        """
        Initialize the Gandalf target.

        Args:
            level (GandalfLevel): The Gandalf level to target.
            max_requests_per_minute (int, Optional): Number of requests the target can handle per
                minute before hitting a rate limit. The number of requests sent to the target
                will be capped at the value provided.
            custom_configuration (TargetConfiguration, Optional): Override the default configuration for this
              target instance.
            custom_capabilities (TargetCapabilities, Optional): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.14.0.
        """
        endpoint = "https://gandalf-api.lakera.ai/api/send-message"
        super().__init__(
            max_requests_per_minute=max_requests_per_minute,
            endpoint=endpoint,
            custom_configuration=custom_configuration,
            custom_capabilities=custom_capabilities,
        )

        self._defender = level.value

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with Gandalf-specific parameters.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "level": self._defender,
            },
        )

    @limit_requests_per_minute
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Asynchronously send a message to the Gandalf target.

        Args:
            message (Message): The message object containing the prompt to send.

        Returns:
            list[Message]: A list containing the response from the prompt target.
        """
        self._validate_request(message=message)
        request = message.message_pieces[0]

        logger.info(f"Sending the following prompt to the prompt target: {request}")

        response = await self._complete_text_async(request.converted_value)

        response_entry = construct_response_from_request(request=request, response_text_pieces=[response])

        return [response_entry]

    async def check_password(self, password: str) -> bool:
        """
        Check if the password is correct.

        Returns:
            bool: True if the password is correct, False otherwise.

        Raises:
            ValueError: If the chat returned an empty response.
        """
        payload: dict[str, object] = {
            "defender": self._defender,
            "password": password,
        }

        resp = await net_utility.make_request_and_raise_if_error_async(
            endpoint_uri=self._endpoint, method="POST", request_body=payload, post_type="data"
        )

        if not resp.text:
            raise ValueError("The chat returned an empty response.")

        json_response = resp.json()
        return bool(json_response["success"])

    async def _complete_text_async(self, text: str) -> str:
        payload: dict[str, object] = {
            "defender": self._defender,
            "prompt": text,
        }

        resp = await net_utility.make_request_and_raise_if_error_async(
            endpoint_uri=self._endpoint, method="POST", request_body=payload, post_type="data"
        )

        if not resp.text:
            raise ValueError("The chat returned an empty response.")

        answer: str = json.loads(resp.text)["answer"]

        logger.info(f'Received the following response from the prompt target "{answer}"')
        return answer
