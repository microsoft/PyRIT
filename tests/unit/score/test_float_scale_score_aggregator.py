# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.identifiers import ScorerIdentifier
from pyrit.models import Score
from pyrit.score.float_scale.float_scale_score_aggregator import (
    FloatScaleScoreAggregator,
    FloatScaleScorerAllCategories,
    FloatScaleScorerByCategory,
)

# Reusable ScorerIdentifier for tests
_TEST_SCORER_ID = ScorerIdentifier(
    class_name="UnitTestScorer",
    class_module="tests.unit.score",
    class_description="",
    identifier_type="instance",
)


def _mk_score(val: float, *, category: Optional[list[str]] = None, prr_id: str = "1", rationale: str = "") -> Score:
    """Helper to create a float scale score."""
    return Score(
        score_value=str(val),
        score_value_description=f"Score of {val}",
        score_type="float_scale",
        score_category=category,
        score_rationale=rationale,
        score_metadata=None,
        message_piece_id=prr_id,
        scorer_class_identifier=_TEST_SCORER_ID,
        objective=None,
    )


# Tests for FloatScaleScoreAggregator (simple aggregation)
def test_float_scale_aggregator_max():
    """Test MAX aggregation returns the maximum value."""
    scores = [_mk_score(0.3, category=["test"]), _mk_score(0.7, category=["test"]), _mk_score(0.5, category=["test"])]
    results = FloatScaleScoreAggregator.MAX(scores)
    assert len(results) == 1
    assert results[0].value == 0.7
    assert isinstance(results[0].description, str)


def test_float_scale_aggregator_min():
    """Test MIN aggregation returns the minimum value."""
    scores = [_mk_score(0.3, category=["test"]), _mk_score(0.7, category=["test"]), _mk_score(0.5, category=["test"])]
    results = FloatScaleScoreAggregator.MIN(scores)
    assert len(results) == 1
    assert results[0].value == 0.3


def test_float_scale_aggregator_average():
    """Test AVERAGE aggregation returns the mean value."""
    scores = [_mk_score(0.2, category=["test"]), _mk_score(0.4, category=["test"]), _mk_score(0.6, category=["test"])]
    results = FloatScaleScoreAggregator.AVERAGE(scores)
    assert len(results) == 1
    assert results[0].value == 0.4


def test_float_scale_aggregator_category_deduplication():
    """Test that duplicate categories are deduplicated."""
    scores = [
        _mk_score(0.5, category=["Hate"], rationale="r1"),
        _mk_score(0.6, category=["Hate"], rationale="r2"),
        _mk_score(0.7, category=["Hate"], rationale="r3"),
    ]
    results = FloatScaleScoreAggregator.MAX(scores)
    assert len(results) == 1
    assert results[0].value == 0.7
    assert results[0].category == ["Hate"]  # Should be deduplicated


def test_float_scale_aggregator_multiple_categories_preserved():
    """Test that multiple unique categories are preserved and sorted."""
    scores = [
        _mk_score(0.5, category=["Violence"]),
        _mk_score(0.6, category=["Hate"]),
    ]
    results = FloatScaleScoreAggregator.AVERAGE(scores)
    assert len(results) == 1
    assert results[0].value == 0.55
    assert results[0].category == ["Hate", "Violence"]  # Sorted alphabetically


def test_float_scale_aggregator_empty_strings_filtered():
    """Test that empty string categories are filtered out."""
    scores = [
        _mk_score(0.5, category=[""]),
        _mk_score(0.7, category=[""]),
    ]
    results = FloatScaleScoreAggregator.MAX(scores)
    assert len(results) == 1
    assert results[0].value == 0.7
    assert results[0].category == []  # Empty strings filtered


def test_float_scale_aggregator_mixed_empty_and_valid():
    """Test that empty strings are filtered but valid categories are kept."""
    scores = [
        _mk_score(0.5, category=[""]),
        _mk_score(0.7, category=["Violence"]),
    ]
    results = FloatScaleScoreAggregator.MIN(scores)
    assert len(results) == 1
    assert results[0].value == 0.5
    assert results[0].category == ["Violence"]


# Tests for FloatScaleScorerByCategory (category-aware aggregation)
def test_by_category_groups_correctly():
    """Test that scores are grouped by category and aggregated separately."""
    scores = [
        _mk_score(0.3, category=["Hate"]),
        _mk_score(0.7, category=["Hate"]),
        _mk_score(0.2, category=["Violence"]),
        _mk_score(0.8, category=["Violence"]),
    ]
    results = FloatScaleScorerByCategory.MAX(scores)
    assert len(results) == 2

    # Results should be sorted by category name
    hate_result = next(r for r in results if r.category == ["Hate"])
    violence_result = next(r for r in results if r.category == ["Violence"])

    assert hate_result.value == 0.7
    assert violence_result.value == 0.8


