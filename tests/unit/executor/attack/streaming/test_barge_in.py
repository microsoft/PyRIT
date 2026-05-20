# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for ``BargeInAttack`` and supporting helpers."""

from __future__ import annotations

import asyncio
import os
import tempfile
import wave
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import BargeInAttack, BargeInAttackContext
from pyrit.executor.attack.core import AttackConverterConfig, AttackParameters
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import AttackOutcome
from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
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
    return RealtimeTarget(api_key="test_key", endpoint="wss://test_url", model_name="test", server_vad=True)


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
    vad_target.connect_async = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.stop = AsyncMock()
    vad_target.subscribe_events_async = AsyncMock(return_value=dispatcher)

    chunks = [b"\x11" * 480, b"\x22" * 480, b"\x33" * 240]
    ctx = _attack_context(audio_chunks=_aiter(chunks))

    with patch.object(attack, "_MAX_POST_STREAM_WAIT_SECONDS", 0):
        result = await attack._perform_async(context=ctx)

    vad_target.connect_async.assert_awaited_once_with(conversation_id=ctx.conversation_id)
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
    vad_target.connect_async = AsyncMock(return_value=connection)
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
        await asyncio.create_task(captured["on_committed"](_CommittedEvent(item_id="raw_1")))

    ctx = _attack_context(audio_chunks=chunks_then_commit())

    with patch.object(attack, "_MAX_POST_STREAM_WAIT_SECONDS", 0):
        result = await attack._perform_async(context=ctx)

    vad_target.request_response_async.assert_awaited_once()
    assert result.executed_turns == 1
    assert "1 assistant turn" in (result.outcome_reason or "")


async def test_perform_async_stops_dispatcher_even_on_exception(vad_target):
    """If the chunk loop raises, dispatcher.stop() and connection.close() still run."""
    attack = BargeInAttack(objective_target=vad_target)
    connection = _mock_connection()
    vad_target.connect_async = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock(side_effect=RuntimeError("push exploded"))
    dispatcher = AsyncMock()
    vad_target.subscribe_events_async = AsyncMock(return_value=dispatcher)

    ctx = _attack_context(audio_chunks=_aiter([b"\x00" * 96]))

    with pytest.raises(RuntimeError, match="push exploded"):
        with patch.object(attack, "_MAX_POST_STREAM_WAIT_SECONDS", 0):
            await attack._perform_async(context=ctx)

    dispatcher.stop.assert_awaited_once()
    connection.close.assert_awaited_once()


# ---- send_streaming_session_config_async (target-side helper added in R4a) -------------------


async def test_send_streaming_session_config_async_emits_create_response_false(vad_target):
    """The streaming session config must flip create_response to False on turn_detection."""
    connection = _mock_connection()
    await vad_target.send_streaming_session_config_async(connection=connection, system_prompt="hi")
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


# Placeholder for R4b tests


# ---- Convert-on-commit dance (R4b) ----------------------------------------------------------


def _make_audio_converter(transformer, *, identifier_name: str = "MockAudioConverter"):
    """Mock audio converter whose convert_tokens_async runs transformer(pcm) and emits a new WAV path."""
    converter = MagicMock()
    converter.get_identifier = MagicMock(
        return_value=ComponentIdentifier(class_name=identifier_name, class_module="tests.unit.mocks"),
    )

    async def _convert(*, prompt, input_type, start_token=None, end_token=None):
        assert input_type == "audio_path"
        with wave.open(prompt, "rb") as wf_in:
            sample_rate = wf_in.getframerate()
            pcm = wf_in.readframes(wf_in.getnframes())
        new_pcm = transformer(pcm)
        out_dir = tempfile.mkdtemp()
        out_path = os.path.join(out_dir, "out.wav")
        with wave.open(out_path, "wb") as wf_out:
            wf_out.setnchannels(1)
            wf_out.setsampwidth(2)
            wf_out.setframerate(sample_rate)
            wf_out.writeframes(new_pcm)
        result = MagicMock()
        result.output_text = out_path
        return result

    converter.convert_tokens_async = AsyncMock(side_effect=_convert)
    return converter


