# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import JsonValue

from pyrit.models import (
    Acquisition,
    ExecutionStepKind,
    ExecutionTraceObservationPayload,
    TraceQueryResult,
    TraceScorable,
    TraceSpan,
    TraceSpanEvent,
    TraceSpanStatus,
)
from pyrit.score import InMemoryTraceClient, OtelExecutionTraceSource, TraceAcquisitionError

TRACE = "a" * 32
SCOPE = TraceScorable(trace_ids=(TRACE,))
START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def client() -> Iterator[InMemoryTraceClient]:
    client = InMemoryTraceClient()
    yield client
    client.close()


def _span(
    index: int,
    attributes: dict[str, JsonValue],
    *,
    name: str = "span",
    parent: int | None = None,
    events: tuple[TraceSpanEvent, ...] = (),
    status: TraceSpanStatus = TraceSpanStatus.OK,
    status_message: str | None = None,
) -> TraceSpan:
    return TraceSpan(
        trace_id=TRACE,
        span_id=f"{index:016x}",
        parent_span_id=f"{parent:016x}" if parent is not None else None,
        name=name,
        start_time=START + timedelta(milliseconds=index),
        end_time=START + timedelta(milliseconds=index + 10),
        attributes=attributes,
        events=events,
        status=status,
        status_message=status_message,
    )


def _tool(index: int, **attributes: JsonValue) -> TraceSpan:
    return _span(index, {"openinference.span.kind": "TOOL", "tool.name": "t", **attributes})


async def _acquire_async(client: InMemoryTraceClient, *spans: TraceSpan) -> ExecutionTraceObservationPayload:
    for span in spans:
        client.add_span(span)
    client.mark_complete(trace_ids=(TRACE,))
    observation = await OtelExecutionTraceSource(trace_client=client).acquire_async(scorable=SCOPE)
    assert isinstance(observation.payload, ExecutionTraceObservationPayload)
    return observation.payload


async def test_open_inference_spans_keep_messages_tool_calls_arguments_and_results_async(
    client: InMemoryTraceClient,
) -> None:
    payload = await _acquire_async(
        client,
        _span(1, {"openinference.span.kind": "AGENT", "langgraph.node.name": "planner"}, name="planner"),
        _span(
            2,
            {
                "openinference.span.kind": "LLM",
                "llm.model_name": "gpt-test",
                "llm.input_messages.0.message.role": "user",
                "llm.input_messages.0.message.contents.0.message_content.text": "first",
                "llm.input_messages.0.message.contents.10.message_content.text": "third",
                "llm.input_messages.0.message.contents.2.message_content.text": "second",
                "llm.output_messages.0.message.role": "assistant",
                "llm.output_messages.0.message.tool_calls.0.tool_call.function.name": "book_flight",
                "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments": '{"to": "SEA"}',
                "llm.token_count.prompt": 12,
                "llm.token_count.completion": 5,
            },
            parent=1,
        ),
        _span(
            3,
            {
                "openinference.span.kind": "TOOL",
                "tool.name": "book_flight",
                "input.value": '{"to": "SEA"}',
                "output.value": "Booked for $900",
            },
            parent=1,
        ),
    )

    agent, llm, tool = payload.steps
    assert (agent.kind, agent.node_name, agent.name) == (ExecutionStepKind.AGENT, "planner", "planner")
    assert (llm.kind, llm.model, llm.input_tokens, llm.output_tokens) == (ExecutionStepKind.LLM, "gpt-test", 12, 5)
    assert llm.input_content == "user: first\nsecond\nthird"
    assert llm.output_content == 'assistant: tool_call book_flight({"to": "SEA"})'
    assert (tool.kind, tool.name, tool.tool_name) == (ExecutionStepKind.TOOL, "book_flight", "book_flight")
    assert (tool.input_content, tool.output_content) == ('{"to": "SEA"}', "Booked for $900")
    assert tool.parent_span_id == agent.span_id
    assert payload.coverage.complete


async def test_gen_ai_spans_use_attributes_and_events_async(client: InMemoryTraceClient) -> None:
    payload = await _acquire_async(
        client,
        _span(
            1,
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "search",
                "gen_ai.tool.call.id": "call-1",
                "gen_ai.tool.call.arguments": {"query": "flights"},
                "gen_ai.tool.call.result": "3 results",
            },
        ),
        _span(
            2,
            {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 7},
            events=(
                TraceSpanEvent(name="gen_ai.user.message", attributes={"content": "hi"}),
                TraceSpanEvent(name="gen_ai.choice", attributes={"message": "hello"}),
            ),
        ),
        _span(3, {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "router"}),
    )

    tool, chat, agent = payload.steps
    assert tool.call_id == "call-1"
    assert json.loads(tool.input_content) == {"query": "flights"}
    assert tool.output_content == "3 results"
    assert (chat.kind, chat.model, chat.input_tokens) == (ExecutionStepKind.LLM, "m", 7)
    assert chat.input_content == 'gen_ai.user.message: {"content": "hi"}'
    assert chat.output_content == 'gen_ai.choice: {"message": "hello"}'
    assert (agent.kind, agent.node_name) == (ExecutionStepKind.AGENT, "router")


