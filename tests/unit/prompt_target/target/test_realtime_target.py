# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import base64
import wave
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions.exception_classes import ServerErrorException
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import RealtimeTarget, ServerVadConfig
from pyrit.prompt_target.common.realtime_audio import (
    CommittedEvent,
    RealtimeTargetResult,
    RealtimeTurnState,
)
from pyrit.prompt_target.openai._openai_realtime_dispatcher import (
    _OpenAIRealtimeDispatcher,
)
from pyrit.prompt_target.openai._openai_realtime_event_router import (
    _OpenAIRealtimeEventKind,
    _OpenAIRealtimeEventRouter,
)

# Env vars that may leak from .env files loaded by other tests in parallel workers.
_CLEAN_UNDERLYING_MODEL_ENV = {
    "OPENAI_REALTIME_UNDERLYING_MODEL": "",
}


@pytest.fixture
@patch.dict("os.environ", _CLEAN_UNDERLYING_MODEL_ENV)
def target(sqlite_instance):
    return RealtimeTarget(api_key="test_key", endpoint="wss://test_url", model_name="test")


async def test_connect_success(target):
    mock_connection = AsyncMock()
    mock_client = MagicMock()
    mock_client.realtime.connect = MagicMock()
    mock_client.realtime.connect.return_value.__aenter__ = AsyncMock(return_value=mock_connection)

    with patch.object(target, "_get_openai_client", return_value=mock_client):
        connection = await target._connect_async(conversation_id="test_conv")
        assert connection == mock_connection
        mock_client.realtime.connect.assert_called_once_with(model="test")
    await target.cleanup_target_async()


async def test_send_prompt_async(target):
    # Mock the necessary methods
    target._connect_async = AsyncMock(return_value=AsyncMock())
    target.send_config_async = AsyncMock()
    result = RealtimeTargetResult(audio_bytes=b"file", transcripts=["hello"])
    target.send_text_async = AsyncMock(return_value=("output.wav", result))

    # Create a mock Message with a valid data type
    message_piece = MessagePiece(
        original_value="Hello",
        original_value_data_type="text",
        converted_value="Hello",
        converted_value_data_type="text",
        role="user",
        conversation_id="test_conversation_id",
    )
    message = Message(message_pieces=[message_piece])

    # Call the send_prompt_async method
    response = await target.send_prompt_async(message=message)

    assert len(response) == 1
    assert response

    target.send_text_async.assert_called_once_with(
        text="Hello",
        conversation_id="test_conversation_id",
    )
    assert response[0].get_value() == "hello"
    assert response[0].get_value(1) == "output.wav"

    # Clean up the WebSocket connections
    await target.cleanup_target_async()


async def test_send_prompt_async_propagates_interrupted_to_metadata(target):
    """When a turn result carries interrupted=True, both response pieces' metadata must reflect it."""
    target._connect_async = AsyncMock(return_value=AsyncMock())
    target.send_config_async = AsyncMock()
    interrupted_result = RealtimeTargetResult(audio_bytes=b"partial", transcripts=["hi"], interrupted=True)
    target.send_text_async = AsyncMock(return_value=("partial.wav", interrupted_result))

    message_piece = MessagePiece(
        original_value="Hello",
        original_value_data_type="text",
        converted_value="Hello",
        converted_value_data_type="text",
        role="user",
        conversation_id="test_conv",
    )
    message = Message(message_pieces=[message_piece])

    response = await target.send_prompt_async(message=message)

    text_piece, audio_piece = response[0].message_pieces
    assert text_piece.prompt_metadata.get("interrupted") is True
    assert audio_piece.prompt_metadata.get("interrupted") is True

    await target.cleanup_target_async()


async def test_send_prompt_async_omits_interrupted_metadata_when_not_set(target):
    """A non-interrupted result must not write an interrupted key to MessagePiece metadata."""
    target._connect_async = AsyncMock(return_value=AsyncMock())
    target.send_config_async = AsyncMock()
    normal_result = RealtimeTargetResult(audio_bytes=b"full", transcripts=["hi"])
    target.send_text_async = AsyncMock(return_value=("full.wav", normal_result))

    message_piece = MessagePiece(
        original_value="Hello",
        original_value_data_type="text",
        converted_value="Hello",
        converted_value_data_type="text",
        role="user",
        conversation_id="test_conv",
    )
    message = Message(message_pieces=[message_piece])

    response = await target.send_prompt_async(message=message)

    text_piece, audio_piece = response[0].message_pieces
    assert "interrupted" not in text_piece.prompt_metadata
    assert "interrupted" not in audio_piece.prompt_metadata

    await target.cleanup_target_async()


async def test_get_system_prompt_from_conversation_with_system_message(target):
    """Test that system prompt is extracted from conversation history when present."""

    # Create a system message
    system_message = Message(
        message_pieces=[
            MessagePiece(
                role="system",
                original_value="You are a helpful assistant specialized in security.",
                converted_value="You are a helpful assistant specialized in security.",
                conversation_id="test_conversation_with_system",
            )
        ]
    )

    # Get the system prompt
    system_prompt = target._get_system_prompt_from_conversation(conversation=[system_message])

    assert system_prompt == "You are a helpful assistant specialized in security."


async def test_get_system_prompt_from_conversation_default(target):
    """Test that default system prompt is returned when no system message in conversation."""

    # Create a user message (no system message)
    user_message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Hello",
                converted_value="Hello",
                conversation_id="test_conversation_no_system",
            )
        ]
    )

    # Get the system prompt
    system_prompt = target._get_system_prompt_from_conversation(conversation=[user_message])

    assert system_prompt == "You are a helpful AI assistant"


async def test_get_system_prompt_empty_conversation(target):
    """Test that default system prompt is returned for empty conversation."""

    # Get the system prompt without any messages
    system_prompt = target._get_system_prompt_from_conversation(conversation=[])

    assert system_prompt == "You are a helpful AI assistant"


