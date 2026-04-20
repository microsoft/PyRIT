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
)
from pyrit.registry.instance_registries.scorer_registry import (
    ScorerRegistry,
)

__all__ = [
    # Base class
    "BaseInstanceRegistry",
    # Concrete registries
    "ScorerRegistry",
]
