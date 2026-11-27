# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import FloatFeedback, Message, MessagePiece, Score, ScoreStatus
from pyrit.score import (
    FloatScaleThresholdScorer,
    HumanLabeledDataset,
    MetricsType,
    ObjectiveHumanLabeledEntry,
    evaluate_threshold,
    suggest_threshold,
    suggest_threshold_async,
)

# Gold-true responses score high, gold-false ones low, with some overlap in the middle.
SCORES = [0.9, 0.8, 0.7, 0.6, 0.4, 0.2, 0.6, 0.3, 0.2, 0.1, 0.0, 0.0]
LABELS = [True, True, True, True, True, True, False, False, False, False, False, False]


def test_evaluate_threshold_counts_scores_at_threshold_as_true() -> None:
    point = evaluate_threshold(scores=SCORES, labels=LABELS, threshold=0.6)

    assert (point.true_positive, point.false_positive, point.true_negative, point.false_negative) == (4, 1, 5, 2)
    assert point.precision == pytest.approx(0.8)
    assert point.recall == pytest.approx(4 / 6)
    assert point.f1_score == pytest.approx(2 * 0.8 * (4 / 6) / (0.8 + 4 / 6))
    assert point.specificity == pytest.approx(5 / 6)


def test_evaluate_threshold_undefined_rates() -> None:
    point = evaluate_threshold(scores=[0.1, 0.2], labels=[True, True], threshold=0.5)

    assert point.precision == 0.0
    assert point.recall == 0.0
    assert point.f1_score == 0.0
    assert point.specificity is None


def test_suggest_threshold_without_holdout_maximizes_f1() -> None:
    suggestion = suggest_threshold(scores=SCORES, labels=LABELS, holdout_fraction=0)

    assert suggestion.criterion == "max_f1"
    best_f1 = max(point.f1_score for point in suggestion.operating_points)
    assert suggestion.tuning_point.f1_score == best_f1
    assert suggestion.threshold == 0.4
    assert suggestion.holdout_point is None
    assert suggestion.num_tuning_responses == 12
    assert suggestion.num_holdout_responses == 0


def test_suggest_threshold_candidates_are_observed_positive_scores() -> None:
    suggestion = suggest_threshold(scores=SCORES, labels=LABELS, holdout_fraction=0)

    assert [point.threshold for point in suggestion.operating_points] == [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9]


def test_suggest_threshold_min_precision_maximizes_recall_subject_to_floor() -> None:
    suggestion = suggest_threshold(scores=SCORES, labels=LABELS, holdout_fraction=0, min_precision=1.0)

    assert suggestion.criterion == "max_recall_at_min_precision"
    assert suggestion.threshold == 0.7
    assert suggestion.tuning_point.precision == 1.0
    assert suggestion.tuning_point.recall == pytest.approx(0.5)


def test_suggest_threshold_breaks_ties_toward_higher_threshold() -> None:
    # 0.9 (TP=1, FP=0, FN=1) and 0.3 (TP=2, FP=2, FN=0) both have an F1 score of 2/3.
    suggestion = suggest_threshold(scores=[0.9, 0.5, 0.4, 0.3], labels=[True, False, False, True], holdout_fraction=0)

    f1_by_threshold = {point.threshold: point.f1_score for point in suggestion.operating_points}
    assert f1_by_threshold[0.9] == pytest.approx(f1_by_threshold[0.3])
    assert suggestion.threshold == 0.9


def test_suggest_threshold_f1_ties_survive_floating_point() -> None:
    # 0.9 gives TP=1, FP=3, FN=1 and 0.2 gives TP=2, FP=8, FN=0: both have an F1 score of exactly 1/3.
    suggestion = suggest_threshold(
        scores=[0.9, 0.2, 0.9, 0.9, 0.9, 0.5, 0.5, 0.5, 0.5, 0.5],
        labels=[True, True] + [False] * 8,
        holdout_fraction=0,
    )

    f1_by_threshold = {point.threshold: point.f1_score for point in suggestion.operating_points}
    assert f1_by_threshold[0.9] == f1_by_threshold[0.2]
    assert suggestion.threshold == 0.9


