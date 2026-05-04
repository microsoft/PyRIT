# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions.exception_classes import ServerErrorException
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import RealtimeTarget
from pyrit.prompt_target.openai.openai_realtime_target import RealtimeTargetResult

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
        connection = await target.connect(conversation_id="test_conv")
        assert connection == mock_connection
        mock_client.realtime.connect.assert_called_once_with(model="test")
    await target.cleanup_target()


async def test_send_prompt_async(target):
    # Mock the necessary methods
    target.connect = AsyncMock(return_value=AsyncMock())
    target.send_config = AsyncMock()
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
    await target.cleanup_target()


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
    target.connect = AsyncMock(return_value=AsyncMock())
    target.send_config = AsyncMock()
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
    await target.cleanup_target()
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
        await target.receive_events(conversation_id)


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
    result = await target.receive_events(conversation_id)
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

    result = await target.receive_events(conversation_id)

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
        await target.receive_events(conversation_id)


async def test_receive_events_connection_closed(target):
    """Test handling of connection closing unexpectedly."""
    mock_connection = AsyncMock()
    conversation_id = "test_connection_closed"
    target._existing_conversation[conversation_id] = mock_connection

    # Mock connection that returns empty list (simulates closed connection)
    mock_connection.__aiter__.return_value = []

    result = await target.receive_events(conversation_id)
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

    result = await target.receive_events(conversation_id)

    # Result should have both audio buffer and transcript from deltas
    assert len(result.transcripts) == 2
    assert result.audio_bytes == b"dummyaudio"
    assert result.transcripts[0] == "Hello, "
    assert result.transcripts[1] == "this is a test transcript."


async def test_multi_turn_reuses_connection(target):
    """Test that multiple turns in the same conversation reuse the same connection.

    This ensures that the server-side conversation context is preserved.
    """
    mock_connection = AsyncMock()
    target.connect = AsyncMock(return_value=mock_connection)
    target.send_config = AsyncMock()
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
    target.connect.assert_called_once_with(conversation_id=conversation_id)
    target.send_config.assert_called_once()

    # Both turns should use the same connection
    assert target._existing_conversation[conversation_id] == mock_connection

    # send_text_async should have been called twice (once per turn)
    assert target.send_text_async.call_count == 2

    await target.cleanup_target()


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

    result = await target.receive_events(conversation_id)

    # Should have processed through to the real response.done with actual audio
    assert result.audio_bytes == b"dummyaudio"
    assert result.transcripts == ["hello"]
