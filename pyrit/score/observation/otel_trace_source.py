# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Acquire and sanitize execution evidence independently of the criterion."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import TYPE_CHECKING

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    Observation,
    ToolEventsObservationPayload,
    ToolExecution,
    TraceCoverage,
    TraceQuery,
)
from pyrit.models.score._argument_paths import keep_argument_paths, validate_kept_argument_paths
from pyrit.score.observation.trace_client import TraceAcquisitionError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from pyrit.models import TraceQueryResult, TraceScorable, TraceSpan
    from pyrit.score.observation.trace_client import TraceClient

logger = logging.getLogger(__name__)

#: The GenAI semantic-convention attribute that carries a tool call's arguments.
_ARGUMENTS_ATTRIBUTE = "gen_ai.tool.call.arguments"
_KEPT_ARGUMENTS_ERROR = "kept_arguments must be a sequence of distinct, nonempty argument paths, none inside another."


def _has_non_finite_number(value: JsonValue) -> bool:
    """
    Find a NaN or infinite number anywhere in a JSON value.

    Returns:
        bool: Whether the value holds a number that is not finite.
    """
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_has_non_finite_number(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_non_finite_number(item) for item in value)
    return False


def _keep_arguments(value: JsonValue | None, *, kept: tuple[str, ...]) -> dict[str, JsonValue] | None:
    """
    Keep only the named arguments of one call, nested as they were, redacting the rest.

    A number that is not finite is not JSON, whether it arrives as a ``NaN`` or ``Infinity``
    constant, as a literal too large to represent, or inside an already-parsed object.

    Returns:
        dict[str, JsonValue] | None: The kept arguments the call has, or None if its arguments
            are missing, are not a JSON object, or hold a number that is not finite.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict) or _has_non_finite_number(value):
        return None
    return keep_argument_paths(value, kept=kept)


class OtelTraceSource:
    """
    Normalize explicit GenAI/OpenInference execution spans into safe evidence.

    Tool arguments are redacted at write time. By default none are kept. ``kept_arguments`` opts in
    to keeping the named arguments from the span's ``gen_ai.tool.call.arguments`` attribute: a
    top-level key, or a dotted path such as ``recipient.email`` that keeps only that nested field.
    Every other argument, and the raw attribute, is discarded before the evidence is stored. Spans
    that do not carry that attribute keep no arguments.
    """

    def __init__(
        self,
        *,
        trace_client: TraceClient,
        max_spans: int = 10000,
        timeout_seconds: float = 30,
        kept_arguments: Sequence[str] = (),
    ) -> None:
        """
        Initialize bounded acquisition through a caller-owned client.

        Raises:
            ValueError: If the retrieval limit or timeout is not positive and finite, or the kept
                argument paths are not distinct, nonempty, and apart from one another.
        """
        if max_spans < 1 or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("max_spans and timeout_seconds must be positive.")
        if isinstance(kept_arguments, str):
            raise ValueError(_KEPT_ARGUMENTS_ERROR)
        try:
            validate_kept_argument_paths(kept_arguments)
        except ValueError:
            raise ValueError(_KEPT_ARGUMENTS_ERROR) from None
        self._client = trace_client
        self._max_spans = max_spans
        self._timeout_seconds = timeout_seconds
        self._kept_arguments = tuple(kept_arguments)

    def get_identifier(self) -> ComponentIdentifier:
        """Return parser version and source identity without serializing the client."""
        return ComponentIdentifier.of(
            self,
            params={
                "normalization_version": 1,
                "max_spans": self._max_spans,
                "timeout_seconds": self._timeout_seconds,
                # Omitted while empty, so a source that keeps no arguments keeps its identity.
                "kept_arguments": list(self._kept_arguments) or None,
            },
            children={"trace_client": self._client.get_identifier()},
        )

    async def acquire_async(self, *, scorable: TraceScorable) -> Observation:
        """
        Acquire a bounded snapshot; only defined operational failures become error evidence.

        Returns:
            Observation: The sanitized snapshot, including acquisition and coverage state.
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
        events, normalization_reasons = self._normalize(spans=spans, kept=self._kept_arguments)
        reasons.extend(normalization_reasons)
        acquisition = Acquisition.PARTIAL if reasons else Acquisition.COMPLETE
        return self._observation(
            scorable=scorable,
            acquisition=acquisition,
            reasons=tuple(dict.fromkeys(reasons)),
            events=events,
        )

    def _observation(
        self,
        *,
        scorable: TraceScorable,
        acquisition: Acquisition,
        reasons: tuple[str, ...],
        events: tuple[ToolExecution, ...] = (),
    ) -> Observation:
        return Observation(
            source_identifier=self.get_identifier(),
            acquisition=acquisition,
            scorable=scorable,
            payload=ToolEventsObservationPayload(
                scope=scorable,
                events=events,
                coverage=TraceCoverage(complete=acquisition is Acquisition.COMPLETE, reasons=reasons),
                kept_arguments=self._kept_arguments,
            ),
        )

    @staticmethod
    def _normalize(*, spans: Sequence[TraceSpan], kept: tuple[str, ...]) -> tuple[tuple[ToolExecution, ...], list[str]]:
        events: list[ToolExecution] = []
        reasons: list[str] = []
        for span in spans:
            open_inference = span.attributes.get("openinference.span.kind") == "TOOL"
            gen_ai = span.attributes.get("gen_ai.operation.name") == "execute_tool"
            if not (open_inference or gen_ai):
                continue
            name = span.attributes.get("tool.name" if open_inference else "gen_ai.tool.name")
            if not isinstance(name, str) or not name.strip():
                reasons.append("tool_name_missing")
                continue
            if open_inference and gen_ai and span.attributes.get("gen_ai.tool.name", name) != name:
                raise ValueError("Span conventions disagree about the executed tool name.")
            call_id = span.attributes.get("gen_ai.tool.call.id")
            events.append(
                ToolExecution(
                    name=name,
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    parent_span_id=span.parent_span_id,
                    call_id=call_id if isinstance(call_id, str) and call_id else None,
                    start_time=span.start_time,
                    end_time=span.end_time,
                    status=span.status,
                    arguments=_keep_arguments(span.attributes.get(_ARGUMENTS_ATTRIBUTE), kept=kept) if kept else None,
                )
            )
        events.sort(key=lambda event: (event.start_time, event.trace_id, event.span_id))
        return tuple(events), reasons


