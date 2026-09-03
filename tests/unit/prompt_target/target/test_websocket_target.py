# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close
from websockets.protocol import State

from pyrit.exceptions import EmptyResponseException
from pyrit.executor.attack.component.prepended_history_send_context import (
    PrependedHistorySendContext,
)
from pyrit.memory import SQLiteMemory
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import WebsocketTarget


@pytest.fixture
def response_parser() -> Callable[[str | bytes], str | None]:
    def parse_response(message: str | bytes) -> str | None:
        if isinstance(message, bytes):
            message = message.decode()
        return json.loads(message).get("message")

    return parse_response


@pytest.fixture
def message_builder() -> Callable[[str], str | bytes]:
    def build_message(prompt: str) -> str:
        return json.dumps({"message": prompt})

    return build_message


@pytest.fixture
def websocket_target(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> WebsocketTarget:
    return WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="test-protocol",
        initialization_strings=["connect", "authenticate"],
        response_parser=response_parser,
        message_builder=message_builder,
        discard_initial_messages=0,
    )


def create_message(*, value: str = "Hello", conversation_id: str = "conversation") -> Message:
    return MessagePiece(
        original_value=value,
        original_value_data_type="text",
        converted_value=value,
        converted_value_data_type="text",
        role="user",
        conversation_id=conversation_id,
    ).to_message()


