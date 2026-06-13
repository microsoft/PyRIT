# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from unittest.mock import MagicMock

import pytest

from pyrit.memory.central_memory import CentralMemory
from pyrit.models import ComponentIdentifier, MessagePiece, Score
from pyrit.score import (
    FloatScaleScorer,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import UNSCOREABLE_METADATA_KEY


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="tests.unit.score",
    )


class MockScorer(TrueFalseScorer):
    """A mock scorer for testing purposes."""

    def _score_aggregator(self, score_list):
        # Use the AND aggregator from the TrueFalseScoreAggregator class
        return TrueFalseScoreAggregator.AND(score_list)

    def __init__(self, *, score_value: bool, score_rationale: str, aggregator=None):
        self._score_value = score_value
        self._score_rationale = score_rationale
        self.aggregator = aggregator
        # Call super().__init__() to properly initialize the scorer including _identifier
        super().__init__(validator=MagicMock())

    def _build_identifier(self) -> ComponentIdentifier:
        """Build the scorer evaluation identifier for this mock scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [
            Score(
                score_value=str(self._score_value),
                score_value_description="",
                score_type=self.scorer_type,
                score_category=[],
                score_metadata=None,
                score_rationale=self._score_rationale,
                scorer_class_identifier=_mock_scorer_id("MockScorer"),
                message_piece_id=str(message_piece.id),
                objective=str(objective),
            )
        ]


@pytest.fixture
def mock_request(patch_central_database):
    memory = CentralMemory.get_memory_instance()
    request = MessagePiece(role="user", original_value="test content", conversation_id="test-conv", sequence=1)
    memory.add_message_pieces_to_memory(message_pieces=[request])
    return request.to_message()


@pytest.fixture
def true_scorer(patch_central_database):
    return MockScorer(score_value=True, score_rationale="This is a true score")


@pytest.fixture
def false_scorer(patch_central_database):
    return MockScorer(score_value=False, score_rationale="This is a false score")


async def test_composite_scorer_and_all_true(mock_request, true_scorer):
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[true_scorer, true_scorer])

    scores = await scorer.score_async(mock_request)
    assert len(scores) == 1
    assert scores[0].get_value() is True
    assert "This is a true score" in scores[0].score_rationale
    assert "All constituent scorers returned True in an AND composite scorer." in scores[0].score_value_description


async def test_composite_scorer_and_one_false(mock_request, true_scorer, false_scorer):
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[true_scorer, false_scorer])

    scores = await scorer.score_async(mock_request)
    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert "This is a false score" in scores[0].score_rationale
    assert "This is a true score" in scores[0].score_rationale


async def test_composite_scorer_or_all_false(mock_request, false_scorer):
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.OR, scorers=[false_scorer, false_scorer])

    scores = await scorer.score_async(mock_request)
    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert "This is a false score" in scores[0].score_rationale
    assert "All constituent scorers returned False in an OR composite scorer." in scores[0].score_value_description


async def test_composite_scorer_or_one_true(mock_request, true_scorer, false_scorer):
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.OR, scorers=[true_scorer, false_scorer])

    scores = await scorer.score_async(mock_request)
    assert len(scores) == 1
    assert scores[0].get_value() is True
    assert "This is a true score" in scores[0].score_rationale


async def test_composite_scorer_majority_true(mock_request, true_scorer, false_scorer):
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.MAJORITY, scorers=[true_scorer, true_scorer, false_scorer]
    )

    scores = await scorer.score_async(mock_request)
    assert len(scores) == 1
    assert scores[0].get_value() is True
    assert "This is a true score" in scores[0].score_rationale
    assert (
        "A strict majority of constituent scorers returned True in a MAJORITY composite scorer."
        in scores[0].score_value_description
    )


async def test_composite_scorer_majority_false(mock_request, true_scorer, false_scorer):
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.MAJORITY, scorers=[true_scorer, false_scorer, false_scorer]
    )

    scores = await scorer.score_async(mock_request)
    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert "This is a true score" in scores[0].score_rationale
    assert "This is a false score" in scores[0].score_rationale


def test_composite_scorer_invalid_scorer_type():
    class InvalidScorer(FloatScaleScorer):
        def __init__(self):
            self._validator = MagicMock()

        def _build_identifier(self) -> ComponentIdentifier:
            return self._create_identifier()

        async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
            return []

    with pytest.raises(ValueError, match="All scorers must be true_false scorers"):
        TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[InvalidScorer()])  # type: ignore[arg-type]


async def test_composite_scorer_with_task(mock_request, true_scorer):
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[true_scorer])

    task = "test task"
    scores = await scorer.score_async(mock_request, objective=task)
    assert len(scores) == 1
    assert scores[0].objective == task


def test_composite_scorer_empty_scorers_list():
    """Test that TrueFalseCompositeScorer raises an exception when given an empty list of scorers."""
    with pytest.raises(ValueError, match="At least one scorer must be provided"):
        TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[])


async def test_composite_scorer_raises_when_message_piece_id_is_none(true_scorer, patch_central_database):
    """Test that _score_async raises ValueError when message piece has no ID."""
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[true_scorer])

    # Create a message with a piece whose id is None
    piece = MessagePiece(role="user", original_value="test content")
    piece.id = None
    message = piece.to_message()

    with pytest.raises(RuntimeError, match="Message piece must have an ID"):
        await scorer.score_async(message)


def test_get_chat_target_returns_first_available(patch_central_database):
    """get_chat_target returns the target from the first sub-scorer that has one."""
    mock_target = MagicMock()

    scorer_without = MockScorer(score_value=True, score_rationale="no target")
    scorer_with = MockScorer(score_value=True, score_rationale="has target")
    scorer_with.get_chat_target = MagicMock(return_value=mock_target)

    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[scorer_without, scorer_with],
    )
    assert composite.get_chat_target() is mock_target


def test_get_chat_target_returns_none_when_no_sub_scorer_has_target(patch_central_database):
    """get_chat_target returns None when no sub-scorer has a chat target."""
    scorer1 = MockScorer(score_value=True, score_rationale="no target")
    scorer2 = MockScorer(score_value=False, score_rationale="also no target")

    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.OR,
        scorers=[scorer1, scorer2],
    )
    assert composite.get_chat_target() is None


class _TextHarmDetector(TrueFalseScorer):
    """Text-only scorer that flags any text piece as a confirmed success (true)."""

    def __init__(self) -> None:
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [
            Score(
                score_value="true",
                score_value_description="harm detected",
                score_type="true_false",
                score_category=[],
                score_metadata=None,
                score_rationale="attack succeeded",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
        ]


class _ImageOnlyDetector(TrueFalseScorer):
    """Image-only scorer; on a text response no piece survives filtering, so the base
    ``_build_fallback_score`` returns the unscoreable fallback false."""

    def __init__(self) -> None:
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["image_path"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        # Never reached for a text response (piece is filtered before this is called).
        return [
            Score(
                score_value="true",
                score_value_description="img harm",
                score_type="true_false",
                score_category=[],
                score_metadata=None,
                score_rationale="img",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
        ]


async def test_unscoreable_fallback_is_marked_distinguishable(mock_request):
    """The filtered fallback false carries the unscoreable metadata flag.

    This is what lets a "could not score" false be told apart from a genuine
    "not harmful" false downstream.
    """
    scores = await _ImageOnlyDetector().score_async(mock_request)
    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert scores[0].score_metadata is not None
    assert scores[0].score_metadata.get(UNSCOREABLE_METADATA_KEY) == 1


async def test_composite_and_unscoreable_masking_is_visible_but_verdict_unchanged(mock_request, caplog):
    """Regression: an unscoreable sub-score masking a confirmed true under AND is surfaced.

    A real text harmful-true scorer is composed with a real image-only scorer whose input
    is filtered (unscoreable fallback false). The AND aggregate stays False (non-breaking),
    but a warning is logged and the rationale records the abstention so the verdict is not
    silently read as "attack failed".
    """
    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[_TextHarmDetector(), _ImageOnlyDetector()],
    )

    with caplog.at_level(logging.WARNING, logger="pyrit.score.true_false.true_false_score_aggregator"):
        scores = await composite.score_async(mock_request)

    assert len(scores) == 1
    # (c) Verdict value is unchanged: AND over true + (unscoreable) false is still False.
    assert scores[0].get_value() is False
    # (a) The unscoreable flag propagates into the aggregate metadata (distinguishable).
    assert scores[0].score_metadata is not None
    assert scores[0].score_metadata.get(UNSCOREABLE_METADATA_KEY) == 1
    # (b) A warning was emitted and the rationale notes the abstention / masking.
    assert any("masking a confirmed success" in record.message for record in caplog.records)
    assert "could not evaluate the response" in scores[0].score_rationale
    assert "under-reporting a confirmed success" in scores[0].score_rationale


async def test_composite_and_genuine_all_false_is_not_flagged(mock_request, false_scorer, caplog):
    """Regression guard: a genuine all-false AND (no unscoreable sub-scores) is not flagged."""
    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[false_scorer, false_scorer],
    )

    with caplog.at_level(logging.WARNING, logger="pyrit.score.true_false.true_false_score_aggregator"):
        scores = await composite.score_async(mock_request)

    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert caplog.records == []
    assert "could not evaluate the response" not in (scores[0].score_rationale or "")
    metadata = scores[0].score_metadata or {}
    assert UNSCOREABLE_METADATA_KEY not in metadata