def _converter_config(converters: list[Any]) -> AttackConverterConfig:
    """Wrap a list of converters into an AttackConverterConfig."""
    return AttackConverterConfig(
        request_converters=PromptConverterConfiguration.from_converters(converters=converters),
    )


async def test_perform_async_swaps_raw_item_when_converters_change_audio(vad_target):
    """When converters change the audio, the attack must delete the raw item + insert converted."""
    bump = _make_audio_converter(lambda pcm: bytes((b + 1) & 0xFF for b in pcm))
    attack = BargeInAttack(objective_target=vad_target, attack_converter_config=_converter_config([bump]))
    connection = _mock_connection()
    vad_target.connect_async = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock()
    vad_target.delete_conversation_item_async = AsyncMock()
    vad_target.insert_user_audio_async = AsyncMock()

    captured: dict[str, Any] = {}

    async def fake_subscribe(*, connection, on_user_audio_committed):
        captured["on_committed"] = on_user_audio_committed
        return AsyncMock()

    vad_target.subscribe_events_async = AsyncMock(side_effect=fake_subscribe)

    result_future: asyncio.Future[RealtimeTargetResult] = asyncio.get_event_loop().create_future()
    result_future.set_result(RealtimeTargetResult(audio_bytes=b"\xaa" * 96, transcripts=["ok"]))
    vad_target.request_response_async = AsyncMock(return_value=result_future)

    raw_chunk = b"\x05" * 96  # PCM16 sample-aligned

    async def chunks_then_commit() -> AsyncIterator[bytes]:
        yield raw_chunk
        await asyncio.create_task(captured["on_committed"](_CommittedEvent(item_id="raw_99")))

    ctx = BargeInAttackContext(
        params=AttackParameters(objective="obj"),
        audio_chunks=chunks_then_commit(),
    )

    with patch.object(attack, "_MAX_POST_STREAM_WAIT_SECONDS", 0):
        result = await attack._perform_async(context=ctx)

    vad_target.delete_conversation_item_async.assert_awaited_once_with(connection=connection, item_id="raw_99")
    vad_target.insert_user_audio_async.assert_awaited_once()
    inserted_pcm = vad_target.insert_user_audio_async.call_args.kwargs["pcm_bytes"]
    assert inserted_pcm == bytes((b + 1) & 0xFF for b in raw_chunk)
    vad_target.request_response_async.assert_awaited_once()
    assert result.executed_turns == 1


async def test_perform_async_skips_swap_when_no_converters(vad_target):
    """Empty converter list: don't delete raw, don't insert converted, just request response."""
    attack = BargeInAttack(objective_target=vad_target)  # no converter config
    connection = _mock_connection()
    vad_target.connect_async = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock()
    vad_target.delete_conversation_item_async = AsyncMock()
    vad_target.insert_user_audio_async = AsyncMock()

    captured: dict[str, Any] = {}

    async def fake_subscribe(*, connection, on_user_audio_committed):
        captured["on_committed"] = on_user_audio_committed
        return AsyncMock()

    vad_target.subscribe_events_async = AsyncMock(side_effect=fake_subscribe)
    result_future: asyncio.Future[RealtimeTargetResult] = asyncio.get_event_loop().create_future()
    result_future.set_result(RealtimeTargetResult(audio_bytes=b"", transcripts=[]))
    vad_target.request_response_async = AsyncMock(return_value=result_future)

    async def chunks_then_commit() -> AsyncIterator[bytes]:
        yield b"\x00" * 96
        await asyncio.create_task(captured["on_committed"](_CommittedEvent(item_id="raw_42")))

    ctx = BargeInAttackContext(
        params=AttackParameters(objective="obj"),
        audio_chunks=chunks_then_commit(),
    )

    with patch.object(attack, "_MAX_POST_STREAM_WAIT_SECONDS", 0):
        result = await attack._perform_async(context=ctx)

    vad_target.delete_conversation_item_async.assert_not_called()
    vad_target.insert_user_audio_async.assert_not_called()
    vad_target.request_response_async.assert_awaited_once()
    assert result.executed_turns == 1


