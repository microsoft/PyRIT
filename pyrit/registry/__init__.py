# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Registry module for PyRIT class and object registries."""

from pyrit.registry.base import RegistryProtocol
from pyrit.registry.buildable_registry import BuildableRegistry
from pyrit.registry.class_registries import (
    BaseClassRegistry,
    ClassEntry,
    InitializerMetadata,
    InitializerRegistry,
    ScenarioMetadata,
    ScenarioParameterMetadata,
    ScenarioRegistry,
)
from pyrit.registry.components import (
    ConverterMetadata,
    ConverterRegistry,
)
from pyrit.registry.discovery import (
    discover_in_directory,
    discover_in_package,
    discover_subclasses_in_loaded_modules,
)
from pyrit.registry.instance_registry import (
    DefaultInstanceRegistry,
    InstanceRegistry,
    SupportsInstances,
)
from pyrit.registry.object_registries import (
    AttackTechniqueRegistry,
    BaseInstanceRegistry,
    RegistryEntry,
    RetrievableInstanceRegistry,
    ScorerRegistry,
    TargetRegistry,
)
from pyrit.registry.tag_query import TagQuery

__all__ = [
    "AttackTechniqueRegistry",
    "BaseClassRegistry",
    "BaseInstanceRegistry",
    "BuildableRegistry",
    "ConverterMetadata",
    "ConverterRegistry",
    "DefaultInstanceRegistry",
    "InstanceRegistry",
    "RetrievableInstanceRegistry",
    "SupportsInstances",
    "ClassEntry",
    "discover_in_directory",
    "discover_in_package",
    "discover_subclasses_in_loaded_modules",
    "InitializerMetadata",
    "InitializerRegistry",
    "RegistryEntry",
    "RegistryProtocol",
    "ScenarioMetadata",
    "ScenarioParameterMetadata",
    "ScenarioRegistry",
    "ScorerRegistry",
    "TargetRegistry",
    "TagQuery",
]
