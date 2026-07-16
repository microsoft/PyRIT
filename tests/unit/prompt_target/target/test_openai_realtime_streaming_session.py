# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the internal _OpenAIRealtimeStreamingSession lifecycle."""

import asyncio
import base64
import contextlib
import uuid
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.realtime_audio import (
    STREAMING_INTERRUPTED_KEY,
    CommittedEvent,
    RealtimeTargetResult,
    ServerVadConfig,
)
from pyrit.prompt_target.openai._openai_realtime_streaming_session import (
    _OpenAIRealtimeStreamingSession,
)


class _StubBadRequest(Exception):  # noqa: N818 - stand-in for openai.BadRequestError shape
    """Stand-in for openai.BadRequestError raised on empty-buffer forced commit."""


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------


def _paced_chunks(chunks: list[bytes], finish: asyncio.Event):
    """Yield each chunk, then block on ``finish`` so the producer can be gated by the test."""

    async def _gen():
        for chunk in chunks:
            yield chunk
        await finish.wait()

    return _gen()


def _build_target() -> MagicMock:
    """Build a MagicMock target exposing the connection + audio surface the session calls."""
    target = MagicMock(name="RealtimeTarget")
    target.SAMPLE_RATE_HZ = 24000

    connection = AsyncMock(name="connection")
    # AsyncMock auto-creates attributes as AsyncMock, but child attribute chains like
    # ``input_audio_buffer.commit`` need explicit construction so ``commit`` is awaitable
    # and we can attach a side_effect to make the forced final commit fail benignly.
    connection.input_audio_buffer = MagicMock()
    connection.input_audio_buffer.commit = AsyncMock(side_effect=_StubBadRequest("input_audio_buffer_commit_empty"))

    target._connect_async = AsyncMock(return_value=connection)
    target.save_audio_async = AsyncMock(side_effect=lambda pcm, **kw: f"/tmp/audio-{uuid.uuid4().hex[:8]}.wav")
    target.get_identifier = MagicMock(
        return_value={"__type__": "RealtimeTarget", "__module__": "test", "id": "test-id"}
    )
    return target


def _make_request_response_async(
    *,
    audio_bytes: bytes = b"\xaa" * 96,
    transcripts: tuple[str, ...] = ("hi",),
    interrupted: bool = False,
) -> AsyncMock:
    """AsyncMock for ``session._request_response_async`` returning a resolved Future."""

    async def _impl() -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        future.set_result(
            RealtimeTargetResult(
                audio_bytes=audio_bytes,
                transcripts=list(transcripts),
                interrupted=interrupted,
            )
        )
        return future

    return AsyncMock(side_effect=_impl)


def _mock_session_wire(session: _OpenAIRealtimeStreamingSession) -> None:
    """
    Replace the session's websocket-facing private methods with AsyncMocks.

    Every test that drives ``run_async`` needs these stubbed so the orchestration
    code under test doesn't try to speak to a real connection. Tests can override
    any individual mock (e.g. ``session._request_response_async = _make_request_response_async(...)``)
    after this call.
    """
    session._send_streaming_session_config_async = AsyncMock()
    session._push_audio_chunk_async = AsyncMock()
    session._swap_user_audio_async = AsyncMock()
    session._request_response_async = _make_request_response_async()


def _build_normalizer() -> MagicMock:
    normalizer = MagicMock(name="PromptNormalizer")
    normalizer.add_prepended_conversation_to_memory_async = AsyncMock()
    # Identity: the session treats ``converted is raw_pcm`` as "no converters ran".
    normalizer.convert_audio_async = AsyncMock(side_effect=lambda raw_pcm, **kw: raw_pcm)
    normalizer.convert_values_async = AsyncMock()
    normalizer.hash_and_persist_message_async = AsyncMock()
    return normalizer


@contextlib.contextmanager
def _patched_dispatcher():
    """Patch the dispatcher factory + BadRequestError symbol inside the session module."""
    captured: dict[str, Any] = {}

    def _factory(*, connection, on_user_audio_committed):
        captured["connection"] = connection
        captured["on_user_audio_committed"] = on_user_audio_committed
        d = MagicMock(name="dispatcher")
        d.start_async = AsyncMock()
        d.stop_async = AsyncMock()
        d.drain_callbacks_async = AsyncMock()
        d.add_failure_callback = MagicMock()
        captured["dispatcher"] = d
        return d

    with (
        patch(
            "pyrit.prompt_target.openai._openai_realtime_streaming_session._OpenAIRealtimeDispatcher",
            side_effect=_factory,
        ),
        patch(
            "pyrit.prompt_target.openai._openai_realtime_streaming_session._OpenAIBadRequestError",
            _StubBadRequest,
        ),
    ):
        yield captured


