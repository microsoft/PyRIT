# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Optional

from pyrit.common.net_utility import make_request_and_raise_if_error_async
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, construct_response_from_request
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute, validate_temperature, validate_top_p

logger = logging.getLogger(__name__)


class HuggingFaceEndpointTarget(PromptTarget):
    """
    The HuggingFaceEndpointTarget interacts with HuggingFace models hosted on cloud endpoints.

    Inherits from PromptTarget to comply with the current design standards.
    """

    def __init__(
        self,
        *,
        hf_token: str,
        endpoint: str,
        model_id: str,
        max_tokens: int = 400,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_requests_per_minute: Optional[int] = None,
        verbose: bool = False,
        custom_configuration: Optional[TargetConfiguration] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
    ) -> None:
        """
        Initialize the HuggingFaceEndpointTarget with API credentials and model parameters.

        Args:
            hf_token (str): The Hugging Face token for authenticating with the Hugging Face endpoint.
            endpoint (str): The endpoint URL for the Hugging Face model.
            model_id (str): The model ID to be used at the endpoint.
            max_tokens (int, Optional): The maximum number of tokens to generate. Defaults to 400.
            temperature (float, Optional): The sampling temperature to use. Defaults to 1.0.
            top_p (float, Optional): The cumulative probability for nucleus sampling. Defaults to 1.0.
            max_requests_per_minute (Optional[int]): The maximum number of requests per minute. Defaults to None.
            verbose (bool, Optional): Flag to enable verbose logging. Defaults to False.
            custom_configuration (Optional[TargetConfiguration]): Custom configuration for this target instance.
            custom_capabilities (TargetCapabilities, Optional): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.14.0.
        """
        super().__init__(
            max_requests_per_minute=max_requests_per_minute,
            verbose=verbose,
            endpoint=endpoint,
            model_name=model_id,
            custom_configuration=custom_configuration,
            custom_capabilities=custom_capabilities,
        )

        validate_temperature(temperature)
        validate_top_p(top_p)

        self.hf_token = hf_token
        self.endpoint = endpoint
        self.model_id = model_id
        self.max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with HuggingFace endpoint-specific parameters.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "temperature": self._temperature,
                "top_p": self._top_p,
                "max_tokens": self.max_tokens,
            },
        )

    @limit_requests_per_minute
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Send a normalized prompt asynchronously to a cloud-based HuggingFace model endpoint.

        Args:
            message (Message): The message containing the input data and associated details
            such as conversation ID and role.

        Returns:
            list[Message]: A list containing the response object with generated text pieces.

        Raises:
            ValueError: If the response from the Hugging Face API is not successful.
            Exception: If an error occurs during the HTTP request to the Hugging Face endpoint.
        """
        self._validate_request(message=message)
        request = message.message_pieces[0]
        headers = {"Authorization": f"Bearer {self.hf_token}"}
        payload: dict[str, object] = {
            "inputs": request.converted_value,
            "parameters": {
                "max_tokens": self.max_tokens,
                "temperature": self._temperature,
                "top_p": self._top_p,
            },
        }

        logger.info(f"Sending the following prompt to the cloud endpoint: {request.converted_value}")

        try:
            # Use the utility method to make the request
            response = await make_request_and_raise_if_error_async(
                endpoint_uri=self.endpoint,
                method="POST",
                request_body=payload,
                headers=headers,
                post_type="json",
            )

            response_data = response.json()

            # Check if the response is a list and handle appropriately
            if isinstance(response_data, list):
                # Access the first element if it's a list and extract 'generated_text' safely
                response_message = response_data[0].get("generated_text", "")
            else:
                response_message = response_data.get("generated_text", "")

            message = construct_response_from_request(
                request=request,
                response_text_pieces=[response_message],
                prompt_metadata={"model_id": self.model_id},
            )
            return [message]

        except Exception as e:
            logger.error(f"Error occurred during HTTP request to the Hugging Face endpoint: {e}")
            raise

    def _validate_request(self, *, message: Message) -> None:
        """
        Validate the provided message.

        Args:
            message (Message): The message to validate.

        Raises:
            ValueError: If the request is not valid for this target.
        """
        n_pieces = len(message.message_pieces)
        if n_pieces != 1:
            raise ValueError(f"This target only supports a single message piece. Received: {n_pieces} pieces.")
