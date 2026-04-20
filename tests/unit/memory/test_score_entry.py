# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.memory.memory_models import ScoreEntry
from pyrit.models import Score


@pytest.mark.usefixtures("patch_central_database")
class TestScoreEntryRoundtrip:
    """Tests for ScoreEntry roundtrip with nested scorer identifiers."""

    def test_score_entry_roundtrip_simple_identifier(self):
        """Test that a Score with a simple ComponentIdentifier can be stored and retrieved."""
        scorer_identifier = ComponentIdentifier(
            class_name="SelfAskScorer",
            class_module="pyrit.score",
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

        # Verify the identifier is preserved as ComponentIdentifier
        assert isinstance(retrieved_score.scorer_class_identifier, ComponentIdentifier)
        assert retrieved_score.scorer_class_identifier.class_name == "SelfAskScorer"
        assert retrieved_score.scorer_class_identifier.class_module == "pyrit.score"

    def test_score_entry_roundtrip_with_nested_children(self):
        """Test roundtrip with nested ComponentIdentifier (composite scorer with children)."""
        inner_scorer = ComponentIdentifier(
            class_name="SelfAskScorer",
            class_module="pyrit.score",
        )

        outer_scorer = ComponentIdentifier(
            class_name="FloatScaleThresholdScorer",
            class_module="pyrit.score",
            children={"sub_scorers": [inner_scorer]},
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
        children_key = ComponentIdentifier.KEY_CHILDREN
        sub_scorers_key = "sub_scorers"
        assert isinstance(score_entry.scorer_class_identifier[children_key][sub_scorers_key], list)
        assert (
            score_entry.scorer_class_identifier[children_key][sub_scorers_key][0][ComponentIdentifier.KEY_CLASS_NAME]
            == "SelfAskScorer"
        )

        # Get score back
        retrieved_score = score_entry.get_score()

        # Verify the ComponentIdentifier is reconstructed with nested children
        assert isinstance(retrieved_score.scorer_class_identifier, ComponentIdentifier)
        assert retrieved_score.scorer_class_identifier.class_name == "FloatScaleThresholdScorer"
        assert "sub_scorers" in retrieved_score.scorer_class_identifier.children
        sub_scorers = retrieved_score.scorer_class_identifier.children["sub_scorers"]
        assert isinstance(sub_scorers, list)
        assert len(sub_scorers) == 1
        assert sub_scorers[0].class_name == "SelfAskScorer"

    def test_score_entry_roundtrip_with_multiple_children(self):
        """Test roundtrip with multiple children (composite scorer with multiple scorers)."""
        scorer_a = ComponentIdentifier(
            class_name="ScorerA",
            class_module="pyrit.score",
        )

        scorer_b = ComponentIdentifier(
            class_name="ScorerB",
            class_module="pyrit.score",
        )

        composite_scorer = ComponentIdentifier(
            class_name="TrueFalseCompositeScorer",
            class_module="pyrit.score",
            children={"sub_scorers": [scorer_a, scorer_b]},
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
        assert isinstance(retrieved_score.scorer_class_identifier, ComponentIdentifier)
        assert "sub_scorers" in retrieved_score.scorer_class_identifier.children
        sub_scorers = retrieved_score.scorer_class_identifier.children["sub_scorers"]
        assert isinstance(sub_scorers, list)
        assert len(sub_scorers) == 2
        assert sub_scorers[0].class_name == "ScorerA"
        assert sub_scorers[1].class_name == "ScorerB"

    def test_score_entry_to_dict(self):
        """Test that to_dict includes all fields correctly."""
        scorer_identifier = ComponentIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score",
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
        assert result["scorer_class_identifier"][ComponentIdentifier.KEY_CLASS_NAME] == "TestScorer"
        assert result["objective"] == "objective"

    def test_score_to_dict_serializes_scorer_identifier(self):
        """Test that Score.to_dict() properly serializes the ComponentIdentifier."""
        scorer_identifier = ComponentIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score",
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

        # to_dict should serialize ComponentIdentifier to dict
        assert isinstance(result["scorer_class_identifier"], dict)
        assert result["scorer_class_identifier"][ComponentIdentifier.KEY_CLASS_NAME] == "TestScorer"
