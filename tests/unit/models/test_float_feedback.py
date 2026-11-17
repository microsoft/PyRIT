# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import math

import pytest

from pyrit.models import (
    FLOAT_FEEDBACK_METADATA_KEY,
    ComponentIdentifier,
    FloatFeedback,
    InvalidFloatFeedbackError,
    Score,
)
from pyrit.score.score_utils import ORIGINAL_FLOAT_VALUE_KEY

# Score metadata coerces booleans to integers, so a boolean is only rejected on construction.
INVALID = [None, "0.5", math.nan, math.inf, -0.1, 1.5]


def _score(metadata: dict | None) -> Score:
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=None,
        score_value_description="",
        score_rationale="",
        score_metadata=metadata,
        scorer_class_identifier=ComponentIdentifier(class_name="ThresholdLike", class_module="test"),
    )


def test_the_existing_key_is_kept_so_stored_scores_need_no_migration():
    assert FLOAT_FEEDBACK_METADATA_KEY == "original_float_value" == ORIGINAL_FLOAT_VALUE_KEY


@pytest.mark.parametrize("value", [0.0, 0.6, 1.0, 1])
def test_reads_a_usable_value(value):
    score = _score({FLOAT_FEEDBACK_METADATA_KEY: value})
    assert FloatFeedback.read(score) == FloatFeedback(value=float(value))
    assert FloatFeedback.require(score).value == float(value)


@pytest.mark.parametrize("raw", INVALID)
def test_ordinary_reads_treat_missing_or_unusable_data_as_unset(raw):
    metadata = None if raw is None else {FLOAT_FEEDBACK_METADATA_KEY: raw}
    assert FloatFeedback.read(_score(metadata)) is None
    assert FloatFeedback.read(None) is None


@pytest.mark.parametrize("raw", INVALID)
def test_strict_reads_reject_missing_or_unusable_data(raw):
    metadata = {} if raw is None else {FLOAT_FEEDBACK_METADATA_KEY: raw}
    with pytest.raises(InvalidFloatFeedbackError, match="ThresholdLike promised a float value"):
        FloatFeedback.require(_score(metadata))


@pytest.mark.parametrize("raw", [True, math.nan, 2.0])
def test_an_invalid_value_cannot_be_constructed(raw):
    with pytest.raises(InvalidFloatFeedbackError):
        FloatFeedback(value=raw)


def test_writing_and_removing_copy_the_metadata():
    metadata = {"model": "judge"}
    written = FloatFeedback(value=0.4).write_to(metadata)
    assert written == {"model": "judge", FLOAT_FEEDBACK_METADATA_KEY: 0.4}
    assert metadata == {"model": "judge"}
    assert FloatFeedback.remove_from(written) == {"model": "judge"}
    assert FLOAT_FEEDBACK_METADATA_KEY in written
