# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.ai.contentsafety.models import TextCategory
from unit.mocks import (
    get_audio_message_piece,
    get_image_message_piece,
    get_test_message_piece,
)

from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.score.float_scale.azure_content_filter_scorer import AzureContentFilterScorer
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer


@pytest.fixture
def audio_message_piece() -> MessagePiece:
    return get_audio_message_piece()


@pytest.fixture
def image_message_piece() -> MessagePiece:
    return get_image_message_piece()


@pytest.fixture
def text_message_piece() -> MessagePiece:
    return get_test_message_piece()


@pytest.mark.asyncio
async def test_score_async_unsupported_data_type_returns_empty_list(
    patch_central_database, audio_message_piece: MessagePiece
):
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])
    request = Message(
        message_pieces=[audio_message_piece],
    )

    # With raise_on_no_valid_pieces=False (default), returns empty list for unsupported data types
    # (FloatScaleScorer does not create synthetic scores like TrueFalseScorer)
    scores = await scorer.score_async(message=request)
    assert len(scores) == 0

    os.remove(audio_message_piece.converted_value)


@pytest.mark.asyncio
async def test_score_piece_async_text(patch_central_database, text_message_piece: MessagePiece):
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])
    mock_client = MagicMock()
    mock_client.analyze_text.return_value = {"categoriesAnalysis": [{"severity": "2", "category": "Hate"}]}
    scorer._azure_cf_client = mock_client
    scores = await scorer._score_piece_async(text_message_piece)
    assert len(scores) == 1
    score = scores[0]
    assert score.score_type == "float_scale"
    assert score.score_value == str(2.0 / 7)
    assert score.score_category == ["Hate"]
    assert isinstance(score.score_metadata, dict)
    assert score.score_metadata["azure_severity"] == 2
    assert "AzureContentFilterScorer" in str(score.scorer_class_identifier)


@pytest.mark.asyncio
async def test_score_piece_async_image(patch_central_database, image_message_piece: MessagePiece):
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])
    mock_client = MagicMock()
    mock_client.analyze_image.return_value = {"categoriesAnalysis": [{"severity": "3", "category": "Hate"}]}
    scorer._azure_cf_client = mock_client
    # Patch _get_base64_image_data to avoid actual file IO
    # Return a valid base64 string (represents a tiny 1x1 PNG image)
    valid_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    with patch.object(scorer, "_get_base64_image_data", AsyncMock(return_value=valid_base64)):
        scores = await scorer._score_piece_async(image_message_piece)
    assert len(scores) == 1
    score = scores[0]
    assert score.score_type == "float_scale"
    assert score.score_value == str(3.0 / 7)
    assert score.score_category == ["Hate"]
    assert isinstance(score.score_metadata, dict)
    assert score.score_metadata["azure_severity"] == 3
    assert "AzureContentFilterScorer" in str(score.scorer_class_identifier)
    assert "AzureContentFilterScorer" in str(score.scorer_class_identifier)
    os.remove(image_message_piece.converted_value)


def test_default_category():
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar")
    assert len(scorer._harm_categories) == 4


def test_explicit_category():
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])
    assert len(scorer._harm_categories) == 1


@pytest.mark.asyncio
async def test_azure_content_filter_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])

        mock_client = MagicMock()
        mock_client.analyze_text.return_value = {"categoriesAnalysis": [{"severity": "2", "category": "Hate"}]}

        scorer._azure_cf_client = mock_client

        await scorer.score_text_async(text="I hate you!")

        memory.add_scores_to_memory.assert_called_once()


