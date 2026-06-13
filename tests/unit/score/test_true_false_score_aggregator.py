# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.models import ComponentIdentifier, Score
from pyrit.score import TrueFalseScoreAggregator
from pyrit.score.true_false.true_false_score_aggregator import UNSCOREABLE_METADATA_KEY

# Reusable ScorerIdentifier for tests
_TEST_SCORER_ID = ComponentIdentifier(
    class_name="UnitTestScorer",
    class_module="tests.unit.score",
)


def _mk_score(val: bool, *, prr_id: str, rationale: str = "", unscoreable: bool = False) -> Score:
    return Score(
        score_value=str(val).lower(),
        score_value_description=str(val),
        score_type="true_false",
        score_category=["test"],
        score_rationale=rationale,
        score_metadata={UNSCOREABLE_METADATA_KEY: 1} if unscoreable else None,
        message_piece_id=prr_id,
        scorer_class_identifier=_TEST_SCORER_ID,
        objective=None,
    )


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


def test_and_unscoreable_masks_true_warns_and_notes(caplog):
    """An unscoreable false that masks a true under AND is flagged but does not change the verdict.

    Regression guard for the false-assurance hazard: a sub-scorer that merely could not
    evaluate the response (unscoreable fallback false) must not silently veto another
    sub-scorer's confirmed true. The verdict value stays False (non-breaking), but a
    warning is logged and the rationale records the abstention.
    """
    scores = [
        _mk_score(True, prr_id="1", rationale="confirmed harmful"),
        _mk_score(False, prr_id="1", rationale="could not score", unscoreable=True),
    ]

    with caplog.at_level(logging.WARNING, logger="pyrit.score.true_false.true_false_score_aggregator"):
        res = TrueFalseScoreAggregator.AND(scores)

    # Verdict value is unchanged (non-breaking regression guard).
    assert res.value is False
    # A warning was emitted naming the masking hazard.
    assert any("masking a confirmed success" in record.message for record in caplog.records)
    # The rationale surfaces the abstention so the verdict is not read as "not harmful".
    assert "could not evaluate the response" in res.rationale
    assert "under-reporting a confirmed success" in res.rationale


def test_and_unscoreable_present_without_true_warns_but_no_masking_note(caplog):
    """An unscoreable false with no competing true is noted but not flagged as masking."""
    scores = [
        _mk_score(False, prr_id="1", rationale="genuinely not harmful"),
        _mk_score(False, prr_id="1", rationale="could not score", unscoreable=True),
    ]

    with caplog.at_level(logging.WARNING, logger="pyrit.score.true_false.true_false_score_aggregator"):
        res = TrueFalseScoreAggregator.AND(scores)

    assert res.value is False
    # Still warns that an abstention is in the mix, but does not claim a confirmed success was masked.
    assert any("could not evaluate the response" in record.message for record in caplog.records)
    assert "under-reporting a confirmed success" not in res.rationale


def test_genuine_all_false_is_not_flagged(caplog):
    """A genuine all-false aggregate (no unscoreable sub-scores) emits no warning or note."""
    scores = [
        _mk_score(False, prr_id="1", rationale="not harmful A"),
        _mk_score(False, prr_id="1", rationale="not harmful B"),
    ]

    with caplog.at_level(logging.WARNING, logger="pyrit.score.true_false.true_false_score_aggregator"):
        res = TrueFalseScoreAggregator.AND(scores)

    assert res.value is False
    assert caplog.records == []
    assert "could not evaluate the response" not in res.rationale
