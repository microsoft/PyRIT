# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_none

from pyrit.memory import SQLiteMemory
from pyrit.models import Message, RequestTraceContext
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import HTTPTarget, HTTPXAPITarget, PromptTarget, TargetTraceConfig
from pyrit.prompt_target.common.target_send_context import TargetSendContext

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _target(*, api: bool, transport: httpx.MockTransport, enabled: bool = True) -> PromptTarget:
    if api:
        return HTTPXAPITarget(
            http_url="https://agent.test/",
            transport=transport,
            trace_config=TargetTraceConfig(enabled=enabled),
        )
    return HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
        transport=transport,
        trace_config=TargetTraceConfig(enabled=enabled),
    )


@pytest.mark.parametrize("api", [False, True])
async def test_concurrent_sends_store_the_emitted_context_async(sqlite_instance: SQLiteMemory, api: bool) -> None:
    emitted: list[str] = []

    async def respond_async(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        emitted.append(request.headers["traceparent"])
        assert emitted[-1].split("-")[1] == f"{trace.get_current_span().get_span_context().trace_id:032x}"
        return httpx.Response(200, text="done")

    target = _target(api=api, transport=httpx.MockTransport(respond_async))
    normalizer = PromptNormalizer()
    await asyncio.gather(
        *(
            normalizer.send_prompt_async(message=Message.from_prompt(prompt=f"request {i}", role="user"), target=target)
            for i in range(2)
        )
    )
    requests = sqlite_instance.get_message_pieces()
    links = [RequestTraceContext.from_metadata(piece.prompt_metadata) for piece in requests if piece.role == "user"]
    assert {link.traceparent for link in links if link} == set(emitted)
    assert len(set(emitted)) == 2
    assert all(
        RequestTraceContext.METADATA_KEY not in piece.prompt_metadata for piece in requests if piece.role == "assistant"
    )
    assert not trace.get_current_span().get_span_context().is_valid
    if isinstance(target, HTTPXAPITarget):
        assert target.headers == {}
    else:
        assert "traceparent" not in target.http_request


@pytest.mark.parametrize("api", [False, True])
async def test_http_tracing_is_disabled_by_default_async(api: bool) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert "traceparent" not in request.headers
        return httpx.Response(200, text="done")

    transport = httpx.MockTransport(respond)
    target = (
        HTTPXAPITarget(http_url="https://agent.test/", transport=transport)
        if api
        else HTTPTarget(http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}", transport=transport)
    )
    message = Message.from_prompt(prompt="run", role="user")
    await target.send_prompt_async(message=message)
    assert RequestTraceContext.from_metadata(message.get_piece().prompt_metadata) is None


@pytest.mark.parametrize("api", [False, True])
async def test_disabled_trace_removes_stale_links_async(api: bool) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert "traceparent" not in request.headers
        return httpx.Response(200, text="done")

    target = _target(api=api, transport=httpx.MockTransport(respond), enabled=False)
    message = Message.from_prompt(prompt="run", role="user")
    message.get_piece().prompt_metadata.update(
        RequestTraceContext(traceparent=f"00-{'1' * 32}-{'2' * 16}-01").to_metadata()
    )
    await target.send_prompt_async(message=message)
    assert RequestTraceContext.from_metadata(message.get_piece().prompt_metadata) is None


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("api", [False, True])
async def test_direct_target_response_excludes_request_metadata_async(api: bool, enabled: bool) -> None:
    target = _target(
        api=api,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="done")),
        enabled=enabled,
    )
    request = Message.from_prompt(prompt="run", role="user")
    responses = await target.send_prompt_async(message=request)
    assert request.get_piece().prompt_metadata[RequestTraceContext.REQUEST_METADATA_KEY] == 1
    assert (RequestTraceContext.from_metadata(request.get_piece().prompt_metadata) is not None) is enabled
    for response in responses:
        for piece in response.message_pieces:
            assert RequestTraceContext.METADATA_KEY not in piece.prompt_metadata
            assert RequestTraceContext.REQUEST_METADATA_KEY not in piece.prompt_metadata


@pytest.mark.parametrize("enabled", [False, True])
async def test_message_validation_failure_clears_inherited_trace_async(enabled: bool) -> None:
    target = _target(api=False, transport=httpx.MockTransport(lambda request: httpx.Response(200)), enabled=enabled)
    original = Message.from_prompt(prompt="run", role="user")
    original.get_piece().prompt_metadata.update(
        RequestTraceContext(traceparent=f"00-{'1' * 32}-{'2' * 16}-01").to_metadata()
    )
    duplicate = original.duplicate()
    with (
        patch.object(Message, "validate", side_effect=ValueError("invalid message")),
        patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send,
        pytest.raises(ValueError, match="invalid message"),
    ):
        await target.send_prompt_async(message=duplicate)
    send.assert_not_called()
    assert RequestTraceContext.from_metadata(duplicate.get_piece().prompt_metadata) is None
    assert duplicate.get_piece().prompt_metadata[RequestTraceContext.REQUEST_METADATA_KEY] == 1
    assert RequestTraceContext.from_metadata(original.get_piece().prompt_metadata) is not None


