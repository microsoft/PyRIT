# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Lazy exports for scenario technique classes built from registered catalogs."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.scenario.core import ScenarioTechnique

    AdversarialBenchmarkTechnique: type[ScenarioTechnique]
    CyberTechnique: type[ScenarioTechnique]
    DoctorTechnique: type[ScenarioTechnique]
    JailbreakTechnique: type[ScenarioTechnique]
    LeakageTechnique: type[ScenarioTechnique]
    MultilingualTechnique: type[ScenarioTechnique]
    RapidResponseTechnique: type[ScenarioTechnique]

_TECHNIQUE_BUILDERS = {
    "AdversarialBenchmarkTechnique": (
        "pyrit.scenario.scenarios.benchmark.adversarial",
        "_build_benchmark_technique",
    ),
    "CyberTechnique": ("pyrit.scenario.scenarios.airt.cyber", "_build_cyber_technique"),
    "DoctorTechnique": ("pyrit.scenario.scenarios.garak.doctor", "_build_doctor_technique"),
    "JailbreakTechnique": ("pyrit.scenario.scenarios.airt.jailbreak", "_build_jailbreak_technique"),
    "LeakageTechnique": ("pyrit.scenario.scenarios.airt.leakage", "_build_leakage_technique"),
    "MultilingualTechnique": (
        "pyrit.scenario.scenarios.airt.multilingual",
        "_build_multilingual_technique",
    ),
    "RapidResponseTechnique": (
        "pyrit.scenario.scenarios.airt.rapid_response",
        "_build_rapid_response_technique",
    ),
}


def __getattr__(name: str) -> object:
    try:
        module_name, builder_name = _TECHNIQUE_BUILDERS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    builder = getattr(import_module(module_name), builder_name)
    value = builder()
    globals()[name] = value
    return value


def reset_dynamic_technique_caches() -> None:
    """Discard scenario technique classes derived from the live registry."""
    package_names = {
        "pyrit.scenario.scenarios.airt",
        "pyrit.scenario.scenarios.benchmark",
        "pyrit.scenario.scenarios.garak",
    }
    for technique_name, (module_name, builder_name) in _TECHNIQUE_BUILDERS.items():
        builder = getattr(import_module(module_name), builder_name)
        builder.cache_clear()
        globals().pop(technique_name, None)
        for package_name in package_names:
            package = import_module(package_name)
            package.__dict__.pop(technique_name, None)
