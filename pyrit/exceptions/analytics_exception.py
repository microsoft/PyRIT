# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.exceptions.exception_classes import PyritException


class AnalyticsException(PyritException):
    """Base for actionable analytics failures."""


class AnalyticsBusyException(AnalyticsException):
    """The bounded analytics admission queue could not accept a request."""

    def __init__(self) -> None:
        """Initialize an explicit retryable overload error."""
        super().__init__(status_code=503, message="Analytics is busy. Retry the request shortly.")


class AnalyticsTimeoutException(AnalyticsException):
    """The database request exceeded its deadline."""

    def __init__(self) -> None:
        """Initialize an explicit query timeout."""
        super().__init__(status_code=504, message="Analytics query timed out. Narrow the filters or retry.")


class AnalyticsDataException(AnalyticsException):
    """Stored metadata could not be interpreted without misrepresenting results."""

    def __init__(self, message: str) -> None:
        """Initialize a stored-data error."""
        super().__init__(status_code=500, message=message)
