# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import json
import logging
from typing import Any, Dict, MutableSequence, Optional

from pyrit.common import convert_local_image_to_data_url
from pyrit.exceptions import (
    EmptyResponseException,
    PyritException,
    pyrit_target_retry,
)
from pyrit.models import (
    ChatMessage,
    DataTypeSerializer,
    Message,
    MessagePiece,
    construct_response_from_request,
    data_serializer_factory,
)
from pyrit.models.json_response_config import _JsonResponseConfig
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.prompt_target.common.utils import limit_requests_per_minute, validate_temperature, validate_top_p
from pyrit.prompt_target.openai.openai_chat_audio_config import OpenAIChatAudioConfig
from pyrit.prompt_target.openai.openai_target import OpenAITarget

logger = logging.getLogger(__name__)


class OpenAIChatTarget(OpenAITarget, PromptChatTarget):
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
        max_tokens (int): Deprecated. Use max_completion_tokens instead
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

    def __init__(
        self,
        *,
        max_completion_tokens: Optional[int] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        seed: Optional[int] = None,
        n: Optional[int] = None,
        is_json_supported: bool = True,
        audio_response_config: Optional[OpenAIChatAudioConfig] = None,
        extra_body_parameters: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """
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
            max_tokens (int, Optional): The maximum number of tokens that can be
                generated in the chat completion. This value can be used to control
                costs for text generated via API.

                This value is now deprecated in favor of `max_completion_tokens`, and IS NOT
                COMPATIBLE with o1 series models.
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
            is_json_supported (bool, Optional): If True, the target will support formatting responses as JSON by
                setting the response_format header. Official OpenAI models all support this, but if you are using
                this target with different models, is_json_supported should be set correctly to avoid issues when
                using adversarial infrastructure (e.g. Crescendo scorers will set this flag).
            audio_response_config (OpenAIChatAudioConfig, Optional): Configuration for audio output from models
                that support it (e.g., gpt-4o-audio-preview). When provided, enables audio modality in responses.
            extra_body_parameters (dict, Optional): Additional parameters to be included in the request body.
            **kwargs: Additional keyword arguments passed to the parent OpenAITarget class.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the ``httpx.AsyncClient()``
                constructor. For example, to specify a 3 minute timeout: ``httpx_client_kwargs={"timeout": 180}``

        Raises:
            PyritException: If the temperature or top_p values are out of bounds.
            ValueError: If the temperature is not between 0 and 2 (inclusive).
            ValueError: If the top_p is not between 0 and 1 (inclusive).
            ValueError: If both `max_completion_tokens` and `max_tokens` are provided.
            RateLimitException: If the target is rate-limited.
            httpx.HTTPStatusError: If the request fails with a 400 Bad Request or 429 Too Many Requests error.
            json.JSONDecodeError: If the response from the target is not valid JSON.
            Exception: If the request fails for any other reason.
        """
        super().__init__(**kwargs)

        # Validate temperature and top_p
        validate_temperature(temperature)
        validate_top_p(top_p)

        if max_completion_tokens and max_tokens:
            raise ValueError("Cannot provide both max_tokens and max_completion_tokens.")

        self._temperature = temperature
        self._top_p = top_p
        self._is_json_supported = is_json_supported
        self._max_completion_tokens = max_completion_tokens
        self._max_tokens = max_tokens
        self._frequency_penalty = frequency_penalty
        self._presence_penalty = presence_penalty
        self._seed = seed
        self._n = n
        self._audio_response_config = audio_response_config

        # Merge audio config into extra_body_parameters if provided
        if audio_response_config:
            audio_params = audio_response_config.to_extra_body_parameters()
            if extra_body_parameters:
                extra_body_parameters = {**audio_params, **extra_body_parameters}
            else:
                extra_body_parameters = audio_params

        self._extra_body_parameters = extra_body_parameters

    def _set_openai_env_configuration_vars(self) -> None:
        """
        Set deployment_environment_variable, endpoint_environment_variable,
        and api_key_environment_variable which are read from .env file.
        """
        self.model_name_environment_variable = "OPENAI_CHAT_MODEL"
        self.endpoint_environment_variable = "OPENAI_CHAT_ENDPOINT"
        self.api_key_environment_variable = "OPENAI_CHAT_KEY"
        self.underlying_model_environment_variable = "OPENAI_CHAT_UNDERLYING_MODEL"

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
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Asynchronously sends a message and handles the response within a managed conversation context.

        Args:
            message (Message): The message object.

        Returns:
            list[Message]: A list containing the response from the prompt target.
        """
        self._validate_request(message=message)

        message_piece: MessagePiece = message.message_pieces[0]
        json_config = self._get_json_response_config(message_piece=message_piece)

        # Get conversation from memory and append the current message
        conversation = self._memory.get_conversation(conversation_id=message_piece.conversation_id)
        conversation.append(message)

        logger.info(f"Sending the following prompt to the prompt target: {message}")

        body = await self._construct_request_body(conversation=conversation, json_config=json_config)

        # Use unified error handling - automatically detects ChatCompletion and validates
        response = await self._handle_openai_request(
            api_call=lambda: self._async_client.chat.completions.create(**body),
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
        try:
            if response.choices and response.choices[0].finish_reason == "content_filter":
                return True
        except (AttributeError, IndexError):
            pass
        return False

    def _validate_response(self, response: Any, request: MessagePiece) -> Optional[Message]:
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
        # Check for missing choices
        if not hasattr(response, "choices") or not response.choices:
            raise PyritException(message="No choices returned in the completion response.")

        choice = response.choices[0]
        finish_reason = choice.finish_reason

        # Check finish_reason (content_filter is handled by _check_content_filter)
        # "tool_calls" is valid when the model invokes functions
        valid_finish_reasons = ["stop", "length", "content_filter", "tool_calls"]
        if finish_reason not in valid_finish_reasons:
            raise PyritException(
                message=f"Unknown finish_reason {finish_reason} from response: {response.model_dump_json()}"
            )

        # Check for at least one valid response type
        has_content, has_audio, has_tool_calls = self._detect_response_content(choice.message)

        if not (has_content or has_audio or has_tool_calls):
            logger.error("The chat returned an empty response (no content, audio, or tool_calls).")
            raise EmptyResponseException(
                message="The chat returned an empty response (no content, audio, or tool_calls)."
            )

        return None

    def _detect_response_content(self, message: Any) -> tuple[bool, bool, bool]:
        """
        Detect what content types are present in a ChatCompletion message.

        Args:
            message: The message object from response.choices[0].message.

        Returns:
            Tuple of (has_content, has_audio, has_tool_calls) booleans.
        """
        has_content = bool(message.content)
        has_audio = hasattr(message, "audio") and message.audio is not None
        has_tool_calls = hasattr(message, "tool_calls") and message.tool_calls
        return has_content, has_audio, has_tool_calls

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

        api_role = message_piece.api_role

        # Skip audio for assistant messages - OpenAI only allows audio in user messages.
        # For assistant responses, the transcript text piece should already be included.
        if api_role == "assistant":
            return True

        # Skip historical user audio if prefer_transcript_for_history is enabled and we have a transcript
        if (
            api_role == "user"
            and not is_last_message
            and has_text_piece
            and self._audio_response_config
            and self._audio_response_config.prefer_transcript_for_history
        ):
            return True

        return False

    async def _construct_message_from_response(self, response: Any, request: MessagePiece) -> Message:
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
        message = response.choices[0].message
        has_content, has_audio, has_tool_calls = self._detect_response_content(message)

        pieces: list[MessagePiece] = []

        # Handle text content
        if has_content:
            text_piece = construct_response_from_request(
                request=request,
                response_text_pieces=[message.content],
                response_type="text",
            ).message_pieces[0]
            pieces.append(text_piece)

        # Handle audio response (transcript + saved audio file)
        if has_audio:
            audio_response = message.audio

            # Add transcript as text piece with metadata
            audio_transcript: Optional[str] = getattr(audio_response, "transcript", None)
            if audio_transcript:
                transcript_piece = construct_response_from_request(
                    request=request,
                    response_text_pieces=[audio_transcript],
                    response_type="text",
                    prompt_metadata={"transcription": "audio"},
                ).message_pieces[0]
                pieces.append(transcript_piece)

            # Save audio data and add as audio_path piece
            audio_data: Optional[str] = getattr(audio_response, "data", None)
            if audio_data:
                audio_path = await self._save_audio_response_async(audio_data_base64=audio_data)
                audio_piece = construct_response_from_request(
                    request=request,
                    response_text_pieces=[audio_path],
                    response_type="audio_path",
                ).message_pieces[0]
                pieces.append(audio_piece)

        # Handle tool calls; for completions it is always function at the time of writing
        if has_tool_calls:
            for tool_call in message.tool_calls:
                tool_call_data = {
                    "type": "function",
                    "id": tool_call.id,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                tool_call_json = json.dumps(tool_call_data)
                tool_piece = construct_response_from_request(
                    request=request,
                    response_text_pieces=[tool_call_json],
                    response_type="function_call",
                ).message_pieces[0]
                pieces.append(tool_piece)

        if not pieces:
            raise EmptyResponseException(message="Failed to extract any response content.")

        return Message(message_pieces=pieces)

    async def _save_audio_response_async(self, *, audio_data_base64: str) -> str:
        """
        Save audio data from an OpenAI audio response to a file.

        Args:
            audio_data_base64: Base64-encoded audio data from message.audio.data.

        Returns:
            str: The file path where the audio was saved.
        """
        audio_bytes = base64.b64decode(audio_data_base64)

        # Determine the format from config, default to wav
        audio_format = self._audio_response_config.audio_format if self._audio_response_config else "wav"
        extension = f".{audio_format}" if audio_format != "pcm16" else ".wav"

        audio_serializer = data_serializer_factory(
            category="prompt-memory-entries",
            data_type="audio_path",
            extension=extension,
        )

        if audio_format == "pcm16":
            # Raw PCM needs WAV headers - OpenAI uses 24kHz mono PCM16
            await audio_serializer.save_formatted_audio(
                data=audio_bytes,
                num_channels=1,
                sample_width=2,
                sample_rate=24000,
            )
        else:
            # wav, mp3, flac, opus are already properly formatted
            await audio_serializer.save_data(audio_bytes)

        return audio_serializer.value

    def is_json_response_supported(self) -> bool:
        """
        Check if the target supports JSON as a response format.

        Returns:
            bool: True if JSON response is supported, False otherwise.
        """
        return self._is_json_supported

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
        else:
            return await self._build_chat_messages_for_multi_modal_async(conversation)

    def _is_text_message_format(self, conversation: MutableSequence[Message]) -> bool:
        """
        Check if the message piece is in text message format.

        Args:
            conversation (list[Message]): The conversation

        Returns:
            bool: True if the message piece is in text message format, False otherwise.
        """
        for turn in conversation:
            if len(turn.message_pieces) != 1:
                return False
            if turn.message_pieces[0].converted_value_data_type != "text":
                return False
        return True

    def _build_chat_messages_for_text(self, conversation: MutableSequence[Message]) -> list[dict[str, Any]]:
        """
        Build chat messages based on message entries. This is needed because many
        openai "compatible" models don't support ChatMessageListDictContent format (this is more universally accepted).

        Args:
            conversation (list[Message]): A list of Message objects.

        Returns:
            list[dict]: The list of constructed chat messages.

        Raises:
            ValueError: If any message does not have exactly one text piece.
            ValueError: If any message piece is not of type text.
        """
        chat_messages: list[dict[str, Any]] = []
        for message in conversation:
            # validated to only have one text entry

            if len(message.message_pieces) != 1:
                raise ValueError("_build_chat_messages_for_text only supports a single message piece.")

            message_piece = message.message_pieces[0]

            if message_piece.converted_value_data_type != "text":
                raise ValueError("_build_chat_messages_for_text only supports text.")

            chat_message = ChatMessage(role=message_piece.api_role, content=message_piece.converted_value)
            chat_messages.append(chat_message.model_dump(exclude_none=True))

        return chat_messages

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
        chat_messages: list[dict[str, Any]] = []
        last_message_index = len(conversation) - 1

        for message_index, message in enumerate(conversation):
            message_pieces = message.message_pieces
            is_last_message = message_index == last_message_index

            # Check if this message has a text piece (transcript) alongside audio
            has_text_piece = any(mp.converted_value_data_type == "text" for mp in message_pieces)

            content = []
            role = None
            for message_piece in message_pieces:
                role = message_piece.api_role

                if self._should_skip_sending_audio(
                    message_piece=message_piece,
                    is_last_message=is_last_message,
                    has_text_piece=has_text_piece,
                ):
                    continue

                if message_piece.converted_value_data_type == "text":
                    entry = {"type": "text", "text": message_piece.converted_value}
                    content.append(entry)
                elif message_piece.converted_value_data_type == "image_path":
                    data_base64_encoded_url = await convert_local_image_to_data_url(message_piece.converted_value)
                    image_url_entry = {"url": data_base64_encoded_url}
                    entry = {"type": "image_url", "image_url": image_url_entry}  # type: ignore
                    content.append(entry)
                elif message_piece.converted_value_data_type == "audio_path":
                    ext = DataTypeSerializer.get_extension(message_piece.converted_value)
                    # OpenAI SDK: openai/types/chat/chat_completion_content_part_input_audio_param.py
                    # defines format: Required[Literal["wav", "mp3"]]
                    if not ext or ext.lower() not in [".wav", ".mp3"]:
                        raise ValueError(
                            f"Unsupported audio format: {ext}. "
                            "OpenAI Chat Completions API input_audio only supports .wav and .mp3. "
                            "Note: This is different from the Whisper Speech-to-Text API which supports more formats."
                        )
                    audio_serializer = data_serializer_factory(
                        category="prompt-memory-entries",
                        value=message_piece.converted_value,
                        data_type="audio_path",
                        extension=ext,
                    )
                    base64_data = await audio_serializer.read_data_base64()
                    audio_format = ext.lower().lstrip(".")
                    input_audio_entry = {"data": base64_data, "format": audio_format}
                    entry = {"type": "input_audio", "input_audio": input_audio_entry}  # type: ignore
                    content.append(entry)
                else:
                    raise ValueError(
                        f"Multimodal data type {message_piece.converted_value_data_type} is not yet supported."
                    )

            if not role:
                raise ValueError("No role could be determined from the message pieces.")

            chat_message = ChatMessage(role=role, content=content)
            chat_messages.append(chat_message.model_dump(exclude_none=True))
        return chat_messages

    async def _construct_request_body(
        self, *, conversation: MutableSequence[Message], json_config: _JsonResponseConfig
    ) -> dict[str, Any]:
        messages = await self._build_chat_messages_async(conversation)
        response_format = self._build_response_format(json_config)

        body_parameters = {
            "model": self._model_name,
            "max_completion_tokens": self._max_completion_tokens,
            "max_tokens": self._max_tokens,
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
            for key, value in self._extra_body_parameters.items():
                body_parameters[key] = value

        # Filter out None values
        return {k: v for k, v in body_parameters.items() if v is not None}

    def _validate_request(self, *, message: Message) -> None:
        """
        Validate the structure and content of a message for compatibility of this target.

        Args:
            message (Message): The message object.

        Raises:
            ValueError: If any of the message pieces have a data type other than 'text' or 'image_path'.
        """
        converted_prompt_data_types = [
            message_piece.converted_value_data_type for message_piece in message.message_pieces
        ]

        # Some models may not support all of these
        for prompt_data_type in converted_prompt_data_types:
            if prompt_data_type not in ["text", "image_path", "audio_path"]:
                raise ValueError(
                    f"This target only supports text, image_path, and audio_path. Received: {prompt_data_type}."
                )

    def _build_response_format(self, json_config: _JsonResponseConfig) -> Optional[Dict[str, Any]]:
        if not json_config.enabled:
            return None

        if json_config.schema:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": json_config.schema_name,
                    "schema": json_config.schema,
                    "strict": json_config.strict,
                },
            }

        return {"type": "json_object"}
