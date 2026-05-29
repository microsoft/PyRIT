# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for ``BargeInAttack`` and supporting helpers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import BargeInAttack, BargeInAttackContext
from pyrit.executor.attack.core import AttackConverterConfig, AttackParameters
from pyrit.executor.attack.streaming.barge_in import _BargeInRunState, _trim_snapshot_to_speech
from pyrit.models import AttackOutcome, Message, MessagePiece
from pyrit.prompt_normalizer import PromptConverterConfiguration
from pyrit.prompt_target import RealtimeTarget
from pyrit.prompt_target.common.realtime_audio import REALTIME_COMMITTED_ITEM_ID_KEY, CommittedEvent

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


def test_constructor_default_max_post_stream_wait_seconds(vad_target):
    """When not passed, max_post_stream_wait_seconds takes the class default."""
    attack = BargeInAttack(objective_target=vad_target)
    assert attack._max_post_stream_wait_seconds == BargeInAttack.DEFAULT_MAX_POST_STREAM_WAIT_SECONDS


def test_constructor_accepts_custom_max_post_stream_wait_seconds(vad_target):
    """max_post_stream_wait_seconds is configurable per-instance."""
    attack = BargeInAttack(objective_target=vad_target, max_post_stream_wait_seconds=120.0)
    assert attack._max_post_stream_wait_seconds == 120.0


def test_constructor_caches_streaming_handle(vad_target):
    """BargeInAttack stashes the target's streaming handle for direct access during _setup_async."""
    attack = BargeInAttack(objective_target=vad_target)
    assert attack._streaming is vad_target.streaming


def test_constructor_rejects_target_without_streaming_handle(vad_target):
    """A target that doesn't satisfy SupportsStreamingBargeIn (no streaming attr) fails fast."""
    # Simulate a malformed target: capability flag still set, but streaming attribute removed.
    del vad_target.streaming
    with pytest.raises(TypeError, match="does not satisfy SupportsStreamingBargeIn"):
        BargeInAttack(objective_target=vad_target)


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


# ---- _setup_async + prepended_conversation persistence ---------------------------------------


async def test_setup_async_persists_prepended_conversation_to_memory(vad_target):
    """Prepended_conversation messages must be written to memory on setup like other attacks do."""
    attack = BargeInAttack(objective_target=vad_target)
    sys_msg = Message(
        message_pieces=[
            MessagePiece(
                role="system",
                original_value="You are a strict assistant.",
                original_value_data_type="text",
                converted_value="You are a strict assistant.",
                converted_value_data_type="text",
                conversation_id="ignored-by-setup",
            )
        ]
    )
    user_msg = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="prior user turn",
                original_value_data_type="text",
                converted_value="prior user turn",
                converted_value_data_type="text",
                conversation_id="ignored-by-setup",
            )
        ]
    )
    assistant_msg = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="prior assistant turn",
                original_value_data_type="text",
                converted_value="prior assistant turn",
                converted_value_data_type="text",
                conversation_id="ignored-by-setup",
            )
        ]
    )

    ctx = BargeInAttackContext(
        params=AttackParameters(
            objective="o",
            prepended_conversation=[sys_msg, user_msg, assistant_msg],
        ),
        audio_chunks=_aiter([b"\x00" * 96]),
    )

    add_calls: list[Any] = []
    with patch.object(attack._conversation_manager._memory, "add_message_to_memory") as mock_add:
        mock_add.side_effect = lambda **kw: add_calls.append(kw["request"])
        await attack._setup_async(context=ctx)

    # All three prepended messages should have been written to memory under the
    # attack's conversation_id; assistant role becomes simulated_assistant on storage.
    assert len(add_calls) == 3
    storage_roles = [m.message_pieces[0].get_role_for_storage() for m in add_calls]
    assert storage_roles == ["system", "user", "simulated_assistant"]
    # All three messages share the context's conversation_id post-setup.
    for m in add_calls:
        assert m.message_pieces[0].conversation_id == ctx.conversation_id


