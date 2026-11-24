# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Isolated request tracing without a global SDK provider."""

import secrets
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, Tracer, use_span

from pyrit.models import Message, ProviderTraceReference, RequestTraceContext


@dataclass(frozen=True, kw_only=True)
class TargetTraceConfig:
    """
    Configure propagation and optional capture with a caller-owned tracer.

    ``enabled`` sends a fresh W3C ``traceparent`` with each request and records it. Separately,
    targets that report the provider's response id, such as ``LiteLLMChatTarget``, record it as a
    ``ProviderTraceReference``, so a provider that does not accept W3C context can still be linked
    to its trace by a backend that resolves that id. A request with a W3C link is resolved through
    that link, so for a provider that does not continue W3C context, set ``enabled=False``.
    """

    enabled: bool = True
    tracer: Tracer | None = None


@contextmanager
def target_trace_context(
    *, config: TargetTraceConfig, request: Message, normalized_request: Message
) -> Generator[None, None, None]:
    """
    Record and activate a fresh context only for this target invocation.

    Raises:
        ValueError: If the configured tracer produces no valid context.
    """
    for message in (request, normalized_request):
        for piece in message.message_pieces:
            piece.prompt_metadata.pop(RequestTraceContext.METADATA_KEY, None)
            ProviderTraceReference.clear_metadata(piece.prompt_metadata)
            piece.prompt_metadata[RequestTraceContext.REQUEST_METADATA_KEY] = 1
    if not config.enabled:
        yield
        return

    conversation_id = request.get_piece().conversation_id
    attributes = {"session.id": conversation_id, "pyrit.conversation_id": conversation_id} if conversation_id else {}
    span_context = (
        config.tracer.start_as_current_span("pyrit.target", context=Context(), attributes=attributes)
        if config.tracer is not None
        else use_span(
            NonRecordingSpan(
                SpanContext(
                    trace_id=secrets.randbits(128) or 1,
                    span_id=secrets.randbits(64) or 1,
                    is_remote=False,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )
            ),
            end_on_exit=True,
        )
    )
    with span_context as span:
        context = span.get_span_context()
        if not context.is_valid:
            raise ValueError("The configured tracer must produce a valid request trace context.")
        link = RequestTraceContext(
            traceparent=f"00-{context.trace_id:032x}-{context.span_id:016x}-{context.trace_flags:02x}"
        )
        for message in (request, normalized_request):
            for piece in message.message_pieces:
                piece.prompt_metadata.update(link.to_metadata())
        yield


def request_trace_headers(
    *, request: Message, headers: Mapping[str, str], default_headers: Mapping[str, str] | None = None
) -> dict[str, str]:
    """
    Add the recorded context without changing shared headers.

    Returns:
        dict[str, str]: Headers for this request.

    Raises:
        ValueError: If manual trace headers conflict with automatic tracing.
    """
    result = dict(headers)
    link = RequestTraceContext.from_metadata(request.get_piece().prompt_metadata)
    if link is None:
        return result
    for configured in (headers, default_headers or {}):
        if any(name.lower() in {"traceparent", "tracestate"} for name in configured):
            raise ValueError(
                "Manual trace headers conflict with automatic tracing. Set TargetTraceConfig(enabled=False)."
            )
    result["traceparent"] = link.traceparent
    return result


def record_provider_trace_reference(*, request: Message, reference: ProviderTraceReference | None) -> None:
    """
    Record the response id a provider returned for this request.

    The base target copies the record from the normalized request to the stored request.

    Args:
        request (Message): The normalized request that was sent.
        reference (ProviderTraceReference | None): The identifier, or None when the provider
            returned none.
    """
    if reference is None:
        return
    for piece in request.message_pieces:
        piece.prompt_metadata.update(reference.to_metadata())
