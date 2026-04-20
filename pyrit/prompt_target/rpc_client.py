# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import socket
import time
from threading import Event, Semaphore, Thread
from typing import Callable, Optional

import rpyc

from pyrit.identifiers import ScorerIdentifier
from pyrit.models import MessagePiece, Score
from pyrit.ui.rpc import RPCAppException

DEFAULT_PORT = 18812


class RPCClientStoppedException(RPCAppException):
    """
    Thrown when the RPC client is stopped.
    """

    def __init__(self) -> None:
        """Initialize the RPCClientStoppedException."""
        super().__init__("RPC client is stopped.")


class RPCClient:
    """
    RPC client for remote procedure calls.

    This class provides functionality for establishing and maintaining RPC connections,
    handling message exchange, and managing connection lifecycle.
    """

    def __init__(self, callback_disconnected: Optional[Callable[[], None]] = None) -> None:
        """
        Initialize the RPC client.

        Args:
            callback_disconnected (Callable[[], None], Optional): Callback function to invoke when disconnected.
        """
        self._c = None  # type: Optional[rpyc.Connection]
        self._bgsrv = None  # type: Optional[rpyc.BgServingThread]

        self._ping_thread = None  # type: Optional[Thread]
        self._bgsrv_thread = None  # type: Optional[Thread]
        self._is_running = False

        self._shutdown_event = None  # type: Optional[Event]
        self._prompt_received_sem = None  # type: Optional[Semaphore]

        self._prompt_received = None  # type: Optional[MessagePiece]
        self._callback_disconnected = callback_disconnected

    def start(self) -> None:
        """Start the RPC client connection and background service thread."""
        # Check if the port is open
        self._wait_for_server_avaible()
        self._prompt_received_sem = Semaphore(0)

        self._c = rpyc.connect("localhost", DEFAULT_PORT, config={"allow_public_attrs": True})
        self._is_running = True
        self._shutdown_event = Event()
        self._bgsrv_thread = Thread(target=self._bgsrv_lifecycle)
        self._bgsrv_thread.start()

    def wait_for_prompt(self) -> MessagePiece:
        """
        Wait for a prompt to be received from the server.

        Returns:
            MessagePiece: The received message piece.

        Raises:
            RPCClientStoppedException: If the client has been stopped.
        """
        self._prompt_received_sem.acquire()
        if self._is_running:
            return self._prompt_received
        raise RPCClientStoppedException()

    def send_message(self, response: bool) -> None:
        """
        Send a score response message back to the RPC server.

        Args:
            response (bool): True if the prompt is safe, False if unsafe.
        """
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
                class_module="pyrit.prompt_target.rpc_client",
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
