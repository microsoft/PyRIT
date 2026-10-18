# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.0
# ---

# %% [markdown]
# # WebSocket Target
#
# `WebsocketTarget` connects PyRIT to services that use a custom WebSocket protocol.
# Supply the service-specific initialization messages, prompt builder, and response parser.
# The `protocol_identifier` is a non-secret name for this complete protocol configuration.
#
# This example starts a local PyRIT WebSocket service. It exercises the real WebSocket
# transport without credentials or an external endpoint.

# %%
import json
import uuid

from websockets.asyncio.client import ClientConnection
from websockets.asyncio.server import ServerConnection, serve

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import WebsocketTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY, load_defaults=False, silent=True)  # type: ignore


async def pyrit_websocket_handler(websocket: ServerConnection) -> None:
    initialization = json.loads(await websocket.recv())
    if initialization != {"type": "initialize", "client": "PyRIT"}:
        await websocket.close(code=1002, reason="Invalid initialization message")
        return

    await websocket.send(json.dumps({"message": "PyRIT WebSocket target ready"}))

    async for raw_message in websocket:
        request = json.loads(raw_message)
        if request["type"] == "restore":
            await websocket.send(json.dumps({"type": "restored"}))
            continue

        await websocket.send(json.dumps({"event": "processing"}))
        await websocket.send(json.dumps({"message": f"PyRIT received: {request['prompt']}"}))


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
        raise ConnectionError("The WebSocket service did not restore the conversation.")


# %% [markdown]
# Start the local service on an available loopback port, then configure the target for its protocol.
#
# The restore callback is service-specific. PyRIT calls it when a multi-turn conversation needs a
# replacement connection. Without this callback, the target fails instead of silently losing history.

# %%
server = await serve(pyrit_websocket_handler, "127.0.0.1", 0)  # type: ignore
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

# %% [markdown]
# Send a prompt through the target and close both sides of the connection.
#
# Cleanup is terminal for this target instance. If a connection fails while a prompt is in
# progress, the target discards that connection and raises the error instead of retrying the prompt.

# %%
request = MessagePiece(
    role="user",
    original_value="Hello",
    original_value_data_type="text",
    conversation_id=str(uuid.uuid4()),
).to_message()

try:
    response = await target.send_prompt_async(message=request)  # type: ignore
    print(response[0].get_value())
finally:
    await target.cleanup_target_async()  # type: ignore
    server.close()
    await server.wait_closed()  # type: ignore