async def test_setup_async_no_op_when_prepended_conversation_empty(vad_target):
    """Empty prepended_conversation: no memory writes, no crash."""
    attack = BargeInAttack(objective_target=vad_target)
    ctx = BargeInAttackContext(
        params=AttackParameters(objective="o"),  # no prepended_conversation
        audio_chunks=_aiter([b"\x00" * 96]),
    )

    add_calls: list[Any] = []
    with patch.object(attack._conversation_manager._memory, "add_message_to_memory") as mock_add:
        mock_add.side_effect = lambda **kw: add_calls.append(kw["request"])
        await attack._setup_async(context=ctx)

    assert add_calls == []


# ---- Streaming loop end-to-end ---------------------------------------------------------------


def _setup_streaming_target(vad_target, *, future_response: Message | None = None) -> AsyncMock:
    """
    Mock the streaming-mode surface on ``vad_target`` and return the connection mock.

    Stubs ``connect_async``, ``send_streaming_session_config_async``, ``push_audio_chunk_async``,
    ``subscribe_events_async``, ``save_audio``, and ``cleanup_conversation`` so a callback can
    be invoked mid-stream without exercising the real target machinery.
    """
    connection = _mock_connection()
    vad_target.streaming.connect_async = AsyncMock(return_value=connection)
    vad_target.streaming.send_streaming_session_config_async = AsyncMock()
    vad_target.streaming.push_audio_chunk_async = AsyncMock()
    vad_target.streaming.save_audio = AsyncMock(return_value="/tmp/snapshot.wav")
    vad_target.streaming.cleanup_conversation = AsyncMock()
    return connection


def _capture_committed_callback(vad_target, captured: dict[str, Any]) -> None:
    """Wire ``subscribe_events_async`` to capture the registered ``on_user_audio_committed``."""

    async def fake_subscribe(*, connection, conversation_id, on_user_audio_committed):
        captured["on_committed"] = on_user_audio_committed
        return AsyncMock()

    vad_target.streaming.subscribe_events_async = AsyncMock(side_effect=fake_subscribe)


def _stub_send_prompt(attack: BargeInAttack, return_value: Message | None = None) -> AsyncMock:
    """Replace the attack's prompt_normalizer.send_prompt_async with an AsyncMock and return it."""
    if return_value is None:
        return_value = Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="ok",
                    original_value_data_type="text",
                    converted_value="ok",
                    converted_value_data_type="text",
                    conversation_id="any",
                )
            ]
        )
    send_mock = AsyncMock(return_value=return_value)
    attack._prompt_normalizer.send_prompt_async = send_mock
    return send_mock


async def test_perform_async_streams_chunks_and_tears_down(vad_target):
    """Happy path: connect, send config, subscribe, push chunks, then cleanup_conversation — no commits."""
    attack = BargeInAttack(objective_target=vad_target)
    connection = _setup_streaming_target(vad_target)
    dispatcher = AsyncMock()
    vad_target.streaming.subscribe_events_async = AsyncMock(return_value=dispatcher)

    chunks = [b"\x11" * 480, b"\x22" * 480, b"\x33" * 240]
    ctx = _attack_context(audio_chunks=_aiter(chunks))

    with patch.object(attack, "_max_post_stream_wait_seconds", 0):
        result = await attack._perform_async(context=ctx)

    vad_target.streaming.connect_async.assert_awaited_once_with(conversation_id=ctx.conversation_id)
    vad_target.streaming.send_streaming_session_config_async.assert_awaited_once()
    vad_target.streaming.subscribe_events_async.assert_awaited_once()
    assert vad_target.streaming.push_audio_chunk_async.await_count == len(chunks)
    pushed = [call.kwargs["pcm_bytes"] for call in vad_target.streaming.push_audio_chunk_async.await_args_list]
    assert pushed == chunks
    vad_target.streaming.cleanup_conversation.assert_awaited_once_with(ctx.conversation_id)
    assert result.executed_turns == 0
    assert result.outcome == AttackOutcome.UNDETERMINED


