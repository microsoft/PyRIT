# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions import PyritException
from pyrit.models import MessagePiece
from pyrit.prompt_target.common.utils import (
    build_empty_truncated_response,
    limit_requests_per_minute,
    validate_temperature,
    validate_top_p,
    warn_truncated_response,
)


def _request_piece(text: str = "ask") -> MessagePiece:
    return MessagePiece(role="user", conversation_id="c", original_value=text, original_value_data_type="text")


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


def test_build_empty_truncated_response_returns_empty_message():
    request = _request_piece("ask")
    result = build_empty_truncated_response(request=request)

    assert result is not None
    assert len(result.message_pieces) == 1
    assert result.message_pieces[0].converted_value == ""
    assert result.message_pieces[0].converted_value_data_type == "text"
    assert result.message_pieces[0].response_error == "empty"


def test_warn_truncated_response_names_the_signal_and_limit(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING):
        warn_truncated_response(signal="finish_reason='length'", limit_parameter="max_completion_tokens")

    assert "finish_reason='length'" in caplog.text
    assert caplog.text.count("max_completion_tokens") == 2


def test_warn_truncated_response_wording_is_shared_across_api_shapes(caplog: pytest.LogCaptureFixture):
    """Only the signal and limit parameter differ between targets; the shared advice must not drift."""
    advice = "Reasoning models consume tokens on hidden reasoning in addition to the visible answer"

    with caplog.at_level(logging.WARNING):
        warn_truncated_response(signal="finish_reason='length'", limit_parameter="max_completion_tokens")
        warn_truncated_response(
            signal="status='incomplete', reason='max_output_tokens'", limit_parameter="max_output_tokens"
        )

    chat_message, responses_message = (record.getMessage() for record in caplog.records)
    assert advice in chat_message
    assert advice in responses_message
    assert "max_output_tokens" in responses_message
    assert "max_output_tokens" not in chat_message
