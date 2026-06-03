# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses

import pytest

from pyrit.scenario.scenarios.adaptive.selectors import SelectorScope


class TestSelectorScopeDefaults:
    def test_default_constructs_all_runs(self):
        scope = SelectorScope()
        assert scope.current_run_only is False
        assert scope.targeted_harm_categories is None

    def test_all_runs_classmethod_equivalent_to_default(self):
        assert SelectorScope.all_runs() == SelectorScope()

    def test_current_run_classmethod_sets_flag(self):
        scope = SelectorScope.current_run()
        assert scope.current_run_only is True
        assert scope.targeted_harm_categories is None


class TestSelectorScopeFrozen:
    def test_assigning_field_raises(self):
        scope = SelectorScope()
        with pytest.raises(dataclasses.FrozenInstanceError):
            scope.current_run_only = True  # type: ignore[misc]

    def test_assigning_new_field_raises(self):
        scope = SelectorScope()
        with pytest.raises(dataclasses.FrozenInstanceError):
            scope.targeted_harm_categories = ("a",)  # type: ignore[misc]


class TestSelectorScopeCombinations:
    def test_fields_combine(self):
        scope = SelectorScope(
            current_run_only=True,
            targeted_harm_categories=["misinformation"],
        )
        assert scope.current_run_only is True
        assert scope.targeted_harm_categories == ["misinformation"]

    def test_equality_value_based(self):
        a = SelectorScope(targeted_harm_categories=("y",))
        b = SelectorScope(targeted_harm_categories=("y",))
        assert a == b

    def test_inequality_when_fields_differ(self):
        a = SelectorScope.all_runs()
        b = SelectorScope.current_run()
        assert a != b
