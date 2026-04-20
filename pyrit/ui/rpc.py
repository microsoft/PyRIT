# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import time
from threading import Semaphore, Thread
from typing import Any, Callable, Optional

from pyrit.identifiers import ScorerIdentifier
from pyrit.models import MessagePiece, Score
from pyrit.ui.app import is_app_running, launch_app

DEFAULT_PORT = 18812

logger = logging.getLogger(__name__)


# Exceptions
class RPCAppException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class RPCAlreadyRunningException(RPCAppException):
    """
    This exception is thrown when an RPC server is already running and the user tries to start another one.
    """

    def __init__(self) -> None:
        super().__init__("RPC server is already running.")


class RPCClientNotReadyException(RPCAppException):
    """
    This exception is thrown when the RPC client is not ready to receive messages.
    """

    def __init__(self) -> None:
        super().__init__("RPC client is not ready.")


class RPCServerStoppedException(RPCAppException):
    """
    This exception is thrown when the RPC server is stopped.
    """

    def __init__(self) -> None:
        super().__init__("RPC server is stopped.")


# RPC Server
class AppRPCServer:
    import rpyc

    # RPC Service
    class RPCService(rpyc.Service):  # type: ignore[misc]
        """
        RPC service is the service that RPyC is using. RPC (Remote Procedure Call) is a way to interact with code that
        is hosted in another process or on an other machine. RPyC is a library that implements RPC and we are using to
        exchange information between PyRIT's main process and Gradio's process. This way the interface is
        independent of which PyRIT code is running the process.
        """

        def __init__(self, *, score_received_semaphore: Semaphore, client_ready_semaphore: Semaphore) -> None:
            super().__init__()
            self._callback_score_prompt: Optional[Callable[[MessagePiece, Optional[str]], None]] = None
            self._last_ping: Optional[float] = None
            self._scores_received: list[Score] = []
            self._score_received_semaphore = score_received_semaphore
            self._client_ready_semaphore = client_ready_semaphore

        def on_connect(self, conn: Any) -> None:
            logger.info("Client connected")

        def on_disconnect(self, conn: Any) -> None:
            logger.info("Client disconnected")

        def exposed_receive_score(self, score: Score) -> None:
            logger.info(f"Score received: {score}")
            self._scores_received.append(score)
            self._score_received_semaphore.release()

        def exposed_receive_ping(self) -> None:
            # A ping should be received every 2s from the client. If a client misses a ping then the server should
            # stopped
            self._last_ping = time.time()
            logger.debug("Ping received")

        def exposed_callback_score_prompt(self, callback: Callable[[MessagePiece, Optional[str]], None]) -> None:
            self._callback_score_prompt = callback
            self._client_ready_semaphore.release()

        def is_client_ready(self) -> bool:
            if self._callback_score_prompt is None:
                return False
            return True

        def send_score_prompt(self, prompt: MessagePiece, task: Optional[str] = None) -> None:
            if not self.is_client_ready():
                raise RPCClientNotReadyException()
            self._callback_score_prompt(prompt, task)

        def is_ping_missed(self) -> bool:
            if self._last_ping is None:
                return False

            return time.time() - self._last_ping > 2

        def pop_score_received(self) -> Score | None:
            try:
                return self._scores_received.pop()
            except IndexError:
                return None

    def __init__(self, open_browser: bool = False) -> None:
        self._server: Any = None
        self._server_thread: Optional[Thread] = None
        self._rpc_service: Optional[AppRPCServer.RPCService] = None
        self._is_alive_thread: Optional[Thread] = None
        self._is_alive_stop = False
        self._score_received_semaphore: Optional[Semaphore] = None
        self._client_ready_semaphore: Optional[Semaphore] = None
        self._server_is_running = False
        self._open_browser = open_browser

    def start(self) -> None:
        """
        Attempt to start the RPC server. If the server is already running, this method will throw an exception.
        """
        # Check if the server is already running by checking if the port is already in use.
        # If the port is already in use, throw an exception.
        if self._is_instance_running():
            raise RPCAlreadyRunningException()

        self._score_received_semaphore = Semaphore(0)
        self._client_ready_semaphore = Semaphore(0)

        # Start the RPC server.
        self._rpc_service = self.RPCService(
            score_received_semaphore=self._score_received_semaphore, client_ready_semaphore=self._client_ready_semaphore
        )
        self._server = self.rpyc.ThreadedServer(
            self._rpc_service, port=DEFAULT_PORT, protocol_config={"allow_all_attrs": True}
        )
        self._server_thread = Thread(target=self._server.start)
        self._server_thread.start()

        # Start a thread to check if the client is still alive
        self._is_alive_stop = False
        self._is_alive_thread = Thread(target=self._is_alive)
        self._is_alive_thread.start()

        self._server_is_running = True

        logger.info("RPC server started")

        if not is_app_running():
            logger.info("Launching Gradio UI")
            launch_app(open_browser=self._open_browser)
        else:
            logger.info("Gradio UI is already running. Will not launch another instance.")

    def stop(self) -> None:
        """
        Stop the RPC server and free up the listening port.
        """
        self.stop_request()
        if self._server is not None:
            self._server_thread.join()

        if self._is_alive_thread is not None:
            self._is_alive_thread.join()

        logger.info("RPC server stopped")

    def stop_request(self) -> None:
        """
        Request the RPC server to stop. This method is does not block while waiting for the server to stop.
        """
        logger.info("RPC server stopping")
        if self._server is not None:
            self._server.close()
            self._server = None

        if self._is_alive_thread is not None:
            self._is_alive_stop = True

        self._server_is_running = False

        if self._client_ready_semaphore is not None:
            self._client_ready_semaphore.release()

        if self._score_received_semaphore is not None:
            self._score_received_semaphore.release()

    def send_score_prompt(self, prompt: MessagePiece, task: Optional[str] = None) -> None:
        """
        Send a score prompt to the client.
        """
        if self._rpc_service is None:
            raise RPCAppException("RPC server is not running.")

        self._rpc_service.send_score_prompt(prompt, task)

    def wait_for_score(self) -> Score:
        """
        Wait for the client to send a score. Should always return a score, but if the synchronisation fails it will
        return None.
        """
        if self._score_received_semaphore is None or self._rpc_service is None:
            raise RPCAppException("RPC server is not running.")

        self._score_received_semaphore.acquire()
        if not self._server_is_running:
            raise RPCServerStoppedException()

        score_ref = self._rpc_service.pop_score_received()
        self._client_ready_semaphore.release()
        if score_ref is None:
            return None
        # Pass instance variables of reflected RPyC Score object as args to PyRIT Score object
        score = Score(
            score_value=score_ref.score_value,
            score_type=score_ref.score_type,
            score_category=score_ref.score_category,
            score_value_description=score_ref.score_value_description,
            score_rationale=score_ref.score_rationale,
            score_metadata=score_ref.score_metadata,
            message_piece_id=score_ref.message_piece_id,
            scorer_class_identifier=ScorerIdentifier(
                class_name="RPCScorer",
                class_module="pyrit.ui.rpc",
                class_description="Score received from RPC client",
                identifier_type="instance",
            ),
        )

        return score

    def wait_for_client(self) -> None:
        """
        Wait for the client to be ready to receive messages.
        """
        if self._client_ready_semaphore is None:
            raise RPCAppException("RPC server is not running.")

        logger.info("Waiting for client to be ready")
        self._client_ready_semaphore.acquire()

        if not self._server_is_running:
            raise RPCServerStoppedException()

        logger.info("Client is ready")

    def _is_instance_running(self) -> bool:
        """
        Check if the RPC server is running.
        """
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("localhost", DEFAULT_PORT)) == 0

    def _is_alive(self) -> None:
        """
        Check if a ping has been missed. If a ping has been missed, stop the server.
        """
        while not self._is_alive_stop:
            if self._rpc_service is not None and self._rpc_service.is_ping_missed():
                logger.error("Ping missed. Stopping server.")
                self.stop_request()
                break
            time.sleep(1)