async def test_errors_and_unrecognized_spans_are_kept_with_exact_labels_async(client: InMemoryTraceClient) -> None:
    payload = await _acquire_async(
        client,
        _span(
            1,
            {"openinference.span.kind": "TOOL", "tool.name": "pay"},
            status=TraceSpanStatus.ERROR,
            events=(TraceSpanEvent(name="exception", attributes={"exception.message": "card declined"}),),
        ),
        _span(2, {"http.method": "GET", "langgraph.node.name": "  \n "}, name="GET /\n" + "n" * 500),
        _span(3, {"openinference.span.kind": "AGENT"}, name=" ", status=TraceSpanStatus.ERROR, status_message="boom"),
    )

    declined, unrecognized, unnamed = payload.steps
    assert declined.error_content == "card declined"
    assert (unrecognized.kind, unrecognized.name) == (ExecutionStepKind.OTHER, "GET /\n" + "n" * 500)
    assert unrecognized.node_name is None
    assert (unnamed.name, unnamed.error_content) == ("agent", "boom")


@pytest.mark.parametrize(
    ("value", "saved"),
    [
        ("booking record " + "x" * 50_000 + " charged_usd=900", "booking record " + "x" * 50_000 + " charged_usd=900"),
        ("ok \ud800 done", "ok � done"),
    ],
)
async def test_content_is_saved_complete_and_storable_async(
    client: InMemoryTraceClient, value: str, saved: str
) -> None:
    payload = await _acquire_async(client, _tool(1, **{"output.value": value}))

    assert payload.steps[0].output_content == saved
    assert ExecutionTraceObservationPayload.model_validate_json(payload.model_dump_json()) == payload


async def test_missing_or_conflicting_tool_names_make_coverage_partial_async(client: InMemoryTraceClient) -> None:
    for span in (
        _span(1, {"openinference.span.kind": "TOOL"}, name="unnamed"),
        _span(2, {"openinference.span.kind": "TOOL", "tool.name": "a", "gen_ai.tool.name": "b"}),
    ):
        client.add_span(span)
    client.mark_complete(trace_ids=(TRACE,))

    observation = await OtelExecutionTraceSource(trace_client=client).acquire_async(scorable=SCOPE)

    assert observation.acquisition is Acquisition.PARTIAL
    assert set(observation.payload.coverage.reasons) == {"tool_name_missing", "span_conventions_disagree"}
    assert [step.name for step in observation.payload.steps] == ["unnamed", "a"]


async def test_pending_failed_and_unavailable_traces_are_not_complete_evidence_async(
    client: InMemoryTraceClient,
) -> None:
    source = OtelExecutionTraceSource(trace_client=client)
    client.add_span(_tool(1))
    pending = await source.acquire_async(scorable=SCOPE)
    with patch.object(client, "get_spans_async", new_callable=AsyncMock, side_effect=TraceAcquisitionError("down")):
        failed = await source.acquire_async(scorable=SCOPE)
    with patch.object(
        client, "get_spans_async", new_callable=AsyncMock, return_value=TraceQueryResult(available=False)
    ):
        unavailable = await source.acquire_async(scorable=SCOPE)

    assert pending.acquisition is Acquisition.PARTIAL
    assert "capture_not_complete" in pending.payload.coverage.reasons
    assert (failed.acquisition, failed.payload.coverage.reasons) == (Acquisition.ERROR, ("trace_query_failed",))
    assert (unavailable.acquisition, unavailable.payload.steps) == (Acquisition.UNAVAILABLE, ())


@pytest.mark.parametrize("options", [{"max_spans": 0}, {"timeout_seconds": 0}])
def test_rejects_invalid_limits(client: InMemoryTraceClient, options: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        OtelExecutionTraceSource(trace_client=client, **options)


@pytest.mark.parametrize("reverse", [False, True])
async def test_equal_start_times_order_parents_first_then_by_end_time_async(
    client: InMemoryTraceClient, reverse: bool
) -> None:
    def tied(span_id: int, name: str, *, parent: int | None, end_ms: int) -> TraceSpan:
        return TraceSpan(
            trace_id=TRACE,
            span_id=f"{span_id:016x}",
            parent_span_id=f"{parent:016x}" if parent is not None else None,
            name=name,
            start_time=START,
            end_time=START + timedelta(milliseconds=end_ms),
        )

    # Exported as spans end: children first. Span IDs sort the root last.
    spans = [
        tied(3, "fetch", parent=2, end_ms=1),
        tied(2, "search", parent=255, end_ms=2),
        tied(1, "book", parent=255, end_ms=3),
        tied(255, "pyrit.target", parent=None, end_ms=5),
    ]

    payload = await _acquire_async(client, *(reversed(spans) if reverse else spans))

    assert [step.name for step in payload.steps] == ["pyrit.target", "search", "book", "fetch"]
    assert [step.index for step in payload.steps] == [0, 1, 2, 3]
