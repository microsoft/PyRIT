# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import socket
import time
from threading import Event, Semaphore, Thread
from typing import Any, Callable, Optional

import rpyc

from pyrit.identifiers import ScorerIdentifier
from pyrit.models import MessagePiece, Score
from pyrit.ui.rpc import RPCAppException

DEFAULT_PORT = 18812


class RPCClientStoppedException(RPCAppException):
    """
    This exception is thrown when the RPC client is stopped.
    """

    def __init__(self) -> None:
        super().__init__("RPC client is stopped.")


class RPCClient:
    def __init__(self, callback_disconnected: Optional[Callable[[], None]] = None) -> None:
        self._c: Optional[rpyc.Connection] = None
        self._bgsrv: Any = None

        self._ping_thread: Optional[Thread] = None
        self._bgsrv_thread: Optional[Thread] = None
        self._is_running = False

        self._shutdown_event: Optional[Event] = None
        self._prompt_received_sem: Optional[Semaphore] = None

        self._prompt_received: Optional[MessagePiece] = None
        self._callback_disconnected = callback_disconnected

    def start(self) -> None:
        # Check if the port is open
        self._wait_for_server_avaible()
        self._prompt_received_sem = Semaphore(0)

        self._c = rpyc.connect("localhost", DEFAULT_PORT, config={"allow_public_attrs": True})
        self._is_running = True
        self._shutdown_event = Event()
        self._bgsrv_thread = Thread(target=self._bgsrv_lifecycle)
        self._bgsrv_thread.start()

    def wait_for_prompt(self) -> MessagePiece:
        self._prompt_received_sem.acquire()
        if self._is_running:
            return self._prompt_received
        raise RPCClientStoppedException()

    def send_message(self, response: bool) -> None:
        score = Score(
            score_value=str(response),
            score_type="true_false",
            score_category=["safety"],
            score_value_description="Safe" if response else "Unsafe",
            score_rationale="The prompt was marked safe" if response else "The prompt was marked unsafe",
            score_metadata=None,
            message_piece_id=self._prompt_received.id,
            scorer_class_identifier=ScorerIdentifier(
                class_name="RPCClient",
                class_module="pyrit.ui.rpc_client",
                class_description="Human-in-the-loop scorer via RPC",
                identifier_type="instance",
            ),
        )
        self._c.root.receive_score(score)

    def _wait_for_server_avaible(self) -> None:
        # Wait for the server to be available
        while not self._is_server_running():
            print("Server is not running. Waiting for server to start...")
            time.sleep(1)

    def stop(self) -> None:
        """
        Stop the client.
        """
        # Send a signal to the thread to stop
        self._shutdown_event.set()

        if self._bgsrv_thread is not None:
            self._bgsrv_thread.join()

    def reconnect(self) -> None:
        """
        Reconnect to the server.
        """
        self.stop()
        print("Reconnecting to server...")
        self.start()

    def _receive_prompt(self, message_piece: MessagePiece, task: Optional[str] = None) -> None:
        print(f"Received prompt: {message_piece}")
        self._prompt_received = message_piece
        self._prompt_received_sem.release()

    def _ping(self) -> None:
        try:
            while self._is_running:
                self._c.root.receive_ping()
                time.sleep(1.5)
            if not self._is_running:
                print("Connection closed")
                if self._callback_disconnected is not None:
                    self._callback_disconnected()
        except EOFError:
            print("Connection closed")
            if self._callback_disconnected is not None:
                self._callback_disconnected()

    def _bgsrv_lifecycle(self) -> None:
        self._bgsrv = rpyc.BgServingThread(self._c)
        self._ping_thread = Thread(target=self._ping)
        self._ping_thread.start()

        # Register callback
        self._c.root.callback_score_prompt(self._receive_prompt)

        # Wait for the server to be disconnected
        self._shutdown_event.wait()

        self._is_running = False

        # Release the semaphore in case it was waiting
        self._prompt_received_sem.release()
        self._ping_thread.join()

        # Avoid calling stop() twice if the server is already stopped. This can happen if the server is stopped
        # by the ping request.
        if self._bgsrv._active:
            self._bgsrv.stop()

    def _is_server_running(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("localhost", DEFAULT_PORT)) == 0