async def _run_session_with_events(
    session: _OpenAIRealtimeStreamingSession,
    *,
    finish: asyncio.Event,
    events: list[CommittedEvent],
) -> list[Message]:
    """Drive run_async to completion while firing the supplied committed events sequentially."""
    messages: list[Message] = []

    async def _consume() -> None:
        messages.extend([msg async for msg in session.run_async()])

    async def _fire() -> None:
        # Let the consumer task start and create the dispatcher / queue.
        await asyncio.sleep(0)
        for event in events:
            await session._on_committed_async(event)
        finish.set()

    await asyncio.gather(_consume(), _fire())
    return messages


# ---------------------------------------------------------------------------
# 1. Constructor smoke + conversation_id auto-generation
# ---------------------------------------------------------------------------


def test_init_autogenerates_conversation_id_when_omitted():
    """Constructor must populate a UUID conversation_id when caller does not supply one."""
    target = _build_target()
    normalizer = _build_normalizer()

    async def _empty():
        if False:
            yield b""

    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_empty(),
        prompt_normalizer=normalizer,
    )

    # Valid UUID4
    parsed = uuid.UUID(session._conversation_id)
    assert parsed.version == 4

    explicit = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_empty(),
        prompt_normalizer=normalizer,
        conversation_id="conv-explicit",
    )
    assert explicit._conversation_id == "conv-explicit"


# ---------------------------------------------------------------------------
# 2. Happy path: 2 VAD-committed turns -> 2 yielded Messages, both persisted
# ---------------------------------------------------------------------------


async def test_run_async_yields_one_message_per_committed_turn():
    """Two simulated server-VAD commits yield two assistant Messages and persist both user+assistant pairs."""
    target = _build_target()
    normalizer = _build_normalizer()

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100, b"\x02" * 100], finish),
        prompt_normalizer=normalizer,
    )
    _mock_session_wire(session)
    session._request_response_async = _make_request_response_async(transcripts=("hello", " world"))

    with _patched_dispatcher():
        messages = await _run_session_with_events(
            session,
            finish=finish,
            events=[CommittedEvent(item_id="item-1"), CommittedEvent(item_id="item-2")],
        )

    assert len(messages) == 2
    for msg in messages:
        # Each yielded Message is the assistant message with a text + audio piece.
        assert len(msg.message_pieces) == 2
        roles = {piece.api_role for piece in msg.message_pieces}
        assert roles == {"assistant"}
        data_types = {piece.original_value_data_type for piece in msg.message_pieces}
        assert data_types == {"text", "audio_path"}

    # 2 turns * (user + assistant) = 4 persistence calls.
    assert normalizer.hash_and_persist_message_async.await_count == 4
    # _request_response_async called once per turn.
    assert session._request_response_async.await_count == 2


# ---------------------------------------------------------------------------
# 3. Interrupted turn propagates the metadata key to both assistant pieces
# ---------------------------------------------------------------------------


async def test_run_async_marks_assistant_pieces_when_turn_interrupted():
    """When a turn is interrupted, STREAMING_INTERRUPTED_KEY must be set on text + audio pieces."""
    target = _build_target()
    normalizer = _build_normalizer()

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100], finish),
        prompt_normalizer=normalizer,
    )
    _mock_session_wire(session)
    session._request_response_async = _make_request_response_async(interrupted=True)

    with _patched_dispatcher():
        messages = await _run_session_with_events(session, finish=finish, events=[CommittedEvent(item_id="item-1")])

    assert len(messages) == 1
    for piece in messages[0].message_pieces:
        assert piece.prompt_metadata.get(STREAMING_INTERRUPTED_KEY) is True


# ---------------------------------------------------------------------------
# 4. Response converters run against the assembled assistant Message
# ---------------------------------------------------------------------------


async def test_run_async_applies_response_converters_to_assistant_message():
    """Response converter configurations must be applied to the assembled assistant Message."""
    target = _build_target()
    normalizer = _build_normalizer()

    response_cfg = MagicMock(name="response_converter_cfg")

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100], finish),
        prompt_normalizer=normalizer,
        response_converter_configurations=[response_cfg],
    )
    _mock_session_wire(session)

    with _patched_dispatcher():
        messages = await _run_session_with_events(session, finish=finish, events=[CommittedEvent(item_id="item-1")])

    assert len(messages) == 1
    normalizer.convert_values_async.assert_awaited_once()
    call_kwargs = normalizer.convert_values_async.await_args.kwargs
    assert call_kwargs["converter_configurations"] == [response_cfg]
    assert call_kwargs["message"] is messages[0]


