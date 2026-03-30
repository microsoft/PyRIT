# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Instance registries package.

This package contains registries that store pre-configured instances (not classes).
Examples include ScorerRegistry which stores Scorer instances that have been
initialized with their required parameters (e.g., chat_target).

For registries that store classes (Type[T]), see class_registries/.
"""

from pyrit.registry.instance_registries.base_instance_registry import (
    BaseInstanceRegistry,
    RegistryEntry,
)
from pyrit.registry.instance_registries.converter_registry import (
    ConverterRegistry,
)
from pyrit.registry.instance_registries.scorer_registry import (
    ScorerRegistry,
)
from pyrit.registry.instance_registries.target_registry import (
    TargetRegistry,
)

__all__ = [
    # Base class
    "BaseInstanceRegistry",
    "RegistryEntry",
    # Concrete registries
    "ConverterRegistry",
    "ScorerRegistry",
    "TargetRegistry",
]