async def test_multiple_websockets_created_for_multiple_conversations(target):
    # Mock the necessary methods
    target._connect_async = AsyncMock(return_value=AsyncMock())
    target.send_config_async = AsyncMock()
    result = RealtimeTargetResult(audio_bytes=b"event1", transcripts=["event2"])
    target.send_text_async = AsyncMock(return_value=("output_audio_path", result))

    # Create mock Messages for two different conversations
    message_piece_1 = MessagePiece(
        original_value="Hello",
        original_value_data_type="text",
        converted_value="Hello",
        converted_value_data_type="text",
        role="user",
        conversation_id="conversation_1",
    )
    message_1 = Message(message_pieces=[message_piece_1])

    message_piece_2 = MessagePiece(
        original_value="Hi",
        original_value_data_type="text",
        converted_value="Hi",
        converted_value_data_type="text",
        role="user",
        conversation_id="conversation_2",
    )
    message_2 = Message(message_pieces=[message_piece_2])

    # Call the send_prompt_async method for both conversations
    await target.send_prompt_async(message=message_1)
    await target.send_prompt_async(message=message_2)

    # Assert that two different WebSocket connections were created
    assert "conversation_1" in target._existing_conversation
    assert "conversation_2" in target._existing_conversation

    # Clean up the WebSocket connections
    await target.cleanup_target_async()
    assert target._existing_conversation == {}


async def test_send_prompt_async_invalid_request(target):
    # Create a mock Message with an invalid data type
    message_piece = MessagePiece(
        original_value="Invalid",
        original_value_data_type="image_path",
        converted_value="Invalid",
        converted_value_data_type="image_path",
        role="user",
    )
    message = Message(message_pieces=[message_piece])
    with pytest.raises(ValueError) as excinfo:
        target._validate_request(normalized_conversation=[message])

    assert "This target supports only the following data types" in str(excinfo.value)
    assert "image_path" in str(excinfo.value)


async def test_send_prompt_to_target_raises_without_conversation_id(target):
    message_piece = MessagePiece(
        original_value="hello",
        original_value_data_type="text",
        converted_value="hello",
        converted_value_data_type="text",
        role="user",
        conversation_id=None,
    )
    message = Message(message_pieces=[message_piece])
    with pytest.raises(ValueError, match="requires a conversation_id"):
        await target._send_prompt_to_target_async(normalized_conversation=[message])


async def test_receive_events_empty_output(target: RealtimeTarget):
    """Test handling of response.done event with empty output array."""
    mock_connection = AsyncMock()
    conversation_id = "test_empty_output"
    target._existing_conversation[conversation_id] = mock_connection

    # Mock the event with empty output - simulates server error
    mock_event = MagicMock()
    mock_event.type = "response.done"
    mock_event.response.status = "failed"

    # Create nested error structure matching the actual API response
    mock_error = MagicMock()
    mock_error.type = "server_error"
    mock_error.message = "The server had an error processing your request"

    mock_status_details = MagicMock()
    mock_status_details.error = mock_error

    mock_event.response.status_details = mock_status_details
    mock_event.response.output = []

    # Mock connection to yield our test event
    mock_connection.__aiter__.return_value = [mock_event]

    with pytest.raises(ServerErrorException, match=r"\[server_error\] The server had an error processing your request"):
        await target.receive_events_async(conversation_id)


async def test_receive_events_response_done_no_transcript_validation(target):
    """Test that response.done completes normally even with no audio or transcript,
    as long as it belongs to the current turn (preceded by other events)."""
    mock_connection = AsyncMock()
    conversation_id = "test_response_done"
    target._existing_conversation[conversation_id] = mock_connection

    # A lifecycle event that precedes response.done, confirming it belongs to this turn
    mock_lifecycle_event = MagicMock()
    mock_lifecycle_event.type = "response.created"

    # Mock response.done event with no audio
    mock_event = MagicMock()
    mock_event.type = "response.done"
    mock_event.response.status = "success"

    # Lifecycle event arrives first, then response.done
    mock_connection.__aiter__.return_value = [mock_lifecycle_event, mock_event]

    # Should complete successfully — response.done is not stale because it was preceded by another event
    result = await target.receive_events_async(conversation_id)
    assert result is not None
    assert len(result.transcripts) == 0
    assert result.audio_bytes == b""


async def test_receive_events_audio_buffer_only(target):
    """Test receiving only audio data with no transcript."""
    mock_connection = AsyncMock()
    conversation_id = "test_audio_only"
    target._existing_conversation[conversation_id] = mock_connection

    # Create audio delta event
    mock_audio_event = MagicMock()
    mock_audio_event.type = "response.audio.delta"
    mock_audio_event.delta = "ZHVtbXlhdWRpbw=="  # base64 for "dummyaudio"

    # Create audio done event
    mock_done_event = MagicMock()
    mock_done_event.type = "response.audio.done"

    # Mock connection to yield both events
    mock_connection.__aiter__.return_value = [mock_audio_event, mock_done_event]

    result = await target.receive_events_async(conversation_id)

    # Should have audio buffer but no transcript
    assert len(result.transcripts) == 0
    assert result.audio_bytes == b"dummyaudio"


async def test_receive_events_error_event(target):
    """Test handling of direct error event."""
    mock_connection = AsyncMock()
    conversation_id = "test_error_event"
    target._existing_conversation[conversation_id] = mock_connection

    # Mock error event
    mock_event = MagicMock()
    mock_event.type = "error"
    mock_event.error.type = "invalid_request_error"
    mock_event.error.message = "Invalid request"

    # Mock connection to yield test event
    mock_connection.__aiter__.return_value = [mock_event]

    # Error events now raise RuntimeError with details
    with pytest.raises(RuntimeError, match=r"Server error: \[invalid_request_error\] Invalid request"):
        await target.receive_events_async(conversation_id)


async def test_receive_events_connection_closed(target):
    """Test handling of connection closing unexpectedly."""
    mock_connection = AsyncMock()
    conversation_id = "test_connection_closed"
    target._existing_conversation[conversation_id] = mock_connection

    # Mock connection that returns empty list (simulates closed connection)
    mock_connection.__aiter__.return_value = []

    result = await target.receive_events_async(conversation_id)
    assert len(result.transcripts) == 0
    assert result.audio_bytes == b""


