# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Retrievable instance registry for PyRIT.

.. note::

    **Legacy stack — do not build new registries on this.** New component
    registries subclass ``BuildableRegistry`` and retain instances via the
    ``.instances`` property (``DefaultInstanceRegistry``), which already
    provides ``get``/``get_entry``/``get_all_instances``. See
    ``ConverterRegistry`` for the target shape. This class remains only for the
    not-yet-migrated ``ScorerRegistry`` and ``TargetRegistry`` and is removed
    once they migrate.

This module provides ``RetrievableInstanceRegistry``, which extends
``BaseInstanceRegistry`` with ``get()``, ``get_entry()``, and
``get_all_instances()`` for registries where callers retrieve stored
objects directly (e.g., ``ScorerRegistry``, ``TargetRegistry``).

For the shared base class, see ``base_instance_registry``.
For registries that store classes (type[T]), see ``class_registries/``.
"""

from __future__ import annotations

from pyrit.registry.object_registries.base_instance_registry import (
    BaseInstanceRegistry,
    RegistryEntry,
    T,
)

# Re-export so existing ``from retrievable_instance_registry import ...`` still works
__all__ = ["RetrievableInstanceRegistry", "BaseInstanceRegistry", "RegistryEntry"]


class RetrievableInstanceRegistry(BaseInstanceRegistry[T]):
    """
    Base class for registries that store directly-retrievable instances.

    .. note::

        **Legacy — do not subclass for new registries.** Use
        ``BuildableRegistry`` + the ``.instances`` property
        (``DefaultInstanceRegistry``), which already exposes
        ``get``/``get_entry``/``get_all_instances``. Retained only for the
        not-yet-migrated ``ScorerRegistry`` and ``TargetRegistry``.

    Extends ``BaseInstanceRegistry`` with ``get()``, ``get_entry()``, and
    ``get_all_instances()`` for registries where callers retrieve the
    stored objects directly (e.g., scorers, converters, targets).

    For registries that store factories or other non-retrievable items,
    subclass ``BaseInstanceRegistry`` directly instead.

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
