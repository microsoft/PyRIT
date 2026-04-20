# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections import defaultdict
from typing import Callable, Dict, Iterable, List

from pyrit.models import Score
from pyrit.score.score_aggregator_result import ScoreAggregatorResult
from pyrit.score.score_utils import (
    combine_metadata_and_categories,
    format_score_for_rationale,
)

FloatScaleOp = Callable[[List[float]], float]
FloatScaleAggregatorFunc = Callable[[Iterable[Score]], List[ScoreAggregatorResult]]


def _build_rationale(scores: List[Score], *, aggregate_description: str) -> tuple[str, str]:
    """
    Build description and rationale for aggregated scores.

    Args:
        scores: List of Score objects to aggregate.
        aggregate_description: Base description for the aggregated result.

    Returns:
        Tuple of (description, rationale) strings.
    """
    if len(scores) == 1:
        description = scores[0].score_value_description or ""
        rationale = scores[0].score_rationale or ""
    else:
        description = aggregate_description
        # Only include scores with non-empty rationales
        rationale_parts = [format_score_for_rationale(s) for s in scores if s.score_rationale]
        rationale = "\n".join(rationale_parts) if rationale_parts else ""

    return description, rationale


def _create_aggregator(
    name: str,
    *,
    result_func: FloatScaleOp,
    aggregate_description: str,
    raise_on_empty: bool = False,
) -> FloatScaleAggregatorFunc:
    """
    Create a float-scale aggregator using a result function over float values.

    Args:
        name (str): Name of the aggregator variant.
        result_func (FloatScaleOp): Function applied to the list of float values to compute the aggregation result.
        aggregate_description (str): Base description for the aggregated result.
        raise_on_empty (bool): Whether to raise ValueError when no scores are provided. Defaults to False.

    Returns:
        FloatScaleAggregatorFunc: Aggregator function that reduces a sequence of float-scale Scores
            into a list containing a single ScoreAggregatorResult with a float value in [0, 1].
    """

    def aggregator(scores: Iterable[Score]) -> List[ScoreAggregatorResult]:
        # Validate types and normalize input
        for s in scores:
            if s.score_type != "float_scale":
                raise ValueError("All scores must be of type 'float_scale'.")

        scores_list = list(scores)
        if not scores_list:
            if raise_on_empty:
                raise ValueError("No scores available for aggregation")
            # No scores; return a neutral result
            return [
                ScoreAggregatorResult(
                    value=0.0,
                    description=f"No scores provided to {name} composite scorer.",
                    rationale="",
                    metadata={},
                    category=[],
                )
            ]

        float_values = [float(s.get_value()) for s in scores_list]
        result = result_func(float_values)

        # Clamp result to [0, 1] defensively
        result = max(0.0, min(1.0, result))

        description, rationale = _build_rationale(scores_list, aggregate_description=aggregate_description)
        metadata, category = combine_metadata_and_categories(scores_list)

        return [
            ScoreAggregatorResult(
                value=result,
                description=description,
                rationale=rationale,
                metadata=metadata,
                category=category,
            )
        ]

    aggregator.__name__ = f"{name}_"
    return aggregator


# Float scale aggregators (return list with single score)
class FloatScaleScoreAggregator:
    """
    Namespace for float scale score aggregators that return a single aggregated score.

    All aggregators return a list containing one ScoreAggregatorResult that combines
    all input scores together, preserving all categories.
    """

    AVERAGE: FloatScaleAggregatorFunc = _create_aggregator(
        "AVERAGE",
        result_func=lambda xs: round(sum(xs) / len(xs), 10) if xs else 0.0,
        aggregate_description="Average of constituent scorers in an AVERAGE composite scorer.",
    )

    MAX: FloatScaleAggregatorFunc = _create_aggregator(
        "MAX",
        result_func=max,
        aggregate_description="Maximum value among constituent scorers in a MAX composite scorer.",
    )

    MIN: FloatScaleAggregatorFunc = _create_aggregator(
        "MIN",
        result_func=min,
        aggregate_description="Minimum value among constituent scorers in a MIN composite scorer.",
    )

    AVERAGE_RAISE_ON_EMPTY: FloatScaleAggregatorFunc = _create_aggregator(
        "AVERAGE_RAISE_ON_EMPTY",
        result_func=lambda xs: round(sum(xs) / len(xs), 10) if xs else 0.0,
        aggregate_description="Average of constituent scorers in an AVERAGE composite scorer.",
        raise_on_empty=True,
    )

    MAX_RAISE_ON_EMPTY: FloatScaleAggregatorFunc = _create_aggregator(
        "MAX_RAISE_ON_EMPTY",
        result_func=max,
        aggregate_description="Maximum value among constituent scorers in a MAX composite scorer.",
        raise_on_empty=True,
    )

    MIN_RAISE_ON_EMPTY: FloatScaleAggregatorFunc = _create_aggregator(
        "MIN_RAISE_ON_EMPTY",
        result_func=min,
        aggregate_description="Minimum value among constituent scorers in a MIN composite scorer.",
        raise_on_empty=True,
    )


