# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import enum
import functools
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pyrit.exceptions import ToolCallLoopLimitExceeded, ToolCallNotSupported
from pyrit.models import Message, MessagePiece

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pyrit.tools.backend import ToolBackend
    from pyrit.tools.parsers import ToolCallParser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCall:
    """
    A parsed tool call extracted from a target response.

    Concrete :class:`~pyrit.tools.ToolCallParser` implementations build
    :class:`ToolCall` instances by walking the response message pieces.
    The :attr:`raw_envelope` carries the original target-specific dict
    (e.g. the function_call JSON section) so dispatchers and observers
    can recover provider-specific fields without re-parsing.

    Attributes:
        call_id (str): The provider-issued call identifier; must round-trip
            into the matching ``function_call_output`` piece.
        name (str): The tool name to dispatch.
        arguments (dict[str, Any]): The parsed JSON arguments.
        raw_envelope (dict[str, Any]): The original provider envelope.
    """

    call_id: str
    name: str
    arguments: dict[str, Any]
    raw_envelope: dict[str, Any] = field(default_factory=dict)


class ToolEventBehavior(enum.Enum):
    """
    What the tool loop should do when a target response contains a
    pending tool call.

    Values:
        EXECUTE: Dispatch the call via ``configuration.tool_backend``
            and re-enter the target with the tool output appended.
            This is the standard agentic loop behavior.
        RAISE: Raise :class:`~pyrit.exceptions.ToolCallNotSupported` with
            the partial conversation attached. Useful for red-team
            attacks that want to observe attempted tool use without
            allowing execution.
        RETURN_RAW: Return the assistant response containing the tool
            call as-is, without dispatching. Useful when a caller wants
            to inspect tool calls in-band (e.g. a scorer that scores
            attempted tool use).
    """

    EXECUTE = "execute"
    RAISE = "raise"
    RETURN_RAW = "return_raw"


@dataclass(frozen=True)
class ToolEventPolicy:
    """
    Per-target configuration that controls how the tool loop responds
    to a pending tool call from the model.

    Attributes:
        behavior (ToolEventBehavior): What to do on each detected tool call.
        max_tool_iterations (int): Maximum number of model<->tool round-trips
            before the loop raises :class:`ToolCallLoopLimitExceeded`. Each
            iteration is one ``_send_prompt_to_target_async`` call.
    """

    behavior: ToolEventBehavior
    max_tool_iterations: int = 5


def _build_function_call_output_message(
    *,
    reference_piece: MessagePiece,
    outputs: list[tuple[ToolCall, Any]],
) -> Message:
    """
    Build the canonical ``tool`` message produced after dispatching one or more
    tool calls in a single iteration.

    The returned :class:`Message` contains one
    :class:`MessagePiece` per ``(call, result)`` pair, in declaration order.
    Every piece has ``role="tool"`` and ``original_value_data_type="function_call_output"``,
    with the JSON envelope ``{"type": "function_call_output", "call_id": ..., "output": ...}``.

    Lineage metadata (conversation_id, identifiers) is copied from
    *reference_piece* — typically the first piece of the assistant message
    that issued the tool calls — so the resulting message stays inside the
    correct conversation.

    Args:
        reference_piece (MessagePiece): Piece whose lineage metadata is
            copied onto every output piece. Pass the first piece of the
            assistant message that produced the calls.
        outputs (list[tuple[ToolCall, Any]]): ``(call, result)`` pairs in
            declaration order. *result* is serialized via :func:`json.dumps`
            unless it is already a string.

    Returns:
        Message: One message carrying every function_call_output piece.
    """
    pieces: list[MessagePiece] = []
    for call, result in outputs:
        output_str = result if isinstance(result, str) else json.dumps(result, separators=(",", ":"))
        envelope = json.dumps(
            {"type": "function_call_output", "call_id": call.call_id, "output": output_str},
            separators=(",", ":"),
        )
        pieces.append(
            MessagePiece(
                role="tool",
                original_value=envelope,
                original_value_data_type="function_call_output",
                conversation_id=reference_piece.conversation_id,
                prompt_target_identifier=reference_piece.prompt_target_identifier,
                attack_identifier=reference_piece.attack_identifier,
            )
        )
    return Message(message_pieces=pieces, skip_validation=True)


