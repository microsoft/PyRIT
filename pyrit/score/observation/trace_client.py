# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend-neutral trace retrieval and a bounded local reference client."""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING, Protocol

from pyrit.models import ComponentIdentifier, TraceCoverage, TraceQueryResult, TraceScorable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import TraceQuery, TraceSpan


class TraceAcquisitionError(RuntimeError):
    """An operational failure retrieving trace evidence, not a negative verdict."""


class TraceClient(Protocol):
    """Retrieve exact trace identities without knowing the scoring condition."""

    def get_identifier(self) -> ComponentIdentifier:
        """Return a stable source identity without credentials."""
        ...

    async def get_spans_async(self, *, query: TraceQuery) -> TraceQueryResult:
        """Retrieve bounded spans and report their coverage."""
        ...


class InMemoryTraceClient:
    """Thread-safe local capture with explicit, caller-owned completion."""

    def __init__(self, *, source_id: str = "local", max_spans: int = 10000) -> None:
        """
        Initialize bounded storage; no SDK provider is installed.

        Raises:
            ValueError: If the identity is empty or capacity is not positive.
        """
        if not source_id.strip() or max_spans < 1:
            raise ValueError("source_id must be nonempty and max_spans must be positive.")
        self._source_id = source_id
        self._max_spans = max_spans
        self._spans: dict[tuple[str, str], TraceSpan] = {}
        self._complete: set[str] = set()
        self._late: set[str] = set()
        self._overflow = False
        self._capture_failed = False
        self._closed = False
        self._lock = Lock()

    def get_identifier(self) -> ComponentIdentifier:
        """Return the caller's nonsecret capture identity."""
        return ComponentIdentifier.of(self, params={"source_id": self._source_id, "max_spans": self._max_spans})

    def add_span(self, span: TraceSpan) -> None:
        """
        Capture a detached span, rejecting conflicting identities or overflow.

        Raises:
            TraceAcquisitionError: If capture is closed or its capacity is exceeded.
            ValueError: If an existing identity has different evidence.
        """
        snapshot = span.model_copy(deep=True)
        key = (snapshot.trace_id, snapshot.span_id)
        with self._lock:
            self._check_open()
            previous = self._spans.get(key)
            if previous is not None:
                if previous != snapshot:
                    self._complete.discard(snapshot.trace_id)
                    self._late.add(snapshot.trace_id)
                    raise ValueError("A trace/span identity has conflicting evidence.")
                return
            if snapshot.trace_id in self._complete:
                self._complete.remove(snapshot.trace_id)
                self._late.add(snapshot.trace_id)
            if len(self._spans) >= self._max_spans:
                self._overflow = True
                self._complete.clear()
                raise TraceAcquisitionError("Local trace capture capacity was exceeded.")
            self._spans[key] = snapshot

    def mark_complete(self, *, trace_ids: Sequence[str]) -> None:
        """
        Declare a controlled capture complete after all work and export finished.

        The caller must join all child work, disable sampling, and finish export.
        A root span ending or a provider flush alone is not this guarantee.

        Raises:
            ValueError: If there is known loss, sampling, or unfinished evidence.
        """
        scope = TraceScorable(trace_ids=tuple(trace_ids))
        with self._lock:
            self._check_open()
            spans = [span for span in self._spans.values() if span.trace_id in scope.trace_ids]
            if (
                self._overflow
                or self._capture_failed
                or self._late.intersection(scope.trace_ids)
                or any(not span.sampled or span.end_time is None for span in spans)
            ):
                raise ValueError("Cannot declare incomplete or lossy trace capture complete.")
            self._complete.update(scope.trace_ids)

    def record_capture_failure(self) -> None:
        """Invalidate completion when an exporter cannot retain an SDK span."""
        with self._lock:
            self._capture_failed = True
            self._complete.clear()

    async def get_spans_async(self, *, query: TraceQuery) -> TraceQueryResult:
        """Return an immutable, identity-filtered snapshot and honest coverage."""
        with self._lock:
            self._check_open()
            spans = sorted(
                (span for span in self._spans.values() if span.trace_id in query.scope.trace_ids),
                key=lambda span: (span.start_time, span.trace_id, span.span_id),
            )
            reasons: list[str] = []
            if self._overflow:
                reasons.append("capture_capacity_exceeded")
            if self._capture_failed:
                reasons.append("capture_failed")
            if not set(query.scope.trace_ids).issubset(self._complete):
                reasons.append("capture_not_complete")
            if len(spans) > query.limit:
                reasons.append("query_limit_exceeded")
            return TraceQueryResult(
                spans=tuple(span.model_copy(deep=True) for span in spans[: query.limit]),
                coverage=TraceCoverage(complete=not reasons, reasons=tuple(reasons)),
            )

    def close(self) -> None:
        """Stop capture and retrieval without affecting any SDK provider."""
        with self._lock:
            self._closed = True

    def _check_open(self) -> None:
        if self._closed:
            raise TraceAcquisitionError("Local trace capture is closed.")
