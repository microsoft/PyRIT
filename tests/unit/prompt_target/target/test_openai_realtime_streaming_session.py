# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the internal _OpenAIRealtimeStreamingSession lifecycle."""

import asyncio
import contextlib
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import Message
from pyrit.prompt_target.common.realtime_audio import CommittedEvent, RealtimeTargetResult
from pyrit.prompt_target.common.streaming import ServerVadConfig
from pyrit.prompt_target.common.streaming.streaming_audio_target import (
    STREAMING_INTERRUPTED_KEY,
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
    """Build a MagicMock target exposing the streaming + connection surface the session calls."""
    target = MagicMock(name="RealtimeTarget")
    target.streaming = MagicMock(name="streaming")
    target.streaming.SAMPLE_RATE_HZ = 24000

    connection = AsyncMock(name="connection")
    # AsyncMock auto-creates attributes as AsyncMock, but child attribute chains like
    # ``input_audio_buffer.commit`` need explicit construction so ``commit`` is awaitable
    # and we can attach a side_effect to make the forced final commit fail benignly.
    connection.input_audio_buffer = MagicMock()
    connection.input_audio_buffer.commit = AsyncMock(side_effect=_StubBadRequest("input_audio_buffer_commit_empty"))

    target.streaming.connect_async = AsyncMock(return_value=connection)
    target.streaming.send_streaming_session_config_async = AsyncMock()
    target.streaming.push_audio_chunk_async = AsyncMock()
    target.streaming.save_audio = AsyncMock(side_effect=lambda pcm, **kw: f"/tmp/audio-{uuid.uuid4().hex[:8]}.wav")
    target.swap_user_audio_async = AsyncMock()
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
    """AsyncMock for ``RealtimeTarget.request_response_async`` returning a resolved Future."""

    async def _impl(*, connection: Any, dispatcher: Any) -> asyncio.Future:
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


def _build_normalizer() -> MagicMock:
    normalizer = MagicMock(name="PromptNormalizer")
    normalizer.add_prepended_conversation_to_memory = AsyncMock()
    # Identity: the session treats ``converted is raw_pcm`` as "no converters ran".
    normalizer.convert_audio_async = AsyncMock(side_effect=lambda raw_pcm, **kw: raw_pcm)
    normalizer.convert_values = AsyncMock()
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
        d.start = AsyncMock()
        d.stop = AsyncMock()
        d.drain_callbacks = AsyncMock()
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
            await session._on_committed(event)
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
    target.request_response_async = _make_request_response_async(transcripts=("hello", " world"))
    normalizer = _build_normalizer()

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100, b"\x02" * 100], finish),
        prompt_normalizer=normalizer,
    )

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
    # request_response_async called once per turn.
    assert target.request_response_async.await_count == 2


# ---------------------------------------------------------------------------
# 3. Interrupted turn propagates the metadata key to both assistant pieces
# ---------------------------------------------------------------------------


async def test_run_async_marks_assistant_pieces_when_turn_interrupted():
    """When a turn is interrupted, STREAMING_INTERRUPTED_KEY must be set on text + audio pieces."""
    target = _build_target()
    target.request_response_async = _make_request_response_async(interrupted=True)
    normalizer = _build_normalizer()

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100], finish),
        prompt_normalizer=normalizer,
    )

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
    target.request_response_async = _make_request_response_async()
    normalizer = _build_normalizer()

    response_cfg = MagicMock(name="response_converter_cfg")

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100], finish),
        prompt_normalizer=normalizer,
        response_converter_configurations=[response_cfg],
    )

    with _patched_dispatcher():
        messages = await _run_session_with_events(session, finish=finish, events=[CommittedEvent(item_id="item-1")])

    assert len(messages) == 1
    normalizer.convert_values.assert_awaited_once()
    call_kwargs = normalizer.convert_values.await_args.kwargs
    assert call_kwargs["converter_configurations"] == [response_cfg]
    assert call_kwargs["message"] is messages[0]


# ---------------------------------------------------------------------------
# 5. Request converters trigger swap + populate user_piece.converter_identifiers
# ---------------------------------------------------------------------------


