# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Instance registry for PyRIT.

This module provides ``BaseInstanceRegistry``, which extends
``BaseItemRegistry`` with ``get()``, ``get_entry()``, and
``get_all_instances()`` for registries where callers retrieve stored
objects directly (e.g., ``ScorerRegistry``, ``ConverterRegistry``,
``TargetRegistry``).

For the shared base class, see ``base_item_registry``.
For registries that store classes (Type[T]), see ``class_registries/``.
"""

from __future__ import annotations

from pyrit.registry.instance_registries.base_item_registry import (
    BaseItemRegistry,
    RegistryEntry,
    T,
)

# Re-export so existing ``from base_instance_registry import ...`` still works
__all__ = ["BaseInstanceRegistry", "BaseItemRegistry", "RegistryEntry"]


class BaseInstanceRegistry(BaseItemRegistry[T]):
    """
    Base class for registries that store directly-retrievable instances.

    Extends ``BaseItemRegistry`` with ``get()``, ``get_entry()``, and
    ``get_all_instances()`` for registries where callers retrieve the
    stored objects directly (e.g., scorers, converters, targets).

    For registries that store factories or other non-retrievable items,
    subclass ``BaseItemRegistry`` directly instead.

    Type Parameters:
        T: The type of instances stored in the registry (must be Identifiable).
    """

    def get(self, name: str) -> T | None:
        """
        Get a registered instance by name.

        Args:
            name: The registry name of the instance.

        Returns:
            The instance, or None if not found.
        """
        entry = self._registry_items.get(name)
        if entry is None:
            return None
        return entry.instance

    def get_entry(self, name: str) -> RegistryEntry[T] | None:
        """
        Get a full registry entry by name, including tags.

        Args:
            name: The registry name of the entry.

        Returns:
            The RegistryEntry, or None if not found.
        """
        return self._registry_items.get(name)

    def get_all_instances(self) -> list[RegistryEntry[T]]:
        """
        Get all registered entries sorted by name.

        Returns:
            List of RegistryEntry objects sorted by name.
        """
        return [self._registry_items[name] for name in sorted(self._registry_items.keys())]
