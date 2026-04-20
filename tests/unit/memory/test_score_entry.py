# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest

from pyrit.identifiers import ScorerIdentifier
from pyrit.memory.memory_models import ScoreEntry
from pyrit.models import Score


@pytest.mark.usefixtures("patch_central_database")
class TestScoreEntryRoundtrip:
    """Tests for ScoreEntry roundtrip with nested scorer identifiers."""

    def test_score_entry_roundtrip_simple_identifier(self):
        """Test that a Score with a simple ScorerIdentifier can be stored and retrieved."""
        scorer_identifier = ScorerIdentifier(
            class_name="SelfAskScorer",
            class_module="pyrit.score",
            class_description="A self-ask scorer",
            identifier_type="instance",
        )

        message_piece_id = uuid.uuid4()
        original_score = Score(
            score_value="0.85",
            score_value_description="High confidence",
            score_type="float_scale",
            score_category=["test"],
            score_rationale="Test rationale",
            score_metadata={"key": "value"},
            scorer_class_identifier=scorer_identifier,
            message_piece_id=message_piece_id,
            objective="test objective",
        )

        # Create ScoreEntry
        score_entry = ScoreEntry(entry=original_score)

        # Get score back
        retrieved_score = score_entry.get_score()

        # Verify the identifier is preserved as ScorerIdentifier
        assert isinstance(retrieved_score.scorer_class_identifier, ScorerIdentifier)
        assert retrieved_score.scorer_class_identifier.class_name == "SelfAskScorer"
        assert retrieved_score.scorer_class_identifier.class_module == "pyrit.score"

    def test_score_entry_roundtrip_with_nested_sub_identifier(self):
        """Test roundtrip with nested ScorerIdentifier (composite scorer)."""
        inner_scorer = ScorerIdentifier(
            class_name="SelfAskScorer",
            class_module="pyrit.score",
            class_description="Inner scorer",
            identifier_type="instance",
        )

        outer_scorer = ScorerIdentifier(
            class_name="FloatScaleThresholdScorer",
            class_module="pyrit.score",
            class_description="A threshold scorer",
            identifier_type="instance",
            sub_identifier=[inner_scorer],
        )

        message_piece_id = uuid.uuid4()
        original_score = Score(
            score_value="0.85",
            score_value_description="High confidence",
            score_type="float_scale",
            score_category=["test"],
            score_rationale="Test rationale",
            score_metadata={"key": "value"},
            scorer_class_identifier=outer_scorer,
            message_piece_id=message_piece_id,
            objective="test objective",
        )

        # Create ScoreEntry
        score_entry = ScoreEntry(entry=original_score)

        # Verify nested structure is serialized to dict in the entry
        assert isinstance(score_entry.scorer_class_identifier, dict)
        assert isinstance(score_entry.scorer_class_identifier["sub_identifier"], list)
        assert score_entry.scorer_class_identifier["sub_identifier"][0]["class_name"] == "SelfAskScorer"

        # Get score back
        retrieved_score = score_entry.get_score()

        # Verify the ScorerIdentifier is reconstructed with nested structure
        assert isinstance(retrieved_score.scorer_class_identifier, ScorerIdentifier)
        assert retrieved_score.scorer_class_identifier.class_name == "FloatScaleThresholdScorer"
        assert retrieved_score.scorer_class_identifier.sub_identifier is not None
        assert len(retrieved_score.scorer_class_identifier.sub_identifier) == 1
        assert retrieved_score.scorer_class_identifier.sub_identifier[0].class_name == "SelfAskScorer"

    def test_score_entry_roundtrip_with_multiple_sub_identifiers(self):
        """Test roundtrip with multiple sub_identifiers (composite scorer with multiple scorers)."""
        scorer_a = ScorerIdentifier(
            class_name="ScorerA",
            class_module="pyrit.score",
            class_description="First scorer",
            identifier_type="instance",
        )

        scorer_b = ScorerIdentifier(
            class_name="ScorerB",
            class_module="pyrit.score",
            class_description="Second scorer",
            identifier_type="instance",
        )

        composite_scorer = ScorerIdentifier(
            class_name="TrueFalseCompositeScorer",
            class_module="pyrit.score",
            class_description="Composite scorer",
            identifier_type="instance",
            sub_identifier=[scorer_a, scorer_b],
        )

        message_piece_id = uuid.uuid4()
        original_score = Score(
            score_value="True",
            score_value_description="Composite result",
            score_type="true_false",
            score_category=["test"],
            score_rationale="Test rationale",
            score_metadata={"key": "value"},
            scorer_class_identifier=composite_scorer,
            message_piece_id=message_piece_id,
            objective="test objective",
        )

        # Create ScoreEntry
        score_entry = ScoreEntry(entry=original_score)

        # Get score back
        retrieved_score = score_entry.get_score()

        # Verify the list structure is preserved
        assert isinstance(retrieved_score.scorer_class_identifier, ScorerIdentifier)
        assert retrieved_score.scorer_class_identifier.sub_identifier is not None
        assert len(retrieved_score.scorer_class_identifier.sub_identifier) == 2
        assert retrieved_score.scorer_class_identifier.sub_identifier[0].class_name == "ScorerA"
        assert retrieved_score.scorer_class_identifier.sub_identifier[1].class_name == "ScorerB"

    def test_score_entry_to_dict(self):
        """Test that to_dict includes all fields correctly."""
        scorer_identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score",
            class_description="A test scorer",
            identifier_type="instance",
        )

        message_piece_id = uuid.uuid4()
        score = Score(
            score_value="0.5",
            score_value_description="Medium",
            score_type="float_scale",
            score_category=["cat1"],
            score_rationale="Rationale",
            score_metadata={"meta": "data"},
            scorer_class_identifier=scorer_identifier,
            message_piece_id=message_piece_id,
            objective="objective",
        )

        score_entry = ScoreEntry(entry=score)
        result = score_entry.to_dict()

        assert result["score_value"] == "0.5"
        assert result["score_type"] == "float_scale"
        # to_dict returns the dict representation
        assert result["scorer_class_identifier"]["class_name"] == "TestScorer"
        assert result["objective"] == "objective"

    def test_score_to_dict_serializes_scorer_identifier(self):
        """Test that Score.to_dict() properly serializes the ScorerIdentifier."""
        scorer_identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score",
            class_description="A test scorer",
            identifier_type="instance",
        )

        message_piece_id = uuid.uuid4()
        score = Score(
            score_value="0.5",
            score_value_description="Medium",
            score_type="float_scale",
            score_category=["cat1"],
            score_rationale="Rationale",
            score_metadata={"meta": "data"},
            scorer_class_identifier=scorer_identifier,
            message_piece_id=message_piece_id,
            objective="objective",
        )

        result = score.to_dict()

        # to_dict should serialize ScorerIdentifier to dict
        assert isinstance(result["scorer_class_identifier"], dict)
        assert result["scorer_class_identifier"]["class_name"] == "TestScorer"
