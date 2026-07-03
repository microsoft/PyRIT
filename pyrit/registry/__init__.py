# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Registry module for PyRIT class and object registries."""

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
from pyrit.registry.registry import Registry
from pyrit.registry.registry_metadata import RegistryMetadata
from pyrit.registry.tag_query import TagQuery

__all__ = [
    "AttackTechniqueRegistry",
    "AttackTechniqueMetadata",
    "ConverterRegistry",
    "ConverterMetadata",
    "DefaultInstanceRegistry",
    "InstanceRegistry",
    "Registry",
    "RegistryMetadata",
    "SupportsInstances",
    "discover_in_directory",
    "InitializerMetadata",
    "InitializerRegistry",
    "RegistryEntry",
    "ScenarioMetadata",
    "ScenarioRegistry",
    "ScorerRegistry",
    "ScorerMetadata",
    "TargetRegistry",
    "TargetMetadata",
    "TagQuery",
]
