# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions import PyritException, RateLimitException, pyrit_target_retry
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


async def test_limit_requests_per_minute_serializes_concurrent_starts() -> None:
    target = MagicMock()
    target._max_requests_per_minute = 60
    sleep_started: asyncio.Queue[int] = asyncio.Queue()
    release_sleep: asyncio.Queue[None] = asyncio.Queue()
    provider_calls: asyncio.Queue[int] = asyncio.Queue()
    delays: list[float] = []

    async def controlled_sleep_async(delay: float) -> None:
        index = len(delays)
        delays.append(delay)
        await sleep_started.put(index)
        await release_sleep.get()

    async def send_async(target: MagicMock, *, request_index: int) -> int:
        await provider_calls.put(request_index)
        return request_index

    decorated = limit_requests_per_minute(send_async)
    with patch("pyrit.prompt_target.common.utils.asyncio.sleep", side_effect=controlled_sleep_async):
        tasks = [asyncio.create_task(decorated(target, request_index=index)) for index in range(3)]

        assert await sleep_started.get() == 0
        assert delays == [1.0]

        for index in range(3):
            await release_sleep.put(None)
            assert await provider_calls.get() == index
            if index < 2:
                assert await sleep_started.get() == index + 1
                assert len(delays) == index + 2

        assert await asyncio.gather(*tasks) == [0, 1, 2]


async def test_limit_requests_per_minute_cancellation_releases_lock() -> None:
    target = MagicMock()
    target._max_requests_per_minute = 60
    sleep_started = asyncio.Event()
    release_sleep = asyncio.Event()
    provider_calls: list[str] = []

    async def controlled_sleep_async(delay: float) -> None:
        sleep_started.set()
        await release_sleep.wait()

    async def send_async(target: MagicMock, *, value: str) -> str:
        provider_calls.append(value)
        return value

    decorated = limit_requests_per_minute(send_async)
    with patch("pyrit.prompt_target.common.utils.asyncio.sleep", side_effect=controlled_sleep_async):
        cancelled_task = asyncio.create_task(decorated(target, value="cancelled"))
        await sleep_started.wait()
        cancelled_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_task

        sleep_started.clear()
        next_task = asyncio.create_task(decorated(target, value="next"))
        await sleep_started.wait()
        release_sleep.set()

        assert await next_task == "next"
        assert provider_calls == ["next"]


async def test_target_retry_paces_every_attempt() -> None:
    target = MagicMock()
    target._max_requests_per_minute = 1
    attempts = 0

    @pyrit_target_retry
    @limit_requests_per_minute
    async def send_async(target: MagicMock) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RateLimitException
        return "response"

    with patch("pyrit.prompt_target.common.utils.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        assert await send_async(target) == "response"

    assert attempts == 2
    assert [call.args[0] for call in mock_sleep.await_args_list].count(60.0) == 2


def test_limit_requests_per_minute_rebuilds_lock_for_new_event_loop() -> None:
    target = MagicMock()
    target._max_requests_per_minute = 60
    decorated = limit_requests_per_minute(AsyncMock(return_value="response"))

    async def invoke_async() -> asyncio.Lock:
        with patch("pyrit.prompt_target.common.utils.asyncio.sleep", new_callable=AsyncMock):
            await decorated(target)
        lock = vars(target)["_rate_limit_lock"]
        assert isinstance(lock, asyncio.Lock)
        return lock

    first_lock = asyncio.run(invoke_async())
    second_lock = asyncio.run(invoke_async())

    assert first_lock is not second_lock


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
