# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Registry module for PyRIT class and object registries."""

from pyrit.registry.base import RegistryProtocol
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
from pyrit.registry.discovery import (
    discover_in_directory,
    discover_in_package,
    discover_subclasses_in_loaded_modules,
)
from pyrit.registry.instance_registry import (
    DefaultInstanceRegistry,
    InstanceRegistry,
    RegistryEntry,
    SupportsInstances,
)
from pyrit.registry.registry import Registry
from pyrit.registry.tag_query import TagQuery

__all__ = [
    "AttackTechniqueRegistry",
    "AttackTechniqueMetadata",
    "ConverterRegistry",
    "ConverterMetadata",
    "DefaultInstanceRegistry",
    "InstanceRegistry",
    "Registry",
    "SupportsInstances",
    "discover_in_directory",
    "discover_in_package",
    "discover_subclasses_in_loaded_modules",
    "InitializerMetadata",
    "InitializerRegistry",
    "RegistryEntry",
    "RegistryProtocol",
    "ScenarioMetadata",
    "ScenarioRegistry",
    "ScorerRegistry",
    "ScorerMetadata",
    "TargetRegistry",
    "TargetMetadata",
    "TagQuery",
]
