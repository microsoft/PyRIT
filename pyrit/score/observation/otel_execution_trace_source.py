# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Normalize model, tool, and agent spans into execution evidence."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ExecutionStep,
    ExecutionStepKind,
    ExecutionTraceObservationPayload,
    Observation,
    TraceContent,
    TraceCoverage,
    TraceSpanStatus,
)
from pyrit.score.observation.otel_trace_source import _query_trace_spans_async, _scoped_spans

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

    from pyrit.models import TraceScorable, TraceSpan
    from pyrit.score.observation.trace_client import TraceClient


class OtelExecutionTraceSource:
    """
    Normalize OpenInference and GenAI semantic-convention spans into execution steps.

    OpenInference attributes take precedence; GenAI attributes and events are the fallback.
    Model calls, tool calls with arguments and results, and agent, chain, and retrieval steps
    are retained. Content and labels are saved complete, with each content value's length and
    SHA-256 digest; what the judge is shown is bounded when rendering. A ``redactor`` sees
    every value before it is measured, hashed, or saved. With ``retain_content=False``, steps
    keep names, lengths, and digests only.
    """

    NORMALIZATION_VERSION: ClassVar[int] = 4
    _OPEN_INFERENCE_KINDS: ClassVar[dict[str, ExecutionStepKind]] = {
        "LLM": ExecutionStepKind.LLM,
        "TOOL": ExecutionStepKind.TOOL,
        "AGENT": ExecutionStepKind.AGENT,
        "CHAIN": ExecutionStepKind.CHAIN,
        "RETRIEVER": ExecutionStepKind.RETRIEVER,
        "EMBEDDING": ExecutionStepKind.EMBEDDING,
        "RERANKER": ExecutionStepKind.RERANKER,
        "GUARDRAIL": ExecutionStepKind.GUARDRAIL,
        "EVALUATOR": ExecutionStepKind.EVALUATOR,
    }
    _GEN_AI_KINDS: ClassVar[dict[str, ExecutionStepKind]] = {
        "chat": ExecutionStepKind.LLM,
        "text_completion": ExecutionStepKind.LLM,
        "generate_content": ExecutionStepKind.LLM,
        "execute_tool": ExecutionStepKind.TOOL,
        "invoke_agent": ExecutionStepKind.AGENT,
        "create_agent": ExecutionStepKind.AGENT,
        "embeddings": ExecutionStepKind.EMBEDDING,
        "retrieval": ExecutionStepKind.RETRIEVER,
    }
    _GEN_AI_INPUT_EVENTS: ClassVar[tuple[str, ...]] = (
        "gen_ai.system.message",
        "gen_ai.user.message",
        "gen_ai.assistant.message",
        "gen_ai.tool.message",
    )
    _MESSAGE_ATTRIBUTE = re.compile(r"^llm\.(input|output)_messages\.(\d+)\.message\.(.+)$")

    def __init__(
        self,
        *,
        trace_client: TraceClient,
        max_spans: int = 10000,
        timeout_seconds: float = 30,
        retain_content: bool = True,
        redactor: Callable[[str], str] | None = None,
    ) -> None:
        """
        Initialize bounded acquisition through a caller-owned client.

        Args:
            trace_client (TraceClient): The client that retrieves spans by trace ID.
            max_spans (int): Maximum spans read per acquisition. Defaults to 10000.
            timeout_seconds (float): Acquisition timeout. Defaults to 30.
            retain_content (bool): Whether to keep inputs, outputs, and errors. Defaults to True.
            redactor (Callable[[str], str] | None): Rewrites each value before it is measured,
                hashed, or saved. Defaults to None.

        Raises:
            ValueError: If the span limit or the timeout is not positive and finite.
        """
        if max_spans < 1 or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("max_spans and timeout_seconds must be positive.")
        self._client = trace_client
        self._max_spans = max_spans
        self._timeout_seconds = timeout_seconds
        self._retain_content = retain_content
        self._redactor = redactor

    def get_identifier(self) -> ComponentIdentifier:
        """
        Return the normalization and content policy without serializing the client.

        Returns:
            ComponentIdentifier: The source identity.
        """
        redactor = getattr(self._redactor, "__qualname__", type(self._redactor).__name__) if self._redactor else None
        return ComponentIdentifier.of(
            self,
            params={
                "normalization_version": self.NORMALIZATION_VERSION,
                "max_spans": self._max_spans,
                "timeout_seconds": self._timeout_seconds,
                "retain_content": self._retain_content,
                "redactor": redactor,
            },
            children={"trace_client": self._client.get_identifier()},
        )

    async def acquire_async(self, *, scorable: TraceScorable) -> Observation:
        """
        Acquire and normalize a bounded snapshot of the requested traces.

        Returns:
            Observation: The execution snapshot, including acquisition and coverage state.
        """
        result = await _query_trace_spans_async(
            client=self._client, scorable=scorable, max_spans=self._max_spans, timeout_seconds=self._timeout_seconds
        )
        if isinstance(result, str):
            return self._observation(scorable=scorable, acquisition=Acquisition.ERROR, reasons=(result,))
        if not result.available:
            return self._observation(
                scorable=scorable,
                acquisition=Acquisition.UNAVAILABLE,
                reasons=result.coverage.reasons or ("trace_unavailable",),
            )
        spans, reasons = _scoped_spans(result=result, scope=scorable, max_spans=self._max_spans)
        steps, normalization_reasons = self._normalize(spans=spans)
        reasons.extend(normalization_reasons)
        return self._observation(
            scorable=scorable,
            acquisition=Acquisition.PARTIAL if reasons else Acquisition.COMPLETE,
            reasons=tuple(dict.fromkeys(reasons)),
            steps=steps,
        )

    def _observation(
        self,
        *,
        scorable: TraceScorable,
        acquisition: Acquisition,
        reasons: tuple[str, ...],
        steps: tuple[ExecutionStep, ...] = (),
    ) -> Observation:
        return Observation(
            source_identifier=self.get_identifier(),
            acquisition=acquisition,
            scorable=scorable,
            payload=ExecutionTraceObservationPayload(
                scope=scorable,
                steps=steps,
                coverage=TraceCoverage(complete=acquisition is Acquisition.COMPLETE, reasons=reasons),
                content_retained=self._retain_content,
            ),
        )

    def _normalize(self, *, spans: Sequence[TraceSpan]) -> tuple[tuple[ExecutionStep, ...], list[str]]:
        reasons: list[str] = []
        steps = []
        for span in _execution_order(spans):
            step, step_reasons = self._normalize_span(span=span, index=len(steps))
            reasons.extend(step_reasons)
            steps.append(step)
        return tuple(steps), reasons

    def _normalize_span(self, *, span: TraceSpan, index: int) -> tuple[ExecutionStep, list[str]]:
        attributes = span.attributes
        kind = self._kind(attributes)
        reasons: list[str] = []
        tool_name = self._label(self._tool_name(attributes=attributes, kind=kind, reasons=reasons))
        input_value, output_value = self._content_values(span=span, kind=kind)
        return (
            ExecutionStep(
                index=index,
                trace_id=span.trace_id,
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                start_time=span.start_time,
                end_time=span.end_time,
                kind=kind,
                name=tool_name or self._label(span.name) or kind.value,
                status=span.status,
                error_content=self._content(self._error_message(span)),
                model=self._label(
                    _first_str(attributes, "llm.model_name", "gen_ai.response.model", "gen_ai.request.model")
                ),
                tool_name=tool_name,
                call_id=self._label(_first_str(attributes, "tool_call.id", "gen_ai.tool.call.id")),
                node_name=self._label(
                    _first_str(attributes, "langgraph.node.name", "graph.node.id", "gen_ai.agent.name")
                ),
                input_content=self._content(input_value),
                output_content=self._content(output_value),
                input_tokens=_first_int(attributes, "llm.token_count.prompt", "gen_ai.usage.input_tokens"),
                output_tokens=_first_int(attributes, "llm.token_count.completion", "gen_ai.usage.output_tokens"),
            ),
            reasons,
        )

    def _label(self, value: str | None) -> str | None:
        """
        Redact a name-like value, keeping the result exactly.

        Returns:
            str | None: The label, or None when only whitespace remains.
        """
        if value is None:
            return None
        label = self._redactor(value) if self._redactor else value
        return label if label.strip() else None

    def _kind(self, attributes: Mapping[str, JsonValue]) -> ExecutionStepKind:
        open_inference = attributes.get("openinference.span.kind")
        if isinstance(open_inference, str):
            return self._OPEN_INFERENCE_KINDS.get(open_inference.upper(), ExecutionStepKind.OTHER)
        gen_ai = attributes.get("gen_ai.operation.name")
        if isinstance(gen_ai, str):
            return self._GEN_AI_KINDS.get(gen_ai, ExecutionStepKind.OTHER)
        return ExecutionStepKind.OTHER

    @staticmethod
    def _tool_name(*, attributes: Mapping[str, JsonValue], kind: ExecutionStepKind, reasons: list[str]) -> str | None:
        if kind is not ExecutionStepKind.TOOL:
            return None
        names = [name for name in (attributes.get("tool.name"), attributes.get("gen_ai.tool.name")) if name]
        valid = [name for name in names if isinstance(name, str) and name.strip()]
        if not valid:
            reasons.append("tool_name_missing")
            return None
        if len(set(valid)) > 1:
            reasons.append("span_conventions_disagree")
        return valid[0]

    def _content_values(self, *, span: TraceSpan, kind: ExecutionStepKind) -> tuple[str | None, str | None]:
        attributes = span.attributes
        messages = self._open_inference_messages(attributes)
        input_value = messages.get("input") or _text(attributes.get("input.value"))
        output_value = messages.get("output") or _text(attributes.get("output.value"))
        if kind is ExecutionStepKind.TOOL:
            input_value = input_value or _text(attributes.get("gen_ai.tool.call.arguments"))
            output_value = output_value or _text(attributes.get("gen_ai.tool.call.result"))
        input_value = input_value or _join(
            _text(attributes.get("gen_ai.system_instructions")), _text(attributes.get("gen_ai.input.messages"))
        )
        output_value = output_value or _text(attributes.get("gen_ai.output.messages"))
        input_value = input_value or self._event_text(span=span, names=self._GEN_AI_INPUT_EVENTS)
        output_value = output_value or self._event_text(span=span, names=("gen_ai.choice",))
        return input_value, output_value

    def _open_inference_messages(self, attributes: Mapping[str, JsonValue]) -> dict[str, str]:
        """
        Rebuild flattened OpenInference messages as ``role: content`` lines.

        Returns:
            dict[str, str]: Rendered ``input`` and ``output`` messages, when present.
        """
        fields: dict[tuple[str, int], dict[str, JsonValue]] = {}
        for key, value in attributes.items():
            match = self._MESSAGE_ATTRIBUTE.match(key)
            if match:
                fields.setdefault((match.group(1), int(match.group(2))), {})[match.group(3)] = value
        rendered: dict[str, list[str]] = {}
        for (direction, _), message in sorted(fields.items()):
            role = message.get("role", "message")
            texts = [_text(message.get("content"))] + [
                _text(message[key])
                for key in sorted((k for k in message if k.endswith(".message_content.text")), key=_natural_key)
            ]
            body = [text for text in texts if text]
            tool_calls = sorted((key for key in message if key.endswith(".tool_call.function.name")), key=_natural_key)
            for name_key in tool_calls:
                prefix = name_key.removesuffix(".function.name")
                arguments = _text(message.get(f"{prefix}.function.arguments")) or ""
                body.append(f"tool_call {message[name_key]}({arguments})")
            if body:
                rendered.setdefault(direction, []).append(f"{role}: " + "\n".join(body))
        return {direction: "\n".join(lines) for direction, lines in rendered.items()}

    @staticmethod
    def _event_text(*, span: TraceSpan, names: tuple[str, ...]) -> str | None:
        lines = [
            f"{event.name}: {json.dumps(event.attributes, ensure_ascii=False, sort_keys=True)}"
            for event in span.events
            if event.name in names
        ]
        return "\n".join(lines) or None

    @staticmethod
    def _error_message(span: TraceSpan) -> str | None:
        if span.status is not TraceSpanStatus.ERROR:
            return None
        exception = next((event for event in span.events if event.name == "exception"), None)
        message = exception.attributes.get("exception.message") if exception else None
        return span.status_message or (message if isinstance(message, str) else None) or "error"

    def _content(self, value: str | None) -> TraceContent | None:
        if value is None:
            return None
        full = self._redactor(value) if self._redactor else value
        return TraceContent.of(full, retain=self._retain_content, redacted=self._redactor is not None)


