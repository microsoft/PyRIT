# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses

import pytest

from pyrit.models import ScoringExpectation


def test_expectation_defaults():
    assert ScoringExpectation().objective is None


def test_expectation_is_frozen():
    expectation = ScoringExpectation(objective="exfiltrate")

    with pytest.raises(dataclasses.FrozenInstanceError):
        expectation.objective = "something else"


def test_expectations_with_equal_values_compare_equal():
    assert ScoringExpectation(objective="a") == ScoringExpectation(objective="a")
