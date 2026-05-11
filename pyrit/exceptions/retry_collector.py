# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contextvar-based retry event collector for capturing Tenacity retry events."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

from pyrit.models.retry_event import RetryEvent


@dataclass
class RetryCollector:
    """
    Collects retry events during attack execution.

    Uses contextvar for thread/task-safe scoping. Each attack execution
    creates its own collector so retry events are naturally scoped
    per-objective.
    """

    events: list[RetryEvent] = field(default_factory=list)

    def record(self, *, retry_state: Any) -> None:
        """
        Record a retry event from a Tenacity RetryCallState.

        Extracts information from the retry state and the current
        ExecutionContext to build a structured RetryEvent.

        Args:
            retry_state: The Tenacity RetryCallState from the after callback.
        """
        import time

        from pyrit.exceptions.exception_context import get_execution_context

        # Extract basic info from retry_state
        call_count = getattr(retry_state, "attempt_number", None) or 0
        start_time = getattr(retry_state, "start_time", None)
        elapsed = (time.monotonic() - start_time) if start_time is not None else 0.0

        fn = getattr(retry_state, "fn", None)
        fn_name = getattr(fn, "__name__", "unknown") if fn else "unknown"

        # Extract exception info
        exception_type = ""
        exception_message = ""
        outcome = getattr(retry_state, "outcome", None)
        if outcome and getattr(outcome, "failed", False):
            exc = outcome.exception() if hasattr(outcome, "exception") else None
            if exc:
                exception_type = type(exc).__name__
                exception_message = str(exc)

        # Extract context info
        component_role = ""
        component_name: str | None = None
        endpoint: str | None = None
        try:
            exec_context = get_execution_context()
            if exec_context:
                component_role = exec_context.component_role.value
                component_name = exec_context.component_name
                endpoint = exec_context.endpoint
        except Exception:
            pass

        event = RetryEvent(
            attempt_number=call_count,
            function_name=fn_name,
            exception_type=exception_type,
            exception_message=exception_message,
            component_role=component_role,
            component_name=component_name,
            endpoint=endpoint,
            elapsed_seconds=round(elapsed, 3),
        )
        self.events.append(event)


_retry_collector: ContextVar[Optional[RetryCollector]] = ContextVar("retry_collector", default=None)


def get_retry_collector() -> Optional[RetryCollector]:
    """
    Get the current retry collector.

    Returns:
        The active RetryCollector, or None if not set.
    """
    return _retry_collector.get()


def set_retry_collector(collector: RetryCollector) -> None:
    """
    Set the current retry collector.

    Args:
        collector: The RetryCollector to activate.
    """
    _retry_collector.set(collector)


def clear_retry_collector() -> None:
    """Clear the current retry collector."""
    _retry_collector.set(None)
