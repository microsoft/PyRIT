# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.models import Score, UndeterminedScoreError

# Key used by FloatScaleThresholdScorer to store the original float value
# in score_metadata when converting float_scale to true_false
ORIGINAL_FLOAT_VALUE_KEY = "original_float_value"


def score_is_true(score: Score | None) -> bool:
    """
    Return whether a score carries a true verdict.

    Callers are branching on true/false scorers, where this is simply the scorer's verdict.
    A float score reads as true when non-zero, which is how these call sites read one before
    undetermined scores existed.

    An undetermined score is neither achievement nor refutation, so it reads as not true here.
    This answers "did it succeed", not "did it fail": an attack deciding the outcome it reports
    must use ``pyrit.executor.attack.attack_outcome_from_score`` so an undetermined score
    surfaces as ``AttackOutcome.UNDETERMINED`` rather than a failure.

    Args:
        score: The score to read, or None.

    Returns:
        True when the score carries a true verdict, False when it does not, is undetermined,
        or is absent.
    """
    if score is None:
        return False
    try:
        return bool(score.get_value())
    except UndeterminedScoreError:
        return False


def combine_metadata_and_categories(scores: list[Score]) -> tuple[dict[str, str | int | float], list[str]]:
    """
    Combine metadata and categories from multiple scores with deduplication.

    Args:
        scores: List of Score objects.

    Returns:
        Tuple of (unambiguous metadata dict, sorted category list with empty strings filtered).
    """
    metadata: dict[str, str | int | float] = {}
    conflicting_metadata_keys: set[str] = set()
    category_set: set[str] = set()

    for s in scores:
        if s.score_metadata:
            for key, value in s.score_metadata.items():
                if key in conflicting_metadata_keys:
                    continue
                if key in metadata and metadata[key] != value:
                    metadata.pop(key)
                    conflicting_metadata_keys.add(key)
                    continue
                metadata[key] = value
        score_categories = s.score_category or []
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
    class_type = score.scorer_class_identifier.class_name or "Unknown" if score.scorer_class_identifier else "Unknown"
    value = score.score_value if score.score_value is not None else "undetermined"
    return f"   - {class_type} {value}: {score.score_rationale or ''}"


def normalize_score_to_float(score: Score | None) -> float:
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
        Float value between 0.0 and 1.0. Returns 0.0 if score is None or undetermined.
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
    try:
        score_value = score.get_value()
    except UndeterminedScoreError:
        return 0.0
    if isinstance(score_value, bool):
        return 1.0 if score_value else 0.0
    if isinstance(score_value, (int, float)):
        return float(score_value)
    return 0.0
