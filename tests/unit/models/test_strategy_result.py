# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass

from pyrit.models.strategy_result import StrategyResult


@dataclass
class ConcreteResult(StrategyResult):
    value: str = ""
    count: int = 0


def test_strategy_result_duplicate_creates_deep_copy():
    original = ConcreteResult(value="hello", count=5)
    copy = original.duplicate()
    assert copy.value == "hello"
    assert copy.count == 5
    assert copy is not original


def test_strategy_result_duplicate_is_independent():
    original = ConcreteResult(value="hello", count=5)
    copy = original.duplicate()
    copy.value = "changed"
    copy.count = 99
    assert original.value == "hello"
    assert original.count == 5


def test_strategy_result_duplicate_preserves_type():
    original = ConcreteResult(value="test", count=1)
    copy = original.duplicate()
    assert type(copy) is ConcreteResult
