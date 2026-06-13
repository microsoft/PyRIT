# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import functools
import logging
import operator
from collections.abc import Callable, Iterable

from pyrit.models import Score
from pyrit.score.score_aggregator_result import ScoreAggregatorResult
from pyrit.score.score_utils import (
    combine_metadata_and_categories,
    format_score_for_rationale,
)

logger = logging.getLogger(__name__)

BinaryBoolOp = Callable[[bool, bool], bool]
TrueFalseAggregatorFunc = Callable[[Iterable[Score]], ScoreAggregatorResult]

# Key set in a fallback ``Score``'s ``score_metadata`` when the scorer could not actually
# evaluate the response (no piece survived validator filtering) and a placeholder ``false``
# was returned instead of a genuine "not harmful" judgement. Defined here (rather than in
# ``true_false_scorer``) because that module imports this one; keeping the constant in the
# lower-level module avoids a circular import while giving aggregators and the base scorer a
# single source of truth. The value is the int ``1`` (truthy, JSON-safe, and within the
# declared ``dict[str, str | int | float]`` metadata type) so it survives metadata merging.
UNSCOREABLE_METADATA_KEY = "unscoreable"


def _build_rationale(scores: list[Score], *, result: bool, true_msg: str, false_msg: str) -> tuple[str, str]:
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


def _is_unscoreable(score: Score) -> bool:
    """
    Report whether a score is an "unscoreable" fallback rather than a real judgement.

    A score is unscoreable when its ``score_metadata`` carries
    ``UNSCOREABLE_METADATA_KEY`` (set by ``TrueFalseScorer._build_fallback_score`` when no
    piece survived validator filtering). Such a ``false`` means the scorer could not
    evaluate the response, not that the response was judged "not harmful".

    Args:
        score (Score): The constituent score to inspect.

    Returns:
        bool: ``True`` if the score is an unscoreable fallback, ``False`` otherwise.
    """
    metadata = score.score_metadata or {}
    return bool(metadata.get(UNSCOREABLE_METADATA_KEY))


def _apply_unscoreable_observability(
    *,
    name: str,
    scores: list[Score],
    result: bool,
    rationale: str,
) -> str:
    """
    Surface unscoreable abstentions so a "could not score" false is not read as "not harmful".

    When one or more constituent scores are unscoreable fallbacks (see ``_is_unscoreable``),
    the aggregate ``false`` may be hiding another scorer's confirmed ``true`` -- a red-team
    false-assurance hazard, most acute under ``AND``. This emits a ``logger.warning`` and
    appends a note to the rationale so the abstention is visible. The aggregate verdict value
    itself is never changed (non-breaking).

    Args:
        name (str): Name of the aggregator variant (e.g. ``"AND"``), used in messages.
        scores (list[Score]): The constituent scores being aggregated.
        result (bool): The already-computed boolean aggregation result. Unchanged here.
        rationale (str): The rationale built from the constituent scores.

    Returns:
        str: The rationale, with an appended abstention note when applicable; otherwise
            the rationale unchanged.
    """
    unscoreable = [s for s in scores if _is_unscoreable(s)]
    if not unscoreable:
        return rationale

    has_confirmed_true = any(s.get_value() is True for s in scores)
    # The hazard is acute when an abstention dragged an otherwise-True signal down to a
    # False aggregate (the classic AND-masking case); call that out explicitly.
    masked_true = has_confirmed_true and result is False

    count = len(unscoreable)
    note = (
        f"NOTE: {count} constituent scorer(s) could not evaluate the response (unscoreable "
        f"fallback) and contributed a placeholder 'false' to this {name} aggregate. "
        "A 'could not score' result is not the same as a genuine 'not harmful' result."
    )
    if masked_true:
        note += (
            " At least one other constituent scorer returned 'true'; this aggregate "
            "'false' may be under-reporting a confirmed success."
        )
        logger.warning(
            "%s composite aggregate is 'false' but %d sub-scorer(s) abstained (unscoreable) "
            "while at least one returned 'true'. The verdict may be masking a confirmed success; "
            "inspect the rationale before treating this as 'attack failed'.",
            name,
            count,
        )
    else:
        logger.warning(
            "%s composite aggregate includes %d unscoreable sub-score(s) that could not evaluate "
            "the response; their placeholder 'false' is not a genuine 'not harmful' judgement.",
            name,
            count,
        )

    return f"{rationale}\n{note}" if rationale else note


def _create_aggregator(
    name: str,
    *,
    result_func: Callable[[list[bool]], bool],
    true_msg: str,
    false_msg: str,
) -> TrueFalseAggregatorFunc:
    """
    Create a True/False aggregator using a result function over boolean values.

    Args:
        name (str): Name of the aggregator variant.
        result_func (Callable[[list[bool]], bool]): Function applied to the list of boolean values
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
        rationale = _apply_unscoreable_observability(name=name, scores=scores_list, result=result, rationale=rationale)
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
        result_func=lambda bs, _op=op: functools.reduce(_op, bs),
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
