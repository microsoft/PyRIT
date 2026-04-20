# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

from pyrit.identifiers import ScorerIdentifier
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece, Score
from pyrit.score import HumanInTheLoopScorerGradio


def if_gradio_installed():
    return importlib.util.find_spec("gradio") is not None


def _make_scorer_id(name: str = "TestScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test_module",
        class_description="",
        identifier_type="instance",
    )


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
        scorer_class_identifier=_make_scorer_id(),
        objective=None,
    )


@pytest.mark.skipif(not if_gradio_installed(), reason="Gradio is not installed.")
class TestHiTLGradio:
    @patch("pyrit.ui.rpc.AppRPCServer")
    def test_scorer_start_stop_rpc_server(self, mock_rpc_server):
        scorer = HumanInTheLoopScorerGradio()
        mock_rpc_server.return_value.start.assert_called_once()
        scorer.__del__()
        mock_rpc_server.return_value.stop.assert_called_once()

    @patch("pyrit.ui.rpc.AppRPCServer")
    def test_scorer_retrieve_score(self, mock_rpc_server, score: Score):
        scorer = HumanInTheLoopScorerGradio()
        prompt = MagicMock()
        prompt.converted_value_data_type = "text"

        rpc_mocked = mock_rpc_server.return_value

        rpc_mocked.wait_for_score.return_value = score

        score_result = scorer.retrieve_score(prompt)

        assert score_result[0].score_value == score.score_value

        rpc_mocked.wait_for_client.assert_called_once()
        rpc_mocked.send_score_prompt.assert_called_once_with(prompt)
        rpc_mocked.wait_for_score.assert_called_once()

    @patch("pyrit.ui.rpc.AppRPCServer")
    @pytest.mark.asyncio
    async def test_scorer_score_async(self, mock_rpc_server, score: Score):
        memory = MagicMock(MemoryInterface)
        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = HumanInTheLoopScorerGradio()
            prompt = Message(
                message_pieces=[MessagePiece(role="user", original_value="test", converted_value_data_type="text")]
            )

            rpc_mocked = mock_rpc_server.return_value

            rpc_mocked.wait_for_score.return_value = score

            score_result = await scorer.score_async(prompt)

            assert score_result[0].score_value == score.score_value

            rpc_mocked.wait_for_client.assert_called_once()
            rpc_mocked.send_score_prompt.assert_called_once_with(prompt.message_pieces[0])
            rpc_mocked.wait_for_score.assert_called_once()

            memory.add_scores_to_memory.assert_called_once()
            _, called_kwargs = memory.add_scores_to_memory.call_args
            assert "scores" in called_kwargs
            assert len(called_kwargs["scores"]) == 1
            result_score = called_kwargs["scores"][0]
            assert result_score.score_value == score.score_value
            assert result_score.score_type == score.score_type
            assert result_score.score_category == score.score_category
            assert result_score.score_value_description == score.score_value_description
            assert result_score.score_rationale == score.score_rationale

    @patch("pyrit.ui.rpc.AppRPCServer")
    @pytest.mark.asyncio
    async def test_scorer_score_async_on_cancel_stops_server(self, mock_rpc_server):
        scorer = HumanInTheLoopScorerGradio()
        prompt = Message(
            message_pieces=[MessagePiece(role="user", original_value="test", converted_value_data_type="text")]
        )

        rpc_mocked = mock_rpc_server.return_value
        rpc_mocked.wait_for_score.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await scorer.score_async(prompt)

        rpc_mocked.stop.assert_called_once()