async def test_perform_async_calls_send_prompt_async_on_commit(vad_target):
    """A commit must invoke prompt_normalizer.send_prompt_async with an audio_path Message."""
    bump = MagicMock()
    bump.get_identifier = MagicMock(return_value=MagicMock())
    converter_config = AttackConverterConfig(
        request_converters=PromptConverterConfiguration.from_converters(converters=[bump]),
    )
    attack = BargeInAttack(objective_target=vad_target, attack_converter_config=converter_config)
    send_mock = _stub_send_prompt(attack)
    _setup_streaming_target(vad_target)
    captured: dict[str, Any] = {}
    _capture_committed_callback(vad_target, captured)

    async def chunks_then_commit() -> AsyncIterator[bytes]:
        yield b"\x05" * 480
        await asyncio.create_task(captured["on_committed"](CommittedEvent(item_id="item_42")))

    ctx = _attack_context(audio_chunks=chunks_then_commit())

    with patch.object(attack, "_max_post_stream_wait_seconds", 0):
        result = await attack._perform_async(context=ctx)

    send_mock.assert_awaited_once()
    kwargs = send_mock.call_args.kwargs
    sent_message = kwargs["message"]
    assert sent_message.message_pieces[0].converted_value_data_type == "audio_path"
    assert sent_message.message_pieces[0].conversation_id == ctx.conversation_id
    assert sent_message.message_pieces[0].prompt_metadata[REALTIME_COMMITTED_ITEM_ID_KEY] == "item_42"
    assert kwargs["target"] is vad_target
    assert kwargs["request_converter_configurations"] == attack._request_converters
    assert kwargs["conversation_id"] == ctx.conversation_id
    assert result.executed_turns == 1


async def test_perform_async_message_carries_snapshot_audio_path(vad_target):
    """The audio_path on the user piece must point at the persisted snapshot WAV."""
    attack = BargeInAttack(objective_target=vad_target)
    send_mock = _stub_send_prompt(attack)
    connection = _setup_streaming_target(vad_target)
    vad_target.streaming.save_audio = AsyncMock(return_value="/tmp/persisted_snapshot.wav")
    captured: dict[str, Any] = {}
    _capture_committed_callback(vad_target, captured)

    raw_chunk = b"\x07" * 96

    async def chunks_then_commit() -> AsyncIterator[bytes]:
        yield raw_chunk
        await asyncio.create_task(captured["on_committed"](CommittedEvent(item_id="i")))

    ctx = _attack_context(audio_chunks=chunks_then_commit())

    with patch.object(attack, "_max_post_stream_wait_seconds", 0):
        await attack._perform_async(context=ctx)

    # save_audio called with the snapshot PCM; the resulting path lands on the message piece.
    save_kwargs_or_args = vad_target.streaming.save_audio.call_args
    saved_pcm = (
        save_kwargs_or_args.args[0] if save_kwargs_or_args.args else save_kwargs_or_args.kwargs.get("audio_bytes")
    )
    assert saved_pcm == raw_chunk
    piece = send_mock.call_args.kwargs["message"].message_pieces[0]
    assert piece.original_value == "/tmp/persisted_snapshot.wav"
    assert piece.converted_value == "/tmp/persisted_snapshot.wav"


async def test_perform_async_clears_raw_buffer_between_commits(vad_target):
    """Each commit gets fresh PCM: the snapshot saved for turn 2 has no carryover from turn 1."""
    attack = BargeInAttack(objective_target=vad_target)
    _stub_send_prompt(attack)
    _setup_streaming_target(vad_target)
    saved_pcm: list[bytes] = []

    async def fake_save_audio(audio_bytes, **_):
        saved_pcm.append(audio_bytes)
        return f"/tmp/snap_{len(saved_pcm)}.wav"

    vad_target.streaming.save_audio = AsyncMock(side_effect=fake_save_audio)
    captured: dict[str, Any] = {}
    _capture_committed_callback(vad_target, captured)

    async def chunks_two_commits() -> AsyncIterator[bytes]:
        yield b"\x01" * 96
        await asyncio.create_task(captured["on_committed"](CommittedEvent(item_id="i1")))
        yield b"\x02" * 96
        await asyncio.create_task(captured["on_committed"](CommittedEvent(item_id="i2")))

    ctx = _attack_context(audio_chunks=chunks_two_commits())

    with patch.object(attack, "_max_post_stream_wait_seconds", 0):
        await attack._perform_async(context=ctx)

    assert saved_pcm == [b"\x01" * 96, b"\x02" * 96]


