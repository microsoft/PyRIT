# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import Score
from pyrit.score import FloatScaleThresholdScorer


def create_mock_float_scorer(score_value: float):
    """Helper to create a mock float scale scorer with proper identifier."""
    mock_identifier = ScorerIdentifier(
        class_name="MockScorer",
        class_module="test.mock",
        class_description="Mock scorer for testing",
        identifier_type="instance",
    )
    scorer = AsyncMock()
    scorer.score_async = AsyncMock(
        return_value=[
            Score(
                score_value=str(score_value),
                score_type="float_scale",
                score_category=["mock category"],
                score_rationale="A mock rationale",
                score_metadata=None,
                message_piece_id=uuid.uuid4(),
                score_value_description="A mock description",
                scorer_class_identifier=mock_identifier,
                id=uuid.uuid4(),
            )
        ]
    )
    # Add mock identifier - get_identifier() returns a ScorerIdentifier
    scorer.get_identifier = MagicMock(return_value=mock_identifier)
    return scorer


@pytest.mark.parametrize("threshold", [0.3, 0.5, 0.7])
@pytest.mark.parametrize("score_value", [0.1, 0.3, 0.5, 0.7, 0.9])
@pytest.mark.asyncio
async def test_float_scale_threshold_scorer_adds_to_memory(threshold, score_value):
    memory = MagicMock(MemoryInterface)

    scorer = create_mock_float_scorer(score_value)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        float_scale_threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=threshold)

        binary_score = (await float_scale_threshold_scorer.score_text_async(text="mock example"))[0]
        assert binary_score.score_value == str(score_value >= threshold)
        assert binary_score.score_type == "true_false"
        assert binary_score.score_value_description == "A mock description"

        memory.add_scores_to_memory.assert_called_once()


@pytest.mark.asyncio
async def test_float_scale_threshold_scorer_returns_single_score_with_multi_category_scorer():
    """
    Test that FloatScaleThresholdScorer returns exactly one score even when the underlying scorer
    returns multiple.
    """

    memory = MagicMock(MemoryInterface)

    # get_identifier() returns a ScorerIdentifier
    mock_identifier = ScorerIdentifier(
        class_name="MockScorer",
        class_module="test.mock",
        class_description="Mock scorer for testing",
        identifier_type="instance",
    )

    # Mock a scorer that returns multiple scores (like AzureContentFilterScorer)
    scorer = AsyncMock()
    prompt_id = uuid.uuid4()
    scorer.score_async = AsyncMock(
        return_value=[
            Score(
                score_value="0.2",
                score_type="float_scale",
                score_category=["Hate"],
                score_rationale="Hate rationale",
                score_metadata={"azure_severity": 2},
                message_piece_id=prompt_id,
                score_value_description="",
                scorer_class_identifier=mock_identifier,
                id=uuid.uuid4(),
            ),
            Score(
                score_value="0.0",
                score_type="float_scale",
                score_category=["Violence"],
                score_rationale="Violence rationale",
                score_metadata={"azure_severity": 0},
                message_piece_id=prompt_id,
                score_value_description="",
                scorer_class_identifier=mock_identifier,
                id=uuid.uuid4(),
            ),
            Score(
                score_value="0.8",
                score_type="float_scale",
                score_category=["Sexual"],
                score_rationale="Sexual rationale",
                score_metadata={"azure_severity": 6},
                message_piece_id=prompt_id,
                score_value_description="",
                scorer_class_identifier=mock_identifier,
                id=uuid.uuid4(),
            ),
        ]
    )
    scorer.get_identifier = MagicMock(return_value=mock_identifier)

    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        float_scale_threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)

        result_scores = await float_scale_threshold_scorer.score_text_async(text="mock example")

        # Should return exactly one score
        assert len(result_scores) == 1

        binary_score = result_scores[0]
        # With MAX aggregator (default), should take max value (0.8) which is >= 0.5
        assert binary_score.get_value() is True
        assert binary_score.score_type == "true_false"

        # Verify memory was called once with a single score
        memory.add_scores_to_memory.assert_called_once()
        added_scores = memory.add_scores_to_memory.call_args[1]["scores"]
        assert len(added_scores) == 1


@pytest.mark.asyncio
async def test_float_scale_threshold_scorer_handles_empty_scores():
    """
    Test that FloatScaleThresholdScorer gracefully handles when the underlying scorer
    returns no scores (e.g., all messages filtered due to length limits).
    """
    memory = MagicMock(MemoryInterface)

    # Mock a scorer that returns empty list (all pieces filtered)
    scorer = AsyncMock()
    scorer.score_async = AsyncMock(return_value=[])
    # get_identifier() returns a ScorerIdentifier
    mock_identifier = ScorerIdentifier(
        class_name="MockScorer",
        class_module="test.mock",
        class_description="Mock scorer for testing",
        identifier_type="instance",
    )
    scorer.get_identifier = MagicMock(return_value=mock_identifier)

    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        float_scale_threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)

        result_scores = await float_scale_threshold_scorer.score_text_async(text="mock example")

        # Should return exactly one score with False value (default aggregator returns 0.0)
        assert len(result_scores) == 1
        binary_score = result_scores[0]
        assert binary_score.get_value() is False  # 0.0 < 0.5 threshold
        assert binary_score.score_type == "true_false"
        assert "Normalized scale score: 0.0" in binary_score.score_rationale

        # Verify memory was called once
        memory.add_scores_to_memory.assert_called_once()


@pytest.mark.asyncio
async def test_float_scale_threshold_scorer_with_raise_on_empty_aggregator():
    """
    Test that FloatScaleThresholdScorer raises ValueError when using RAISE_ON_EMPTY aggregator
    and the underlying scorer returns no scores.
    """
    from pyrit.score.float_scale.float_scale_score_aggregator import (
        FloatScaleScoreAggregator,
    )

    memory = MagicMock(MemoryInterface)

    # Mock a scorer that returns empty list (all pieces filtered)
    scorer = AsyncMock()
    scorer.score_async = AsyncMock(return_value=[])
    # get_identifier() returns a ScorerIdentifier
    mock_identifier = ScorerIdentifier(
        class_name="MockScorer",
        class_module="test.mock",
        class_description="Mock scorer for testing",
        identifier_type="instance",
    )
    scorer.get_identifier = MagicMock(return_value=mock_identifier)

    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        float_scale_threshold_scorer = FloatScaleThresholdScorer(
            scorer=scorer, threshold=0.5, float_scale_aggregator=FloatScaleScoreAggregator.MAX_RAISE_ON_EMPTY
        )

        # Should raise RuntimeError wrapping ValueError when aggregator encounters empty list
        with pytest.raises(
            RuntimeError, match="Error in scorer FloatScaleThresholdScorer.*No scores available for aggregation"
        ):
            await float_scale_threshold_scorer.score_text_async(text="mock example")
