# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Registry module for PyRIT class and object registries."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.registry.components import (
        AttackRegistry,
        AttackTechniqueMetadata,
        AttackTechniqueRegistry,
        ConverterMetadata,
        ConverterRegistry,
        InitializerMetadata,
        InitializerRegistry,
        ScenarioMetadata,
        ScenarioPresetMetadata,
        ScenarioPresetNameRegisteredError,
        ScenarioPresetRegistry,
        ScenarioRegistry,
        ScenarioSuiteMetadata,
        ScenarioSuiteNameRegisteredError,
        ScenarioSuiteRegistry,
        ScorerMetadata,
        ScorerRegistry,
        TargetMetadata,
        TargetRegistry,
    )
    from pyrit.registry.discovery import discover_in_directory
    from pyrit.registry.instance_registry import (
        DefaultInstanceRegistry,
        InstanceRegistry,
        RegistryEntry,
        SupportsInstances,
    )
    from pyrit.registry.registry import InstanceHoldingRegistry, ParamBagRegistry, Registry
    from pyrit.registry.registry_metadata import RegistryMetadata
    from pyrit.registry.scenario_preset_storage import ScenarioPresetConflictError, ScenarioPresetStorage
    from pyrit.registry.scenario_suite_resolver import (
        ResolvedScenarioSuiteEntry,
        ResolvedScenarioSuiteLaunch,
        ScenarioSuiteExecutionNotFoundError,
        ScenarioSuiteLaunchConflictError,
        ScenarioSuiteLaunchResolver,
        ScenarioSuiteNotRunnableError,
        resolve_scenario_preset_run_request,
    )
    from pyrit.registry.scenario_suite_storage import ScenarioSuiteConflictError, ScenarioSuiteStorage
    from pyrit.registry.tag_query import TagQuery

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AttackRegistry": "pyrit.registry.components",
    "AttackTechniqueRegistry": "pyrit.registry.components",
    "AttackTechniqueMetadata": "pyrit.registry.components",
    "ConverterRegistry": "pyrit.registry.components",
    "ConverterMetadata": "pyrit.registry.components",
    "DefaultInstanceRegistry": "pyrit.registry.instance_registry",
    "InstanceRegistry": "pyrit.registry.instance_registry",
    "InstanceHoldingRegistry": "pyrit.registry.registry",
    "ParamBagRegistry": "pyrit.registry.registry",
    "Registry": "pyrit.registry.registry",
    "RegistryMetadata": "pyrit.registry.registry_metadata",
    "SupportsInstances": "pyrit.registry.instance_registry",
    "discover_in_directory": "pyrit.registry.discovery",
    "InitializerMetadata": "pyrit.registry.components",
    "InitializerRegistry": "pyrit.registry.components",
    "RegistryEntry": "pyrit.registry.instance_registry",
    "ScenarioMetadata": "pyrit.registry.components",
    "ScenarioPresetConflictError": "pyrit.registry.scenario_preset_storage",
    "ScenarioPresetMetadata": "pyrit.registry.components",
    "ScenarioPresetNameRegisteredError": "pyrit.registry.components",
    "ScenarioPresetRegistry": "pyrit.registry.components",
    "ScenarioPresetStorage": "pyrit.registry.scenario_preset_storage",
    "ScenarioSuiteConflictError": "pyrit.registry.scenario_suite_storage",
    "ResolvedScenarioSuiteEntry": "pyrit.registry.scenario_suite_resolver",
    "ResolvedScenarioSuiteLaunch": "pyrit.registry.scenario_suite_resolver",
    "ScenarioSuiteExecutionNotFoundError": "pyrit.registry.scenario_suite_resolver",
    "ScenarioSuiteLaunchConflictError": "pyrit.registry.scenario_suite_resolver",
    "ScenarioSuiteLaunchResolver": "pyrit.registry.scenario_suite_resolver",
    "ScenarioSuiteNotRunnableError": "pyrit.registry.scenario_suite_resolver",
    "resolve_scenario_preset_run_request": "pyrit.registry.scenario_suite_resolver",
    "ScenarioSuiteMetadata": "pyrit.registry.components",
    "ScenarioSuiteNameRegisteredError": "pyrit.registry.components",
    "ScenarioSuiteRegistry": "pyrit.registry.components",
    "ScenarioSuiteStorage": "pyrit.registry.scenario_suite_storage",
    "ScenarioRegistry": "pyrit.registry.components",
    "ScorerRegistry": "pyrit.registry.components",
    "ScorerMetadata": "pyrit.registry.components",
    "TargetRegistry": "pyrit.registry.components",
    "TargetMetadata": "pyrit.registry.components",
    "TagQuery": "pyrit.registry.tag_query",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