async def test_perform_async_clears_raw_buffer_between_commits(vad_target):
    """A commit must snapshot+reset the raw buffer so the next turn doesn't see prior audio."""
    bump = _make_audio_converter(lambda pcm: bytes((b + 1) & 0xFF for b in pcm))
    attack = BargeInAttack(objective_target=vad_target, attack_converter_config=_converter_config([bump]))
    connection = _mock_connection()
    vad_target.connect_async = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock()
    vad_target.delete_conversation_item_async = AsyncMock()
    vad_target.insert_user_audio_async = AsyncMock()

    captured: dict[str, Any] = {}

    async def fake_subscribe(*, connection, on_user_audio_committed):
        captured["on_committed"] = on_user_audio_committed
        return AsyncMock()

    vad_target.subscribe_events_async = AsyncMock(side_effect=fake_subscribe)

    def _future_with(result: RealtimeTargetResult) -> asyncio.Future[RealtimeTargetResult]:
        fut: asyncio.Future[RealtimeTargetResult] = asyncio.get_event_loop().create_future()
        fut.set_result(result)
        return fut

    vad_target.request_response_async = AsyncMock(
        side_effect=lambda **_: _future_with(RealtimeTargetResult(audio_bytes=b"", transcripts=[]))
    )

    async def chunks_then_two_commits() -> AsyncIterator[bytes]:
        yield b"\x01" * 96
        await asyncio.create_task(captured["on_committed"](_CommittedEvent(item_id="raw_1")))
        yield b"\x02" * 96
        await asyncio.create_task(captured["on_committed"](_CommittedEvent(item_id="raw_2")))

    ctx = BargeInAttackContext(
        params=AttackParameters(objective="obj"),
        audio_chunks=chunks_then_two_commits(),
    )

    with patch.object(attack, "_MAX_POST_STREAM_WAIT_SECONDS", 0):
        await attack._perform_async(context=ctx)

    insert_calls = vad_target.insert_user_audio_async.await_args_list
    assert len(insert_calls) == 2
    assert insert_calls[0].kwargs["pcm_bytes"] == bytes((b + 1) & 0xFF for b in (b"\x01" * 96))
    assert insert_calls[1].kwargs["pcm_bytes"] == bytes((b + 1) & 0xFF for b in (b"\x02" * 96))


async def test_perform_async_uses_injected_normalizer(vad_target):
    """The attack must delegate audio conversion to its injected PromptNormalizer."""
    fake_normalizer = MagicMock(spec=PromptNormalizer)
    fake_normalizer.convert_audio_async = AsyncMock(return_value=(b"\xff" * 96, []))
    attack = BargeInAttack(
        objective_target=vad_target,
        attack_converter_config=_converter_config([_make_audio_converter(lambda pcm: pcm)]),
        prompt_normalizer=fake_normalizer,
    )
    connection = _mock_connection()
    vad_target.connect_async = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock()
    vad_target.delete_conversation_item_async = AsyncMock()
    vad_target.insert_user_audio_async = AsyncMock()

    captured: dict[str, Any] = {}

    async def fake_subscribe(*, connection, on_user_audio_committed):
        captured["on_committed"] = on_user_audio_committed
        return AsyncMock()

    vad_target.subscribe_events_async = AsyncMock(side_effect=fake_subscribe)
    fut: asyncio.Future[RealtimeTargetResult] = asyncio.get_event_loop().create_future()
    fut.set_result(RealtimeTargetResult(audio_bytes=b"", transcripts=[]))
    vad_target.request_response_async = AsyncMock(return_value=fut)

    raw = b"\x05" * 96

    async def chunks_then_commit() -> AsyncIterator[bytes]:
        yield raw
        await asyncio.create_task(captured["on_committed"](_CommittedEvent(item_id="raw_z")))

    ctx = BargeInAttackContext(
        params=AttackParameters(objective="obj"),
        audio_chunks=chunks_then_commit(),
    )

    with patch.object(attack, "_MAX_POST_STREAM_WAIT_SECONDS", 0):
        await attack._perform_async(context=ctx)

    fake_normalizer.convert_audio_async.assert_awaited_once()
    kwargs = fake_normalizer.convert_audio_async.call_args.kwargs
    assert kwargs["pcm_bytes"] == raw
    assert kwargs["sample_rate"] == 24000
    # Converted audio (returned by mock) should reach insert_user_audio_async.
    vad_target.insert_user_audio_async.assert_awaited_once()
    assert vad_target.insert_user_audio_async.call_args.kwargs["pcm_bytes"] == b"\xff" * 96


