# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the lazy ``__getattr__`` hooks on scenario subpackages."""

from unittest.mock import MagicMock

import pytest

from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.scenario.scenarios.airt.cyber import _build_cyber_technique
from pyrit.scenario.scenarios.airt.leakage import _build_leakage_technique
from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_technique
from pyrit.scenario.scenarios.benchmark.adversarial import _build_benchmark_technique
from pyrit.setup.initializers.techniques import build_technique_factories


@pytest.fixture(autouse=True)
def populate_registries():
    """Populate the technique + target registries so lazy technique builders succeed."""
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_cyber_technique.cache_clear()
    _build_leakage_technique.cache_clear()
    _build_rapid_response_technique.cache_clear()
    _build_benchmark_technique.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().instances.register(adv_target, name="adversarial_chat")

    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_cyber_technique.cache_clear()
    _build_leakage_technique.cache_clear()
    _build_rapid_response_technique.cache_clear()
    _build_benchmark_technique.cache_clear()


class TestAirtPackageLazyAttrs:
    """The ``airt`` package exposes dynamic technique enums via ``__getattr__``."""

    def test_rapid_response_technique_is_lazy_built(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        cls = airt.RapidResponseTechnique  # type: ignore[attr-defined]
        assert issubclass(cls, ScenarioTechnique)

    def test_leakage_technique_is_lazy_built(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        cls = airt.LeakageTechnique  # type: ignore[attr-defined]
        assert issubclass(cls, ScenarioTechnique)

    def test_cyber_technique_is_lazy_built(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        cls = airt.CyberTechnique  # type: ignore[attr-defined]
        assert issubclass(cls, ScenarioTechnique)

    def test_unknown_attribute_raises(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        with pytest.raises(AttributeError, match="no attribute 'NotAThing'"):
            _ = airt.NotAThing  # type: ignore[attr-defined]


class TestBenchmarkPackageLazyAttrs:
    """The ``benchmark`` package exposes the dynamic BenchmarkTechnique via ``__getattr__``."""

    def test_adversarial_benchmark_technique_is_lazy_built(self) -> None:
        import pyrit.scenario.scenarios.benchmark as benchmark

        cls = benchmark.AdversarialBenchmarkTechnique  # type: ignore[attr-defined]
        assert issubclass(cls, ScenarioTechnique)

    def test_unknown_attribute_raises(self) -> None:
        import pyrit.scenario.scenarios.benchmark as benchmark

        with pytest.raises(AttributeError, match="no attribute 'NotAThing'"):
            _ = benchmark.NotAThing  # type: ignore[attr-defined]
