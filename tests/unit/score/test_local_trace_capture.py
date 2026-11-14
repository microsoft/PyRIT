# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import threading
from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan

from pyrit.models import (
    Acquisition,
    ExecutionStepKind,
    Message,
    MessagePiece,
    RequestTraceContext,
    TraceQuery,
    TraceScorable,
)
from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration
from pyrit.registry import SourceRegistry
from pyrit.score import InMemoryTraceExporter, LocalTraceCapture, OtelExecutionTraceSource

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture
def capture() -> Iterator[LocalTraceCapture]:
    capture = LocalTraceCapture()
    yield capture
    capture.close()


class _AgentTarget(PromptTarget):
    """An in-process agent: a short PromptTarget subclass that records spans in the request trace."""

    _DEFAULT_CONFIGURATION = TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True))

    def __init__(self, *, capture: LocalTraceCapture) -> None:
        super().__init__(trace_config=capture.trace_config())
        self._tracer = capture.tracer
        self.background: asyncio.Task[None] | None = None

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1].get_piece()
        with self._tracer.start_as_current_span(
            "lookup", attributes={"openinference.span.kind": "TOOL", "tool.name": "lookup", "input.value": "q"}
        ):
            pass
        if request.original_value == "background":
            span = self._tracer.start_span("cleanup", attributes={"openinference.span.kind": "TOOL", "tool.name": "x"})

            async def finish_async() -> None:
                await asyncio.sleep(0)
                span.end()

            self.background = asyncio.ensure_future(finish_async())
        return [
            MessagePiece(role="assistant", original_value="done", conversation_id=request.conversation_id).to_message()
        ]


async def _send_async(target: _AgentTarget, text: str = "hello") -> str:
    request = MessagePiece(role="user", original_value=text, conversation_id="conversation").to_message()
    await target.send_prompt_async(message=request)
    link = RequestTraceContext.from_metadata(request.get_piece().prompt_metadata)
    assert link is not None
    return link.trace_id


async def test_target_trace_is_complete_once_its_spans_end_async(capture: LocalTraceCapture) -> None:
    trace_id = await _send_async(_AgentTarget(capture=capture))

    observation = await capture.source().acquire_async(scorable=TraceScorable(trace_ids=(trace_id,)))

    assert observation.acquisition is Acquisition.COMPLETE
    root, tool = observation.payload.steps
    assert root.name == "pyrit.target"
    assert (tool.kind, tool.tool_name, tool.parent_span_id) == (ExecutionStepKind.TOOL, "lookup", root.span_id)


async def test_target_span_records_conversation_as_session_async(capture: LocalTraceCapture) -> None:
    trace_id = await _send_async(_AgentTarget(capture=capture))

    spans = (await capture.client.get_spans_async(query=_query(trace_id))).spans
    root = next(span for span in spans if span.name == "pyrit.target")
    assert root.attributes["session.id"] == "conversation"
    assert root.attributes["pyrit.conversation_id"] == "conversation"


async def test_background_span_delays_completion_until_it_ends_async(capture: LocalTraceCapture) -> None:
    target = _AgentTarget(capture=capture)
    trace_id = await _send_async(target, "background")
    scope = TraceScorable(trace_ids=(trace_id,))

    pending = await capture.source().acquire_async(scorable=scope)
    assert target.background is not None
    await target.background
    finished = await capture.source().acquire_async(scorable=scope)

    assert pending.acquisition is Acquisition.PARTIAL
    assert finished.acquisition is Acquisition.COMPLETE
    assert len(finished.payload.steps) == 3


async def test_late_span_invalidates_completion_async(capture: LocalTraceCapture) -> None:
    trace_id = await _send_async(_AgentTarget(capture=capture))
    root = (await capture.client.get_spans_async(query=_query(trace_id))).spans[0]
    parent = trace.NonRecordingSpan(
        trace.SpanContext(
            trace_id=int(trace_id, 16),
            span_id=int(root.span_id, 16),
            is_remote=True,
            trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED),
        )
    )
    with capture.tracer.start_as_current_span("late", context=trace.set_span_in_context(parent, Context())):
        pass

    observation = await capture.source().acquire_async(scorable=TraceScorable(trace_ids=(trace_id,)))

    assert observation.acquisition is Acquisition.PARTIAL


async def test_without_auto_complete_coverage_stays_incomplete_async() -> None:
    capture = LocalTraceCapture(auto_complete=False)
    try:
        trace_id = await _send_async(_AgentTarget(capture=capture))
        observation = await capture.source().acquire_async(scorable=TraceScorable(trace_ids=(trace_id,)))
    finally:
        capture.close()

    assert observation.acquisition is Acquisition.PARTIAL
    assert "capture_not_complete" in observation.payload.coverage.reasons


def test_trace_config_enables_tracing_with_the_capture_tracer(capture: LocalTraceCapture) -> None:
    config = capture.trace_config()
    assert config.enabled
    assert config.tracer is not None
    assert _AgentTarget(capture=capture).trace_config.enabled


