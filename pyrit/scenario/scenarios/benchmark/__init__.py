# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Benchmark scenario classes."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.scenario.scenarios._dynamic_techniques import AdversarialBenchmarkTechnique
    from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark
    from pyrit.scenario.scenarios.benchmark.sampling_sweep import (
        AdversarialGeneratorSpec,
        SamplingParameter,
        SamplingSweepPlan,
        SamplingVariant,
        UnsupportedParameterPolicy,
        run_sampling_sweep_async,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AdversarialBenchmark": "pyrit.scenario.scenarios.benchmark.adversarial",
    "AdversarialBenchmarkTechnique": "pyrit.scenario.scenarios._dynamic_techniques",
    "AdversarialGeneratorSpec": "pyrit.scenario.scenarios.benchmark.sampling_sweep",
    "SamplingParameter": "pyrit.scenario.scenarios.benchmark.sampling_sweep",
    "SamplingSweepPlan": "pyrit.scenario.scenarios.benchmark.sampling_sweep",
    "SamplingVariant": "pyrit.scenario.scenarios.benchmark.sampling_sweep",
    "UnsupportedParameterPolicy": "pyrit.scenario.scenarios.benchmark.sampling_sweep",
    "run_sampling_sweep_async": "pyrit.scenario.scenarios.benchmark.sampling_sweep",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    if _LAZY_EXPORTS.get(name) == "pyrit.scenario.scenarios._dynamic_techniques":
        from pyrit.scenario.scenarios import _dynamic_techniques

        return getattr(_dynamic_techniques, name)
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
