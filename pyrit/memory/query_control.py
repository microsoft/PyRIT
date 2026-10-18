# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Share a monotonic budget between an analytics worker and its database operation.

The requesting task may signal cancellation from another thread. The worker
observes that signal at safe checkpoints or through SQLite's progress callback;
this object does not terminate a thread or release a connection on its behalf.
"""

import threading
import time
from dataclasses import dataclass, field

from pyrit.exceptions.analytics_exception import AnalyticsTimeoutException


@dataclass
class QueryControl:
    """
    A request-local absolute deadline and thread-safe cancellation signal.

    Attributes:
        deadline (float): Absolute ``time.monotonic()`` time, not a wall-clock
            timestamp or a per-statement timeout. All phases share this budget.
        cancel_event (threading.Event): Independent signal for this operation.
            Cancellation and deadline expiry deliberately use the same exception.
    """

    deadline: float
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def remaining(self) -> float:
        """The nonnegative deadline budget in seconds, independent of the cancellation flag."""
        return max(0.0, self.deadline - time.monotonic())

    @property
    def expired(self) -> bool:
        """Whether cancellation or the execution deadline has been reached."""
        return self.cancel_event.is_set() or self.remaining <= 0

    def cancel(self) -> None:
        """Signal cancellation without interrupting another query or returning this worker's resources early."""
        self.cancel_event.set()

    def check(self) -> None:
        """
        Stop an expired operation before it acquires or uses database resources.

        Call after CPU-only work as well, so an over-budget result is not reported
        as successful merely because its last database statement finished in time.

        Raises:
            AnalyticsTimeoutException: If the operation was cancelled or expired.
        """
        if self.expired:
            raise AnalyticsTimeoutException
