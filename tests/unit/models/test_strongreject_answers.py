# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models import ComponentIdentifier, InvalidStrongRejectAnswersError, Score, StrongRejectAnswers

KEYS = {
    "refusal": StrongRejectAnswers.REFUSAL_KEY,
    "convincingness": StrongRejectAnswers.CONVINCINGNESS_KEY,
    "specificity": StrongRejectAnswers.SPECIFICITY_KEY,
}


def _score(metadata: dict | None) -> Score:
    return Score(
        score_type="float_scale",
        score_value="0.875",
        score_category=None,
        score_value_description="",
        score_rationale="",
        score_metadata=metadata,
        scorer_class_identifier=ComponentIdentifier(class_name="StrongRejectScorer", class_module="test"),
    )


def test_keys_are_namespaced():
    assert set(KEYS.values()) == {
        "strongreject_refusal",
        "strongreject_convincingness",
        "strongreject_specificity",
    }


def test_round_trips_through_score_metadata():
    answers = StrongRejectAnswers(refusal=0, convincingness=4, specificity=5)

    assert answers.to_metadata() == {
        "strongreject_refusal": 0,
        "strongreject_convincingness": 4,
        "strongreject_specificity": 5,
    }
    assert StrongRejectAnswers.read(_score(answers.to_metadata())) == answers


def test_reading_keeps_other_metadata_out():
    metadata = {**StrongRejectAnswers(refusal=1, convincingness=1, specificity=2).to_metadata(), "other": "x"}

    assert StrongRejectAnswers.from_metadata(metadata=metadata) == StrongRejectAnswers(
        refusal=1, convincingness=1, specificity=2
    )


@pytest.mark.parametrize("metadata", [None, {}, {"original_float_value": 0.5}])
def test_scores_without_answers_read_as_unset(metadata):
    assert StrongRejectAnswers.from_metadata(metadata=metadata) is None
    assert StrongRejectAnswers.read(_score(metadata)) is None


def test_reading_none_score_is_unset():
    assert StrongRejectAnswers.read(None) is None


@pytest.mark.parametrize("missing", list(KEYS.values()))
def test_partial_answers_read_as_unset(missing):
    metadata = StrongRejectAnswers(refusal=0, convincingness=3, specificity=3).to_metadata()
    del metadata[missing]

    assert StrongRejectAnswers.from_metadata(metadata=metadata) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("refusal", 2),
        ("refusal", -1),
        ("convincingness", 0),
        ("convincingness", 6),
        ("specificity", 10),
        ("specificity", "5"),
        ("convincingness", 4.0),
        ("refusal", True),
    ],
)
def test_invalid_stored_answers_read_as_unset(field, value):
    metadata = {**StrongRejectAnswers(refusal=0, convincingness=3, specificity=3).to_metadata(), KEYS[field]: value}

    assert StrongRejectAnswers.from_metadata(metadata=metadata) is None


@pytest.mark.parametrize(
    "answers",
    [
        {"refusal": 2, "convincingness": 3, "specificity": 3},
        {"refusal": 0, "convincingness": 0, "specificity": 3},
        {"refusal": 0, "convincingness": 3, "specificity": 6},
        {"refusal": True, "convincingness": 3, "specificity": 3},
        {"refusal": 0, "convincingness": 3.0, "specificity": 3},
    ],
)
def test_construction_rejects_answers_outside_the_rubric(answers):
    with pytest.raises(InvalidStrongRejectAnswersError):
        StrongRejectAnswers(**answers)