async def test_receive_events_with_audio_and_transcript(target):
    """Test successful processing of both audio data and transcript."""
    mock_connection = AsyncMock()
    conversation_id = "test_success"
    target._existing_conversation[conversation_id] = mock_connection

    # Create audio delta event
    mock_audio_event = MagicMock()
    mock_audio_event.type = "response.audio.delta"
    mock_audio_event.delta = "ZHVtbXlhdWRpbw=="  # base64 for "dummyaudio"

    # Create audio done event
    mock_audio_done_event = MagicMock()
    mock_audio_done_event.type = "response.audio.done"

    # Create transcript delta events (transcripts now come from deltas, not response.done)
    mock_transcript_delta1 = MagicMock()
    mock_transcript_delta1.type = "response.audio_transcript.delta"
    mock_transcript_delta1.delta = "Hello, "

    mock_transcript_delta2 = MagicMock()
    mock_transcript_delta2.type = "response.audio_transcript.delta"
    mock_transcript_delta2.delta = "this is a test transcript."

    # Create response.done event (no longer extracts transcript)
    mock_done_event = MagicMock()
    mock_done_event.type = "response.done"
    mock_done_event.response.status = "success"

    # Mock connection to yield all events
    mock_connection.__aiter__.return_value = [
        mock_audio_event,
        mock_transcript_delta1,
        mock_transcript_delta2,
        mock_audio_done_event,
        mock_done_event,
    ]

    result = await target.receive_events_async(conversation_id)

    # Result should have both audio buffer and transcript from deltas
    assert len(result.transcripts) == 2
    assert result.audio_bytes == b"dummyaudio"
    assert result.transcripts[0] == "Hello, "
    assert result.transcripts[1] == "this is a test transcript."


async def test_receive_events_soft_finishes_after_audio_done(target):
    """Atomic receiving returns accumulated deltas when audio.done is followed by its grace-period timeout."""
    mock_connection = AsyncMock()
    conversation_id = "test_soft_finish"
    target._existing_conversation[conversation_id] = mock_connection

    async def _events():
        yield _scripted_event("response.output_audio.delta", delta=base64.b64encode(b"audio").decode("ascii"))
        yield _scripted_event("response.output_audio_transcript.delta", delta="partial")
        yield _scripted_event("response.output_audio.done")
        raise asyncio.TimeoutError

    mock_connection.__aiter__.side_effect = _events

    result = await target.receive_events_async(conversation_id)

    assert result.audio_bytes == b"audio"
    assert result.transcripts == ["partial"]


async def test_receive_events_audio_done_deadline_is_not_extended_by_stale_or_noisy_events(target):
    """Stale, duplicate, and unrelated events consume rather than reset the soft-finish grace period."""
    mock_connection = AsyncMock()
    conversation_id = "test_bounded_soft_finish"
    target._existing_conversation[conversation_id] = mock_connection
    mock_connection.__aiter__.return_value = [
        _scripted_event("response.done", **{"response.status": "success"}),
        _scripted_event("response.output_audio.done"),
        _scripted_event("provider.noise"),
        _scripted_event("response.output_audio.done"),
        _scripted_event("session.updated"),
    ]
    observed_timeouts: list[float | None] = []
    wait_for = asyncio.wait_for

    async def _record_wait_for(awaitable: Any, *, timeout: float | None) -> Any:
        observed_timeouts.append(timeout)
        return await wait_for(awaitable, timeout=timeout)

    mock_loop = MagicMock()
    mock_loop.time.side_effect = [100.0, 100.25, 100.5, 100.75, 101.0]
    with (
        patch(
            "pyrit.prompt_target.openai.openai_realtime_target.asyncio.get_running_loop",
            return_value=mock_loop,
        ),
        patch(
            "pyrit.prompt_target.openai.openai_realtime_target.asyncio.wait_for",
            side_effect=_record_wait_for,
        ),
    ):
        result = await target.receive_events_async(conversation_id)

    assert result.audio_bytes == b""
    assert observed_timeouts == [None, None, 0.75, 0.5, 0.25, 0.0]


async def test_receive_events_accepts_response_done_before_audio_done_deadline(target):
    """A terminal event arriving within the remaining grace period still completes normally."""
    mock_connection = AsyncMock()
    conversation_id = "test_late_terminal_event"
    target._existing_conversation[conversation_id] = mock_connection
    mock_connection.__aiter__.return_value = [
        _scripted_event("response.output_audio.done"),
        _scripted_event("response.done", **{"response.status": "success"}),
    ]
    observed_timeouts: list[float | None] = []
    wait_for = asyncio.wait_for

    async def _record_wait_for(awaitable: Any, *, timeout: float | None) -> Any:
        observed_timeouts.append(timeout)
        return await wait_for(awaitable, timeout=timeout)

    mock_loop = MagicMock()
    mock_loop.time.side_effect = [100.0, 100.99]
    with (
        patch(
            "pyrit.prompt_target.openai.openai_realtime_target.asyncio.get_running_loop",
            return_value=mock_loop,
        ),
        patch(
            "pyrit.prompt_target.openai.openai_realtime_target.asyncio.wait_for",
            side_effect=_record_wait_for,
        ),
    ):
        result = await target.receive_events_async(conversation_id)

    assert result.audio_bytes == b""
    assert observed_timeouts[0] is None
    assert observed_timeouts[1] == pytest.approx(0.01)


async def test_receive_events_cancellation_during_audio_done_grace_propagates(target):
    """Cancellation while waiting within the grace period is never converted into a soft finish."""
    mock_connection = AsyncMock()
    conversation_id = "test_grace_cancellation"
    target._existing_conversation[conversation_id] = mock_connection

    async def _events() -> AsyncIterator[Any]:
        yield _scripted_event("response.output_audio.done")
        raise asyncio.CancelledError

    mock_connection.__aiter__.side_effect = _events
    mock_loop = MagicMock()
    mock_loop.time.side_effect = [100.0, 100.25]
    with patch(
        "pyrit.prompt_target.openai.openai_realtime_target.asyncio.get_running_loop",
        return_value=mock_loop,
    ):
        with pytest.raises(asyncio.CancelledError):
            await target.receive_events_async(conversation_id)


async def test_receive_events_ignores_late_events_from_soft_finished_response(target):
    """Late prior-turn deltas and completion events must not contaminate the next response."""
    mock_connection = AsyncMock()
    conversation_id = "test_response_ownership"
    target._existing_conversation[conversation_id] = mock_connection

    async def _first_response_events() -> AsyncIterator[Any]:
        yield _scripted_event("response.created", **{"response.id": "response-1"})
        yield _scripted_event(
            "response.audio.delta",
            response_id="response-1",
            delta=base64.b64encode(b"first").decode("ascii"),
        )
        yield _scripted_event("response.audio.done", response_id="response-1")
        raise asyncio.TimeoutError

    async def _second_response_events() -> AsyncIterator[Any]:
        yield _scripted_event(
            "response.audio_transcript.delta",
            response_id="response-1",
            delta="late first transcript",
        )
        yield _scripted_event("response.done", **{"response.id": "response-1", "response.status": "success"})
        yield _scripted_event("response.created", **{"response.id": "response-2"})
        yield _scripted_event(
            "response.audio.delta",
            response_id="response-2",
            delta=base64.b64encode(b"second").decode("ascii"),
        )
        yield _scripted_event(
            "response.audio_transcript.delta",
            response_id="response-2",
            delta="second transcript",
        )
        yield _scripted_event("response.audio.done", response_id="response-2")
        yield _scripted_event("response.done", **{"response.id": "response-2", "response.status": "success"})

    event_streams = iter([_first_response_events(), _second_response_events()])
    mock_connection.__aiter__.side_effect = lambda: next(event_streams)

    first_result = await target.receive_events_async(conversation_id)
    second_result = await target.receive_events_async(conversation_id)

    assert first_result.audio_bytes == b"first"
    assert second_result.audio_bytes == b"second"
    assert second_result.transcripts == ["second transcript"]


