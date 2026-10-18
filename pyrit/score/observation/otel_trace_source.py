# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Acquire and sanitize execution evidence independently of the criterion."""

from __future__ import annotations

import asyncio
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
from pyrit.score.observation.trace_client import TraceAcquisitionError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import TraceScorable, TraceSpan
    from pyrit.score.observation.trace_client import TraceClient

logger = logging.getLogger(__name__)


class OtelTraceSource:
    """Normalize explicit GenAI/OpenInference execution spans into safe evidence."""

    def __init__(self, *, trace_client: TraceClient, max_spans: int = 10000, timeout_seconds: float = 30) -> None:
        """
        Initialize bounded acquisition through a caller-owned client.

        Raises:
            ValueError: If the retrieval limit or timeout is not positive and finite.
        """
        if max_spans < 1 or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("max_spans and timeout_seconds must be positive.")
        self._client = trace_client
        self._max_spans = max_spans
        self._timeout_seconds = timeout_seconds

    def get_identifier(self) -> ComponentIdentifier:
        """Return parser version and source identity without serializing the client."""
        return ComponentIdentifier.of(
            self,
            params={"normalization_version": 1, "max_spans": self._max_spans, "timeout_seconds": self._timeout_seconds},
            children={"trace_client": self._client.get_identifier()},
        )

    async def acquire_async(self, *, scorable: TraceScorable) -> Observation:
        """
        Acquire a bounded snapshot; only defined operational failures become error evidence.

        Returns:
            Observation: The sanitized snapshot, including acquisition and coverage state.
        """
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._client.get_spans_async(query=TraceQuery(scope=scorable, limit=self._max_spans))
        except (TraceAcquisitionError, TimeoutError) as error:
            reason = "trace_query_timeout" if isinstance(error, TimeoutError) else "trace_query_failed"
            logger.warning("Trace acquisition failed (%s).", reason)
            return self._observation(scorable=scorable, acquisition=Acquisition.ERROR, reasons=(reason,))

        if not result.available:
            return self._observation(
                scorable=scorable,
                acquisition=Acquisition.UNAVAILABLE,
                reasons=result.coverage.reasons or ("trace_unavailable",),
            )

        reasons = list(result.coverage.reasons)
        if not result.coverage.complete:
            reasons.append("source_coverage_incomplete")
        if len(result.spans) > self._max_spans:
            reasons.append("query_limit_exceeded")
        events, normalization_reasons = self._normalize(spans=result.spans[: self._max_spans], scope=scorable)
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
            ),
        )

    @staticmethod
    def _normalize(*, spans: Sequence[TraceSpan], scope: TraceScorable) -> tuple[tuple[ToolExecution, ...], list[str]]:
        seen: dict[tuple[str, str], TraceSpan] = {}
        events: list[ToolExecution] = []
        reasons: list[str] = []
        for span in spans:
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
                )
            )
        events.sort(key=lambda event: (event.start_time, event.trace_id, event.span_id))
        return tuple(events), reasons
