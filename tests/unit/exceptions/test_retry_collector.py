# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio

from pyrit.exceptions.retry_collector import (
    RetryCollector,
    clear_retry_collector,
    get_retry_collector,
    set_retry_collector,
)


class TestRetryCollector:
    """Tests for the RetryCollector and its contextvar helpers."""

    def test_collector_starts_empty(self) -> None:
        """A new RetryCollector has no events."""
        c = RetryCollector()
        assert c.events == []

    def test_contextvar_default_is_none(self) -> None:
        """get_retry_collector returns None when no collector is set."""
        clear_retry_collector()
        assert get_retry_collector() is None

    def test_set_and_get(self) -> None:
        """set_retry_collector makes it visible to get_retry_collector."""
        c = RetryCollector()
        set_retry_collector(c)
        assert get_retry_collector() is c
        clear_retry_collector()

    def test_clear(self) -> None:
        """clear_retry_collector resets the contextvar to None."""
        c = RetryCollector()
        set_retry_collector(c)
        clear_retry_collector()
        assert get_retry_collector() is None

    def test_record_extracts_exception_info(self) -> None:
        """record() extracts exception type and message from retry_state."""
        from unittest.mock import MagicMock

        c = RetryCollector()

        # Build a mock retry_state matching Tenacity's RetryCallState
        retry_state = MagicMock()
        retry_state.attempt_number = 2
        retry_state.start_time = 0.0
        retry_state.fn = MagicMock()
        retry_state.fn.__name__ = "my_function"

        # Mock outcome with .failed=True and .exception() returning a ValueError
        outcome = MagicMock()
        outcome.failed = True
        outcome.exception.return_value = ValueError("test error")
        retry_state.outcome = outcome

        c.record(retry_state=retry_state)

        assert len(c.events) == 1
        evt = c.events[0]
        assert evt.attempt_number == 2
        assert evt.function_name == "my_function"
        assert evt.exception_type == "ValueError"
        assert evt.exception_message == "test error"

    def test_record_multiple_events(self) -> None:
        """record() accumulates events."""
        from unittest.mock import MagicMock

        c = RetryCollector()

        for i in range(3):
            retry_state = MagicMock()
            retry_state.attempt_number = i + 1
            retry_state.start_time = 0.0
            retry_state.fn = MagicMock()
            retry_state.fn.__name__ = f"fn_{i}"
            outcome = MagicMock()
            outcome.failed = True
            outcome.exception.return_value = RuntimeError(f"error {i}")
            retry_state.outcome = outcome
            c.record(retry_state=retry_state)

        assert len(c.events) == 3
        assert c.events[0].function_name == "fn_0"
        assert c.events[2].function_name == "fn_2"

    def test_contextvar_isolation_across_tasks(self) -> None:
        """Each asyncio task gets its own contextvar value."""
        results: dict[str, bool] = {}

        async def task_a() -> None:
            c = RetryCollector()
            set_retry_collector(c)
            await asyncio.sleep(0.01)
            results["a_has_collector"] = get_retry_collector() is c
            clear_retry_collector()

        async def task_b() -> None:
            await asyncio.sleep(0.005)
            results["b_sees_none"] = get_retry_collector() is None

        async def run() -> None:
            clear_retry_collector()
            await asyncio.gather(task_a(), task_b())

        asyncio.run(run())
        assert results.get("a_has_collector") is True
        assert results.get("b_sees_none") is True

    def test_record_extracts_execution_context(self) -> None:
        """record() extracts component_role, component_name, and endpoint from ExecutionContext."""
        from unittest.mock import MagicMock

        from pyrit.exceptions.exception_context import (
            ComponentRole,
            ExecutionContext,
            set_execution_context,
        )

        c = RetryCollector()

        ctx = ExecutionContext(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            component_name="OpenAIChatTarget",
            endpoint="https://api.openai.com",
        )
        set_execution_context(ctx)

        retry_state = MagicMock()
        retry_state.attempt_number = 1
        retry_state.start_time = 0.0
        retry_state.fn = MagicMock()
        retry_state.fn.__name__ = "send_prompt_async"
        outcome = MagicMock()
        outcome.failed = True
        outcome.exception.return_value = RuntimeError("timeout")
        retry_state.outcome = outcome

        c.record(retry_state=retry_state)

        assert len(c.events) == 1
        evt = c.events[0]
        assert evt.component_role == "objective_target"
        assert evt.component_name == "OpenAIChatTarget"
        assert evt.endpoint == "https://api.openai.com"