async def test_receive_events_connection_close_soft_finishes_with_audio(target):
    """Atomic receiving returns accumulated audio when the provider closes before response.done."""

    class ConnectionClosedTestError(Exception):
        pass

    mock_connection = AsyncMock()
    conversation_id = "test_connection_close_with_audio"
    target._existing_conversation[conversation_id] = mock_connection

    async def _events():
        yield _scripted_event("response.audio.delta", delta=base64.b64encode(b"partial").decode("ascii"))
        raise ConnectionClosedTestError("closed")

    mock_connection.__aiter__.side_effect = _events

    result = await target.receive_events_async(conversation_id)

    assert result.audio_bytes == b"partial"


async def test_receive_events_connection_close_without_audio_raises(target):
    """Atomic receiving must not hide a connection failure before any response audio arrives."""

    class ConnectionClosedTestError(Exception):
        pass

    mock_connection = AsyncMock()
    conversation_id = "test_connection_close_without_audio"
    target._existing_conversation[conversation_id] = mock_connection

    async def _events():
        raise ConnectionClosedTestError("closed")
        yield  # pragma: no cover

    mock_connection.__aiter__.side_effect = _events

    with pytest.raises(ConnectionClosedTestError, match="closed"):
        await target.receive_events_async(conversation_id)


async def test_receive_events_timeout_before_audio_done_raises(target):
    """Atomic receiving only treats a timeout as completion after an audio.done event."""
    mock_connection = AsyncMock()
    conversation_id = "test_timeout_without_audio_done"
    target._existing_conversation[conversation_id] = mock_connection

    async def _events():
        raise asyncio.TimeoutError
        yield  # pragma: no cover

    mock_connection.__aiter__.side_effect = _events

    with pytest.raises(asyncio.TimeoutError):
        await target.receive_events_async(conversation_id)


async def test_receive_events_ignores_non_response_and_empty_delta_events(target):
    """Lifecycle, unknown, text-done, and empty transcript events do not mutate an atomic result."""
    mock_connection = AsyncMock()
    conversation_id = "test_ignored_events"
    target._existing_conversation[conversation_id] = mock_connection

    mock_connection.__aiter__.return_value = [
        _scripted_event("response.audio_transcript.delta", delta=""),
        _scripted_event("response.output_text.done"),
        _scripted_event("provider.new_event"),
        _scripted_event("response.done", **{"response.status": "success"}),
    ]

    result = await target.receive_events_async(conversation_id)

    assert result.audio_bytes == b""
    assert result.transcripts == []


async def test_multi_turn_reuses_connection(target):
    """Test that multiple turns in the same conversation reuse the same connection.

    This ensures that the server-side conversation context is preserved.
    """
    mock_connection = AsyncMock()
    target._connect_async = AsyncMock(return_value=mock_connection)
    target.send_config_async = AsyncMock()
    result = RealtimeTargetResult(audio_bytes=b"audio", transcripts=["response"])
    target.send_text_async = AsyncMock(return_value=("output.wav", result))

    conversation_id = "multi_turn_convo"

    # Send first turn
    message_piece_1 = MessagePiece(
        original_value="Turn 1",
        original_value_data_type="text",
        converted_value="Turn 1",
        converted_value_data_type="text",
        role="user",
        conversation_id=conversation_id,
    )
    await target.send_prompt_async(message=Message(message_pieces=[message_piece_1]))

    # Send second turn in the same conversation
    message_piece_2 = MessagePiece(
        original_value="Turn 2",
        original_value_data_type="text",
        converted_value="Turn 2",
        converted_value_data_type="text",
        role="user",
        conversation_id=conversation_id,
    )
    await target.send_prompt_async(message=Message(message_pieces=[message_piece_2]))

    # Connection should only be created once for the conversation
    target._connect_async.assert_called_once_with(conversation_id=conversation_id)
    target.send_config_async.assert_called_once()

    # Both turns should use the same connection
    assert target._existing_conversation[conversation_id] == mock_connection

    # send_text_async should have been called twice (once per turn)
    assert target.send_text_async.call_count == 2

    await target.cleanup_target_async()


async def test_receive_events_skips_stale_response_done(target):
    """Test that a stale response.done (with no audio) from a prior turn's soft-finish
    is skipped, and the current turn's events are processed normally."""
    mock_connection = AsyncMock()
    conversation_id = "test_stale_response_done"
    target._existing_conversation[conversation_id] = mock_connection

    # Stale response.done left over from previous turn's soft-finish — no audio for current turn yet
    stale_done_event = MagicMock()
    stale_done_event.type = "response.done"
    stale_done_event.response.status = "success"

    # Current turn's actual events
    audio_delta_event = MagicMock()
    audio_delta_event.type = "response.audio.delta"
    audio_delta_event.delta = "ZHVtbXlhdWRpbw=="  # base64 for "dummyaudio"

    transcript_delta_event = MagicMock()
    transcript_delta_event.type = "response.audio_transcript.delta"
    transcript_delta_event.delta = "hello"

    audio_done_event = MagicMock()
    audio_done_event.type = "response.audio.done"

    real_done_event = MagicMock()
    real_done_event.type = "response.done"
    real_done_event.response.status = "success"

    # Stale event comes first, then the real turn's events
    mock_connection.__aiter__.return_value = [
        stale_done_event,
        audio_delta_event,
        transcript_delta_event,
        audio_done_event,
        real_done_event,
    ]

    result = await target.receive_events_async(conversation_id)

    # Should have processed through to the real response.done with actual audio
    assert result.audio_bytes == b"dummyaudio"
    assert result.transcripts == ["hello"]


