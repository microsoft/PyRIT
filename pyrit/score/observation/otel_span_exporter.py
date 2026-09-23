# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Local SDK bridge; attach it only to a caller-owned provider."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from opentelemetry.attributes import BoundedAttributes
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from pyrit.models import TraceSpan, TraceSpanStatus
from pyrit.score.observation.trace_client import TraceAcquisitionError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from opentelemetry.sdk.trace import ReadableSpan
    from pydantic import JsonValue

    from pyrit.score.observation.trace_client import InMemoryTraceClient

logger = logging.getLogger(__name__)


class InMemoryTraceExporter(SpanExporter):
    """
    Copy ended SDK spans into a local trace client without global state.

    The provider must disable span attribute string truncation with
    ``SpanLimits(max_span_attribute_length=SpanLimits.UNSET)``. Spans with finite
    or unknown length limits are omitted and make capture incomplete.
    """

    def __init__(self, *, trace_client: InMemoryTraceClient) -> None:
        """Initialize a bridge to a caller-owned client."""
        self._client = trace_client
        self._closed = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """
        Export a batch; report capture failures through the SDK contract.

        Returns:
            SpanExportResult: Whether every span was retained.
        """
        if self._closed:
            logger.warning("Cannot export into a closed trace exporter.")
            return SpanExportResult.FAILURE
        result = SpanExportResult.SUCCESS
        try:
            for span in spans:
                # ReadableSpan's public mapping hides the SDK's string-length limit.
                attributes = getattr(span, "_attributes", None)
                if (
                    not isinstance(attributes, BoundedAttributes)
                    or getattr(attributes, "max_value_len", -1) is not None
                ):
                    self._client.record_capture_failure()
                    logger.warning(
                        "SDK span attribute length is limited or unknown; span omitted. "
                        "Use SpanLimits(max_span_attribute_length=SpanLimits.UNSET)."
                    )
                    result = SpanExportResult.FAILURE
                    continue
                if span.dropped_attributes:
                    self._client.record_capture_failure()
                    logger.warning("SDK span attributes were dropped; evidence is not complete.")
                    result = SpanExportResult.FAILURE
                self._client.add_span(self._convert_span(span))
        except (TraceAcquisitionError, ValueError):
            self._client.record_capture_failure()
            logger.warning("Local trace export failed; evidence is not complete.")
            return SpanExportResult.FAILURE
        return result

    def shutdown(self) -> None:
        """Stop this exporter without closing the caller's shared client."""
        self._closed = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """
        Report synchronous export completion, not trace completeness.

        Returns:
            bool: Whether this synchronous exporter remains open.
        """
        return not self._closed

    @staticmethod
    def _convert_span(span: ReadableSpan) -> TraceSpan:
        context = span.get_span_context()
        if context is None or not context.is_valid or span.start_time is None:
            raise ValueError("An SDK span must have valid identity and start time.")
        attributes: dict[str, JsonValue] = {}
        for key, value in (span.attributes or {}).items():
            if value is None or isinstance(value, (str, bool, int, float)):
                attributes[key] = value
            else:
                attributes[key] = list(value)
        return TraceSpan(
            trace_id=f"{context.trace_id:032x}",
            span_id=f"{context.span_id:016x}",
            parent_span_id=f"{span.parent.span_id:016x}" if span.parent else None,
            start_time=datetime.fromtimestamp(span.start_time / 1e9, tz=UTC),
            end_time=datetime.fromtimestamp(span.end_time / 1e9, tz=UTC) if span.end_time is not None else None,
            attributes=attributes,
            status=TraceSpanStatus(span.status.status_code.name.lower()),
            sampled=context.trace_flags.sampled,
        )
