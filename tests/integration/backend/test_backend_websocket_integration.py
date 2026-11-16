# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from collections.abc import AsyncGenerator
from typing import Literal
from unittest.mock import patch

import pytest
from fastapi import FastAPI, WebSocket
from unit.async_utils import TEST_WATCHDOG_SECONDS
from uvicorn import Config, Server
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Frame, Opcode
from websockets.protocol import Event
from websockets.typing import Subprotocol

pytestmark = [
    pytest.mark.run_only_if_all_tests,
    pytest.mark.filterwarnings("error::DeprecationWarning:websockets.*"),
    pytest.mark.filterwarnings("error::DeprecationWarning:uvicorn.*"),
]

WebsocketServer = tuple[Server, str, asyncio.Queue[tuple[int, str]]]


@pytest.fixture(name="websocket_server", params=["h11", "httptools"])
async def websocket_server_async(request: pytest.FixtureRequest) -> AsyncGenerator[WebsocketServer, None]:
    app = FastAPI()
    disconnects: asyncio.Queue[tuple[int, str]] = asyncio.Queue()

    @app.websocket("/echo")
    async def echo_async(websocket: WebSocket) -> None:
        assert websocket.scope["subprotocols"] == ["unused", "pyrit.v1"]
        assert websocket.headers["x-client"] == "CaseSensitive"
        await websocket.accept(subprotocol="pyrit.v1", headers=[(b"x-session", b"CaseSensitive")])
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                disconnects.put_nowait((message["code"], message.get("reason", "")))
                return
            if message.get("text") == "close":
                await websocket.send_bytes(b"x" * 2**20)
                await websocket.close(code=1000, reason="Server finished")
                return
            await websocket.send({**message, "type": "websocket.send"})

    config = Config(
        app,
        host="127.0.0.1",
        port=0,
        http=request.param,
        lifespan="off",
        log_config=None,
        ws_max_size=1024,
        ws_ping_interval=0.05,
        ws_ping_timeout=TEST_WATCHDOG_SECONDS,
        timeout_keep_alive=0,
        timeout_graceful_shutdown=int(TEST_WATCHDOG_SECONDS),
    )
    config.load()
    server = Server(config)
    server.lifespan = config.lifespan_class(config)
    await server.startup()
    try:
        port = server.servers[0].sockets[0].getsockname()[1]
        with patch.dict("os.environ", {"NO_PROXY": "127.0.0.1"}):
            yield server, f"ws://127.0.0.1:{port}/echo", disconnects
    finally:
        if any(listener.is_serving() for listener in server.servers):
            await server.shutdown()
    assert not server.server_state.connections
    assert not server.server_state.tasks


@pytest.mark.parametrize("compression", ["deflate", None])
async def test_default_websocket_protocol_round_trip_and_client_close_async(
    *, websocket_server: WebsocketServer, compression: Literal["deflate"] | None
) -> None:
    _, uri, disconnects = websocket_server
    async with asyncio.timeout(TEST_WATCHDOG_SECONDS):
        async with connect(
            uri,
            subprotocols=[Subprotocol("unused"), Subprotocol("pyrit.v1")],
            additional_headers={"x-client": "CaseSensitive"},
            compression=compression,
        ) as websocket:
            assert websocket.subprotocol == "pyrit.v1"
            assert websocket.response is not None
            assert websocket.response.headers["x-session"] == "CaseSensitive"
            if compression is None:
                assert "sec-websocket-extensions" not in websocket.response.headers
            else:
                assert "permessage-deflate" in websocket.response.headers["sec-websocket-extensions"]
            for payload in ["Hello", "", b"\x00\x01", b""]:
                await websocket.send(payload)
                assert await websocket.recv() == payload
            await websocket.send(["fragmented", " text"])
            assert await websocket.recv() == "fragmented text"
            await websocket.send([b"fragmented", b" bytes"])
            assert await websocket.recv() == b"fragmented bytes"
            pong = await websocket.ping(b"client ping")
            await pong
            await websocket.close(code=1001, reason="Client finished")
        assert await disconnects.get() == (1001, "Client finished")


async def test_default_websocket_protocol_server_close_async(websocket_server: WebsocketServer) -> None:
    _, uri, _ = websocket_server
    async with asyncio.timeout(TEST_WATCHDOG_SECONDS):
        async with connect(
            uri,
            subprotocols=[Subprotocol("unused"), Subprotocol("pyrit.v1")],
            additional_headers={"x-client": "CaseSensitive"},
            compression=None,
        ) as websocket:
            await websocket.send("close")
            assert await websocket.recv() == b"x" * 2**20
            await websocket.wait_closed()
            assert websocket.close_code == 1000
            assert websocket.close_reason == "Server finished"


async def test_default_websocket_protocol_size_limit_async(websocket_server: WebsocketServer) -> None:
    _, uri, _ = websocket_server
    async with asyncio.timeout(TEST_WATCHDOG_SECONDS):
        async with connect(
            uri,
            subprotocols=[Subprotocol("unused"), Subprotocol("pyrit.v1")],
            additional_headers={"x-client": "CaseSensitive"},
        ) as websocket:
            await websocket.send("x" * 1025)
            with pytest.raises(ConnectionClosedError):
                await websocket.recv()
            assert websocket.close_code == 1009


async def test_default_websocket_protocol_keepalive_and_shutdown_async(websocket_server: WebsocketServer) -> None:
    server, uri, disconnects = websocket_server
    pings: asyncio.Queue[bytes] = asyncio.Queue()

    class ObservedClientConnection(ClientConnection):
        def process_event(self, event: Event) -> None:
            if isinstance(event, Frame) and event.opcode == Opcode.PING:
                pings.put_nowait(bytes(event.data))
            super().process_event(event)

    async with asyncio.timeout(TEST_WATCHDOG_SECONDS):
        async with connect(
            uri,
            subprotocols=[Subprotocol("unused"), Subprotocol("pyrit.v1")],
            additional_headers={"x-client": "CaseSensitive"},
            ping_interval=None,
            create_connection=ObservedClientConnection,
        ) as websocket:
            await pings.get()
            await pings.get()
            await websocket.send("Still connected")
            assert await websocket.recv() == "Still connected"
            await server.shutdown()
            await websocket.wait_closed()
            assert websocket.close_code == 1012
        assert (await disconnects.get())[0] == 1012