# ---------------------------------------------------------------------------
# 5. Request converters trigger swap + populate user_piece.converter_identifiers
# ---------------------------------------------------------------------------


async def test_run_async_swaps_user_audio_and_records_identifiers_when_request_converters_present():
    """With request converters: convert_audio_async + _swap_user_audio_async run, identifiers reach user piece."""
    target = _build_target()
    normalizer = _build_normalizer()
    # Force convert_audio_async to return a NEW object so the session treats it as "converted".
    normalizer.convert_audio_async = AsyncMock(side_effect=lambda raw_pcm, **kw: b"converted" + raw_pcm)

    fake_converter = MagicMock(name="converter")
    fake_converter.get_identifier = MagicMock(return_value={"__type__": "FakeConverter"})
    request_cfg = MagicMock(name="request_converter_cfg")
    request_cfg.converters = [fake_converter]

    persisted_user_messages: list[Message] = []

    async def _capture(*, message: Message, target_identifier=None) -> None:
        if message.message_pieces[0].api_role == "user":
            persisted_user_messages.append(message)

    normalizer.hash_and_persist_message_async = AsyncMock(side_effect=_capture)

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100], finish),
        prompt_normalizer=normalizer,
        request_converter_configurations=[request_cfg],
    )
    _mock_session_wire(session)

    with _patched_dispatcher():
        await _run_session_with_events(session, finish=finish, events=[CommittedEvent(item_id="item-A")])

    normalizer.convert_audio_async.assert_awaited_once()
    session._swap_user_audio_async.assert_awaited_once()
    swap_kwargs = session._swap_user_audio_async.await_args.kwargs
    assert swap_kwargs["committed_event"].item_id == "item-A"

    assert len(persisted_user_messages) == 1
    user_piece = persisted_user_messages[0].message_pieces[0]
    assert user_piece.converter_identifiers == [{"__type__": "FakeConverter"}]


async def test_run_async_skips_swap_and_identifiers_when_no_request_converters():
    """Without request converters: no convert_audio_async, no _swap_user_audio_async, empty identifiers."""
    target = _build_target()
    normalizer = _build_normalizer()

    persisted_user_messages: list[Message] = []

    async def _capture(*, message: Message, target_identifier=None) -> None:
        if message.message_pieces[0].api_role == "user":
            persisted_user_messages.append(message)

    normalizer.hash_and_persist_message_async = AsyncMock(side_effect=_capture)

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100], finish),
        prompt_normalizer=normalizer,
    )
    _mock_session_wire(session)

    with _patched_dispatcher():
        await _run_session_with_events(session, finish=finish, events=[CommittedEvent(item_id="item-B")])

    normalizer.convert_audio_async.assert_not_called()
    session._swap_user_audio_async.assert_not_called()

    assert len(persisted_user_messages) == 1
    assert persisted_user_messages[0].message_pieces[0].converter_identifiers == []


# ---------------------------------------------------------------------------
# 6. Prepended conversation + VAD config reach the streaming handle and memory
# ---------------------------------------------------------------------------


async def test_run_async_persists_prepended_conversation_and_forwards_vad_config():
    """``prepended_conversation`` reaches normalizer.add_prepended_conversation_to_memory_async; vad reaches session."""
    target = _build_target()
    normalizer = _build_normalizer()

    prepended = [MagicMock(name="prepended_message")]
    vad = ServerVadConfig()

    finish = asyncio.Event()
    finish.set()  # No chunks to drain; iterator exhausts immediately.

    async def _empty():
        if False:
            yield b""

    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_empty(),
        prompt_normalizer=normalizer,
        prepended_conversation=prepended,
        server_vad=vad,
        conversation_id="conv-prep",
    )
    _mock_session_wire(session)

    with _patched_dispatcher():
        # No committed events; iterator is empty so producer exits immediately.
        async for _ in session.run_async():
            pytest.fail("no events were fired; session should yield nothing")

    # Session captured the per-call vad as the effective config.
    assert session._effective_vad is vad
    # The session retained the prepended conversation (its config builder reads from it).
    assert session._prepended_conversation == prepended
    # The streaming session config was emitted exactly once.
    session._send_streaming_session_config_async.assert_awaited_once()

    normalizer.add_prepended_conversation_to_memory_async.assert_awaited_once()
    prep_kwargs = normalizer.add_prepended_conversation_to_memory_async.await_args.kwargs
    assert prep_kwargs["conversation_id"] == "conv-prep"
    assert prep_kwargs["should_convert"] is False
    assert prep_kwargs["prepended_conversation"] == prepended