# ---------------------------------------------------------------------------
# Chunk 1 — ServerVadConfig + session config
# ---------------------------------------------------------------------------


def test_session_config_omits_turn_detection_when_vad_disabled(target):
    """Default construction must not emit a turn_detection block; pins atomic flow."""
    config = target._set_system_prompt_and_config_vars(system_prompt="test prompt")

    assert "turn_detection" not in config["audio"]["input"]
    assert config["instructions"] == "test prompt"


def test_session_config_emits_server_vad_block_with_defaults(target):
    """Passing ``server_vad=ServerVadConfig()`` must emit the default tuning."""
    config = target._set_system_prompt_and_config_vars(system_prompt="test prompt", server_vad=ServerVadConfig())

    turn_detection = config["audio"]["input"]["turn_detection"]
    assert turn_detection == {
        "type": "server_vad",
        "threshold": 0.4,
        "prefix_padding_ms": 200,
        "silence_duration_ms": 1500,
        "create_response": True,
        "interrupt_response": True,
    }


def test_session_config_honors_custom_vad_tuning(target):
    """Passing a ServerVadConfig must flow through to the emitted turn_detection block."""
    turn_detection = target._set_system_prompt_and_config_vars(
        system_prompt="x",
        server_vad=ServerVadConfig(threshold=0.7, prefix_padding_ms=350, silence_duration_ms=800),
    )["audio"]["input"]["turn_detection"]

    assert turn_detection["threshold"] == 0.7
    assert turn_detection["prefix_padding_ms"] == 350
    assert turn_detection["silence_duration_ms"] == 800


@pytest.mark.parametrize(
    "kwargs",
    [
        {"threshold": -0.1},
        {"threshold": 1.5},
        {"prefix_padding_ms": -1},
        {"silence_duration_ms": -1},
    ],
)
def test_server_vad_config_rejects_invalid_values(kwargs):
    """ServerVadConfig must reject out-of-range tuning values at construction."""
    with pytest.raises(ValueError):
        ServerVadConfig(**kwargs)


# ---- Wire primitives for streaming attacks ---------------------------------------------------


def _turn_state(*, response_id: str | None = "resp_abc", item_id: str | None = "item_xyz") -> RealtimeTurnState:
    """Build a turn state with the named ids preset; completion future is unused by cancel tests."""
    return RealtimeTurnState(
        completion=asyncio.get_event_loop().create_future(),
        is_responding=True,
        last_response_id=response_id,
        current_item_id=item_id,
    )


def _make_dispatcher(connection):
    """Build an _OpenAIRealtimeDispatcher around the given mock connection."""
    return _OpenAIRealtimeDispatcher(connection=connection)


@pytest.mark.parametrize(
    ("event_type", "expected_kind"),
    [
        ("response.done", _OpenAIRealtimeEventKind.RESPONSE_DONE),
        ("error", _OpenAIRealtimeEventKind.ERROR),
        ("response.audio.delta", _OpenAIRealtimeEventKind.AUDIO_DELTA),
        ("response.output_audio.delta", _OpenAIRealtimeEventKind.AUDIO_DELTA),
        ("response.audio.done", _OpenAIRealtimeEventKind.AUDIO_DONE),
        ("response.output_audio.done", _OpenAIRealtimeEventKind.AUDIO_DONE),
        ("response.audio_transcript.delta", _OpenAIRealtimeEventKind.TRANSCRIPT_DELTA),
        ("response.output_audio_transcript.delta", _OpenAIRealtimeEventKind.TRANSCRIPT_DELTA),
        ("response.output_text.done", _OpenAIRealtimeEventKind.OUTPUT_TEXT_DONE),
        ("response.created", _OpenAIRealtimeEventKind.RESPONSE_CREATED),
        ("response.output_item.added", _OpenAIRealtimeEventKind.OUTPUT_ITEM),
        ("response.output_item.created", _OpenAIRealtimeEventKind.OUTPUT_ITEM),
        ("input_audio_buffer.speech_started", _OpenAIRealtimeEventKind.SPEECH_STARTED),
        ("input_audio_buffer.committed", _OpenAIRealtimeEventKind.INPUT_COMMITTED),
        ("session.updated", _OpenAIRealtimeEventKind.LIFECYCLE),
        ("provider.new_event", _OpenAIRealtimeEventKind.OTHER),
    ],
)
def test_realtime_event_router_classifies_provider_events(event_type, expected_kind):
    assert _OpenAIRealtimeEventRouter.classify_event(event_type) is expected_kind


@pytest.mark.parametrize(
    ("event_kind", "expected"),
    [
        (_OpenAIRealtimeEventKind.RESPONSE_CREATED, True),
        (_OpenAIRealtimeEventKind.OUTPUT_ITEM, True),
        (_OpenAIRealtimeEventKind.SPEECH_STARTED, True),
        (_OpenAIRealtimeEventKind.INPUT_COMMITTED, True),
        (_OpenAIRealtimeEventKind.LIFECYCLE, True),
        (_OpenAIRealtimeEventKind.RESPONSE_DONE, False),
        (_OpenAIRealtimeEventKind.OTHER, False),
    ],
)
def test_realtime_event_router_identifies_atomic_lifecycle_events(event_kind, expected):
    assert _OpenAIRealtimeEventRouter.is_lifecycle_event(event_kind) is expected


def test_realtime_event_router_collects_audio_and_transcript_deltas():
    audio_buffer = bytearray()
    transcripts: list[str] = []

    _OpenAIRealtimeEventRouter.collect_response_delta(
        event=_scripted_event("response.output_audio.delta", delta=base64.b64encode(b"audio").decode("ascii")),
        event_kind=_OpenAIRealtimeEventKind.AUDIO_DELTA,
        audio_buffer=audio_buffer,
        transcripts=transcripts,
    )
    _OpenAIRealtimeEventRouter.collect_response_delta(
        event=_scripted_event("response.output_audio_transcript.delta", delta="hello"),
        event_kind=_OpenAIRealtimeEventKind.TRANSCRIPT_DELTA,
        audio_buffer=audio_buffer,
        transcripts=transcripts,
    )

    assert bytes(audio_buffer) == b"audio"
    assert transcripts == ["hello"]


