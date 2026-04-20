# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Base instance registry for PyRIT.

This module provides the abstract base class for registries that store
pre-configured instances (not classes). Unlike class registries which
store Type[T] and create instances on demand, instance registries store
already-instantiated objects.

Examples include:
- ScorerRegistry: stores Scorer instances configured with their chat_target
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Generic, Iterator, List, Optional, TypeVar

from pyrit.identifiers import Identifier
from pyrit.registry.base import RegistryProtocol

T = TypeVar("T")  # The type of instances stored
MetadataT = TypeVar("MetadataT", bound=Identifier)


class BaseInstanceRegistry(ABC, RegistryProtocol[MetadataT], Generic[T, MetadataT]):
    """
    Abstract base class for registries that store pre-configured instances.

    This class implements RegistryProtocol. Unlike BaseClassRegistry which stores
    Type[T] and supports lazy discovery, instance registries store already-instantiated
    objects that are registered explicitly (typically during initialization).

    Type Parameters:
        T: The type of instances stored in the registry.
        MetadataT: A TypedDict subclass for instance metadata.

    Subclasses must implement:
        - _build_metadata(): Convert an instance to its metadata representation
    """

    # Class-level singleton instances, keyed by registry class
    _instances: Dict[type, "BaseInstanceRegistry[Any, Any]"] = {}

    @classmethod
    def get_registry_singleton(cls) -> "BaseInstanceRegistry[T, MetadataT]":
        """
        Get the singleton instance of this registry.

        Creates the instance on first call with default parameters.

        Returns:
            The singleton instance of this registry class.
        """
        if cls not in cls._instances:
            cls._instances[cls] = cls()
        return cls._instances[cls]

    @classmethod
    def reset_instance(cls) -> None:
        """
        Reset the singleton instance.

        Useful for testing or reinitializing the registry.
        """
        if cls in cls._instances:
            del cls._instances[cls]

    def __init__(self) -> None:
        """Initialize the instance registry."""
        # Maps registry names to registered items
        self._registry_items: Dict[str, T] = {}
        self._metadata_cache: Optional[List[MetadataT]] = None

    def register(
        self,
        instance: T,
        *,
        name: str,
    ) -> None:
        """
        Register an instance.

        Args:
            instance: The pre-configured instance to register.
            name: The registry name for this instance.
        """
        self._registry_items[name] = instance
        self._metadata_cache = None

    def get(self, name: str) -> Optional[T]:
        """
        Get a registered instance by name.

        Args:
            name: The registry name of the instance.

        Returns:
            The instance, or None if not found.
        """
        return self._registry_items.get(name)

    def get_names(self) -> List[str]:
        """
        Get a sorted list of all registered names.

        Returns:
            Sorted list of registry names (keys).
        """
        return sorted(self._registry_items.keys())

    def list_metadata(
        self,
        *,
        include_filters: Optional[Dict[str, object]] = None,
        exclude_filters: Optional[Dict[str, object]] = None,
    ) -> List[MetadataT]:
        """
        List metadata for all registered instances, optionally filtered.

        Supports filtering on any metadata property:
        - Simple types (str, int, bool): exact match
        - List types: checks if filter value is in the list

        Args:
            include_filters: Optional dict of filters that items must match.
                Keys are metadata property names, values are the filter criteria.
                All filters must match (AND logic).
            exclude_filters: Optional dict of filters that items must NOT match.
                Keys are metadata property names, values are the filter criteria.
                Any matching filter excludes the item.

        Returns:
            List of metadata dictionaries describing each registered instance.
        """
        from pyrit.registry.base import _matches_filters

        if self._metadata_cache is None:
            items = []
            for name in sorted(self._registry_items.keys()):
                instance = self._registry_items[name]
                items.append(self._build_metadata(name, instance))
            self._metadata_cache = items

        if not include_filters and not exclude_filters:
            return self._metadata_cache

        return [
            m
            for m in self._metadata_cache
            if _matches_filters(m, include_filters=include_filters, exclude_filters=exclude_filters)
        ]

    @abstractmethod
    def _build_metadata(self, name: str, instance: T) -> MetadataT:
        """
        Build metadata for an instance.

        Args:
            name: The registry name of the instance.
            instance: The instance.

        Returns:
            A metadata dictionary describing the instance.
        """
        ...

    def __contains__(self, name: str) -> bool:
        """
        Check if a name is registered.

        Returns:
            True if the name is registered, False otherwise.
        """
        return name in self._registry_items

    def __len__(self) -> int:
        """
        Get the count of registered instances.

        Returns:
            The number of registered instances.
        """
        return len(self._registry_items)

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over registered names.

        Returns:
            An iterator over sorted registered names.
        """
        return iter(sorted(self._registry_items.keys()))
