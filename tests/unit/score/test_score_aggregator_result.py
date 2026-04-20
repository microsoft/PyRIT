# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import FrozenInstanceError

import pytest

from pyrit.score.score_aggregator_result import ScoreAggregatorResult


def test_init_with_bool_value():
    result = ScoreAggregatorResult(
        value=True,
        description="All passed",
        rationale="All scores were true",
        category=["safety"],
        metadata={"count": 3},
    )
    assert result.value is True
    assert result.description == "All passed"
    assert result.rationale == "All scores were true"
    assert result.category == ["safety"]
    assert result.metadata == {"count": 3}


def test_init_with_float_value():
    result = ScoreAggregatorResult(
        value=0.75,
        description="High score",
        rationale="Average was above threshold",
        category=["harm", "violence"],
        metadata={"mean": 0.75, "std": 0.1},
    )
    assert result.value == 0.75
    assert result.description == "High score"
    assert result.category == ["harm", "violence"]
    assert result.metadata == {"mean": 0.75, "std": 0.1}


def test_init_with_empty_category_and_metadata():
    result = ScoreAggregatorResult(
        value=False,
        description="No matches",
        rationale="",
        category=[],
        metadata={},
    )
    assert result.category == []
    assert result.metadata == {}
    assert result.rationale == ""


def test_frozen_cannot_set_value():
    result = ScoreAggregatorResult(
        value=True,
        description="test",
        rationale="test",
        category=[],
        metadata={},
    )
    with pytest.raises(FrozenInstanceError):
        result.value = False  # type: ignore[misc]


def test_frozen_cannot_set_description():
    result = ScoreAggregatorResult(
        value=0.5,
        description="original",
        rationale="test",
        category=[],
        metadata={},
    )
    with pytest.raises(FrozenInstanceError):
        result.description = "changed"  # type: ignore[misc]


def test_equality_same_values():
    r1 = ScoreAggregatorResult(value=True, description="d", rationale="r", category=["c"], metadata={"k": 1})
    r2 = ScoreAggregatorResult(value=True, description="d", rationale="r", category=["c"], metadata={"k": 1})
    assert r1 == r2


def test_inequality_different_values():
    r1 = ScoreAggregatorResult(value=True, description="d", rationale="r", category=[], metadata={})
    r2 = ScoreAggregatorResult(value=False, description="d", rationale="r", category=[], metadata={})
    assert r1 != r2


def test_inequality_different_description():
    r1 = ScoreAggregatorResult(value=0.5, description="a", rationale="r", category=[], metadata={})
    r2 = ScoreAggregatorResult(value=0.5, description="b", rationale="r", category=[], metadata={})
    assert r1 != r2


def test_slots_no_dict():
    result = ScoreAggregatorResult(value=True, description="d", rationale="r", category=[], metadata={})
    assert not hasattr(result, "__dict__")


def test_metadata_with_mixed_types():
    result = ScoreAggregatorResult(
        value=0.9,
        description="mixed",
        rationale="test",
        category=["a"],
        metadata={"name": "scorer1", "count": 5, "threshold": 0.8},
    )
    assert result.metadata["name"] == "scorer1"
    assert result.metadata["count"] == 5
    assert result.metadata["threshold"] == 0.8
