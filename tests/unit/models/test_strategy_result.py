# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models import StrategyResult


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


def test_strategy_result_forbids_extra_fields():
    with pytest.raises(ValueError):
        ConcreteResult(value="hello", count=1, unexpected="boom")
