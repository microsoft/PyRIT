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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Optional, TypeVar, Union

from pyrit.identifiers import ComponentIdentifier
from pyrit.registry.base import RegistryProtocol

if TYPE_CHECKING:
    from collections.abc import Iterator

T = TypeVar("T")  # The type of instances stored
MetadataT = TypeVar("MetadataT", bound=ComponentIdentifier)


@dataclass
class RegistryEntry(Generic[T]):
    """
    A wrapper around a registered instance, holding its name, tags, and the instance itself.

    Tags are always stored as ``dict[str, str]``. When callers pass a plain
    ``list[str]``, each string is normalized to a key with an empty-string value.

    Attributes:
        name: The registry name for this entry.
        instance: The registered object.
        tags: Key-value tags for categorization and filtering.
    """

    name: str
    instance: T
    tags: dict[str, str] = field(default_factory=dict)


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
    _instances: dict[type, BaseInstanceRegistry[Any, Any]] = {}

    @classmethod
    def get_registry_singleton(cls) -> BaseInstanceRegistry[T, MetadataT]:
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

    @staticmethod
    def _normalize_tags(tags: Optional[Union[dict[str, str], list[str]]] = None) -> dict[str, str]:
        """
        Normalize tags into a ``dict[str, str]``.

        Args:
            tags: Tags as a dict, a list of string keys (values default to ``""``),
                or ``None`` (returns empty dict).

        Returns:
            A ``dict[str, str]`` of normalised tags.
        """
        if tags is None:
            return {}
        if isinstance(tags, list):
            return dict.fromkeys(tags, "")
        return dict(tags)

    def __init__(self) -> None:
        """Initialize the instance registry."""
        # Maps registry names to registry entries
        self._registry_items: dict[str, RegistryEntry[T]] = {}
        self._metadata_cache: Optional[list[MetadataT]] = None

    def register(
        self,
        instance: T,
        *,
        name: str,
        tags: Optional[Union[dict[str, str], list[str]]] = None,
    ) -> None:
        """
        Register an instance.

        Args:
            instance: The pre-configured instance to register.
            name: The registry name for this instance.
            tags: Optional tags for categorisation. Accepts a ``dict[str, str]``
                or a ``list[str]`` (each string becomes a key with value ``""``).
        """
        normalized = self._normalize_tags(tags)
        self._registry_items[name] = RegistryEntry(name=name, instance=instance, tags=normalized)
        self._metadata_cache = None

    def get(self, name: str) -> Optional[T]:
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

    def get_entry(self, name: str) -> Optional[RegistryEntry[T]]:
        """
        Get a full registry entry by name, including tags.

        Args:
            name: The registry name of the entry.

        Returns:
            The RegistryEntry, or None if not found.
        """
        return self._registry_items.get(name)

    def get_names(self) -> list[str]:
        """
        Get a sorted list of all registered names.

        Returns:
            Sorted list of registry names (keys).
        """
        return sorted(self._registry_items.keys())

    def get_all_instances(self) -> list[RegistryEntry[T]]:
        """
        Get all registered entries sorted by name.

        Returns:
            List of RegistryEntry objects sorted by name.
        """
        return [self._registry_items[name] for name in sorted(self._registry_items.keys())]

    def get_by_tag(
        self,
        *,
        tag: str,
        value: Optional[str] = None,
    ) -> list[RegistryEntry[T]]:
        """
        Get all entries that have a given tag, optionally matching a specific value.

        Args:
            tag: The tag key to match.
            value: If provided, only entries whose tag value equals this are returned.
                If ``None``, any entry that has the tag key is returned regardless of value.

        Returns:
            List of matching RegistryEntry objects sorted by name.
        """
        results: list[RegistryEntry[T]] = []
        for name in sorted(self._registry_items.keys()):
            entry = self._registry_items[name]
            if tag in entry.tags and (value is None or entry.tags[tag] == value):
                results.append(entry)
        return results

    def add_tags(
        self,
        *,
        name: str,
        tags: Union[dict[str, str], list[str]],
    ) -> None:
        """
        Add tags to an existing registry entry.

        Args:
            name: The registry name of the entry to tag.
            tags: Tags to add. Accepts a ``dict[str, str]``
                or a ``list[str]`` (each string becomes a key with value ``""``).

        Raises:
            KeyError: If no entry with the given name exists.
        """
        entry = self._registry_items.get(name)
        if entry is None:
            raise KeyError(f"No entry named '{name}' in registry.")
        entry.tags.update(self._normalize_tags(tags))
        self._metadata_cache = None

    def find_dependents_of_tag(self, *, tag: str) -> list[RegistryEntry[T]]:
        """
        Find entries whose children depend on entries with the given tag.

        Scans each registry entry's ``ComponentIdentifier`` tree and checks
        whether any child's ``eval_hash`` matches the ``eval_hash`` of an
        entry that carries *tag*.  Entries that themselves carry *tag* are
        excluded from the results.

        This enables automatic dependency detection: for example, tagging
        base refusal scorers with ``"refusal"`` lets you discover all
        wrapper scorers (inverters, composites) that embed a refusal scorer
        without any explicit ``depends_on`` declaration.

        Args:
            tag: The tag key that identifies the "base" entries.

        Returns:
            List of ``RegistryEntry`` objects that depend on tagged entries,
            sorted by name.
        """
        # Collect eval_hashes of all tagged entries
        tagged_hashes: set[str] = set()
        tagged_names: set[str] = set()
        for entry in self.get_by_tag(tag=tag):
            tagged_names.add(entry.name)
            identifier = self._build_metadata(entry.name, entry.instance)
            if identifier.eval_hash:
                tagged_hashes.add(identifier.eval_hash)

        if not tagged_hashes:
            return []

        # Find non-tagged entries whose children reference a tagged eval_hash
        dependents: list[RegistryEntry[T]] = []
        for name in sorted(self._registry_items.keys()):
            if name in tagged_names:
                continue
            entry = self._registry_items[name]
            identifier = self._build_metadata(name, entry.instance)
            child_hashes = identifier._collect_child_eval_hashes()
            if child_hashes & tagged_hashes:
                dependents.append(entry)
        return dependents

    def list_metadata(
        self,
        *,
        include_filters: Optional[dict[str, object]] = None,
        exclude_filters: Optional[dict[str, object]] = None,
    ) -> list[MetadataT]:
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
                entry = self._registry_items[name]
                items.append(self._build_metadata(name, entry.instance))
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
