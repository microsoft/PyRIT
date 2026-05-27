# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the lazy ``__getattr__`` hooks on scenario subpackages."""

import pytest

from pyrit.scenario.core.scenario_strategy import ScenarioStrategy


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
