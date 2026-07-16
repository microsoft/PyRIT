# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Data model for capturing individual retry events during execution."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


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
    component_name: str | None = None
    endpoint: str | None = None
    elapsed_seconds: float = 0.0
