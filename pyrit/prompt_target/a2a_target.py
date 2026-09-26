# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from pyrit.exceptions import EmptyResponseException, RateLimitException, pyrit_target_retry
from pyrit.exceptions.exception_classes import CONTENT_FILTER_MARKERS
from pyrit.models import (
    ComponentIdentifier,
    Message,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)

_a2a_cache: tuple[Any, ...] | None = None


def _get_a2a() -> tuple[Any, ...]:
    """
    Import and cache a2a-sdk modules lazily.

    Returns:
        tuple[Any, ...]: Cached tuple of a2a modules and classes.

    Raises:
        ImportError: If the a2a-sdk package is not installed.
    """
    global _a2a_cache
    if _a2a_cache is None:
        try:
            import a2a
            from a2a.client import ClientFactory
            from a2a.client.client_factory import ClientConfig, TransportProtocol
            from a2a.client.errors import A2AClientError, AgentCardResolutionError
            from a2a.types import a2a_pb2
            from a2a.utils.errors import MethodNotFoundError

            _a2a_cache = (
                a2a,
                a2a_pb2,
                ClientFactory,
                ClientConfig,
                TransportProtocol,
                A2AClientError,
                AgentCardResolutionError,
                MethodNotFoundError,
            )
        except ImportError as e:
            raise ImportError(
                "The a2a-sdk package is required for A2ATarget. Install it with `pip install pyrit[a2a]`."
            ) from e
    return _a2a_cache


@dataclass
class _A2AConversationState:
    """Server-issued identifiers that continue one PyRIT conversation on the agent."""

    context_id: str | None = None
    open_task_id: str | None = None