def test_source_options_are_forwarded(capture: LocalTraceCapture) -> None:
    source = capture.source(retain_content=False, timeout_seconds=7)
    assert isinstance(source, OtelExecutionTraceSource)
    params = source.get_identifier().params
    assert (params["retain_content"], params["timeout_seconds"]) == (False, 7)


def test_register_makes_the_source_nameable(capture: LocalTraceCapture) -> None:
    SourceRegistry.reset_registry_singleton()
    try:
        source = capture.register(name="local_app")
        assert SourceRegistry.get_registry_singleton().instances.get("local_app") is source
    finally:
        SourceRegistry.reset_registry_singleton()


def test_instrument_enables_only_open_inference_instrumentors_on_the_capture(capture: LocalTraceCapture) -> None:
    instrumentor = MagicMock()
    entries = [
        SimpleNamespace(
            name="openai", value="openinference.instrumentation.openai:Inst", load=lambda: lambda: instrumentor
        ),
        SimpleNamespace(name="other", value="opentelemetry.instrumentation.requests:Inst", load=MagicMock()),
    ]
    with patch("pyrit.score.observation.local_trace_capture.entry_points", return_value=entries):
        enabled = capture.instrument()

    assert enabled == ["openai"]
    instrumentor.instrument.assert_called_once_with(tracer_provider=capture.provider)
    entries[1].load.assert_not_called()
    capture.close()
    instrumentor.uninstrument.assert_called_once()


def test_does_not_set_the_global_provider_by_default() -> None:
    with patch("pyrit.score.observation.local_trace_capture.trace.set_tracer_provider") as set_provider:
        LocalTraceCapture().close()
        global_capture = LocalTraceCapture(set_global=True)
    set_provider.assert_called_once_with(global_capture.provider)
    global_capture.close()


def _query(trace_id: str) -> TraceQuery:
    return TraceQuery(scope=TraceScorable(trace_ids=(trace_id,)))


class _ChattyAgentTarget(_AgentTarget):
    """Records one span with more attributes and events than the SDK defaults allow."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        attributes = {f"llm.input_messages.{i}.message.content": f"m{i}" for i in range(200)}
        with self._tracer.start_as_current_span(
            "llm", attributes={"openinference.span.kind": "LLM", **attributes}
        ) as span:
            for i in range(200):
                span.add_event(f"event-{i}", attributes={f"k{j}": j for j in range(200)})
        return await super()._send_prompt_to_target_async(normalized_conversation=normalized_conversation)


async def test_large_spans_keep_this_and_later_traces_complete_async(capture: LocalTraceCapture) -> None:
    large = await _send_async(_ChattyAgentTarget(capture=capture))
    later = await _send_async(_AgentTarget(capture=capture))

    for trace_id in (large, later):
        observation = await capture.source().acquire_async(scorable=TraceScorable(trace_ids=(trace_id,)))
        assert observation.acquisition is Acquisition.COMPLETE
    spans = (await capture.client.get_spans_async(query=_query(large))).spans
    llm = next(span for span in spans if span.name == "llm")
    assert len(llm.attributes) == 201
    assert len(llm.events) == 200


async def test_traces_outside_target_requests_are_ignored_async(capture: LocalTraceCapture) -> None:
    with capture.tracer.start_as_current_span("judge-llm-call", context=Context()) as judge:
        with capture.tracer.start_as_current_span("child"):
            pass
    judge_trace = f"{judge.get_span_context().trace_id:032x}"

    result = await capture.client.get_spans_async(query=_query(judge_trace))

    assert result.spans == ()
    assert await _send_async(_AgentTarget(capture=capture))


async def test_remote_entered_traces_are_kept_but_not_declared_complete_async(capture: LocalTraceCapture) -> None:
    remote = trace.NonRecordingSpan(
        trace.SpanContext(
            trace_id=int("ab" * 16, 16),
            span_id=int("cd" * 8, 16),
            is_remote=True,
            trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED),
        )
    )
    with capture.tracer.start_as_current_span("agent", context=trace.set_span_in_context(remote, Context())):
        pass

    result = await capture.client.get_spans_async(query=_query("ab" * 16))

    assert [span.name for span in result.spans] == ["agent"]
    assert "capture_not_complete" in result.coverage.reasons


async def test_capture_failure_is_scoped_to_its_trace_async(capture: LocalTraceCapture) -> None:
    failed = await _send_async(_AgentTarget(capture=capture))
    capture.client.record_capture_failure(trace_id=failed)
    healthy = await _send_async(_AgentTarget(capture=capture))

    failed_result = await capture.client.get_spans_async(query=_query(failed))
    healthy_result = await capture.client.get_spans_async(query=_query(healthy))

    assert "capture_failed" in failed_result.coverage.reasons
    assert healthy_result.coverage.complete


async def test_full_capture_evicts_the_oldest_complete_trace_async() -> None:
    capture = LocalTraceCapture(max_spans=4)
    try:
        target = _AgentTarget(capture=capture)
        first, second, third = [await _send_async(target) for _ in range(3)]
        evicted = await capture.source().acquire_async(scorable=TraceScorable(trace_ids=(first,)))
        kept = await capture.source().acquire_async(scorable=TraceScorable(trace_ids=(second, third)))
    finally:
        capture.close()

    assert evicted.acquisition is Acquisition.UNAVAILABLE
    assert evicted.payload.coverage.reasons == ("trace_evicted", "capture_not_complete")
    assert kept.acquisition is Acquisition.COMPLETE


class _DeferringAgentTarget(_AgentTarget):
    """Keeps its request context so work can start in the request trace after the send returns."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.request_context = otel_context.get_current()
        return await super()._send_prompt_to_target_async(normalized_conversation=normalized_conversation)