async def test_perform_async_tracks_last_response_and_turn_count(vad_target):
    """AttackResult.last_response is the last Message from send_prompt_async; count matches commits."""
    attack = BargeInAttack(objective_target=vad_target)
    responses_in_order = [
        Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value=text,
                    original_value_data_type="text",
                    converted_value=text,
                    converted_value_data_type="text",
                    conversation_id="x",
                )
            ]
        )
        for text in ("first", "second", "final")
    ]
    send_mock = AsyncMock(side_effect=responses_in_order)
    attack._prompt_normalizer.send_prompt_async = send_mock
    _setup_streaming_target(vad_target)
    captured: dict[str, Any] = {}
    _capture_committed_callback(vad_target, captured)

    async def chunks_three_commits() -> AsyncIterator[bytes]:
        for i in range(3):
            yield bytes([i + 1]) * 96
            await asyncio.create_task(captured["on_committed"](CommittedEvent(item_id=f"i{i}")))

    ctx = _attack_context(audio_chunks=chunks_three_commits())

    with patch.object(attack, "_max_post_stream_wait_seconds", 0):
        result = await attack._perform_async(context=ctx)

    assert result.executed_turns == 3
    assert result.last_response is not None
    assert result.last_response.converted_value == "final"


async def test_perform_async_cleans_up_even_on_exception(vad_target):
    """If the chunk loop raises, cleanup_conversation still fires."""
    attack = BargeInAttack(objective_target=vad_target)
    _setup_streaming_target(vad_target)
    vad_target.streaming.push_audio_chunk_async = AsyncMock(side_effect=RuntimeError("push exploded"))
    vad_target.streaming.subscribe_events_async = AsyncMock(return_value=AsyncMock())

    ctx = _attack_context(audio_chunks=_aiter([b"\x00" * 96]))

    with pytest.raises(RuntimeError, match="push exploded"):
        with patch.object(attack, "_max_post_stream_wait_seconds", 0):
            await attack._perform_async(context=ctx)

    vad_target.streaming.cleanup_conversation.assert_awaited_once_with(ctx.conversation_id)


async def test_perform_async_swallows_callback_exception(vad_target):
    """If send_prompt_async raises mid-turn, the session keeps going (no executed turn)."""
    attack = BargeInAttack(objective_target=vad_target)
    attack._prompt_normalizer.send_prompt_async = AsyncMock(side_effect=RuntimeError("converter blew up"))
    _setup_streaming_target(vad_target)
    captured: dict[str, Any] = {}
    _capture_committed_callback(vad_target, captured)

    async def chunks_then_commit() -> AsyncIterator[bytes]:
        yield b"\x00" * 96
        await asyncio.create_task(captured["on_committed"](CommittedEvent(item_id="i")))

    ctx = _attack_context(audio_chunks=chunks_then_commit())

    with patch.object(attack, "_max_post_stream_wait_seconds", 0):
        result = await attack._perform_async(context=ctx)

    # The callback caught the exception; no turn counted as successful.
    assert result.executed_turns == 0


# ---- send_streaming_session_config_async (target-side helper added in R4a) -------------------


async def test_send_streaming_session_config_async_emits_create_response_false(vad_target):
    """The streaming session config must flip create_response to False on turn_detection."""
    connection = _mock_connection()
    await vad_target.streaming.send_streaming_session_config_async(connection=connection)
    connection.session.update.assert_awaited_once()
    config = connection.session.update.call_args.kwargs["session"]
    assert config["audio"]["input"]["turn_detection"]["create_response"] is False


@patch.dict("os.environ", _CLEAN_ENV)
async def test_send_streaming_session_config_async_requires_server_vad(sqlite_instance):
    """Without server VAD, sending streaming session config must raise."""
    no_vad = RealtimeTarget(api_key="k", endpoint="wss://test_url", model_name="test")
    connection = _mock_connection()
    with pytest.raises(ValueError, match="server VAD"):
        await no_vad.streaming.send_streaming_session_config_async(connection=connection)


