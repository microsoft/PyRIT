# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""``ScenarioPlanLookup.from_plan`` is annotated ``-> Self`` and builds the calling subclass."""

from typing import assert_type

import pytest

from pyrit.analytics.scenario_statistics import ScenarioPlanLookup


class _DerivedScenarioPlanLookup(ScenarioPlanLookup):
    pass


@pytest.mark.parametrize("lookup_cls", [ScenarioPlanLookup, _DerivedScenarioPlanLookup])
def test_from_plan_returns_calling_class(lookup_cls: type[ScenarioPlanLookup]) -> None:
    lookup = lookup_cls.from_plan(plan=None)

    assert type(lookup) is lookup_cls
    assert lookup.groups_by_name == {}


def test_from_plan_preserves_subclass_type() -> None:
    lookup = _DerivedScenarioPlanLookup.from_plan(plan=None)

    assert_type(lookup, _DerivedScenarioPlanLookup)
