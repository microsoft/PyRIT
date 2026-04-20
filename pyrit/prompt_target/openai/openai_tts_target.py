# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Any, Literal, Optional

from pyrit.exceptions import (
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

TTSModel = Literal["tts-1", "tts-1-hd"]
TTSVoice = Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
TTSResponseFormat = Literal["flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "wav", "webm"]


class OpenAITTSTarget(OpenAITarget):
    """A prompt target for OpenAI Text-to-Speech (TTS) endpoints."""

    def __init__(
        self,
        *,
        voice: TTSVoice = "alloy",
        response_format: TTSResponseFormat = "mp3",
        language: str = "en",
        speed: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the TTS target with specified parameters.

        Args:
            model_name (str, Optional): The name of the model (or deployment name in Azure).
                If no value is provided, the OPENAI_TTS_MODEL environment variable will be used.
            endpoint (str, Optional): The target URL for the OpenAI service.
            api_key (str | Callable[[], str], Optional): The API key for accessing the OpenAI service,
                or a callable that returns an access token. For Azure endpoints with Entra authentication,
                pass a token provider from pyrit.auth (e.g., get_azure_openai_auth(endpoint)).
                Defaults to the `OPENAI_TTS_KEY` environment variable.
            headers (str, Optional): Headers of the endpoint (JSON).
            max_requests_per_minute (int, Optional): Number of requests the target can handle per
                minute before hitting a rate limit. The number of requests sent to the target
                will be capped at the value provided.
            voice (str, Optional): The voice to use for TTS. Defaults to "alloy".
            response_format (str, Optional): The format of the audio response. Defaults to "mp3".
            language (str): The language for TTS. Defaults to "en".
            speed (float, Optional): The speed of the TTS. Select a value from 0.25 to 4.0. 1.0 is normal.
            **kwargs: Additional keyword arguments passed to the parent OpenAITarget class.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the ``httpx.AsyncClient()``
                constructor. For example, to specify a 3 minute timeout: ``httpx_client_kwargs={"timeout": 180}``
        """
        super().__init__(**kwargs)

        self._voice = voice
        self._response_format = response_format
        self._language = language
        self._speed = speed

    def _set_openai_env_configuration_vars(self) -> None:
        self.model_name_environment_variable = "OPENAI_TTS_MODEL"
        self.endpoint_environment_variable = "OPENAI_TTS_ENDPOINT"
        self.api_key_environment_variable = "OPENAI_TTS_KEY"
        self.underlying_model_environment_variable = "OPENAI_TTS_UNDERLYING_MODEL"

    def _get_target_api_paths(self) -> list[str]:
        """Return API paths that should not be in the URL."""
        return ["/audio/speech", "/v1/audio/speech"]

    def _get_provider_examples(self) -> dict[str, str]:
        """Return provider-specific example URLs."""
        return {
            ".openai.azure.com": "https://{resource}.openai.azure.com/openai/v1",
            "api.openai.com": "https://api.openai.com/v1",
        }

    @limit_requests_per_minute
    @pyrit_target_retry
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Asynchronously send a message to the OpenAI TTS target.

        Args:
            message (Message): The message object containing the prompt to send.

        Returns:
            list[Message]: A list containing the audio response from the prompt target.
        """
        self._validate_request(message=message)
        message_piece = message.message_pieces[0]

        logger.info(f"Sending the following prompt to the prompt target: {message_piece}")

        # Construct request parameters for SDK
        body_parameters: dict[str, object] = {
            "model": self._model_name,
            "input": message_piece.converted_value,
            "voice": self._voice,
            "response_format": self._response_format,
        }

        # Add optional parameters
        if self._speed is not None:
            body_parameters["speed"] = self._speed

        # Use unified error handler for consistent error handling
        response = await self._handle_openai_request(
            api_call=lambda: self._async_client.audio.speech.create(
                model=body_parameters["model"],  # type: ignore[arg-type]
                voice=body_parameters["voice"],  # type: ignore[arg-type]
                input=body_parameters["input"],  # type: ignore[arg-type]
                response_format=body_parameters.get("response_format"),  # type: ignore[arg-type]
                speed=body_parameters.get("speed"),  # type: ignore[arg-type]
            ),
            request=message,
        )
        return [response]

    async def _construct_message_from_response(self, response: Any, request: Any) -> Message:
        """
        Construct a Message from a TTS audio response.

        Args:
            response: The audio response from OpenAI SDK.
            request: The original request MessagePiece.

        Returns:
            Message: Constructed message with audio file path.
        """
        audio_bytes = response.content

        logger.info("Received valid response from the prompt target")

        audio_response = data_serializer_factory(
            category="prompt-memory-entries", data_type="audio_path", extension=self._response_format
        )

        await audio_response.save_data(data=audio_bytes)

        return construct_response_from_request(
            request=request, response_text_pieces=[str(audio_response.value)], response_type="audio_path"
        )

    def _validate_request(self, *, message: Message) -> None:
        n_pieces = len(message.message_pieces)
        if n_pieces != 1:
            raise ValueError(f"This target only supports a single message piece. Received: {n_pieces} pieces.")

        piece_type = message.message_pieces[0].converted_value_data_type
        if piece_type != "text":
            raise ValueError(f"This target only supports text prompt input. Received: {piece_type}.")

        request = message.message_pieces[0]
        messages = self._memory.get_conversation(conversation_id=request.conversation_id)

        n_messages = len(messages)
        if n_messages > 0:
            raise ValueError(
                "This target only supports a single turn conversation. "
                f"Received: {n_messages} messages which indicates a prior turn."
            )

    def is_json_response_supported(self) -> bool:
        """
        Check if the target supports JSON as a response format.

        Returns:
            bool: True if JSON response is supported, False otherwise.
        """
        return False