# ---------------------------------------------------------------------------
# 7. Dispatcher failure (no active turn) propagates via failure callback bridge
# ---------------------------------------------------------------------------


async def test_run_async_propagates_dispatcher_failure_via_failure_callback():
    """If the dispatch loop dies without an active turn, the failure callback unblocks the consumer."""
    target = _build_target()
    normalizer = _build_normalizer()

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100], finish),
        prompt_normalizer=normalizer,
    )
    _mock_session_wire(session)

    dispatcher_failure = RuntimeError("dispatch loop died")

    with _patched_dispatcher() as captured:

        async def _consume() -> None:
            async for _ in session.run_async():
                pytest.fail("no message should be yielded before the failure surfaces")

        async def _fire_failure() -> None:
            # Let run_async progress past dispatcher.start_async() and the add_failure_callback registration.
            for _ in range(5):
                await asyncio.sleep(0)
            assert captured["dispatcher"].add_failure_callback.call_count == 1
            registered_cb = captured["dispatcher"].add_failure_callback.call_args.args[0]
            # Simulate the dispatch loop dying: invoke the registered failure callback synchronously.
            registered_cb(dispatcher_failure)
            # Unblock the chunks iterator so the producer can exit cleanly after the consumer raises.
            finish.set()

        with pytest.raises(RuntimeError, match="dispatch loop died"):
            await asyncio.gather(_consume(), _fire_failure())


# ---------------------------------------------------------------------------
# 7b. Forced final commit is gated on the server's minimum buffer size
# ---------------------------------------------------------------------------


async def test_drain_skips_forced_commit_when_pending_below_minimum():
    """A tail buffer under the 100ms server minimum must not trigger a forced commit."""
    target = _build_target()
    normalizer = _build_normalizer()
    connection = target._connect_async.return_value

    finish = asyncio.Event()
    # 96 bytes = 1ms at 24 kHz PCM16 mono, well below the 100ms minimum.
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x00" * 96], finish),
        prompt_normalizer=normalizer,
    )
    _mock_session_wire(session)

    with _patched_dispatcher():
        messages = await _run_session_with_events(session, finish=finish, events=[])

    assert messages == []
    connection.input_audio_buffer.commit.assert_not_awaited()


async def test_drain_forces_commit_when_pending_meets_minimum():
    """At least 100ms of uncommitted audio must trigger the forced final commit."""
    target = _build_target()
    normalizer = _build_normalizer()
    connection = target._connect_async.return_value

    finish = asyncio.Event()
    # 4800 bytes = exactly 100ms at 24 kHz PCM16 mono.
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x00" * 4800], finish),
        prompt_normalizer=normalizer,
    )
    _mock_session_wire(session)

    with _patched_dispatcher():
        messages = await _run_session_with_events(session, finish=finish, events=[])

    assert messages == []
    connection.input_audio_buffer.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# 8. Trim: pre-speech silence is stripped using audio_start_ms before persistence
# ---------------------------------------------------------------------------


async def test_on_committed_trims_pre_speech_silence_before_persisting_user_audio():
    """``audio_start_ms`` past prefix_padding trims the snapshot before save_audio is called."""
    target = _build_target()
    normalizer = _build_normalizer()

    # 600ms buffer @ 24kHz mono PCM16 = 600 * 48 = 28800 bytes. We push it as one chunk
    # so the trim computation is easy to reason about.
    bytes_per_ms = 48
    buffer_ms = 600
    chunk = b"\xaa" * (buffer_ms * bytes_per_ms)
    finish = asyncio.Event()

    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([chunk], finish),
        prompt_normalizer=normalizer,
        server_vad=ServerVadConfig(prefix_padding_ms=100),
    )
    _mock_session_wire(session)

    with _patched_dispatcher():
        await _run_session_with_events(
            session,
            finish=finish,
            events=[CommittedEvent(item_id="item-1", audio_start_ms=500)],
        )

    # start_ms = max(0, 500 - 100) = 400 → start_byte = 400 * 48 = 19200
    # trimmed length = 28800 - 19200 = 9600 bytes (200ms)
    raw_save_call = target.save_audio_async.await_args_list[0]
    saved_user_pcm = raw_save_call.args[0] if raw_save_call.args else raw_save_call.kwargs.get("pcm")
    assert len(saved_user_pcm) == 9600