@pytest.mark.parametrize(
    ("event_kind", "delta"),
    [
        (_OpenAIRealtimeEventKind.AUDIO_DELTA, ""),
        (_OpenAIRealtimeEventKind.TRANSCRIPT_DELTA, ""),
        (_OpenAIRealtimeEventKind.OTHER, base64.b64encode(b"ignored").decode("ascii")),
    ],
)
def test_realtime_event_router_ignores_empty_or_unrelated_deltas(event_kind, delta):
    audio_buffer = bytearray(b"existing")
    transcripts = ["existing"]

    _OpenAIRealtimeEventRouter.collect_response_delta(
        event=_scripted_event("test", delta=delta),
        event_kind=event_kind,
        audio_buffer=audio_buffer,
        transcripts=transcripts,
    )

    assert bytes(audio_buffer) == b"existing"
    assert transcripts == ["existing"]


async def test_cancel_does_not_send_response_cancel():
    """_cancel_async must NOT send response.cancel (server auto-cancels on speech detection)."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = _turn_state(response_id="resp_42")
    state.delivered_audio.extend(b"\x00" * 4800)

    await dispatcher._cancel_async(state=state)

    connection.response.cancel.assert_not_awaited()


async def test_cancel_truncates_to_delivered_audio_ms():
    """Truncate must be called with audio_end_ms computed from delivered_audio length."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = _turn_state(item_id="item_99")
    # 4800 delivered bytes / 48 bytes-per-ms = 100ms
    state.delivered_audio.extend(b"\x00" * 4800)

    await dispatcher._cancel_async(state=state)

    connection.conversation.item.truncate.assert_awaited_once_with(
        item_id="item_99",
        content_index=0,
        audio_end_ms=100,
    )
    assert state.interrupted is True


async def test_cancel_only_truncates_no_response_cancel(caplog):
    """_cancel_async must only truncate, not send response.cancel (server handles cancellation)."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = _turn_state(item_id="item_1")
    state.delivered_audio.extend(b"\x00" * 4800)

    await dispatcher._cancel_async(state=state)

    assert state.interrupted is True
    connection.conversation.item.truncate.assert_awaited_once()
    connection.response.cancel.assert_not_awaited()


async def test_cancel_marks_interrupted_when_truncate_raises(caplog):
    """A failed conversation.item.truncate must log a warning and still flip state.interrupted."""
    connection = AsyncMock()
    connection.conversation.item.truncate.side_effect = RuntimeError("boom")
    dispatcher = _make_dispatcher(connection)
    state = _turn_state()

    await dispatcher._cancel_async(state=state)

    assert state.interrupted is True
    assert any(
        "conversation.item.truncate failed" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    )


async def test_cancel_without_current_item_only_marks_interrupted():
    """A turn interrupted before an output item exists cannot be truncated but is still marked."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = _turn_state(item_id=None)

    await dispatcher._cancel_async(state=state)

    connection.conversation.item.truncate.assert_not_awaited()
    assert state.interrupted is True


def _scripted_event(event_type, **fields):
    """Build a MagicMock event with the named type plus any extra attribute paths."""
    event = MagicMock()
    event.type = event_type
    for path, value in fields.items():
        # Allow dotted attribute paths like "response.id" by walking nested MagicMocks.
        parts = path.split(".")
        target_attr = event
        for part in parts[:-1]:
            target_attr = getattr(target_attr, part)
        setattr(target_attr, parts[-1], value)
    return event


async def test_route_event_happy_path_resolves_completion_with_assembled_result():
    """response.created -> output_item.added -> audio.delta -> transcript.delta -> response.done."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    await dispatcher._route_event_async(event=_scripted_event("response.created", **{"response.id": "r1"}), state=state)
    await dispatcher._route_event_async(
        event=_scripted_event("response.output_item.added", **{"item.id": "i1"}), state=state
    )
    await dispatcher._route_event_async(
        event=_scripted_event("response.audio.delta", delta=base64.b64encode(b"\xaa" * 4800).decode("ascii")),
        state=state,
    )
    await dispatcher._route_event_async(
        event=_scripted_event("response.audio_transcript.delta", delta="hello "), state=state
    )
    await dispatcher._route_event_async(
        event=_scripted_event("response.audio_transcript.delta", delta="world"), state=state
    )
    await dispatcher._route_event_async(event=_scripted_event("response.done", **{"response.id": "r1"}), state=state)

    assert state.completion.done()
    result = state.completion.result()
    assert result.audio_bytes == b"\xaa" * 4800
    assert result.transcripts == ["hello ", "world"]
    assert state.interrupted is False


@pytest.mark.parametrize(
    ("audio_event_type", "transcript_event_type"),
    [
        ("response.audio.delta", "response.audio_transcript.delta"),
        ("response.output_audio.delta", "response.output_audio_transcript.delta"),
    ],
)
async def test_route_event_accumulates_response_aliases(audio_event_type, transcript_event_type):
    dispatcher = _make_dispatcher(AsyncMock())
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    await dispatcher._route_event_async(
        event=_scripted_event(audio_event_type, delta=base64.b64encode(b"audio").decode("ascii")),
        state=state,
    )
    await dispatcher._route_event_async(
        event=_scripted_event(transcript_event_type, delta="transcript"),
        state=state,
    )

    assert bytes(state.delivered_audio) == b"audio"
    assert state.delivered_transcripts == ["transcript"]
    assert not state.completion.done()


async def test_route_event_audio_done_does_not_complete_streaming_turn():
    """Streaming waits for response.done or barge-in instead of using the atomic soft-finish policy."""
    dispatcher = _make_dispatcher(AsyncMock())
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    await dispatcher._route_event_async(event=_scripted_event("response.output_audio.done"), state=state)

    assert not state.completion.done()


async def test_route_event_missing_optional_payloads_leave_ids_unset():
    """Missing speech timing, response, and output item payloads do not synthesize state identifiers."""
    dispatcher = _make_dispatcher(AsyncMock())
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    await dispatcher._route_event_async(
        event=_scripted_event("input_audio_buffer.speech_started", audio_start_ms=None),
        state=state,
    )
    await dispatcher._route_event_async(
        event=_scripted_event("response.created", response=None),
        state=state,
    )
    await dispatcher._route_event_async(
        event=_scripted_event("response.output_item.added", item=None),
        state=state,
    )

    assert dispatcher._pending_speech_start_ms is None
    assert state.is_responding is True
    assert state.last_response_id is None
    assert state.current_item_id is None


async def test_route_event_drops_output_without_active_turn():
    dispatcher = _make_dispatcher(AsyncMock())

    await dispatcher._route_event_async(
        event=_scripted_event("response.audio.delta", delta=base64.b64encode(b"ignored").decode("ascii")),
        state=None,
    )


async def test_route_event_drops_output_for_completed_turn():
    dispatcher = _make_dispatcher(AsyncMock())
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    state.completion.set_result(RealtimeTargetResult())

    await dispatcher._route_event_async(
        event=_scripted_event("response.audio.delta", delta=base64.b64encode(b"ignored").decode("ascii")),
        state=state,
    )

    assert state.delivered_audio == bytearray()


async def test_route_event_speech_started_while_responding_cancels_and_resolves_interrupted():
    """speech_started during a response triggers cancel and resolves with interrupted=True."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    await dispatcher._route_event_async(event=_scripted_event("response.created", **{"response.id": "r1"}), state=state)
    await dispatcher._route_event_async(
        event=_scripted_event("response.output_item.added", **{"item.id": "i1"}), state=state
    )
    await dispatcher._route_event_async(
        event=_scripted_event("response.audio.delta", delta=base64.b64encode(b"\xbb" * 2400).decode("ascii")),
        state=state,
    )
    await dispatcher._route_event_async(event=_scripted_event("input_audio_buffer.speech_started"), state=state)

    connection.response.cancel.assert_not_awaited()
    connection.conversation.item.truncate.assert_awaited_once_with(
        item_id="i1",
        content_index=0,
        audio_end_ms=50,  # 2400 / 48
    )
    result = state.completion.result()
    assert result.audio_bytes == b"\xbb" * 2400
    assert result.interrupted is True
    assert state.interrupted is True


