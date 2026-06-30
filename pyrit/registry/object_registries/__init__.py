# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Object registries package.

This package contains the legacy instance-only registry stack still used by
``AttackTechniqueRegistry``. Component registries that hold pre-configured
instances (converters, scorers, targets) now live in ``registry/components/`` as
``Registry`` subclasses that expose their instances via the ``.instances``
property.
"""

from pyrit.registry.object_registries.base_instance_registry import (
    BaseInstanceRegistry,
    RegistryEntry,
)

__all__ = [
    # Base classes
    "BaseInstanceRegistry",
    "RegistryEntry",
]