@pytest.mark.parametrize("header", ["TraceParent", "tracestate"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_manual_client_headers_require_disabled_tracing_async(header: str, enabled: bool) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers[header] == "manual"
        return httpx.Response(200, text="done")

    async with httpx.AsyncClient(headers={header: "manual"}, transport=httpx.MockTransport(respond)) as client:
        target = HTTPTarget.with_client(
            client=client,
            http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
            trace_config=TargetTraceConfig(enabled=enabled),
        )
        if enabled:
            with pytest.raises(ValueError, match="Manual trace headers"):
                await target.send_prompt_async(message=Message.from_prompt(prompt="run", role="user"))
        else:
            await target.send_prompt_async(message=Message.from_prompt(prompt="run", role="user"))
        assert client.headers[header] == "manual"


async def test_normalizer_copy_and_new_send_keep_distinct_links_async(sqlite_instance: SQLiteMemory) -> None:
    emitted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        emitted.append(request.headers["traceparent"])
        return httpx.Response(200, text="done")

    target = _target(api=False, transport=httpx.MockTransport(respond))
    original = Message.from_prompt(prompt="run", role="user")
    normalized = original.duplicate()
    with patch.object(target, "_get_normalized_conversation_async", new_callable=AsyncMock) as normalize:
        normalize.return_value = [normalized]
        await target.send_prompt_async(message=original)
    first = RequestTraceContext.from_metadata(original.get_piece().prompt_metadata)
    assert first == RequestTraceContext.from_metadata(normalized.get_piece().prompt_metadata)
    duplicate = original.duplicate()
    await target.send_prompt_async(message=duplicate)
    assert RequestTraceContext.from_metadata(original.get_piece().prompt_metadata) == first
    assert RequestTraceContext.from_metadata(duplicate.get_piece().prompt_metadata) != first
    assert len(set(emitted)) == 2


@pytest.mark.parametrize("cancel", [False, True])
async def test_send_failure_restores_ambient_trace_async(cancel: bool) -> None:
    parent = trace.NonRecordingSpan(
        trace.SpanContext(trace_id=1, span_id=2, is_remote=False, trace_flags=trace.TraceFlags(1))
    )

    async def fail_async(request: httpx.Request) -> httpx.Response:
        assert request.headers["traceparent"].split("-")[1] != f"{1:032x}"
        if cancel:
            raise asyncio.CancelledError
        raise ValueError("send failed")

    target = _target(api=False, transport=httpx.MockTransport(fail_async))
    with trace.use_span(parent), pytest.raises(asyncio.CancelledError if cancel else ValueError):
        try:
            await target.send_prompt_async(message=Message.from_prompt(prompt="run", role="user"))
        finally:
            assert trace.get_current_span() is parent


async def test_transport_retries_share_one_logical_request_trace_async() -> None:
    class RetryingHTTP(HTTPTarget):
        @retry(stop=stop_after_attempt(2), wait=wait_none(), retry=retry_if_exception_type(ValueError), reraise=True)
        async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
            responses: list[Message] = await super()._send_prompt_to_target_async(
                normalized_conversation=normalized_conversation
            )
            return responses

    emitted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        emitted.append(request.headers["traceparent"])
        if len(emitted) == 1:
            raise ValueError("retry")
        return httpx.Response(200, text="done")

    target = RetryingHTTP(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
        transport=httpx.MockTransport(respond),
        trace_config=TargetTraceConfig(enabled=True),
    )
    await target.send_prompt_async(message=Message.from_prompt(prompt="run", role="user"))
    assert len(emitted) == 2
    assert emitted[0] == emitted[1]


@pytest.mark.parametrize("value", ["bad", f"00-{'0' * 32}-{'1' * 16}-01", f"00-{'1' * 32}-{'0' * 16}-01"])
def test_invalid_trace_metadata_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        RequestTraceContext.from_metadata({RequestTraceContext.METADATA_KEY: value})


async def test_caller_owned_tracer_captures_isolated_sends_async() -> None:
    global_provider = trace.get_tracer_provider()
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("caller-owned")

    def respond(request: httpx.Request) -> httpx.Response:
        with tracer.start_as_current_span("tool") as span:
            assert request.headers["traceparent"].split("-")[1] == f"{span.get_span_context().trace_id:032x}"
        return httpx.Response(200, text="done")

    target = HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
        transport=httpx.MockTransport(respond),
        trace_config=TargetTraceConfig(tracer=tracer),
    )
    try:
        with tracer.start_as_current_span("unrelated") as ambient:
            await target.send_prompt_async(message=Message.from_prompt(prompt="run", role="user"))
            assert trace.get_current_span() is ambient
        spans = exporter.get_finished_spans()
        root = next(span for span in spans if span.name == "pyrit.target")
        tool = next(span for span in spans if span.name == "tool")
        assert root.parent is None
        assert root.context.trace_id != ambient.get_span_context().trace_id
        assert tool.parent.span_id == root.context.span_id
        assert trace.get_tracer_provider() is global_provider
    finally:
        provider.shutdown()


async def test_noop_tracer_fails_instead_of_storing_invalid_link_async() -> None:
    target = HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
        trace_config=TargetTraceConfig(tracer=trace.NoOpTracerProvider().get_tracer("disabled")),
    )
    send_context = MagicMock(spec=TargetSendContext)
    send_context.conversation_id = ""
    send_context.select_history.return_value = []
    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
        with pytest.raises(ValueError, match="valid request trace context"):
            await target.send_prompt_async(
                message=Message.from_prompt(prompt="run", role="user"), send_context=send_context
            )
        send.assert_not_called()
    send_context.mark_target_invoked.assert_not_called()


async def test_failed_http_send_keeps_only_request_trace_metadata_async(sqlite_instance: SQLiteMemory) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("No connection", request=request)

    target = _target(api=False, transport=httpx.MockTransport(fail))
    with pytest.raises(Exception, match="Error sending prompt"):
        await PromptNormalizer().send_prompt_async(
            message=Message.from_prompt(prompt="run", role="user"), target=target
        )
    pieces = sqlite_instance.get_message_pieces()
    request = next(piece for piece in pieces if piece.role == "user")
    response = next(piece for piece in pieces if piece.role == "assistant")
    assert RequestTraceContext.from_metadata(request.prompt_metadata) is not None
    assert RequestTraceContext.from_metadata(response.prompt_metadata) is None
