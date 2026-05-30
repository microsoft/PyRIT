# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Object registries package.

This package contains registries that store pre-configured instances (not classes).
Examples include ScorerRegistry which stores Scorer instances that have been
initialized with their required parameters (e.g., chat_target).

For registries that store classes (Type[T]), see class_registries/.
"""

from pyrit.registry.object_registries.attack_technique_registry import (
    AttackTechniqueRegistry,
)
from pyrit.registry.object_registries.base_instance_registry import (
    BaseInstanceRegistry,
    RegistryEntry,
)
from pyrit.registry.object_registries.converter_registry import (
    ConverterRegistry,
)
from pyrit.registry.object_registries.retrievable_instance_registry import (
    RetrievableInstanceRegistry,
)
from pyrit.registry.object_registries.scorer_registry import (
    ScorerRegistry,
)
from pyrit.registry.object_registries.target_registry import (
    TargetRegistry,
)

__all__ = [
    # Base classes
    "BaseInstanceRegistry",
    "RetrievableInstanceRegistry",
    "RegistryEntry",
    # Concrete registries
    "AttackTechniqueRegistry",
    "ConverterRegistry",
    "ScorerRegistry",
    "TargetRegistry",
]
