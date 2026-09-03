# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Registry module for PyRIT class and object registries."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.registry.components import (
        AttackTechniqueMetadata,
        AttackTechniqueRegistry,
        ConverterMetadata,
        ConverterRegistry,
        InitializerMetadata,
        InitializerRegistry,
        ScenarioMetadata,
        ScenarioRegistry,
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
    from pyrit.registry.registry import ParamBagRegistry, Registry
    from pyrit.registry.registry_metadata import RegistryMetadata
    from pyrit.registry.tag_query import TagQuery

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AttackTechniqueRegistry": "pyrit.registry.components",
    "AttackTechniqueMetadata": "pyrit.registry.components",
    "ConverterRegistry": "pyrit.registry.components",
    "ConverterMetadata": "pyrit.registry.components",
    "DefaultInstanceRegistry": "pyrit.registry.instance_registry",
    "InstanceRegistry": "pyrit.registry.instance_registry",
    "ParamBagRegistry": "pyrit.registry.registry",
    "Registry": "pyrit.registry.registry",
    "RegistryMetadata": "pyrit.registry.registry_metadata",
    "SupportsInstances": "pyrit.registry.instance_registry",
    "discover_in_directory": "pyrit.registry.discovery",
    "InitializerMetadata": "pyrit.registry.components",
    "InitializerRegistry": "pyrit.registry.components",
    "RegistryEntry": "pyrit.registry.instance_registry",
    "ScenarioMetadata": "pyrit.registry.components",
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