def test_suggest_threshold_rejects_empty_holdout_when_requested() -> None:
    # Half of one response per class rounds to zero, so nothing would be held out.
    with pytest.raises(ValueError, match="holdout split has no gold-true"):
        suggest_threshold(scores=[0.9, 0.1], labels=[True, False])


def test_suggest_threshold_reports_current_threshold() -> None:
    suggestion = suggest_threshold(scores=SCORES, labels=LABELS, holdout_fraction=0, current_threshold=0.9)

    assert suggestion.current_threshold == 0.9
    assert suggestion.current_tuning_point.recall == pytest.approx(1 / 6)
    assert suggestion.current_holdout_point is None


def test_suggest_threshold_holdout_is_stratified_and_disjoint() -> None:
    rng = np.random.default_rng(1)
    labels = np.array([True] * 40 + [False] * 60)
    scores = np.clip(np.where(labels, 0.6, 0.3) + rng.normal(0, 0.15, labels.size), 0, 1)

    suggestion = suggest_threshold(scores=scores, labels=labels, holdout_fraction=0.25, current_threshold=0.9)

    assert suggestion.num_holdout_responses == 25
    assert suggestion.num_tuning_responses == 75
    holdout = suggestion.holdout_point
    assert holdout.true_positive + holdout.false_negative == 10
    assert holdout.false_positive + holdout.true_negative == 15
    assert suggestion.current_holdout_point is not None
    assert suggestion.current_holdout_point.threshold == 0.9


def test_suggest_threshold_is_reproducible_with_seed() -> None:
    rng = np.random.default_rng(2)
    labels = rng.random(60) < 0.5
    scores = np.clip(np.where(labels, 0.7, 0.3) + rng.normal(0, 0.2, labels.size), 0, 1)

    first = suggest_threshold(scores=scores, labels=labels, seed=7)
    second = suggest_threshold(scores=scores, labels=labels, seed=7)

    assert first == second


def test_suggest_threshold_counts_every_trial() -> None:
    trials = np.array([[0.9, 0.1], [0.8, 0.7]])

    suggestion = suggest_threshold(scores=trials, labels=[True, False], holdout_fraction=0)

    assert suggestion.num_scorer_trials == 2
    assert suggestion.num_tuning_responses == 2
    point = suggestion.tuning_point
    assert point.true_positive + point.false_positive + point.true_negative + point.false_negative == 4


def test_suggest_threshold_accepts_zero_one_labels() -> None:
    suggestion = suggest_threshold(scores=[0.9, 0.1], labels=[1, 0], holdout_fraction=0)

    assert suggestion.threshold == 0.9


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"scores": [], "labels": []}, "non-empty"),
        ({"scores": [0.5, 1.5], "labels": [True, False]}, r"\[0, 1\]"),
        ({"scores": [0.5, float("nan")], "labels": [True, False]}, r"\[0, 1\]"),
        ({"scores": [0.5, 0.2], "labels": [True]}, "one entry per response"),
        ({"scores": [0.5, 0.2], "labels": [True, 2]}, "boolean"),
        ({"scores": [0.5, 0.2], "labels": [True, False], "min_precision": 0}, "min_precision"),
        ({"scores": [0.5, 0.2], "labels": [True, False], "holdout_fraction": 1}, "holdout_fraction"),
        ({"scores": [0.5, 0.2], "labels": [True, False], "current_threshold": 0}, "current_threshold"),
        ({"scores": [0.5, 0.2], "labels": [False, False], "holdout_fraction": 0}, "no gold-true"),
        ({"scores": [0.0, 0.0], "labels": [True, False], "holdout_fraction": 0}, "Every tuning score is 0.0"),
        (
            {"scores": [0.9, 0.9], "labels": [True, False], "holdout_fraction": 0, "min_precision": 0.9},
            "No threshold reaches a precision of 0.9",
        ),
    ],
)
def test_suggest_threshold_rejects_bad_input(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        suggest_threshold(**kwargs)


def test_suggest_threshold_rejects_holdout_without_positive() -> None:
    # One gold-true response cannot be in both splits.
    with pytest.raises(ValueError, match="holdout split has no gold-true"):
        suggest_threshold(scores=[0.9, 0.1, 0.2], labels=[True, False, False], holdout_fraction=0.4)


def _entry(objective: str, human_scores: list[bool]) -> ObjectiveHumanLabeledEntry:
    assistant = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=f"response to {objective}",
                original_value_data_type="text",
                conversation_id=objective,
            )
        ]
    )
    return ObjectiveHumanLabeledEntry([assistant], human_scores, objective)


