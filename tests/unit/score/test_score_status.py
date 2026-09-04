# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the undetermined score status and its three-valued aggregation.

An undetermined score means no verdict was reachable. It is neither achievement nor
refutation, so ``get_value()`` refuses rather than reporting a clean negative, and
composites settle only when the missing verdict cannot change the answer.
"""

import uuid

import pytest

from pyrit.models import (
    ComponentIdentifier,
    ContentScorable,
    Score,
    ScoreStatus,
    UndeterminedScoreError,
)
from pyrit.score.float_scale.float_scale_score_aggregator import FloatScaleScoreAggregator
from pyrit.score.score_utils import normalize_score_to_float, score_is_true
from pyrit.score.true_false.true_false_composite_scorer import TrueFalseCompositeScorer
from pyrit.score.true_false.true_false_inverter_scorer import TrueFalseInverterScorer
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseScoreAggregator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


def _scorer_id() -> ComponentIdentifier:
    return ComponentIdentifier(class_name="TestScorer", class_module="tests.unit.score")


def _true_false(value: str | None, *, status: ScoreStatus = ScoreStatus.COMPLETE) -> Score:
    return Score(
        score_value=value,
        status=status,
        score_type="true_false",
        score_rationale="because",
        scorer_class_identifier=_scorer_id(),
        message_piece_id=uuid.uuid4(),
    )


def _float_scale(value: str | None, *, status: ScoreStatus = ScoreStatus.COMPLETE) -> Score:
    return Score(
        score_value=value,
        status=status,
        score_type="float_scale",
        score_rationale="because",
        scorer_class_identifier=_scorer_id(),
        message_piece_id=uuid.uuid4(),
    )


def _undetermined_true_false() -> Score:
    return _true_false(None, status=ScoreStatus.UNDETERMINED)


def _undetermined_float() -> Score:
    return _float_scale(None, status=ScoreStatus.UNDETERMINED)


class TestScoreStatus:
    """The status and value axes have to stay consistent with each other."""

    def test_undetermined_score_carries_no_value(self):
        score = _undetermined_true_false()

        assert score.is_undetermined
        assert score.score_value is None

    def test_get_value_refuses_rather_than_guessing(self):
        score = _undetermined_true_false()

        with pytest.raises(UndeterminedScoreError):
            score.get_value()

    def test_undetermined_score_rejects_a_value(self):
        with pytest.raises(ValueError, match="carries no value"):
            _true_false("true", status=ScoreStatus.UNDETERMINED)

    def test_complete_score_requires_a_value(self):
        with pytest.raises(ValueError, match="requires a score_value"):
            _true_false(None)

    def test_scores_are_complete_by_default(self):
        assert _true_false("true").status is ScoreStatus.COMPLETE


class TestThreeValuedAnd:
    """One definite False settles an AND regardless of what could not be observed."""

    @pytest.mark.parametrize(
        "left, right, expected",
        [
            ("true", "true", True),
            ("true", "false", False),
            ("false", "false", False),
        ],
    )
    def test_determined_pairs_are_unchanged(self, left: str, right: str, expected: bool):
        result = TrueFalseScoreAggregator.AND([_true_false(left), _true_false(right)])

        assert result.value is expected

    def test_true_and_undetermined_is_undetermined(self):
        result = TrueFalseScoreAggregator.AND([_true_false("true"), _undetermined_true_false()])

        assert result.value is None

    def test_false_and_undetermined_is_false(self):
        result = TrueFalseScoreAggregator.AND([_true_false("false"), _undetermined_true_false()])

        assert result.value is False

    def test_undetermined_and_undetermined_is_undetermined(self):
        result = TrueFalseScoreAggregator.AND([_undetermined_true_false(), _undetermined_true_false()])

        assert result.value is None


class TestThreeValuedOr:
    """One definite True settles an OR regardless of what could not be observed."""

    @pytest.mark.parametrize(
        "left, right, expected",
        [
            ("true", "true", True),
            ("true", "false", True),
            ("false", "false", False),
        ],
    )
    def test_determined_pairs_are_unchanged(self, left: str, right: str, expected: bool):
        result = TrueFalseScoreAggregator.OR([_true_false(left), _true_false(right)])

        assert result.value is expected

    def test_true_or_undetermined_is_true(self):
        result = TrueFalseScoreAggregator.OR([_true_false("true"), _undetermined_true_false()])

        assert result.value is True

    def test_false_or_undetermined_is_undetermined(self):
        result = TrueFalseScoreAggregator.OR([_true_false("false"), _undetermined_true_false()])

        assert result.value is None

    def test_undetermined_or_undetermined_is_undetermined(self):
        result = TrueFalseScoreAggregator.OR([_undetermined_true_false(), _undetermined_true_false()])

        assert result.value is None


class TestRemainingAggregators:
    """Aggregators without a three-valued rule degrade to undetermined rather than guessing."""

    def test_majority_is_unchanged_when_every_score_is_determined(self):
        scores = [_true_false("true"), _true_false("true"), _true_false("false")]

        assert TrueFalseScoreAggregator.MAJORITY(scores).value is True

    def test_majority_with_an_undetermined_score_is_undetermined(self):
        scores = [_true_false("true"), _true_false("true"), _undetermined_true_false()]

        assert TrueFalseScoreAggregator.MAJORITY(scores).value is None

    @pytest.mark.parametrize(
        "aggregator",
        [FloatScaleScoreAggregator.AVERAGE, FloatScaleScoreAggregator.MAX, FloatScaleScoreAggregator.MIN],
    )
    def test_float_aggregators_are_unchanged_when_determined(self, aggregator):
        results = aggregator([_float_scale("0.25"), _float_scale("0.75")])

        assert results[0].value is not None

    @pytest.mark.parametrize(
        "aggregator",
        [FloatScaleScoreAggregator.AVERAGE, FloatScaleScoreAggregator.MAX, FloatScaleScoreAggregator.MIN],
    )
    def test_float_aggregators_propagate_undetermined(self, aggregator):
        results = aggregator([_float_scale("0.75"), _undetermined_float()])

        assert results[0].value is None


class TestScoreUtilHelpers:
    """Undetermined reads as not true, matching how attacks treat it today."""

    def test_true_helper_treats_undetermined_as_not_true(self):
        assert score_is_true(_true_false("true")) is True
        assert score_is_true(_true_false("false")) is False
        assert score_is_true(_undetermined_true_false()) is False
        assert score_is_true(None) is False

    def test_normalizing_an_undetermined_score_yields_zero(self):
        assert normalize_score_to_float(_undetermined_float()) == 0.0


class _NonMessageScorer(TrueFalseScorer):
    """A true/false scorer whose evidence is not a message."""

    def __init__(self, *, value: str) -> None:
        super().__init__()
        self._value = value

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable, expectation) -> list[Score]:
        return [
            Score(
                score_value=self._value,
                score_type="true_false",
                score_rationale="not a message",
                scorer_class_identifier=self.get_identifier(),
                scorable=scorable,
            )
        ]


@pytest.mark.usefixtures("patch_central_database")
class TestNonMessageComposition:
    """A composite can hold a child that scores something other than a message."""

    async def test_composite_forwards_the_scorable_to_a_non_message_child(self):
        composite = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND,
            scorers=[_NonMessageScorer(value="true"), _NonMessageScorer(value="true")],
        )

        scores = await composite.score_async(scorable=ContentScorable(value="loose text"))

        assert len(scores) == 1
        assert scores[0].get_value() is True

    async def test_composite_aggregates_a_non_message_child_with_three_valued_logic(self):
        composite = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND,
            scorers=[_NonMessageScorer(value="true"), _NonMessageScorer(value="false")],
        )

        scores = await composite.score_async(scorable=ContentScorable(value="loose text"))

        assert scores[0].get_value() is False

    async def test_inverter_forwards_the_scorable_to_a_non_message_child(self):
        inverter = TrueFalseInverterScorer(scorer=_NonMessageScorer(value="true"))

        scores = await inverter.score_async(scorable=ContentScorable(value="loose text"))

        assert scores[0].get_value() is False