async def test_run_async_swaps_user_audio_and_records_identifiers_when_request_converters_present():
    """With request converters: convert_audio_async + swap_user_audio_async run, identifiers reach user piece."""
    target = _build_target()
    target.request_response_async = _make_request_response_async()
    normalizer = _build_normalizer()
    # Force convert_audio_async to return a NEW object so the session treats it as "converted".
    normalizer.convert_audio_async = AsyncMock(side_effect=lambda raw_pcm, **kw: b"converted" + raw_pcm)

    fake_converter = MagicMock(name="converter")
    fake_converter.get_identifier = MagicMock(return_value={"__type__": "FakeConverter"})
    request_cfg = MagicMock(name="request_converter_cfg")
    request_cfg.converters = [fake_converter]

    persisted_user_messages: list[Message] = []

    async def _capture(*, message: Message) -> None:
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

    with _patched_dispatcher():
        await _run_session_with_events(session, finish=finish, events=[CommittedEvent(item_id="item-A")])

    normalizer.convert_audio_async.assert_awaited_once()
    target.swap_user_audio_async.assert_awaited_once()
    swap_kwargs = target.swap_user_audio_async.await_args.kwargs
    assert swap_kwargs["committed_event"].item_id == "item-A"

    assert len(persisted_user_messages) == 1
    user_piece = persisted_user_messages[0].message_pieces[0]
    assert user_piece.converter_identifiers == [{"__type__": "FakeConverter"}]


async def test_run_async_skips_swap_and_identifiers_when_no_request_converters():
    """Without request converters: no convert_audio_async, no swap_user_audio_async, empty identifiers."""
    target = _build_target()
    target.request_response_async = _make_request_response_async()
    normalizer = _build_normalizer()

    persisted_user_messages: list[Message] = []

    async def _capture(*, message: Message) -> None:
        if message.message_pieces[0].api_role == "user":
            persisted_user_messages.append(message)

    normalizer.hash_and_persist_message_async = AsyncMock(side_effect=_capture)

    finish = asyncio.Event()
    session = _OpenAIRealtimeStreamingSession(
        target=target,
        audio_chunks=_paced_chunks([b"\x01" * 100], finish),
        prompt_normalizer=normalizer,
    )

    with _patched_dispatcher():
        await _run_session_with_events(session, finish=finish, events=[CommittedEvent(item_id="item-B")])

    normalizer.convert_audio_async.assert_not_called()
    target.swap_user_audio_async.assert_not_called()

    assert len(persisted_user_messages) == 1
    assert persisted_user_messages[0].message_pieces[0].converter_identifiers == []


# ---------------------------------------------------------------------------
# 6. Prepended conversation + VAD config reach the streaming handle and memory
# ---------------------------------------------------------------------------


async def test_run_async_persists_prepended_conversation_and_forwards_vad_config():
    """``prepended_conversation`` reaches normalizer.add_prepended_conversation_to_memory and session.update."""
    target = _build_target()
    target.request_response_async = _make_request_response_async()
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
        vad=vad,
        conversation_id="conv-prep",
    )

    with _patched_dispatcher():
        # No committed events; iterator is empty so producer exits immediately.
        async for _ in session.run_async():
            pytest.fail("no events were fired; session should yield nothing")

    target.streaming.send_streaming_session_config_async.assert_awaited_once()
    config_kwargs = target.streaming.send_streaming_session_config_async.await_args.kwargs
    assert config_kwargs["conversation"] == prepended
    assert config_kwargs["vad"] is vad

    normalizer.add_prepended_conversation_to_memory.assert_awaited_once()
    prep_kwargs = normalizer.add_prepended_conversation_to_memory.await_args.kwargs
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

    dispatcher_failure = RuntimeError("dispatch loop died")

    with _patched_dispatcher() as captured:

        async def _consume() -> None:
            async for _ in session.run_async():
                pytest.fail("no message should be yielded before the failure surfaces")

        async def _fire_failure() -> None:
            # Let run_async progress past dispatcher.start() and the add_failure_callback registration.
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
