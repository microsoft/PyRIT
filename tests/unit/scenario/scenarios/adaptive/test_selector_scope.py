# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses

import pytest

from pyrit.scenario.scenarios.adaptive.selectors import SelectorScope


class TestSelectorScopeDefaults:
    def test_default_constructs_all_runs(self):
        scope = SelectorScope()
        assert scope.current_run_only is False

    def test_all_runs_classmethod_equivalent_to_default(self):
        assert SelectorScope.all_runs() == SelectorScope()

    def test_current_run_classmethod_sets_flag(self):
        scope = SelectorScope.current_run()
        assert scope.current_run_only is True


class TestSelectorScopeFrozen:
    def test_assigning_field_raises(self):
        scope = SelectorScope()
        with pytest.raises(dataclasses.FrozenInstanceError):
            scope.current_run_only = True  # type: ignore[misc]


class TestSelectorScopeCombinations:
    def test_fields_combine(self):
        scope = SelectorScope(current_run_only=True)
        assert scope.current_run_only is True

    def test_equality_value_based(self):
        a = SelectorScope(current_run_only=True)
        b = SelectorScope(current_run_only=True)
        assert a == b

    def test_inequality_when_fields_differ(self):
        a = SelectorScope.all_runs()
        b = SelectorScope.current_run()
        assert a != b
