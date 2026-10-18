# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the lazy ``__getattr__`` hooks on scenario subpackages."""

from collections.abc import Callable, Iterator
from importlib import import_module
from unittest.mock import MagicMock, patch

import pytest

from pyrit.executor.attack import PromptSendingAttack
from pyrit.prompt_target import PromptTarget
from pyrit.registry import Registry, TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.scenario.scenarios._dynamic_techniques import reset_dynamic_technique_caches
from pyrit.scenario.scenarios.airt.cyber import _build_cyber_technique
from pyrit.scenario.scenarios.airt.leakage import _build_leakage_technique
from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_technique
from pyrit.scenario.scenarios.benchmark.adversarial import _build_benchmark_technique
from pyrit.score import TrueFalseScorer
from pyrit.setup.initialization import reset_setup_registries
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

    def test_registry_reset_discards_lazy_technique_exports(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        first = airt.RapidResponseTechnique  # type: ignore[attr-defined]
        reset_dynamic_technique_caches()
        second = airt.RapidResponseTechnique  # type: ignore[attr-defined]

        assert second is not first

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


@pytest.fixture
def isolated_setup_registries() -> Iterator[None]:
    with patch.dict(Registry._singletons):
        try:
            yield
        finally:
            reset_dynamic_technique_caches()


@pytest.mark.usefixtures("patch_central_database", "isolated_setup_registries")
class TestDynamicTechniqueReset:
    @pytest.mark.parametrize("reset_caches", [reset_dynamic_technique_caches, reset_setup_registries])
    @pytest.mark.parametrize(
        ("package_name", "scenario_name", "technique_name"),
        [
            ("garak", "Doctor", "DoctorTechnique"),
            ("airt", "Jailbreak", "JailbreakTechnique"),
        ],
    )
    def test_local_technique_identity_survives_reset(
        self,
        *,
        package_name: str,
        scenario_name: str,
        technique_name: str,
        reset_caches: Callable[[], None],
    ) -> None:
        packages = [
            import_module(f"pyrit.scenario.{package_name}"),
            import_module(f"pyrit.scenario.scenarios.{package_name}"),
            import_module("pyrit.scenario.scenarios._dynamic_techniques"),
        ]
        technique_class = getattr(packages[0], technique_name)
        scenario_class = getattr(packages[0], scenario_name)
        scorer = MagicMock(spec=TrueFalseScorer)
        before = scenario_class(objective_scorer=scorer)

        for _ in range(2):
            reset_caches()

            assert all(getattr(package, technique_name) is technique_class for package in packages)
            after = scenario_class(objective_scorer=scorer)
            assert after._technique_class is before._technique_class is technique_class
            assert after._default_technique is before._default_technique is technique_class.default()
            assert (
                after._technique_class.resolve([technique_class["ALL"]], default=after._default_technique)
                == technique_class.get_all_techniques()
            )

    @pytest.mark.parametrize(
        ("package_name", "technique_name"),
        [
            ("airt", "CyberTechnique"),
            ("airt", "LeakageTechnique"),
            ("airt", "MultilingualTechnique"),
            ("airt", "RapidResponseTechnique"),
            ("benchmark", "AdversarialBenchmarkTechnique"),
        ],
    )
    def test_registry_technique_cache_and_exports_are_discarded(
        self, *, package_name: str, technique_name: str
    ) -> None:
        package = import_module(f"pyrit.scenario.scenarios.{package_name}")
        dynamic = import_module("pyrit.scenario.scenarios._dynamic_techniques")
        reset_dynamic_technique_caches()
        before = getattr(dynamic, technique_name)
        assert getattr(package, technique_name) is before

        reset_dynamic_technique_caches()

        assert technique_name not in vars(package)
        after = getattr(dynamic, technique_name)
        assert after is not before
        assert getattr(package, technique_name) is after

    def test_setup_reset_rebuilds_from_replacement_catalog(self) -> None:
        import pyrit.scenario.scenarios.airt as airt

        reset_dynamic_technique_caches()
        before = airt.RapidResponseTechnique
        before_techniques = before.get_all_techniques()
        reset_setup_registries()
        factory = AttackTechniqueFactory(name="replacement", attack_class=PromptSendingAttack)
        AttackTechniqueRegistry.get_registry_singleton().register_from_factories([factory])

        after = airt.RapidResponseTechnique

        assert after is not before
        assert [technique.value for technique in after.get_all_techniques()] == ["replacement"]
        assert before.get_all_techniques() == before_techniques
        with pytest.raises(ValueError, match="unsupported techniques"):
            after.resolve([before["ALL"]], default=after.default())
