# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Any

from pyrit.exceptions import (
    pyrit_target_retry,
)
from pyrit.models import (
    Message,
    MessagePiece,
    construct_response_from_request,
    data_serializer_factory,
)
from pyrit.prompt_target.common.utils import limit_requests_per_minute
from pyrit.prompt_target.openai.openai_error_handling import _is_content_filter_error
from pyrit.prompt_target.openai.openai_target import OpenAITarget

logger = logging.getLogger(__name__)


class OpenAIVideoTarget(OpenAITarget):
    """
    OpenAI Video Target using the OpenAI SDK for video generation.

    Supports Sora-2 and Sora-2-Pro models via the OpenAI videos API.

    Supported resolutions:
    - Sora-2: 720x1280, 1280x720
    - Sora-2-Pro: 720x1280, 1280x720, 1024x1792, 1792x1024

    Supported durations: 4, 8, or 12 seconds

    Default: resolution="1280x720", duration=4 seconds
    """

    SUPPORTED_RESOLUTIONS = ["720x1280", "1280x720", "1024x1792", "1792x1024"]
    SUPPORTED_DURATIONS = [4, 8, 12]

    def __init__(
        self,
        *,
        resolution_dimensions: str = "1280x720",
        n_seconds: int = 4,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the OpenAI Video Target.

        Args:
            model_name (str, Optional): The video model to use (e.g., "sora-2", "sora-2-pro")
                (or deployment name in Azure). If no value is provided, the OPENAI_VIDEO_MODEL
                environment variable will be used.
            endpoint (str, Optional): The target URL for the OpenAI service.
            api_key (str | Callable[[], str], Optional): The API key for accessing the OpenAI service,
                or a callable that returns an access token. For Azure endpoints with Entra authentication,
                pass a token provider from pyrit.auth (e.g., get_azure_openai_auth(endpoint)).
                Uses OPENAI_VIDEO_KEY environment variable by default.
            headers (str, Optional): Extra headers of the endpoint (JSON).
            max_requests_per_minute (int, Optional): Number of requests the target can handle per
                minute before hitting a rate limit.
            resolution_dimensions (str, Optional): Resolution dimensions for the video in WIDTHxHEIGHT format.
                Defaults to "1280x720".
                Supported resolutions:
                - Sora-2: "720x1280", "1280x720"
                - Sora-2-Pro: "720x1280", "1280x720", "1024x1792", "1792x1024"
            n_seconds (int, Optional): The duration of the generated video (in seconds).
                Defaults to 4. Supported values: 4, 8, or 12 seconds.
            **kwargs: Additional keyword arguments passed to the parent OpenAITarget class.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the ``httpx.AsyncClient()``
                constructor. For example, to specify a 3 minute timeout: ``httpx_client_kwargs={"timeout": 180}``
        """
        super().__init__(**kwargs)

        self._n_seconds = n_seconds
        self._validate_duration()
        self._size = self._validate_resolution(resolution_dimensions=resolution_dimensions)

    def _set_openai_env_configuration_vars(self) -> None:
        """Set environment variable names."""
        self.model_name_environment_variable = "OPENAI_VIDEO_MODEL"
        self.endpoint_environment_variable = "OPENAI_VIDEO_ENDPOINT"
        self.api_key_environment_variable = "OPENAI_VIDEO_KEY"
        self.underlying_model_environment_variable = "OPENAI_VIDEO_UNDERLYING_MODEL"

    def _get_target_api_paths(self) -> list[str]:
        """Return API paths that should not be in the URL."""
        return ["/videos", "/v1/videos"]

    def _get_provider_examples(self) -> dict[str, str]:
        """Return provider-specific example URLs."""
        return {
            ".openai.azure.com": "https://{resource}.openai.azure.com/openai/v1",
            "api.openai.com": "https://api.openai.com/v1",
        }

    def _validate_resolution(self, *, resolution_dimensions: str) -> str:
        """
        Validate resolution dimensions.

        Args:
            resolution_dimensions: Resolution in WIDTHxHEIGHT format.

        Returns:
            The validated resolution string.

        Raises:
            ValueError: If the resolution is not supported.
        """
        if resolution_dimensions not in self.SUPPORTED_RESOLUTIONS:
            raise ValueError(
                f"Invalid resolution '{resolution_dimensions}'. "
                f"Supported resolutions: {', '.join(self.SUPPORTED_RESOLUTIONS)}"
            )
        return resolution_dimensions

    def _validate_duration(self) -> None:
        """
        Validate video duration.

        Raises:
            ValueError: If the duration is not supported.
        """
        if self._n_seconds not in self.SUPPORTED_DURATIONS:
            raise ValueError(
                f"Invalid duration {self._n_seconds}s. "
                f"Supported durations: {', '.join(map(str, self.SUPPORTED_DURATIONS))} seconds"
            )

    @limit_requests_per_minute
    @pyrit_target_retry
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Asynchronously sends a message and generates a video using the OpenAI SDK.

        Args:
            message (Message): The message object containing the prompt.

        Returns:
            list[Message]: A list containing the response with the generated video path.

        Raises:
            RateLimitException: If the rate limit is exceeded.
            ValueError: If the request is invalid.
        """
        self._validate_request(message=message)
        message_piece = message.message_pieces[0]
        prompt = message_piece.converted_value

        logger.info(f"Sending video generation prompt: {prompt}")

        # Use unified error handler - automatically detects Video and validates
        response = await self._handle_openai_request(
            api_call=lambda: self._async_client.videos.create_and_poll(
                model=self._model_name,
                prompt=prompt,
                size=self._size,  # type: ignore[arg-type]
                seconds=str(self._n_seconds),  # type: ignore[arg-type]
            ),
            request=message,
        )
        return [response]

    def _check_content_filter(self, response: Any) -> bool:
        """
        Check if a video generation response was content filtered.

        Response indicates content filtering through:
        - Status is "failed"
        - Error code is "content_filter" (output-side filtering)
        - Error code is "moderation_blocked" (input moderation)

        Note: Input-side filtering (content_policy_violation via BadRequestError) is also caught
        by the base class before reaching this method.

        Args:
            response: A Video object from the OpenAI SDK.

        Returns:
            True if content was filtered, False otherwise.
        """
        if response.status == "failed" and response.error:
            # Convert response to dict and use common filter detection
            response_dict = response.model_dump()
            return _is_content_filter_error(response_dict)
        return False

    async def _construct_message_from_response(self, response: Any, request: Any) -> Message:
        """
        Construct a Message from a video response.

        Args:
            response: The Video response from OpenAI SDK.
            request: The original request MessagePiece.

        Returns:
            Message: Constructed message with video file path.
        """
        video = response

        # Check if video generation was successful
        if video.status == "completed":
            logger.info(f"Video generation completed successfully: {video.id}")

            # Download video content using SDK
            video_response = await self._async_client.videos.download_content(video.id)
            # Extract bytes from HttpxBinaryResponseContent
            video_content = video_response.content

            # Save the video to storage
            return await self._save_video_response(request=request, video_data=video_content)

        elif video.status == "failed":
            # Handle failed video generation (non-content-filter)
            error_message = str(video.error) if video.error else "Video generation failed"
            logger.error(f"Video generation failed: {error_message}")

            # Non-content-filter errors are returned as processing errors
            return construct_response_from_request(
                request=request,
                response_text_pieces=[error_message],
                response_type="error",
                error="processing",
            )
        else:
            # Unexpected status
            error_message = f"Video generation ended with unexpected status: {video.status}"
            logger.error(error_message)
            return construct_response_from_request(
                request=request,
                response_text_pieces=[error_message],
                response_type="error",
                error="unknown",
            )

    async def _save_video_response(self, *, request: MessagePiece, video_data: bytes) -> Message:
        """
        Save video data to storage and construct response.

        Args:
            request: The original request message piece.
            video_data: The video content as bytes.

        Returns:
            Message: The response with the video file path.
        """
        # Save video using data serializer
        data = data_serializer_factory(category="prompt-memory-entries", data_type="video_path")
        await data.save_data(data=video_data)
        video_path = data.value

        logger.info(f"Video saved to: {video_path}")

        # Construct response
        response_entry = construct_response_from_request(
            request=request,
            response_text_pieces=[video_path],
            response_type="video_path",
        )

        return response_entry

    def _validate_request(self, *, message: Message) -> None:
        """
        Validate the request message.

        Args:
            message: The message to validate.

        Raises:
            ValueError: If the request is invalid.
        """
        n_pieces = len(message.message_pieces)
        if n_pieces != 1:
            raise ValueError(f"This target only supports a single message piece. Received: {n_pieces} pieces.")

        piece_type = message.message_pieces[0].converted_value_data_type
        if piece_type != "text":
            raise ValueError(f"This target only supports text prompt input. Received: {piece_type}.")

    def is_json_response_supported(self) -> bool:
        """
        Check if the target supports JSON response data.

        Returns:
            bool: False, as video generation doesn't return JSON content.
        """
        return False
