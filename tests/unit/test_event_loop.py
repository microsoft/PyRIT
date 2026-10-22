# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Regression coverage for Windows pytest loop socket exhaustion."""

import asyncio
import socket
import struct
import subprocess
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from test_support import event_loop
from test_support.event_loop import new_test_event_loop
from unit.async_utils import TEST_WATCHDOG_SECONDS, wait_for_completion_async

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows uses TCP for the loop's private socket pair"),
    pytest.mark.timeout(TEST_WATCHDOG_SECONDS, method="thread"),
]


@pytest.fixture
async def fixture_loop_async() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


def test_fixture_only_runner_uses_test_loop(fixture_loop_async: asyncio.AbstractEventLoop) -> None:
    assert isinstance(fixture_loop_async, event_loop._WindowsTestEventLoop)
    assert not fixture_loop_async.is_closed()


async def test_coroutine_runner_uses_test_loop_async(fixture_loop_async: asyncio.AbstractEventLoop) -> None:
    assert isinstance(asyncio.get_running_loop(), event_loop._WindowsTestEventLoop)
    assert asyncio.get_running_loop() is fixture_loop_async


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
    await asyncio.wait_for(
        asyncio.to_thread(loop.call_soon_threadsafe, future.set_result, "awake"), timeout=TEST_WATCHDOG_SECONDS
    )
    assert await wait_for_completion_async(future=future) == "awake"

    started = asyncio.Event()

    async def wait_async() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(wait_async())
    try:
        await asyncio.wait_for(started.wait(), timeout=TEST_WATCHDOG_SECONDS)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait_for_completion_async(future=task)
    finally:
        if not task.done():
            task.cancel()
            await wait_for_completion_async(future=asyncio.gather(task, return_exceptions=True))


def test_test_loop_uses_windows_linger_layout() -> None:
    loop = new_test_event_loop()
    expected = struct.pack("HH", 1, 0)
    with patch.object(socket.socket, "setsockopt", autospec=True) as setsockopt:
        loop.close()
    assert setsockopt.call_count == 2
    assert all(call.args[-3:] == (socket.SOL_SOCKET, socket.SO_LINGER, expected) for call in setsockopt.call_args_list)


@asynccontextmanager
async def _child_process_async() -> AsyncGenerator[asyncio.subprocess.Process]:
    process = await asyncio.wait_for(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.buffer.read(); print('done')",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        ),
        timeout=TEST_WATCHDOG_SECONDS,
    )
    try:
        yield process
    finally:
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.communicate(), timeout=TEST_WATCHDOG_SECONDS)


async def test_test_loop_keeps_subprocess_support_async() -> None:
    async with _child_process_async() as process:
        stdout, _ = await asyncio.wait_for(process.communicate(input=b"finish"), timeout=TEST_WATCHDOG_SECONDS)
    assert process.returncode == 0
    assert stdout.splitlines() == [b"ready", b"done"]


@pytest.mark.parametrize("cancelled", [False, True])
async def test_subprocess_is_reaped_on_timeout_or_cancellation_async(*, cancelled: bool) -> None:
    ready = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []

    async def run_async() -> None:
        async with _child_process_async() as process:
            processes.append(process)
            assert process.stdout is not None
            assert (await process.stdout.readline()).strip() == b"ready"
            ready.set()
            async with asyncio.timeout(None) as deadline:
                if not cancelled:
                    deadline.reschedule(asyncio.get_running_loop().time())
                await process.wait()

    task = asyncio.create_task(run_async())
    try:
        if cancelled:
            await asyncio.wait_for(ready.wait(), timeout=TEST_WATCHDOG_SECONDS)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancelled else TimeoutError):
            await wait_for_completion_async(future=task)
        assert len(processes) == 1
        assert processes[0].returncode is not None
    finally:
        if not task.done():
            task.cancel()
            await wait_for_completion_async(future=asyncio.gather(task, return_exceptions=True))


async def test_test_loop_rejects_close_while_running_async() -> None:
    loop = asyncio.get_running_loop()
    with pytest.raises(RuntimeError, match="Cannot close a running event loop"):
        loop.close()
    assert not loop.is_closed()
