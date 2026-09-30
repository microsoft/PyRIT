# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Resolve the request traces behind a stored conversation prefix."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.models import MessagePiece, MessageScorable, RequestTraceContext, TraceScorable
from pyrit.score.message_scorable_resolver import MessageScorableResolver

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface


def resolve_message_trace_scope(
    *, scorable: MessageScorable, memory: MemoryInterface
) -> tuple[TraceScorable | None, bool]:
    """
    Resolve outbound request traces through the named message, not later turns.

    Returns:
        The known trace scope and whether every selected request has a link.

    Raises:
        ValueError: If the message is not stored or its request metadata is invalid.
    """
    message = MessageScorableResolver().resolve(scorable=scorable, memory=memory)
    piece = message.message_pieces[0]
    if not piece.conversation_id or piece.sequence < 0:
        raise ValueError("Trace resolution requires a stored conversation and message sequence.")
    requests = [
        request
        for request in memory.get_message_pieces(conversation_id=piece.conversation_id)
        if request.sequence <= piece.sequence
        and (
            request.prompt_metadata.get(RequestTraceContext.REQUEST_METADATA_KEY) == 1
            or RequestTraceContext.METADATA_KEY in request.prompt_metadata
            or (request.role == "user" and not request.prompt_metadata.get(MessagePiece.PREPENDED_HISTORY_METADATA_KEY))
        )
    ]
    links = [RequestTraceContext.from_metadata(request.prompt_metadata) for request in requests]
    trace_ids = tuple(dict.fromkeys(link.trace_id for link in links if link is not None))
    scope = TraceScorable(trace_ids=trace_ids) if trace_ids else None
    return scope, bool(links) and all(link is not None for link in links)
