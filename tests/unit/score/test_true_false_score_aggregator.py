# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models import ComponentIdentifier, Score
from pyrit.score import TrueFalseScoreAggregator

# Reusable ScorerIdentifier for tests
_TEST_SCORER_ID = ComponentIdentifier(
    class_name="UnitTestScorer",
    class_module="tests.unit.score",
)


def _mk_score(val: bool, *, prr_id: str, rationale: str = "") -> Score:
    return Score(
        score_value=str(val).lower(),
        score_value_description=str(val),
        score_type="true_false",
        score_category=["test"],
        score_rationale=rationale,
        score_metadata=None,
        message_piece_id=prr_id,
        scorer_class_identifier=_TEST_SCORER_ID,
        objective=None,
    )


def test_aggregator_accepts_a_generator():
    scores = (_mk_score(True, prr_id="1") for _ in range(2))
    res = TrueFalseScoreAggregator.AND(scores)
    assert res.value is True


def test_and_aggregator_all_true():
    scores = [_mk_score(True, prr_id="1"), _mk_score(True, prr_id="1")]
    res = TrueFalseScoreAggregator.AND(scores)
    assert res.value is True
    assert isinstance(res.description, str) and res.description
    assert isinstance(res.rationale, str)


def test_and_aggregator_any_false():
    scores = [_mk_score(True, prr_id="1"), _mk_score(False, prr_id="1")]
    res = TrueFalseScoreAggregator.AND(scores)
    assert res.value is False


def test_or_aggregator_any_true():
    scores = [_mk_score(False, prr_id="1"), _mk_score(True, prr_id="1")]
    res = TrueFalseScoreAggregator.OR(scores)
    assert res.value is True


def test_or_aggregator_all_false():
    scores = [_mk_score(False, prr_id="1"), _mk_score(False, prr_id="1")]
    res = TrueFalseScoreAggregator.OR(scores)
    assert res.value is False


def test_majority_strict_majority_true():
    scores = [
        _mk_score(True, prr_id="1", rationale="A"),
        _mk_score(True, prr_id="1", rationale="B"),
        _mk_score(False, prr_id="1", rationale="C"),
    ]
    res = TrueFalseScoreAggregator.MAJORITY(scores)
    assert res.value is True
    assert "MAJORITY" in res.description
    assert "-" in res.rationale or ":" in res.rationale


def test_majority_tie_is_false():
    scores = [_mk_score(True, prr_id="1"), _mk_score(False, prr_id="1")]
    res = TrueFalseScoreAggregator.MAJORITY(scores)
    assert res.value is False


def test_category_deduplication():
    """Test that duplicate categories are deduplicated into a single category."""
    scores = [
        Score(
            score_value="true",
            score_value_description="true",
            score_type="true_false",
            score_category=["Hate"],
            score_rationale="test1",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
        Score(
            score_value="true",
            score_value_description="true",
            score_type="true_false",
            score_category=["Hate"],
            score_rationale="test2",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
    ]
    res = TrueFalseScoreAggregator.AND(scores)
    assert res.value is True
    assert res.category == ["Hate"]  # Should be deduplicated to single entry


def test_category_multiple_unique():
    """Test that multiple unique categories are preserved and sorted."""
    scores = [
        Score(
            score_value="true",
            score_value_description="true",
            score_type="true_false",
            score_category=["Violence"],
            score_rationale="test1",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
        Score(
            score_value="true",
            score_value_description="true",
            score_type="true_false",
            score_category=["Hate"],
            score_rationale="test2",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
    ]
    res = TrueFalseScoreAggregator.OR(scores)
    assert res.value is True
    assert res.category == ["Hate", "Violence"]  # Should be sorted alphabetically


def test_category_empty_strings_filtered():
    """Test that empty string categories are filtered out."""
    scores = [
        Score(
            score_value="true",
            score_value_description="true",
            score_type="true_false",
            score_category=[""],
            score_rationale="test1",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
        Score(
            score_value="false",
            score_value_description="false",
            score_type="true_false",
            score_category=[""],
            score_rationale="test2",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
    ]
    res = TrueFalseScoreAggregator.MAJORITY(scores)
    assert res.category == []  # Empty strings should be filtered out


def test_category_mixed_empty_and_valid():
    """Test that empty strings are filtered but valid categories are kept."""
    scores = [
        Score(
            score_value="true",
            score_value_description="true",
            score_type="true_false",
            score_category=[""],
            score_rationale="test1",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
        Score(
            score_value="true",
            score_value_description="true",
            score_type="true_false",
            score_category=["Violence"],
            score_rationale="test2",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
    ]
    res = TrueFalseScoreAggregator.AND(scores)
    assert res.value is True
    assert res.category == ["Violence"]  # Only valid category preserved


def test_category_none_and_empty_list():
    """Test that None and empty list categories result in empty category list."""
    scores = [
        Score(
            score_value="true",
            score_value_description="true",
            score_type="true_false",
            score_category=[],
            score_rationale="test1",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
        Score(
            score_value="true",
            score_value_description="true",
            score_type="true_false",
            score_category=[],
            score_rationale="test2",
            score_metadata=None,
            message_piece_id="1",
            scorer_class_identifier=_TEST_SCORER_ID,
            objective=None,
        ),
    ]
    res = TrueFalseScoreAggregator.OR(scores)
    assert res.value is True
    assert res.category == []  # Should be empty list


def test_aggregator_empty_scores():
    """Test that empty score list returns a neutral false result."""
    res = TrueFalseScoreAggregator.AND([])
    assert res.value is False
    assert "No scores provided" in res.description


def test_aggregator_single_score():
    """Test that single score preserves its description and rationale."""
    scores = [_mk_score(True, prr_id="1", rationale="Single score rationale")]
    res = TrueFalseScoreAggregator.OR(scores)
    assert res.value is True
    assert res.rationale == "Single score rationale"


def test_aggregators_accept_generators():
    """
    Aggregators are typed to take an Iterable, so a generator must aggregate the same
    as the equivalent list. Validating by iterating before materializing exhausted the
    generator and silently produced the empty-input result (False).
    """
    values = [False, True, False]

    for aggregator in (TrueFalseScoreAggregator.OR, TrueFalseScoreAggregator.AND):
        from_list = aggregator([_mk_score(v, prr_id="1") for v in values])
        from_generator = aggregator(_mk_score(v, prr_id="1") for v in values)
        assert from_generator.value == from_list.value
        assert from_generator.description == from_list.description


def test_generator_of_wrong_type_still_raises():
    """Materializing first must not weaken type validation."""
    bad = Score(
        score_value="0.5",
        score_value_description="",
        score_type="float_scale",
        score_category=["test"],
        score_rationale="",
        score_metadata=None,
        message_piece_id="1",
        scorer_class_identifier=_TEST_SCORER_ID,
        objective=None,
    )
    with pytest.raises(ValueError, match="must be of type 'true_false'"):
        TrueFalseScoreAggregator.OR(s for s in [bad])