# Placeholder for R4c tests


# ---- Per-turn persistence to CentralMemory (R4c) --------------------------------------------


async def _drive_one_audio_turn(
    attack,
    vad_target,
    *,
    raw_chunk: bytes,
    item_id: str,
    turn_result: RealtimeTargetResult,
):
    """Helper that runs a single audio-driven turn end-to-end against a mocked target."""
    connection = _mock_connection()
    vad_target.connect_async = AsyncMock(return_value=connection)
    vad_target.send_streaming_session_config_async = AsyncMock()
    vad_target.push_audio_chunk_async = AsyncMock()
    vad_target.delete_conversation_item_async = AsyncMock()
    vad_target.insert_user_audio_async = AsyncMock()

    captured: dict[str, Any] = {}

    async def fake_subscribe(*, connection, on_user_audio_committed):
        captured["on_committed"] = on_user_audio_committed
        return AsyncMock()

    vad_target.subscribe_events_async = AsyncMock(side_effect=fake_subscribe)
    fut: asyncio.Future[RealtimeTargetResult] = asyncio.get_event_loop().create_future()
    fut.set_result(turn_result)
    vad_target.request_response_async = AsyncMock(return_value=fut)

    async def chunks_then_commit() -> AsyncIterator[bytes]:
        yield raw_chunk
        await asyncio.create_task(captured["on_committed"](_CommittedEvent(item_id=item_id)))

    ctx = BargeInAttackContext(
        params=AttackParameters(objective="obj"),
        audio_chunks=chunks_then_commit(),
    )
    with patch.object(attack, "_MAX_POST_STREAM_WAIT_SECONDS", 0):
        return await attack._perform_async(context=ctx)


async def test_persists_user_and_assistant_messages_per_turn(vad_target):
    """A successful turn writes 1 user piece + 2 assistant pieces sharing the conversation id."""
    attack = BargeInAttack(objective_target=vad_target)
    add_calls: list[Any] = []
    mock_memory = MagicMock()
    mock_memory.add_message_to_memory = MagicMock(side_effect=lambda **kw: add_calls.append(kw["request"]))

    with patch("pyrit.executor.attack.streaming.barge_in.CentralMemory") as mock_cm:
        mock_cm.get_memory_instance.return_value = mock_memory
        result = await _drive_one_audio_turn(
            attack,
            vad_target,
            raw_chunk=b"\x00" * 96,
            item_id="raw_1",
            turn_result=RealtimeTargetResult(audio_bytes=b"\xaa" * 96, transcripts=["hello"]),
        )

    assert len(add_calls) == 2
    user_msg, assistant_msg = add_calls
    assert len(user_msg.message_pieces) == 1
    assert user_msg.message_pieces[0].converted_value_data_type == "audio_path"
    assert user_msg.message_pieces[0].conversation_id == result.conversation_id
    assert len(assistant_msg.message_pieces) == 2
    piece_types = sorted(p.converted_value_data_type for p in assistant_msg.message_pieces)
    assert piece_types == ["audio_path", "text"]
    text_piece = next(p for p in assistant_msg.message_pieces if p.converted_value_data_type == "text")
    assert text_piece.converted_value == "hello"


async def test_persists_interrupted_metadata_on_assistant_pieces(vad_target):
    """Interrupted turns mark both assistant pieces with prompt_metadata['interrupted'] = True."""
    attack = BargeInAttack(objective_target=vad_target)
    add_calls: list[Any] = []
    mock_memory = MagicMock()
    mock_memory.add_message_to_memory = MagicMock(side_effect=lambda **kw: add_calls.append(kw["request"]))

    with patch("pyrit.executor.attack.streaming.barge_in.CentralMemory") as mock_cm:
        mock_cm.get_memory_instance.return_value = mock_memory
        await _drive_one_audio_turn(
            attack,
            vad_target,
            raw_chunk=b"\x00" * 96,
            item_id="raw_int",
            turn_result=RealtimeTargetResult(audio_bytes=b"\xbb" * 96, transcripts=["partial"], interrupted=True),
        )

    assistant_msg = add_calls[1]
    for piece in assistant_msg.message_pieces:
        assert piece.prompt_metadata.get("interrupted") is True