def _threshold_score(value: float | None, *, threshold: float = 0.9) -> Score:
    if value is None:
        return Score(score_type="true_false", status=ScoreStatus.UNDETERMINED)
    return Score(
        score_type="true_false",
        score_value=str(value >= threshold).lower(),
        score_metadata=FloatFeedback(value=value).to_metadata(),
    )


@pytest.fixture
def mock_threshold_scorer() -> MagicMock:
    scorer = MagicMock(spec=FloatScaleThresholdScorer)
    scorer.threshold = 0.9
    scorer._memory = MagicMock(spec=MemoryInterface)
    scorer._memory.add_message_to_memory_async = AsyncMock()
    scorer.get_chat_target.return_value = None
    return scorer


async def test_suggest_threshold_async_uses_kept_float_values(mock_threshold_scorer: MagicMock) -> None:
    values = {"a": 0.95, "b": 0.6, "c": 0.5, "d": 0.4, "e": 0.1, "f": None}
    dataset = HumanLabeledDataset(
        name="threshold_test",
        metrics_type=MetricsType.OBJECTIVE,
        version="1.0",
        entries=[
            _entry("a", [True]),
            _entry("b", [True, True, False]),
            _entry("c", [True]),
            _entry("d", [False]),
            _entry("e", [True, False]),  # A split vote counts as false.
            _entry("f", [True]),
        ],
    )

    async def score_async(**kwargs):
        return [_threshold_score(values[kwargs["expectation"].objective])]

    mock_threshold_scorer.score_async = AsyncMock(side_effect=score_async)

    suggestion = await suggest_threshold_async(
        scorer=mock_threshold_scorer, labeled_dataset=dataset, holdout_fraction=0
    )

    assert suggestion.num_excluded_responses == 1
    assert suggestion.num_tuning_responses == 5
    assert suggestion.threshold == 0.5
    assert suggestion.tuning_point.recall == 1.0
    assert suggestion.tuning_point.false_positive == 0
    assert suggestion.current_threshold == 0.9
    assert suggestion.current_tuning_point.recall == pytest.approx(1 / 3)


async def test_suggest_threshold_async_runs_each_trial(mock_threshold_scorer: MagicMock) -> None:
    dataset = HumanLabeledDataset(
        name="threshold_test",
        metrics_type=MetricsType.OBJECTIVE,
        version="1.0",
        entries=[_entry("a", [True]), _entry("b", [False])],
    )
    mock_threshold_scorer.score_async = AsyncMock(
        side_effect=[[_threshold_score(0.8)], [_threshold_score(0.2)], [_threshold_score(0.7)], [_threshold_score(0.3)]]
    )

    suggestion = await suggest_threshold_async(
        scorer=mock_threshold_scorer, labeled_dataset=dataset, num_scorer_trials=2, holdout_fraction=0
    )

    assert mock_threshold_scorer.score_async.await_count == 4
    assert suggestion.num_scorer_trials == 2
    assert suggestion.threshold == 0.7


async def test_suggest_threshold_async_requires_a_float_score(mock_threshold_scorer: MagicMock) -> None:
    dataset = HumanLabeledDataset(
        name="threshold_test", metrics_type=MetricsType.OBJECTIVE, version="1.0", entries=[_entry("a", [True])]
    )
    mock_threshold_scorer.score_async = AsyncMock(return_value=[_threshold_score(None)])

    with pytest.raises(ValueError, match="no float score"):
        await suggest_threshold_async(scorer=mock_threshold_scorer, labeled_dataset=dataset, holdout_fraction=0)


async def test_suggest_threshold_async_rejects_zero_trials(mock_threshold_scorer: MagicMock) -> None:
    dataset = HumanLabeledDataset(
        name="threshold_test", metrics_type=MetricsType.OBJECTIVE, version="1.0", entries=[_entry("a", [True])]
    )

    with pytest.raises(ValueError, match="num_scorer_trials"):
        await suggest_threshold_async(scorer=mock_threshold_scorer, labeled_dataset=dataset, num_scorer_trials=0)