class A2ATarget(PromptTarget):
    """
    A PromptTarget for interacting with agents speaking the Agent-to-Agent (A2A) protocol.

    The Agent-to-Agent protocol defines task-based message exchange between autonomous agents.
    This target adapts PyRIT's prompt target interface to the official `a2a-sdk`, supporting:
    - A2A v0.3 compatibility (JSON-RPC `message/send` and `tasks/get`)
    - A2A v1.0 (JSON-RPC `SendMessage` and `GetTask`)
    - Agent card discovery (`protocol_version="auto"`)

    Each PyRIT conversation maps to an upstream A2A context (`context_id`), so the agent
    keeps its own server-side state across turns. Tasks left in `input-required` or
    `auth-required` states are continued with their task ID.

    Rate limits (429) during polling are retried against the existing task without resubmitting
    the prompt. Failed or canceled tasks produce error responses, and questions from
    `input-required` tasks are extracted from the task status message.
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
        protocol_version: Literal["auto", "1.0", "0.3"] = "0.3",
        task_timeout_seconds: float = 120.0,
        request_timeout_seconds: float | None = None,
        poll_interval_seconds: float = 1.0,
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
            protocol_version (Literal["auto", "1.0", "0.3"]): A2A protocol version. Defaults to "0.3".
            task_timeout_seconds (float): How long to poll a pending task before timing out. Defaults to 120.
            request_timeout_seconds (float, Optional): Timeout for individual HTTP requests. Defaults to
                task_timeout_seconds.
            poll_interval_seconds (float): Interval in seconds between task status polls. Defaults to 1.0.
            max_requests_per_minute (int, Optional): Rate limit cap for requests per minute.
            custom_configuration (TargetConfiguration, Optional): Custom target capabilities override.
            **httpx_client_kwargs: Additional keyword arguments passed to httpx.AsyncClient.

        Raises:
            ImportError: If the a2a-sdk package is not installed.
            ValueError: If protocol_version is unsupported or custom capabilities advertise unsupported features.
        """
        _get_a2a()

        if protocol_version not in ("auto", "1.0", "0.3"):
            raise ValueError(
                f"Unsupported A2A protocol_version '{protocol_version}'. Expected 'auto', '1.0', or '0.3'."
            )

        if custom_configuration:
            caps = custom_configuration.capabilities
            for mod_combo in caps.input_modalities:
                if any(m != "text" for m in mod_combo):
                    raise ValueError(
                        f"A2ATarget only supports text input modality, but custom_configuration specified: {mod_combo}"
                    )
            for mod_combo in caps.output_modalities:
                if any(m != "text" for m in mod_combo):
                    raise ValueError(
                        f"A2ATarget only supports text output modality, but custom_configuration specified: {mod_combo}"
                    )
            if caps.supports_system_prompt:
                raise ValueError("A2ATarget does not support system prompts.")

        super().__init__(
            endpoint=endpoint,
            max_requests_per_minute=max_requests_per_minute,
            custom_configuration=custom_configuration,
        )

        self._endpoint = endpoint.rstrip("/")
        self._auth_token = auth_token
        self._api_key = api_key
        self._api_key_header = api_key_header
        self._protocol_version: Literal["auto", "1.0", "0.3"] = protocol_version
        self._task_timeout_seconds = task_timeout_seconds
        self._request_timeout_seconds = (
            request_timeout_seconds if request_timeout_seconds is not None else task_timeout_seconds
        )
        self._poll_interval_seconds = poll_interval_seconds
        self._conversations: dict[str, _A2AConversationState] = {}
        self._httpx_client_kwargs = httpx_client_kwargs

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "endpoint": self._endpoint,
                "protocol_version": self._protocol_version,
                "task_timeout_seconds": self._task_timeout_seconds,
            }
        )

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if self._api_key:
            headers[self._api_key_header] = self._api_key
        return headers

    def set_conversation_context(
        self,
        *,
        conversation_id: str,
        context_id: str,
        open_task_id: str | None = None,
    ) -> None:
        """
        Explicitly set or restore the upstream A2A context for a conversation.

        Args:
            conversation_id (str): PyRIT conversation ID.
            context_id (str): Upstream A2A context ID.
            open_task_id (str, Optional): Open task ID waiting for input.
        """
        self._conversations[conversation_id] = _A2AConversationState(context_id=context_id, open_task_id=open_task_id)

    async def reset_conversation_async(self, *, conversation_id: str) -> None:
        """
        Forget the A2A context held for a conversation.

        Args:
            conversation_id (str): PyRIT conversation ID.
        """
        self._conversations.pop(conversation_id, None)

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        """
        Check if an exception represents an HTTP or relayed rate limit (429).

        Args:
            exc (Exception): The exception to inspect.

        Returns:
            bool: True if the exception corresponds to a rate limit (429).
        """
        if isinstance(exc, RateLimitException):
            return True
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 429:
            return True
        msg = str(exc)
        return "429" in msg or "rate limit" in msg.lower() or "too many requests" in msg.lower()

    @staticmethod
    def _extract_message_text(msg: Any) -> str:
        texts: list[str] = [str(part.text) for part in msg.parts if getattr(part, "text", None)]
        return "\n".join(texts).strip()

    @staticmethod
    def _extract_task_artifacts_text(task: Any) -> str:
        texts: list[str] = []
        for artifact in getattr(task, "artifacts", []):
            texts.extend(str(part.text) for part in getattr(artifact, "parts", []) if getattr(part, "text", None))
        return "\n".join(texts).strip()

    async def _create_a2a_client(self, http_client: httpx.AsyncClient) -> Any:
        (
            _,
            a2a_pb2,
            client_factory_cls,
            client_config_cls,
            transport_protocol_enum,
            _,
            agent_card_resolution_error_cls,
            _,
        ) = _get_a2a()

        config = client_config_cls(streaming=False, httpx_client=http_client)
        factory = client_factory_cls(config)

        if self._protocol_version == "auto":
            try:
                return await factory.create_from_url(self._endpoint)
            except agent_card_resolution_error_cls:
                logger.info(
                    "Agent card resolution failed at %s; defaulting to protocol version 0.3",
                    self._endpoint,
                )
                protocol_ver = "0.3"
        else:
            protocol_ver = self._protocol_version

        card = a2a_pb2.AgentCard(
            name="A2AAgent",
            supported_interfaces=[
                a2a_pb2.AgentInterface(
                    url=self._endpoint,
                    protocol_binding=transport_protocol_enum.JSONRPC,
                    protocol_version=protocol_ver,
                )
            ],
        )
        return factory.create(card)

    async def _await_task_async(self, client: Any, task: Any) -> Any:
        _, a2a_pb2, _, _, _, _, _, _ = _get_a2a()
        deadline = time.monotonic() + self._task_timeout_seconds

        while True:
            if task.status.state not in (
                a2a_pb2.TASK_STATE_SUBMITTED,
                a2a_pb2.TASK_STATE_WORKING,
            ):
                return task

            if time.monotonic() >= deadline:
                state_name = a2a_pb2.TaskState.Name(task.status.state)
                raise TimeoutError(
                    f"A2A task {task.id} still in state {state_name} after {self._task_timeout_seconds} seconds."
                )

            await asyncio.sleep(self._poll_interval_seconds)

            try:
                task = await client.get_task(a2a_pb2.GetTaskRequest(id=task.id))
            except Exception as exc:
                if self._is_rate_limit(exc):
                    logger.warning(
                        "Rate limit encountered while polling A2A task %s; retrying poll without resubmitting prompt.",
                        task.id,
                    )
                    continue
                raise

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
            ValueError: If no conversation is provided or upstream context is missing for multi-turn history.
            RateLimitException: If the agent, or the model behind it, is rate limited.
            TimeoutError: If task execution times out.
            EmptyResponseException: If the agent returns an empty response without text.
        """
        if not normalized_conversation:
            raise ValueError("No conversation provided to A2ATarget.")

        message_piece = normalized_conversation[-1].get_piece()
        conversation_id = message_piece.conversation_id or ""
        if conversation_id not in self._conversations and len(normalized_conversation) > 1:
            raise ValueError(
                f"A2ATarget has no upstream context for conversation '{conversation_id}'. "
                "The target requires server-side context continuity for multi-turn conversations, "
                "and earlier turns cannot be restored."
            )
        state = self._conversations.setdefault(conversation_id, _A2AConversationState())
        prompt_text = message_piece.converted_value

        _, a2a_pb2, _, _, _, _, _, _ = _get_a2a()

        msg_kwargs: dict[str, Any] = {
            "role": a2a_pb2.ROLE_USER,
            "parts": [a2a_pb2.Part(text=prompt_text)],
            "message_id": str(uuid.uuid4()),
        }
        if state.context_id:
            msg_kwargs["context_id"] = state.context_id
        if state.open_task_id:
            msg_kwargs["task_id"] = state.open_task_id

        req = a2a_pb2.SendMessageRequest(
            message=a2a_pb2.Message(**msg_kwargs),
            configuration=a2a_pb2.SendMessageConfiguration(return_immediately=False),
        )

        headers = self._build_headers()
        client_kwargs = dict(self._httpx_client_kwargs)
        existing_headers = client_kwargs.pop("headers", None)
        if existing_headers:
            headers.update(existing_headers)
        timeout = client_kwargs.pop("timeout", None)
        if timeout is None:
            timeout = httpx.Timeout(self._request_timeout_seconds)

        async with httpx.AsyncClient(headers=headers, timeout=timeout, **client_kwargs) as http_client:
            client = await self._create_a2a_client(http_client)

            try:
                stream = client.send_message(req)
                resp_event = None
                async for event in stream:
                    resp_event = event
                    break
            except Exception as exc:
                if self._is_rate_limit(exc):
                    raise RateLimitException(message=f"A2A agent rate limited: {exc}") from exc
                error_text = str(exc)
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

            if resp_event is None:
                raise EmptyResponseException(message="A2A agent returned an empty response stream.")

            if resp_event.HasField("message"):
                msg = resp_event.message
                if msg.context_id:
                    state.context_id = msg.context_id
                state.open_task_id = None
                reply_text = self._extract_message_text(msg)
                if not reply_text:
                    raise EmptyResponseException(message=f"A2A message {msg.message_id} contained no text content.")
                return [
                    construct_response_from_request(
                        request=message_piece,
                        response_text_pieces=[reply_text],
                    )
                ]

            if resp_event.HasField("task"):
                task = resp_event.task
                task = await self._await_task_async(client, task)
                if task.context_id:
                    state.context_id = task.context_id

                # Task failure states
                if task.status.state in (
                    a2a_pb2.TASK_STATE_FAILED,
                    a2a_pb2.TASK_STATE_CANCELED,
                    a2a_pb2.TASK_STATE_REJECTED,
                ):
                    state.open_task_id = None
                    state_name = a2a_pb2.TaskState.Name(task.status.state)
                    error_msg = ""
                    if task.HasField("status") and task.status.HasField("message"):
                        error_msg = self._extract_message_text(task.status.message)
                    error_text = error_msg or f"A2A task {task.id} ended with state {state_name}"
                    is_blocked = any(marker in error_text for marker in CONTENT_FILTER_MARKERS)
                    logger.warning("A2A task %s ended with error state %s: %s", task.id, state_name, error_text)
                    return [
                        construct_response_from_request(
                            request=message_piece,
                            response_text_pieces=[error_text],
                            response_type="error",
                            error="blocked" if is_blocked else "unknown",
                        )
                    ]

                # Input required states
                if task.status.state in (
                    a2a_pb2.TASK_STATE_INPUT_REQUIRED,
                    a2a_pb2.TASK_STATE_AUTH_REQUIRED,
                ):
                    state.open_task_id = task.id
                    question = ""
                    if task.HasField("status") and task.status.HasField("message"):
                        question = self._extract_message_text(task.status.message)
                    if not question:
                        question = self._extract_task_artifacts_text(task)
                    if not question:
                        state_name = a2a_pb2.TaskState.Name(task.status.state)
                        raise EmptyResponseException(
                            message=f"A2A task {task.id} is in {state_name} state but returned no text prompt."
                        )
                    return [
                        construct_response_from_request(
                            request=message_piece,
                            response_text_pieces=[question],
                        )
                    ]

                # Completed state
                state.open_task_id = None
                text = self._extract_task_artifacts_text(task)
                if not text and task.HasField("status") and task.status.HasField("message"):
                    text = self._extract_message_text(task.status.message)
                if not text:
                    raise EmptyResponseException(message=f"A2A task {task.id} completed but returned no text response.")
                return [
                    construct_response_from_request(
                        request=message_piece,
                        response_text_pieces=[text],
                    )
                ]

            raise EmptyResponseException(message="A2A response had neither message nor task.")
