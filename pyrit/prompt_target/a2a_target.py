# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable  # noqa: TC003 - registry annotation resolution
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Literal, cast
from weakref import WeakValueDictionary

import httpx

from pyrit.common.net_utility import get_httpx_client
from pyrit.exceptions import EmptyResponseException, RateLimitException, pyrit_target_retry
from pyrit.exceptions.exception_classes import CONTENT_FILTER_MARKERS
from pyrit.models import (
    ComponentIdentifier,
    Message,
    MessagePiece,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName, TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute

if TYPE_CHECKING:
    from a2a.client import Client
    from a2a.types import a2a_pb2

logger = logging.getLogger(__name__)


class _BearerTokenAuth(httpx.Auth):
    """Resolve bearer credentials for each HTTP request, including polls and retries."""

    def __init__(self, token: str | Callable[[], Awaitable[str]]) -> None:
        self._token = token

    async def async_auth_flow(  # pyrit-async-suffix-exempt
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = self._token if isinstance(self._token, str) else await self._token()
        if not isinstance(token, str) or not token.strip():
            raise ValueError("The A2A token provider must return a non-empty token string.")
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


@dataclass
class _A2AConversationState:
    """Identifiers that continue one PyRIT conversation on the agent (our context ID until the agent sends its own)."""

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
        auth_token: str | Callable[[], Awaitable[str]] | None = None,
        api_key: str | None = None,
        api_key_header: str = "X-API-Key",
        protocol_version: Literal["auto", "1.0", "0.3"] = "0.3",
        agent_card_path: str | None = None,
        routing_identifier: str | None = None,
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
            auth_token (str | Callable[[], Awaitable[str]] | None): Bearer token or async token provider.
                The provider is called before each HTTP request and owns token refresh and credential cleanup.
            api_key (str | None): Custom API key for the agent.
            api_key_header (str): Header name for the API key (defaults to "X-API-Key").
            protocol_version (Literal["auto", "1.0", "0.3"]): A2A protocol version. Defaults to "0.3".
            agent_card_path (str | None): Relative card path for automatic discovery.
            routing_identifier (str | None): Non-secret deployment label for header-based routing.
                This is stored in the identifier; do not put credentials here.
            task_timeout_seconds (float): Polling deadline after submission returns. Must be finite and positive.
            request_timeout_seconds (float | None): Timeout for individual HTTP requests. Defaults to
                task_timeout_seconds.
            poll_interval_seconds (float): Finite, non-negative delay between task polls. Defaults to 1.0.
            max_requests_per_minute (int | None): Rate limit for submission attempts and polls, excluding discovery.
            custom_configuration (TargetConfiguration | None): Custom target capabilities override.
            **httpx_client_kwargs: Additional HTTPX options. ``timeout`` supports HTTPX's per-phase
                settings or None to disable HTTP timeouts. Do not combine it with request_timeout_seconds.

        Raises:
            ImportError: If the a2a-sdk package is not installed.
            ValueError: If the protocol, capabilities, or timeouts are invalid, or authentication options conflict.
        """
        try:
            from a2a.client import ClientFactory  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The a2a-sdk package is required for A2ATarget. Install it with `pip install pyrit[a2a]`."
            ) from exc

        if protocol_version not in ("auto", "1.0", "0.3"):
            raise ValueError(
                f"Unsupported A2A protocol_version '{protocol_version}'. Expected 'auto', '1.0', or '0.3'."
            )

        if custom_configuration:
            self._validate_capabilities(custom_configuration.capabilities)

        for name, value in (
            ("task_timeout_seconds", task_timeout_seconds),
            ("request_timeout_seconds", request_timeout_seconds),
            ("poll_interval_seconds", poll_interval_seconds),
        ):
            allow_zero = name == "poll_interval_seconds"
            if value is not None and (not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero)):
                requirement = "non-negative" if allow_zero else "positive"
                raise ValueError(f"{name} must be finite and {requirement}.")
        if request_timeout_seconds is not None and "timeout" in httpx_client_kwargs:
            raise ValueError("Specify either request_timeout_seconds or HTTPX timeout, not both.")
        if auth_token is not None:
            if "auth" in httpx_client_kwargs:
                raise ValueError("Specify either auth_token or HTTPX auth, not both.")
            if "Authorization" in httpx.Headers(httpx_client_kwargs.get("headers")) or (
                api_key and api_key_header.lower() == "authorization"
            ):
                raise ValueError("Specify either auth_token or an Authorization header, not both.")
        if "timeout" in httpx_client_kwargs:
            timeout = httpx.Timeout(httpx_client_kwargs["timeout"])
            for value in timeout.as_dict().values():
                if value is not None and (not math.isfinite(value) or value <= 0):
                    raise ValueError("HTTPX timeouts must be finite and positive, or None.")

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
        self._agent_card_path = agent_card_path
        self._routing_identifier = routing_identifier
        self._task_timeout_seconds = task_timeout_seconds
        self._request_timeout_seconds = (
            request_timeout_seconds if request_timeout_seconds is not None else task_timeout_seconds
        )
        self._poll_interval_seconds = poll_interval_seconds
        self._conversations: dict[str, _A2AConversationState] = {}
        self._conversation_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._httpx_client_kwargs = httpx_client_kwargs

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "protocol_version": self._protocol_version,
                "agent_card_path": self._agent_card_path,
                "routing_identifier": self._routing_identifier,
            }
        )

    @staticmethod
    def _validate_capabilities(capabilities: TargetCapabilities) -> None:
        if not capabilities.supports_multi_turn:
            raise ValueError("A2ATarget requires supports_multi_turn=True because it maintains upstream context.")
        for name, modalities in (
            ("input", capabilities.input_modalities),
            ("output", capabilities.output_modalities),
        ):
            if modalities != frozenset({frozenset({"text"})}):
                raise ValueError(f"A2ATarget only supports text {name} modality.")
        for capability in CapabilityName:
            if capability != CapabilityName.MULTI_TURN and capabilities.includes(capability=capability):
                raise ValueError(f"A2ATarget does not support {capability.value}.")

    def apply_capabilities(self, *, capabilities: TargetCapabilities) -> None:
        """Replace capabilities only if the text-only adapter can implement them."""
        self._validate_capabilities(capabilities)
        super().apply_capabilities(capabilities=capabilities)

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        self._validate_capabilities(self.capabilities)
        super()._validate_request(normalized_conversation=normalized_conversation)

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
        }
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
            open_task_id (str | None): Open task ID waiting for input.

        Raises:
            ValueError: If context_id is empty.
            RuntimeError: If a send or reset is in progress for this conversation.
        """
        if not context_id.strip():
            raise ValueError("A2ATarget requires a non-empty upstream context ID.")
        lock = self._conversation_locks.get(conversation_id)
        if lock is not None and lock.locked():
            raise RuntimeError("Cannot replace an A2A context while its conversation is in use.")
        self._conversations[conversation_id] = _A2AConversationState(context_id=context_id, open_task_id=open_task_id)

    async def reset_conversation_async(self, *, conversation_id: str) -> None:
        """
        Forget the A2A context held for a conversation.

        Args:
            conversation_id (str): PyRIT conversation ID.
        """
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            self._conversations.pop(conversation_id, None)

    @staticmethod
    def _rate_limit_response(exc: Exception) -> httpx.Response | None:
        """Return only an explicit HTTP 429, not a rate-limit string in an agent error."""
        cause = exc if isinstance(exc, httpx.HTTPStatusError) else exc.__cause__
        if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 429:
            return cause.response
        return None

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                logger.warning("Invalid A2A Retry-After header; using polling backoff.")
                return None
        if not math.isfinite(delay):
            logger.warning("Non-finite A2A Retry-After header; using polling backoff.")
            return None
        return max(0.0, delay)

    @staticmethod
    def _extract_message_text(msg: a2a_pb2.Message) -> str:
        texts = [part.text for part in msg.parts if part.HasField("text")]
        return "\n".join(texts).strip()

    @staticmethod
    def _extract_task_artifacts_text(task: a2a_pb2.Task) -> str:
        texts: list[str] = []
        for artifact in task.artifacts:
            texts.extend(part.text for part in artifact.parts if part.HasField("text"))
        return "\n".join(texts).strip()

    async def _create_a2a_client_async(self, http_client: httpx.AsyncClient) -> Client:
        from a2a.client import ClientConfig, ClientFactory
        from a2a.types import a2a_pb2
        from a2a.utils.constants import TransportProtocol

        config = ClientConfig(streaming=False, httpx_client=http_client, accepted_output_modes=["text/plain"])
        factory = ClientFactory(config)

        if self._protocol_version == "auto":
            return await factory.create_from_url(self._endpoint, relative_card_path=self._agent_card_path)

        card = a2a_pb2.AgentCard(
            name="A2AAgent",
            supported_interfaces=[
                a2a_pb2.AgentInterface(
                    url=self._endpoint,
                    protocol_binding=TransportProtocol.JSONRPC,
                    protocol_version=self._protocol_version,
                )
            ],
        )
        return factory.create(card)

    async def _await_task_async(self, *, client: Client, task: a2a_pb2.Task) -> a2a_pb2.Task:
        from a2a.types import a2a_pb2
        from a2a.utils.errors import A2AError

        delay = self._poll_interval_seconds
        try:
            async with asyncio.timeout(self._task_timeout_seconds):
                while task.status.state in (a2a_pb2.TASK_STATE_SUBMITTED, a2a_pb2.TASK_STATE_WORKING):
                    await asyncio.sleep(delay)
                    try:
                        task = cast("a2a_pb2.Task", await self._get_task_async(client=client, task_id=task.id))
                        delay = self._poll_interval_seconds
                    except (A2AError, httpx.HTTPError) as exc:
                        response = self._rate_limit_response(exc)
                        if response is None:
                            raise
                        retry_after = self._retry_after_seconds(response)
                        delay = (
                            max(self._poll_interval_seconds, retry_after)
                            if retry_after is not None
                            else min(self._task_timeout_seconds, max(1.0, delay * 2))
                        )
                        logger.warning(
                            "Rate limit polling A2A task %s; retrying only the poll in %s seconds.", task.id, delay
                        )
        except TimeoutError as exc:
            state_name = a2a_pb2.TaskState.Name(task.status.state)
            raise TimeoutError(
                f"A2A task {task.id} still in state {state_name} after {self._task_timeout_seconds} seconds."
            ) from exc
        return task

    @limit_requests_per_minute
    async def _get_task_async(self, *, client: Client, task_id: str) -> a2a_pb2.Task:
        from a2a.types import a2a_pb2

        return await client.get_task(a2a_pb2.GetTaskRequest(id=task_id))

    @pyrit_target_retry
    @limit_requests_per_minute
    async def _submit_message_async(
        self, *, client: Client, request: a2a_pb2.SendMessageRequest
    ) -> a2a_pb2.StreamResponse | None:
        from a2a.utils.errors import A2AError

        stream = client.send_message(request)
        try:
            return await anext(stream, None)
        except (A2AError, httpx.HTTPError) as exc:
            if self._rate_limit_response(exc) is not None:
                raise RateLimitException(message=f"A2A endpoint rate limited: {exc}") from exc
            raise
        finally:
            if isinstance(stream, AsyncGenerator):
                await stream.aclose()

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Send the latest message to the agent, continuing the conversation's A2A context.

        Args:
            normalized_conversation (list[Message]): Normalized conversation with the current request last.

        Returns:
            list[Message]: A list containing the agent's response.

        Raises:
            ValueError: If no conversation is provided or upstream context is missing for multi-turn history.
            RateLimitException: If submission still returns HTTP 429 after retries.
            TimeoutError: If task execution times out.
            EmptyResponseException: If the agent returns an empty response without text.
        """
        if not normalized_conversation:
            raise ValueError("No conversation provided to A2ATarget.")
        conversation_id = normalized_conversation[-1].get_piece().conversation_id
        if not conversation_id:
            raise ValueError("A2ATarget requires a conversation ID.")
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            return [
                await self._send_message_async(
                    normalized_conversation=normalized_conversation, conversation_id=conversation_id
                )
            ]

    async def _send_message_async(self, *, normalized_conversation: list[Message], conversation_id: str) -> Message:
        from a2a.types import a2a_pb2
        from a2a.utils.errors import A2AError

        message_piece = normalized_conversation[-1].get_piece()
        state = self._conversations.get(conversation_id)
        if (state is None or not state.context_id) and len(normalized_conversation) > 1:
            raise ValueError(
                f"A2ATarget has no upstream context for conversation '{conversation_id}'. "
                "The target requires server-side context continuity for multi-turn conversations, "
                "and earlier turns cannot be restored."
            )
        # Name the context ourselves so turn 2 can continue it even when the agent's reply carries no
        # contextId (a plain Message may omit it) or the first turn came back as an error.
        state = self._conversations.setdefault(conversation_id, _A2AConversationState(context_id=str(uuid.uuid4())))
        prompt_text = message_piece.converted_value

        req = a2a_pb2.SendMessageRequest(
            message=a2a_pb2.Message(
                role=a2a_pb2.ROLE_USER,
                parts=[a2a_pb2.Part(text=prompt_text)],
                message_id=str(uuid.uuid4()),
                context_id=state.context_id or "",
                task_id=state.open_task_id or "",
            ),
            configuration=a2a_pb2.SendMessageConfiguration(return_immediately=False),
        )

        client_kwargs = dict(self._httpx_client_kwargs)
        headers = httpx.Headers(self._build_headers())
        headers.update(client_kwargs.pop("headers", {}))
        client_kwargs.setdefault("timeout", self._request_timeout_seconds)
        if self._auth_token is not None:
            client_kwargs["auth"] = _BearerTokenAuth(self._auth_token)
        async with get_httpx_client(use_async=True, headers=headers, **client_kwargs) as http_client:
            client = await self._create_a2a_client_async(http_client)

            try:
                resp_event = await self._submit_message_async(client=client, request=req)
            except (A2AError, httpx.HTTPError) as exc:
                return self._error_response(request=message_piece, error_text=str(exc))

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
                return construct_response_from_request(request=message_piece, response_text_pieces=[reply_text])

            if resp_event.HasField("task"):
                task = resp_event.task
                if task.context_id:
                    state.context_id = task.context_id
                task = await self._await_task_async(client=client, task=task)
                return self._task_response(request=message_piece, task=task, state=state)

            raise EmptyResponseException(message="A2A response had neither message nor task.")

    def _error_response(self, *, request: MessagePiece, error_text: str) -> Message:
        logger.warning("A2A agent at %s returned error: %s", self._endpoint, error_text)
        return construct_response_from_request(
            request=request,
            response_text_pieces=[error_text],
            response_type="error",
            error="blocked" if any(marker in error_text for marker in CONTENT_FILTER_MARKERS) else "unknown",
        )

    def _task_response(self, *, request: MessagePiece, task: a2a_pb2.Task, state: _A2AConversationState) -> Message:
        from a2a.types import a2a_pb2

        if task.context_id:
            state.context_id = task.context_id
        status_text = self._extract_message_text(task.status.message)
        state_name = a2a_pb2.TaskState.Name(task.status.state)
        if task.status.state in (
            a2a_pb2.TASK_STATE_FAILED,
            a2a_pb2.TASK_STATE_CANCELED,
            a2a_pb2.TASK_STATE_REJECTED,
        ):
            state.open_task_id = None
            return self._error_response(
                request=request, error_text=status_text or f"A2A task {task.id} ended with state {state_name}"
            )

        artifacts_text = self._extract_task_artifacts_text(task)
        if task.status.state in (a2a_pb2.TASK_STATE_INPUT_REQUIRED, a2a_pb2.TASK_STATE_AUTH_REQUIRED):
            state.open_task_id = task.id
            text = status_text or artifacts_text
            empty_message = f"A2A task {task.id} is in {state_name} state but returned no text prompt."
        elif task.status.state == a2a_pb2.TASK_STATE_COMPLETED:
            state.open_task_id = None
            text = artifacts_text or status_text
            empty_message = f"A2A task {task.id} completed but returned no text response."
        else:
            raise ValueError(f"A2A task {task.id} returned unsupported state {state_name}.")
        if not text:
            raise EmptyResponseException(message=empty_message)
        return construct_response_from_request(request=request, response_text_pieces=[text])
