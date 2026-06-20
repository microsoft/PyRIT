# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import logging
import mimetypes
import os
from typing import Any

import anthropic

from pyrit.exceptions import (
    EmptyResponseException,
    PyritException,
    pyrit_target_retry,
)
from pyrit.models import (
    Message,
    MessagePiece,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import (
    limit_requests_per_minute,
    validate_temperature,
    validate_top_p,
)

logger = logging.getLogger(__name__)


class AnthropicChatTarget(PromptChatTarget):
    """
    Target for Anthropic Claude models via the Anthropic API.

    Args:
        api_key (str): Anthropic API key. Defaults to ANTHROPIC_API_KEY env var.
        model_name (str): Claude model to use. Defaults to ANTHROPIC_CHAT_MODEL env var.
        max_tokens (int): Max tokens to generate. Required by Anthropic API.
        temperature (float): Sampling temperature (0-1).
        top_p (float): Nucleus sampling probability.
        top_k (int): Top-k sampling. Anthropic-specific.
    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=True,
            supports_editable_history=True,
            supports_multi_message_pieces=False,
            supports_json_output=False,
            input_modalities=frozenset(
                {frozenset({"text"}), frozenset({"image_path"}), frozenset({"text", "image_path"})}
            ),
        )
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_name: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        custom_configuration: TargetConfiguration | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(custom_configuration=custom_configuration, **kwargs)

        validate_temperature(temperature)
        validate_top_p(top_p)

        self._max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._top_k = top_k

        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._model_name = model_name or os.environ.get("ANTHROPIC_CHAT_MODEL", "claude-sonnet-4-6")

        if not self._api_key:
            raise ValueError("Anthropic API key must be provided or set via ANTHROPIC_API_KEY env var.")

        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

    async def _build_chat_messages_async(self, conversation: list[Message]) -> tuple[list[dict], str | None]:
        """
        Convert PyRIT Message objects into Anthropic API message format.
        Also extracts the system prompt if present.

        Returns:
            tuple: (messages list, system prompt string or None)
        """
        messages = []
        system_prompt = None

        for message in conversation:
            piece = message.message_pieces[0]
            role = piece.api_role

            if role == "system":
                system_prompt = piece.converted_value
                continue

            if piece.converted_value_data_type == "text":
                messages.append({
                    "role": role,
                    "content": piece.converted_value,
                })
            elif piece.converted_value_data_type == "image_path":
                with open(piece.converted_value, "rb") as f:
                    image_data = base64.standard_b64encode(f.read()).decode("utf-8")
                media_type = mimetypes.guess_type(piece.converted_value)[0] or "image/jpeg"
                messages.append({
                    "role": role,
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        }
                    ],
                })
            else:
                raise ValueError(
                    f"Unsupported data type: {piece.converted_value_data_type}"
                )

        return messages, system_prompt

    async def _construct_request_body_async(self, *, conversation: list[Message]) -> dict[str, Any]:
        """
        Build the request body for the Anthropic API.
        """
        messages, system_prompt = await self._build_chat_messages_async(conversation)

        body: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }

        if system_prompt:
            body["system"] = system_prompt

        if self._temperature is not None:
            body["temperature"] = self._temperature
        if self._top_p is not None:
            body["top_p"] = self._top_p
        if self._top_k is not None:
            body["top_k"] = self._top_k

        return body

    def _validate_response(self, response: Any, request: MessagePiece) -> None:
        """
        Validate the Anthropic API response for errors.

        Raises:
            PyritException: For unexpected response structures or stop reasons.
            EmptyResponseException: When the API returns no content.
        """
        if not hasattr(response, "content") or not response.content:
            raise EmptyResponseException(message="Anthropic returned an empty response.")

        valid_stop_reasons = ["end_turn", "max_tokens", "stop_sequence"]
        if response.stop_reason not in valid_stop_reasons:
            raise PyritException(
                message=f"Unexpected stop_reason '{response.stop_reason}' in response."
            )

    async def _construct_message_from_response_async(self, response: Any, request: MessagePiece) -> Message:
        """
        Convert Anthropic API response into a PyRIT Message object.

        Raises:
            EmptyResponseException: If no text content found in response.
        """
        pieces = []

        for block in response.content:
            if block.type == "text":
                piece = construct_response_from_request(
                    request=request,
                    response_text_pieces=[block.text],
                    response_type="text",
                ).message_pieces[0]
                pieces.append(piece)

        if not pieces:
            raise EmptyResponseException(message="No text content found in Anthropic response.")

        if hasattr(response, "usage") and response.usage and pieces:
            pieces[0].prompt_metadata["token_usage_model_name"] = self._model_name
            pieces[0].prompt_metadata["token_usage_prompt_tokens"] = getattr(response.usage, "input_tokens", 0)
            pieces[0].prompt_metadata["token_usage_completion_tokens"] = getattr(response.usage, "output_tokens", 0)
            pieces[0].prompt_metadata["token_usage_total_tokens"] = (
                getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0)
            )

        return Message(message_pieces=pieces)

    @limit_requests_per_minute
    @pyrit_target_retry
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Send a prompt to the Anthropic API and return the response.

        Args:
            normalized_conversation: Full conversation history, current message is last.

        Returns:
            list[Message]: Response from Claude wrapped in a list.
        """
        request_message = normalized_conversation[-1]
        request_piece = request_message.message_pieces[0]

        logger.info(f"Sending prompt to Anthropic target: {request_message}")

        body = await self._construct_request_body_async(conversation=normalized_conversation)

        try:
            response = await self._client.messages.create(**body)
        except anthropic.BadRequestError as e:
            raise PyritException(message=f"Anthropic rejected the request: {e}") from e
        except anthropic.RateLimitError as e:
            raise PyritException(message=f"Anthropic rate limit hit: {e}") from e
        except anthropic.APIError as e:
            raise PyritException(message=f"Anthropic API error: {e}") from e

        self._validate_response(response=response, request=request_piece)
        result = await self._construct_message_from_response_async(
            response=response,
            request=request_piece,
        )
        return [result]
