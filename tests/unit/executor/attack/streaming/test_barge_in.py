# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for ``BargeInAttack`` (R4a — streaming session plumbing only)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.executor.attack import BargeInAttack, BargeInAttackContext
from pyrit.executor.attack.core import AttackParameters
from pyrit.models import AttackOutcome
from pyrit.prompt_target import RealtimeTarget
from pyrit.prompt_target.common.realtime_audio import (
    RealtimeTargetResult,
    _CommittedEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_CLEAN_ENV = {"OPENAI_REALTIME_UNDERLYING_MODEL": ""}


@pytest.fixture
@patch.dict("os.environ", _CLEAN_ENV)
def vad_target(sqlite_instance):
    return RealtimeTarget(
        api_key="test_key", endpoint="wss://test_url", model_name="test", server_vad=True
    )


async def _aiter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _attack_context(*, audio_chunks: AsyncIterator[bytes], objective: str = "obj") -> BargeInAttackContext[Any]:
    return BargeInAttackContext(
        params=AttackParameters(objective=objective),
        audio_chunks=audio_chunks,
    )


def _mock_connection() -> AsyncMock:
    connection = AsyncMock()
    connection.input_audio_buffer.append = AsyncMock()
    connection.conversation.item.create = AsyncMock()
    connection.conversation.item.delete = AsyncMock()
    connection.response.create = AsyncMock()
    connection.session.update = AsyncMock()
    connection.close = AsyncMock()
    return connection


# ---- Construction validation -----------------------------------------------------------------


@patch.dict("os.environ", _CLEAN_ENV)
def test_constructor_rejects_target_without_streaming_capability(sqlite_instance):
    """A target whose capabilities lack STREAMING_BARGE_IN must be rejected at construction."""
    from pyrit.prompt_target import OpenAIChatTarget

    no_streaming = OpenAIChatTarget(api_key="k", endpoint="https://x", model_name="m")
    with pytest.raises(Exception, match="streaming_barge_in"):
        BargeInAttack(objective_target=no_streaming)


def test_constructor_succeeds_with_vad_target(vad_target):
    """A RealtimeTarget declares STREAMING_BARGE_IN — construction succeeds."""
    attack = BargeInAttack(objective_target=vad_target)
    assert attack.get_objective_target() is vad_target


def test_constructor_succeeds_even_without_server_vad_enabled(sqlite_instance):
    """Capability check passes; server VAD is a runtime config concern surfaced when used."""
    with patch.dict("os.environ", _CLEAN_ENV):
        no_vad = RealtimeTarget(api_key="k", endpoint="wss://test_url", model_name="test")
    # Construction succeeds — capability is about the target type, not server_vad config.
    attack = BargeInAttack(objective_target=no_vad)
    assert attack.get_objective_target() is no_vad


# ---- Context validation ----------------------------------------------------------------------


async def test_validate_context_requires_objective(vad_target):
    attack = BargeInAttack(objective_target=vad_target)
    ctx = BargeInAttackContext(
        params=AttackParameters(objective=""),
        audio_chunks=_aiter([b"\x00" * 96]),
    )
    with pytest.raises(ValueError, match="objective"):
        attack._validate_context(context=ctx)


async def test_validate_context_requires_audio_chunks(vad_target):
    attack = BargeInAttack(objective_target=vad_target)
    ctx = BargeInAttackContext(
        params=AttackParameters(objective="o"),
        audio_chunks=None,
    )
    with pytest.raises(ValueError, match="audio_chunks"):
        attack._validate_context(context=ctx)


# ---- Streaming loop end-to-end ---------------------------------------------------------------


async def test_perform_async_streams_chunks_and_tears_down(vad_target):
    """Happy path: connect, send config, subscribe, push chunks, stop, close — no commits."""
    attack = BargeInAttack(objective_target=vad_target)
    connection = _mock_connection()
    vad_target.connect = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.stop = AsyncMock()
    vad_target.subscribe_events_async = AsyncMock(return_value=dispatcher)

    chunks = [b"\x11" * 480, b"\x22" * 480, b"\x33" * 240]
    ctx = _attack_context(audio_chunks=_aiter(chunks))

    with patch.object(attack, "_POST_STREAM_SETTLE_SECONDS", 0):
        result = await attack._perform_async(context=ctx)

    vad_target.connect.assert_awaited_once_with(conversation_id=ctx.conversation_id)
    vad_target.send_streaming_session_config_async.assert_awaited_once()
    vad_target.subscribe_events_async.assert_awaited_once()
    assert vad_target.push_audio_chunk_async.await_count == len(chunks)
    pushed = [call.kwargs["pcm_bytes"] for call in vad_target.push_audio_chunk_async.await_args_list]
    assert pushed == chunks
    dispatcher.stop.assert_awaited_once()
    connection.close.assert_awaited_once()
    assert result.executed_turns == 0
    assert result.outcome == AttackOutcome.UNDETERMINED


async def test_perform_async_fires_request_response_on_commit(vad_target):
    """A commit event must drive request_response_async and increment the turn counter."""
    attack = BargeInAttack(objective_target=vad_target)
    connection = _mock_connection()
    vad_target.connect = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock()

    # Capture the registered on_user_audio_committed so we can drive it.
    captured: dict[str, Any] = {}

    async def fake_subscribe(*, connection, on_user_audio_committed):
        captured["on_committed"] = on_user_audio_committed
        return AsyncMock()

    vad_target.subscribe_events_async = AsyncMock(side_effect=fake_subscribe)

    expected = RealtimeTargetResult(audio_bytes=b"\xaa" * 96, transcripts=["hello"])
    expected_future: asyncio.Future[RealtimeTargetResult] = asyncio.get_event_loop().create_future()
    expected_future.set_result(expected)
    vad_target.request_response_async = AsyncMock(return_value=expected_future)

    async def chunks_then_commit() -> AsyncIterator[bytes]:
        yield b"\x00" * 480
        # Drive a fake commit mid-stream.
        await captured["on_committed"](_CommittedEvent(item_id="raw_1"))

    ctx = _attack_context(audio_chunks=chunks_then_commit())

    with patch.object(attack, "_POST_STREAM_SETTLE_SECONDS", 0):
        result = await attack._perform_async(context=ctx)

    vad_target.request_response_async.assert_awaited_once()
    assert result.executed_turns == 1
    assert "1 assistant turn" in (result.outcome_reason or "")


async def test_perform_async_stops_dispatcher_even_on_exception(vad_target):
    """If the chunk loop raises, dispatcher.stop() and connection.close() still run."""
    attack = BargeInAttack(objective_target=vad_target)
    connection = _mock_connection()
    vad_target.connect = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock(side_effect=RuntimeError("push exploded"))
    dispatcher = AsyncMock()
    vad_target.subscribe_events_async = AsyncMock(return_value=dispatcher)

    ctx = _attack_context(audio_chunks=_aiter([b"\x00" * 96]))

    with pytest.raises(RuntimeError, match="push exploded"):
        with patch.object(attack, "_POST_STREAM_SETTLE_SECONDS", 0):
            await attack._perform_async(context=ctx)

    dispatcher.stop.assert_awaited_once()
    connection.close.assert_awaited_once()


# ---- send_streaming_session_config_async (target-side helper added in R4a) -------------------


async def test_send_streaming_session_config_async_emits_create_response_false(vad_target):
    """The streaming session config must flip create_response to False on turn_detection."""
    connection = _mock_connection()
    await vad_target.send_streaming_session_config_async(
        connection=connection, system_prompt="hi"
    )
    connection.session.update.assert_awaited_once()
    config = connection.session.update.call_args.kwargs["session"]
    assert config["audio"]["input"]["turn_detection"]["create_response"] is False


@patch.dict("os.environ", _CLEAN_ENV)
async def test_send_streaming_session_config_async_requires_server_vad(sqlite_instance):
    """Without server VAD, sending streaming session config must raise."""
    no_vad = RealtimeTarget(api_key="k", endpoint="wss://test_url", model_name="test")
    connection = _mock_connection()
    with pytest.raises(ValueError, match="server VAD"):
        await no_vad.send_streaming_session_config_async(connection=connection, system_prompt="hi")
