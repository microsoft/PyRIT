# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pyrit.models import Message, MessagePiece
    from pyrit.tools.models import ToolCall


@runtime_checkable
class ToolCallParser(Protocol):
    """
    Protocol for extracting tool calls from a target response message.

    Concrete parsers live next to the target whose response shape they
    understand (the canonical-envelope parser shipped here is shared by
    :class:`OpenAIResponseTarget`; per-model-family parsers for non-OpenAI
    targets ship in a follow-up, see plan §12.9). Parsers MUST return an
    empty list when the model has issued a stop response — the tool loop
    uses the empty list as the signal to exit.
    """

    def parse(self, message: Message) -> list[ToolCall]:
        """
        Extract tool calls from a target response message.

        Args:
            message (Message): The most recent assistant response.

        Returns:
            list[ToolCall]: Tool calls, in declaration order. An empty list
                signals that the model produced a stop response.
        """
        ...


def _extract_function_call_pieces(message: Message) -> list[MessagePiece]:
    """
    Return every :class:`MessagePiece` in *message* whose
    ``original_value_data_type`` is ``"function_call"``.

    This is the canonical envelope used by every PyRIT-supported tool-emitting
    target. It is exposed here so concrete parsers can reuse the filter rather
    than re-implementing it.

    Args:
        message (Message): The message to scan.

    Returns:
        list[MessagePiece]: Pieces whose ``original_value_data_type`` is
            ``"function_call"``, in their declaration order.
    """
    return [piece for piece in message.message_pieces if piece.original_value_data_type == "function_call"]


class CanonicalEnvelopeParser:
    """
    Reference :class:`ToolCallParser` for the canonical function_call envelope.

    Walks every :class:`MessagePiece` whose ``original_value_data_type`` is
    ``"function_call"`` and decodes the canonical JSON shape::

        {
            "type": "function_call",
            "call_id": "<provider id>",
            "name": "<tool name>",
            "arguments": "<JSON-encoded arguments>"
        }

    into :class:`ToolCall` instances. Pieces of other data types -- reasoning,
    mcp_call, web_search_call, etc. -- are ignored (they pass through to
    Memory but are not client-side dispatchable). Per-model-family parsers
    for non-OpenAI targets ship in a follow-up PR (see plan §12.9).
    """

    def parse(self, message: Message) -> list[ToolCall]:
        """
        Decode canonical ``function_call`` pieces in *message* into :class:`ToolCall`.

        Args:
            message (Message): The most recent assistant response.

        Returns:
            list[ToolCall]: One :class:`ToolCall` per ``function_call``
                piece, in declaration order. Empty if the message contains
                no ``function_call`` pieces (model stop).
        """
        from pyrit.tools.models import ToolCall

        calls: list[ToolCall] = []
        for piece in _extract_function_call_pieces(message):
            envelope = json.loads(piece.original_value)
            arguments_raw = envelope.get("arguments", "{}")
            arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else dict(arguments_raw)
            calls.append(
                ToolCall(
                    call_id=envelope["call_id"],
                    name=envelope["name"],
                    arguments=arguments,
                    raw_envelope=envelope,
                )
            )
        return calls
