# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ScenarioRegistry._build_metadata and create_and_initialize_async."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.registry.components.scenario_registry import ScenarioRegistry


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

    with pytest.raises(TypeError, match="must be instantiable with no arguments"):
        registry._build_metadata("not_no_arg", _NotNoArgScenario)


async def test_create_and_initialize_async_creates_sets_params_and_initializes() -> None:
    """The registry owns build + set-params + initialize and returns the scenario."""
    registry = ScenarioRegistry()

    scenario = MagicMock()
    scenario.initialize_async = AsyncMock()
    target = MagicMock()

    registry.create_instance = MagicMock(return_value=scenario)  # type: ignore[method-assign]

    result = await registry.create_and_initialize_async(
        "my.scenario",
        scenario_params={"foo": "bar"},
        scenario_result_id="sr-1",
        objective_target=target,
        max_concurrency=2,
    )

    assert result is scenario
    registry.create_instance.assert_called_once_with("my.scenario", scenario_result_id="sr-1")
    scenario.set_params_from_args.assert_called_once_with(args={"foo": "bar"})
    scenario.initialize_async.assert_awaited_once_with(objective_target=target, max_concurrency=2)


async def test_create_and_initialize_async_omits_result_id_when_none() -> None:
    """When no scenario_result_id is supplied, it is not forwarded to the constructor."""
    registry = ScenarioRegistry()

    scenario = MagicMock()
    scenario.initialize_async = AsyncMock()
    registry.create_instance = MagicMock(return_value=scenario)  # type: ignore[method-assign]

    await registry.create_and_initialize_async("my.scenario", objective_target=MagicMock())

    registry.create_instance.assert_called_once_with("my.scenario")
    scenario.set_params_from_args.assert_called_once_with(args={})
