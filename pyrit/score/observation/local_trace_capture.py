# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""In-process trace capture for scoring execution evidence, without global SDK state by default."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from threading import Lock
from typing import TYPE_CHECKING, Any, ClassVar

from opentelemetry import trace
from opentelemetry.sdk.trace import SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from pyrit.prompt_target import TargetTraceConfig
from pyrit.score.observation.otel_execution_trace_source import OtelExecutionTraceSource
from pyrit.score.observation.otel_span_exporter import InMemoryTraceExporter
from pyrit.score.observation.trace_client import InMemoryTraceClient

if TYPE_CHECKING:
    from collections.abc import Callable

    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import ReadableSpan, Span
    from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)


class _TargetTraceProcessor(SpanProcessor):
    """
    Export target traces into the capture and declare them complete once they finish.

    A trace is captured when its root is the target's ``pyrit.target`` span, or when it enters
    through a propagated remote context. Other local root traces, such as PyRIT's own judge or
    adversarial model calls under instrumentation, are ignored. Only ``pyrit.target`` traces are
    declared complete, once their root and every span started in them have ended. A span that
    starts in a trace already declared complete withdraws that completion. Bookkeeping, export,
    and completion share one lock, so completion never precedes a finished span's export, and
    state is dropped once a trace has no open spans.
    """

    def __init__(
        self,
        *,
        client: InMemoryTraceClient,
        exporter: InMemoryTraceExporter,
        root_span_name: str,
        auto_complete: bool,
    ) -> None:
        self._client = client
        self._exporter = exporter
        self._root_span_name = root_span_name
        self._auto_complete = auto_complete
        self._completable: set[int] = set()
        self._open: dict[int, set[int]] = {}
        self._roots_ended: set[int] = set()
        self._lock = Lock()

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        context = span.get_span_context()
        parent = span.parent
        with self._lock:
            if parent is None or not parent.is_valid:
                if span.name != self._root_span_name:
                    return
                self._completable.add(context.trace_id)
            elif (
                not parent.is_remote
                and context.trace_id not in self._open
                and not self._client.reopen(trace_id=f"{context.trace_id:032x}")
            ):
                return
            self._open.setdefault(context.trace_id, set()).add(context.span_id)

    def on_end(self, span: ReadableSpan) -> None:
        context = span.get_span_context()
        if context is None:
            return
        trace_id = context.trace_id
        with self._lock:
            open_spans = self._open.get(trace_id)
            if open_spans is None or context.span_id not in open_spans:
                return
            open_spans.discard(context.span_id)
            if (span.parent is None or not span.parent.is_valid) and span.name == self._root_span_name:
                self._roots_ended.add(trace_id)
            completable = trace_id in self._completable
            finished = not open_spans and (not completable or trace_id in self._roots_ended)
            if finished:
                self._open.pop(trace_id, None)
                self._roots_ended.discard(trace_id)
                self._completable.discard(trace_id)
            self._exporter.export((span,))
            if finished and completable and self._auto_complete:
                try:
                    self._client.mark_complete(trace_ids=(f"{trace_id:032x}",))
                except ValueError:
                    logger.debug("Trace %032x is not complete; leaving its coverage incomplete.", trace_id)

    def shutdown(self) -> None:
        self._exporter.shutdown()
        with self._lock:
            self._open.clear()
            self._roots_ended.clear()
            self._completable.clear()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class LocalTraceCapture:
    """
    Capture an in-process application's spans so scorers can read its execution.

    Owns a ``TracerProvider`` with always-on sampling and no attribute, event, or length
    limits, and an ``InMemoryTraceClient``. Give the target ``trace_config()`` so each send runs
    in a fresh ``pyrit.target`` trace, and give scorers ``source()``. Only target traces, and
    traces entered through a propagated context, are kept; PyRIT's own judge and adversarial
    calls are ignored even when instrumented. With ``auto_complete``, a target trace is declared
    complete when its root span and every span started in that trace have ended, which suits
    in-process agents. When ``max_spans`` is reached, the oldest complete trace is evicted, or
    the oldest other trace if none is complete, so score traces soon after they are recorded.
    Spans recorded through another provider never reach this capture; call ``instrument()`` to
    route installed OpenInference instrumentors here, or set ``set_global`` for libraries that
    only use the global provider.
    """

    ROOT_SPAN_NAME: ClassVar[str] = "pyrit.target"
    INSTRUMENTOR_GROUP: ClassVar[str] = "opentelemetry_instrumentor"
    OPEN_INFERENCE_PREFIX: ClassVar[str] = "openinference.instrumentation."

    def __init__(
        self,
        *,
        source_id: str = "local",
        max_spans: int = 10000,
        auto_complete: bool = True,
        set_global: bool = False,
    ) -> None:
        """
        Create the provider, local store, and exporter.

        Args:
            source_id (str): Nonsecret identity of this capture. Defaults to "local".
            max_spans (int): Maximum spans, and traces, retained before the oldest trace is evicted.
                Defaults to 10000.
            auto_complete (bool): Declare target traces complete when all their spans end.
                Defaults to True.
            set_global (bool): Also install the provider as the global tracer provider.
                Defaults to False.
        """
        self._client = InMemoryTraceClient(source_id=source_id, max_spans=max_spans)
        unlimited = SpanLimits.UNSET
        self._provider = TracerProvider(
            sampler=ALWAYS_ON,
            span_limits=SpanLimits(
                max_attributes=unlimited,
                max_events=unlimited,
                max_links=unlimited,
                max_span_attributes=unlimited,
                max_event_attributes=unlimited,
                max_link_attributes=unlimited,
                max_attribute_length=unlimited,
                max_span_attribute_length=unlimited,
            ),
        )
        self._provider.add_span_processor(
            _TargetTraceProcessor(
                client=self._client,
                exporter=InMemoryTraceExporter(trace_client=self._client),
                root_span_name=self.ROOT_SPAN_NAME,
                auto_complete=auto_complete,
            )
        )
        self._instrumentors: list[Any] = []
        if set_global:
            trace.set_tracer_provider(self._provider)

    @property
    def client(self) -> InMemoryTraceClient:
        """The local span store."""
        return self._client

    @property
    def provider(self) -> TracerProvider:
        """The capture's tracer provider, for instrumenting application code."""
        return self._provider

    @property
    def tracer(self) -> Tracer:
        """A tracer that records into this capture."""
        return self._provider.get_tracer("pyrit")

    def trace_config(self) -> TargetTraceConfig:
        """
        Build a target configuration that records each send in a fresh capture trace.

        Returns:
            TargetTraceConfig: Enabled tracing with this capture's tracer.
        """
        return TargetTraceConfig(enabled=True, tracer=self.tracer)

    def source(
        self,
        *,
        retain_content: bool = True,
        max_content_chars: int = 4000,
        redactor: Callable[[str], str] | None = None,
        timeout_seconds: float = 30,
    ) -> OtelExecutionTraceSource:
        """
        Build an execution source that reads this capture.

        Args:
            retain_content (bool): Whether to keep inputs and outputs. Defaults to True.
            max_content_chars (int): Characters kept per input or output. Defaults to 4000.
            redactor (Callable[[str], str] | None): Rewrites each full value first. Defaults to None.
            timeout_seconds (float): Acquisition timeout. Defaults to 30.

        Returns:
            OtelExecutionTraceSource: The source.
        """
        return OtelExecutionTraceSource(
            trace_client=self._client,
            retain_content=retain_content,
            max_content_chars=max_content_chars,
            redactor=redactor,
            timeout_seconds=timeout_seconds,
        )

    def register(self, *, name: str, replace: bool = False) -> OtelExecutionTraceSource:
        """
        Register a default-configured source for this capture so scenarios can name it.

        Args:
            name (str): The ``SourceRegistry`` instance name.
            replace (bool): Whether to replace an existing registration. Defaults to False.

        Returns:
            OtelExecutionTraceSource: The registered source.
        """
        from pyrit.registry import SourceRegistry

        source = self.source()
        SourceRegistry.get_registry_singleton().instances.register(source, name=name, replace=replace)
        return source

    def instrument(self) -> list[str]:
        """
        Enable installed OpenInference instrumentors on this capture's provider.

        Returns:
            list[str]: The entry-point names that were instrumented.
        """
        enabled = []
        for entry_point in entry_points(group=self.INSTRUMENTOR_GROUP):
            if not entry_point.value.startswith(self.OPEN_INFERENCE_PREFIX):
                continue
            try:
                instrumentor = entry_point.load()()
                instrumentor.instrument(tracer_provider=self._provider)
            except Exception as error:
                logger.warning("Could not enable instrumentor %s: %s", entry_point.name, error)
                continue
            self._instrumentors.append(instrumentor)
            enabled.append(entry_point.name)
        return enabled

    def close(self) -> None:
        """Remove instrumentation, flush and stop the provider, and close the store."""
        for instrumentor in self._instrumentors:
            try:
                instrumentor.uninstrument()
            except Exception as error:
                logger.warning("Could not remove instrumentor %s: %s", type(instrumentor).__name__, error)
        self._instrumentors.clear()
        self._provider.shutdown()
        self._client.close()
