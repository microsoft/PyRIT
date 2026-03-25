# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Optional

from httpx import HTTPStatusError

from pyrit.common import default_values, net_utility
from pyrit.exceptions import (
    EmptyResponseException,
    handle_bad_request_exception,
    pyrit_target_retry,
)
from pyrit.models import Message, construct_response_from_request
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)


class CrucibleTarget(PromptTarget):
    """A prompt target for the Crucible service."""

    API_KEY_ENVIRONMENT_VARIABLE: str = "CRUCIBLE_API_KEY"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: Optional[str] = None,
        max_requests_per_minute: Optional[int] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
    ) -> None:
        """
        Initialize the Crucible target.

        Args:
            endpoint (str): The endpoint URL for the Crucible service.
            api_key (str, Optional): The API key for accessing the Crucible service.
                Defaults to the `CRUCIBLE_API_KEY` environment variable.
            max_requests_per_minute (int, Optional): Number of requests the target can handle per
                minute before hitting a rate limit. The number of requests sent to the target
                will be capped at the value provided.
            custom_capabilities (TargetCapabilities, Optional): Override the default capabilities for
                this target instance. Defaults to None.
        """
        super().__init__(
            max_requests_per_minute=max_requests_per_minute,
            endpoint=endpoint,
            custom_capabilities=custom_capabilities,
        )

        self._api_key: str = default_values.get_required_value(
            env_var_name=self.API_KEY_ENVIRONMENT_VARIABLE, passed_value=api_key
        )

    @limit_requests_per_minute
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Asynchronously send a message to the Crucible target.

        Args:
            message (Message): The message object containing the prompt to send.

        Returns:
            list[Message]: A list containing the response from the prompt target.

        Raises:
            HTTPStatusError: For any other HTTP errors during the process.
        """
        self._validate_request(message=message)
        request = message.message_pieces[0]

        logger.info(f"Sending the following prompt to the prompt target: {request}")

        try:
            response = await self._complete_text_async(request.converted_value)
            response_entry = construct_response_from_request(request=request, response_text_pieces=[response])
        except HTTPStatusError as bre:
            if bre.response.status_code == 400:
                response_entry = handle_bad_request_exception(
                    response_text=bre.response.text, request=request, is_content_filter=True
                )
            else:
                raise

        return [response_entry]

    @pyrit_target_retry
    async def _complete_text_async(self, text: str) -> str:
        payload: dict[str, object] = {
            "data": text,
        }

        resp = await net_utility.make_request_and_raise_if_error_async(
            endpoint_uri=f"{self._endpoint.rstrip('/')}/score",
            method="POST",
            request_body=payload,
            headers={"X-API-Key": self._api_key},
        )

        if not resp.text:
            raise EmptyResponseException

        logger.info(f'Received the following response from the prompt target "{resp.text}"')
        return resp.text
