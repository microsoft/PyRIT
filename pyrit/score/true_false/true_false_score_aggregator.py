# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import functools
from collections.abc import Callable, Iterable

from pyrit.models import Score, UndeterminedScoreError
from pyrit.score.score_aggregator_result import ScoreAggregatorResult
from pyrit.score.score_utils import (
    combine_metadata_and_categories,
    format_score_for_rationale,
)

BinaryBoolOp = Callable[[bool | None, bool | None], bool | None]
TrueFalseAggregatorFunc = Callable[[Iterable[Score]], ScoreAggregatorResult]


def _verdict(score: Score) -> bool | None:
    """
    Read a score's verdict, or None when it could not reach one.

    Returns:
        bool | None: The boolean verdict, or None when the score is undetermined.
    """
    try:
        return bool(score.get_value())
    except UndeterminedScoreError:
        return None


def _and(left: bool | None, right: bool | None) -> bool | None:
    """
    Combine two verdicts under AND, where None means undetermined.

    One definite False settles an AND regardless of what else could not be observed.

    Returns:
        bool | None: The combined verdict.
    """
    if left is False or right is False:
        return False
    if left is None or right is None:
        return None
    return True


def _or(left: bool | None, right: bool | None) -> bool | None:
    """
    Combine two verdicts under OR, where None means undetermined.

    One definite True settles an OR regardless of what else could not be observed.

    Returns:
        bool | None: The combined verdict.
    """
    if left is True or right is True:
        return True
    if left is None or right is None:
        return None
    return False


def _build_rationale(
    scores: list[Score],
    *,
    result: bool | None,
    true_msg: str,
    false_msg: str,
    undetermined_msg: str,
) -> tuple[str, str]:
    """
    Build description and rationale for aggregated true/false scores.

    Args:
        scores: List of Score objects to aggregate.
        result: The boolean result of the aggregation, or None when undetermined.
        true_msg: Description to use when result is True.
        false_msg: Description to use when result is False.
        undetermined_msg: Description to use when no verdict was reachable.

    Returns:
        Tuple of (description, rationale) strings.
    """
    if len(scores) == 1:
        description = scores[0].score_value_description or ""
        rationale = scores[0].score_rationale or ""
    else:
        description = undetermined_msg if result is None else (true_msg if result else false_msg)
        rationale = "\n".join(format_score_for_rationale(s) for s in scores)

    return description, rationale


def _create_aggregator(
    name: str,
    *,
    result_func: Callable[[list[bool | None]], bool | None],
    true_msg: str,
    false_msg: str,
) -> TrueFalseAggregatorFunc:
    """
    Create a True/False aggregator using a result function over boolean values.

    Args:
        name (str): Name of the aggregator variant.
        result_func (Callable[[list[bool | None]], bool | None]): Function applied to the list of
            verdicts to compute the aggregation result. ``None`` entries are undetermined
            constituent scores, and a ``None`` result means no verdict was reachable.
        true_msg (str): Description to use when the result is True.
        false_msg (str): Description to use when the result is False.

    Returns:
        TrueFalseAggregatorFunc: Aggregator function that reduces a sequence of true/false Scores
            into a single ScoreAggregatorResult.
    """
    undetermined_msg = f"No verdict was reachable in a {name} composite scorer."

    def aggregator(scores: Iterable[Score]) -> ScoreAggregatorResult:
        # Materialize before validating: `scores` is an Iterable, so validating by
        # iterating it first would exhaust a generator and leave nothing to aggregate.
        scores_list = list(scores)
        for s in scores_list:
            if s.score_type != "true_false":
                raise ValueError("All scores must be of type 'true_false'.")

        if not scores_list:
            # No scores; return a neutral result
            return ScoreAggregatorResult(
                value=False,
                description=f"No scores provided to {name} composite scorer.",
                rationale="",
                metadata={},
                category=[],
            )

        bool_values = [_verdict(s) for s in scores_list]
        result = result_func(bool_values)

        description, rationale = _build_rationale(
            scores_list,
            result=result,
            true_msg=true_msg,
            false_msg=false_msg,
            undetermined_msg=undetermined_msg,
        )
        metadata, category = combine_metadata_and_categories(scores_list)

        return ScoreAggregatorResult(
            value=result,
            description=description,
            rationale=rationale,
            metadata=metadata,
            category=category,
        )

    aggregator.__name__ = f"{name}_"
    return aggregator


def _create_binary_aggregator(
    name: str,
    op: BinaryBoolOp,
    true_msg: str,
    false_msg: str,
) -> TrueFalseAggregatorFunc:
    """
    Turn a binary operator over verdicts (e.g. ``_and``) into an aggregation function.

    Args:
        name (str): Name of the aggregator variant.
        op (BinaryBoolOp): Binary three-valued operator to apply.
        true_msg (str): Description to use when the result is True.
        false_msg (str): Description to use when the result is False.

    Returns:
        TrueFalseAggregatorFunc: Aggregator function that reduces scores using the binary operator.
    """
    return _create_aggregator(
        name,
        result_func=lambda bs, _op=op: functools.reduce(_op, bs),
        true_msg=true_msg,
        false_msg=false_msg,
    )


def _majority(values: list[bool | None]) -> bool | None:
    """
    Apply a strict majority, treating any undetermined constituent as blocking.

    Returns:
        bool | None: The majority verdict, or None when a constituent was undetermined.
    """
    if any(value is None for value in values):
        return None
    return sum(bool(value) for value in values) > len(values) / 2


# True/False aggregators (return list with single score)
class TrueFalseScoreAggregator:
    """
    Namespace for true/false score aggregators that return a single aggregated score.

    All aggregators return a list containing one ScoreAggregatorResult that combines
    all input scores together, preserving all categories.
    """

    AND: TrueFalseAggregatorFunc = _create_binary_aggregator(
        "AND",
        _and,
        "All constituent scorers returned True in an AND composite scorer.",
        "At least one constituent scorer returned False in an AND composite scorer.",
    )

    OR: TrueFalseAggregatorFunc = _create_binary_aggregator(
        "OR",
        _or,
        "At least one constituent scorer returned True in an OR composite scorer.",
        "All constituent scorers returned False in an OR composite scorer.",
    )

    MAJORITY: TrueFalseAggregatorFunc = _create_aggregator(
        "MAJORITY",
        result_func=_majority,
        true_msg="A strict majority of constituent scorers returned True in a MAJORITY composite scorer.",
        false_msg="A strict majority of constituent scorers did not return True in a MAJORITY composite scorer.",
    )