async def _query_trace_spans_async(
    *, client: TraceClient, scorable: TraceScorable, max_spans: int, timeout_seconds: float
) -> TraceQueryResult | str:
    """
    Query a client within the timeout, mapping defined operational failures to a reason.

    Returns:
        TraceQueryResult | str: The client's result, or the failure reason.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            return await client.get_spans_async(query=TraceQuery(scope=scorable, limit=max_spans))
    except (TraceAcquisitionError, TimeoutError) as error:
        reason = "trace_query_timeout" if isinstance(error, TimeoutError) else "trace_query_failed"
        logger.warning("Trace acquisition failed (%s).", reason)
        return reason


def _scoped_spans(
    *, result: TraceQueryResult, scope: TraceScorable, max_spans: int
) -> tuple[list[TraceSpan], list[str]]:
    """
    Select unique in-scope spans and report coverage gaps.

    Returns:
        tuple[list[TraceSpan], list[str]]: The unique spans and the reasons coverage is incomplete.

    Raises:
        ValueError: If the client returned out-of-scope or conflicting evidence.
    """
    reasons = list(result.coverage.reasons)
    if not result.coverage.complete:
        reasons.append("source_coverage_incomplete")
    if len(result.spans) > max_spans:
        reasons.append("query_limit_exceeded")
    seen: dict[tuple[str, str], TraceSpan] = {}
    for span in result.spans[:max_spans]:
        if span.trace_id not in scope.trace_ids:
            raise ValueError("Trace client returned evidence outside the requested scope.")
        key = (span.trace_id, span.span_id)
        if key in seen:
            if seen[key] != span:
                raise ValueError("Trace client returned conflicting span identities.")
            continue
        seen[key] = span
        if not span.sampled or span.end_time is None:
            reasons.append("span_not_complete")
    return list(seen.values()), reasons
