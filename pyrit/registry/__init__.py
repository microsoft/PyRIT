# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Registry module for PyRIT class and instance registries."""

from pyrit.identifiers import Identifier, class_name_to_snake_case, snake_case_to_class_name
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
    ScorerRegistry,
)

__all__ = [
    "BaseClassRegistry",
    "BaseInstanceRegistry",
    "ClassEntry",
    "class_name_to_snake_case",
    "discover_in_directory",
    "discover_in_package",
    "discover_subclasses_in_loaded_modules",
    "Identifier",
    "InitializerMetadata",
    "InitializerRegistry",
    "RegistryProtocol",
    "snake_case_to_class_name",
    "ScenarioMetadata",
    "ScenarioRegistry",
    "ScorerRegistry",
]
