# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared fixtures for ``tests/unit/tools``.

Provides the minimal collaborators the tool-loop tests need to exercise
:func:`pyrit.tools.tool_loop` end-to-end without standing up real targets
or MCP transports:

* :class:`_FakeToolTarget` — a :class:`PromptTarget` subclass whose
  ``_send_prompt_to_target_async`` returns scripted messages from a queue
  and whose ``_get_normalized_conversation_async`` skips the memory round
  trip so decorator behavior is isolated from normalization.
* :class:`_RecordingToolBackend` — a :class:`ToolBackend` that records
  every dispatched call (for order-of-execution assertions) and returns
  results from a scripted queue.
* :class:`_CanonicalEnvelopeParser` — a :class:`ToolCallParser` that walks
  message pieces and parses the canonical ``function_call`` JSON envelope.

Helper message builders (``_make_user_message``,
``_make_assistant_text_message``, ``_make_assistant_function_call_message``)
produce the canonical envelope shape used by the OpenAI targets after the
C6 normalization commit.
"""

from __future__ import annotations

import json
import uuid
from collections import deque
from typing import Any

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.tools import (
    LocalToolBackend,
    ToolBackend,
    ToolCall,
    ToolCallParser,
    ToolEventBehavior,
    ToolEventPolicy,
)


def _make_user_message(text: str, *, conversation_id: str | None = None) -> Message:
    """Build a single-piece user :class:`Message` carrying *text*."""
    return Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value=text,
                original_value_data_type="text",
                conversation_id=conversation_id or str(uuid.uuid4()),
            )
        ]
    )


def _make_assistant_text_message(text: str, *, conversation_id: str | None = None) -> Message:
    """Build a single-piece assistant :class:`Message` carrying plain text."""
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=text,
                original_value_data_type="text",
                conversation_id=conversation_id or str(uuid.uuid4()),
            )
        ],
        skip_validation=True,
    )


def _make_function_call_piece(
    *,
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    conversation_id: str | None = None,
) -> MessagePiece:
    """Build one assistant ``function_call`` piece carrying the canonical envelope."""
    envelope = {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments, separators=(",", ":")),
    }
    return MessagePiece(
        role="assistant",
        original_value=json.dumps(envelope, separators=(",", ":")),
        original_value_data_type="function_call",
        conversation_id=conversation_id or str(uuid.uuid4()),
    )


def _make_assistant_function_call_message(
    *,
    calls: list[tuple[str, str, dict[str, Any]]],
    conversation_id: str | None = None,
) -> Message:
    """
    Build an assistant :class:`Message` carrying one ``function_call`` piece
    per ``(call_id, name, args)`` tuple, in declaration order.
    """
    conv_id = conversation_id or str(uuid.uuid4())
    pieces = [
        _make_function_call_piece(call_id=cid, name=name, arguments=args, conversation_id=conv_id)
        for cid, name, args in calls
    ]
    return Message(message_pieces=pieces, skip_validation=True)


class _CanonicalEnvelopeParser:
    """
    Reference :class:`ToolCallParser` that understands the canonical envelope
    (``original_value_data_type == "function_call"`` carrying a JSON object
    with ``type``/``call_id``/``name``/``arguments``).

    Per-target parsers shipped in C7/C8 will reuse this shape; this stand-in
    keeps decorator tests independent of the real OpenAI parsers.
    """

    def parse(self, message: Message) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for piece in message.message_pieces:
            if piece.original_value_data_type != "function_call":
                continue
            envelope = json.loads(piece.original_value)
            arguments_str = envelope.get("arguments", "{}")
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else dict(arguments_str)
            calls.append(
                ToolCall(
                    call_id=envelope["call_id"],
                    name=envelope["name"],
                    arguments=arguments,
                    raw_envelope=envelope,
                )
            )
        return calls


class _RecordingToolBackend(ToolBackend):
    """
    Minimal :class:`ToolBackend` that records every dispatched call and
    returns results from a scripted queue. Used to assert dispatch order,
    iteration count, and per-call payload shape without invoking real tools.
    """

    def __init__(
        self,
        *,
        scripted_results: list[Any] | None = None,
        schemas: list[dict[str, Any]] | None = None,
    ) -> None:
        self._results: deque[Any] = deque(scripted_results or [])
        self._schemas: list[dict[str, Any]] = list(schemas) if schemas is not None else []
        self.recorded_calls: list[ToolCall] = []

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return list(self._schemas)

    async def dispatch_async(self, call: ToolCall) -> dict[str, Any]:
        self.recorded_calls.append(call)
        if not self._results:
            return {"result": f"recorded:{call.name}:{call.call_id}"}
        nxt = self._results.popleft()
        return nxt if isinstance(nxt, dict) else {"result": nxt}


class _FakeToolTarget(PromptTarget):
    """
    Test-only :class:`PromptTarget` whose ``_send_prompt_to_target_async``
    pops scripted responses off a queue. ``_get_normalized_conversation_async``
    is overridden to return ``[message]`` directly, isolating decorator
    behavior from the memory + normalization pipeline.

    Inherits the base class's ``@final @tool_loop send_prompt_async``; the
    policy + backend are wired through :class:`TargetConfiguration` so the
    wrapper finds them via ``self.configuration.tool_event_policy`` and
    ``self.configuration.tool_backend``.
    """

    def __init__(
        self,
        *,
        scripted_responses: list[Message],
        policy: ToolEventPolicy | None = None,
        backend: Any = None,
        parser: ToolCallParser | None = None,
    ) -> None:
        # ``supports_tool_use`` is forced on whenever a policy is configured so
        # the TargetConfiguration validator accepts the backend.
        caps = TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_tool_use=policy is not None,
        )
        config = TargetConfiguration(
            capabilities=caps,
            tool_event_policy=policy,
            tool_backend=backend,
        )
        super().__init__(custom_configuration=config)
        self._scripted_responses: deque[Message] = deque(scripted_responses)
        self.call_count: int = 0
        self.normalized_conversations_seen: list[list[Message]] = []
        self._parser_instance: ToolCallParser | None = parser if parser is not None else _CanonicalEnvelopeParser()

    @property
    def _tool_parser(self) -> ToolCallParser | None:
        return self._parser_instance

    async def _get_normalized_conversation_async(self, *, message: Message) -> list[Message]:
        return [message]

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        return

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.call_count += 1
        self.normalized_conversations_seen.append(list(normalized_conversation))
        if not self._scripted_responses:
            raise AssertionError(f"Fake target ran out of scripted responses on iteration {self.call_count}.")
        return [self._scripted_responses.popleft()]


@pytest.fixture
def make_fake_target(patch_central_database):
    """
    Factory fixture for :class:`_FakeToolTarget`. Each invocation returns a
    fresh target instance whose scripted response queue is independent of
    other targets created during the test.
    """

    def _factory(
        *,
        scripted_responses: list[Message],
        policy: ToolEventPolicy | None = None,
        backend: Any = None,
        parser: ToolCallParser | None = None,
    ) -> _FakeToolTarget:
        return _FakeToolTarget(
            scripted_responses=scripted_responses,
            policy=policy,
            backend=backend,
            parser=parser,
        )

    return _factory


@pytest.fixture
def recording_backend():
    """Factory fixture for :class:`_RecordingToolBackend`."""

    def _factory(*, scripted_results: list[Any] | None = None) -> _RecordingToolBackend:
        return _RecordingToolBackend(scripted_results=scripted_results)

    return _factory


@pytest.fixture
def execute_policy():
    """
    Factory fixture for :class:`ToolEventPolicy` with
    ``behavior=ToolEventBehavior.EXECUTE`` and a tunable iteration cap.
    """

    def _factory(*, max_tool_iterations: int = 5) -> ToolEventPolicy:
        return ToolEventPolicy(
            behavior=ToolEventBehavior.EXECUTE,
            max_tool_iterations=max_tool_iterations,
        )

    return _factory


__all__ = [
    "LocalToolBackend",
    "ToolCall",
    "ToolEventBehavior",
    "ToolEventPolicy",
    "_CanonicalEnvelopeParser",
    "_FakeToolTarget",
    "_RecordingToolBackend",
    "_make_assistant_function_call_message",
    "_make_assistant_text_message",
    "_make_function_call_piece",
    "_make_user_message",
    "execute_policy",
    "make_fake_target",
    "recording_backend",
]
