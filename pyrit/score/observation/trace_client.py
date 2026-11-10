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
    """
    Thread-safe local capture with explicit, caller-owned completion.

    Capture failures and capacity overflow make only the affected trace incomplete. When the
    store is full, the oldest complete trace is evicted to make room, or, if none is complete,
    the oldest other trace. A later query for an evicted trace reports it as evicted. Retained
    spans, traces, and per-trace markers are all bounded by ``max_spans``, so capacity bounds
    retained evidence, not a run.
    """

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
        self._trace_keys: dict[str, list[tuple[str, str]]] = {}
        self._complete: dict[str, None] = {}
        self._late: set[str] = set()
        self._trace_failures: dict[str, set[str]] = {}
        self._evicted: dict[str, None] = {}
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
            if snapshot.trace_id in self._evicted:
                return
            previous = self._spans.get(key)
            if previous is not None:
                if previous != snapshot:
                    self._complete.pop(snapshot.trace_id, None)
                    self._late.add(snapshot.trace_id)
                    raise ValueError("A trace/span identity has conflicting evidence.")
                return
            if snapshot.trace_id in self._complete:
                del self._complete[snapshot.trace_id]
                self._late.add(snapshot.trace_id)
            self._trace_keys.setdefault(snapshot.trace_id, [])
            if not self._make_room(keep=snapshot.trace_id, span_slot=True):
                self._trace_failures.setdefault(snapshot.trace_id, set()).add("capture_capacity_exceeded")
                raise TraceAcquisitionError("Local trace capture capacity was exceeded.")
            self._spans[key] = snapshot
            self._trace_keys[snapshot.trace_id].append(key)

    def reopen(self, *, trace_id: str) -> bool:
        """
        Withdraw completion because a new span started in a trace already declared complete.

        The trace stays incomplete, since its earlier snapshot may have been scored without that span.

        Returns:
            bool: Whether the trace is retained in this capture.
        """
        with self._lock:
            if trace_id not in self._trace_keys:
                return False
            if trace_id in self._complete:
                del self._complete[trace_id]
                self._late.add(trace_id)
            return True

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
            spans = [self._spans[key] for trace_id in scope.trace_ids for key in self._trace_keys.get(trace_id, ())]
            if (
                self._capture_failed
                or any(trace_id in self._trace_failures or trace_id in self._evicted for trace_id in scope.trace_ids)
                or self._late.intersection(scope.trace_ids)
                or any(not span.sampled or span.end_time is None for span in spans)
            ):
                raise ValueError("Cannot declare incomplete or lossy trace capture complete.")
            for trace_id in scope.trace_ids:
                self._complete.pop(trace_id, None)
                self._complete[trace_id] = None

    def record_capture_failure(self, *, trace_id: str | None = None) -> None:
        """
        Invalidate completion when an exporter cannot retain an SDK span.

        Args:
            trace_id (str | None): The affected trace. Without one, every trace is invalidated.
        """
        with self._lock:
            if trace_id is None:
                self._capture_failed = True
                self._complete.clear()
                return
            if trace_id in self._evicted:
                return
            self._trace_keys.setdefault(trace_id, [])
            self._trace_failures.setdefault(trace_id, set()).add("capture_failed")
            self._complete.pop(trace_id, None)
            self._make_room(keep=trace_id, span_slot=False)

    async def get_spans_async(self, *, query: TraceQuery) -> TraceQueryResult:
        """Return an immutable, identity-filtered snapshot and honest coverage."""
        with self._lock:
            self._check_open()
            requested = query.scope.trace_ids
            spans = sorted(
                (self._spans[key] for trace_id in requested for key in self._trace_keys.get(trace_id, ())),
                # Stable on ties, so spans with equal start times keep the order they were recorded in.
                key=lambda span: span.start_time,
            )
            reasons: list[str] = []
            if self._capture_failed:
                reasons.append("capture_failed")
            for trace_id in requested:
                reasons.extend(sorted(self._trace_failures.get(trace_id, ())))
            evicted = [trace_id for trace_id in requested if trace_id in self._evicted]
            if evicted:
                reasons.append("trace_evicted")
            if not all(trace_id in self._complete for trace_id in requested):
                reasons.append("capture_not_complete")
            if len(spans) > query.limit:
                reasons.append("query_limit_exceeded")
            reasons = list(dict.fromkeys(reasons))
            if len(evicted) == len(requested):
                return TraceQueryResult(available=False, coverage=TraceCoverage(reasons=tuple(reasons)))
            return TraceQueryResult(
                spans=tuple(span.model_copy(deep=True) for span in spans[: query.limit]),
                coverage=TraceCoverage(complete=not reasons, reasons=tuple(reasons)),
            )

    def close(self) -> None:
        """Stop capture and retrieval without affecting any SDK provider."""
        with self._lock:
            self._closed = True

    def _make_room(self, *, keep: str, span_slot: bool) -> bool:
        """
        Evict traces other than ``keep`` until the requested span slot and every trace fit.

        The caller holds the lock.

        Returns:
            bool: Whether there is room.
        """
        while (span_slot and len(self._spans) >= self._max_spans) or len(self._trace_keys) > self._max_spans:
            victim = next(iter(self._complete), None) or next((t for t in self._trace_keys if t != keep), None)
            if victim is None:
                return False
            for key in self._trace_keys.pop(victim):
                del self._spans[key]
            self._complete.pop(victim, None)
            self._late.discard(victim)
            self._trace_failures.pop(victim, None)
            self._evicted[victim] = None
            while len(self._evicted) > self._max_spans:
                del self._evicted[next(iter(self._evicted))]
        return True

    def _check_open(self) -> None:
        if self._closed:
            raise TraceAcquisitionError("Local trace capture is closed.")
