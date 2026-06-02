# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the lazy ``__getattr__`` hooks on scenario subpackages."""

from unittest.mock import MagicMock

import pytest

from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
from pyrit.scenario.scenarios.airt.cyber import _build_cyber_strategy
from pyrit.scenario.scenarios.airt.leakage import _build_leakage_strategy
from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_strategy
from pyrit.scenario.scenarios.benchmark.adversarial import _build_benchmark_strategy
from pyrit.setup.initializers.components.scenario_techniques import build_scenario_technique_factories


@pytest.fixture(autouse=True)
def populate_registries():
    """Populate the technique + target registries so lazy strategy builders succeed."""
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    _build_cyber_strategy.cache_clear()
    _build_leakage_strategy.cache_clear()
    _build_rapid_response_strategy.cache_clear()
    _build_benchmark_strategy.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().register_instance(adv_target, name="adversarial_chat")

    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_scenario_technique_factories())
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    _build_cyber_strategy.cache_clear()
    _build_leakage_strategy.cache_clear()
    _build_rapid_response_strategy.cache_clear()
    _build_benchmark_strategy.cache_clear()


class TestAirtPackageLazyAttrs:
    """The ``airt`` package exposes dynamic strategy enums via ``__getattr__``."""

    def test_rapid_response_strategy_is_lazy_built(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        cls = airt.RapidResponseStrategy  # type: ignore[attr-defined]
        assert issubclass(cls, ScenarioStrategy)

    def test_leakage_strategy_is_lazy_built(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        cls = airt.LeakageStrategy  # type: ignore[attr-defined]
        assert issubclass(cls, ScenarioStrategy)

    def test_cyber_strategy_is_lazy_built(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        cls = airt.CyberStrategy  # type: ignore[attr-defined]
        assert issubclass(cls, ScenarioStrategy)

    def test_unknown_attribute_raises(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        with pytest.raises(AttributeError, match="no attribute 'NotAThing'"):
            _ = airt.NotAThing  # type: ignore[attr-defined]


class TestBenchmarkPackageLazyAttrs:
    """The ``benchmark`` package exposes the dynamic BenchmarkStrategy via ``__getattr__``."""

    def test_adversarial_benchmark_strategy_is_lazy_built(self) -> None:
        import pyrit.scenario.scenarios.benchmark as benchmark

        cls = benchmark.AdversarialBenchmarkStrategy  # type: ignore[attr-defined]
        assert issubclass(cls, ScenarioStrategy)

    def test_unknown_attribute_raises(self) -> None:
        import pyrit.scenario.scenarios.benchmark as benchmark

        with pytest.raises(AttributeError, match="no attribute 'NotAThing'"):
            _ = benchmark.NotAThing  # type: ignore[attr-defined]
