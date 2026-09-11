# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import store_message

from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score
from pyrit.score import FloatScaleThresholdScorer, MessageScorable
from pyrit.score.float_scale.float_scale_scorer import MessageFloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


def create_mock_float_scorer(score_value: float):
    """Helper to create a mock float scale scorer with proper identifier."""
    mock_identifier = ComponentIdentifier(
        class_name="MockScorer",
        class_module="test.mock",
    )
    scorer = MagicMock(spec=MessageFloatScaleScorer)
    scorer._score_nested_async = AsyncMock(
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
    # Add mock identifier - get_identifier() returns a ComponentIdentifier
    scorer.get_identifier = MagicMock(return_value=mock_identifier)
    return scorer


@pytest.mark.parametrize("empty_rationale", ["", "   ", None])
async def test_float_scale_threshold_scorer_omits_label_when_no_scale_rationale(empty_rationale):
    """A wrapped scorer with no rationale must not leave a dangling heading."""
    memory = MagicMock(MemoryInterface)

    mock_identifier = ComponentIdentifier(class_name="MockScorer", class_module="test.mock")
    scorer = MagicMock(spec=MessageFloatScaleScorer)
    scorer._score_nested_async = AsyncMock(
        return_value=[
            Score(
                score_value="0.9",
                score_type="float_scale",
                score_category=["mock category"],
                score_rationale=empty_rationale,
                score_metadata=None,
                message_piece_id=uuid.uuid4(),
                score_value_description="A mock description",
                scorer_class_identifier=mock_identifier,
                id=uuid.uuid4(),
            )
        ]
    )
    scorer.get_identifier = MagicMock(return_value=mock_identifier)

    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)

        binary_score = (await threshold_scorer.score_text_async(text="mock example"))[0]

        assert "Rationale for scale score" not in binary_score.score_rationale
        assert not binary_score.score_rationale.endswith("\n")
        assert "Normalized scale score: 0.9" in binary_score.score_rationale


async def test_float_scale_threshold_scorer_keeps_scale_rationale_when_present():
    memory = MagicMock(MemoryInterface)

    scorer = create_mock_float_scorer(0.9)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)

        binary_score = (await threshold_scorer.score_text_async(text="mock example"))[0]

        assert "Rationale for scale score: A mock rationale" in binary_score.score_rationale


@pytest.mark.parametrize("threshold", [0.3, 0.5, 0.7])
@pytest.mark.parametrize("score_value", [0.1, 0.3, 0.5, 0.7, 0.9])
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


async def test_float_scale_threshold_scorer_returns_single_score_with_multi_category_scorer():
    """
    Test that FloatScaleThresholdScorer returns exactly one score even when the underlying scorer
    returns multiple.
    """

    memory = MagicMock(MemoryInterface)

    # get_identifier() returns a ComponentIdentifier
    mock_identifier = ComponentIdentifier(
        class_name="MockScorer",
        class_module="test.mock",
    )

    # Mock a scorer that returns multiple scores (like AzureContentFilterScorer)
    scorer = MagicMock(spec=MessageFloatScaleScorer)
    prompt_id = uuid.uuid4()
    scorer._score_nested_async = AsyncMock(
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


async def test_float_scale_threshold_scorer_attributes_result_to_aggregate_not_first_score():
    """
    The threshold decision is made on the aggregate, so the resulting score must be described
    by the aggregate too. Previously the category, rationale and metadata were taken from
    scores[0], so a scorer returning one score per harm category (AzureContentFilterScorer)
    produced a True score labelled with whichever category happened to be first, even when
    that category scored 0.0.
    """

    memory = MagicMock(MemoryInterface)
    mock_identifier = ComponentIdentifier(class_name="MockScorer", class_module="test.mock")

    prompt_id = uuid.uuid4()
    scorer = MagicMock(spec=MessageFloatScaleScorer)
    scorer._score_nested_async = AsyncMock(
        return_value=[
            Score(
                score_value="0.857",
                score_type="float_scale",
                score_category=["Violence"],
                score_rationale="Violence rationale",
                score_metadata={"azure_severity": 6},
                message_piece_id=prompt_id,
                score_value_description="",
                scorer_class_identifier=mock_identifier,
                id=uuid.uuid4(),
            ),
            Score(
                score_value="0.0",
                score_type="float_scale",
                score_category=["Hate"],
                score_rationale="Hate rationale",
                score_metadata={"azure_severity": 0},
                message_piece_id=prompt_id,
                score_value_description="",
                scorer_class_identifier=mock_identifier,
                id=uuid.uuid4(),
            ),
        ]
    )
    scorer.get_identifier = MagicMock(return_value=mock_identifier)

    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)
        score = (await threshold_scorer.score_text_async(text="mock example"))[0]

    # Violence (0.857) is what crossed the threshold; Hate scored 0.0.
    assert score.get_value() is True

    # The category that fired must not be dropped in favour of the first score's.
    assert "Violence" in (score.score_category or [])

    # The rationale must mention the score that actually crossed, not only the first one.
    assert "Violence rationale" in score.score_rationale

    # The aggregate spans categories with different severities, so the ambiguous
    # category-specific severity must not be paired with the aggregate value.
    assert score.score_metadata["original_float_value"] == pytest.approx(0.857)
    assert "azure_severity" not in score.score_metadata


