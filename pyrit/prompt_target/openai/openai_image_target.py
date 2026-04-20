# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import base64
import logging
from typing import Any, Literal, Optional

import httpx

from pyrit.exceptions import (
    EmptyResponseException,
    pyrit_target_retry,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    Message,
    construct_response_from_request,
    data_serializer_factory,
)
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute
from pyrit.prompt_target.openai.openai_target import OpenAITarget

logger = logging.getLogger(__name__)


class OpenAIImageTarget(OpenAITarget):
    """A target for image generation or editing using OpenAI's image models."""

    # Maximum number of image inputs supported by the OpenAI image API
    _MAX_INPUT_IMAGES = 16
    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_message_pieces=True,
            input_modalities=frozenset(
                {
                    frozenset(["text"]),
                    frozenset(["image_path"]),
                    frozenset(["text", "image_path"]),
                }
            ),
            output_modalities=frozenset(
                {
                    frozenset(["image_path"]),
                }
            ),
        )
    )

    def __init__(
        self,
        image_size: Literal[
            "256x256", "512x512", "1024x1024", "1536x1024", "1024x1536", "1792x1024", "1024x1792"
        ] = "1024x1024",
        output_format: Optional[Literal["png", "jpeg", "webp"]] = None,
        quality: Optional[Literal["standard", "hd", "low", "medium", "high"]] = None,
        style: Optional[Literal["natural", "vivid"]] = None,
        custom_configuration: Optional[TargetConfiguration] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
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
            image_size (Literal, Optional): The size of the generated image.
                Accepts "256x256", "512x512", "1024x1024", "1536x1024",
                "1024x1536", "1792x1024", or "1024x1792".
                Different models support different image sizes.
                GPT image models support "1024x1024", "1536x1024" and "1024x1536".
                DALL-E-3 supports "1024x1024", "1792x1024" and "1024x1792".
                DALL-E-2 supports "256x256", "512x512" and "1024x1024".
                Defaults to "1024x1024".
            output_format (Literal["png", "jpeg", "webp"], Optional): The output format of the generated images.
                This parameter is only supported for GPT image models.
                Default is to not specify (which will use the model's default format, e.g. PNG for OpenAI image models).
            quality (Literal["standard", "hd", "low", "medium", "high"], Optional): The quality of the generated images.
                Different models support different quality settings.
                GPT image models support "high", "medium" and "low".
                DALL-E-3 supports "hd" and "standard".
                DALL-E-2 supports "standard" only.
                Default is to not specify.
            style (Literal["natural", "vivid"], Optional): The style of the generated images.
                This parameter is only supported for DALL-E-3.
                Default is to not specify.
            custom_configuration (TargetConfiguration, Optional): Override the default configuration for
                this target instance. Defaults to None.
            custom_capabilities (TargetCapabilities, Optional): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.14.0.
            *args: Additional positional arguments to be passed to AzureOpenAITarget.
            **kwargs: Additional keyword arguments to be passed to AzureOpenAITarget.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the
                `httpx.AsyncClient()` constructor.
                For example, to specify a 3 minutes timeout: httpx_client_kwargs={"timeout": 180}
        """
        self.output_format = output_format
        self.quality = quality
        self.style = style
        self.image_size = image_size

        super().__init__(
            *args, custom_configuration=custom_configuration, custom_capabilities=custom_capabilities, **kwargs
        )

    def _set_openai_env_configuration_vars(self) -> None:
        self.model_name_environment_variable = "OPENAI_IMAGE_MODEL"
        self.endpoint_environment_variable = "OPENAI_IMAGE_ENDPOINT"
        self.api_key_environment_variable = "OPENAI_IMAGE_API_KEY"

    def _get_target_api_paths(self) -> list[str]:
        """Return API paths that should not be in the URL."""
        return ["/images/generations", "/v1/images/generations", "/images/edits", "/v1/images/edits"]

    def _get_provider_examples(self) -> dict[str, str]:
        """Return provider-specific example URLs."""
        return {
            ".openai.azure.com": "https://{resource}.openai.azure.com/openai/v1",
            "api.openai.com": "https://api.openai.com/v1",
        }

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with image generation-specific parameters.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "image_size": self.image_size,
                "quality": self.quality,
                "style": self.style,
            },
        )

    @limit_requests_per_minute
    @pyrit_target_retry
    async def _send_prompt_to_target_async(
        self,
        *,
        normalized_conversation: list[Message],
    ) -> list[Message]:
        """
        Send a prompt to the OpenAI image target and return the response.
        Supports both image generation (text input) and image editing (text + images input).

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: A list containing the response from the image target.
        """
        message = normalized_conversation[-1]

        logger.info(f"Sending the following prompt to the prompt target: {message}")

        # Generation requests have only one message piece (text)
        # Editing requests have 2+ message pieces (text + images)
        is_editing_request = len(message.message_pieces) >= 2

        if is_editing_request:
            response = await self._send_edit_request_async(message)
        else:
            response = await self._send_generate_request_async(message)

        return [response]

    async def _send_generate_request_async(self, message: Message) -> Message:
        """
        Send a text-only prompt to generate a new image.

        Args:
            message (Message): The text message to send.

        Returns:
            Message: The response from the image target.
        """
        prompt = message.message_pieces[0].converted_value

        # Construct request parameters
        image_generation_args: dict[str, Any] = {
            "model": self._model_name,
            "prompt": prompt,
            "size": self.image_size,
        }

        if self.output_format:
            image_generation_args["output_format"] = self.output_format
        if self.quality:
            image_generation_args["quality"] = self.quality
        if self.style:
            image_generation_args["style"] = self.style

        # Use unified error handler for consistent error handling
        return await self._handle_openai_request(
            api_call=lambda: self._client.images.generate(**image_generation_args),
            request=message,
        )

    async def _send_edit_request_async(self, message: Message) -> Message:
        """
        Send a multimodal prompt (text + images) to edit an existing image.

        Args:
            message (Message): The text + images message to send.

        Returns:
            Message: The response from the image target.

        Raises:
            ValueError: If at least one image file cannot be opened.
        """
        # Extract text and images from message pieces
        text_pieces = [p for p in message.message_pieces if p.converted_value_data_type == "text"]
        text_prompt = text_pieces[0].converted_value

        image_paths = [p.converted_value for p in message.message_pieces if p.converted_value_data_type == "image_path"]
        image_files = []
        for image_path in image_paths:
            img_serializer = data_serializer_factory(
                category="prompt-memory-entries", value=image_path, data_type="image_path"
            )

            image_name = str(await img_serializer.get_data_filename())
            image_bytes = await img_serializer.read_data()
            image_type = img_serializer.get_mime_type(image_path)

            image_files.append((image_name, image_bytes, image_type))

        # Construct request parameters for image editing
        image_edit_args: dict[str, Any] = {
            "model": self._model_name,
            "image": image_files,
            "prompt": text_prompt,
            "size": self.image_size,
        }

        if self.output_format:
            image_edit_args["output_format"] = self.output_format
        if self.quality:
            image_edit_args["quality"] = self.quality
        if self.style:
            image_edit_args["style"] = self.style

        return await self._handle_openai_request(
            api_call=lambda: self._client.images.edit(**image_edit_args),
            request=message,
        )

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

        extension = self.output_format or "png"
        data = data_serializer_factory(
            category="prompt-memory-entries",
            data_type="image_path",
            extension=extension,
        )
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

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        super()._validate_request(normalized_conversation=normalized_conversation)
        message = normalized_conversation[-1]

        text_pieces = [p for p in message.message_pieces if p.converted_value_data_type == "text"]
        image_pieces = [p for p in message.message_pieces if p.converted_value_data_type == "image_path"]

        if len(text_pieces) != 1:
            raise ValueError(f"The message must contain exactly one text piece. Received: {len(text_pieces)}.")

        if len(image_pieces) > self._MAX_INPUT_IMAGES:
            raise ValueError(
                f"The message can contain up to {self._MAX_INPUT_IMAGES} image pieces. Received: {len(image_pieces)}."
            )
