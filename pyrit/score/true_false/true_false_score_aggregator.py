# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import functools
import operator
from typing import Callable, Iterable, List

from pyrit.models import Score
from pyrit.score.score_aggregator_result import ScoreAggregatorResult
from pyrit.score.score_utils import (
    combine_metadata_and_categories,
    format_score_for_rationale,
)

BinaryBoolOp = Callable[[bool, bool], bool]
TrueFalseAggregatorFunc = Callable[[Iterable[Score]], ScoreAggregatorResult]


def _build_rationale(scores: List[Score], *, result: bool, true_msg: str, false_msg: str) -> tuple[str, str]:
    """
    Build description and rationale for aggregated true/false scores.

    Args:
        scores: List of Score objects to aggregate.
        result: The boolean result of the aggregation.
        true_msg: Description to use when result is True.
        false_msg: Description to use when result is False.

    Returns:
        Tuple of (description, rationale) strings.
    """
    if len(scores) == 1:
        description = scores[0].score_value_description or ""
        rationale = scores[0].score_rationale or ""
    else:
        description = true_msg if result else false_msg
        rationale = "\n".join(format_score_for_rationale(s) for s in scores)

    return description, rationale


def _create_aggregator(
    name: str,
    *,
    result_func: Callable[[List[bool]], bool],
    true_msg: str,
    false_msg: str,
) -> TrueFalseAggregatorFunc:
    """
    Create a True/False aggregator using a result function over boolean values.

    Args:
        name (str): Name of the aggregator variant.
        result_func (Callable[[List[bool]], bool]): Function applied to the list of boolean values
            to compute the aggregation result.
        true_msg (str): Description to use when the result is True.
        false_msg (str): Description to use when the result is False.

    Returns:
        TrueFalseAggregatorFunc: Aggregator function that reduces a sequence of true/false Scores
            into a single ScoreAggregatorResult with a boolean value.
    """

    def aggregator(scores: Iterable[Score]) -> ScoreAggregatorResult:
        # Validate types and normalize input
        for s in scores:
            if s.score_type != "true_false":
                raise ValueError("All scores must be of type 'true_false'.")

        scores_list = list(scores)
        if not scores_list:
            # No scores; return a neutral result
            return ScoreAggregatorResult(
                value=False,
                description=f"No scores provided to {name} composite scorer.",
                rationale="",
                metadata={},
                category=[],
            )

        bool_values = [bool(s.get_value()) for s in scores_list]
        result = result_func(bool_values)

        description, rationale = _build_rationale(scores_list, result=result, true_msg=true_msg, false_msg=false_msg)
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
    Turn a binary Boolean operator (e.g. operator.and_) into an aggregation function.

    Args:
        name (str): Name of the aggregator variant.
        op (BinaryBoolOp): Binary boolean operator to apply.
        true_msg (str): Description to use when the result is True.
        false_msg (str): Description to use when the result is False.

    Returns:
        TrueFalseAggregatorFunc: Aggregator function that reduces scores using the binary operator.
    """
    return _create_aggregator(
        name,
        result_func=lambda bs, _op=op: functools.reduce(_op, bs),  # type: ignore[misc]
        true_msg=true_msg,
        false_msg=false_msg,
    )


# True/False aggregators (return list with single score)
class TrueFalseScoreAggregator:
    """
    Namespace for true/false score aggregators that return a single aggregated score.

    All aggregators return a list containing one ScoreAggregatorResult that combines
    all input scores together, preserving all categories.
    """

    AND: TrueFalseAggregatorFunc = _create_binary_aggregator(
        "AND",
        operator.and_,
        "All constituent scorers returned True in an AND composite scorer.",
        "At least one constituent scorer returned False in an AND composite scorer.",
    )

    OR: TrueFalseAggregatorFunc = _create_binary_aggregator(
        "OR",
        operator.or_,
        "At least one constituent scorer returned True in an OR composite scorer.",
        "All constituent scorers returned False in an OR composite scorer.",
    )

    MAJORITY: TrueFalseAggregatorFunc = _create_aggregator(
        "MAJORITY",
        result_func=lambda bs: sum(bs) > len(bs) / 2,
        true_msg="A strict majority of constituent scorers returned True in a MAJORITY composite scorer.",
        false_msg="A strict majority of constituent scorers did not return True in a MAJORITY composite scorer.",
    )