def _text(value: JsonValue) -> str | None:
    """
    Render an attribute as text; structured values become compact JSON.

    Returns:
        str | None: The text, or None when the value is absent or empty.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _join(*parts: str | None) -> str | None:
    present = [part for part in parts if part]
    return "\n".join(present) or None


def _natural_key(key: str) -> tuple[tuple[int, int | str], ...]:
    """
    Order flattened attribute keys so numbered segments sort numerically.

    Returns:
        tuple[tuple[int, int | str], ...]: The sort key.
    """
    parts: list[str] = re.split(r"(\d+)", key)
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)


def _first_str(attributes: Mapping[str, JsonValue], *keys: str) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _first_int(attributes: Mapping[str, JsonValue], *keys: str) -> int | None:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _execution_order(spans: Sequence[TraceSpan]) -> list[TraceSpan]:
    """
    Order spans by start time, then parent before child, then end time, then the order received.

    Clocks can give a parent and its children the same start time (Windows' timer is coarse), so
    the tree depth keeps step numbering deterministic and each parent before its children.

    Returns:
        list[TraceSpan]: The spans in step order.
    """
    parents = {(span.trace_id, span.span_id): span.parent_span_id for span in spans}

    def depth(span: TraceSpan) -> int:
        seen = {span.span_id}
        parent, count = span.parent_span_id, 0
        while parent is not None and parent not in seen and (span.trace_id, parent) in parents:
            seen.add(parent)
            parent, count = parents[(span.trace_id, parent)], count + 1
        return count

    arrival = {id(span): position for position, span in enumerate(spans)}
    return sorted(
        spans,
        key=lambda span: (
            span.start_time,
            depth(span),
            span.end_time or datetime.max.replace(tzinfo=UTC),
            arrival[id(span)],
        ),
    )
