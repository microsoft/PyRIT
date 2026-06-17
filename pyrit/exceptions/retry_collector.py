# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contextvar-based retry event collector for capturing Tenacity retry events."""

import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from tenacity import RetryCallState

from pyrit.exceptions.exception_context import get_execution_context
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

    def record(self, *, retry_state: RetryCallState) -> None:
        """
        Record a retry event from a Tenacity RetryCallState.

        Extracts information from the retry state and the current
        ExecutionContext to build a structured RetryEvent.

        Args:
            retry_state (RetryCallState): The Tenacity retry call state from the after callback.
        """
        elapsed = time.monotonic() - retry_state.start_time
        fn_name = getattr(retry_state.fn, "__name__", "unknown") if retry_state.fn is not None else "unknown"

        # Extract exception info
        exception_type = ""
        exception_message = ""
        outcome = retry_state.outcome
        if outcome is not None and outcome.failed:
            exc = outcome.exception()
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
            attempt_number=retry_state.attempt_number,
            function_name=fn_name,
            exception_type=exception_type,
            exception_message=exception_message,
            component_role=component_role,
            component_name=component_name,
            endpoint=endpoint,
            elapsed_seconds=round(elapsed, 3),
        )
        self.events.append(event)


_retry_collector: ContextVar[RetryCollector | None] = ContextVar("retry_collector", default=None)


def get_retry_collector() -> RetryCollector | None:
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