async def test_on_committed_skips_trim_when_audio_start_ms_missing():
    """When ``audio_start_ms`` is None, the full buffer is persisted (no trim)."""
    target = _build_target()
    normalizer = _build_normalizer()

    bytes_per_ms = 48
    buffer_ms = 200
    chunk = b"\xbb" * (buffer_ms * bytes_per_ms)
    finish = asyncio.Event()

    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([chunk], finish),
        prompt_normalizer=normalizer,
        server_vad=ServerVadConfig(prefix_padding_ms=100),
    )
    _mock_session_wire(session)

    with _patched_dispatcher():
        await _run_session_with_events(
            session,
            finish=finish,
            events=[CommittedEvent(item_id="item-1", audio_start_ms=None)],
        )

    raw_save_call = target.save_audio_async.await_args_list[0]
    saved_user_pcm = raw_save_call.args[0] if raw_save_call.args else raw_save_call.kwargs.get("pcm")
    assert len(saved_user_pcm) == buffer_ms * bytes_per_ms


async def test_buffer_start_session_ms_advances_across_commits():
    """Second commit's server-relative ``audio_start_ms`` is mapped through ``_buffer_start_session_ms``."""
    target = _build_target()
    normalizer = _build_normalizer()

    bytes_per_ms = 48
    # Turn 1 buffer: 600ms, audio_start_ms=500 (server-relative) → trims 400ms = 19200 bytes.
    # After turn 1, _buffer_start_session_ms advances to 600.
    # Turn 2 buffer: 400ms, audio_start_ms=800 (server-relative) → buffer-relative = 200.
    #   start_ms = max(0, 200 - 100) = 100 → 100*48 = 4800 bytes trimmed.
    #   trimmed length = 19200 - 4800 = 14400 bytes (300ms).
    chunk1 = b"\xaa" * (600 * bytes_per_ms)
    chunk2 = b"\xbb" * (400 * bytes_per_ms)

    # Per-chunk gating so the second chunk lands AFTER commit #1 fires; otherwise the
    # producer would drain both chunks into the buffer before any commit and turn 1
    # would see 1000ms of audio (advancing _buffer_start_session_ms past turn 2's
    # event.audio_start_ms).
    gate2 = asyncio.Event()
    finish = asyncio.Event()

    async def _gated_chunks():
        yield chunk1
        await gate2.wait()
        yield chunk2
        await finish.wait()

    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_gated_chunks(),
        prompt_normalizer=normalizer,
        server_vad=ServerVadConfig(prefix_padding_ms=100),
    )
    _mock_session_wire(session)

    async def _consume() -> None:
        async for _msg in session.run_async():
            pass

    async def _fire() -> None:
        await asyncio.sleep(0)
        await session._on_committed_async(CommittedEvent(item_id="t1", audio_start_ms=500))
        gate2.set()
        # Give the producer time to drain chunk2 into _pending_chunks.
        for _ in range(20):
            await asyncio.sleep(0)
        await session._on_committed_async(CommittedEvent(item_id="t2", audio_start_ms=800))
        finish.set()

    with _patched_dispatcher():
        await asyncio.gather(_consume(), _fire())

    # save_audio call ordering per turn: raw_user, assistant. We requested no request converters
    # so converted_user_path == raw_user_path and only one user save_audio fires per turn.
    # Across two turns: [user_t1, assistant_t1, user_t2, assistant_t2].
    calls = target.save_audio_async.await_args_list
    assert len(calls) == 4
    assert len(calls[0].args[0]) == 9600
    assert len(calls[2].args[0]) == 14400


# ---------------------------------------------------------------------------
# 10. persist_prepended_conversation=False skips the prepended-memory write
# ---------------------------------------------------------------------------


async def test_persist_prepended_conversation_false_skips_memory_add():
    """``persist_prepended_conversation=False`` skips ``add_prepended_conversation_to_memory``."""
    target = _build_target()
    normalizer = _build_normalizer()

    finish = asyncio.Event()
    finish.set()

    async def _empty():
        if False:
            yield b""

    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_empty(),
        prompt_normalizer=normalizer,
        prepended_conversation=[MagicMock(name="prepended_message")],
        persist_prepended_conversation=False,
    )
    _mock_session_wire(session)

    with _patched_dispatcher():
        async for _ in session.run_async():
            pytest.fail("no events were fired; session should yield nothing")

    # _send_streaming_session_config still runs (it reads the prepended conversation for system msg).
    session._send_streaming_session_config_async.assert_awaited_once()
    # But the memory write is skipped — the caller (e.g., the attack) has already persisted it.
    normalizer.add_prepended_conversation_to_memory_async.assert_not_called()