async def test_span_starting_after_completion_withdraws_it_while_open_async(capture: LocalTraceCapture) -> None:
    target = _DeferringAgentTarget(capture=capture)
    trace_id = await _send_async(target)
    scope = TraceScorable(trace_ids=(trace_id,))
    assert (await capture.source().acquire_async(scorable=scope)).acquisition is Acquisition.COMPLETE

    late = capture.tracer.start_span("late", context=target.request_context)
    while_open = await capture.source().acquire_async(scorable=scope)
    late.end()
    after = await capture.source().acquire_async(scorable=scope)

    assert while_open.acquisition is Acquisition.PARTIAL
    assert after.acquisition is Acquisition.PARTIAL
    assert [step.name for step in after.payload.steps][-1] == "late"


async def test_root_ending_during_a_child_export_waits_for_it_async(capture: LocalTraceCapture) -> None:
    exporting, release = threading.Event(), threading.Event()
    export = InMemoryTraceExporter.export

    def slow_export(self: InMemoryTraceExporter, spans: Sequence[ReadableSpan]) -> object:
        if spans[0].name == "child":
            exporting.set()
            release.wait(5)
        return export(self, spans)

    root = capture.tracer.start_span("pyrit.target", context=Context())
    child = capture.tracer.start_span("child", context=trace.set_span_in_context(root))
    with patch.object(InMemoryTraceExporter, "export", slow_export):
        child_thread = threading.Thread(target=child.end)
        child_thread.start()
        assert exporting.wait(5)
        root_thread = threading.Thread(target=root.end)
        root_thread.start()
        root_thread.join(0.1)
        release.set()
        child_thread.join(5)
        root_thread.join(5)

    trace_id = f"{root.get_span_context().trace_id:032x}"
    observation = await capture.source().acquire_async(scorable=TraceScorable(trace_ids=(trace_id,)))
    assert observation.acquisition is Acquisition.COMPLETE
    assert [step.name for step in observation.payload.steps] == ["pyrit.target", "child"]


class _TwoToolAgentTarget(_AgentTarget):
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        with self._tracer.start_as_current_span("search", attributes={"openinference.span.kind": "TOOL"}):
            pass
        return await super()._send_prompt_to_target_async(normalized_conversation=normalized_conversation)


async def test_a_failed_trace_cannot_pin_a_full_capture_async() -> None:
    capture = LocalTraceCapture(max_spans=2)
    try:
        failed = await _send_async(_TwoToolAgentTarget(capture=capture))
        failed_before = await capture.client.get_spans_async(query=_query(failed))
        later = await _send_async(_AgentTarget(capture=capture))
        failed_after = await capture.client.get_spans_async(query=_query(failed))
        later_result = await capture.client.get_spans_async(query=_query(later))
    finally:
        capture.close()

    assert "capture_capacity_exceeded" in failed_before.coverage.reasons
    assert not failed_after.available
    assert "trace_evicted" in failed_after.coverage.reasons
    assert later_result.coverage.complete


async def test_remote_traces_and_bookkeeping_stay_bounded_async() -> None:
    capture = LocalTraceCapture(max_spans=4)
    try:
        for index in range(12):
            remote = trace.NonRecordingSpan(
                trace.SpanContext(
                    trace_id=index + 1,
                    span_id=index + 1,
                    is_remote=True,
                    trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED),
                )
            )
            with capture.tracer.start_as_current_span("agent", context=trace.set_span_in_context(remote, Context())):
                pass
        later = await _send_async(_AgentTarget(capture=capture))
        result = await capture.client.get_spans_async(query=_query(later))
        oldest = await capture.client.get_spans_async(query=_query(f"{1:032x}"))
    finally:
        capture.close()

    assert result.coverage.complete
    assert oldest.spans == ()
    assert not oldest.coverage.complete
    client = capture.client
    assert len(client._trace_keys) <= 4
    assert len(client._evicted) <= 4
    assert set(client._late) | set(client._trace_failures) <= set(client._trace_keys)
