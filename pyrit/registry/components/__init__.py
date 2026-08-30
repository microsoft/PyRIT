# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Component registries package.

This package contains registries for PyRIT components (objects identified by a
``ComponentIdentifier``, such as converters, scorers, and targets). A component
registry is a ``Registry`` class catalog that can build instances from
classes and, when it retains pre-configured instances, also exposes them via an
``.instances`` property.

Shared capabilities and base classes (``Registry``, ``InstanceRegistry``,
``DefaultInstanceRegistry``) live at the top level of ``pyrit.registry``.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.registry.components.attack_technique_registry import AttackTechniqueMetadata, AttackTechniqueRegistry
    from pyrit.registry.components.converter_registry import ConverterMetadata, ConverterRegistry
    from pyrit.registry.components.initializer_registry import InitializerMetadata, InitializerRegistry
    from pyrit.registry.components.scenario_registry import ScenarioMetadata, ScenarioRegistry
    from pyrit.registry.components.scorer_registry import ScorerMetadata, ScorerRegistry
    from pyrit.registry.components.target_registry import TargetMetadata, TargetRegistry

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AttackTechniqueRegistry": "pyrit.registry.components.attack_technique_registry",
    "AttackTechniqueMetadata": "pyrit.registry.components.attack_technique_registry",
    "ConverterRegistry": "pyrit.registry.components.converter_registry",
    "ConverterMetadata": "pyrit.registry.components.converter_registry",
    "InitializerRegistry": "pyrit.registry.components.initializer_registry",
    "InitializerMetadata": "pyrit.registry.components.initializer_registry",
    "ScorerRegistry": "pyrit.registry.components.scorer_registry",
    "ScorerMetadata": "pyrit.registry.components.scorer_registry",
    "ScenarioRegistry": "pyrit.registry.components.scenario_registry",
    "ScenarioMetadata": "pyrit.registry.components.scenario_registry",
    "TargetRegistry": "pyrit.registry.components.target_registry",
    "TargetMetadata": "pyrit.registry.components.target_registry",
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
