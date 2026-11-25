# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Suggest a ``FloatScaleThresholdScorer`` threshold from human-labeled data.

The helpers here only suggest a value. They never change a scorer: the caller reads the
suggestion, checks the held-out result, and sets the threshold themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from pyrit.models import FloatFeedback
from pyrit.score.scorer_evaluation.scorer_evaluator import ObjectiveScorerEvaluator

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.score.scorer_evaluation.human_labeled_dataset import HumanLabeledDataset
    from pyrit.score.true_false.float_scale_threshold_scorer import FloatScaleThresholdScorer


@dataclass(frozen=True)
class ThresholdOperatingPoint:
    """
    How a threshold classifies a set of labeled float scores.

    A score at or above ``threshold`` counts as true, which is how ``FloatScaleThresholdScorer``
    applies its threshold. With several scoring trials, every trial of every response is
    counted, so the counts describe single scoring runs rather than responses.

    Attributes:
        threshold (float): The threshold that was applied.
        true_positive (int): Gold-true scores at or above the threshold.
        false_positive (int): Gold-false scores at or above the threshold.
        true_negative (int): Gold-false scores below the threshold.
        false_negative (int): Gold-true scores below the threshold.
        precision (float): ``TP / (TP + FP)``, or 0.0 when nothing is at or above the threshold.
        recall (float): ``TP / (TP + FN)``, or 0.0 when there are no gold-true scores.
        f1_score (float): The harmonic mean of precision and recall, or 0.0 when both are 0.0.
        specificity (float | None): ``TN / (TN + FP)``, or None when there are no gold-false scores.
    """

    threshold: float
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: float
    recall: float
    f1_score: float
    specificity: float | None


@dataclass(frozen=True)
class ThresholdSuggestion:
    """
    A suggested threshold, how it was chosen, and how it does on held-out data.

    The threshold is chosen on the tuning split only. ``holdout_point`` then applies that frozen
    threshold to responses the choice never saw, which is the estimate to trust. The tuning
    numbers are optimistic, because the threshold was picked to look good on them.

    Attributes:
        threshold (float): The suggested threshold, one of the observed tuning scores.
        criterion (str): ``"max_f1"``, or ``"max_recall_at_min_precision"`` when ``min_precision`` was set.
        min_precision (float | None): The precision floor the threshold had to meet on the tuning split.
        tuning_point (ThresholdOperatingPoint): The suggested threshold on the tuning split.
        holdout_point (ThresholdOperatingPoint | None): The suggested threshold on the holdout split,
            or None when ``holdout_fraction`` was 0.
        operating_points (tuple[ThresholdOperatingPoint, ...]): Every candidate threshold on the
            tuning split, in ascending order of threshold.
        current_threshold (float | None): The threshold the suggestion is compared with, if any.
        current_tuning_point (ThresholdOperatingPoint | None): ``current_threshold`` on the tuning split.
        current_holdout_point (ThresholdOperatingPoint | None): ``current_threshold`` on the holdout split.
        num_tuning_responses (int): Responses in the tuning split.
        num_holdout_responses (int): Responses in the holdout split.
        num_scorer_trials (int): Scoring trials per response.
        num_excluded_responses (int): Responses left out because a trial returned no float score.
    """

    threshold: float
    criterion: str
    min_precision: float | None
    tuning_point: ThresholdOperatingPoint
    holdout_point: ThresholdOperatingPoint | None
    operating_points: tuple[ThresholdOperatingPoint, ...]
    current_threshold: float | None
    current_tuning_point: ThresholdOperatingPoint | None
    current_holdout_point: ThresholdOperatingPoint | None
    num_tuning_responses: int
    num_holdout_responses: int
    num_scorer_trials: int
    num_excluded_responses: int = 0


def evaluate_threshold(
    *,
    scores: Sequence[float] | np.ndarray,
    labels: Sequence[bool] | np.ndarray,
    threshold: float,
) -> ThresholdOperatingPoint:
    """
    Classify labeled float scores with one threshold.

    Args:
        scores (Sequence[float] | np.ndarray): Float scores in ``[0, 1]``, either one per response
            or shaped ``(num_trials, num_responses)``.
        labels (Sequence[bool] | np.ndarray): The gold label of each response.
        threshold (float): The threshold to apply. Scores at or above it count as true.

    Returns:
        ThresholdOperatingPoint: The confusion counts and rates at ``threshold``.
    """
    score_matrix, gold = _validate_scores_and_labels(scores=scores, labels=labels)
    _validate_threshold(threshold=threshold, name="threshold")
    return _operating_point(score_matrix=score_matrix, gold=gold, threshold=threshold)


