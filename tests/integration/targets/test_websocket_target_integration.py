# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import uuid

import pytest
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.server import ServerConnection, serve

from pyrit.memory import SQLiteMemory
from pyrit.models import Conversation, Message, MessagePiece
from pyrit.prompt_target import WebsocketTarget


@pytest.mark.run_only_if_all_tests
async def test_websocket_target_round_trip_with_local_pyrit_server(sqlite_instance: SQLiteMemory) -> None:
    received_messages: list[dict[str, str]] = []

    async def pyrit_websocket_handler(websocket: ServerConnection) -> None:
        initialization = json.loads(await websocket.recv())
        received_messages.append(initialization)
        await websocket.send(json.dumps({"message": "PyRIT WebSocket target ready"}))

        async for raw_message in websocket:
            prompt_message = json.loads(raw_message)
            received_messages.append(prompt_message)
            await websocket.send(json.dumps({"event": "processing"}))
            await websocket.send(json.dumps({"message": f"PyRIT received: {prompt_message['prompt']}"}))

    def response_parser(message: str | bytes) -> str | None:
        if isinstance(message, bytes):
            message = message.decode()
        return json.loads(message).get("message")

    def message_builder(prompt: str) -> str:
        return json.dumps({"type": "prompt", "prompt": prompt})

    async with serve(pyrit_websocket_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        target = WebsocketTarget(
            endpoint=f"ws://127.0.0.1:{port}",
            protocol_identifier="local-pyrit-echo-v1",
            initialization_strings=[json.dumps({"type": "initialize", "client": "PyRIT"})],
            response_parser=response_parser,
            message_builder=message_builder,
            discard_initial_messages=1,
        )

        conversation_id = str(uuid.uuid4())
        request = MessagePiece(
            role="user",
            original_value="Hello",
            original_value_data_type="text",
            conversation_id=conversation_id,
        ).to_message()

        try:
            response = await target.send_prompt_async(message=request)
        finally:
            await target.cleanup_target_async()

    assert response[0].get_value() == "PyRIT received: Hello"
    assert received_messages == [
        {"type": "initialize", "client": "PyRIT"},
        {"type": "prompt", "prompt": "Hello"},
    ]


@pytest.mark.run_only_if_all_tests
async def test_websocket_target_restores_history_after_server_disconnect(sqlite_instance: SQLiteMemory) -> None:
    received_messages: list[dict[str, object]] = []
    first_connection_closed = asyncio.Event()
    connection_count = 0

    async def pyrit_websocket_handler(websocket: ServerConnection) -> None:
        nonlocal connection_count
        connection_count += 1
        current_connection = connection_count

        initialization = json.loads(await websocket.recv())
        received_messages.append(initialization)
        await websocket.send(json.dumps({"message": "PyRIT WebSocket target ready"}))

        async for raw_message in websocket:
            request = json.loads(raw_message)
            received_messages.append(request)

            if request["type"] == "restore":
                await websocket.send(json.dumps({"type": "restored"}))
                continue

            await websocket.send(json.dumps({"message": f"PyRIT received: {request['prompt']}"}))
            if current_connection == 1:
                await websocket.close()
                first_connection_closed.set()
                return

    def response_parser(message: str | bytes) -> str | None:
        if isinstance(message, bytes):
            message = message.decode()
        return json.loads(message).get("message")

    def message_builder(prompt: str) -> str:
        return json.dumps({"type": "prompt", "prompt": prompt})

    async def restore_conversation_async(
        websocket: ClientConnection,
        conversation_history: list[Message],
    ) -> None:
        history = [
            {
                "role": message.message_pieces[0].role,
                "content": message.get_value(),
            }
            for message in conversation_history
        ]
        await websocket.send(json.dumps({"type": "restore", "history": history}))
        acknowledgement = json.loads(await websocket.recv())
        if acknowledgement != {"type": "restored"}:
            raise ConnectionError("The local WebSocket server did not restore the conversation.")

    async with serve(pyrit_websocket_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        target = WebsocketTarget(
            endpoint=f"ws://127.0.0.1:{port}",
            protocol_identifier="local-pyrit-echo-v1",
            initialization_strings=[json.dumps({"type": "initialize", "client": "PyRIT"})],
            response_parser=response_parser,
            message_builder=message_builder,
            conversation_restore_callback=restore_conversation_async,
            discard_initial_messages=1,
        )
        conversation_id = str(uuid.uuid4())
        first_request = MessagePiece(
            role="user",
            original_value="First",
            original_value_data_type="text",
            conversation_id=conversation_id,
        ).to_message()

        try:
            first_response = await target.send_prompt_async(message=first_request)
            await first_connection_closed.wait()

            sqlite_instance.add_conversation_to_memory(
                conversation=Conversation(
                    conversation_id=conversation_id,
                    target_identifier=target.get_identifier(),
                )
            )
            sqlite_instance.add_message_to_memory(request=first_request)
            sqlite_instance.add_message_to_memory(request=first_response[0])

            second_request = MessagePiece(
                role="user",
                original_value="Second",
                original_value_data_type="text",
                conversation_id=conversation_id,
            ).to_message()
            second_response = await target.send_prompt_async(message=second_request)
        finally:
            await target.cleanup_target_async()

    assert second_response[0].get_value() == "PyRIT received: Second"
    assert received_messages == [
        {"type": "initialize", "client": "PyRIT"},
        {"type": "prompt", "prompt": "First"},
        {"type": "initialize", "client": "PyRIT"},
        {
            "type": "restore",
            "history": [
                {"role": "user", "content": "First"},
                {"role": "assistant", "content": "PyRIT received: First"},
            ],
        },
        {"type": "prompt", "prompt": "Second"},
    ]