async def test_float_scale_threshold_scorer_single_score_attribution_unchanged():
    """A single wrapped score must keep its own category and rationale, as before."""

    memory = MagicMock(MemoryInterface)
    scorer = create_mock_float_scorer(0.9)

    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)
        score = (await threshold_scorer.score_text_async(text="mock example"))[0]

    assert score.get_value() is True
    assert score.score_category == ["mock category"]
    assert "A mock rationale" in score.score_rationale
    assert score.score_metadata["original_float_value"] == pytest.approx(0.9)


async def test_float_scale_threshold_scorer_propagates_empty_scores():
    """A non-applicable wrapped scorer remains non-applicable."""
    memory = MagicMock(MemoryInterface)

    # Mock a scorer that returns empty list (all pieces filtered)
    scorer = MagicMock(spec=MessageFloatScaleScorer)
    scorer._score_nested_async = AsyncMock(return_value=[])
    # get_identifier() returns a ComponentIdentifier
    mock_identifier = ComponentIdentifier(
        class_name="MockScorer",
        class_module="test.mock",
    )
    scorer.get_identifier = MagicMock(return_value=mock_identifier)

    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        float_scale_threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)

        result_scores = await float_scale_threshold_scorer.score_text_async(text="mock example")

        assert result_scores == []
        memory.add_scores_to_memory.assert_not_called()


async def test_float_scale_threshold_scorer_bypasses_raise_on_empty_aggregator():
    from pyrit.score.float_scale.float_scale_score_aggregator import FloatScaleScoreAggregator

    memory = MagicMock(MemoryInterface)

    # Mock a scorer that returns empty list (all pieces filtered)
    scorer = MagicMock(spec=MessageFloatScaleScorer)
    scorer._score_nested_async = AsyncMock(return_value=[])
    # get_identifier() returns a ComponentIdentifier
    mock_identifier = ComponentIdentifier(
        class_name="MockScorer",
        class_module="test.mock",
    )
    scorer.get_identifier = MagicMock(return_value=mock_identifier)

    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        float_scale_threshold_scorer = FloatScaleThresholdScorer(
            scorer=scorer, threshold=0.5, float_scale_aggregator=FloatScaleScoreAggregator.MAX_RAISE_ON_EMPTY
        )

        result_scores = await float_scale_threshold_scorer.score_text_async(text="mock example")

        assert result_scores == []
        memory.add_scores_to_memory.assert_not_called()


def test_get_chat_target_delegates_to_wrapped_scorer():
    """get_chat_target returns the chat target from the wrapped scorer."""
    mock_target = MagicMock()
    scorer = MagicMock()
    scorer.get_chat_target.return_value = mock_target
    scorer.get_identifier = MagicMock(return_value=ComponentIdentifier(class_name="Mock", class_module="test"))

    threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)
    assert threshold_scorer.get_chat_target() is mock_target


def test_get_chat_target_returns_none_when_wrapped_has_none():
    """get_chat_target returns None when the wrapped scorer has no chat target."""
    scorer = MagicMock()
    scorer.get_chat_target.return_value = None
    scorer.get_identifier = MagicMock(return_value=ComponentIdentifier(class_name="Mock", class_module="test"))

    threshold_scorer = FloatScaleThresholdScorer(scorer=scorer, threshold=0.5)
    assert threshold_scorer.get_chat_target() is None


async def test_float_scale_threshold_scorer_with_real_float_scorer_on_blocked(patch_central_database):
    """Integration test: a real MessageFloatScaleScorer subclass returns Score(0.0) on blocked input
    (via its domain fallback), and the threshold wrapper correctly converts that
    to a False true_false score.

    This is the end-to-end path that replaced TAP's deleted error_score_map: the inner scorer
    handles blocked responses itself, so wrappers like FloatScaleThresholdScorer don't need
    any special blocked-handling logic.
    """

    class _RealFloatScaleScorer(MessageFloatScaleScorer):
        def __init__(self):
            super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

        def _build_identifier(self) -> ComponentIdentifier:
            return self._create_identifier()

        async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
            return [
                Score(
                    score_value="0.9",
                    score_type="float_scale",
                    score_category=["mock"],
                    score_rationale="should not be hit for blocked",
                    score_metadata=None,
                    message_piece_id=message_piece.id,
                    score_value_description="mock",
                    scorer_class_identifier=self.get_identifier(),
                )
            ]

    inner = _RealFloatScaleScorer()
    threshold_scorer = FloatScaleThresholdScorer(scorer=inner, threshold=0.5)

    blocked_piece = MessagePiece(
        role="assistant",
        original_value="",
        converted_value="",
        converted_value_data_type="error",
        response_error="blocked",
    )
    blocked_message = Message(message_pieces=[blocked_piece])

    scores = await threshold_scorer.score_async(scorable=MessageScorable.from_message(store_message(blocked_message)))

    assert len(scores) == 1
    binary_score = scores[0]
    assert binary_score.score_type == "true_false"
    assert binary_score.get_value() is False
    assert "Normalized scale score: 0.0" in binary_score.score_rationale

    memory = CentralMemory.get_memory_instance()
    persisted_scores = memory.get_scores(score_type="true_false")
    assert len(persisted_scores) == 1
    assert memory.get_scores(score_type="float_scale") == []
