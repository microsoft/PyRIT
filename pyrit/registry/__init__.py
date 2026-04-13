# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Registry module for PyRIT class and instance registries."""

from pyrit.registry.base import RegistryProtocol
from pyrit.registry.class_registries import (
    BaseClassRegistry,
    ClassEntry,
    InitializerMetadata,
    InitializerRegistry,
    ScenarioMetadata,
    ScenarioRegistry,
)
from pyrit.registry.discovery import (
    discover_in_directory,
    discover_in_package,
    discover_subclasses_in_loaded_modules,
)
from pyrit.registry.instance_registries import (
    BaseInstanceRegistry,
    RegistryEntry,
    ScorerRegistry,
    TargetRegistry,
)

__all__ = [
    "BaseClassRegistry",
    "BaseInstanceRegistry",
    "ClassEntry",
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
    "TargetRegistry",
]
