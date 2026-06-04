# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Data model for capturing individual retry events during execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from pyrit.common.deprecation import print_deprecation_message


class RetryEvent(BaseModel):
    """
    A single retry attempt captured during attack execution.

    Records structured information about a Tenacity retry event, including
    which component was retrying, what exception triggered the retry, and
    timing information. These events are collected by a RetryCollector and
    attached to AttackResult objects for persistence and REST API exposure.
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    attempt_number: int = 0
    function_name: str = ""
    exception_type: str = ""
    exception_message: str = ""
    component_role: str = ""
    component_name: Optional[str] = None
    endpoint: Optional[str] = None
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        """
        Serialize to a dictionary suitable for JSON storage.

        .. deprecated::
            Use ``model_dump`` with ``mode="json"`` instead. This method
            will be removed in version 0.16.0.

        Returns:
            dict: Dictionary representation of the retry event.
        """
        print_deprecation_message(
            old_item=RetryEvent.to_dict,
            new_item='RetryEvent.model_dump(mode="json")',
            removed_in="0.16.0",
        )
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict) -> RetryEvent:
        """
        Deserialize from a dictionary.

        .. deprecated::
            Use ``model_validate`` instead. This method will be removed
            in version 0.16.0.

        Args:
            data: Dictionary representation of a retry event.

        Returns:
            RetryEvent: Deserialized retry event.
        """
        print_deprecation_message(
            old_item=RetryEvent.from_dict,
            new_item="RetryEvent.model_validate",
            removed_in="0.16.0",
        )
        return cls.model_validate(data)