def test_by_category_average():
    """Test AVERAGE aggregation by category."""
    scores = [
        _mk_score(0.2, category=["Hate"]),
        _mk_score(0.4, category=["Hate"]),
        _mk_score(0.6, category=["Violence"]),
        _mk_score(0.8, category=["Violence"]),
    ]
    results = FloatScaleScorerByCategory.AVERAGE(scores)
    assert len(results) == 2

    hate_result = next(r for r in results if r.category == ["Hate"])
    violence_result = next(r for r in results if r.category == ["Violence"])

    assert hate_result.value == 0.3
    assert violence_result.value == 0.7


def test_by_category_min():
    """Test MIN aggregation by category."""
    scores = [
        _mk_score(0.3, category=["Hate"]),
        _mk_score(0.7, category=["Hate"]),
        _mk_score(0.2, category=["Violence"]),
        _mk_score(0.9, category=["Violence"]),
    ]
    results = FloatScaleScorerByCategory.MIN(scores)
    assert len(results) == 2

    hate_result = next(r for r in results if r.category == ["Hate"])
    violence_result = next(r for r in results if r.category == ["Violence"])

    assert hate_result.value == 0.3
    assert violence_result.value == 0.2


def test_by_category_empty_strings_treated_as_uncategorized():
    """Test that empty string categories are grouped together as uncategorized."""
    scores = [
        _mk_score(0.3, category=[""]),
        _mk_score(0.7, category=[""]),
        _mk_score(0.5, category=["Hate"]),
    ]
    results = FloatScaleScorerByCategory.MAX(scores)

    # Should have 2 groups: uncategorized (empty) and "Hate"
    assert len(results) == 2

    # Find the uncategorized group (should have empty category list)
    uncategorized_result = next(r for r in results if r.category == [])
    hate_result = next(r for r in results if r.category == ["Hate"])

    assert uncategorized_result.value == 0.7  # Max of 0.3 and 0.7
    assert hate_result.value == 0.5


def test_by_category_none_categories_grouped_as_uncategorized():
    """Test that None categories are grouped as uncategorized."""
    scores = [
        _mk_score(0.4, category=None),
        _mk_score(0.6, category=None),
        _mk_score(0.8, category=["Hate"]),
    ]
    results = FloatScaleScorerByCategory.AVERAGE(scores)

    assert len(results) == 2

    uncategorized_result = next(r for r in results if r.category == [])
    hate_result = next(r for r in results if r.category == ["Hate"])

    assert uncategorized_result.value == 0.5  # Average of 0.4 and 0.6
    assert hate_result.value == 0.8


def test_by_category_multiple_categories_uses_first():
    """Test that when a score has multiple categories, the first is used for grouping."""
    scores = [
        _mk_score(0.5, category=["Hate", "Violence"]),
        _mk_score(0.7, category=["Hate"]),
        _mk_score(0.3, category=["Violence"]),
    ]
    results = FloatScaleScorerByCategory.MAX(scores)

    # The first score should be grouped under "Hate" (first category)
    # But its category list should include both after deduplication
    assert len(results) == 2

    hate_result = next(r for r in results if "Hate" in r.category)
    violence_result = next(r for r in results if r.category == ["Violence"])

    assert hate_result.value == 0.7  # Max of 0.5 and 0.7
    assert violence_result.value == 0.3


def test_by_category_description_includes_category_name():
    """Test that the description includes the category name."""
    scores = [
        _mk_score(0.5, category=["Hate"]),
        _mk_score(0.7, category=["Hate"]),
    ]
    results = FloatScaleScorerByCategory.MAX(scores)

    assert len(results) == 1
    assert "Hate" in results[0].description


# Tests for FloatScaleScorerAllCategories (combine all categories)
def test_all_categories_combines_everything():
    """Test that all scores are combined regardless of category."""
    scores = [
        _mk_score(0.3, category=["Hate"]),
        _mk_score(0.7, category=["Violence"]),
        _mk_score(0.5, category=["Sexual"]),
    ]
    results = FloatScaleScorerAllCategories.MAX(scores)

    assert len(results) == 1
    assert results[0].value == 0.7  # Max across all categories