def _create_aggregator_by_category(
    name: str,
    *,
    result_func: FloatScaleOp,
    aggregate_description: str,
    group_by_category: bool = True,
) -> FloatScaleAggregatorFunc:
    """
    Create a float-scale aggregator that can optionally group scores by category.

    When group_by_category=True (default), scores are grouped by their category and each
    category is aggregated separately, returning multiple ScoreAggregatorResult objects.
    This is useful for scorers like AzureContentFilterScorer that return multiple scores
    per item (e.g., one per harm category).

    When group_by_category=False, all scores are aggregated together regardless of category,
    returning a single ScoreAggregatorResult with all categories combined.

    Args:
        name (str): Name of the aggregator variant.
        result_func (FloatScaleOp): Function applied to the list of float values to compute the aggregation result.
        aggregate_description (str): Base description for the aggregated result.
        group_by_category (bool): Whether to group scores by category. Defaults to True.

    Returns:
        FloatScaleMultiScoreAggregator: Aggregator function that reduces a sequence of float-scale Scores
            into one or more ScoreAggregatorResult objects.
    """

    def aggregator(scores: Iterable[Score]) -> List[ScoreAggregatorResult]:
        # Validate types and normalize input
        for s in scores:
            if s.score_type != "float_scale":
                raise ValueError("All scores must be of type 'float_scale'.")

        scores_list = list(scores)
        if not scores_list:
            # No scores; return a neutral result
            return [
                ScoreAggregatorResult(
                    value=0.0,
                    description=f"No scores provided to {name} composite scorer.",
                    rationale="",
                    metadata={},
                    category=[],
                )
            ]

        if not group_by_category:
            # Original behavior: aggregate all scores together
            float_values = [float(s.get_value()) for s in scores_list]
            result = result_func(float_values)
            result = max(0.0, min(1.0, result))

            description, rationale = _build_rationale(scores_list, aggregate_description=aggregate_description)
            metadata, category = combine_metadata_and_categories(scores_list)

            return [
                ScoreAggregatorResult(
                    value=result,
                    description=description,
                    rationale=rationale,
                    metadata=metadata,
                    category=category,
                )
            ]

        # Group scores by category
        # We need to handle the fact that score_category can be None, [], or a list of categories
        category_groups: Dict[str, List[Score]] = defaultdict(list)

        for score in scores_list:
            categories = getattr(score, "score_category", None) or []
            # Filter out empty strings from categories
            categories = [c for c in categories if c]

            if not categories:
                # If no category (or only empty strings), use empty string as key
                category_groups[""].append(score)
            else:
                # Use the first category as the primary grouping key
                # (most scorers should have only one category per score)
                primary_category = categories[0]
                category_groups[primary_category].append(score)

        # Aggregate each category group separately
        results: List[ScoreAggregatorResult] = []

        for category_name, category_scores in sorted(category_groups.items()):
            float_values = [float(s.get_value()) for s in category_scores]
            result = result_func(float_values)
            result = max(0.0, min(1.0, result))

            # Build description and rationale for this category group
            if len(category_scores) == 1:
                description = category_scores[0].score_value_description or ""
                rationale = category_scores[0].score_rationale or ""
            else:
                # Add category suffix to description if we have a category name
                category_suffix = f" (Category: {category_name})" if category_name else ""
                description = f"{aggregate_description}{category_suffix}"
                # Use generic description for rationale, not "Frame score"
                rationale = _build_rationale(category_scores, aggregate_description="")[1]

            # Combine metadata and categories for this group
            metadata, category_list = combine_metadata_and_categories(category_scores)

            results.append(
                ScoreAggregatorResult(
                    value=result,
                    description=description,
                    rationale=rationale,
                    metadata=metadata,
                    category=category_list,
                )
            )

        return results

    aggregator.__name__ = f"{name}_by_category_"
    return aggregator


# Category-aware aggregators (group by category and return multiple scores)
class FloatScaleScorerByCategory:
    """
    Namespace for float scale score aggregators that group by category.

    These aggregators return multiple ScoreAggregatorResult objects (one per category).
    Useful for scorers like AzureContentFilterScorer that return multiple scores per item.
    """

    AVERAGE: FloatScaleAggregatorFunc = _create_aggregator_by_category(
        "AVERAGE",
        result_func=lambda xs: round(sum(xs) / len(xs), 10) if xs else 0.0,
        aggregate_description="Average of constituent scorers",
        group_by_category=True,
    )

    MAX: FloatScaleAggregatorFunc = _create_aggregator_by_category(
        "MAX",
        result_func=max,
        aggregate_description="Maximum value among constituent scorers",
        group_by_category=True,
    )

    MIN: FloatScaleAggregatorFunc = _create_aggregator_by_category(
        "MIN",
        result_func=min,
        aggregate_description="Minimum value among constituent scorers",
        group_by_category=True,
    )


# Non-category-aware aggregators (combine all categories into one score)
class FloatScaleScorerAllCategories:
    """
    Namespace for float scale score aggregators that combine all categories.

    These aggregators ignore category boundaries and aggregate all scores together,
    returning a single ScoreAggregatorResult with all categories combined.
    """

    MAX: FloatScaleAggregatorFunc = _create_aggregator_by_category(
        "MAX",
        result_func=max,
        aggregate_description="Maximum value among all constituent scorers across categories",
        group_by_category=False,
    )

    AVERAGE: FloatScaleAggregatorFunc = _create_aggregator_by_category(
        "AVERAGE",
        result_func=lambda xs: round(sum(xs) / len(xs), 10) if xs else 0.0,
        aggregate_description="Average of all constituent scorers across categories",
        group_by_category=False,
    )

    MIN: FloatScaleAggregatorFunc = _create_aggregator_by_category(
        "MIN",
        result_func=min,
        aggregate_description="Minimum value among all constituent scorers across categories",
        group_by_category=False,
    )
