# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Regression coverage for Windows pytest loop socket exhaustion."""

import asyncio
import socket
import struct
import subprocess
import sys
from unittest.mock import patch

import pytest

from test_support.event_loop import new_test_event_loop

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows uses TCP for the loop's private socket pair")


def test_test_loop_releases_wakeup_ports_without_time_wait() -> None:
    endpoints: set[tuple[str, str]] = set()
    for _ in range(64):
        loop = new_test_event_loop()
        try:
            assert isinstance(loop, asyncio.ProactorEventLoop)
            for wakeup_socket in (loop._ssock, loop._csock):
                local_host, local_port = wakeup_socket.getsockname()
                peer_host, peer_port = wakeup_socket.getpeername()
                endpoints.add((f"{local_host}:{local_port}", f"{peer_host}:{peer_port}"))
        finally:
            loop.close()
        assert loop.is_closed()

    connections = subprocess.run(
        ["netstat", "-an", "-p", "tcp"], check=True, capture_output=True, text=True, timeout=30
    ).stdout.splitlines()
    remaining = []
    for line in connections:
        fields = line.split()
        if fields and fields[0] == "TCP" and (fields[1], fields[2]) in endpoints:
            remaining.append(fields)
    assert remaining == []


def test_test_loop_changes_only_private_wakeup_socket_close() -> None:
    loop = new_test_event_loop()
    wakeup_sockets = (loop._ssock, loop._csock)
    with socket.socket() as application_socket:
        original_linger = application_socket.getsockopt(socket.SOL_SOCKET, socket.SO_LINGER, 4)
        loop.close()
        assert application_socket.getsockopt(socket.SOL_SOCKET, socket.SO_LINGER, 4) == original_linger
    assert all(wakeup_socket.fileno() == -1 for wakeup_socket in wakeup_sockets)


def test_test_loop_closes_wakeup_sockets_when_linger_configuration_fails() -> None:
    loop = new_test_event_loop()
    wakeup_sockets = (loop._ssock, loop._csock)
    with patch.object(socket.socket, "setsockopt", side_effect=OSError("linger failed")):
        with pytest.raises(OSError, match="linger failed"):
            loop.close()
    assert loop.is_closed()
    loop.close()
    assert all(wakeup_socket.fileno() == -1 for wakeup_socket in wakeup_sockets)


async def test_test_loop_keeps_thread_wakeup_and_cancellation_async() -> None:
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await asyncio.to_thread(loop.call_soon_threadsafe, future.set_result, "awake")
    assert await asyncio.wait_for(future, timeout=5) == "awake"

    started = asyncio.Event()

    async def wait_async() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(wait_async())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_test_loop_uses_windows_linger_layout() -> None:
    loop = new_test_event_loop()
    expected = struct.pack("HH", 1, 0)
    with patch.object(socket.socket, "setsockopt", autospec=True) as setsockopt:
        loop.close()
    assert setsockopt.call_count == 2
    assert all(call.args[-3:] == (socket.SOL_SOCKET, socket.SO_LINGER, expected) for call in setsockopt.call_args_list)


async def test_test_loop_keeps_subprocess_support_async() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "print('ready')", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
    assert process.returncode == 0
    assert stdout.strip() == b"ready"


async def test_test_loop_rejects_close_while_running_async() -> None:
    loop = asyncio.get_running_loop()
    with pytest.raises(RuntimeError, match="Cannot close a running event loop"):
        loop.close()
    assert not loop.is_closed()