def tool_loop(
    method: Callable[..., Awaitable[list[Message]]],
) -> Callable[..., Awaitable[list[Message]]]:
    """
    Wrap a :class:`~pyrit.prompt_target.PromptTarget`-style
    ``send_prompt_async`` to run an agentic tool-use loop.

    When the target's ``configuration.tool_event_policy`` is ``None`` the
    wrapper is a no-op — the wrapped method runs unchanged. When a policy
    is configured, the wrapper replaces the method body with the loop:

    1. Validate and normalize the incoming message exactly once.
    2. Repeatedly call ``self._send_prompt_to_target_async`` with the
       growing conversation.
    3. After each call, parse the last response via ``self._tool_parser``.
       Exit on empty parse (model issued a stop response).
    4. On a non-empty parse, branch on ``policy.behavior``:
       ``RAISE`` raises :class:`ToolCallNotSupported`; ``RETURN_RAW``
       returns the chain as-is; ``EXECUTE`` dispatches the calls via
       ``configuration.tool_backend`` and appends the tool message.
    5. Raise :class:`ToolCallLoopLimitExceeded` if the loop runs past
       ``policy.max_tool_iterations`` without the model stopping.

    The decorator deliberately knows nothing about MCP, OpenAI, or any
    specific transport. The two collaborators it requires —
    ``self._tool_parser`` and ``self.configuration.tool_backend`` — are
    plain protocols (:class:`ToolCallParser`, :class:`ToolBackend`).

    Args:
        method (Callable): The async method to wrap. Must have the
            ``async def f(self, *, message: Message) -> list[Message]``
            signature of :meth:`PromptTarget.send_prompt_async`.

    Returns:
        Callable: The wrapped method.
    """

    @functools.wraps(method)
    async def wrapper(self: Any, *, message: Message) -> list[Message]:
        policy: ToolEventPolicy | None = getattr(self.configuration, "tool_event_policy", None)
        if policy is None:
            return await method(self, message=message)

        message.validate()
        normalized_conversation = await self._get_normalized_conversation_async(message=message)
        if not normalized_conversation:
            raise ValueError("Normalization pipeline returned an empty conversation. Cannot send an empty request.")
        self._validate_request(normalized_conversation=normalized_conversation)

        parser: ToolCallParser | None = getattr(self, "_tool_parser", None)
        backend: ToolBackend | None = getattr(self.configuration, "tool_backend", None)
        max_iter = policy.max_tool_iterations

        all_responses: list[Message] = []

        for _ in range(max_iter):
            responses_this_turn = await self._send_prompt_to_target_async(
                normalized_conversation=normalized_conversation,
            )
            all_responses.extend(responses_this_turn)

            if parser is None:
                return all_responses

            last_response = responses_this_turn[-1]
            pending_calls = parser.parse(last_response)

            if not pending_calls:
                return all_responses

            if policy.behavior is ToolEventBehavior.RAISE:
                raise ToolCallNotSupported(
                    message=(
                        f"Target produced {len(pending_calls)} tool call(s) but ToolEventPolicy.behavior is RAISE."
                    ),
                    partial_conversation=all_responses,
                )

            if policy.behavior is ToolEventBehavior.RETURN_RAW:
                return all_responses

            if backend is None:
                raise ToolCallNotSupported(
                    message=(f"Target produced {len(pending_calls)} tool call(s) but no tool_backend is configured."),
                    partial_conversation=all_responses,
                )

            results = await backend.dispatch_all_sequential_async(pending_calls)
            tool_msg = _build_function_call_output_message(
                reference_piece=last_response.message_pieces[0],
                outputs=results,
            )
            all_responses.append(tool_msg)
            normalized_conversation = list(normalized_conversation) + [last_response, tool_msg]

        raise ToolCallLoopLimitExceeded(
            message=f"Tool loop exceeded max_tool_iterations={max_iter} without a stop response.",
            partial_conversation=all_responses,
        )

    return wrapper
