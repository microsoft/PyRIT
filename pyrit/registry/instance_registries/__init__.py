# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Instance registries package.

This package contains registries that store pre-configured instances (not classes).
Examples include ScorerRegistry which stores Scorer instances that have been
initialized with their required parameters (e.g., chat_target).

For registries that store classes (Type[T]), see class_registries/.
"""

from pyrit.registry.instance_registries.attack_technique_registry import (
    AttackTechniqueRegistry,
)
from pyrit.registry.instance_registries.base_instance_registry import (
    BaseInstanceRegistry,
)
from pyrit.registry.instance_registries.base_item_registry import (
    BaseItemRegistry,
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
    # Base classes
    "BaseInstanceRegistry",
    "BaseItemRegistry",
    "RegistryEntry",
    # Concrete registries
    "AttackTechniqueRegistry",
    "ConverterRegistry",
    "ScorerRegistry",
    "TargetRegistry",
]
