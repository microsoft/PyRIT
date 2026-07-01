# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Class registries package.

This package contains registries that store classes (type[T]) which can be
instantiated on demand. Examples include ScenarioRegistry and InitializerRegistry.

For registries that store pre-configured instances, see object_registries/.
"""

from pyrit.registry.class_registries.base_class_registry import (
    BaseClassRegistry,
    ClassEntry,
)
from pyrit.registry.class_registries.initializer_registry import (
    InitializerMetadata,
    InitializerRegistry,
)

__all__ = [
    "BaseClassRegistry",
    "ClassEntry",
    "InitializerRegistry",
    "InitializerMetadata",
]