async def test_send_streaming_session_config_async_uses_system_message_from_conversation(vad_target):
    """If the prepended conversation begins with a system message, it becomes session instructions."""
    connection = _mock_connection()
    system_msg = Message(
        message_pieces=[
            MessagePiece(
                role="system",
                original_value="You are a strict assistant.",
                original_value_data_type="text",
                converted_value="You are a strict assistant.",
                converted_value_data_type="text",
                conversation_id="x",
            )
        ]
    )
    await vad_target.streaming.send_streaming_session_config_async(connection=connection, conversation=[system_msg])
    config = connection.session.update.call_args.kwargs["session"]
    assert config["instructions"] == "You are a strict assistant."


# ---- _trim_snapshot_to_speech (pre-speech silence trim) -------------------------------------


def test_trim_drops_leading_silence_using_audio_start_ms():
    """When audio_start_ms is set, everything before (audio_start_ms - prefix_padding_ms) is trimmed."""
    # 24 kHz mono PCM16 → 48 bytes per ms. 1000 ms of silence + 100 ms of "speech".
    silence = b"\x00" * (1000 * 48)
    speech = b"\x11" * (100 * 48)
    buffer = silence + speech

    trimmed = _trim_snapshot_to_speech(
        raw_buffer=buffer,
        sample_rate_hz=24000,
        audio_start_ms=1000,  # speech starts at 1000 ms
        prefix_padding_ms=200,  # keep 200 ms before speech
    )

    # Expect: dropped 800 ms (1000 - 200) of silence; kept 200 ms silence + 100 ms speech.
    assert len(trimmed) == (200 + 100) * 48
    assert trimmed[-len(speech) :] == speech


def test_trim_passes_through_when_audio_start_ms_missing():
    """If the server didn't report audio_start_ms, no trim happens."""
    buffer = b"\xff" * 480
    assert (
        _trim_snapshot_to_speech(
            raw_buffer=buffer,
            sample_rate_hz=24000,
            audio_start_ms=None,
            prefix_padding_ms=300,
        )
        is buffer
    )


def test_trim_passes_through_when_audio_start_ms_zero():
    """audio_start_ms == 0 means speech started immediately; no trim."""
    buffer = b"\xff" * 480
    assert (
        _trim_snapshot_to_speech(
            raw_buffer=buffer,
            sample_rate_hz=24000,
            audio_start_ms=0,
            prefix_padding_ms=300,
        )
        is buffer
    )


def test_trim_raises_on_negative_audio_start_ms():
    """A negative audio_start_ms is a server contract violation, not 'unknown'."""
    buffer = b"\xff" * 480
    with pytest.raises(ValueError, match="audio_start_ms must be >= 0"):
        _trim_snapshot_to_speech(
            raw_buffer=buffer,
            sample_rate_hz=24000,
            audio_start_ms=-100,
            prefix_padding_ms=300,
        )


def test_trim_clamps_when_audio_start_ms_less_than_prefix_padding():
    """audio_start_ms - prefix_padding_ms shouldn't go negative."""
    buffer = b"\xab" * (500 * 48)
    trimmed = _trim_snapshot_to_speech(
        raw_buffer=buffer,
        sample_rate_hz=24000,
        audio_start_ms=100,
        prefix_padding_ms=300,
    )
    # max(0, 100 - 300) = 0 → no bytes dropped.
    assert trimmed == buffer


def test_trim_aligns_to_sample_boundary():
    """Trim must land on a sample-frame boundary (2 bytes for PCM16 mono) so playback isn't garbled."""
    # Sample rate 8000 Hz → 16 bytes/ms; audio_start_ms=3, prefix=0 → start_byte=48 (aligned).
    buffer = bytes(range(256)) * 4  # arbitrary bytes
    trimmed = _trim_snapshot_to_speech(
        raw_buffer=buffer,
        sample_rate_hz=8000,
        audio_start_ms=3,
        prefix_padding_ms=0,
        sample_width_bytes=2,
        channels=1,
    )
    # 48 bytes is already a frame boundary (48 % 2 == 0).
    assert len(trimmed) == len(buffer) - 48
    # Sanity: the trim point is sample-aligned.
    assert (len(buffer) - len(trimmed)) % 2 == 0


