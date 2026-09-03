# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal

import pytest
from pydantic import ValidationError

from pyrit.models import Condition, MatchesObjective
from pyrit.models.score.condition import _CONDITION_TYPES, condition_from_dict


class _KeywordCondition(Condition):
    condition_type: Literal["test_keyword_condition"] = "test_keyword_condition"
    keyword: str


def test_matches_objective_has_stable_discriminator():
    assert MatchesObjective().condition_type == "matches_objective"
    assert _CONDITION_TYPES["matches_objective"] is MatchesObjective


def test_explicit_discriminator_is_registered():
    assert _KeywordCondition(keyword="k").condition_type == "test_keyword_condition"
    assert _CONDITION_TYPES["test_keyword_condition"] is _KeywordCondition


def test_condition_subclass_requires_discriminator_field():
    with pytest.raises(TypeError, match="single non-empty string Literal"):

        class _MissingDiscriminatorCondition(Condition):
            threshold: float = 0.5


def test_condition_subclass_requires_single_literal_value():
    with pytest.raises(TypeError, match="single non-empty string Literal"):

        class _MultipleDiscriminatorCondition(Condition):
            condition_type: Literal["first", "second"] = "first"


def test_condition_subclass_requires_matching_literal_default():
    with pytest.raises(TypeError, match="must default to its Literal value"):

        class _MismatchedDiscriminatorCondition(Condition):
            condition_type: Literal["expected"] = "different"  # type: ignore[assignment]


def test_condition_base_cannot_be_instantiated():
    with pytest.raises(ValidationError, match="abstract registry root"):
        Condition(condition_type="base")


def test_condition_is_frozen():
    condition = MatchesObjective()

    with pytest.raises(ValidationError):
        condition.condition_type = "something"


def test_matches_objective_serializes_to_discriminator_only():
    assert MatchesObjective().model_dump() == {"condition_type": "matches_objective"}


def test_condition_round_trip_preserves_subclass_fields():
    condition = _KeywordCondition(keyword="secret")

    serialized = condition.model_dump()

    assert serialized == {"condition_type": "test_keyword_condition", "keyword": "secret"}
    assert condition_from_dict(serialized) == condition


def test_condition_from_dict_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unknown condition_type 'nope'"):
        condition_from_dict({"condition_type": "nope"})


@pytest.mark.parametrize("value", [{}, {"condition_type": 1}])
def test_condition_from_dict_rejects_missing_or_non_string_type(value):
    with pytest.raises(ValueError, match="requires a string condition_type"):
        condition_from_dict(value)


def test_condition_from_dict_rejects_extra_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        condition_from_dict({"condition_type": "matches_objective", "unexpected": True})


def test_matches_objective_instances_compare_equal():
    assert MatchesObjective() == MatchesObjective()


def test_duplicate_discriminator_is_rejected():
    with pytest.raises((ValueError, TypeError), match="already registered"):

        class _First(Condition):
            condition_type: Literal["test_duplicate_discriminator"] = "test_duplicate_discriminator"

        class _Second(Condition):
            condition_type: Literal["test_duplicate_discriminator"] = "test_duplicate_discriminator"