async def test_route_event_stale_response_done_after_cancel_is_dropped():
    """A response.done with a stale response_id must not re-resolve a completed future."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    # Pretend a turn just resolved as interrupted on response_id r1.
    state.last_response_id = "r1"
    state.completion.set_result(RealtimeTargetResult())

    # Late response.done for r1 arrives; router must not raise InvalidStateError.
    await dispatcher._route_event_async(event=_scripted_event("response.done", **{"response.id": "r1"}), state=state)


async def test_route_event_stale_response_done_does_not_resolve_active_turn():
    """An active turn ignores response.done from a different response id."""
    dispatcher = _make_dispatcher(AsyncMock())
    state = RealtimeTurnState(
        completion=asyncio.get_event_loop().create_future(),
        is_responding=True,
        last_response_id="current",
    )

    await dispatcher._route_event_async(
        event=_scripted_event("response.done", **{"response.id": "stale"}),
        state=state,
    )

    assert not state.completion.done()
    assert state.is_responding is True


async def test_route_event_error_resolves_with_exception():
    """error events resolve the completion future via set_exception."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    await dispatcher._route_event_async(
        event=_scripted_event("error", **{"error.message": "rate limited"}), state=state
    )

    with pytest.raises(RuntimeError, match="rate limited"):
        state.completion.result()


async def test_route_event_ignores_benign_empty_commit_error():
    """An input_audio_buffer_commit_empty error is benign and must not fail the active turn."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    await dispatcher._route_event_async(
        event=_scripted_event(
            "error",
            **{"error.code": "input_audio_buffer_commit_empty", "error.message": "buffer too small"},
        ),
        state=state,
    )

    assert not state.completion.done()


async def test_route_event_speech_started_without_responding_is_noop():
    """speech_started before a response is in flight does not call cancel or resolve."""
    connection = AsyncMock()
    dispatcher = _make_dispatcher(connection)
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    await dispatcher._route_event_async(event=_scripted_event("input_audio_buffer.speech_started"), state=state)

    connection.response.cancel.assert_not_awaited()
    connection.conversation.item.truncate.assert_not_awaited()
    assert not state.completion.done()
    assert state.interrupted is False


async def test_route_event_committed_event_fires_user_audio_callback():
    """input_audio_buffer.committed must fire the registered on_user_audio_committed callback."""
    connection = AsyncMock()
    received: list[Any] = []

    async def on_committed(event):
        received.append(event)

    dispatcher = _OpenAIRealtimeDispatcher(connection=connection, on_user_audio_committed=on_committed)

    await dispatcher._route_event_async(
        event=_scripted_event("input_audio_buffer.committed", item_id="raw_item_42", audio_start_ms=1234),
        state=None,
    )
    # Background callback task may not have run yet; yield until it does.
    for _ in range(20):
        if received:
            break
        await asyncio.sleep(0.01)

    assert len(received) == 1
    assert received[0].item_id == "raw_item_42"
    assert received[0].audio_start_ms == 1234


async def test_route_event_committed_event_without_callback_is_noop():
    """A committed event with no callback configured must be ignored quietly."""
    connection = AsyncMock()
    dispatcher = _OpenAIRealtimeDispatcher(connection=connection)  # no callback

    # Must not raise.
    await dispatcher._route_event_async(
        event=_scripted_event("input_audio_buffer.committed", item_id="raw_item_99"),
        state=None,
    )


async def test_route_event_committed_without_item_id_is_ignored():
    received: list[CommittedEvent] = []

    async def on_committed(event: CommittedEvent) -> None:
        received.append(event)

    dispatcher = _OpenAIRealtimeDispatcher(connection=AsyncMock(), on_user_audio_committed=on_committed)

    await dispatcher._route_event_async(
        event=_scripted_event("input_audio_buffer.committed", item_id=None),
        state=None,
    )
    await asyncio.sleep(0)

    assert received == []


async def test_route_event_speech_started_audio_start_propagates_to_commit():
    """speech_started's audio_start_ms is captured and attached to the next CommittedEvent.

    The OpenAI Realtime server omits audio_start_ms from the input_audio_buffer.committed
    event but reports it on speech_started. The dispatcher bridges the two so callbacks
    receive the value reliably.
    """
    received: list[CommittedEvent] = []

    async def on_committed(event: CommittedEvent) -> None:
        received.append(event)

    connection = AsyncMock()
    dispatcher = _OpenAIRealtimeDispatcher(connection=connection, on_user_audio_committed=on_committed)

    await dispatcher._route_event_async(
        event=_scripted_event("input_audio_buffer.speech_started", audio_start_ms=8536),
        state=None,
    )
    await dispatcher._route_event_async(
        event=_scripted_event("input_audio_buffer.committed", item_id="raw_99", audio_start_ms=None),
        state=None,
    )
    for _ in range(20):
        if received:
            break
        await asyncio.sleep(0.01)

    assert len(received) == 1
    assert received[0].item_id == "raw_99"
    assert received[0].audio_start_ms == 8536


async def test_route_event_pending_speech_start_resets_after_commit():
    """After commit fires, the dispatcher clears its captured speech_start so a later
    commit (e.g. for a turn whose speech_started never fired) doesn't see stale data."""
    received: list[CommittedEvent] = []

    async def on_committed(event: CommittedEvent) -> None:
        received.append(event)

    connection = AsyncMock()
    dispatcher = _OpenAIRealtimeDispatcher(connection=connection, on_user_audio_committed=on_committed)

    await dispatcher._route_event_async(
        event=_scripted_event("input_audio_buffer.speech_started", audio_start_ms=500),
        state=None,
    )
    await dispatcher._route_event_async(
        event=_scripted_event("input_audio_buffer.committed", item_id="i1", audio_start_ms=None),
        state=None,
    )
    # Second commit without a prior speech_started: must NOT reuse the 500 captured above.
    await dispatcher._route_event_async(
        event=_scripted_event("input_audio_buffer.committed", item_id="i2", audio_start_ms=None),
        state=None,
    )
    for _ in range(20):
        if len(received) >= 2:
            break
        await asyncio.sleep(0.01)

    assert len(received) == 2
    assert received[0].audio_start_ms == 500
    assert received[1].audio_start_ms is None


