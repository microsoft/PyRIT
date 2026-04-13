# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import TYPE_CHECKING, Optional, Protocol

from pyrit.models import (
    Message,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.utils import limit_requests_per_minute

# Avoid errors for users who don't have playwright installed
if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = None


class InteractionFunction(Protocol):
    """
    Defines the structure of interaction functions used with PlaywrightTarget.
    """

    async def __call__(self, page: "Page", message: Message) -> str:
        """
        Execute the interaction function with the given page and message.

        Args:
            page (Page): The Playwright page object.
            message (Message): The message object containing the prompt.

        Returns:
            str: The response text from the interaction.
        """
        ...


class PlaywrightTarget(PromptTarget):
    """
    PlaywrightTarget uses Playwright to interact with a web UI.

    The interaction function receives the complete Message and can process
    multiple pieces as needed. All pieces must be of type 'text' or 'image_path'.

    Parameters:
        interaction_func (InteractionFunction): The function that defines how to interact with the page.
            This function receives the Playwright page and the complete Message.
        page (Page): The Playwright page object to use for interaction.
    """

    # Supported data types
    SUPPORTED_DATA_TYPES = {"text", "image_path"}
    _DEFAULT_CAPABILITIES: TargetCapabilities = TargetCapabilities(
        supports_multi_turn=True,
        supports_multi_message_pieces=True,
        input_modalities=frozenset(
            {
                frozenset(["text"]),
                frozenset(["text", "image_path"]),
            }
        ),
    )

    def __init__(
        self,
        *,
        interaction_func: InteractionFunction,
        page: "Page",
        max_requests_per_minute: Optional[int] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
    ) -> None:
        """
        Initialize the Playwright target.

        Args:
            interaction_func (InteractionFunction): The function that defines how to interact with the page.
            page (Page): The Playwright page object to use for interaction.
            max_requests_per_minute (int, Optional): Number of requests the target can handle per
                minute before hitting a rate limit. The number of requests sent to the target
                will be capped at the value provided.
            custom_capabilities (TargetCapabilities, Optional): Override the default capabilities for
                this target instance. Defaults to None.
        """
        endpoint = page.url if page else ""
        super().__init__(
            max_requests_per_minute=max_requests_per_minute, endpoint=endpoint, custom_capabilities=custom_capabilities
        )
        self._interaction_func = interaction_func
        self._page = page

    @limit_requests_per_minute
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Asynchronously send a message to the Playwright target.

        Args:
            message (Message): The message object containing the prompt to send.

        Returns:
            list[Message]: A list containing the response from the prompt target.

        Raises:
            RuntimeError: If the Playwright page is not initialized or if an error occurs during interaction.
        """
        self._validate_request(message=message)
        if not self._page:
            raise RuntimeError(
                "Playwright page is not initialized. Please pass a Page object when initializing PlaywrightTarget."
            )

        try:
            text = await self._interaction_func(self._page, message)
        except Exception as e:
            raise RuntimeError(f"An error occurred during interaction: {str(e)}") from e

        # For response construction, we'll use the first piece as reference
        # but the interaction function can process all pieces.
        request_piece = message.message_pieces[0]
        response_entry = construct_response_from_request(request=request_piece, response_text_pieces=[text])
        return [response_entry]
