# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for pyrit.score.score_utils module.

This module tests the score utility functions, particularly normalize_score_to_float,
which is critical for multi-turn attacks like TAP and Crescendo. These attacks need
to provide numerical feedback to adversarial chats regardless of score type.
"""

import uuid
from unittest.mock import MagicMock

from pyrit.identifiers import ScorerIdentifier
from pyrit.models import Score
from pyrit.score.score_utils import (
    ORIGINAL_FLOAT_VALUE_KEY,
    combine_metadata_and_categories,
    format_score_for_rationale,
    normalize_score_to_float,
)


# Reusable test scorer identifiers
def _make_scorer_id(name: str) -> ScorerIdentifier:
    return ScorerIdentifier(
        class_name=name,
        class_module="tests.unit.score",
        class_description="",
        identifier_type="instance",
    )


class TestNormalizeScoreToFloat:
    """
    Tests for the normalize_score_to_float function.

    This function is critical for multi-turn attacks (TAP, Crescendo) because:
    1. It provides consistent numerical feedback to adversarial chats
    2. It preserves granularity from FloatScaleThresholdScorer via metadata
    3. It handles both true_false and float_scale score types uniformly

    Without this function, attacks would only get boolean feedback (0 or 1),
    making it impossible for the adversarial chat to know "how close" it is
    to achieving the objective.
    """

    def test_returns_zero_for_none_score(self) -> None:
        """None scores should return 0.0 as a safe default."""
        result = normalize_score_to_float(None)

        assert result == 0.0

    def test_extracts_float_from_metadata_when_present(self) -> None:
        """
        When score_metadata contains ORIGINAL_FLOAT_VALUE_KEY, use that value.

        This is the key behavior for FloatScaleThresholdScorer integration.
        The scorer converts float_scale to true_false but preserves the original
        float in metadata. This test verifies we extract and use that value.
        """
        original_float = 0.73

        score = Score(
            score_type="true_false",
            score_value="True",  # The threshold result
            score_value_description="Above threshold",
            score_rationale="Test",
            score_metadata={ORIGINAL_FLOAT_VALUE_KEY: original_float},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_make_scorer_id("FloatScaleThresholdScorer"),
        )

        result = normalize_score_to_float(score)

        assert result == original_float

    def test_uses_score_value_when_no_metadata(self) -> None:
        """When no metadata, fall back to the score's get_value() result."""
        score = Score(
            score_type="float_scale",
            score_value="0.65",
            score_value_description="Test",
            score_rationale="Test",
            score_metadata={},  # No ORIGINAL_FLOAT_VALUE_KEY
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_make_scorer_id("SelfAskScaleScorer"),
        )

        result = normalize_score_to_float(score)

        assert result == 0.65

    def test_converts_true_boolean_to_one(self) -> None:
        """Boolean True scores should convert to 1.0."""
        score = Score(
            score_type="true_false",
            score_value="True",
            score_value_description="Test",
            score_rationale="Test",
            score_metadata={},  # No float metadata
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_make_scorer_id("TrueFalseScorer"),
        )

        result = normalize_score_to_float(score)

        assert result == 1.0

    def test_converts_false_boolean_to_zero(self) -> None:
        """Boolean False scores should convert to 0.0."""
        score = Score(
            score_type="true_false",
            score_value="False",
            score_value_description="Test",
            score_rationale="Test",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_make_scorer_id("TrueFalseScorer"),
        )

        result = normalize_score_to_float(score)

        assert result == 0.0

    def test_handles_integer_score_values(self) -> None:
        """Integer score values should be converted to float."""
        score = MagicMock()
        score.score_metadata = {}
        score.get_value.return_value = 1

        result = normalize_score_to_float(score)

        assert result == 1.0
        assert isinstance(result, float)

    def test_returns_zero_for_non_numeric_string_values(self) -> None:
        """Non-numeric string values should return 0.0."""
        score = MagicMock()
        score.score_metadata = {}
        score.get_value.return_value = "not_a_number"

        result = normalize_score_to_float(score)

        assert result == 0.0

    def test_metadata_takes_precedence_over_score_value(self) -> None:
        """
        Metadata float should take precedence over score value.

        This is important for FloatScaleThresholdScorer: the score_value
        might be "True" (threshold met) but the actual float is 0.71.
        We want 0.71 for feedback, not 1.0.
        """
        score = Score(
            score_type="true_false",
            score_value="True",  # Would give 1.0 without metadata
            score_value_description="Above threshold",
            score_rationale="Test",
            score_metadata={ORIGINAL_FLOAT_VALUE_KEY: 0.71},  # Actual value
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_make_scorer_id("FloatScaleThresholdScorer"),
        )

        result = normalize_score_to_float(score)

        # Should use metadata value, not 1.0 from True
        assert result == 0.71

    def test_handles_none_metadata(self) -> None:
        """Scores with None metadata should not raise errors."""
        score = MagicMock()
        score.score_metadata = None
        score.get_value.return_value = 0.5

        result = normalize_score_to_float(score)

        assert result == 0.5


class TestCombineMetadataAndCategories:
    """Tests for the combine_metadata_and_categories function."""

    def test_combines_metadata_from_multiple_scores(self) -> None:
        """Metadata from multiple scores should be merged."""
        score1 = MagicMock()
        score1.score_metadata = {"key1": "value1"}
        score1.score_category = ["cat1"]

        score2 = MagicMock()
        score2.score_metadata = {"key2": "value2"}
        score2.score_category = ["cat2"]

        metadata, categories = combine_metadata_and_categories([score1, score2])

        assert metadata == {"key1": "value1", "key2": "value2"}
        assert categories == ["cat1", "cat2"]

    def test_deduplicates_categories(self) -> None:
        """Duplicate categories should be removed."""
        score1 = MagicMock()
        score1.score_metadata = {}
        score1.score_category = ["cat1", "cat2"]

        score2 = MagicMock()
        score2.score_metadata = {}
        score2.score_category = ["cat2", "cat3"]

        _, categories = combine_metadata_and_categories([score1, score2])

        assert sorted(categories) == ["cat1", "cat2", "cat3"]

    def test_filters_empty_categories(self) -> None:
        """Empty string categories should be filtered out."""
        score = MagicMock()
        score.score_metadata = {}
        score.score_category = ["cat1", "", "cat2"]

        _, categories = combine_metadata_and_categories([score])

        assert "" not in categories
        assert sorted(categories) == ["cat1", "cat2"]

    def test_handles_none_metadata_and_categories(self) -> None:
        """Scores with None metadata/categories should not raise errors."""
        score = MagicMock()
        score.score_metadata = None
        score.score_category = None

        metadata, categories = combine_metadata_and_categories([score])

        assert metadata == {}
        assert categories == []


class TestFormatScoreForRationale:
    """Tests for the format_score_for_rationale function."""

    def test_formats_score_with_all_fields(self) -> None:
        """Score should be formatted with type, value, and rationale."""
        score = Score(
            score_type="true_false",
            score_value="True",
            score_value_description="Test",
            score_rationale="This is the rationale",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_make_scorer_id("TestScorer"),
        )

        result = format_score_for_rationale(score)

        assert "TestScorer" in result
        assert "True" in result
        assert "This is the rationale" in result

    def test_handles_missing_rationale(self) -> None:
        """Scores without rationale should not raise errors."""
        score = Score(
            score_type="true_false",
            score_value="False",
            score_value_description="Test",
            score_rationale=None,
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_make_scorer_id("TestScorer"),
        )

        result = format_score_for_rationale(score)

        assert "TestScorer" in result
        assert "False" in result
