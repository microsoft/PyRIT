# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from unittest.mock import MagicMock, patch

import pytest
from unit.mocks import get_image_message_piece

from pyrit.analytics import ApproximateTextMatching, ExactTextMatching
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import MessagePiece
from pyrit.score import SubStringScorer


@pytest.fixture
def image_message_piece() -> MessagePiece:
    return get_image_message_piece()


@pytest.mark.asyncio
async def test_score_async_unsupported_data_type_returns_false(
    patch_central_database, image_message_piece: MessagePiece
):
    image_message_piece.id = None
    request = image_message_piece.to_message()
    scorer = SubStringScorer(substring="test", categories=["new_category"])

    # With raise_on_no_valid_pieces=False (default), returns False for unsupported data types
    scores = await scorer.score_async(request)
    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert "No supported pieces" in scores[0].score_rationale

    os.remove(image_message_piece.converted_value)


@pytest.mark.asyncio
@pytest.mark.parametrize("sub_string", ["I can't answer", "Not present"])
async def test_substring_scorer_score(sub_string: str, patch_central_database):
    full_text = "blah I can't answer that too"
    scorer = SubStringScorer(substring=sub_string, categories=["new_category"])

    score = await scorer.score_text_async(full_text)

    assert len(score) == 1

    assert score[0].get_value() == (sub_string in full_text)
    assert score[0].score_type == "true_false"
    assert score[0].score_category == ["new_category"]
    assert score[0].message_piece_id is None


@pytest.mark.asyncio
async def test_substring_scorer_case_sensitive():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        text_matcher = ExactTextMatching(case_sensitive=True)
        scorer = SubStringScorer(substring="Hello", text_matcher=text_matcher)
        score_match = await scorer.score_text_async(text="Hello World")
        score_no_match = await scorer.score_text_async(text="hello world")

        assert score_match[0].get_value() is True
        assert score_no_match[0].get_value() is False


@pytest.mark.asyncio
async def test_substring_scorer_case_insensitive():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        # Default is case insensitive
        scorer = SubStringScorer(substring="Hello")
        score1 = await scorer.score_text_async(text="Hello World")
        score2 = await scorer.score_text_async(text="hello world")
        score3 = await scorer.score_text_async(text="HELLO WORLD")

        assert score1[0].get_value() is True
        assert score2[0].get_value() is True
        assert score3[0].get_value() is True


@pytest.mark.asyncio
async def test_substring_scorer_approximate_match():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        # Test approximate matching with low threshold
        text_matcher = ApproximateTextMatching(threshold=0.3, n=3, case_sensitive=False)
        scorer = SubStringScorer(substring="hello", text_matcher=text_matcher)
        score = await scorer.score_text_async(text="hallo world")  # Similar but not exact

        assert len(score) == 1
        # Should detect some similarity


@pytest.mark.asyncio
async def test_substring_scorer_approximate_no_match():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        text_matcher = ApproximateTextMatching(threshold=0.5, n=3, case_sensitive=False)
        scorer = SubStringScorer(substring="hello", text_matcher=text_matcher)
        score = await scorer.score_text_async(text="xyz abc")  # No overlap

        assert len(score) == 1
        assert score[0].get_value() is False


@pytest.mark.asyncio
async def test_substring_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SubStringScorer(substring="string", categories=["new_category"])
        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()


@pytest.mark.asyncio
async def test_substring_scorer_no_category():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SubStringScorer(substring="string")
        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()