# ---------------------------------------------------------------------------
# 11. Factory passthrough: RealtimeTarget.open_streaming_session forwards every kwarg
# ---------------------------------------------------------------------------


_CLEAN_ENV = {"OPENAI_REALTIME_UNDERLYING_MODEL": ""}


@patch.dict("os.environ", _CLEAN_ENV)
def test_open_streaming_session_forwards_kwargs_to_session_constructor(sqlite_instance):
    """``RealtimeTarget.open_streaming_session`` is a thin pass-through to the session ctor."""
    from pyrit.prompt_target import RealtimeTarget

    target = RealtimeTarget(api_key="k", endpoint="wss://test_url", model_name="test")
    normalizer = _build_normalizer()

    async def _empty():
        if False:
            yield b""

    chunks = _empty()
    prepended = [MagicMock(name="prepended_message")]
    req_cfgs = [MagicMock(name="req_cfg")]
    resp_cfgs = [MagicMock(name="resp_cfg")]
    vad = ServerVadConfig(prefix_padding_ms=42)

    captured: dict[str, Any] = {}

    def _fake_session_ctor(**kwargs):
        captured.update(kwargs)
        return MagicMock(name="session")

    with patch(
        "pyrit.prompt_target.openai.openai_realtime_target._OpenAIRealtimeStreamingSession",
        side_effect=_fake_session_ctor,
    ):
        target.open_streaming_session(
            audio_chunks=chunks,
            prompt_normalizer=normalizer,
            conversation_id="conv-X",
            request_converter_configurations=req_cfgs,
            response_converter_configurations=resp_cfgs,
            prepended_conversation=prepended,
            server_vad=vad,
            persist_prepended_conversation=False,
        )

    assert captured["target"] is target
    assert captured["audio_chunks"] is chunks
    assert captured["prompt_normalizer"] is normalizer
    assert captured["conversation_id"] == "conv-X"
    assert captured["request_converter_configurations"] is req_cfgs
    assert captured["response_converter_configurations"] is resp_cfgs
    assert captured["prepended_conversation"] is prepended
    assert captured["server_vad"] is vad
    assert captured["persist_prepended_conversation"] is False


# ---------------------------------------------------------------------------
# 12. Direct unit tests for the _trim_snapshot_to_speech helper
# ---------------------------------------------------------------------------


def test_trim_returns_full_buffer_when_audio_start_ms_none():
    from pyrit.prompt_target.openai._openai_realtime_streaming_session import _trim_snapshot_to_speech

    buf = b"\xaa" * (100 * 48)
    out = _trim_snapshot_to_speech(raw_buffer=buf, sample_rate_hz=24000, audio_start_ms=None, prefix_padding_ms=300)
    assert out is buf


def test_trim_returns_full_buffer_when_audio_start_ms_zero():
    from pyrit.prompt_target.openai._openai_realtime_streaming_session import _trim_snapshot_to_speech

    buf = b"\xaa" * (100 * 48)
    out = _trim_snapshot_to_speech(raw_buffer=buf, sample_rate_hz=24000, audio_start_ms=0, prefix_padding_ms=300)
    assert out is buf


def test_trim_raises_on_negative_audio_start_ms():
    from pyrit.prompt_target.openai._openai_realtime_streaming_session import _trim_snapshot_to_speech

    with pytest.raises(ValueError, match="must be >= 0"):
        _trim_snapshot_to_speech(raw_buffer=b"\x00" * 100, sample_rate_hz=24000, audio_start_ms=-1, prefix_padding_ms=0)


def test_trim_keeps_prefix_padding_window():
    """``audio_start_ms - prefix_padding_ms`` is the trim point; padding before speech is kept."""
    from pyrit.prompt_target.openai._openai_realtime_streaming_session import _trim_snapshot_to_speech

    # 1000ms buffer; speech starts at 700ms with 200ms padding → trim at 500ms = 24000 bytes.
    buf = b"\xaa" * (1000 * 48)
    out = _trim_snapshot_to_speech(raw_buffer=buf, sample_rate_hz=24000, audio_start_ms=700, prefix_padding_ms=200)
    assert len(out) == 500 * 48


