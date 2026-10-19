# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import math
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypedDict, cast

from pyrit.exceptions import pyrit_target_retry
from pyrit.models import (
    ComponentIdentifier,
    Message,
    MessagePiece,
    TokenUsage,
    construct_response_from_request,
    read_usage_int,
    read_usage_value,
)
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.target_trace_config import TargetTraceConfig, request_trace_headers
from pyrit.prompt_target.common.utils import limit_requests_per_minute, set_token_usage_metadata
from pyrit.prompt_target.litellm_chat_target import LiteLLMChatTarget

if TYPE_CHECKING:
    from litellm.types.decisions import DecisionsResponse


class _DecisionRequest(TypedDict):
    """A validated state/questions request for the decision API."""

    state: str | dict[str, object] | list[object]
    questions: dict[str, object]


class LiteLLMDecisionTarget(LiteLLMChatTarget):
    """
    Send prepared state and typed questions through LiteLLM's decision API.

    Responses use the SDK's JSON types. LiteLLM converts Boolean answers and
    numeric strings to numbers before returning them.
    """

    _SUPPORTS_TOOL_CALL_HISTORY = False
    _DEFAULT_CONFIGURATION = TargetConfiguration(capabilities=TargetCapabilities())
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

    def __init__(
        self,
        *,
        model_name: str,
        endpoint: str | None = None,
        api_key: str | Callable[[], str | Awaitable[str]] | None = None,
        headers: dict[str, str] | None = None,
        max_requests_per_minute: int | None = None,
        custom_configuration: TargetConfiguration | None = None,
        trace_config: TargetTraceConfig | None = None,
    ) -> None:
        """
        Initialize a stateless, text-only LiteLLM decision target.

        Args:
            model_name (str): Provider-prefixed LiteLLM decision model name.
            endpoint (str | None): Provider API base URL. LiteLLM selects the default
                when neither this value nor ``LITELLM_ENDPOINT`` is set.
            api_key (str | Callable[[], str | Awaitable[str]] | None): API key or token
                provider. Uses the same credential lookup as ``LiteLLMChatTarget``.
            headers (dict[str, str] | None): Extra provider request headers, copied at construction.
            max_requests_per_minute (int | None): Optional request rate limit.
            custom_configuration (TargetConfiguration | None): Target capability overrides.
            trace_config (TargetTraceConfig | None): Optional per-request tracing.

        Raises:
            ValueError: If the model name is empty.
        """
        if not model_name.strip():
            raise ValueError("A LiteLLM decision model name is required.")
        super().__init__(
            model_name=model_name,
            endpoint=endpoint,
            api_key=api_key,
            headers=dict(headers) if headers is not None else None,
            max_requests_per_minute=max_requests_per_minute,
            custom_configuration=custom_configuration or self._DEFAULT_CONFIGURATION,
            trace_config=trace_config,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Identify the decision model and endpoint without credentials.

        Returns:
            ComponentIdentifier: The target identity.
        """
        headers = {
            key: value for key, value in (self._headers or {}).items() if key.lower() not in self._CREDENTIAL_HEADERS
        }
        return self._create_identifier(params={"endpoint": self._endpoint, "headers": headers or None})

    @pyrit_target_retry
    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Send the prepared request without interpreting its questions or answers.

        Args:
            normalized_conversation (list[Message]): Normalized request messages.

        Returns:
            list[Message]: The SDK's JSON response as text evidence.

        Raises:
            ValueError: If the request is not a state/questions JSON object.
            ImportError: If LiteLLM does not provide a decision API.
            PyritException: If the provider rejects the request.
            RateLimitException: If provider retries are exhausted.
        """
        from openai import APIError

        litellm = self._import_litellm()
        decision_api = getattr(litellm, "asystemone", None) or getattr(litellm, "adecisions", None)
        if decision_api is None:
            raise ImportError("LiteLLMDecisionTarget requires litellm>=1.104.2. Install `pyrit[litellm]`.")
        request, body = await self._prepare_decision_request_async(message=normalized_conversation[-1])
        try:
            response = await decision_api(**body)
        except APIError as exc:
            return self._handle_litellm_exception(exc=exc, request=request)
        return [self._build_decision_response(request=request, response=response)]

    async def _prepare_decision_request_async(self, *, message: Message) -> tuple[MessagePiece, dict[str, Any]]:
        """
        Prepare the decision SDK arguments for one request.

        Args:
            message (Message): The prepared decision request.

        Returns:
            tuple[MessagePiece, dict[str, Any]]: The request piece and SDK arguments.

        Raises:
            ValueError: If the request is not a state/questions JSON object.
        """
        request = message.get_piece()
        body: dict[str, Any] = {
            **self._parse_request(request.converted_value),
            "model": self._model_name,
            "api_key": await self._resolve_api_key_async(),
            # Decision APIs do not use LiteLLM's chat retry wrapper. PyRIT owns retries.
            "num_retries": 0,
        }
        if self._endpoint:
            body["api_base"] = self._endpoint
        headers = request_trace_headers(request=message, headers=self._headers or {})
        if headers:
            body["extra_headers"] = headers
        return request, body

    def _build_decision_response(self, *, request: MessagePiece, response: "DecisionsResponse") -> Message:
        """
        Build response evidence with decision usage and cost metadata.

        Args:
            request (MessagePiece): The request associated with the response.
            response (DecisionsResponse): The SDK-normalized decision response.

        Returns:
            Message: The response as JSON text with usage and cost metadata.

        Raises:
            ValueError: If the reported cost is invalid.
        """
        result = construct_response_from_request(
            request=request,
            response_text_pieces=[response.model_dump_json(exclude_none=True)],
        )
        input_tokens = read_usage_int(source=response.usage, name="input_tokens")
        output_tokens = read_usage_int(source=response.usage, name="output_tokens")
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=(
                input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None
            ),
        )
        set_token_usage_metadata(pieces=result.message_pieces, usage=usage)
        self._capture_response_cost(pieces=result.message_pieces, response=response)
        return result

    @staticmethod
    def _extract_response_cost(*, response: "DecisionsResponse") -> float | None:
        """
        Read decision cost without using Chat Completions pricing.

        Args:
            response (DecisionsResponse): The LiteLLM decision response.

        Returns:
            float | None: Provider-reported or SDK-calculated cost, when available.

        Raises:
            ValueError: If the reported cost is invalid.
        """
        cost = read_usage_value(source=response.usage, name="cost")
        if cost is None:
            hidden_params = getattr(response, "_hidden_params", None)
            cost = hidden_params.get("response_cost") if isinstance(hidden_params, dict) else None
        if cost is None:
            return None
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            raise ValueError("Decision response cost must be a finite nonnegative number.")
        return float(cost)

    @staticmethod
    def _parse_request(text: str) -> _DecisionRequest:
        try:
            body = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("A LiteLLM decision request must be a JSON object.") from exc
        if not isinstance(body, dict) or set(body) != {"state", "questions"}:
            raise ValueError("A LiteLLM decision request must contain exactly state and questions.")
        if not isinstance(body["state"], (str, dict, list)):
            raise ValueError("Decision state must be text, a JSON object, or a JSON array.")
        if not isinstance(body["questions"], dict) or not body["questions"]:
            raise ValueError("Decision questions must be a nonempty JSON object.")
        return cast("_DecisionRequest", body)
