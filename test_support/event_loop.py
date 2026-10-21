# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Function-scoped Windows test loops without TCP TIME_WAIT accumulation."""

import asyncio
import socket
import struct
import sys

if sys.platform == "win32":
    from asyncio.windows_events import ProactorEventLoop

    class _WindowsTestEventLoop(ProactorEventLoop):
        _ssock: socket.socket | None
        _csock: socket.socket | None

        def close(self) -> None:
            if self.is_running() or self.is_closed():
                super().close()
                return
            # Only the loop's private wakeup sockets use abortive close, not application traffic.
            try:
                for wakeup_socket in (self._ssock, self._csock):
                    if wakeup_socket is not None:
                        wakeup_socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 1, 0))
            finally:
                super().close()


def new_test_event_loop() -> asyncio.AbstractEventLoop:
    """Keep the native loop and per-test isolation, but release Windows wakeup ports on close."""
    if sys.platform == "win32":
        return _WindowsTestEventLoop()
    return asyncio.new_event_loop()
