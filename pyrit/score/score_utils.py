# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Dict, List, Optional, Union

from pyrit.common.utils import combine_dict
from pyrit.models import Score

# Key used by FloatScaleThresholdScorer to store the original float value
# in score_metadata when converting float_scale to true_false
ORIGINAL_FLOAT_VALUE_KEY = "original_float_value"


def combine_metadata_and_categories(scores: List[Score]) -> tuple[Dict[str, Union[str, int, float]], List[str]]:
    """
    Combine metadata and categories from multiple scores with deduplication.

    Args:
        scores: List of Score objects.

    Returns:
        Tuple of (metadata dict, sorted category list with empty strings filtered).
    """
    metadata: Dict[str, Union[str, int, float]] = {}
    category_set: set[str] = set()

    for s in scores:
        metadata = combine_dict(metadata, getattr(s, "score_metadata", None))
        score_categories = getattr(s, "score_category", None) or []
        category_set.update([c for c in score_categories if c])

    category = sorted(category_set)
    return metadata, category


def format_score_for_rationale(score: Score) -> str:
    """
    Format a single score for inclusion in an aggregated rationale.

    Args:
        score: The Score object to format.

    Returns:
        Formatted string with scorer class, value, and rationale.
    """
    if score.scorer_class_identifier:
        class_type = score.scorer_class_identifier.class_name or "Unknown"
    else:
        class_type = "Unknown"
    return f"   - {class_type} {score.score_value}: {score.score_rationale or ''}"


def normalize_score_to_float(score: Optional[Score]) -> float:
    """
    Normalize any score to a float value between 0.0 and 1.0.

    This function extracts a float value from a score object, handling different
    score types and metadata. It first checks for the original float value in
    score metadata (stored by FloatScaleThresholdScorer) to preserve granularity.
    Falls back to the score value if metadata is not present.

    This is useful for providing numerical feedback to adversarial chats in
    multi-turn attacks, regardless of whether the underlying scorer produces
    float_scale or true_false type scores.

    Args:
        score: The score to normalize, or None.

    Returns:
        Float value between 0.0 and 1.0. Returns 0.0 if score is None.
    """
    if not score:
        return 0.0

    # Check for original float value in metadata (from FloatScaleThresholdScorer)
    # This preserves granularity when using threshold scorers
    if score.score_metadata:
        original_float = score.score_metadata.get(ORIGINAL_FLOAT_VALUE_KEY)
        if original_float is not None:
            return float(original_float)

    # Fall back to the score value itself
    score_value = score.get_value()
    if isinstance(score_value, bool):
        return 1.0 if score_value else 0.0
    elif isinstance(score_value, (int, float)):
        return float(score_value)
    else:
        return 0.0
