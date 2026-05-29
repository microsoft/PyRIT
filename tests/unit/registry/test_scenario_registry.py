# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ScenarioRegistry._build_metadata."""

import pytest

from pyrit.registry.class_registries.base_class_registry import ClassEntry
from pyrit.registry.class_registries.scenario_registry import ScenarioRegistry


class _NotNoArgScenario:
    """A scenario-like stub whose constructor requires arguments."""

    @classmethod
    def supported_parameters(cls):
        return []

    def __init__(self, *, required_arg) -> None:
        self.required_arg = required_arg


def test_build_metadata_raises_when_scenario_requires_constructor_args() -> None:
    """Scenarios that cannot be instantiated with no args must surface a clear error."""
    registry = ScenarioRegistry()
    entry = ClassEntry(registered_class=_NotNoArgScenario)

    with pytest.raises(TypeError, match="must be instantiable with no arguments"):
        registry._build_metadata("not_no_arg", entry)
