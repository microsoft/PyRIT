# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from collections.abc import MutableSequence
from typing import Any

from pyrit.exceptions import (
    EmptyResponseException,
    pyrit_target_retry,
)
from pyrit.models import (
    ComponentIdentifier,
    JsonResponseConfig,
    Message,
    MessagePiece,
)
from pyrit.prompt_target.common.chat_completions_message_builder import (
    build_multimodal_chat_messages_async,
    build_response_format,
    build_text_chat_messages,
    is_text_only_conversation,
    should_skip_audio_piece,
)
from pyrit.prompt_target.common.chat_completions_response_parser import (
    build_response_pieces_async,
    capture_token_usage,
    detect_response_content,
    extract_partial_content,
    is_content_filter_response,
    save_audio_response_async,
    validate_chat_completion_response,
)
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import (
    limit_requests_per_minute,
    validate_temperature,
    validate_top_p,
)
from pyrit.prompt_target.openai.openai_chat_audio_config import OpenAIChatAudioConfig
from pyrit.prompt_target.openai.openai_target import OpenAITarget

logger = logging.getLogger(__name__)


class OpenAIChatTarget(OpenAITarget):
    """
    Facilitates multimodal (image and text) input and text output generation.

    This works with GPT3.5, GPT4, GPT4o, GPT-V, and other compatible models

    Args:
        api_key (str): The api key for the OpenAI API
        endpoint (str): The endpoint for the OpenAI API
        model_name (str): The model name for the OpenAI API (or deployment name in Azure)
        temperature (float): The temperature for the completion
        max_completion_tokens (int): The maximum number of tokens to be returned by the model.
            The total length of input tokens and generated tokens is limited by
            the model's context length.
        top_p (float): The nucleus sampling probability.
        frequency_penalty (float): Number between -2.0 and 2.0. Positive values
            penalize new tokens based on their existing frequency in the text so far,
            decreasing the model's likelihood to repeat the same line verbatim.
        presence_penalty (float): Number between -2.0 and 2.0. Positive values
            penalize new tokens based on whether they appear in the text so far,
            increasing the model's likelihood to talk about new topics.
        seed (int): This feature is in Beta. If specified, our system will make a best effort to sample
            deterministically, such that repeated requests with the same seed
            and parameters should return the same result.
        n (int): How many chat completion choices to generate for each input message.
            Note that you will be charged based on the number of generated tokens across all
            of the choices. Keep n as 1 to minimize costs.
        extra_body_parameters (dict): Additional parameters to send in the request body

    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
            supports_json_output=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
            input_modalities=frozenset(
                {frozenset({"text"}), frozenset({"image_path"}), frozenset({"text", "image_path"})}
            ),
        )
    )

    def __init__(
        self,
        *,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        seed: int | None = None,
        n: int | None = None,
        audio_response_config: OpenAIChatAudioConfig | None = None,
        extra_body_parameters: dict[str, Any] | None = None,
        custom_configuration: TargetConfiguration | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the target.

        Args:
            model_name (str, Optional): The name of the model.
                If no value is provided, the OPENAI_CHAT_MODEL environment variable will be used.
            endpoint (str, Optional): The target URL for the OpenAI service.
            api_key (str | Callable[[], str], Optional): The API key for accessing the OpenAI service,
                or a callable that returns an access token. For Azure endpoints with Entra authentication,
                pass a token provider from pyrit.auth (e.g., get_azure_openai_auth(endpoint)).
                Defaults to the `OPENAI_CHAT_KEY` environment variable.
            headers (str, Optional): Headers of the endpoint (JSON).
            max_requests_per_minute (int, Optional): Number of requests the target can handle per
                minute before hitting a rate limit. The number of requests sent to the target
                will be capped at the value provided.
            max_completion_tokens (int, Optional): An upper bound for the number of tokens that
                can be generated for a completion, including visible output tokens and
                reasoning tokens.
                NOTE: Specify this value when using an o1 series model.
            temperature (float, Optional): The temperature parameter for controlling the
                randomness of the response.
            top_p (float, Optional): The top-p parameter for controlling the diversity of the
                response.
            frequency_penalty (float, Optional): The frequency penalty parameter for penalizing
                frequently generated tokens.
            presence_penalty (float, Optional): The presence penalty parameter for penalizing
                tokens that are already present in the conversation history.
            seed (int, Optional): If specified, openAI will make a best effort to sample deterministically,
                such that repeated requests with the same seed and parameters should return the same result.
            n (int, Optional): The number of completions to generate for each prompt.
            audio_response_config (OpenAIChatAudioConfig, Optional): Configuration for audio output from models
                that support it (e.g., gpt-4o-audio-preview). When provided, enables audio modality in responses.
            extra_body_parameters (dict, Optional): Additional parameters to be included in the request body.
            custom_configuration (TargetConfiguration, Optional): Override the default target configuration.
            **kwargs: Additional keyword arguments passed to the parent OpenAITarget class.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the ``httpx.AsyncClient()``
                constructor. For example, to specify a 3 minute timeout: ``httpx_client_kwargs={"timeout": 180}``

        Raises:
            PyritException: If the temperature or top_p values are out of bounds.
            ValueError: If the temperature is not between 0 and 2 (inclusive).
            ValueError: If the top_p is not between 0 and 1 (inclusive).
            RateLimitException: If the target is rate-limited.
            httpx.HTTPStatusError: If the request fails with a 400 Bad Request or 429 Too Many Requests error.
            json.JSONDecodeError: If the response from the target is not valid JSON.
            Exception: If the request fails for any other reason.
        """
        super().__init__(custom_configuration=custom_configuration, **kwargs)

        # Validate temperature and top_p
        validate_temperature(temperature)
        validate_top_p(top_p)

        self._temperature = temperature
        self._top_p = top_p
        self._max_completion_tokens = max_completion_tokens
        self._frequency_penalty = frequency_penalty
        self._presence_penalty = presence_penalty
        self._seed = seed
        self._n = n
        self._audio_response_config = audio_response_config

        # Merge audio config into extra_body_parameters if provided
        if audio_response_config:
            audio_params = audio_response_config.to_extra_body_parameters()
            extra_body_parameters = {**audio_params, **extra_body_parameters} if extra_body_parameters else audio_params

        self._extra_body_parameters = extra_body_parameters

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with OpenAI chat-specific parameters.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "temperature": self._temperature,
                "top_p": self._top_p,
                "max_completion_tokens": self._max_completion_tokens,
                "frequency_penalty": self._frequency_penalty,
                "presence_penalty": self._presence_penalty,
                "seed": self._seed,
                "n": self._n,
            },
        )

    def _set_openai_env_configuration_vars(self) -> None:
        """
        Set deployment_environment_variable, endpoint_environment_variable,
        and api_key_environment_variable which are read from .env file.
        """
        self.model_name_environment_variable = "OPENAI_CHAT_MODEL"
        self.endpoint_environment_variable = "OPENAI_CHAT_ENDPOINT"
        self.api_key_environment_variable = "OPENAI_CHAT_KEY"

    def _get_target_api_paths(self) -> list[str]:
        """Return API paths that should not be in the URL."""
        return ["/chat/completions", "/v1/chat/completions"]

    def _get_provider_examples(self) -> dict[str, str]:
        """Return provider-specific example URLs."""
        return {
            ".openai.azure.com": "https://{resource}.openai.azure.com/openai/v1",
            "api.openai.com": "https://api.openai.com/v1",
            "api.anthropic.com": "https://api.anthropic.com/v1",
            "generativelanguage.googleapis.com": "https://generativelanguage.googleapis.com/v1beta/openai",
        }

    @limit_requests_per_minute
    @pyrit_target_retry
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Asynchronously sends a message and handles the response within a managed conversation context.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: A list containing the response from the prompt target.
        """
        message = normalized_conversation[-1]
        message_piece: MessagePiece = message.message_pieces[0]
        json_config = self._get_json_response_config(message_piece=message_piece)

        logger.info(f"Sending the following prompt to the prompt target: {message}")

        body = await self._construct_request_body_async(conversation=normalized_conversation, json_config=json_config)

        # Use unified error handling - automatically detects ChatCompletion and validates
        response = await self._handle_openai_request_async(
            api_call=lambda: self._client.chat.completions.create(**body),
            request=message,
        )
        return [response]

    def _check_content_filter(self, response: Any) -> bool:
        """
        Check if a Chat Completions API response has finish_reason=content_filter.

        Args:
            response: A ChatCompletion object from the OpenAI SDK.

        Returns:
            True if content was filtered, False otherwise.
        """
        return is_content_filter_response(response)

    def _extract_partial_content(self, response: Any) -> str | None:
        """
        Extract partial content from a Chat Completions response with finish_reason=content_filter.

        When Azure Content Safety triggers mid-generation, the model may have produced partial
        text in ``response.choices[0].message.content`` before being cut off.

        Args:
            response: A ChatCompletion object from the OpenAI SDK.

        Returns:
            The partial text content, or None if no content was generated.
        """
        return extract_partial_content(response)

    def _validate_response(self, response: Any, request: MessagePiece) -> Message | None:
        """
        Validate a Chat Completions API response for errors.

        Checks for:
        - Missing choices
        - Invalid finish_reason
        - At least one valid response type (text content, audio, or tool_calls)

        Args:
            response: The ChatCompletion response from OpenAI SDK.
            request: The original request MessagePiece.

        Returns:
            None if valid, does not return Message for content filter (handled by _check_content_filter).

        Raises:
            PyritException: For unexpected response structures or finish reasons.
            EmptyResponseException: When the API returns an empty response.
        """
        validate_chat_completion_response(response=response)
        return None

    def _detect_response_content(self, message: Any) -> tuple[bool, bool, bool]:
        """
        Detect what content types are present in a ChatCompletion message.

        Args:
            message: The message object from response.choices[0].message.

        Returns:
            Tuple of (has_content, has_audio, has_tool_calls) booleans.
        """
        return detect_response_content(message)

    def _should_skip_sending_audio(
        self,
        *,
        message_piece: MessagePiece,
        is_last_message: bool,
        has_text_piece: bool,
    ) -> bool:
        """
        Determine if an audio_path piece should be skipped when building chat messages.

        Args:
            message_piece: The MessagePiece to evaluate.
            is_last_message: Whether this is the last (current) message in the conversation.
            has_text_piece: Whether the message contains a text piece (e.g., transcript).

        Returns:
            True if the audio should be skipped, False if it should be included.
        """
        if message_piece.converted_value_data_type != "audio_path":
            return False

        prefer_transcript_for_history = bool(
            self._audio_response_config and self._audio_response_config.prefer_transcript_for_history
        )
        return should_skip_audio_piece(
            message_piece=message_piece,
            is_last_message=is_last_message,
            has_text_piece=has_text_piece,
            prefer_transcript_for_history=prefer_transcript_for_history,
        )

    async def _construct_message_from_response_async(self, response: Any, request: MessagePiece) -> Message:
        """
        Construct a Message from a ChatCompletion response.

        Handles multiple response types:
        - Text content from message.content
        - Audio transcript and audio file from message.audio
        - Tool calls serialized as JSON from message.tool_calls

        Args:
            response: The ChatCompletion response from OpenAI SDK.
            request: The original request MessagePiece.

        Returns:
            Message: Constructed message with one or more MessagePiece entries.

        Raises:
            EmptyResponseException: If the response contains no content, audio, or tool calls.
        """
        audio_format = self._audio_response_config.audio_format if self._audio_response_config else "wav"
        pieces = await build_response_pieces_async(response=response, request=request, audio_format=audio_format)

        if not pieces:
            raise EmptyResponseException(message="Failed to extract any response content.")

        # Capture token usage from the API response and store in the first piece's metadata
        capture_token_usage(pieces=pieces, response=response)

        return Message(message_pieces=pieces)

    async def _save_audio_response_async(self, *, audio_data_base64: str) -> str:
        """
        Save audio data from an OpenAI audio response to a file.

        Args:
            audio_data_base64: Base64-encoded audio data from message.audio.data.

        Returns:
            str: The file path where the audio was saved.
        """
        audio_format = self._audio_response_config.audio_format if self._audio_response_config else "wav"
        return await save_audio_response_async(audio_data_base64=audio_data_base64, audio_format=audio_format)

    async def _build_chat_messages_async(self, conversation: MutableSequence[Message]) -> list[dict[str, Any]]:
        """
        Build chat messages based on message entries.

        Args:
            conversation (list[Message]): A list of Message objects.

        Returns:
            list[dict]: The list of constructed chat messages.
        """
        if self._is_text_message_format(conversation):
            return self._build_chat_messages_for_text(conversation)
        return await self._build_chat_messages_for_multi_modal_async(conversation)

    def _is_text_message_format(self, conversation: MutableSequence[Message]) -> bool:
        """
        Check if the message piece is in text message format.

        Args:
            conversation (list[Message]): The conversation

        Returns:
            bool: True if the message piece is in text message format, False otherwise.
        """
        return is_text_only_conversation(conversation)

    def _build_chat_messages_for_text(self, conversation: MutableSequence[Message]) -> list[dict[str, Any]]:
        """
        Build chat messages based on message entries. This is needed because many
        openai "compatible" models don't support multi-part content format (this is more universally accepted).

        Args:
            conversation (list[Message]): A list of Message objects.

        Returns:
            list[dict]: The list of constructed chat messages.

        Raises:
            ValueError: If any message does not have exactly one text piece.
            ValueError: If any message piece is not of type text.
        """
        return build_text_chat_messages(conversation)

    async def _build_chat_messages_for_multi_modal_async(
        self, conversation: MutableSequence[Message]
    ) -> list[dict[str, Any]]:
        """
        Build chat messages based on message entries.

        Args:
            conversation (list[Message]): A list of Message objects.

        Returns:
            list[dict]: The list of constructed chat messages.

        Raises:
            ValueError: If any message does not have a role.
            ValueError: If any message piece has an unsupported data type.
        """
        prefer_transcript_for_history = bool(
            self._audio_response_config and self._audio_response_config.prefer_transcript_for_history
        )
        return await build_multimodal_chat_messages_async(
            conversation, prefer_transcript_for_history=prefer_transcript_for_history
        )

    async def _construct_request_body_async(
        self, *, conversation: MutableSequence[Message], json_config: JsonResponseConfig
    ) -> dict[str, Any]:
        messages = await self._build_chat_messages_async(conversation)
        response_format = self._build_response_format(json_config)

        body_parameters = {
            "model": self._model_name,
            "max_completion_tokens": self._max_completion_tokens,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "frequency_penalty": self._frequency_penalty,
            "presence_penalty": self._presence_penalty,
            "stream": False,
            "seed": self._seed,
            "n": self._n,
            "messages": messages,
            "response_format": response_format,
        }

        if self._extra_body_parameters:
            body_parameters.update(self._extra_body_parameters)

        # Filter out None values
        return {k: v for k, v in body_parameters.items() if v is not None}

    def _build_response_format(self, json_config: JsonResponseConfig) -> dict[str, Any] | None:
        return build_response_format(json_config=json_config)