async def test_persists_converter_identifiers_on_user_piece(vad_target):
    """Converter identifiers reported by convert_audio_async must land on the user piece."""
    bump = _make_audio_converter(
        lambda pcm: bytes((b + 1) & 0xFF for b in pcm),
        identifier_name="BumpConverter",
    )
    attack = BargeInAttack(
        objective_target=vad_target,
        attack_converter_config=AttackConverterConfig(
            request_converters=PromptConverterConfiguration.from_converters(converters=[bump]),
        ),
    )
    add_calls: list[Any] = []
    mock_memory = MagicMock()
    mock_memory.add_message_to_memory = MagicMock(side_effect=lambda **kw: add_calls.append(kw["request"]))

    with patch("pyrit.executor.attack.streaming.barge_in.CentralMemory") as mock_cm:
        mock_cm.get_memory_instance.return_value = mock_memory
        await _drive_one_audio_turn(
            attack,
            vad_target,
            raw_chunk=b"\x05" * 96,
            item_id="raw_c",
            turn_result=RealtimeTargetResult(audio_bytes=b"", transcripts=[]),
        )

    user_msg = add_calls[0]
    identifiers = user_msg.message_pieces[0].converter_identifiers
    assert len(identifiers) == 1
    assert identifiers[0].class_name == "BumpConverter"


async def test_persists_converted_audio_when_converters_changed_bytes(vad_target):
    """The user piece's audio_path must point at the converted PCM, not the raw snapshot."""
    bump = _make_audio_converter(lambda pcm: bytes((b + 1) & 0xFF for b in pcm))
    attack = BargeInAttack(
        objective_target=vad_target,
        attack_converter_config=AttackConverterConfig(
            request_converters=PromptConverterConfiguration.from_converters(converters=[bump]),
        ),
    )
    saved_calls: list[bytes] = []

    async def fake_save_audio(audio_bytes, **_):
        saved_calls.append(audio_bytes)
        return f"/tmp/audio_{len(saved_calls)}.wav"

    vad_target.save_audio = AsyncMock(side_effect=fake_save_audio)
    mock_memory = MagicMock()
    mock_memory.add_message_to_memory = MagicMock()

    raw = b"\x05" * 96
    with patch("pyrit.executor.attack.streaming.barge_in.CentralMemory") as mock_cm:
        mock_cm.get_memory_instance.return_value = mock_memory
        await _drive_one_audio_turn(
            attack,
            vad_target,
            raw_chunk=raw,
            item_id="raw_x",
            turn_result=RealtimeTargetResult(audio_bytes=b"\xff" * 96, transcripts=[]),
        )

    # save_audio called twice per turn: first for user audio (must be CONVERTED), then assistant audio.
    assert len(saved_calls) == 2
    assert saved_calls[0] == bytes((b + 1) & 0xFF for b in raw)
    assert saved_calls[1] == b"\xff" * 96


async def test_attack_result_last_response_is_final_assistant_text_piece(vad_target):
    """AttackResult.last_response must point at the last assistant message's first piece (text)."""
    attack = BargeInAttack(objective_target=vad_target)
    mock_memory = MagicMock()
    mock_memory.add_message_to_memory = MagicMock()

    with patch("pyrit.executor.attack.streaming.barge_in.CentralMemory") as mock_cm:
        mock_cm.get_memory_instance.return_value = mock_memory
        result = await _drive_one_audio_turn(
            attack,
            vad_target,
            raw_chunk=b"\x00" * 96,
            item_id="raw_lr",
            turn_result=RealtimeTargetResult(audio_bytes=b"\xaa" * 96, transcripts=["final answer"]),
        )

    assert result.last_response is not None
    assert result.last_response.converted_value_data_type == "text"
    assert result.last_response.converted_value == "final answer"
