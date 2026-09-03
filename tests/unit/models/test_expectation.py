# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
from typing import ClassVar

import pytest

from pyrit.models import (
    Condition,
    MatchesObjective,
    ScoringExpectation,
    scoring_expectation_fingerprint,
    scoring_expectation_from_dict,
    scoring_expectation_to_dict,
)


@dataclasses.dataclass(frozen=True, kw_only=True)
class _AlphaCondition(Condition):
    CONDITION_TYPE: ClassVar[str] = "test_expectation_alpha"


@dataclasses.dataclass(frozen=True, kw_only=True)
class _BetaCondition(Condition):
    CONDITION_TYPE: ClassVar[str] = "test_expectation_beta"
    label: str = "b"


def test_expectation_defaults():
    expectation = ScoringExpectation()

    assert expectation.objective is None
    assert expectation.conditions == ()


def test_expectation_is_frozen():
    expectation = ScoringExpectation(objective="exfiltrate")

    with pytest.raises(dataclasses.FrozenInstanceError):
        expectation.objective = "something else"


def test_expectations_with_equal_values_compare_equal():
    assert ScoringExpectation(objective="a") == ScoringExpectation(objective="a")


def test_expectation_carries_conditions_beside_the_objective():
    expectation = ScoringExpectation(objective="exfiltrate", conditions=(MatchesObjective(),))

    assert expectation.objective == "exfiltrate"
    assert expectation.conditions == (MatchesObjective(),)


def test_expectation_carries_conditions_without_an_objective():
    expectation = ScoringExpectation(conditions=(MatchesObjective(),))

    assert expectation.objective is None
    assert expectation.conditions == (MatchesObjective(),)


def test_expectations_differing_only_in_conditions_compare_unequal():
    assert ScoringExpectation(objective="a") != ScoringExpectation(objective="a", conditions=(MatchesObjective(),))


def test_matches_objective_carries_no_text_of_its_own():
    assert dataclasses.fields(MatchesObjective()) == ()


def test_matches_objective_is_a_condition():
    assert isinstance(MatchesObjective(), Condition)


def test_matches_objective_instances_compare_equal():
    assert MatchesObjective() == MatchesObjective()


def test_matches_objective_is_frozen():
    condition = MatchesObjective()

    with pytest.raises(dataclasses.FrozenInstanceError):
        condition.objective = "something"


# --------------------------------------------------------------------------- #
# Versioned serialization
# --------------------------------------------------------------------------- #
def test_objective_only_round_trip():
    expectation = ScoringExpectation(objective="do x")

    serialized = scoring_expectation_to_dict(expectation)

    assert serialized == {"schema_version": 1, "objective": "do x", "conditions": []}
    assert scoring_expectation_from_dict(serialized) == expectation


def test_condition_only_round_trip():
    expectation = ScoringExpectation(conditions=(MatchesObjective(),))

    serialized = scoring_expectation_to_dict(expectation)

    assert serialized == {
        "schema_version": 1,
        "objective": None,
        "conditions": [{"condition_type": "matches_objective"}],
    }
    assert scoring_expectation_from_dict(serialized) == expectation


def test_mixed_round_trip():
    expectation = ScoringExpectation(objective="do x", conditions=(MatchesObjective(),))

    assert scoring_expectation_from_dict(scoring_expectation_to_dict(expectation)) == expectation


def test_conditions_serialize_in_order():
    expectation = ScoringExpectation(conditions=(_AlphaCondition(), _BetaCondition(label="x")))

    serialized = scoring_expectation_to_dict(expectation)

    assert [condition["condition_type"] for condition in serialized["conditions"]] == [
        "test_expectation_alpha",
        "test_expectation_beta",
    ]
    assert scoring_expectation_from_dict(serialized) == expectation


def test_from_dict_rejects_unknown_schema_version():
    with pytest.raises(ValueError, match="Unsupported ScoringExpectation schema_version"):
        scoring_expectation_from_dict({"schema_version": 99, "objective": None, "conditions": []})


def test_from_dict_rejects_unknown_top_level_field():
    with pytest.raises(ValueError, match="Unknown ScoringExpectation field"):
        scoring_expectation_from_dict({"schema_version": 1, "objective": None, "conditions": [], "extra": 1})


def test_from_dict_rejects_non_string_objective():
    with pytest.raises(ValueError, match="objective must be a string or None"):
        scoring_expectation_from_dict({"schema_version": 1, "objective": 5, "conditions": []})


def test_from_dict_rejects_conditions_not_list_of_dicts():
    with pytest.raises(ValueError, match="conditions must be a list of dicts"):
        scoring_expectation_from_dict({"schema_version": 1, "objective": None, "conditions": ["nope"]})


def test_from_dict_rejects_unknown_condition_type():
    with pytest.raises(ValueError, match="Unknown condition_type"):
        scoring_expectation_from_dict(
            {"schema_version": 1, "objective": None, "conditions": [{"condition_type": "ghost"}]}
        )


# --------------------------------------------------------------------------- #
# __post_init__ validation
# --------------------------------------------------------------------------- #
def test_post_init_rejects_non_string_objective():
    with pytest.raises(TypeError, match="objective must be a string or None"):
        ScoringExpectation(objective=5)  # type: ignore[arg-type]


def test_post_init_rejects_non_condition():
    with pytest.raises(TypeError, match="must all be Condition instances"):
        ScoringExpectation(conditions=("not a condition",))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Fingerprint
# --------------------------------------------------------------------------- #
def test_fingerprint_ignores_construction_kwarg_order():
    first = ScoringExpectation(objective="o", conditions=(_BetaCondition(label="x"),))
    second = ScoringExpectation(conditions=(_BetaCondition(label="x"),), objective="o")

    assert scoring_expectation_fingerprint(first) == scoring_expectation_fingerprint(second)


def test_fingerprint_is_lowercase_sha256_hex():
    fingerprint = scoring_expectation_fingerprint(ScoringExpectation(objective="o"))

    assert len(fingerprint) == 64
    assert fingerprint == fingerprint.lower()


def test_fingerprint_depends_on_condition_order():
    forward = ScoringExpectation(conditions=(_AlphaCondition(), _BetaCondition(label="x")))
    reverse = ScoringExpectation(conditions=(_BetaCondition(label="x"), _AlphaCondition()))

    assert scoring_expectation_fingerprint(forward) != scoring_expectation_fingerprint(reverse)
