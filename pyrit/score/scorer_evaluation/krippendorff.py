# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""
Krippendorff's alpha for ordinal data.

This implementation follows the standard Krippendorff's alpha formulation and
is inspired by LightTag/simpledorff's clean decomposition of expected/observed
disagreement with a pluggable distance metric:
https://github.com/LightTag/simpledorff

The ordinal distance metric follows Krippendorff's specification.
"""

from __future__ import annotations

import numpy as np


def _validate_and_prepare_data(
    reliability_data: "np.ndarray",  # type: ignore[type-arg, unused-ignore]
    level_of_measurement: str,
    missing: float | None,
) -> tuple["np.ndarray", "np.ndarray", "np.ndarray"]:  # type: ignore[type-arg, unused-ignore]
    """
    Validate inputs and prepare data for reliability calculation.

    Args:
        reliability_data: Ratings array of shape (num_raters_or_trials, num_items).
        level_of_measurement: Level of measurement (must be "ordinal").
        missing: Sentinel value indicating missing ratings.

    Returns:
        tuple: (data, valid_mask, categories) where:
            - data: Float64 array of ratings
            - valid_mask: Boolean mask of non-missing values
            - categories: Sorted unique categories

    Raises:
        ValueError: If level_of_measurement is not "ordinal".
    """
    # Validate level of measurement
    if level_of_measurement != "ordinal":
        raise ValueError(f"Only 'ordinal' level of measurement is supported, got '{level_of_measurement}'")

    # Convert to float64 for numerical precision
    data = np.asarray(reliability_data, dtype=np.float64)

    # Handle missing value indicator
    if missing is None:
        missing = np.nan

    # Identify valid (non-missing) values
    if np.isnan(missing):
        valid_mask = ~np.isnan(data)
    else:
        valid_mask = data != missing

    # Get sorted unique categories
    valid_ratings = data[valid_mask]
    categories = np.unique(valid_ratings)

    return data, valid_mask, categories


def _build_value_counts_matrix(
    data: "np.ndarray",  # type: ignore[type-arg, unused-ignore]
    valid_mask: "np.ndarray",  # type: ignore[type-arg, unused-ignore]
    categories: "np.ndarray",  # type: ignore[type-arg, unused-ignore]
) -> "np.ndarray":  # type: ignore[type-arg, unused-ignore]
    """
    Build matrix counting how many raters assigned each category to each item.

    Args:
        data: Float64 array of ratings.
        valid_mask: Boolean mask of non-missing values.
        categories: Sorted unique categories.

    Returns:
        np.ndarray: Matrix of shape (num_items, num_categories) with counts.
    """
    num_items = data.shape[1]
    num_categories = len(categories)
    value_counts = np.zeros((num_items, num_categories), dtype=np.int64)

    for item_idx in range(num_items):
        item_ratings = data[:, item_idx]
        item_valid = valid_mask[:, item_idx]
        valid_item_ratings = item_ratings[item_valid]

        for rating in valid_item_ratings:
            cat_idx = np.searchsorted(categories, rating)
            value_counts[item_idx, cat_idx] += 1

    return value_counts


def _build_coincidence_matrix(
    value_counts: "np.ndarray",  # type: ignore[type-arg, unused-ignore]
) -> "np.ndarray":  # type: ignore[type-arg, unused-ignore]
    """
    Build coincidence matrix from value counts.

    Args:
        value_counts: Matrix of shape (num_items, num_categories) with counts.

    Returns:
        np.ndarray: Coincidence matrix of shape (num_categories, num_categories).
    """
    num_items, num_categories = value_counts.shape
    coincidence_matrix = np.zeros((num_categories, num_categories), dtype=np.float64)

    # Calculate pairable values per item (must have at least 2 raters)
    pairable = np.maximum(value_counts.sum(axis=1), 2)

    for item_idx in range(num_items):
        item_counts = value_counts[item_idx]
        m_c = pairable[item_idx]

        if m_c < 2:
            continue

        # Compute outer product for this item
        item_coincidences = np.outer(item_counts, item_counts).astype(np.float64)

        # Set diagonal to n_i * (n_i - 1) to remove self-pairs
        np.fill_diagonal(item_coincidences, item_counts * (item_counts - 1))

        # Normalize and add to total
        coincidence_matrix += item_coincidences / (m_c - 1)

    return coincidence_matrix


def _build_expected_matrix(
    coincidence_matrix: "np.ndarray",  # type: ignore[type-arg, unused-ignore]
) -> tuple["np.ndarray", "np.ndarray", float]:  # type: ignore[type-arg, unused-ignore]
    """
    Build expected coincidence matrix from observed coincidences.

    Args:
        coincidence_matrix: Observed coincidence matrix.

    Returns:
        tuple: (expected_matrix, n_v, total_n) where:
            - expected_matrix: Expected coincidence matrix
            - n_v: Marginal counts per category
            - total_n: Total number of coincidences
    """
    # Calculate marginals (total coincidences per category)
    n_v = coincidence_matrix.sum(axis=0)
    total_n = n_v.sum()

    # Expected coincidence matrix
    expected_matrix = np.outer(n_v, n_v) - np.diag(n_v)
    if total_n > 1:
        expected_matrix = expected_matrix / (total_n - 1)

    return expected_matrix, n_v, total_n


def _build_ordinal_distance_matrix(
    num_categories: int,
    n_v: "np.ndarray",  # type: ignore[type-arg, unused-ignore]
) -> "np.ndarray":  # type: ignore[type-arg, unused-ignore]
    """
    Build ordinal distance matrix using category marginals.

    Args:
        num_categories: Number of unique categories.
        n_v: Marginal counts per category.

    Returns:
        np.ndarray: Distance matrix of shape (num_categories, num_categories).
    """
    distance_matrix = np.zeros((num_categories, num_categories), dtype=np.float64)

    for i in range(num_categories):
        for j in range(num_categories):
            if i == j:
                continue
            min_idx, max_idx = (i, j) if i < j else (j, i)
            between_sum = n_v[min_idx : max_idx + 1].sum() - (n_v[min_idx] + n_v[max_idx]) / 2.0
            distance_matrix[i, j] = between_sum**2

    return distance_matrix


def _compute_alpha_from_disagreements(
    observed_disagreement: float,
    expected_disagreement: float,
) -> float:
    """
    Compute Krippendorff's alpha from observed and expected disagreements.

    Args:
        observed_disagreement: Observed disagreement value.
        expected_disagreement: Expected disagreement value.

    Returns:
        float: Krippendorff's alpha value.
    """
    # Handle edge case where expected disagreement is zero
    if np.abs(expected_disagreement) < 1e-15:
        if np.abs(observed_disagreement) < 1e-15:
            return 1.0
        return np.nan

    # Krippendorff's alpha
    alpha_value = 1.0 - observed_disagreement / expected_disagreement

    # Clip tiny negative values due to rounding
    if -1e-10 < alpha_value < 0:
        alpha_value = 0.0

    return float(alpha_value)


def krippendorff_alpha(
    reliability_data: "np.ndarray",  # type: ignore[type-arg, unused-ignore]  # shape: (num_raters_or_trials, num_items); dtype float
    level_of_measurement: str = "ordinal",
    missing: float | None = np.nan,
) -> float:
    """
    Compute Krippendorff's alpha inter-rater reliability for ordinal data.

    Computes inter-rater reliability for ordered categories, ignoring missing
    entries and supporting varying numbers of raters per item.

    Args:
        reliability_data (np.ndarray):
            Ratings array of shape (num_raters_or_trials, num_items). Each entry
            is a numeric rating on an ordered scale. Missing ratings should be
            represented by ``np.nan`` (default) or the value specified by
            ``missing``.
        level_of_measurement (str):
            Level of measurement. Must be ``"ordinal"``. Any other value will
            raise ``ValueError``.
        missing (float | None):
            Sentinel value indicating missing ratings. If ``None``, it is
            treated as ``np.nan``. Defaults to ``np.nan``.

    Returns:
        float: Krippendorff's alpha, where 1.0 indicates perfect agreement. The
        value can be below 0 when agreement is worse than chance. ``np.nan`` is
        returned when the statistic is undefined (e.g., fewer than two usable
        ratings or zero expected disagreement with non-zero observed
        disagreement).

    Raises:
        ValueError: If ``level_of_measurement`` is not ``"ordinal"``.
    """
    # Validate and prepare data
    data, valid_mask, categories = _validate_and_prepare_data(reliability_data, level_of_measurement, missing)

    # Check if we have enough data
    valid_ratings = data[valid_mask]
    if len(valid_ratings) < 2:
        return np.nan

    # All ratings identical - perfect agreement
    num_categories = len(categories)
    if num_categories == 1:
        return 1.0

    # Build value counts matrix
    value_counts = _build_value_counts_matrix(data, valid_mask, categories)

    # Build coincidence matrix
    coincidence_matrix = _build_coincidence_matrix(value_counts)

    # Build expected coincidence matrix
    expected_matrix, n_v, total_n = _build_expected_matrix(coincidence_matrix)

    # Check for degenerate case
    if total_n == 0:
        return np.nan

    # Build ordinal distance matrix
    distance_matrix = _build_ordinal_distance_matrix(num_categories, n_v)

    # Compute observed and expected disagreements
    observed_disagreement = float(np.sum(coincidence_matrix * distance_matrix))
    expected_disagreement = float(np.sum(expected_matrix * distance_matrix))

    # Compute and return alpha
    return _compute_alpha_from_disagreements(observed_disagreement, expected_disagreement)
