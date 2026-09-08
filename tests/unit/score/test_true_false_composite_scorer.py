# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest
from unit.mocks import store_message

from pyrit.memory.central_memory import CentralMemory
from pyrit.models import (
    ComponentIdentifier,
    MatchesObjective,
    Message,
    MessagePiece,
    Score,
    ScoringExpectation,
)
from pyrit.score import (
    MessageFloatScaleScorer,
    MessageScorable,
    MessageTrueFalseScorer,
    ScorerPromptValidator,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
)


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="tests.unit.score",
    )


class MockScorer(MessageTrueFalseScorer):
    """A mock scorer for testing purposes."""

    def _score_aggregator(self, score_list):
        # Use the AND aggregator from the TrueFalseScoreAggregator class
        return TrueFalseScoreAggregator.AND(score_list)

    def __init__(
        self,
        *,
        score_value: bool,
        score_rationale: str,
        aggregator: object | None = None,
        is_objective_required: bool = False,
    ) -> None:
        self._score_value = score_value
        self._score_rationale = score_rationale
        self.aggregator = aggregator
        self.received_expectations: list[ScoringExpectation | None] = []
        # Call super().__init__() to properly initialize the scorer including _identifier
        super().__init__(validator=ScorerPromptValidator(is_objective_required=is_objective_required))

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

    async def _score_prepared_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        self.received_expectations.append(expectation)
        return await super()._score_prepared_message_async(
            message=message,
            expectation=expectation,
        )


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

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(mock_request)))
    assert len(scores) == 1
    assert scores[0].get_value() is True
    assert "This is a true score" in scores[0].score_rationale
    assert "All constituent scorers returned True in an AND composite scorer." in scores[0].score_value_description


async def test_composite_scorer_and_one_false(mock_request, true_scorer, false_scorer):
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[true_scorer, false_scorer])

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(mock_request)))
    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert "This is a false score" in scores[0].score_rationale
    assert "This is a true score" in scores[0].score_rationale


async def test_composite_scorer_or_all_false(mock_request, false_scorer):
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.OR, scorers=[false_scorer, false_scorer])

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(mock_request)))
    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert "This is a false score" in scores[0].score_rationale
    assert "All constituent scorers returned False in an OR composite scorer." in scores[0].score_value_description


async def test_composite_scorer_or_one_true(mock_request, true_scorer, false_scorer):
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.OR, scorers=[true_scorer, false_scorer])

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(mock_request)))
    assert len(scores) == 1
    assert scores[0].get_value() is True
    assert "This is a true score" in scores[0].score_rationale


async def test_composite_scorer_majority_true(mock_request, true_scorer, false_scorer):
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.MAJORITY, scorers=[true_scorer, true_scorer, false_scorer]
    )

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(mock_request)))
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

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(mock_request)))
    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert "This is a true score" in scores[0].score_rationale
    assert "This is a false score" in scores[0].score_rationale


def test_composite_scorer_invalid_scorer_type():
    class InvalidScorer(MessageFloatScaleScorer):
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
    scores = await scorer.score_async(
        scorable=MessageScorable.from_message(store_message(mock_request)),
        expectation=ScoringExpectation(objective=task),
    )
    assert len(scores) == 1
    assert scores[0].objective == task


async def test_composite_scorer_is_silent_when_all_children_are_not_applicable(mock_request, true_scorer):
    true_scorer._validator = ScorerPromptValidator(supported_roles=["assistant"])
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[true_scorer])

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(mock_request)))

    assert scores == []


async def test_composite_scorer_ignores_non_applicable_child(mock_request, true_scorer, false_scorer):
    false_scorer._validator = ScorerPromptValidator(supported_roles=["assistant"])
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.OR,
        scorers=[false_scorer, true_scorer],
    )

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(mock_request)))

    assert len(scores) == 1
    assert scores[0].get_value() is True


async def test_composite_routes_full_expectation_to_matching_and_nonmatching_leaves(mock_request):
    objective_scorer = MockScorer(
        score_value=True,
        score_rationale="objective",
        is_objective_required=True,
    )
    fixed_criterion_scorer = MockScorer(score_value=True, score_rationale="fixed")
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[objective_scorer, fixed_criterion_scorer],
    )
    expectation = ScoringExpectation(
        objective="test objective",
        conditions=(MatchesObjective(),),
    )

    await scorer.score_async(
        scorable=MessageScorable.from_message(store_message(mock_request)),
        expectation=expectation,
    )

    assert scorer.matched_conditions() == frozenset({MatchesObjective})
    assert scorer.required_conditions() == frozenset({MatchesObjective})
    assert objective_scorer.received_expectations == [expectation]
    assert fixed_criterion_scorer.received_expectations == [expectation]


def test_composite_scorer_empty_scorers_list():
    """Test that TrueFalseCompositeScorer raises an exception when given an empty list of scorers."""
    with pytest.raises(ValueError, match="At least one scorer must be provided"):
        TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[])


async def test_composite_scorer_anchors_where_its_children_anchored(true_scorer, patch_central_database):
    """The aggregate is about whatever its children were about, so it anchors where they did."""
    scorer = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[true_scorer])
    message = store_message(MessagePiece(role="user", original_value="test content").to_message())

    scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

    assert str(scores[0].message_piece_id) == str(message.get_piece().id)


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
