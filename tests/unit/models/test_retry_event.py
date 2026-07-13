# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime, timezone

from pyrit.models.retry_event import RetryEvent


class TestRetryEvent:
    """Tests for the RetryEvent model."""

    def test_defaults(self) -> None:
        """RetryEvent constructed with minimal args gets correct defaults."""
        evt = RetryEvent(attempt_number=1, function_name="test_fn")
        assert evt.attempt_number == 1
        assert evt.function_name == "test_fn"
        assert evt.exception_type == ""
        assert evt.exception_message == ""
        assert evt.component_role == ""
        assert evt.component_name is None
        assert evt.endpoint is None
        assert evt.elapsed_seconds == 0.0
        assert evt.timestamp is not None
        assert evt.timestamp.tzinfo is timezone.utc

    def test_full_construction(self) -> None:
        """RetryEvent constructed with all args stores them correctly."""
        ts = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
        evt = RetryEvent(
            attempt_number=3,
            function_name="send_prompt_async",
            exception_type="RateLimitError",
            exception_message="Rate limit exceeded",
            component_role="objective_target",
            component_name="OpenAIChatTarget",
            endpoint="https://api.openai.com/v1/chat",
            elapsed_seconds=5.123,
            timestamp=ts,
        )
        assert evt.attempt_number == 3
        assert evt.function_name == "send_prompt_async"
        assert evt.exception_type == "RateLimitError"
        assert evt.exception_message == "Rate limit exceeded"
        assert evt.component_role == "objective_target"
        assert evt.component_name == "OpenAIChatTarget"
        assert evt.endpoint == "https://api.openai.com/v1/chat"
        assert evt.elapsed_seconds == 5.123
        assert evt.timestamp == ts

    def test_to_dict(self) -> None:
        """model_dump(mode="json") returns a JSON-serializable dictionary."""
        evt = RetryEvent(
            attempt_number=2,
            function_name="fn",
            exception_type="ValueError",
            exception_message="bad input",
            component_role="scorer",
            component_name="TFScorer",
            endpoint="https://example.com",
            elapsed_seconds=1.5,
        )
        d = evt.model_dump(mode="json")
        assert d["attempt_number"] == 2
        assert d["function_name"] == "fn"
        assert d["exception_type"] == "ValueError"
        assert d["exception_message"] == "bad input"
        assert d["component_role"] == "scorer"
        assert d["component_name"] == "TFScorer"
        assert d["endpoint"] == "https://example.com"
        assert d["elapsed_seconds"] == 1.5
        assert "timestamp" in d

    def test_from_dict_roundtrip(self) -> None:
        """model_validate correctly reconstructs a RetryEvent from model_dump output."""
        original = RetryEvent(
            attempt_number=1,
            function_name="call_target",
            exception_type="TimeoutError",
            exception_message="Request timed out",
            component_role="objective_target",
            component_name="AzureTarget",
            endpoint="https://azure.openai.com",
            elapsed_seconds=10.0,
        )
        d = original.model_dump(mode="json")
        restored = RetryEvent.model_validate(d)

        assert restored.attempt_number == original.attempt_number
        assert restored.function_name == original.function_name
        assert restored.exception_type == original.exception_type
        assert restored.exception_message == original.exception_message
        assert restored.component_role == original.component_role
        assert restored.component_name == original.component_name
        assert restored.endpoint == original.endpoint
        assert restored.elapsed_seconds == original.elapsed_seconds

    def test_from_dict_missing_optional_fields(self) -> None:
        """model_validate handles missing optional fields gracefully."""
        d = {
            "attempt_number": 1,
            "function_name": "fn",
            "timestamp": "2026-05-07T12:00:00+00:00",
        }
        evt = RetryEvent.model_validate(d)
        assert evt.attempt_number == 1
        assert evt.function_name == "fn"
        assert evt.exception_type == ""
        assert evt.component_name is None
        assert evt.endpoint is None
        assert evt.elapsed_seconds == 0.0

    def test_from_dict_timestamp_parsing(self) -> None:
        """model_validate correctly parses ISO format timestamp."""
        d = {
            "attempt_number": 1,
            "function_name": "fn",
            "timestamp": "2026-05-07T12:30:00+00:00",
        }
        evt = RetryEvent.model_validate(d)
        assert evt.timestamp.year == 2026
        assert evt.timestamp.month == 5
        assert evt.timestamp.hour == 12
        assert evt.timestamp.minute == 30
