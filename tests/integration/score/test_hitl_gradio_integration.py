# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib.util
import time
from collections.abc import Callable
from threading import Event, Thread
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import MessagePiece, Score
from pyrit.score import HumanInTheLoopScorerGradio

try:
    from pyrit.ui.rpc import RPCAlreadyRunningException
    from pyrit.ui.rpc_client import RPCClient, RPCClientStoppedException

    _rpyc_available = True
except (ImportError, ModuleNotFoundError):
    _rpyc_available = False

pytestmark = pytest.mark.skipif(not _rpyc_available, reason="rpyc not installed")


def if_gradio_installed():
    return importlib.util.find_spec("gradio") is not None


@pytest.fixture
def score() -> Score:
    return Score(
        score_value="true",
        score_type="true_false",
        score_category=["safety"],
        score_value_description="Safe",
        score_rationale="The prompt was marked safe",
        score_metadata=None,
        message_piece_id="1234",
    )


@pytest.fixture
def prompt_original() -> MessagePiece:
    return MessagePiece(
        role="assistant",
        original_value="This is the original value",
        converted_value="This is the converted value",
    )


class IntegrationRpcClient:
    def __init__(self, score_callback: Callable[[MessagePiece], bool], disconnect_callback: Optional[Callable]):
        self._score_callback = score_callback
        self.rpc_client = RPCClient(disconnect_callback)
        self._is_running = False
        self._thread = None  # type: Optional[Thread]
        self._thread_exception = None  # type: Optional[Exception]

    def start(self):
        self._is_running = True
        self._thread = Thread(target=self._run)
        self._thread.start()

    def _run(self):
        self.rpc_client.start()
        try:
            while self._is_running:
                prompt = self.rpc_client.wait_for_prompt()
                response = self._score_callback(prompt)
                self.rpc_client.send_message(response)
        except RPCClientStoppedException as e:
            if self._is_running:
                self._thread_exception = e
        except Exception as e:
            self._thread_exception = e
        finally:
            if self._is_running:
                self.rpc_client.stop()

    def stop(self):
        self._is_running = False
        self.rpc_client.stop()
        self._thread.join()

        if self._thread_exception is not None:
            raise self._thread_exception


@pytest.mark.skipif(not if_gradio_installed(), reason="Gradio is not installed")
class TestHiTLGradioIntegration:
    @patch("pyrit.ui.rpc.is_app_running")
    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_scorer_can_start(self, mock_is_app_running, prompt_original: MessagePiece):
        memory = MagicMock(MemoryInterface)
        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            disconnected_event = Event()

            def disconnected():
                print("Disconnected called")
                disconnected_event.set()

            def score_callback(prompt: MessagePiece) -> bool:
                assert prompt.original_value == prompt_original.original_value
                assert prompt.converted_value == prompt_original.converted_value
                return True

            mock_is_app_running.return_value = True

            rpc_client = IntegrationRpcClient(score_callback, disconnected)
            scorer = HumanInTheLoopScorerGradio()

            rpc_client.start()
            score_result = await scorer.score_async(message=prompt_original.to_message())

            assert score_result[0].score_value == "true"
            rpc_client.stop()
            scorer.__del__()

            disconnected_event.wait(15)
            assert disconnected_event.is_set()

            mock_is_app_running.assert_called_once()

    @patch("pyrit.ui.rpc.is_app_running")
    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_scorer_receive_multiple(self, mock_is_app_running, prompt_original: MessagePiece):
        memory = MagicMock(MemoryInterface)
        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            disconnected_event = Event()

            def disconnected():
                print("Disconnected called")
                disconnected_event.set()

            i = -1

            def score_callback(prompt: MessagePiece) -> bool:
                nonlocal i
                i += 1
                assert prompt.original_value == prompt_original.original_value
                assert prompt.converted_value == prompt_original.converted_value

                return i % 2 == 0

            mock_is_app_running.return_value = True

            rpc_client = IntegrationRpcClient(score_callback, disconnected)
            scorer = HumanInTheLoopScorerGradio()

            rpc_client.start()

            score_result = await scorer.score_async(message=prompt_original.to_message())
            assert score_result[0].score_value == "true"

            # Next prompt
            score_result = await scorer.score_async(message=prompt_original.to_message())
            assert score_result[0].score_value == "false"

            score_result = await scorer.score_async(message=prompt_original.to_message())
            assert score_result[0].score_value == "true"

            rpc_client.stop()
            scorer.__del__()

            disconnected_event.wait(15)
            assert disconnected_event.is_set()

            mock_is_app_running.assert_called_once()

    @patch("pyrit.ui.rpc.is_app_running")
    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_scorer_handle_client_disconnect(self, mock_is_app_running):
        memory = MagicMock(MemoryInterface)
        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            disconnected_event = Event()

            def disconnected():
                print("Disconnected called")
                disconnected_event.set()

            def score_callback(prompt: MessagePiece) -> bool:
                pytest.fail("Should not be called")
                return True

            mock_is_app_running.return_value = True

            rpc_client = IntegrationRpcClient(score_callback, disconnected)
            scorer = HumanInTheLoopScorerGradio()

            rpc_client.start()
            time.sleep(2)  # Wait for the client to start
            rpc_client.stop()

            scorer.__del__()

            disconnected_event.wait(15)
            assert disconnected_event.is_set()

    @patch("pyrit.ui.rpc.is_app_running")
    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_scorer_handle_server_disconnect(self, mock_is_app_running):
        memory = MagicMock(MemoryInterface)
        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            disconnected_event = Event()

            def disconnected():
                print("Disconnected called")
                disconnected_event.set()

            def score_callback(prompt: MessagePiece) -> bool:
                pytest.fail("Should not be called")
                return True

            mock_is_app_running.return_value = True

            rpc_client = IntegrationRpcClient(score_callback, disconnected)
            scorer = HumanInTheLoopScorerGradio()
            rpc_client.start()
            time.sleep(2)  # Wait for the client to start
            scorer.__del__()

            time.sleep(2)  # Wait for the client to stop
            rpc_client.stop()

            disconnected_event.wait(15)
            assert disconnected_event.is_set()

    @patch("pyrit.ui.rpc.is_app_running")
    @pytest.mark.timeout(30)
    def test_scorer_handle_exception_scorer(self, mock_is_app_running):
        memory = MagicMock(MemoryInterface)
        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            mock_is_app_running.return_value = True

            _ = HumanInTheLoopScorerGradio()
            with pytest.raises(RPCAlreadyRunningException):
                HumanInTheLoopScorerGradio()