def test_init_invalid_endpoint_raises(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    with pytest.raises(ValueError, match="endpoint must start"):
        WebsocketTarget(
            endpoint="https://example.com",
            protocol_identifier="test-protocol",
            initialization_strings=[],
            response_parser=response_parser,
            message_builder=message_builder,
        )


def test_init_empty_protocol_identifier_raises(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    with pytest.raises(ValueError, match="protocol_identifier must not be empty"):
        WebsocketTarget(
            endpoint="wss://example.com",
            protocol_identifier=" ",
            initialization_strings=[],
            response_parser=response_parser,
            message_builder=message_builder,
        )


def test_init_invalid_discard_count_raises(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    with pytest.raises(ValueError, match="must be nonnegative"):
        WebsocketTarget(
            endpoint="wss://example.com",
            protocol_identifier="test-protocol",
            initialization_strings=[],
            response_parser=response_parser,
            message_builder=message_builder,
            discard_initial_messages=-1,
        )


def test_init_invalid_timeout_raises(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        WebsocketTarget(
            endpoint="wss://example.com",
            protocol_identifier="test-protocol",
            initialization_strings=[],
            response_parser=response_parser,
            message_builder=message_builder,
            response_timeout_seconds=0,
        )


def test_identifier_distinguishes_protocols(
    websocket_target: WebsocketTarget,
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
) -> None:
    other_target = WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="other-protocol",
        initialization_strings=[],
        response_parser=response_parser,
        message_builder=message_builder,
        discard_initial_messages=0,
    )

    assert websocket_target.get_identifier() != other_target.get_identifier()


async def test_connect_async_passes_websocket_arguments(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    target = WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="test-protocol",
        initialization_strings=[],
        response_parser=response_parser,
        message_builder=message_builder,
        proxy="http://proxy.example.com",
    )
    connection = AsyncMock(spec=ClientConnection)

    with patch(
        "pyrit.prompt_target.websocket_target.websockets.connect",
        new_callable=AsyncMock,
        return_value=connection,
    ) as mock_connect:
        result = await target._connect_async()

    assert result is connection
    mock_connect.assert_awaited_once_with(uri="wss://example.com", proxy="http://proxy.example.com")


async def test_send_prompt_async_initializes_connection_once(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    connection.state = State.OPEN

    with (
        patch.object(websocket_target, "_connect_async", new_callable=AsyncMock, return_value=connection) as connect,
        patch.object(
            websocket_target,
            "_send_text_async",
            new_callable=AsyncMock,
            side_effect=["First response", "Second response"],
        ) as send_text,
    ):
        first_response = await websocket_target.send_prompt_async(
            message=create_message(value="First", conversation_id="shared")
        )
        second_response = await websocket_target.send_prompt_async(
            message=create_message(value="Second", conversation_id="shared")
        )

    connect.assert_awaited_once()
    assert connection.send.await_count == 2
    assert [call.args[0] for call in connection.send.await_args_list] == ["connect", "authenticate"]
    assert send_text.await_count == 2
    assert first_response[0].get_value() == "First response"
    assert second_response[0].get_value() == "Second response"

    await websocket_target.cleanup_target_async()


async def test_send_prompt_async_serializes_same_conversation(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    connection.state = State.OPEN
    active_requests = 0
    maximum_active_requests = 0

    async def send_text(*, text: str, conversation_id: str) -> str:
        nonlocal active_requests, maximum_active_requests
        active_requests += 1
        maximum_active_requests = max(maximum_active_requests, active_requests)
        await asyncio.sleep(0)
        active_requests -= 1
        return text

    with (
        patch.object(websocket_target, "_connect_async", new_callable=AsyncMock, return_value=connection) as connect,
        patch.object(websocket_target, "_send_text_async", side_effect=send_text),
    ):
        await asyncio.gather(
            websocket_target.send_prompt_async(message=create_message(value="First", conversation_id="shared")),
            websocket_target.send_prompt_async(message=create_message(value="Second", conversation_id="shared")),
        )

    connect.assert_awaited_once()
    assert maximum_active_requests == 1

    await websocket_target.cleanup_target_async()


async def test_send_prompt_async_failure_discards_connection(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    connection.state = State.OPEN
    websocket_target._existing_conversation["conversation"] = connection

    with (
        patch.object(websocket_target, "_connect_async", new_callable=AsyncMock) as connect,
        patch.object(
            websocket_target,
            "_send_text_async",
            new_callable=AsyncMock,
            side_effect=ConnectionError("connection failed"),
        ),
    ):
        with pytest.raises(ConnectionError, match="connection failed"):
            await websocket_target.send_prompt_async(message=create_message())

    connect.assert_not_awaited()
    connection.close.assert_awaited_once()
    assert "conversation" not in websocket_target._existing_conversation


async def test_cancellation_during_websocket_setup_occurs_after_target_invocation(
    websocket_target: WebsocketTarget,
    sqlite_instance: SQLiteMemory,
) -> None:
    seed = create_message(value="Seed")
    sqlite_instance.add_message_to_memory(request=seed)
    send_context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(seed.get_piece().id,),
        replay_seed_each_send=False,
    )
    connection_started = asyncio.Event()
    connection_release = asyncio.Event()

    async def wait_for_connection(
        *,
        conversation_id: str,
        conversation_history: list[Message],
    ) -> ClientConnection:
        connection_started.set()
        await connection_release.wait()
        return AsyncMock(spec=ClientConnection)

    with patch.object(websocket_target, "_get_or_create_connection_async", side_effect=wait_for_connection):
        send_task = asyncio.create_task(
            websocket_target.send_prompt_async(
                message=create_message(value="Current"),
                send_context=send_context,
            )
        )
        await connection_started.wait()
        send_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await send_task

    assert send_context.target_invocation_count == 1
    assert not send_context.is_seed_consumed


async def test_get_or_create_connection_async_restores_history_on_reconnect(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    stale_connection = AsyncMock(spec=ClientConnection)
    stale_connection.state = State.CLOSED
    replacement_connection = AsyncMock(spec=ClientConnection)
    replacement_connection.state = State.OPEN
    restore_callback = AsyncMock()
    target = WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="test-protocol",
        initialization_strings=[],
        response_parser=response_parser,
        message_builder=message_builder,
        conversation_restore_callback=restore_callback,
        discard_initial_messages=0,
        existing_convo={"conversation": stale_connection},
    )
    history = [create_message(value="Prior")]

    with patch.object(
        target,
        "_connect_async",
        new_callable=AsyncMock,
        return_value=replacement_connection,
    ) as connect:
        result = await target._get_or_create_connection_async(
            conversation_id="conversation",
            conversation_history=history,
        )

    assert result is replacement_connection
    stale_connection.close.assert_awaited_once()
    connect.assert_awaited_once()
    restore_callback.assert_awaited_once_with(replacement_connection, history)
    assert target._existing_conversation == {"conversation": replacement_connection}


async def test_consumed_context_retains_history_for_reconnect(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    stale_connection = AsyncMock(spec=ClientConnection)
    stale_connection.state = State.CLOSED
    replacement_connection = AsyncMock(spec=ClientConnection)
    replacement_connection.state = State.OPEN
    restore_callback = AsyncMock()
    target = WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="test-protocol",
        initialization_strings=[],
        response_parser=response_parser,
        message_builder=message_builder,
        conversation_restore_callback=restore_callback,
        discard_initial_messages=0,
        existing_convo={"conversation": stale_connection},
    )
    seed = create_message(value="Seed")
    first_request = create_message(value="First request")
    first_response = MessagePiece(
        role="assistant",
        original_value="First response",
        converted_value="First response",
        conversation_id="conversation",
    ).to_message()
    for sequence, message in enumerate([seed, first_request, first_response]):
        for piece in message.message_pieces:
            piece.sequence = sequence
        sqlite_instance.add_message_to_memory(request=message)

    send_context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(seed.get_piece().id,),
        replay_seed_each_send=False,
    )
    send_context.begin_send()
    send_context.mark_target_invoked()
    send_context.finish_send(succeeded=True)

    with (
        patch.object(
            target,
            "_connect_async",
            new_callable=AsyncMock,
            return_value=replacement_connection,
        ),
        patch.object(
            target,
            "_send_text_async",
            new_callable=AsyncMock,
            return_value="Second response",
        ),
    ):
        await target.send_prompt_async(
            message=create_message(value="Second request"),
            send_context=send_context,
        )

    restored_history = restore_callback.await_args.args[1]
    assert [message.get_value() for message in restored_history] == [
        "Seed",
        "First request",
        "First response",
    ]


async def test_get_or_create_connection_async_fails_when_history_cannot_be_restored(
    websocket_target: WebsocketTarget,
) -> None:
    stale_connection = AsyncMock(spec=ClientConnection)
    stale_connection.state = State.CLOSED
    websocket_target._existing_conversation["conversation"] = stale_connection

    with (
        patch.object(websocket_target, "_connect_async", new_callable=AsyncMock) as connect,
        pytest.raises(ConnectionError, match="Configure conversation_restore_callback"),
    ):
        await websocket_target._get_or_create_connection_async(
            conversation_id="conversation",
            conversation_history=[create_message(value="Prior")],
        )

    stale_connection.close.assert_awaited_once()
    connect.assert_not_awaited()
    assert websocket_target._existing_conversation == {}


async def test_send_prompt_async_retry_restores_history(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    existing_connection = AsyncMock(spec=ClientConnection)
    existing_connection.state = State.OPEN
    replacement_connection = AsyncMock(spec=ClientConnection)
    replacement_connection.state = State.OPEN
    restore_callback = AsyncMock()
    target = WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="test-protocol",
        initialization_strings=[],
        response_parser=response_parser,
        message_builder=message_builder,
        conversation_restore_callback=restore_callback,
        discard_initial_messages=0,
        existing_convo={"conversation": existing_connection},
    )
    prior_message = create_message(value="Prior")
    current_message = create_message(value="Current")

    with (
        patch.object(
            target,
            "_connect_async",
            new_callable=AsyncMock,
            return_value=replacement_connection,
        ) as connect,
        patch.object(
            target,
            "_send_text_async",
            new_callable=AsyncMock,
            side_effect=[
                EmptyResponseException(message="empty response"),
                "Recovered response",
            ],
        ),
    ):
        response = await target._send_prompt_to_target_async(
            normalized_conversation=[prior_message, current_message],
        )

    existing_connection.close.assert_awaited_once()
    connect.assert_awaited_once()
    restore_callback.assert_awaited_once_with(replacement_connection, [prior_message])
    assert response[0].get_value() == "Recovered response"


async def test_get_or_create_connection_async_closes_connection_when_initialization_fails(
    websocket_target: WebsocketTarget,
) -> None:
    connection = AsyncMock(spec=ClientConnection)

    with (
        patch.object(
            websocket_target,
            "_connect_async",
            new_callable=AsyncMock,
            return_value=connection,
        ),
        patch.object(
            websocket_target,
            "_initialize_connection_async",
            new_callable=AsyncMock,
            side_effect=ConnectionError("initialization failed"),
        ),
        pytest.raises(ConnectionError, match="initialization failed"),
    ):
        await websocket_target._get_or_create_connection_async(
            conversation_id="conversation",
            conversation_history=[],
        )

    connection.close.assert_awaited_once()
    assert websocket_target._existing_conversation == {}


async def test_get_or_create_connection_async_closes_connection_when_restore_times_out(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    async def restore_forever(
        websocket: ClientConnection,
        conversation_history: list[Message],
    ) -> None:
        await asyncio.sleep(1)

    target = WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="test-protocol",
        initialization_strings=[],
        response_parser=response_parser,
        message_builder=message_builder,
        conversation_restore_callback=restore_forever,
        discard_initial_messages=0,
        response_timeout_seconds=0.001,
    )
    connection = AsyncMock(spec=ClientConnection)

    with (
        patch.object(
            target,
            "_connect_async",
            new_callable=AsyncMock,
            return_value=connection,
        ),
        pytest.raises(TimeoutError, match="Timed out restoring WebSocket conversation history"),
    ):
        await target._get_or_create_connection_async(
            conversation_id="conversation",
            conversation_history=[create_message(value="Prior")],
        )

    connection.close.assert_awaited_once()
    assert target._existing_conversation == {}


def test_validate_request_invalid_type_raises(websocket_target: WebsocketTarget) -> None:
    message = MessagePiece(
        original_value="image.png",
        original_value_data_type="image_path",
        converted_value="image.png",
        converted_value_data_type="image_path",
        role="user",
    ).to_message()

    with pytest.raises(ValueError, match="supports only the following data types: text"):
        websocket_target._validate_request(normalized_conversation=[message])


async def test_send_prompt_async_without_conversation_id_raises(websocket_target: WebsocketTarget) -> None:
    message = create_message()
    message.message_pieces[0].conversation_id = None

    with pytest.raises(ValueError, match="requires a conversation_id"):
        await websocket_target.send_prompt_async(message=message)


async def test_receive_messages_async_ignores_unparsed_frames(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    connection.__aiter__.return_value = [
        json.dumps({"event": "progress"}),
        json.dumps({"message": "response"}),
    ]
    websocket_target._existing_conversation["conversation"] = connection

    result = await websocket_target._receive_messages_async("conversation")

    assert result == "response"


async def test_receive_messages_async_propagates_parser_error(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    connection.__aiter__.return_value = ["not-json"]
    websocket_target._existing_conversation["conversation"] = connection

    with pytest.raises(json.JSONDecodeError):
        await websocket_target._receive_messages_async("conversation")


async def test_receive_messages_async_propagates_connection_closed(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    close_frame = Close(1000, "Normal closure")

    class FailingAsyncIterator:
        def __aiter__(self) -> "FailingAsyncIterator":
            return self

        async def __anext__(self) -> str:
            raise ConnectionClosed(rcvd=close_frame, sent=None)

    connection.__aiter__.side_effect = lambda: FailingAsyncIterator()
    websocket_target._existing_conversation["conversation"] = connection

    with pytest.raises(ConnectionClosed):
        await websocket_target._receive_messages_async("conversation")


async def test_receive_messages_async_accepts_binary_frame(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    connection.__aiter__.return_value = [b'{"message": "response"}']
    websocket_target._existing_conversation["conversation"] = connection

    result = await websocket_target._receive_messages_async("conversation")

    assert result == "response"


async def test_receive_messages_async_without_connection_raises(websocket_target: WebsocketTarget) -> None:
    with pytest.raises(ConnectionError, match="not established"):
        await websocket_target._receive_messages_async("missing")


async def test_send_text_async_timeout_raises(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    websocket_target._existing_conversation["conversation"] = connection
    websocket_target._response_timeout_seconds = 0.001

    async def wait_forever(conversation_id: str) -> str:
        await asyncio.sleep(1)
        return "unreachable"

    with patch.object(websocket_target, "_receive_messages_async", side_effect=wait_forever):
        with pytest.raises(TimeoutError, match="Timed out waiting for a WebSocket response"):
            await websocket_target._send_text_async(text="Hello", conversation_id="conversation")


async def test_initialize_connection_async_discards_configured_messages(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    target = WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="test-protocol",
        initialization_strings=["connect", "authenticate"],
        response_parser=response_parser,
        message_builder=message_builder,
        discard_initial_messages=2,
    )
    connection = AsyncMock(spec=ClientConnection)

    with patch.object(
        target,
        "_receive_message_async",
        new_callable=AsyncMock,
        side_effect=["first", "second"],
    ) as receive:
        await target._initialize_connection_async(websocket=connection)

    assert [call.args[0] for call in connection.send.await_args_list] == ["connect", "authenticate"]
    assert receive.await_count == 2


async def test_initialize_connection_async_timeout_raises(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    target = WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="test-protocol",
        initialization_strings=[],
        response_parser=response_parser,
        message_builder=message_builder,
        discard_initial_messages=1,
        response_timeout_seconds=0.001,
    )
    connection = AsyncMock(spec=ClientConnection)

    async def wait_forever(*, websocket: ClientConnection) -> str:
        await asyncio.sleep(1)
        return "unreachable"

    with patch.object(target, "_receive_message_async", side_effect=wait_forever):
        with pytest.raises(TimeoutError, match="Timed out waiting for an initial WebSocket message"):
            await target._initialize_connection_async(websocket=connection)


async def test_reset_conversation_async_removes_connection(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    websocket_target._existing_conversation["conversation"] = connection

    await websocket_target.reset_conversation_async(conversation_id="conversation")

    connection.close.assert_awaited_once()
    assert websocket_target._existing_conversation == {}


async def test_cleanup_conversation_async_warns_and_delegates(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    websocket_target._existing_conversation["conversation"] = connection

    with pytest.warns(DeprecationWarning, match="cleanup_conversation_async"):
        await websocket_target.cleanup_conversation_async("conversation")

    connection.close.assert_awaited_once()
    assert websocket_target._existing_conversation == {}


async def test_reset_conversation_async_does_not_retain_unknown_lock(websocket_target: WebsocketTarget) -> None:
    await websocket_target.reset_conversation_async(conversation_id="missing")

    assert "missing" not in websocket_target._conversation_locks


async def test_reset_conversation_async_swallows_close_error(
    websocket_target: WebsocketTarget,
) -> None:
    connection = AsyncMock(spec=ClientConnection)
    connection.close.side_effect = ConnectionError("close failed")
    websocket_target._existing_conversation["conversation"] = connection

    # The error is swallowed and the conversation is still removed.
    await websocket_target.reset_conversation_async(conversation_id="conversation")

    connection.close.assert_awaited_once()
    assert websocket_target._existing_conversation == {}


async def test_reset_conversation_async_propagates_cancellation(
    websocket_target: WebsocketTarget,
) -> None:
    connection = AsyncMock(spec=ClientConnection)
    connection.close.side_effect = asyncio.CancelledError
    websocket_target._existing_conversation["conversation"] = connection

    with pytest.raises(asyncio.CancelledError):
        await websocket_target.reset_conversation_async(conversation_id="conversation")

    connection.close.assert_awaited_once()
    assert websocket_target._existing_conversation == {}


async def test_cleanup_target_async_attempts_every_connection(websocket_target: WebsocketTarget) -> None:
    failing_connection = AsyncMock(spec=ClientConnection)
    failing_connection.close.side_effect = RuntimeError("close failed")
    successful_connection = AsyncMock(spec=ClientConnection)
    websocket_target._existing_conversation = {
        "failing": failing_connection,
        "successful": successful_connection,
    }
    conversation_lock = asyncio.Lock()
    websocket_target._conversation_locks["failing"] = conversation_lock

    with pytest.raises(ConnectionError, match="one or more"):
        await websocket_target.cleanup_target_async()

    failing_connection.close.assert_awaited_once()
    successful_connection.close.assert_awaited_once()
    assert websocket_target._existing_conversation == {}
    assert websocket_target._conversation_locks == {}


async def test_cleanup_target_async_makes_target_terminal(websocket_target: WebsocketTarget) -> None:
    connection = AsyncMock(spec=ClientConnection)
    connection.state = State.OPEN
    websocket_target._existing_conversation["conversation"] = connection

    await websocket_target.cleanup_target_async()

    with (
        patch.object(websocket_target, "_connect_async", new_callable=AsyncMock) as connect,
        pytest.raises(RuntimeError, match="has been cleaned up"),
    ):
        await websocket_target.send_prompt_async(message=create_message())

    connect.assert_not_awaited()
    connection.close.assert_awaited_once()


async def test_cleanup_target_async_blocks_rate_limited_send(
    response_parser: Callable[[str | bytes], str | None],
    message_builder: Callable[[str], str | bytes],
    sqlite_instance: SQLiteMemory,
) -> None:
    target = WebsocketTarget(
        endpoint="wss://example.com",
        protocol_identifier="test-protocol",
        initialization_strings=[],
        response_parser=response_parser,
        message_builder=message_builder,
        discard_initial_messages=0,
        max_requests_per_minute=1,
    )
    rate_limit_started = asyncio.Event()
    release_rate_limit = asyncio.Event()

    async def wait_for_rate_limit(delay: float) -> None:
        rate_limit_started.set()
        await release_rate_limit.wait()

    with (
        patch("pyrit.prompt_target.common.utils.asyncio.sleep", side_effect=wait_for_rate_limit),
        patch.object(target, "_connect_async", new_callable=AsyncMock) as connect,
    ):
        send_task = asyncio.create_task(target.send_prompt_async(message=create_message()))
        await rate_limit_started.wait()
        await target.cleanup_target_async()
        release_rate_limit.set()

        with pytest.raises(RuntimeError, match="has been cleaned up"):
            await send_task

    connect.assert_not_awaited()


async def test_cleanup_target_async_cancellation_finishes_closing_connections(
    websocket_target: WebsocketTarget,
) -> None:
    connection = AsyncMock(spec=ClientConnection)
    websocket_target._existing_conversation["conversation"] = connection
    close_started = asyncio.Event()
    finish_close = asyncio.Event()

    async def close_connection() -> None:
        close_started.set()
        await finish_close.wait()

    connection.close.side_effect = close_connection
    cleanup_task = asyncio.create_task(websocket_target.cleanup_target_async())
    await close_started.wait()

    cleanup_task.cancel()
    await asyncio.sleep(0)
    assert not cleanup_task.done()

    finish_close.set()
    with pytest.raises(asyncio.CancelledError):
        await cleanup_task

    assert websocket_target._existing_conversation == {}
    with pytest.raises(RuntimeError, match="has been cleaned up"):
        await websocket_target.send_prompt_async(message=create_message())