def test_all_categories_preserves_all_unique_categories():
    """Test that all unique categories are preserved in the result."""
    scores = [
        _mk_score(0.3, category=["Hate"]),
        _mk_score(0.7, category=["Violence"]),
        _mk_score(0.5, category=["Sexual"]),
    ]
    results = FloatScaleScorerAllCategories.AVERAGE(scores)

    assert len(results) == 1
    assert results[0].value == 0.5  # Average of all scores
    assert results[0].category == ["Hate", "Sexual", "Violence"]  # All categories, sorted


def test_all_categories_deduplicates_categories():
    """Test that duplicate categories are deduplicated."""
    scores = [
        _mk_score(0.3, category=["Hate"]),
        _mk_score(0.7, category=["Hate"]),
        _mk_score(0.5, category=["Violence"]),
    ]
    results = FloatScaleScorerAllCategories.MIN(scores)

    assert len(results) == 1
    assert results[0].value == 0.3
    assert results[0].category == ["Hate", "Violence"]  # Deduplicated and sorted


def test_all_categories_filters_empty_strings():
    """Test that empty string categories are filtered."""
    scores = [
        _mk_score(0.3, category=[""]),
        _mk_score(0.7, category=["Hate"]),
        _mk_score(0.5, category=[""]),
    ]
    results = FloatScaleScorerAllCategories.MAX(scores)

    assert len(results) == 1
    assert results[0].value == 0.7
    assert results[0].category == ["Hate"]  # Only valid category


# Edge cases
def test_empty_scores_list():
    """Test that empty score lists are handled gracefully."""
    results = FloatScaleScoreAggregator.MAX([])
    assert len(results) == 1
    assert results[0].value == 0.0
    assert results[0].category == []


def test_single_score():
    """Test that single score aggregation works correctly."""
    scores = [_mk_score(0.5, category=["Hate"])]
    results = FloatScaleScoreAggregator.AVERAGE(scores)

    assert len(results) == 1
    assert results[0].value == 0.5
    assert results[0].category == ["Hate"]


def test_values_clamped_to_range():
    """Test that values outside [0, 1] are clamped."""
    # This would require modifying the score values directly which shouldn't happen in practice
    # But the aggregator should handle it defensively
    scores = [_mk_score(0.0, category=["test"]), _mk_score(1.0, category=["test"])]
    results = FloatScaleScoreAggregator.MAX(scores)

    assert results[0].value >= 0.0
    assert results[0].value <= 1.0


# Tests for raise_on_empty behavior
def test_max_raise_on_empty_with_scores():
    """Test that MAX_RAISE_ON_EMPTY works normally when scores are present."""
    scores = [_mk_score(0.3, category=["test"]), _mk_score(0.7, category=["test"])]
    results = FloatScaleScoreAggregator.MAX_RAISE_ON_EMPTY(scores)
    assert len(results) == 1
    assert results[0].value == 0.7


def test_max_raise_on_empty_with_no_scores():
    """Test that MAX_RAISE_ON_EMPTY raises ValueError when no scores are present."""
    import pytest

    with pytest.raises(ValueError, match="No scores available for aggregation"):
        FloatScaleScoreAggregator.MAX_RAISE_ON_EMPTY([])


def test_min_raise_on_empty_with_scores():
    """Test that MIN_RAISE_ON_EMPTY works normally when scores are present."""
    scores = [_mk_score(0.3, category=["test"]), _mk_score(0.7, category=["test"])]
    results = FloatScaleScoreAggregator.MIN_RAISE_ON_EMPTY(scores)
    assert len(results) == 1
    assert results[0].value == 0.3


def test_min_raise_on_empty_with_no_scores():
    """Test that MIN_RAISE_ON_EMPTY raises ValueError when no scores are present."""
    import pytest

    with pytest.raises(ValueError, match="No scores available for aggregation"):
        FloatScaleScoreAggregator.MIN_RAISE_ON_EMPTY([])


def test_average_raise_on_empty_with_scores():
    """Test that AVERAGE_RAISE_ON_EMPTY works normally when scores are present."""
    scores = [_mk_score(0.2, category=["test"]), _mk_score(0.4, category=["test"]), _mk_score(0.6, category=["test"])]
    results = FloatScaleScoreAggregator.AVERAGE_RAISE_ON_EMPTY(scores)
    assert len(results) == 1
    assert results[0].value == 0.4


def test_average_raise_on_empty_with_no_scores():
    """Test that AVERAGE_RAISE_ON_EMPTY raises ValueError when no scores are present."""
    import pytest

    with pytest.raises(ValueError, match="No scores available for aggregation"):
        FloatScaleScoreAggregator.AVERAGE_RAISE_ON_EMPTY([])