def test_trim_passes_through_when_computed_start_exceeds_buffer():
    """Safety: if audio_start_ms points past the buffer, return the buffer unchanged."""
    buffer = b"\x00" * 480  # 10 ms at 24 kHz
    trimmed = _trim_snapshot_to_speech(
        raw_buffer=buffer,
        sample_rate_hz=24000,
        audio_start_ms=10_000,
        prefix_padding_ms=0,
    )
    assert trimmed is buffer


async def test_perform_async_trims_first_turn_using_audio_start_ms(vad_target):
    """Turn 1: buffer_start_session_ms=0, so audio_start_ms is already buffer-relative."""
    from pyrit.prompt_target.common.realtime_audio import ServerVadConfig

    # Pin prefix_padding_ms to a known value so the expected byte count is unambiguous.
    vad_target._server_vad = ServerVadConfig(prefix_padding_ms=300, silence_duration_ms=500)

    attack = BargeInAttack(objective_target=vad_target)
    send_mock = _stub_send_prompt(attack)
    _setup_streaming_target(vad_target)
    saved_pcm: list[bytes] = []

    async def fake_save_audio(audio_bytes, **_):
        saved_pcm.append(audio_bytes)
        return "/tmp/snap.wav"

    vad_target.streaming.save_audio = AsyncMock(side_effect=fake_save_audio)
    captured: dict[str, Any] = {}
    _capture_committed_callback(vad_target, captured)

    # 1000 ms of leading silence + 100 ms speech-like payload at 24 kHz mono PCM16 → 48 bytes/ms.
    silence = b"\x00" * (1000 * 48)
    speech = b"\x11" * (100 * 48)

    async def chunks_then_commit() -> AsyncIterator[bytes]:
        yield silence + speech
        # Server says speech started at 1000 ms (session-relative); with prefix_padding_ms=300, drop 700 ms.
        await asyncio.create_task(
            captured["on_committed"](CommittedEvent(item_id="i", audio_start_ms=1000)),
        )

    ctx = _attack_context(audio_chunks=chunks_then_commit())
    with patch.object(attack, "_max_post_stream_wait_seconds", 0):
        await attack._perform_async(context=ctx)

    # Expect save_audio to receive the trimmed snapshot:
    # max(0, 1000 - 300) = 700 ms dropped; remaining = 300 ms silence + 100 ms speech = 400 ms.
    assert len(saved_pcm) == 1
    assert len(saved_pcm[0]) == 400 * 48
    assert saved_pcm[0].endswith(speech)
    send_mock.assert_awaited_once()