# ---- streaming wiring & config ------------------------------------------------


def test_sample_rate_hz_class_constant():
    """SAMPLE_RATE_HZ is the single source of truth for the realtime PCM sample rate."""
    assert RealtimeTarget.SAMPLE_RATE_HZ == 24000


# ---- send_prompt audio routing -------------------------------------------------


def _write_wav(
    path: Any,
    *,
    rate: int = 24000,
    channels: int = 1,
    sampwidth: int = 2,
    pcm: bytes = b"\x00" * 96,
) -> str:
    """Write a small WAV file at ``path`` and return the path as a string."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(pcm)
    return str(path)


async def test_send_audio_async_reads_wav_off_event_loop(target, tmp_path):
    connection = AsyncMock()
    target._existing_conversation["conv"] = connection
    target.receive_events_async = AsyncMock(
        return_value=RealtimeTargetResult(audio_bytes=b"response", transcripts=["transcript"])
    )
    target.send_response_create_async = AsyncMock()
    target.save_audio_async = AsyncMock(return_value="output.wav")

    pcm = b"\x01\x02" * 8
    wav_path = _write_wav(tmp_path / "input.wav", pcm=pcm)
    with patch(
        "pyrit.prompt_target.openai.openai_realtime_target.asyncio.to_thread",
        new_callable=AsyncMock,
        wraps=asyncio.to_thread,
    ) as to_thread_mock:
        output_path, _ = await target.send_audio_async(filename=wav_path, conversation_id="conv")

    assert output_path == "output.wav"
    assert to_thread_mock.await_args.args[1] == wav_path
    connection.conversation.item.create.assert_awaited_once_with(
        item={
            "type": "message",
            "role": "user",
            "content": [{"type": "input_audio", "audio": base64.b64encode(pcm).decode("utf-8")}],
        }
    )
    target.save_audio_async.assert_awaited_once_with(b"response", 1, 2, 24000)


async def test_send_prompt_audio_path_calls_send_audio_async(target, tmp_path):
    """An audio_path message is routed through the atomic send_audio_async path."""
    wav_path = _write_wav(tmp_path / "in.wav")
    piece = MessagePiece(
        role="user",
        original_value=wav_path,
        original_value_data_type="audio_path",
        converted_value=wav_path,
        converted_value_data_type="audio_path",
        conversation_id="conv-A",
    )
    message = Message(message_pieces=[piece])

    target._connect_async = AsyncMock(return_value=AsyncMock())
    target.send_config_async = AsyncMock()
    target.send_audio_async = AsyncMock(
        return_value=("/tmp/out.wav", RealtimeTargetResult(audio_bytes=b"", transcripts=["hi"])),
    )

    await target._send_prompt_to_target_async(normalized_conversation=[message])

    target.send_audio_async.assert_awaited_once()


async def test_reset_conversation_async_closes_and_removes(target):
    mock_connection = AsyncMock()
    target._existing_conversation["conv"] = mock_connection

    await target.reset_conversation_async(conversation_id="conv")

    mock_connection.close.assert_awaited_once()
    assert "conv" not in target._existing_conversation


async def test_reset_conversation_async_swallows_close_error(target):
    mock_connection = AsyncMock()
    mock_connection.close.side_effect = RuntimeError("close failed")
    target._existing_conversation["conv"] = mock_connection

    # The error is swallowed and the conversation is still removed.
    await target.reset_conversation_async(conversation_id="conv")

    assert "conv" not in target._existing_conversation


async def test_reset_conversation_async_propagates_cancellation(target):
    mock_connection = AsyncMock()
    mock_connection.close.side_effect = asyncio.CancelledError
    target._existing_conversation["conv"] = mock_connection

    with pytest.raises(asyncio.CancelledError):
        await target.reset_conversation_async(conversation_id="conv")

    mock_connection.close.assert_awaited_once()
    assert "conv" not in target._existing_conversation


async def test_cleanup_conversation_async_warns_and_delegates(target):
    mock_connection = AsyncMock()
    target._existing_conversation["conv"] = mock_connection

    with pytest.warns(DeprecationWarning, match="reset_conversation_async"):
        await target.cleanup_conversation_async(conversation_id="conv")

    mock_connection.close.assert_awaited_once()
    assert "conv" not in target._existing_conversation


async def test_reset_conversation_async_unknown_id_is_noop(target):
    target._existing_conversation["conv"] = AsyncMock()

    await target.reset_conversation_async(conversation_id="missing")

    assert "conv" in target._existing_conversation


async def test_cleanup_target_async_swallows_connection_and_client_errors(target):
    bad_connection = AsyncMock()
    bad_connection.close.side_effect = RuntimeError("connection close failed")
    target._existing_conversation["conv"] = bad_connection

    mock_client = AsyncMock()
    mock_client.close.side_effect = RuntimeError("client close failed")
    target._realtime_client = mock_client

    # Both close errors are swallowed; state is fully reset regardless.
    await target.cleanup_target_async()

    bad_connection.close.assert_awaited_once()
    mock_client.close.assert_awaited_once()
    assert target._existing_conversation == {}
    assert target._realtime_client is None
