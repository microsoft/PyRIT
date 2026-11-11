# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Resolve the request traces behind a stored conversation or conversation prefix."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models import MessagePiece, MessageScorable, RequestTraceContext, TraceScorable
from pyrit.score.message_scorable_resolver import MessageScorableResolver

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface


@dataclass(frozen=True, kw_only=True)
class RequestTraceLink:
    """
    One outbound request in a conversation and the trace recorded when it was sent.

    ``linked`` is false when any piece of the request has no trace link; ``trace_id`` is the
    first recorded link, if any.
    """

    sequence: int
    trace_id: str | None
    linked: bool


async def resolve_conversation_trace_links_async(
    *, conversation_id: str, memory: MemoryInterface, through_sequence: int | None = None
) -> tuple[RequestTraceLink, ...]:
    """
    Resolve each outbound request's trace in sequence order.

    Requests are pieces marked as sent to a target, pieces carrying a trace link, or live
    user turns. Prepended history is not an outbound request.

    Args:
        conversation_id (str): The stored conversation.
        memory (MemoryInterface): The memory holding the conversation.
        through_sequence (int | None): Ignore requests after this sequence. Defaults to None.

    Returns:
        tuple[RequestTraceLink, ...]: One link per request sequence, in order.

    Raises:
        ValueError: If request trace metadata is invalid.
    """
    pieces_by_sequence: dict[int, list[MessagePiece]] = {}
    for piece in await memory.get_message_pieces_async(conversation_id=conversation_id):
        if through_sequence is not None and piece.sequence > through_sequence:
            continue
        if _is_outbound_request(piece):
            pieces_by_sequence.setdefault(piece.sequence, []).append(piece)
    links = []
    for sequence in sorted(pieces_by_sequence):
        contexts = [RequestTraceContext.from_metadata(piece.prompt_metadata) for piece in pieces_by_sequence[sequence]]
        trace_id = next((context.trace_id for context in contexts if context is not None), None)
        links.append(
            RequestTraceLink(
                sequence=sequence,
                trace_id=trace_id,
                linked=all(context is not None for context in contexts),
            )
        )
    return tuple(links)


def trace_scope_from_links(links: tuple[RequestTraceLink, ...]) -> TraceScorable | None:
    """
    Build the trace scope covering every linked request.

    Args:
        links (tuple[RequestTraceLink, ...]): The resolved request links.

    Returns:
        TraceScorable | None: The scope, or None when no request is linked.
    """
    trace_ids = tuple(dict.fromkeys(link.trace_id for link in links if link.trace_id is not None))
    return TraceScorable(trace_ids=trace_ids) if trace_ids else None


async def resolve_message_trace_scope_async(
    *, scorable: MessageScorable, memory: MemoryInterface
) -> tuple[TraceScorable | None, bool]:
    """
    Resolve outbound request traces through the named message, not later turns.

    Returns:
        The known trace scope and whether every selected request has a link.

    Raises:
        ValueError: If the message is not stored or its request metadata is invalid.
    """
    message = await MessageScorableResolver().resolve_async(scorable=scorable, memory=memory)
    piece = message.message_pieces[0]
    if not piece.conversation_id or piece.sequence < 0:
        raise ValueError("Trace resolution requires a stored conversation and message sequence.")
    links = await resolve_conversation_trace_links_async(
        conversation_id=piece.conversation_id, memory=memory, through_sequence=piece.sequence
    )
    return trace_scope_from_links(links), bool(links) and all(link.linked for link in links)


def _is_outbound_request(piece: MessagePiece) -> bool:
    return bool(
        piece.prompt_metadata.get(RequestTraceContext.REQUEST_METADATA_KEY) == 1
        or RequestTraceContext.METADATA_KEY in piece.prompt_metadata
        or (piece.role == "user" and not piece.prompt_metadata.get(MessagePiece.PREPENDED_HISTORY_METADATA_KEY))
    )
