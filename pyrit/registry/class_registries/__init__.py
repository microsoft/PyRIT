# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Class registries package.

This package contains registries that store classes (Type[T]) which can be
instantiated on demand. Examples include ScenarioRegistry and InitializerRegistry.

For registries that store pre-configured instances, see instance_registries/.
"""

from pyrit.registry.class_registries.base_class_registry import (
    BaseClassRegistry,
    ClassEntry,
)
from pyrit.registry.class_registries.initializer_registry import (
    InitializerMetadata,
    InitializerRegistry,
)
from pyrit.registry.class_registries.scenario_registry import (
    ScenarioMetadata,
    ScenarioRegistry,
)

__all__ = [
    "BaseClassRegistry",
    "ClassEntry",
    "ScenarioRegistry",
    "ScenarioMetadata",
    "InitializerRegistry",
    "InitializerMetadata",
]
