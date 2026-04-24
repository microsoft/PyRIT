# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import numpy as np
import pytest

from pyrit.score.scorer_evaluation.krippendorff import (
    _build_coincidence_matrix,
    _build_expected_matrix,
    _build_ordinal_distance_matrix,
    _build_value_counts_matrix,
    _compute_alpha_from_disagreements,
    _validate_and_prepare_data,
    krippendorff_alpha,
)


class TestKrippendorffAlpha:
    """Test cases for Krippendorff's alpha inter-rater reliability."""

    def test_perfect_agreement(self):
        """Test that perfect agreement returns alpha = 1.0."""
        # All raters agree completely
        data = np.array([[1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert result == 1.0

    def test_perfect_agreement_single_value(self):
        """Test that all identical ratings return alpha = 1.0."""
        # All ratings are the same value
        data = np.array([[2, 2, 2], [2, 2, 2]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert result == 1.0

    def test_complete_disagreement(self):
        """Test that complete disagreement returns alpha <= 0."""
        # Maximum disagreement pattern
        data = np.array([[1, 2, 3, 4], [4, 3, 2, 1]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert result <= 0.0

    def test_partial_agreement(self):
        """Test partial agreement returns alpha between 0 and 1."""
        # Some agreement, some disagreement
        data = np.array([[1, 2, 3, 4, 5], [1, 2, 3, 5, 4], [1, 3, 3, 4, 5]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert 0.0 <= result <= 1.0

    def test_with_missing_values_nan(self):
        """Test handling of missing values represented as NaN."""
        # Missing values should be ignored
        data = np.array([[1, 2, np.nan, 4], [1, 2, 3, 4], [1, np.nan, 3, 4]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal", missing=np.nan)
        assert not np.isnan(result)
        assert 0.0 <= result <= 1.0

    def test_with_missing_values_custom_sentinel(self):
        """Test handling of missing values with custom sentinel value."""
        # Use -999 as missing value indicator
        data = np.array([[1, 2, -999, 4], [1, 2, 3, 4], [1, -999, 3, 4]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal", missing=-999)
        assert not np.isnan(result)
        assert 0.0 <= result <= 1.0

    def test_insufficient_data_returns_nan(self):
        """Test that insufficient data returns NaN."""
        # Only one valid rating
        data = np.array([[1, np.nan], [np.nan, np.nan]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert np.isnan(result)

    def test_all_missing_returns_nan(self):
        """Test that all missing values returns NaN."""
        data = np.array([[np.nan, np.nan], [np.nan, np.nan]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert np.isnan(result)

    def test_two_raters_three_items(self):
        """Test with two raters and three items - basic scenario."""
        # Example from Krippendorff's work
        data = np.array([[1, 2, 3], [1, 2, 4]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert 0.0 <= result <= 1.0
        # Should show high but not perfect agreement
        assert result > 0.5

    def test_varying_raters_per_item(self):
        """Test with varying numbers of raters per item."""
        # Different items have different numbers of raters
        data = np.array([[1, 2, np.nan, 4], [1, 2, 3, np.nan], [np.nan, 2, 3, 4]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert not np.isnan(result)

    def test_invalid_level_of_measurement_raises_error(self):
        """Test that invalid level of measurement raises ValueError."""
        data = np.array([[1, 2, 3], [1, 2, 3]])
        with pytest.raises(ValueError, match="Only 'ordinal' level of measurement is supported"):
            krippendorff_alpha(data, level_of_measurement="nominal")

    def test_ordinal_scale_respects_order(self):
        """Test that ordinal distances respect order of categories."""
        # Disagreement between adjacent categories should be less severe
        # than disagreement between distant categories
        data_adjacent = np.array([[1, 2, 3, 4], [2, 3, 4, 5]])
        data_distant = np.array([[1, 2, 3, 4], [5, 5, 5, 5]])

        alpha_adjacent = krippendorff_alpha(data_adjacent, level_of_measurement="ordinal")
        alpha_distant = krippendorff_alpha(data_distant, level_of_measurement="ordinal")

        # Adjacent disagreement should have higher alpha than distant disagreement
        assert alpha_adjacent > alpha_distant

    def test_symmetric_disagreement(self):
        """Test that swapping rater roles gives same result."""
        data1 = np.array([[1, 2, 3], [3, 2, 1]])
        data2 = np.array([[3, 2, 1], [1, 2, 3]])

        alpha1 = krippendorff_alpha(data1, level_of_measurement="ordinal")
        alpha2 = krippendorff_alpha(data2, level_of_measurement="ordinal")

        assert np.isclose(alpha1, alpha2)

    def test_with_float_ratings(self):
        """Test with float ratings on ordinal scale."""
        data = np.array([[1.0, 2.0, 3.0], [1.0, 2.5, 3.0], [1.0, 2.0, 3.5]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert 0.0 <= result <= 1.0

    def test_large_scale_ratings(self):
        """Test with larger ordinal scale (e.g., 1-10)."""
        data = np.array([[1, 5, 10, 3, 7], [2, 5, 9, 3, 7], [1, 4, 10, 4, 6]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert not np.isnan(result)
        assert -1.0 <= result <= 1.0

    def test_three_raters_moderate_agreement(self):
        """Test with three raters and moderate agreement."""
        # Simulating harm scores (0.0 to 1.0 scale with 5 levels)
        data = np.array([[0.0, 0.25, 0.5, 0.75, 1.0], [0.0, 0.25, 0.75, 0.75, 1.0], [0.25, 0.25, 0.5, 1.0, 1.0]])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")
        assert 0.0 <= result <= 1.0
        # This scenario actually shows high agreement (>0.9)
        assert result > 0.8


class TestValidateAndPrepareData:
    """Test the data validation and preparation helper function."""

    def test_validates_ordinal_measurement(self):
        """Test that only ordinal measurement is accepted."""
        data = np.array([[1, 2], [1, 2]])
        with pytest.raises(ValueError, match="Only 'ordinal' level of measurement is supported"):
            _validate_and_prepare_data(data, "nominal", np.nan)

    def test_converts_to_float64(self):
        """Test that data is converted to float64."""
        data = np.array([[1, 2], [1, 2]], dtype=np.int32)
        result_data, _, _ = _validate_and_prepare_data(data, "ordinal", np.nan)
        assert result_data.dtype == np.float64

    def test_handles_none_missing_value(self):
        """Test that None missing value is converted to NaN."""
        data = np.array([[1, 2], [1, 2]])
        result_data, valid_mask, categories = _validate_and_prepare_data(data, "ordinal", None)
        # Should work without error and treat NaN as missing
        assert len(categories) == 2

    def test_creates_valid_mask_for_nan(self):
        """Test valid mask creation for NaN missing values."""
        data = np.array([[1, np.nan], [2, 3]])
        _, valid_mask, _ = _validate_and_prepare_data(data, "ordinal", np.nan)
        expected_mask = np.array([[True, False], [True, True]])
        assert np.array_equal(valid_mask, expected_mask)

    def test_creates_valid_mask_for_custom_sentinel(self):
        """Test valid mask creation for custom sentinel value."""
        data = np.array([[1, -999], [2, 3]])
        _, valid_mask, _ = _validate_and_prepare_data(data, "ordinal", -999)
        expected_mask = np.array([[True, False], [True, True]])
        assert np.array_equal(valid_mask, expected_mask)

    def test_extracts_sorted_categories(self):
        """Test that categories are extracted and sorted."""
        data = np.array([[3, 1, 2], [2, 1, 3]])
        _, _, categories = _validate_and_prepare_data(data, "ordinal", np.nan)
        expected_categories = np.array([1.0, 2.0, 3.0])
        assert np.array_equal(categories, expected_categories)


class TestBuildValueCountsMatrix:
    """Test the value counts matrix building function."""

    def test_counts_ratings_per_category(self):
        """Test basic counting of ratings per category."""
        data = np.array([[1, 2, 3], [1, 2, 3]])
        valid_mask = np.array([[True, True, True], [True, True, True]])
        categories = np.array([1.0, 2.0, 3.0])

        result = _build_value_counts_matrix(data, valid_mask, categories)

        # Each item should have 2 raters agreeing
        expected = np.array([[2, 0, 0], [0, 2, 0], [0, 0, 2]])
        assert np.array_equal(result, expected)

    def test_handles_missing_values(self):
        """Test that missing values are not counted."""
        data = np.array([[1, 2, 3], [1, np.nan, 3]])
        valid_mask = np.array([[True, True, True], [True, False, True]])
        categories = np.array([1.0, 2.0, 3.0])

        result = _build_value_counts_matrix(data, valid_mask, categories)

        # Item 0: 2 raters for category 1
        # Item 1: 1 rater for category 2
        # Item 2: 2 raters for category 3
        expected = np.array([[2, 0, 0], [0, 1, 0], [0, 0, 2]])
        assert np.array_equal(result, expected)

    def test_handles_disagreement(self):
        """Test counting when raters disagree."""
        data = np.array([[1, 2], [2, 3]])
        valid_mask = np.array([[True, True], [True, True]])
        categories = np.array([1.0, 2.0, 3.0])

        result = _build_value_counts_matrix(data, valid_mask, categories)

        # Item 0: 1 rater for cat 1, 1 for cat 2
        # Item 1: 1 rater for cat 2, 1 for cat 3
        expected = np.array([[1, 1, 0], [0, 1, 1]])
        assert np.array_equal(result, expected)


class TestBuildCoincidenceMatrix:
    """Test the coincidence matrix building function."""

    def test_perfect_agreement_coincidence(self):
        """Test coincidence matrix for perfect agreement."""
        # All items have same rating pattern: 2 raters agree on category 0
        value_counts = np.array([[2, 0, 0], [2, 0, 0]])

        result = _build_coincidence_matrix(value_counts)

        # All coincidences should be in (0,0)
        assert result[0, 0] > 0
        assert result[1, 1] == 0
        assert result[2, 2] == 0

    def test_handles_single_rater_items(self):
        """Test that items with < 2 raters are skipped."""
        # Item 0: 2 raters, Item 1: 1 rater, Item 2: 0 raters
        value_counts = np.array([[2, 0], [1, 0], [0, 0]])

        result = _build_coincidence_matrix(value_counts)

        # Only item 0 should contribute
        assert result.sum() > 0

    def test_disagreement_creates_off_diagonal(self):
        """Test that disagreement creates off-diagonal coincidences."""
        # Item with 1 rating in category 0 and 1 in category 1
        value_counts = np.array([[1, 1, 0]])

        result = _build_coincidence_matrix(value_counts)

        # Should have coincidences between categories 0 and 1
        assert result[0, 1] > 0 or result[1, 0] > 0


class TestBuildExpectedMatrix:
    """Test the expected matrix building function."""

    def test_calculates_marginals(self):
        """Test marginal calculation from coincidence matrix."""
        coincidence_matrix = np.array([[4.0, 1.0], [1.0, 2.0]])

        _, n_v, total_n = _build_expected_matrix(coincidence_matrix)

        # Marginals should be row sums
        expected_n_v = np.array([5.0, 3.0])
        assert np.array_equal(n_v, expected_n_v)
        assert total_n == 8.0

    def test_expected_matrix_shape(self):
        """Test that expected matrix has correct shape."""
        coincidence_matrix = np.array([[4.0, 1.0, 0.5], [1.0, 2.0, 0.3], [0.5, 0.3, 1.0]])

        expected_matrix, _, _ = _build_expected_matrix(coincidence_matrix)

        assert expected_matrix.shape == (3, 3)

    def test_handles_zero_total(self):
        """Test handling when total coincidences is zero."""
        coincidence_matrix = np.zeros((2, 2))

        expected_matrix, n_v, total_n = _build_expected_matrix(coincidence_matrix)

        assert total_n == 0.0
        assert np.array_equal(n_v, np.array([0.0, 0.0]))


class TestBuildOrdinalDistanceMatrix:
    """Test the ordinal distance matrix building function."""

    def test_diagonal_is_zero(self):
        """Test that diagonal distances are zero."""
        n_v = np.array([1.0, 2.0, 3.0])

        result = _build_ordinal_distance_matrix(3, n_v)

        assert result[0, 0] == 0.0
        assert result[1, 1] == 0.0
        assert result[2, 2] == 0.0

    def test_symmetric_distances(self):
        """Test that distance matrix is symmetric."""
        n_v = np.array([1.0, 2.0, 3.0, 4.0])

        result = _build_ordinal_distance_matrix(4, n_v)

        # Check symmetry
        for i in range(4):
            for j in range(4):
                assert result[i, j] == result[j, i]

    def test_adjacent_categories_smaller_distance(self):
        """Test that adjacent categories have smaller distances."""
        n_v = np.array([1.0, 1.0, 1.0, 1.0])

        result = _build_ordinal_distance_matrix(4, n_v)

        # Distance between adjacent categories should be less than distant ones
        # For uniform marginals, d(0,1) < d(0,2) < d(0,3)
        assert result[0, 1] < result[0, 2]
        assert result[0, 2] < result[0, 3]


class TestComputeAlphaFromDisagreements:
    """Test the alpha computation from disagreements function."""

    def test_zero_disagreement_returns_one(self):
        """Test that zero observed and expected disagreement returns 1.0."""
        result = _compute_alpha_from_disagreements(0.0, 0.0)
        assert result == 1.0

    def test_zero_expected_with_nonzero_observed_returns_nan(self):
        """Test that zero expected with non-zero observed returns NaN."""
        result = _compute_alpha_from_disagreements(1.0, 0.0)
        assert np.isnan(result)

    def test_equal_disagreements_returns_zero(self):
        """Test that equal observed and expected disagreement returns 0.0."""
        result = _compute_alpha_from_disagreements(5.0, 5.0)
        assert result == 0.0

    def test_half_expected_disagreement_returns_half(self):
        """Test that observed = 0.5 * expected returns alpha = 0.5."""
        result = _compute_alpha_from_disagreements(5.0, 10.0)
        assert np.isclose(result, 0.5)

    def test_higher_observed_than_expected_negative_alpha(self):
        """Test that observed > expected gives negative alpha."""
        result = _compute_alpha_from_disagreements(10.0, 5.0)
        assert result < 0.0
        assert np.isclose(result, -1.0)

    def test_tiny_negative_clipped_to_zero(self):
        """Test that tiny negative values due to rounding are clipped to 0."""
        # Simulate rounding error
        result = _compute_alpha_from_disagreements(10.0 + 1e-12, 10.0)
        assert result == 0.0


class TestKrippendorffAlphaIntegration:
    """Integration tests with known examples from literature."""

    def test_krippendorff_example_ordinal(self):
        """Test with a classic example adapted for ordinal data."""
        # Based on examples from Krippendorff's work
        # 4 items, 3 raters, ordinal scale 1-4
        data = np.array([[1, 2, 3, 3], [1, 2, 3, 4], [np.nan, 3, 3, 4]])

        result = krippendorff_alpha(data, level_of_measurement="ordinal")

        # Should show good but not perfect agreement
        assert 0.4 <= result <= 0.9
        assert not np.isnan(result)

    def test_harm_scoring_scenario(self):
        """Test with realistic harm scoring scenario (0.0 to 1.0 scale)."""
        # 3 human raters, 5 prompts, scale: 0.0, 0.25, 0.5, 0.75, 1.0
        data = np.array(
            [
                [0.0, 0.25, 0.5, 0.75, 1.0],  # Rater 1
                [0.0, 0.25, 0.75, 0.75, 1.0],  # Rater 2
                [0.0, 0.5, 0.5, 1.0, 1.0],  # Rater 3
            ]
        )

        result = krippendorff_alpha(data, level_of_measurement="ordinal")

        # Should show moderate to good agreement
        assert 0.3 <= result <= 0.95
        assert not np.isnan(result)

    def test_single_item_multiple_raters(self):
        """Test edge case with single item and multiple raters."""
        # 1 item, 3 raters
        data = np.array([[1], [2], [1]])

        result = krippendorff_alpha(data, level_of_measurement="ordinal")

        # Should compute without error
        assert not np.isnan(result)
        assert -1.0 <= result <= 1.0

    def test_many_items_two_raters(self):
        """Test with many items but only two raters."""
        np.random.seed(42)
        # Simulate two raters with high agreement
        rater1 = np.random.choice([1, 2, 3, 4, 5], size=20)
        # Rater 2 agrees 80% of the time
        rater2 = rater1.copy()
        disagreement_indices = np.random.choice(20, size=4, replace=False)
        rater2[disagreement_indices] = np.random.choice([1, 2, 3, 4, 5], size=4)

        data = np.array([rater1, rater2])
        result = krippendorff_alpha(data, level_of_measurement="ordinal")

        # Should show high agreement
        assert result > 0.6
        assert result <= 1.0
