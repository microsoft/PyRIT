# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.exceptions import PyritException
from pyrit.exceptions.analytics_exception import (
    AnalyticsBusyException,
    AnalyticsDataException,
    AnalyticsException,
    AnalyticsTimeoutException,
)


@pytest.mark.parametrize(
    "exception_type, status_code, message",
    [
        (AnalyticsBusyException, 503, "Analytics is busy. Retry the request shortly."),
        (AnalyticsTimeoutException, 504, "Analytics query timed out. Narrow the filters or retry."),
    ],
)
def test_analytics_failure_preserves_pyrit_exception_contract(
    *, exception_type: type[AnalyticsException], status_code: int, message: str
) -> None:
    error = exception_type()
    assert isinstance(error, PyritException)
    assert error.status_code == status_code
    assert error.message == message
    assert str(error) == f"Status Code: {status_code}, Message: {message}"


def test_analytics_data_exception_preserves_failure_details() -> None:
    error = AnalyticsDataException("Invalid converter metadata")
    assert isinstance(error, AnalyticsException)
    assert error.status_code == 500
    assert error.message == "Invalid converter metadata"
