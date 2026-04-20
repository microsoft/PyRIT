# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import base64
import logging
from typing import Any, Dict, Literal, Optional

import httpx

from pyrit.exceptions import (
    EmptyResponseException,
    pyrit_target_retry,
)
from pyrit.models import (
    Message,
    construct_response_from_request,
    data_serializer_factory,
)
from pyrit.prompt_target.common.utils import limit_requests_per_minute
from pyrit.prompt_target.openai.openai_target import OpenAITarget

logger = logging.getLogger(__name__)


class OpenAIImageTarget(OpenAITarget):
    """A target for image generation using OpenAI's image models."""

    def __init__(
        self,
        image_size: Literal["256x256", "512x512", "1024x1024", "1536x1024", "1024x1536"] = "1024x1024",
        quality: Optional[Literal["standard", "hd", "low", "medium", "high"]] = None,
        style: Optional[Literal["natural", "vivid"]] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the image target with specified parameters.

        Args:
            model_name (str, Optional): The name of the model (or deployment name in Azure).
                If no value is provided, the OPENAI_IMAGE_MODEL environment variable will be used.
            endpoint (str, Optional): The target URL for the OpenAI service.
            api_key (str | Callable[[], str], Optional): The API key for accessing the OpenAI service,
                or a callable that returns an access token. For Azure endpoints with Entra authentication,
                pass a token provider from pyrit.auth (e.g., get_azure_openai_auth(endpoint)).
                Defaults to the `OPENAI_IMAGE_API_KEY` environment variable.
            headers (str, Optional): Headers of the endpoint (JSON).
            max_requests_per_minute (int, Optional): Number of requests the target can handle per
                minute before hitting a rate limit. The number of requests sent to the target
                will be capped at the value provided.
            image_size (Literal["256x256", "512x512", "1024x1024"], Optional): The size of the generated images.
                Defaults to "1024x1024".
            quality (Literal["standard", "hd", "low", "medium", "high"], Optional): The quality of the generated images.
                Different models support different quality settings.
                For DALL-E-3, there's "standard" and "hd".
                For newer models, there are "low", "medium", and "high".
                Default is to not specify.
            style (Literal["natural", "vivid"], Optional): The style of the generated images.
                Default is to not specify.
            *args: Additional positional arguments to be passed to AzureOpenAITarget.
            **kwargs: Additional keyword arguments to be passed to AzureOpenAITarget.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the
                `httpx.AsyncClient()` constructor.
                For example, to specify a 3 minutes timeout: httpx_client_kwargs={"timeout": 180}
        """
        self.quality = quality
        self.style = style
        self.image_size = image_size

        super().__init__(*args, **kwargs)

    def _set_openai_env_configuration_vars(self) -> None:
        self.model_name_environment_variable = "OPENAI_IMAGE_MODEL"
        self.endpoint_environment_variable = "OPENAI_IMAGE_ENDPOINT"
        self.api_key_environment_variable = "OPENAI_IMAGE_API_KEY"
        self.underlying_model_environment_variable = "OPENAI_IMAGE_UNDERLYING_MODEL"

    def _get_target_api_paths(self) -> list[str]:
        """Return API paths that should not be in the URL."""
        return ["/images/generations", "/v1/images/generations"]

    def _get_provider_examples(self) -> dict[str, str]:
        """Return provider-specific example URLs."""
        return {
            ".openai.azure.com": "https://{resource}.openai.azure.com/openai/v1",
            "api.openai.com": "https://api.openai.com/v1",
        }

    @limit_requests_per_minute
    @pyrit_target_retry
    async def send_prompt_async(
        self,
        *,
        message: Message,
    ) -> list[Message]:
        """
        Send a prompt to the DALL-E target and return the response.

        Args:
            message (Message): The message to send.

        Returns:
            list[Message]: A list containing the response from the image target.
        """
        self._validate_request(message=message)
        message_piece = message.message_pieces[0]

        logger.info(f"Sending the following prompt to the prompt target: {message_piece}")

        # Construct request parameters
        image_generation_args: Dict[str, Any] = {
            "model": self._model_name,
            "prompt": message_piece.converted_value,
            "size": self.image_size,
        }

        if self.quality:
            image_generation_args["quality"] = self.quality
        if self.style:
            image_generation_args["style"] = self.style

        # Use unified error handler for consistent error handling
        response = await self._handle_openai_request(
            api_call=lambda: self._async_client.images.generate(**image_generation_args),
            request=message,
        )
        return [response]

    async def _construct_message_from_response(self, response: Any, request: Any) -> Message:
        """
        Construct a Message from an ImagesResponse.

        Args:
            response: The ImagesResponse from OpenAI SDK.
            request: The original request MessagePiece.

        Returns:
            Message: Constructed message with image path.

        Raises:
            EmptyResponseException: If the image generation returned an empty response.
        """
        image_data = response.data[0]
        image_bytes = await self._get_image_bytes(image_data)

        data = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
        await data.save_data(data=image_bytes)

        return construct_response_from_request(
            request=request, response_text_pieces=[data.value], response_type="image_path"
        )

    async def _get_image_bytes(self, image_data: Any) -> bytes:
        """
        Extract image bytes from the API response.

        Handles both base64-encoded data and URL responses. Some models (like gpt-image-1)
        return base64 directly, while others (like dall-e) may return URLs.

        Args:
            image_data: The image data object from the API response.

        Returns:
            bytes: The raw image bytes.

        Raises:
            EmptyResponseException: If neither base64 data nor URL is available.
        """
        # Try base64 first (preferred format)
        b64_data = getattr(image_data, "b64_json", None)
        if b64_data:
            return base64.b64decode(b64_data)

        # Fall back to URL download
        image_url = getattr(image_data, "url", None)
        if image_url:
            logger.info("Image model returned URL. Downloading image.")
            async with httpx.AsyncClient() as http_client:
                image_response = await http_client.get(image_url)
                image_response.raise_for_status()
                return image_response.content

        raise EmptyResponseException(message="The image generation returned an empty response.")

    def _validate_request(self, *, message: Message) -> None:
        n_pieces = len(message.message_pieces)
        if n_pieces != 1:
            raise ValueError(f"This target only supports a single message piece. Received: {n_pieces} pieces.")

        piece_type = message.message_pieces[0].converted_value_data_type
        if piece_type != "text":
            raise ValueError(f"This target only supports text prompt input. Received: {piece_type}.")

    def is_json_response_supported(self) -> bool:
        """
        Check if the target supports JSON as a response format.

        Returns:
            bool: True if JSON response is supported, False otherwise.
        """
        return False