def suggest_threshold(
    *,
    scores: Sequence[float] | np.ndarray,
    labels: Sequence[bool] | np.ndarray,
    min_precision: float | None = None,
    holdout_fraction: float = 0.5,
    seed: int | None = 0,
    current_threshold: float | None = None,
) -> ThresholdSuggestion:
    """
    Suggest a ``FloatScaleThresholdScorer`` threshold from human-labeled float scores.

    The responses are split at random, keeping the share of gold-true labels the same in both
    parts. On the tuning part, every observed score is a candidate, because a threshold between
    two observed scores classifies them the same way as the higher one. Without
    ``min_precision``, the candidate with the highest F1 score is suggested. With it, the
    candidate with the highest recall among those whose precision reaches ``min_precision`` is
    suggested. Ties go to the higher threshold. The suggested threshold is then applied, unchanged,
    to the holdout part.

    Args:
        scores (Sequence[float] | np.ndarray): Float scores in ``[0, 1]``, either one per response
            or shaped ``(num_trials, num_responses)``. Several trials of a response stay in the
            same split.
        labels (Sequence[bool] | np.ndarray): The gold (human) label of each response.
        min_precision (float | None): If set, a precision in ``(0, 1]`` the suggested threshold must
            reach on the tuning split. Defaults to None, which maximizes F1.
        holdout_fraction (float): The share of responses held out to check the suggestion, in
            ``[0, 1)``. 0 tunes on every response and reports no held-out result. Defaults to 0.5.
        seed (int | None): Seed for the random split. Defaults to 0, so repeated calls agree.
        current_threshold (float | None): A threshold to report next to the suggestion on both
            splits, such as the one a scorer uses today. Defaults to None.

    Returns:
        ThresholdSuggestion: The suggested threshold with its tuning and held-out results.

    Raises:
        ValueError: If the inputs are malformed, a split has no gold-true response (including an
            empty holdout split when ``holdout_fraction`` is above 0), every tuning score is 0.0, or
            no candidate reaches ``min_precision``.
    """
    score_matrix, gold = _validate_scores_and_labels(scores=scores, labels=labels)
    if min_precision is not None and not 0 < min_precision <= 1:
        raise ValueError("min_precision must be in (0, 1].")
    if not 0 <= holdout_fraction < 1:
        raise ValueError("holdout_fraction must be in [0, 1).")
    if current_threshold is not None:
        _validate_threshold(threshold=current_threshold, name="current_threshold")

    tuning_index, holdout_index = _stratified_split(gold=gold, holdout_fraction=holdout_fraction, seed=seed)
    tuning_scores, tuning_gold = score_matrix[:, tuning_index], gold[tuning_index]
    if not tuning_gold.any():
        raise ValueError("The tuning split has no gold-true response, so recall is undefined.")
    if holdout_fraction > 0 and not gold[holdout_index].any():
        raise ValueError(
            "The holdout split has no gold-true response; add labeled data, or set holdout_fraction=0 "
            "to tune on every response without a held-out check."
        )

    # A threshold must be in (0, 1], so a score of 0.0 can never count as true.
    candidates = np.unique(tuning_scores[tuning_scores > 0])
    if candidates.size == 0:
        raise ValueError("Every tuning score is 0.0, so no threshold in (0, 1] separates the responses.")
    operating_points = tuple(
        _operating_point(score_matrix=tuning_scores, gold=tuning_gold, threshold=float(candidate))
        for candidate in candidates
    )

    if min_precision is None:
        criterion = "max_f1"
        eligible = list(operating_points)
        ranking_key = "f1_score"
    else:
        criterion = "max_recall_at_min_precision"
        eligible = [point for point in operating_points if point.precision >= min_precision]
        ranking_key = "recall"
        if not eligible:
            best_precision = max(point.precision for point in operating_points)
            raise ValueError(
                f"No threshold reaches a precision of {min_precision} on the tuning split; "
                f"the highest is {best_precision:.3f}."
            )
    # operating_points ascend by threshold, so max() over the reversed list breaks ties upward.
    chosen = max(reversed(eligible), key=lambda point: getattr(point, ranking_key))

    def at(*, threshold: float | None, index: np.ndarray) -> ThresholdOperatingPoint | None:
        if threshold is None or index.size == 0:
            return None
        return _operating_point(score_matrix=score_matrix[:, index], gold=gold[index], threshold=threshold)

    return ThresholdSuggestion(
        threshold=chosen.threshold,
        criterion=criterion,
        min_precision=min_precision,
        tuning_point=chosen,
        holdout_point=at(threshold=chosen.threshold, index=holdout_index),
        operating_points=operating_points,
        current_threshold=current_threshold,
        current_tuning_point=at(threshold=current_threshold, index=tuning_index),
        current_holdout_point=at(threshold=current_threshold, index=holdout_index),
        num_tuning_responses=int(tuning_index.size),
        num_holdout_responses=int(holdout_index.size),
        num_scorer_trials=int(score_matrix.shape[0]),
    )


