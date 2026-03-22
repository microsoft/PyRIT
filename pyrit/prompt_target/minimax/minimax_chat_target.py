# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
from collections.abc import MutableSequence
from typing import Any, Optional

from openai import AsyncOpenAI, BadRequestError, RateLimitError
from openai._exceptions import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError

from pyrit.common import default_values
from pyrit.exceptions import EmptyResponseException, PyritException, pyrit_target_retry
from pyrit.exceptions.exception_classes import RateLimitException, handle_bad_request_exception
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import ChatMessage, Message, MessagePiece, construct_response_from_request
from pyrit.models.json_response_config import _JsonResponseConfig
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.utils import limit_requests_per_minute, validate_temperature, validate_top_p

logger = logging.getLogger(__name__)

# MiniMax temperature range is [0, 1] (unlike OpenAI's [0, 2])
_MINIMAX_MAX_TEMPERATURE = 1.0


class MiniMaxChatTarget(PromptChatTarget):
    """
    Prompt target for MiniMax AI chat models via the OpenAI-compatible API.

    MiniMax provides large language models accessible through an OpenAI-compatible
    chat completions endpoint at https://api.minimax.io/v1.

    Supported models:
    - MiniMax-M2.7: Latest model with 1M token context
    - MiniMax-M2.7-highspeed: Faster variant of M2.7
    - MiniMax-M2.5: Previous generation with 204K context
    - MiniMax-M2.5-highspeed: Faster variant of M2.5

    Args:
        api_key (str): The API key for the MiniMax API.
        endpoint (str): The endpoint URL (default: https://api.minimax.io/v1).
        model_name (str): The model name (default: MiniMax-M2.7).
        temperature (float): The temperature for the completion (0.0-1.0).
        max_completion_tokens (int): Maximum number of tokens to generate.
        top_p (float): The nucleus sampling probability.
        max_requests_per_minute (int): Rate limit for requests per minute.

    Example usage:

        >>> from pyrit.prompt_target import MiniMaxChatTarget
        >>> target = MiniMaxChatTarget(
        ...     api_key="your-minimax-api-key",
        ...     model_name="MiniMax-M2.7",
        ... )
    """

    _DEFAULT_CAPABILITIES: TargetCapabilities = TargetCapabilities(
        supports_multi_turn=True,
        supports_json_output=True,
        supports_multi_message_pieces=False,
    )

    MINIMAX_CHAT_MODEL_ENV: str = "MINIMAX_CHAT_MODEL"
    MINIMAX_CHAT_ENDPOINT_ENV: str = "MINIMAX_CHAT_ENDPOINT"
    MINIMAX_CHAT_KEY_ENV: str = "MINIMAX_API_KEY"

    _DEFAULT_ENDPOINT: str = "https://api.minimax.io/v1"
    _DEFAULT_MODEL: str = "MiniMax-M2.7"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_completion_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        max_requests_per_minute: Optional[int] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
        httpx_client_kwargs: Optional[dict[str, Any]] = None,
    ) -> None:
        # Resolve configuration from parameters or environment variables
        resolved_api_key = default_values.get_required_value(
            env_var_name=self.MINIMAX_CHAT_KEY_ENV, passed_value=api_key
        )
        resolved_endpoint = default_values.get_non_required_value(
            env_var_name=self.MINIMAX_CHAT_ENDPOINT_ENV, passed_value=endpoint
        ) or self._DEFAULT_ENDPOINT
        resolved_model = default_values.get_non_required_value(
            env_var_name=self.MINIMAX_CHAT_MODEL_ENV, passed_value=model_name
        ) or self._DEFAULT_MODEL

        # Validate and clamp temperature to MiniMax range [0, 1]
        if temperature is not None:
            validate_temperature(temperature)
            if temperature > _MINIMAX_MAX_TEMPERATURE:
                logger.warning(
                    f"MiniMax supports temperature in [0, 1]. "
                    f"Clamping {temperature} to {_MINIMAX_MAX_TEMPERATURE}."
                )
                temperature = _MINIMAX_MAX_TEMPERATURE

        validate_top_p(top_p)

        effective_capabilities = custom_capabilities or type(self)._DEFAULT_CAPABILITIES

        super().__init__(
            max_requests_per_minute=max_requests_per_minute,
            endpoint=resolved_endpoint,
            model_name=resolved_model,
            custom_capabilities=effective_capabilities,
        )

        self._temperature = temperature
        self._top_p = top_p
        self._max_completion_tokens = max_completion_tokens
        self._api_key = resolved_api_key

        # Initialize the OpenAI-compatible async client for MiniMax
        httpx_kwargs = httpx_client_kwargs or {}
        self._async_client = AsyncOpenAI(
            base_url=resolved_endpoint,
            api_key=resolved_api_key,
            **httpx_kwargs,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "temperature": self._temperature,
                "top_p": self._top_p,
                "max_completion_tokens": self._max_completion_tokens,
            },
        )

    @limit_requests_per_minute
    @pyrit_target_retry
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Sends a chat prompt to the MiniMax API and returns the response.

        Args:
            message: The message to send.

        Returns:
            A list containing the response Message.
        """
        self._validate_request(message=message)

        message_piece: MessagePiece = message.message_pieces[0]
        json_config = self._get_json_response_config(message_piece=message_piece)

        # Get conversation history and append the current message
        conversation = self._memory.get_conversation(conversation_id=message_piece.conversation_id)
        conversation.append(message)

        logger.info(f"Sending the following prompt to MiniMax: {message}")

        body = self._construct_request_body(conversation=conversation, json_config=json_config)

        try:
            response = await self._async_client.chat.completions.create(**body)

            # Validate response
            if not hasattr(response, "choices") or not response.choices:
                raise PyritException(message="No choices returned in the MiniMax completion response.")

            choice = response.choices[0]
            finish_reason = choice.finish_reason

            valid_finish_reasons = ["stop", "length", "tool_calls"]
            if finish_reason not in valid_finish_reasons:
                raise PyritException(
                    message=f"Unknown finish_reason '{finish_reason}' from MiniMax response: "
                    f"{response.model_dump_json()}"
                )

            content = choice.message.content
            if not content:
                raise EmptyResponseException(message="MiniMax returned an empty response.")

            # Strip thinking tags that MiniMax M2.5+ models may include
            content = self._strip_thinking_tags(content)

            result_message = construct_response_from_request(
                request=message_piece,
                response_text_pieces=[content],
                response_type="text",
            )

            # Capture token usage metadata
            if hasattr(response, "usage") and response.usage:
                result_message.message_pieces[0].prompt_metadata["token_usage_model_name"] = getattr(
                    response, "model", "unknown"
                )
                result_message.message_pieces[0].prompt_metadata["token_usage_prompt_tokens"] = getattr(
                    response.usage, "prompt_tokens", 0
                )
                result_message.message_pieces[0].prompt_metadata["token_usage_completion_tokens"] = getattr(
                    response.usage, "completion_tokens", 0
                )
                result_message.message_pieces[0].prompt_metadata["token_usage_total_tokens"] = getattr(
                    response.usage, "total_tokens", 0
                )

            return [result_message]

        except BadRequestError as e:
            logger.warning(f"MiniMax BadRequestError: {e}")
            return [
                handle_bad_request_exception(
                    response_text=str(e),
                    request=message_piece,
                    error_code=400,
                    is_content_filter=False,
                )
            ]
        except RateLimitError as e:
            logger.warning(f"MiniMax RateLimitError: {e}")
            raise RateLimitException from None
        except APIStatusError as e:
            if getattr(e, "status_code", None) == 429:
                logger.warning(f"MiniMax 429 rate limit via APIStatusError: {e}")
                raise RateLimitException from None
            logger.exception(f"MiniMax APIStatusError: {e}")
            raise
        except (APITimeoutError, APIConnectionError) as e:
            logger.warning(f"MiniMax transient error ({e.__class__.__name__}): {e}")
            raise
        except AuthenticationError as e:
            logger.error(f"MiniMax authentication error: {e}")
            raise

    def _construct_request_body(
        self, *, conversation: MutableSequence[Message], json_config: _JsonResponseConfig
    ) -> dict[str, Any]:
        """Build the request body for the MiniMax chat completions API."""
        messages = self._build_chat_messages(conversation)
        response_format = self._build_response_format(json_config)

        body: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "stream": False,
        }

        if self._temperature is not None:
            body["temperature"] = self._temperature
        if self._top_p is not None:
            body["top_p"] = self._top_p
        if self._max_completion_tokens is not None:
            body["max_tokens"] = self._max_completion_tokens
        if response_format is not None:
            body["response_format"] = response_format

        return body

    def _build_chat_messages(self, conversation: MutableSequence[Message]) -> list[dict[str, Any]]:
        """Convert conversation Messages to the chat API format."""
        chat_messages: list[dict[str, Any]] = []
        for message in conversation:
            if len(message.message_pieces) != 1:
                raise ValueError("MiniMaxChatTarget only supports single-piece text messages.")

            piece = message.message_pieces[0]
            if piece.converted_value_data_type not in ("text", "error"):
                raise ValueError(
                    f"MiniMaxChatTarget only supports text data types. "
                    f"Received: {piece.converted_value_data_type}."
                )

            chat_message = ChatMessage(role=piece.api_role, content=piece.converted_value)
            chat_messages.append(chat_message.model_dump(exclude_none=True))

        return chat_messages

    def _build_response_format(self, json_config: _JsonResponseConfig) -> Optional[dict[str, Any]]:
        """Build the response_format parameter for JSON mode."""
        if not json_config.enabled:
            return None
        # MiniMax supports json_object mode but not json_schema
        return {"type": "json_object"}

    @staticmethod
    def _strip_thinking_tags(content: str) -> str:
        """
        Strip <think>...</think> tags from MiniMax M2.5+ model responses.

        Some MiniMax models include internal reasoning in <think> tags.
        These should be stripped from the final response.
        """
        import re

        return re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