@pytest.mark.asyncio
async def test_azure_content_filter_scorer_score(patch_central_database):
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])

    mock_client = MagicMock()
    mock_client.analyze_text.return_value = {"categoriesAnalysis": [{"severity": "2", "category": "Hate"}]}

    scorer._azure_cf_client = mock_client

    score = await scorer.score_text_async(text="I hate you!")

    assert len(score) == 1
    # The azure score is on a ratio of 0-7, so 2/7 is the normalized value
    assert score[0].score_value == str(2.0 / 7)
    assert score[0].score_value_description == ""
    assert score[0].score_type == "float_scale"
    assert score[0].score_category == ["Hate"]
    assert score[0].score_metadata == {"azure_severity": 2}
    assert not score[0].score_rationale
    assert "AzureContentFilterScorer" in str(score[0].scorer_class_identifier)


def test_azure_content_default_category():
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar")
    assert len(scorer._harm_categories) == 4


def test_azure_content_explicit_category():
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])
    assert len(scorer._harm_categories) == 1


@pytest.mark.asyncio
async def test_azure_content_filter_scorer_chunks_long_text(patch_central_database):
    """
    Test that AzureContentFilterScorer chunks text longer than 10,000 characters
    and aggregates the results by category.
    """
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])

        mock_client = MagicMock()
        # Mock returns for two chunks
        mock_client.analyze_text.return_value = {"categoriesAnalysis": [{"severity": "3", "category": "Hate"}]}
        scorer._azure_cf_client = mock_client

        # Create text longer than 10,000 characters (will be split into 2 chunks)
        long_text = "a" * 10001

        # Should chunk the text and aggregate by category (max severity)
        scores = await scorer.score_text_async(text=long_text)
        assert len(scores) == 1  # One score per category
        assert scores[0].score_category == ["Hate"]
        assert mock_client.analyze_text.call_count == 2  # Called once per chunk


@pytest.mark.asyncio
async def test_azure_content_filter_scorer_accepts_short_text(patch_central_database):
    """
    Test that AzureContentFilterScorer accepts text under 10,000 characters.
    """
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])

        mock_client = MagicMock()
        mock_client.analyze_text.return_value = {"categoriesAnalysis": [{"severity": "3", "category": "Hate"}]}
        scorer._azure_cf_client = mock_client

        # Create text just under the limit
        text_near_limit = "a" * 9999

        scores = await scorer.score_text_async(text=text_near_limit)

        # Should successfully score the text
        assert len(scores) == 1
        assert scores[0].score_value == str(3.0 / 7)
        mock_client.analyze_text.assert_called_once()


@pytest.mark.asyncio
async def test_evaluate_async_raises_for_multiple_categories():
    """Test that evaluate_async raises ValueError when multiple harm categories are configured."""
    scorer = AzureContentFilterScorer(
        api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE, TextCategory.VIOLENCE]
    )
    with pytest.raises(ValueError, match="requires exactly one harm category"):
        await scorer.evaluate_async()


@pytest.mark.asyncio
async def test_evaluate_async_raises_for_all_categories():
    """Test that evaluate_async raises ValueError when all categories are configured (default)."""
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar")
    with pytest.raises(ValueError, match="requires exactly one harm category"):
        await scorer.evaluate_async()


@pytest.mark.asyncio
async def test_evaluate_async_sets_file_mapping_for_single_category(patch_central_database):
    """Test that evaluate_async sets evaluation_file_mapping for single category."""
    scorer = AzureContentFilterScorer(api_key="foo", endpoint="bar", harm_categories=[TextCategory.HATE])

    # Initially no file mapping
    assert scorer.evaluation_file_mapping is None

    # Mock the parent evaluate_async to avoid actual evaluation
    with patch.object(FloatScaleScorer, "evaluate_async", AsyncMock(return_value=None)) as mock_eval:
        await scorer.evaluate_async()

        # File mapping should be set
        assert scorer.evaluation_file_mapping is not None
        assert scorer.evaluation_file_mapping.harm_category == "hate_speech"
        assert scorer.evaluation_file_mapping.result_file == "harm/hate_speech_metrics.jsonl"

        # Parent evaluate_async should be called
        mock_eval.assert_called_once()