async def suggest_threshold_async(
    *,
    scorer: FloatScaleThresholdScorer,
    labeled_dataset: HumanLabeledDataset,
    num_scorer_trials: int = 1,
    max_concurrency: int = 10,
    min_precision: float | None = None,
    holdout_fraction: float = 0.5,
    seed: int | None = 0,
) -> ThresholdSuggestion:
    """
    Score an OBJECTIVE dataset with a threshold scorer and suggest a threshold for it.

    Each response is scored the way ``ObjectiveScorerEvaluator`` scores it, but the float value
    the threshold was applied to is kept instead of the true/false verdict. The gold label of a
    response is the majority vote of its human labels; a split vote counts as false, as in the
    evaluator. Responses with a trial that returned no float score are left out and counted in
    ``num_excluded_responses``. The scorer's own threshold is reported as ``current_threshold``.

    The scorer is not changed. To use the suggestion, create a new ``FloatScaleThresholdScorer``
    with ``threshold=suggestion.threshold``.

    Args:
        scorer (FloatScaleThresholdScorer): The threshold scorer whose threshold to tune.
        labeled_dataset (HumanLabeledDataset): An OBJECTIVE dataset with one assistant response per entry.
        num_scorer_trials (int): Scoring runs per response. Must be at least 1. Defaults to 1.
        max_concurrency (int): Maximum number of concurrent scoring requests. Defaults to 10.
        min_precision (float | None): See ``suggest_threshold``. Defaults to None.
        holdout_fraction (float): See ``suggest_threshold``. Defaults to 0.5.
        seed (int | None): See ``suggest_threshold``. Defaults to 0.

    Returns:
        ThresholdSuggestion: The suggested threshold with its tuning and held-out results.

    Raises:
        ValueError: If ``num_scorer_trials`` is less than 1, the dataset is not a valid OBJECTIVE
            dataset, no response has a float score, or ``suggest_threshold`` rejects the scores.
    """
    if num_scorer_trials < 1:
        raise ValueError("num_scorer_trials must be at least 1.")

    evaluator = ObjectiveScorerEvaluator(scorer=scorer)
    responses, human_scores, objectives = await evaluator._validate_and_extract_data_async(labeled_dataset)
    # Entries may have different numbers of raters, so take each majority vote separately.
    gold = np.array([np.round(np.median(entry_scores)) == 1 for entry_scores in human_scores], dtype=bool)

    trial_scores = np.full((num_scorer_trials, len(responses)), np.nan)
    for trial in range(num_scorer_trials):
        score_groups = await evaluator._score_responses_grouped_async(
            responses=responses,
            objectives=objectives or [],
            max_concurrency=max_concurrency,
        )
        for index, scores in enumerate(score_groups):
            score = evaluator._select_evaluation_score(scores=scores, harm_category=None)
            if score is None or score.is_undetermined:
                continue
            feedback = FloatFeedback.read(score)
            if feedback is not None:
                trial_scores[trial, index] = feedback.value

    determined = ~np.isnan(trial_scores).any(axis=0)
    if not determined.any():
        raise ValueError("The scorer returned no float score for any response, so there is nothing to tune on.")

    suggestion = suggest_threshold(
        scores=trial_scores[:, determined],
        labels=gold[determined],
        min_precision=min_precision,
        holdout_fraction=holdout_fraction,
        seed=seed,
        current_threshold=scorer.threshold,
    )
    return replace(suggestion, num_excluded_responses=int((~determined).sum()))