async def test_perform_async_trims_second_turn_with_session_relative_offset(vad_target):
    """Turn 2: audio_start_ms is session-relative; the attack converts it to buffer-relative.

    Without the conversion, a session-relative audio_start_ms larger than the local buffer
    would skip the trim (passthrough on out-of-range), letting silence reach the model.
    """
    from pyrit.prompt_target.common.realtime_audio import ServerVadConfig

    vad_target._server_vad = ServerVadConfig(prefix_padding_ms=300, silence_duration_ms=500)

    attack = BargeInAttack(objective_target=vad_target)
    _stub_send_prompt(attack)
    _setup_streaming_target(vad_target)
    saved_pcm: list[bytes] = []

    async def fake_save_audio(audio_bytes, **_):
        saved_pcm.append(audio_bytes)
        return "/tmp/snap.wav"

    vad_target.streaming.save_audio = AsyncMock(side_effect=fake_save_audio)
    captured: dict[str, Any] = {}
    _capture_committed_callback(vad_target, captured)

    silence_500 = b"\x00" * (500 * 48)  # 500 ms silence
    speech_short = b"\x11" * (100 * 48)  # 100 ms speech-like
    silence_2000 = b"\x00" * (2000 * 48)  # 2000 ms silence (between turns)
    speech_long = b"\x22" * (300 * 48)  # 300 ms speech-like (turn 2)

    async def two_turns() -> AsyncIterator[bytes]:
        # Turn 1: 500 ms silence + 100 ms speech; total local buffer = 600 ms.
        yield silence_500 + speech_short
        # Server VAD fires commit at session_ms ≈ 600 with audio_start_ms = 500 (session-relative).
        await asyncio.create_task(
            captured["on_committed"](CommittedEvent(item_id="i1", audio_start_ms=500)),
        )
        # Turn 2: 2000 ms silence (since turn 1's commit) + 300 ms speech.
        # session_ms_at_speech_start ≈ 600 + 2000 = 2600.
        yield silence_2000 + speech_long
        await asyncio.create_task(
            captured["on_committed"](CommittedEvent(item_id="i2", audio_start_ms=2600)),
        )

    ctx = _attack_context(audio_chunks=two_turns())
    with patch.object(attack, "_max_post_stream_wait_seconds", 0):
        await attack._perform_async(context=ctx)

    assert len(saved_pcm) == 2

    # Turn 1: buffer_relative_start = 500 - 0 = 500; trim = max(0, 500 - 300) = 200 ms;
    # remaining = 300 ms pre-speech-padding + 100 ms speech = 400 ms.
    assert len(saved_pcm[0]) == 400 * 48
    assert saved_pcm[0].endswith(speech_short)

    # Turn 2: buffer_start_session_ms advanced by 600 ms (turn 1's full buffer duration).
    # buffer_relative_start = 2600 - 600 = 2000; trim = max(0, 2000 - 300) = 1700 ms;
    # remaining = 300 ms pre-speech-padding + 300 ms speech = 600 ms.
    assert len(saved_pcm[1]) == 600 * 48
    assert saved_pcm[1].endswith(speech_long)


# ---- _snapshot_and_trim (helper unit tests) --------------------------------------------------


def test_snapshot_and_trim_returns_buffer_and_duration(vad_target):
    """Helper returns the (trimmed snapshot, original-duration) pair without mutating state."""
    from pyrit.prompt_target.common.realtime_audio import ServerVadConfig

    vad_target._server_vad = ServerVadConfig(prefix_padding_ms=300, silence_duration_ms=500)
    attack = BargeInAttack(objective_target=vad_target)

    state = _BargeInRunState()
    silence = b"\x00" * (1000 * 48)
    speech = b"\x11" * (100 * 48)
    state.raw_buffer.extend(silence + speech)
    pre_call_buffer_len = len(state.raw_buffer)

    event = CommittedEvent(item_id="i", audio_start_ms=1000)
    snapshot, duration_ms = attack._snapshot_and_trim(event=event, state=state)

    # Trimmed: drop max(0, 1000 - 300) = 700 ms; remaining = 300 ms pad + 100 ms speech.
    assert len(snapshot) == 400 * 48
    assert snapshot.endswith(speech)
    # Original duration spans the entire pre-trim buffer (1100 ms at 48 bytes/ms).
    assert duration_ms == 1100
    # State is NOT mutated — caller is responsible for clearing the buffer and advancing offset.
    assert len(state.raw_buffer) == pre_call_buffer_len
    assert state.buffer_start_session_ms == 0


def test_snapshot_and_trim_passes_through_when_audio_start_ms_none(vad_target):
    """When the bridged audio_start_ms is None, the helper returns the buffer unchanged."""
    attack = BargeInAttack(objective_target=vad_target)
    state = _BargeInRunState()
    raw = b"\x42" * (300 * 48)
    state.raw_buffer.extend(raw)

    event = CommittedEvent(item_id="i", audio_start_ms=None)
    snapshot, duration_ms = attack._snapshot_and_trim(event=event, state=state)

    assert snapshot == raw
    assert duration_ms == 300


