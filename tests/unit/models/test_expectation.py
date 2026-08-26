# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses

import pytest

from pyrit.models import Condition, MatchesObjective, ScoringExpectation


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