def _validate_scores_and_labels(
    *,
    scores: Sequence[float] | np.ndarray,
    labels: Sequence[bool] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Check the inputs and return scores shaped ``(num_trials, num_responses)`` with boolean labels.

    Returns:
        tuple[np.ndarray, np.ndarray]: The score matrix and the gold labels.

    Raises:
        ValueError: If the shapes disagree, a score is outside ``[0, 1]``, or a label is not boolean.
    """
    score_matrix = np.asarray(scores, dtype=np.float64)
    if score_matrix.ndim == 1:
        score_matrix = score_matrix[np.newaxis, :]
    if score_matrix.ndim != 2 or score_matrix.size == 0:
        raise ValueError("scores must be a non-empty 1-D or (num_trials, num_responses) array.")
    if not np.isfinite(score_matrix).all() or (score_matrix < 0).any() or (score_matrix > 1).any():
        raise ValueError("Every score must be a finite number in [0, 1].")

    label_array = np.asarray(labels)
    if label_array.ndim != 1 or label_array.shape[0] != score_matrix.shape[1]:
        raise ValueError(
            f"labels must have one entry per response ({score_matrix.shape[1]}), but has shape {label_array.shape}."
        )
    if label_array.dtype != np.bool_:
        if not np.isin(label_array, [0, 1]).all():
            raise ValueError("Every label must be a boolean (or 0/1).")
        label_array = label_array.astype(bool)
    return score_matrix, label_array


def _validate_threshold(*, threshold: float, name: str) -> None:
    """
    Check that a threshold is one ``FloatScaleThresholdScorer`` accepts.

    Raises:
        ValueError: If the threshold is not a finite number in ``(0, 1]``.
    """
    if not np.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError(f"{name} must be a finite number in (0, 1].")


def _stratified_split(*, gold: np.ndarray, holdout_fraction: float, seed: int | None) -> tuple[np.ndarray, np.ndarray]:
    """
    Split response indices into tuning and holdout parts with the same share of gold-true labels.

    Returns:
        tuple[np.ndarray, np.ndarray]: The sorted tuning and holdout indices.
    """
    if holdout_fraction == 0:
        return np.arange(gold.size), np.array([], dtype=np.int64)
    rng = np.random.default_rng(seed)
    holdout_parts = []
    for label in (True, False):
        members = np.flatnonzero(gold == label)
        holdout_parts.append(rng.permutation(members)[: round(members.size * holdout_fraction)])
    holdout_index = np.sort(np.concatenate(holdout_parts))
    tuning_index = np.setdiff1d(np.arange(gold.size), holdout_index)
    return tuning_index, holdout_index


def _operating_point(*, score_matrix: np.ndarray, gold: np.ndarray, threshold: float) -> ThresholdOperatingPoint:
    """
    Count every trial's verdict at ``threshold`` against the gold labels.

    Returns:
        ThresholdOperatingPoint: The confusion counts and rates.
    """
    predicted = score_matrix >= threshold
    actual = np.broadcast_to(gold, score_matrix.shape)
    true_positive = int(np.sum(predicted & actual))
    false_positive = int(np.sum(predicted & ~actual))
    true_negative = int(np.sum(~predicted & ~actual))
    false_negative = int(np.sum(~predicted & actual))
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    # From the integer counts, so equal F1 scores compare equal and ties break toward the higher threshold.
    f1_score = 2 * true_positive / (2 * true_positive + false_positive + false_negative) if true_positive else 0.0
    specificity = true_negative / (true_negative + false_positive) if true_negative + false_positive else None
    return ThresholdOperatingPoint(
        threshold=threshold,
        true_positive=true_positive,
        false_positive=false_positive,
        true_negative=true_negative,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        specificity=specificity,
    )
