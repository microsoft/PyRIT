# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pyrit.common import forward_init_parameters
from pyrit.models import ComponentIdentifier, JsonResponseConfig, Message, MessagePiece, construct_response_from_request
from pyrit.models.messages.chat_message import FunctionCall
from pyrit.prompt_target.common.chat_completions_response_parser import capture_usage_and_finish_reason
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.tool_call_history import parse_function_call
from pyrit.prompt_target.common.tool_provider import Tool
from pyrit.prompt_target.litellm_chat_target import LiteLLMChatTarget


class ClaudeChatTarget(LiteLLMChatTarget):
    """Use Claude through LiteLLM's native Anthropic Messages adapter."""

    _CREDENTIAL_HEADERS = frozenset(
        {
            "authorization",
            "proxy-authorization",
            "x-api-key",
            "api-key",
            "ocp-apim-subscription-key",
            "cookie",
            "set-cookie",
        }
    )
    _JSON_OUTPUT_TOOL_NAME = "pyrit_claude_json_output"

    @forward_init_parameters
    def __init__(
        self,
        *,
        model_name: str,
        api_key: str | Callable[[], str | Awaitable[str]] | None = None,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        custom_configuration: TargetConfiguration | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the native Claude target.

        Args:
            model_name: A Claude name, with or without the ``anthropic/`` prefix.
            api_key: Explicit API key or sync/async token provider. Defaults to
                ``ANTHROPIC_API_KEY``, never ``LITELLM_API_KEY``.
            endpoint: Optional Anthropic API base URL; ignores ``LITELLM_ENDPOINT``.
            headers: Additional request headers, copied at construction.
            custom_configuration: Override model-specific capabilities and policy.
            **kwargs: Forwarded LiteLLM generation, tool, and tracing parameters.

        Raises:
            ValueError: If the model or passthrough parameters select another provider.
        """
        raw_model = model_name.removeprefix("anthropic/")
        if re.fullmatch(r"claude-[A-Za-z0-9][A-Za-z0-9._-]*", raw_model) is None:
            raise ValueError("model_name must be a Claude name or an anthropic/claude-... route.")
        routed_model = f"anthropic/{raw_model}"
        underlying = kwargs.pop("underlying_model", raw_model)
        if underlying not in (None, raw_model, routed_model):
            raise ValueError("underlying_model must match the Claude model_name.")
        extra = kwargs.get("extra_body_parameters") or {}
        if (
            extra.get("model", routed_model) != routed_model
            or extra.get("custom_llm_provider", "anthropic") != "anthropic"
        ):
            raise ValueError("ClaudeChatTarget requires the native anthropic provider route.")
        if {"api_key", "api_base", "headers", "extra_headers", "provider_specific_header"} & extra.keys():
            raise ValueError("Configure Claude credentials, endpoint, and headers through constructor parameters.")
        super().__init__(
            model_name=routed_model,
            underlying_model=raw_model,
            api_key=api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", ""),
            headers=dict(headers) if headers is not None else None,
            endpoint=endpoint,
            custom_configuration=custom_configuration,
            **kwargs,
        )
        self._endpoint = endpoint

    def _build_identifier(self) -> ComponentIdentifier:
        params = super()._build_identifier().params
        params["headers"] = {
            name: value for name, value in (self._headers or {}).items() if name.lower() not in self._CREDENTIAL_HEADERS
        } or None
        return self._create_identifier(params=params)

    def _validate_tool_definitions(self, tools: Sequence[Tool]) -> None:
        super()._validate_tool_definitions(tools)
        names = {tool.name for tool in tools} | {
            definition.get("function", {}).get("name")
            for definition in (self._extra_body_parameters or {}).get("tools") or []
        }
        if self._JSON_OUTPUT_TOOL_NAME in names:
            raise ValueError(f"Tool name {self._JSON_OUTPUT_TOOL_NAME!r} is reserved for Claude JSON output.")

    def _construct_request_body(
        self,
        *,
        messages: list[dict[str, Any]],
        json_config: JsonResponseConfig,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        body = super()._construct_request_body(messages=messages, json_config=json_config, api_key=api_key)
        if not json_config.enabled or json_config.json_schema is not None:
            return body
        # Anthropic's native schema output cannot represent an unconstrained JSON object,
        # and LiteLLM's bare json_object hint does not install a formatting tool.
        body.pop("response_format", None)
        body["tools"] = [
            *body.get("tools", []),
            {
                "type": "function",
                "function": {
                    "name": self._JSON_OUTPUT_TOOL_NAME,
                    "description": "Return the final answer as a JSON object.",
                    "parameters": {"type": "object", "additionalProperties": True},
                },
            },
        ]
        has_user_tools = bool(
            len(body["tools"]) > 1 or (not self._suppress_tools and (self._direct_tools or self._tool_providers))
        )
        body.setdefault(
            "tool_choice",
            "auto" if has_user_tools else {"type": "function", "function": {"name": self._JSON_OUTPUT_TOOL_NAME}},
        )
        return body

    async def _construct_message_from_response_async(self, *, response: Any, request: MessagePiece) -> Message:
        message = await super()._construct_message_from_response_async(response=response, request=request)
        if not self._get_json_response_config(message_piece=request).enabled or any(
            piece.is_truncated or piece.response_error != "none" for piece in message.message_pieces
        ):
            return message
        formatting: list[MessagePiece] = []
        remaining: list[MessagePiece] = []
        for piece in message.message_pieces:
            payload = json.loads(piece.converted_value) if piece.converted_value_data_type == "function_call" else {}
            if payload.get("function", {}).get("name") == self._JSON_OUTPUT_TOOL_NAME:
                call = parse_function_call(piece)
                if not isinstance(call.function, FunctionCall):
                    raise ValueError("Claude JSON formatting requires structured function arguments.")
                value = json.loads(call.function.arguments)
                if not isinstance(value, dict):
                    raise ValueError("Claude JSON formatting output must be a JSON object.")
                formatting.extend(
                    construct_response_from_request(
                        request=request, response_text_pieces=[call.function.arguments], response_type="text"
                    ).message_pieces
                )
            else:
                remaining.append(piece)
        if formatting:
            cost = message.message_pieces[0].prompt_metadata.get("token_usage_cost")
            pieces = [*formatting, *remaining]
            capture_usage_and_finish_reason(pieces=pieces, response=response)
            if cost is not None:
                pieces[0].prompt_metadata["token_usage_cost"] = cost
            return Message(message_pieces=pieces)
        if not any(piece.converted_value_data_type == "function_call" for piece in remaining):
            value = json.loads(message.get_value())
            if not isinstance(value, dict):
                raise ValueError("Claude JSON output must be a JSON object.")
        return message
