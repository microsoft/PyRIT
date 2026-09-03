# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass
from typing import ClassVar

import pytest

from pyrit.models import Condition, MatchesObjective
from pyrit.models.score.condition import (
    _CONDITION_TYPES,
    condition_from_dict,
    condition_to_dict,
)


@dataclass(frozen=True, kw_only=True)
class _KeywordCondition(Condition):
    CONDITION_TYPE: ClassVar[str] = "test_keyword_condition"
    keyword: str


@dataclass(frozen=True, kw_only=True)
class _DefaultNameCondition(Condition):
    threshold: float = 0.5


@dataclass(frozen=True, kw_only=True)
class _ReservedFieldCondition(Condition):
    CONDITION_TYPE: ClassVar[str] = "test_reserved_field_condition"
    condition_type: str = "collision"


@dataclass(frozen=True, kw_only=True)
class _TupleCondition(Condition):
    CONDITION_TYPE: ClassVar[str] = "test_tuple_condition"
    values: tuple[str, ...] = ()


def test_matches_objective_has_stable_discriminator():
    assert MatchesObjective.CONDITION_TYPE == "matches_objective"
    assert _CONDITION_TYPES["matches_objective"] is MatchesObjective


def test_explicit_discriminator_is_used():
    assert _KeywordCondition.CONDITION_TYPE == "test_keyword_condition"
    assert _CONDITION_TYPES["test_keyword_condition"] is _KeywordCondition


def test_default_discriminator_falls_back_to_class_name():
    assert _DefaultNameCondition.CONDITION_TYPE == "_DefaultNameCondition"
    assert _CONDITION_TYPES["_DefaultNameCondition"] is _DefaultNameCondition


def test_condition_to_dict_tags_matches_objective():
    assert condition_to_dict(MatchesObjective()) == {"condition_type": "matches_objective"}


def test_condition_round_trip_preserves_fields():
    condition = _KeywordCondition(keyword="secret")

    serialized = condition_to_dict(condition)

    assert serialized == {"condition_type": "test_keyword_condition", "keyword": "secret"}
    assert condition_from_dict(serialized) == condition


def test_condition_from_dict_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unknown condition_type 'nope'"):
        condition_from_dict({"condition_type": "nope"})


def test_condition_to_dict_rejects_reserved_field_name():
    with pytest.raises(ValueError, match="reserved field name 'condition_type'"):
        condition_to_dict(_ReservedFieldCondition())


def test_to_persisted_dict_rejects_non_json_fields():
    with pytest.raises(TypeError, match="do not survive a JSON round trip"):
        _TupleCondition(values=("a", "b")).to_persisted_dict()


def test_duplicate_discriminator_is_rejected():
    class _First(Condition):
        CONDITION_TYPE = "test_duplicate_discriminator"

    with pytest.raises(ValueError, match="already registered"):

        class _Second(Condition):
            CONDITION_TYPE = "test_duplicate_discriminator"
