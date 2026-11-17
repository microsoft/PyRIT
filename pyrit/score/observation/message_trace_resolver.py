# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Resolve the request traces behind a stored conversation or conversation prefix."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pyrit.models import MessagePiece, MessageScorable, ProviderTraceReference, RequestTraceContext, TraceScorable
from pyrit.score.message_scorable_resolver import MessageScorableResolver
from pyrit.score.observation.trace_client import TraceAcquisitionError

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface
    from pyrit.score.observation.provider_trace_resolver import ProviderTraceResolver

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class RequestTraceLink:
    """
    One outbound request in a conversation and the trace recorded when it was sent.

    ``linked`` is true when every piece of the request carries a W3C trace link, or when the
    identifiers the provider returned resolved to one trace. ``trace_id`` is the first recorded
    W3C link, otherwise the resolved trace, if any. ``provider_reference`` is the provider's own
    identifiers, kept so an unresolved request can be explained.
    """

    sequence: int
    trace_id: str | None
    linked: bool
    provider_reference: ProviderTraceReference | None = None


async def resolve_conversation_trace_links_async(
    *,
    conversation_id: str,
    memory: MemoryInterface,
    through_sequence: int | None = None,
    provider_resolver: ProviderTraceResolver | None = None,
) -> tuple[RequestTraceLink, ...]:
    """
    Resolve each outbound request's trace in sequence order.

    Requests are pieces marked as sent to a target, pieces carrying a trace link, or live
    user turns. Prepended history is not an outbound request. A request without a W3C link is
    linked through the identifiers its provider returned: directly when the provider reported
    its trace id, otherwise through ``provider_resolver``.

    Args:
        conversation_id (str): The stored conversation.
        memory (MemoryInterface): The memory holding the conversation.
        through_sequence (int | None): Ignore requests after this sequence. Defaults to None.
        provider_resolver (ProviderTraceResolver | None): Maps provider identifiers to traces.
            Defaults to None, in which case only provider-reported trace ids link a request.

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
        pieces = pieces_by_sequence[sequence]
        contexts = [RequestTraceContext.from_metadata(piece.prompt_metadata) for piece in pieces]
        references = [ProviderTraceReference.from_metadata(piece.prompt_metadata) for piece in pieces]
        links.append(
            RequestTraceLink(
                sequence=sequence,
                trace_id=next((context.trace_id for context in contexts if context is not None), None),
                linked=all(context is not None for context in contexts),
                provider_reference=next((reference for reference in references if reference is not None), None),
            )
        )
    return await _link_through_providers_async(links=links, provider_resolver=provider_resolver)


async def _link_through_providers_async(
    *, links: list[RequestTraceLink], provider_resolver: ProviderTraceResolver | None
) -> tuple[RequestTraceLink, ...]:
    """
    Link the requests that have no W3C link but carry provider identifiers.

    Returns:
        tuple[RequestTraceLink, ...]: The links, with resolved requests linked to their trace.

    Raises:
        ValueError: If the resolver does not answer each reference exactly once.
    """
    pending: dict[int, ProviderTraceReference] = {}
    for index, link in enumerate(links):
        reference = link.provider_reference
        if link.trace_id is not None or reference is None:
            continue
        if reference.trace_id is not None:
            # The provider named its trace, so no resolver can drop or replace the link.
            links[index] = replace(link, trace_id=reference.trace_id, linked=True)
        else:
            pending[index] = reference
    if not pending or provider_resolver is None:
        return tuple(links)
    try:
        resolved = await provider_resolver.resolve_trace_ids_async(references=list(pending.values()))
    except TraceAcquisitionError:
        # Like a failed trace query, a backend that cannot be read leaves the requests unlinked.
        logger.warning("Provider trace resolution failed; the affected requests stay unlinked.")
        return tuple(links)
    if len(resolved) != len(pending):
        raise ValueError("A provider trace resolver must answer each reference once.")
    for index, trace_id in zip(pending, resolved, strict=True):
        if trace_id is not None:
            links[index] = replace(links[index], trace_id=trace_id, linked=True)
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
    *, scorable: MessageScorable, memory: MemoryInterface, provider_resolver: ProviderTraceResolver | None = None
) -> tuple[TraceScorable | None, bool]:
    """
    Resolve outbound request traces through the named message, not later turns.

    Args:
        scorable (MessageScorable): The scored message.
        memory (MemoryInterface): The memory holding the conversation.
        provider_resolver (ProviderTraceResolver | None): Maps provider identifiers to traces.
            Defaults to None.

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
        conversation_id=piece.conversation_id,
        memory=memory,
        through_sequence=piece.sequence,
        provider_resolver=provider_resolver,
    )
    return trace_scope_from_links(links), bool(links) and all(link.linked for link in links)


def _is_outbound_request(piece: MessagePiece) -> bool:
    return bool(
        piece.prompt_metadata.get(RequestTraceContext.REQUEST_METADATA_KEY) == 1
        or RequestTraceContext.METADATA_KEY in piece.prompt_metadata
        or (piece.role == "user" and not piece.prompt_metadata.get(MessagePiece.PREPENDED_HISTORY_METADATA_KEY))
    )
