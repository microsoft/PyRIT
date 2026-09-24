# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from pyrit.exceptions import RateLimitException, pyrit_target_retry
from pyrit.exceptions.exception_classes import CONTENT_FILTER_MARKERS
from pyrit.models import (
    Message,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)

RPC_METHOD_NOT_FOUND = -32601
_PENDING_TASK_STATES = frozenset({"submitted", "working"})
_INTERRUPTED_TASK_STATES = frozenset({"input-required", "auth-required"})
_TASK_POLL_INTERVAL_SECONDS = 1.0


@dataclass
class _A2AConversationState:
    """Server-issued identifiers that continue one PyRIT conversation on the agent."""

    context_id: str | None = None
    open_task_id: str | None = None


class A2ATarget(PromptTarget):
    """
    A PromptTarget for interacting with agents speaking the Agent-to-Agent (A2A) protocol.

    The Agent-to-Agent protocol defines task-based message exchange over JSON-RPC 2.0.
    This target natively supports both protocol revisions:
    - Spec 0.3+: uses `message/send` with message parts discriminator `kind: "text"`.
    - Spec 0.2: uses `tasks/send` with message parts discriminator `type: "text"`.

    By default (`dialect="auto"`), the target attempts the current 0.3 specification first.
    If the target agent responds with JSON-RPC error code -32601 (Method not found),
    it automatically falls back to 0.2 and pins that dialect for subsequent turns.

    Each PyRIT conversation maps to one A2A context (``contextId`` in 0.3, ``sessionId`` in 0.2),
    so the agent keeps its own server-side state across turns. Tasks left waiting for input are
    continued with their task ID. Requests ask the agent to block until the task completes, and
    tasks still pending are polled with ``tasks/get``.
    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            input_modalities=frozenset({frozenset(["text"])}),
        )
    )

    def __init__(
        self,
        *,
        endpoint: str,
        auth_token: str | None = None,
        api_key: str | None = None,
        api_key_header: str = "X-API-Key",
        dialect: Literal["auto", "v03", "v02"] = "auto",
        task_timeout_seconds: float = 120.0,
        max_requests_per_minute: int | None = None,
        custom_configuration: TargetConfiguration | None = None,
        **httpx_client_kwargs: Any,
    ) -> None:
        """
        Initialize the A2ATarget.

        Args:
            endpoint (str): The target URL of the A2A agent endpoint.
            auth_token (str, Optional): Bearer token for HTTP Authorization header.
            api_key (str, Optional): Custom API key for the agent.
            api_key_header (str): Header name for the API key (defaults to "X-API-Key").
            dialect (Literal["auto", "v03", "v02"]): A2A protocol dialect to use. Defaults to "auto".
            task_timeout_seconds (float): How long to poll a pending task before giving up. Defaults to 120.
            max_requests_per_minute (int, Optional): Rate limit cap for requests per minute.
            custom_configuration (TargetConfiguration, Optional): Custom target capabilities override.
            **httpx_client_kwargs: Additional keyword arguments passed to httpx.AsyncClient.

        Raises:
            ValueError: If the dialect is not supported.
        """
        if dialect not in ("auto", "v03", "v02"):
            raise ValueError(f"Unsupported A2A dialect '{dialect}'. Expected 'auto', 'v03' or 'v02'.")

        super().__init__(
            endpoint=endpoint,
            max_requests_per_minute=max_requests_per_minute,
            custom_configuration=custom_configuration,
        )

        self._endpoint = endpoint.rstrip("/")
        self._auth_token = auth_token
        self._api_key = api_key
        self._api_key_header = api_key_header
        self._dialect: Literal["auto", "v03", "v02"] = dialect
        self._task_timeout_seconds = task_timeout_seconds
        self._conversations: dict[str, _A2AConversationState] = {}
        self._httpx_client_kwargs = httpx_client_kwargs

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if self._api_key:
            headers[self._api_key_header] = self._api_key
        return headers

    @staticmethod
    def _rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}

    def _build_payload(
        self, *, dialect: Literal["v03", "v02"], message_text: str, state: _A2AConversationState
    ) -> dict[str, Any]:
        if dialect == "v03":
            message: dict[str, Any] = {
                "kind": "message",
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": message_text}],
            }
            if state.context_id:
                message["contextId"] = state.context_id
            if state.open_task_id:
                message["taskId"] = state.open_task_id
            return self._rpc("message/send", {"message": message, "configuration": {"blocking": True}})

        params: dict[str, Any] = {
            "id": state.open_task_id or str(uuid.uuid4()),
            "message": {"role": "user", "parts": [{"type": "text", "text": message_text}]},
        }
        if state.context_id:
            params["sessionId"] = state.context_id
        return self._rpc("tasks/send", params)

    @staticmethod
    def _parts_text(parts: Any) -> list[str]:
        if not isinstance(parts, list):
            return []
        return [str(part["text"]) for part in parts if isinstance(part, dict) and "text" in part]

    @classmethod
    def _extract_response_text(cls, result: Any) -> str:
        """
        Extract the agent's text from an A2A ``Message`` or ``Task`` result.

        Returns:
            str: The concatenated text parts of the agent's reply.
        """
        if not isinstance(result, dict):
            return str(result) if result is not None else ""

        # Message result, or a message embedded in a legacy task result
        texts = cls._parts_text(result.get("parts"))
        if not texts and isinstance(result.get("message"), dict):
            texts = cls._parts_text(result["message"].get("parts"))
        if texts:
            return "\n".join(texts)

        # Task artifacts
        artifacts = result.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if isinstance(artifact, dict):
                    texts.extend(cls._parts_text(artifact.get("parts")))
            if texts:
                return "\n".join(texts)

        # Task status message, e.g. input-required or failed
        status = result.get("status")
        if isinstance(status, dict) and isinstance(status.get("message"), dict):
            texts = cls._parts_text(status["message"].get("parts"))
            if texts:
                return "\n".join(texts)

        return str(result)

    @staticmethod
    def _update_state(*, state: _A2AConversationState, result: Any) -> None:
        if not isinstance(result, dict):
            return
        context_id = result.get("contextId") or result.get("sessionId")
        if context_id:
            state.context_id = context_id
        task_state = result.get("status", {}).get("state") if isinstance(result.get("status"), dict) else None
        is_task = result.get("kind") == "task" or "status" in result
        state.open_task_id = result.get("id") if is_task and task_state in _INTERRUPTED_TASK_STATES else None

    async def _post_async(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await client.post(self._endpoint, json=payload, headers=self._build_headers())
        if resp.status_code == 429:
            raise RateLimitException(message=f"A2A agent rate limited: {resp.text}")
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def _await_task_async(self, client: httpx.AsyncClient, data: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + self._task_timeout_seconds
        while True:
            result = data.get("result")
            if not isinstance(result, dict):
                return data
            status = result.get("status")
            if not (isinstance(status, dict) and status.get("state") in _PENDING_TASK_STATES):
                return data
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"A2A task {result['id']} still {status['state']} after {self._task_timeout_seconds} seconds."
                )
            await asyncio.sleep(_TASK_POLL_INTERVAL_SECONDS)
            data = await self._post_async(client, self._rpc("tasks/get", {"id": result["id"]}))

    @pyrit_target_retry
    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Send the latest message to the agent, continuing the conversation's A2A context.

        Args:
            normalized_conversation (list[Message]): Normalized conversation with the current request last.

        Returns:
            list[Message]: A list containing the agent's response.

        Raises:
            ValueError: If no conversation is provided.
            RateLimitException: If the agent, or the model behind it, is rate limited.
        """
        if not normalized_conversation:
            raise ValueError("No conversation provided to A2ATarget.")

        message_piece = normalized_conversation[-1].get_piece()
        conversation_id = message_piece.conversation_id or ""
        if conversation_id not in self._conversations and len(normalized_conversation) > 1:
            logger.warning(
                "A2ATarget has no A2A context for conversation %s; earlier turns are not visible to the agent.",
                conversation_id,
            )
        state = self._conversations.setdefault(conversation_id, _A2AConversationState())
        prompt_text = message_piece.converted_value

        current_dialect: Literal["v03", "v02"] = "v02" if self._dialect == "v02" else "v03"
        async with httpx.AsyncClient(**self._httpx_client_kwargs) as client:
            data = await self._post_async(
                client, self._build_payload(dialect=current_dialect, message_text=prompt_text, state=state)
            )

            # Handle automatic dialect fallback if method not found
            error = data.get("error")
            if self._dialect == "auto" and isinstance(error, dict) and error.get("code") == RPC_METHOD_NOT_FOUND:
                logger.info("A2A method message/send returned -32601; falling back to tasks/send (dialect 0.2)")
                current_dialect = "v02"
                data = await self._post_async(
                    client, self._build_payload(dialect=current_dialect, message_text=prompt_text, state=state)
                )
            if self._dialect == "auto" and data.get("error") is None:
                self._dialect = current_dialect

            data = await self._await_task_async(client, data)

        error = data.get("error")
        if error is not None:
            error_text = str(error)
            # agents may relay an upstream model rate limit as an internal error
            if "429" in str(error.get("message", "") if isinstance(error, dict) else error):
                raise RateLimitException(message=f"A2A agent relayed a rate limit: {error_text}")
            is_blocked = any(marker in error_text for marker in CONTENT_FILTER_MARKERS)
            logger.warning("A2A agent at %s returned error: %s", self._endpoint, error_text)
            return [
                construct_response_from_request(
                    request=message_piece,
                    response_text_pieces=[error_text],
                    response_type="error",
                    error="blocked" if is_blocked else "unknown",
                )
            ]

        result = data.get("result")
        self._update_state(state=state, result=result)
        return [
            construct_response_from_request(
                request=message_piece,
                response_text_pieces=[self._extract_response_text(result)],
            )
        ]

    async def reset_conversation_async(self, *, conversation_id: str) -> None:
        """
        Forget the A2A context held for a conversation.

        Args:
            conversation_id (str): PyRIT conversation ID.
        """
        self._conversations.pop(conversation_id, None)
