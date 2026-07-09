# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
from collections.abc import MutableSequence
from typing import Any, Optional

from pyrit.exceptions import (
    EmptyResponseException,
    PyritException,
    RateLimitException,
    pyrit_target_retry,
)
from pyrit.models import (
    ChatMessage,
    Message,
    MessagePiece,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute, validate_temperature, validate_top_p

logger = logging.getLogger(__name__)


class LiteLLMChatTarget(PromptTarget):
    """
    Chat target that uses the LiteLLM SDK to access 100+ LLM providers.

    Unlike ``OpenAIChatTarget`` (which uses the OpenAI SDK directly), this
    target calls ``litellm.acompletion()`` so it can route to any provider
    LiteLLM supports (Anthropic, AWS Bedrock, Google Vertex, Cohere, etc.)
    without requiring a separate proxy server.

    Install the optional dependency::

        pip install pyrit[litellm]

    Example::

        target = LiteLLMChatTarget(
            model_name="anthropic/claude-sonnet-4-6",
        )

    LiteLLM reads provider API keys from environment variables automatically
    (e.g. ``ANTHROPIC_API_KEY``, ``AWS_ACCESS_KEY_ID``). You can also pass
    ``api_key`` explicitly.

    Args:
        model_name: LiteLLM model string (e.g. ``"anthropic/claude-sonnet-4-6"``,
            ``"bedrock/anthropic.claude-v2"``, ``"vertex_ai/gemini-pro"``).
            Falls back to the ``LITELLM_MODEL`` environment variable.
        api_key: Optional API key. When omitted, LiteLLM reads provider-specific
            environment variables.
        api_base: Optional base URL override (e.g. for a self-hosted proxy).
        temperature: Sampling temperature (0-2).
        top_p: Nucleus sampling probability (0-1).
        max_tokens: Maximum number of tokens to generate.
        drop_params: When ``True`` (the default), LiteLLM silently drops
            parameters that the target provider does not support, preventing
            cross-provider compatibility errors.
    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
            supports_json_output=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
        )
    )

    def __init__(
        self,
        *,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        drop_params: bool = True,
        max_requests_per_minute: Optional[int] = None,
        custom_configuration: Optional[TargetConfiguration] = None,
    ) -> None:
        """
        Initialize a LiteLLMChatTarget.

        Raises:
            ValueError: If model_name is not provided and LITELLM_MODEL env var is not set.
        """
        import os

        resolved_model = model_name or os.environ.get("LITELLM_MODEL", "")
        if not resolved_model:
            raise ValueError(
                "model_name is required. Pass it directly or set the LITELLM_MODEL environment variable."
            )

        validate_temperature(temperature)
        validate_top_p(top_p)

        super().__init__(
            model_name=resolved_model,
            max_requests_per_minute=max_requests_per_minute,
            custom_configuration=custom_configuration,
        )

        self._api_key = api_key
        self._api_base = api_base
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._drop_params = drop_params

    @limit_requests_per_minute
    @pyrit_target_retry
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        try:
            import litellm
        except ImportError as e:
            raise ImportError(
                "The litellm package is required for LiteLLMChatTarget. "
                "Install it with: pip install pyrit[litellm]"
            ) from e

        message = normalized_conversation[-1]
        request_piece: MessagePiece = message.message_pieces[0]

        logger.info(f"Sending prompt to LiteLLM target ({self._model_name}): {message}")

        messages = self._build_chat_messages(normalized_conversation)
        body = self._construct_request_body(messages)

        try:
            response = await litellm.acompletion(**body)
        except Exception as exc:
            self._translate_litellm_exception(exc)

        self._validate_response(response)

        return [self._construct_message_from_response(response, request_piece)]

    def _build_chat_messages(self, conversation: MutableSequence[Message]) -> list[dict[str, Any]]:
        chat_messages: list[dict[str, Any]] = []
        for msg in conversation:
            for piece in msg.message_pieces:
                chat_message = ChatMessage(role=piece.api_role, content=piece.converted_value)
                chat_messages.append(chat_message.model_dump(exclude_none=True))
        return chat_messages

    def _construct_request_body(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "drop_params": self._drop_params,
        }

        if self._api_key:
            body["api_key"] = self._api_key
        if self._api_base:
            body["api_base"] = self._api_base
        if self._temperature is not None:
            body["temperature"] = self._temperature
        if self._top_p is not None:
            body["top_p"] = self._top_p
        if self._max_tokens is not None:
            body["max_tokens"] = self._max_tokens

        return body

    def _translate_litellm_exception(self, exc: Exception) -> None:
        qualname = f"{type(exc).__module__}.{type(exc).__qualname__}"

        rate_limit_types = {
            "litellm.exceptions.RateLimitError",
        }
        transient_types = {
            "litellm.exceptions.APIConnectionError",
            "litellm.exceptions.Timeout",
            "litellm.exceptions.InternalServerError",
            "litellm.exceptions.ServiceUnavailableError",
        }
        auth_types = {
            "litellm.exceptions.AuthenticationError",
        }

        if qualname in rate_limit_types:
            raise RateLimitException(
                message=f"Rate limited by provider: {exc}",
                status_code=429,
            ) from exc

        if qualname in auth_types:
            raise PyritException(message=f"Authentication failed: {exc}") from exc

        if qualname in transient_types:
            raise RateLimitException(
                message=f"Transient provider error (will retry): {exc}",
                status_code=getattr(exc, "status_code", 503),
            ) from exc

        raise PyritException(message=f"LiteLLM error: {exc}") from exc

    def _validate_response(self, response: Any) -> None:
        if not hasattr(response, "choices") or not response.choices:
            raise EmptyResponseException(message="No choices returned from LiteLLM.")

        choice = response.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        valid_reasons = {"stop", "length", "tool_calls", "content_filter"}
        if finish_reason and finish_reason not in valid_reasons:
            raise PyritException(
                message=f"Unexpected finish_reason '{finish_reason}' from LiteLLM response."
            )

        content = getattr(choice.message, "content", None) if hasattr(choice, "message") else None
        tool_calls = getattr(choice.message, "tool_calls", None) if hasattr(choice, "message") else None

        if not content and not tool_calls:
            raise EmptyResponseException(message="LiteLLM returned an empty response.")

    def _construct_message_from_response(self, response: Any, request_piece: MessagePiece) -> Message:
        choice = response.choices[0]
        content = getattr(choice.message, "content", None) or ""

        pieces: list[MessagePiece] = []

        if content:
            text_msg = construct_response_from_request(
                request=request_piece,
                response_text_pieces=[content],
                response_type="text",
            )
            pieces.append(text_msg.message_pieces[0])

        tool_calls = getattr(choice.message, "tool_calls", None)
        if tool_calls:
            for tool_call in tool_calls:
                tool_call_data = {
                    "type": "function",
                    "id": tool_call.id,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                tool_msg = construct_response_from_request(
                    request=request_piece,
                    response_text_pieces=[json.dumps(tool_call_data)],
                    response_type="function_call",
                )
                pieces.append(tool_msg.message_pieces[0])

        if not pieces:
            raise EmptyResponseException(message="Failed to extract any response content from LiteLLM.")

        if hasattr(response, "usage") and response.usage and pieces:
            pieces[0].prompt_metadata["token_usage_model_name"] = getattr(response, "model", "unknown")
            pieces[0].prompt_metadata["token_usage_prompt_tokens"] = getattr(response.usage, "prompt_tokens", 0)
            pieces[0].prompt_metadata["token_usage_completion_tokens"] = getattr(
                response.usage, "completion_tokens", 0
            )
            pieces[0].prompt_metadata["token_usage_total_tokens"] = getattr(response.usage, "total_tokens", 0)

        return Message(message_pieces=pieces)
