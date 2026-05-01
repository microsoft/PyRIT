# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions import PyritException
from pyrit.prompt_target.common.utils import (
    limit_requests_per_minute,
    validate_temperature,
    validate_top_p,
)


def test_validate_temperature_none():
    validate_temperature(None)


def test_validate_temperature_valid_zero():
    validate_temperature(0.0)


def test_validate_temperature_valid_two():
    validate_temperature(2.0)


def test_validate_temperature_valid_mid():
    validate_temperature(1.0)


def test_validate_temperature_below_zero_raises():
    with pytest.raises(PyritException, match="temperature must be between 0 and 2"):
        validate_temperature(-0.1)


def test_validate_temperature_above_two_raises():
    with pytest.raises(PyritException, match="temperature must be between 0 and 2"):
        validate_temperature(2.1)


def test_validate_top_p_none():
    validate_top_p(None)


def test_validate_top_p_valid_zero():
    validate_top_p(0.0)


def test_validate_top_p_valid_one():
    validate_top_p(1.0)


def test_validate_top_p_valid_mid():
    validate_top_p(0.5)


def test_validate_top_p_below_zero_raises():
    with pytest.raises(PyritException, match="top_p must be between 0 and 1"):
        validate_top_p(-0.1)


def test_validate_top_p_above_one_raises():
    with pytest.raises(PyritException, match="top_p must be between 0 and 1"):
        validate_top_p(1.1)


async def test_limit_requests_per_minute_no_rpm():
    mock_self = MagicMock()
    mock_self._max_requests_per_minute = None

    inner_func = AsyncMock(return_value="response")
    decorated = limit_requests_per_minute(inner_func)

    with patch("asyncio.sleep") as mock_sleep:
        result = await decorated(mock_self, message="test")
        mock_sleep.assert_not_called()
    assert result == "response"


async def test_limit_requests_per_minute_with_rpm():
    mock_self = MagicMock()
    mock_self._max_requests_per_minute = 30

    inner_func = AsyncMock(return_value="response")
    decorated = limit_requests_per_minute(inner_func)

    with patch("asyncio.sleep") as mock_sleep:
        result = await decorated(mock_self, message="test")
        mock_sleep.assert_called_once_with(2.0)  # 60/30
    assert result == "response"


async def test_limit_requests_per_minute_zero_rpm():
    mock_self = MagicMock()
    mock_self._max_requests_per_minute = 0

    inner_func = AsyncMock(return_value="response")
    decorated = limit_requests_per_minute(inner_func)

    with patch("asyncio.sleep") as mock_sleep:
        result = await decorated(mock_self, message="test")
        mock_sleep.assert_not_called()
    assert result == "response"