def test_snapshot_and_trim_uses_session_relative_offset(vad_target):
    """The helper subtracts state.buffer_start_session_ms before passing to the trim function."""
    from pyrit.prompt_target.common.realtime_audio import ServerVadConfig

    vad_target._server_vad = ServerVadConfig(prefix_padding_ms=300, silence_duration_ms=500)
    attack = BargeInAttack(objective_target=vad_target)

    state = _BargeInRunState()
    state.buffer_start_session_ms = 1000  # turn 2: 1000 ms of prior turns
    silence = b"\x00" * (500 * 48)
    speech = b"\x22" * (200 * 48)
    state.raw_buffer.extend(silence + speech)

    # Server reports session-relative audio_start_ms = 1500 → buffer-relative = 500.
    event = CommittedEvent(item_id="i", audio_start_ms=1500)
    snapshot, _ = attack._snapshot_and_trim(event=event, state=state)

    # Trim = max(0, 500 - 300) = 200 ms; remaining = 300 ms pad + 200 ms speech.
    assert len(snapshot) == 500 * 48
    assert snapshot.endswith(speech)


# ---- _build_message_for_turn (helper unit tests) ---------------------------------------------


async def test_build_message_for_turn_persists_and_wraps(vad_target):
    """Builder calls save_audio and wraps the path in an audio_path-shaped Message."""
    attack = BargeInAttack(objective_target=vad_target)
    vad_target.streaming.save_audio = AsyncMock(return_value="/tmp/persisted.wav")

    snapshot_bytes = b"\xaa" * 480
    message = await attack._build_message_for_turn(
        snapshot=snapshot_bytes,
        item_id="server_item_xyz",
        conversation_id="conv-1",
    )

    # save_audio receives the snapshot bytes and the streaming-handle sample rate.
    save_call = vad_target.streaming.save_audio.await_args
    assert save_call.args[0] == snapshot_bytes
    assert save_call.kwargs["sample_rate"] == vad_target.streaming.SAMPLE_RATE_HZ

    # Message shape: one audio_path piece pointing at the persisted file.
    assert len(message.message_pieces) == 1
    piece = message.message_pieces[0]
    assert piece.original_value == "/tmp/persisted.wav"
    assert piece.converted_value == "/tmp/persisted.wav"
    assert piece.original_value_data_type == "audio_path"
    assert piece.converted_value_data_type == "audio_path"
    assert piece.conversation_id == "conv-1"


async def test_build_message_for_turn_stashes_item_id_in_metadata(vad_target):
    """The server's committed item_id is stashed under REALTIME_COMMITTED_ITEM_ID_KEY."""
    attack = BargeInAttack(objective_target=vad_target)
    vad_target.streaming.save_audio = AsyncMock(return_value="/tmp/persisted.wav")

    message = await attack._build_message_for_turn(
        snapshot=b"\x00" * 96,
        item_id="srv_item_42",
        conversation_id="conv-2",
    )

    assert message.message_pieces[0].prompt_metadata[REALTIME_COMMITTED_ITEM_ID_KEY] == "srv_item_42"


# ---- _send_via_normalizer (helper unit tests) ------------------------------------------------


async def test_send_via_normalizer_forwards_to_prompt_normalizer(vad_target):
    """Helper hands message + converters + identifiers to PromptNormalizer.send_prompt_async."""
    attack = BargeInAttack(objective_target=vad_target)
    response = Message(message_pieces=[MessagePiece(role="assistant", original_value="ok")])
    attack._prompt_normalizer.send_prompt_async = AsyncMock(return_value=response)  # type: ignore[method-assign]

    request = Message(message_pieces=[MessagePiece(role="user", original_value="/tmp/r.wav")])
    result = await attack._send_via_normalizer(message=request, conversation_id="conv-3")

    assert result is response
    call = attack._prompt_normalizer.send_prompt_async.await_args
    assert call.kwargs["message"] is request
    # Forwards the underlying PromptTarget (not the streaming handle).
    assert call.kwargs["target"] is vad_target
    assert call.kwargs["conversation_id"] == "conv-3"
    assert call.kwargs["request_converter_configurations"] is attack._request_converters
    assert call.kwargs["response_converter_configurations"] is attack._response_converters
    assert call.kwargs["attack_identifier"] == attack.get_identifier()
