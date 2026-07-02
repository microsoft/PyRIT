# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Class registries package.

This package contains the transitional ``BaseClassRegistry`` base that predates
the unified ``Registry``. It survives only until the remaining domains migrate;
new registries should extend ``pyrit.registry.registry.Registry`` instead.

For registries that store pre-configured instances, see object_registries/.
"""

from pyrit.registry.class_registries.base_class_registry import (
    BaseClassRegistry,
    ClassEntry,
)

__all__ = [
    "BaseClassRegistry",
    "ClassEntry",
]
