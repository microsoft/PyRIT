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


class _DefaultNameCondition(Condition):
    threshold: float = 0.5


def test_matches_objective_has_stable_discriminator():
    assert MatchesObjective().condition_type == "matches_objective"
    assert _CONDITION_TYPES["matches_objective"] is MatchesObjective


def test_explicit_discriminator_is_registered():
    assert _KeywordCondition(keyword="k").condition_type == "test_keyword_condition"
    assert _CONDITION_TYPES["test_keyword_condition"] is _KeywordCondition


def test_discriminator_falls_back_to_class_name_without_field():
    assert _CONDITION_TYPES["_DefaultNameCondition"] is _DefaultNameCondition


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


def test_matches_objective_instances_compare_equal():
    assert MatchesObjective() == MatchesObjective()


def test_duplicate_discriminator_is_rejected():
    with pytest.raises((ValueError, TypeError), match="already registered"):

        class _First(Condition):
            condition_type: Literal["test_duplicate_discriminator"] = "test_duplicate_discriminator"

        class _Second(Condition):
            condition_type: Literal["test_duplicate_discriminator"] = "test_duplicate_discriminator"