def test_trim_returns_full_buffer_when_trim_would_exceed_length():
    """Defensive: when the computed trim is beyond the buffer, return the original buffer."""
    from pyrit.prompt_target.openai._openai_realtime_streaming_session import _trim_snapshot_to_speech

    # 100ms buffer; speech "starts" at 5000ms with 0 padding → trim past end.
    buf = b"\xaa" * (100 * 48)
    out = _trim_snapshot_to_speech(raw_buffer=buf, sample_rate_hz=24000, audio_start_ms=5000, prefix_padding_ms=0)
    assert out is buf


def test_trim_aligns_to_sample_frame_boundary():
    """Trim must align to ``sample_width_bytes * channels`` so PCM frames aren't split."""
    from pyrit.prompt_target.openai._openai_realtime_streaming_session import _trim_snapshot_to_speech

    # An odd start_byte (e.g., 1) must be rounded down to 0 for sample_width=2.
    buf = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    # Construct an audio_start_ms that lands on byte 1 to trigger alignment:
    # bytes_per_ms = 24000 * 2 // 1000 = 48 → no fine-grained way to land on byte 1.
    # Instead pick rate=1000Hz so bytes_per_ms = 1000*2//1000 = 2; audio_start_ms=1 → start_byte=2.
    # That's already aligned. Use sample_width=2 with a contrived offset by inspecting math:
    # For rate=500Hz (so bytes_per_ms = 1), audio_start_ms=3 → start_byte=3 → must align to 2.
    out = _trim_snapshot_to_speech(
        raw_buffer=buf,
        sample_rate_hz=500,
        audio_start_ms=3,
        prefix_padding_ms=0,
        sample_width_bytes=2,
        channels=1,
    )
    # start_byte = 3 → aligned down to 2 → trimmed = buf[2:] = b"\x03..\x08" (6 bytes)
    assert out == b"\x03\x04\x05\x06\x07\x08"


# ---------------------------------------------------------------------------
# 13. Wire-level tests for the session's websocket-facing private helpers
# ---------------------------------------------------------------------------


_CLEAN_ENV = {"OPENAI_REALTIME_UNDERLYING_MODEL": ""}


def _real_session_with_mock_connection(
    sqlite_instance,
    *,
    server_vad: bool | ServerVadConfig = True,
    prepended_conversation=None,
):
    """Build a real ``_OpenAIRealtimeStreamingSession`` over a real ``RealtimeTarget`` with a mock connection.

    The session is wired to a real target (so the privates that delegate into
    ``_set_system_prompt_and_config_vars`` work) but its connection is replaced
    with an AsyncMock so wire calls are observable. Audio chunks iterator and
    prompt normalizer are stubbed to whatever the caller wants — these tests
    only exercise individual private helpers, not ``run_async``.
    """
    from pyrit.prompt_target import RealtimeTarget

    with patch.dict("os.environ", _CLEAN_ENV):
        target = RealtimeTarget(api_key="k", endpoint="wss://test_url", model_name="test")

    async def _empty():
        if False:
            yield b""

    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_empty(),
        prompt_normalizer=MagicMock(name="normalizer"),
        prepended_conversation=prepended_conversation,
        server_vad=server_vad,
    )
    session._connection = AsyncMock(name="connection")
    return session


# --- _push_audio_chunk_async ------------------------------------------------


async def test_push_audio_chunk_async_base64_encodes_and_appends(sqlite_instance):
    session = _real_session_with_mock_connection(sqlite_instance)
    pcm = b"\x33" * 480

    await session._push_audio_chunk_async(pcm)

    session._connection.input_audio_buffer.append.assert_awaited_once()
    audio_b64 = session._connection.input_audio_buffer.append.call_args.kwargs["audio"]
    assert base64.b64decode(audio_b64) == pcm


async def test_push_audio_chunk_async_empty_is_noop(sqlite_instance):
    session = _real_session_with_mock_connection(sqlite_instance)
    await session._push_audio_chunk_async(b"")
    session._connection.input_audio_buffer.append.assert_not_called()


# --- _swap_user_audio_async -------------------------------------------------


async def test_swap_user_audio_async_inserts_converted_then_deletes_original(sqlite_instance):
    """``_swap_user_audio_async`` must insert the converted PCM then delete the original item."""
    session = _real_session_with_mock_connection(sqlite_instance)
    event = CommittedEvent(item_id="raw_swap_1")

    await session._swap_user_audio_async(committed_event=event, converted_pcm=b"\xab" * 96)

    session._connection.conversation.item.create.assert_awaited_once()
    session._connection.conversation.item.delete.assert_awaited_once_with(item_id="raw_swap_1")
    # Insert must precede delete: any future refactor that swaps the order or runs them
    # concurrently would corrupt the streaming session — pin the ordering here.
    create_index = session._connection.method_calls.index(call.conversation.item.create(item=ANY))
    delete_index = session._connection.method_calls.index(call.conversation.item.delete(item_id="raw_swap_1"))
    assert create_index < delete_index


