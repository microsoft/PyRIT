# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Data model for capturing individual retry events during execution."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RetryEvent:
    """
    A single retry attempt captured during attack execution.

    Records structured information about a Tenacity retry event, including
    which component was retrying, what exception triggered the retry, and
    timing information. These events are collected by a RetryCollector and
    attached to AttackResult objects for persistence and REST API exposure.
    """

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attempt_number: int = 0
    function_name: str = ""
    exception_type: str = ""
    exception_message: str = ""
    component_role: str = ""
    component_name: str | None = None
    endpoint: str | None = None
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        """
        Serialize to a dictionary suitable for JSON storage.

        Returns:
            dict: Dictionary representation of the retry event.
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "attempt_number": self.attempt_number,
            "function_name": self.function_name,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "component_role": self.component_role,
            "component_name": self.component_name,
            "endpoint": self.endpoint,
            "elapsed_seconds": self.elapsed_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RetryEvent":
        """
        Deserialize from a dictionary.

        Args:
            data: Dictionary representation of a retry event.

        Returns:
            RetryEvent: Deserialized retry event.
        """
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            attempt_number=data.get("attempt_number", 0),
            function_name=data.get("function_name", ""),
            exception_type=data.get("exception_type", ""),
            exception_message=data.get("exception_message", ""),
            component_role=data.get("component_role", ""),
            component_name=data.get("component_name"),
            endpoint=data.get("endpoint"),
            elapsed_seconds=data.get("elapsed_seconds", 0.0),
        )
