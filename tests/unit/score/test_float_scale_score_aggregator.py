# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import pytest

from pyrit.models import ComponentIdentifier, Score, ScoreStatus
from pyrit.score.float_scale.float_scale_score_aggregator import (
    FloatScaleScoreAggregator,
    FloatScaleScorerAllCategories,
    FloatScaleScorerByCategory,
)

# Reusable ComponentIdentifier for tests
_TEST_SCORER_ID = ComponentIdentifier(
    class_name="UnitTestScorer",
    class_module="tests.unit.score",
)


def _mk_score(
    val: float | None,
    *,
    category: list[str] | None = None,
    metadata: dict[str, str | int | float] | None = None,
    prr_id: str = "1",
    rationale: str = "",
) -> Score:
    """Helper to create a float scale score."""
    return Score(
        score_value=str(val) if val is not None else None,
        status=ScoreStatus.UNDETERMINED if val is None else ScoreStatus.COMPLETE,
        score_value_description=f"Score of {val}",
        score_type="float_scale",
        score_category=category,
        score_rationale=rationale,
        score_metadata=metadata,
        message_piece_id=prr_id,
        scorer_class_identifier=_TEST_SCORER_ID,
        objective=None,
    )


def test_float_scale_aggregator_accepts_a_generator():
    scores = (_mk_score(value, category=["test"]) for value in (0.3, 0.7))
    results = FloatScaleScoreAggregator.MAX(scores)
    assert len(results) == 1
    assert results[0].value == 0.7


def test_float_scale_by_category_aggregator_accepts_a_generator():
    scores = (_mk_score(value, category=["test"]) for value in (0.3, 0.7))
    results = FloatScaleScorerByCategory.MAX(scores)
    assert len(results) == 1
    assert results[0].value == 0.7


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


@pytest.mark.parametrize(
    "aggregator",
    [FloatScaleScoreAggregator.MAX, FloatScaleScorerAllCategories.MAX],
)
def test_undetermined_aggregate_preserves_categories_and_metadata(aggregator):
    scores = [
        _mk_score(
            0.5,
            category=["Hate"],
            metadata={"shared": "same", "complete_only": 1},
        ),
        _mk_score(
            None,
            category=["Violence"],
            metadata={"shared": "same", "undetermined_only": 2},
        ),
    ]

    result = aggregator(scores)[0]

    assert result.value is None
    assert result.category == ["Hate", "Violence"]
    assert result.metadata == {
        "shared": "same",
        "complete_only": 1,
        "undetermined_only": 2,
    }


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


def test_by_category_extrema_resolve_conflicting_metadata_from_selected_score() -> None:
    scores = [
        _mk_score(0.3, category=["Hate"], metadata={"severity": 2, "low_only": 1}),
        _mk_score(0.7, category=["Hate"], metadata={"severity": 5, "high_only": 1}),
    ]

    max_result = FloatScaleScorerByCategory.MAX(scores)[0]
    min_result = FloatScaleScorerByCategory.MIN(scores)[0]

    assert max_result.value == 0.7
    assert max_result.metadata == {"severity": 5, "low_only": 1, "high_only": 1}
    assert min_result.value == 0.3
    assert min_result.metadata == {"severity": 2, "low_only": 1, "high_only": 1}


def test_by_category_max_keeps_categories_and_metadata_separate() -> None:
    scores = [
        _mk_score(0.3, category=["Hate"], metadata={"severity": 2}),
        _mk_score(0.7, category=["Hate"], metadata={"severity": 5}),
        _mk_score(0.8, category=["Violence"], metadata={"severity": 6}),
        _mk_score(0.4, category=["Violence"], metadata={"severity": 3}),
    ]

    results = FloatScaleScorerByCategory.MAX(scores)

    assert [(result.category, result.value, result.metadata) for result in results] == [
        (["Hate"], 0.7, {"severity": 5}),
        (["Violence"], 0.8, {"severity": 6}),
    ]


def test_by_category_max_uses_first_winner_metadata_for_ties() -> None:
    scores = [
        _mk_score(0.7, category=["Hate"], metadata={"source": "first"}),
        _mk_score(0.7, category=["Hate"], metadata={"source": "second"}),
        _mk_score(0.3, category=["Hate"], metadata={"source": "lower"}),
    ]

    result = FloatScaleScorerByCategory.MAX(scores)[0]

    assert result.value == 0.7
    assert result.metadata == {"source": "first"}


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


def test_by_category_undetermined_aggregate_preserves_group_context():
    scores = [
        _mk_score(0.5, category=["Hate"], metadata={"shared": "same"}),
        _mk_score(None, category=["Hate"], metadata={"undetermined": 1}),
        _mk_score(0.7, category=["Violence"], metadata={"complete": 1}),
    ]

    results = FloatScaleScorerByCategory.MAX(scores)

    hate_result = next(result for result in results if result.category == ["Hate"])
    violence_result = next(result for result in results if result.category == ["Violence"])
    assert hate_result.value is None
    assert hate_result.metadata == {"shared": "same", "undetermined": 1}
    assert violence_result.value == 0.7


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


def test_aggregators_accept_generators():
    """
    Aggregators are typed to take an Iterable, so a generator must aggregate the same
    as the equivalent list. Validating by iterating before materializing exhausted the
    generator and silently produced the empty-input result (0.0).
    """
    values = [0.3, 0.9, 0.5]

    aggregators = [
        FloatScaleScoreAggregator.MAX,
        FloatScaleScoreAggregator.MIN,
        FloatScaleScoreAggregator.AVERAGE,
        FloatScaleScorerByCategory.MAX,
        FloatScaleScorerAllCategories.MAX,
    ]
    for aggregator in aggregators:
        from_list = aggregator([_mk_score(v, category=["harm"]) for v in values])
        from_generator = aggregator(_mk_score(v, category=["harm"]) for v in values)
        assert [r.value for r in from_generator] == [r.value for r in from_list]


def test_raise_on_empty_aggregator_accepts_generators():
    """A generator with scores must not trip the empty-input guard."""
    values = [0.3, 0.9, 0.5]
    results = FloatScaleScoreAggregator.MAX_RAISE_ON_EMPTY(_mk_score(v) for v in values)
    assert results[0].value == 0.9


def test_generator_of_wrong_type_still_raises():
    """Materializing first must not weaken type validation."""
    bad = Score(
        score_value="true",
        score_value_description="",
        score_type="true_false",
        score_category=["test"],
        score_rationale="",
        score_metadata=None,
        message_piece_id="1",
        scorer_class_identifier=_TEST_SCORER_ID,
        objective=None,
    )
    with pytest.raises(ValueError, match="must be of type 'float_scale'"):
        FloatScaleScoreAggregator.MAX(s for s in [bad])