async def test_swap_user_audio_async_logs_and_swallows_delete_failure(sqlite_instance, caplog):
    """Best-effort delete: if ``delete`` raises, ``swap`` logs a warning and returns normally."""
    session = _real_session_with_mock_connection(sqlite_instance)
    session._connection.conversation.item.delete.side_effect = RuntimeError("delete blew up")
    event = CommittedEvent(item_id="raw_swap_fail")

    with caplog.at_level("WARNING"):
        await session._swap_user_audio_async(committed_event=event, converted_pcm=b"\x01" * 96)

    session._connection.conversation.item.create.assert_awaited_once()
    session._connection.conversation.item.delete.assert_awaited_once_with(item_id="raw_swap_fail")
    # Even on delete failure, insert must have happened first.
    create_index = session._connection.method_calls.index(call.conversation.item.create(item=ANY))
    delete_index = session._connection.method_calls.index(call.conversation.item.delete(item_id="raw_swap_fail"))
    assert create_index < delete_index
    assert any("delete failed for raw_swap_fail" in record.message for record in caplog.records)


# --- _request_response_async ------------------------------------------------


async def test_request_response_async_registers_turn_and_sends_response_create(sqlite_instance):
    """_request_response_async must register a fresh turn and call response.create."""
    session = _real_session_with_mock_connection(sqlite_instance)
    from pyrit.prompt_target.common.realtime_audio import RealtimeTurnState

    dispatcher = MagicMock()
    dispatcher.register_turn = MagicMock()
    session._dispatcher = dispatcher

    future = await session._request_response_async()

    dispatcher.register_turn.assert_called_once()
    registered_state = dispatcher.register_turn.call_args.args[0]
    assert isinstance(registered_state, RealtimeTurnState)
    assert registered_state.completion is future
    session._connection.response.create.assert_awaited_once_with()


async def test_request_response_async_future_resolves_with_dispatcher_result(sqlite_instance):
    """The future returned by _request_response_async resolves when the turn ends."""
    session = _real_session_with_mock_connection(sqlite_instance)
    dispatcher = MagicMock()
    expected_result = RealtimeTargetResult(audio_bytes=b"\xaa" * 96, transcripts=["ok"])

    def _register(state):
        state.completion.set_result(expected_result)

    dispatcher.register_turn = MagicMock(side_effect=_register)
    session._dispatcher = dispatcher

    future = await session._request_response_async()
    result = await future
    assert result is expected_result


async def test_request_response_async_propagates_register_turn_failure(sqlite_instance):
    """If another turn is already pending, register_turn raises and request_response_async surfaces it."""
    session = _real_session_with_mock_connection(sqlite_instance)
    dispatcher = MagicMock()
    dispatcher.register_turn = MagicMock(side_effect=RuntimeError("turn already pending"))
    session._dispatcher = dispatcher

    with pytest.raises(RuntimeError, match="turn already pending"):
        await session._request_response_async()

    session._connection.response.create.assert_not_called()


# --- _send_streaming_session_config_async -----------------------------------


async def test_send_streaming_session_config_async_emits_create_response_false(sqlite_instance):
    """The streaming session config must flip create_response to False on turn_detection."""
    session = _real_session_with_mock_connection(sqlite_instance, server_vad=True)
    await session._send_streaming_session_config_async()
    session._connection.session.update.assert_awaited_once()
    config = session._connection.session.update.call_args.kwargs["session"]
    assert config["audio"]["input"]["turn_detection"]["create_response"] is False


async def test_send_streaming_session_config_async_requires_server_vad(sqlite_instance):
    """Without server VAD on the session, sending streaming session config must raise."""
    session = _real_session_with_mock_connection(sqlite_instance, server_vad=False)
    with pytest.raises(ValueError, match="server VAD"):
        await session._send_streaming_session_config_async()


async def test_send_streaming_session_config_async_uses_system_message_from_conversation(sqlite_instance):
    """If the prepended conversation begins with a system message, it becomes session instructions."""
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
    session = _real_session_with_mock_connection(sqlite_instance, server_vad=True, prepended_conversation=[system_msg])
    await session._send_streaming_session_config_async()
    config = session._connection.session.update.call_args.kwargs["session"]
    assert config["instructions"] == "You are a strict assistant."
