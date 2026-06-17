# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Instance-registry capability for PyRIT registries.

A registry that retains pre-configured, named instances exposes that capability
as an ``.instances`` property whose type is the ``InstanceRegistry`` protocol.
The concrete default implementation is ``DefaultInstanceRegistry``.

Modelling instance-holding as a typed property (rather than a base class) makes
the capability visible in the type: a function can accept "a registry that holds
instances" (``SupportsInstances``) or just "an instance registry"
(``InstanceRegistry[T]``) without depending on a concrete class, and a registry
that does not hold instances simply has no ``.instances`` attribute.

Stored items must implement ``Identifiable`` so per-instance metadata can be
derived from ``get_identifier()``. This module imports no ``pyrit.backend`` code,
so it can be reused anywhere (forms, agents, attack strategies).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, runtime_checkable

from pyrit.models import ComponentIdentifier, Identifiable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

T = TypeVar("T", bound=Identifiable)  # The type of items stored


@dataclass
class RegistryEntry(Generic[T]):
    """
    A wrapper around a registered item, holding its name, tags, and the item itself.

    Tags are always stored as ``dict[str, str]``. When callers pass a plain
    ``list[str]``, each string is normalized to a key with an empty-string value.

    Attributes:
        name (str): The registry name for this entry.
        instance (T): The registered object.
        tags (dict[str, str]): Key-value tags for categorization and filtering.
        metadata (dict[str, Any]): Arbitrary key-value metadata for capability flags
            and other per-entry data that should not pollute the tag namespace.
    """

    name: str
    instance: T
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class InstanceRegistry(Protocol[T]):
    """
    Typed instance-container capability a registry exposes as ``.instances``.

    Holds named, pre-configured instances that callers register and retrieve by
    name, list, tag, and filter. Stored items must implement ``Identifiable``.
    ``DefaultInstanceRegistry`` is the concrete default implementation; expressing
    the surface as a protocol lets callers depend on the capability rather than a
    concrete class.

    Type Parameters:
        T: The type of instances held (must be ``Identifiable``).
    """

    def register(
        self,
        instance: T,
        *,
        name: str | None = None,
        tags: dict[str, str] | list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a pre-configured instance, defaulting its name to the identifier's ``unique_name``."""
        ...

    def get(self, name: str) -> T | None:
        """Return the instance registered under ``name``, or None."""
        ...

    def get_entry(self, name: str) -> RegistryEntry[T] | None:
        """Return the full entry (including tags) for ``name``, or None."""
        ...

    def get_all_instances(self) -> list[RegistryEntry[T]]:
        """Return all entries sorted by name."""
        ...

    def get_by_tag(self, *, tag: str, value: str | None = None) -> list[RegistryEntry[T]]:
        """Return entries carrying ``tag`` (optionally matching ``value``), sorted by name."""
        ...

    def add_tags(self, *, name: str, tags: dict[str, str] | list[str]) -> None:
        """Add tags to an existing entry."""
        ...

    def find_dependents_of_tag(self, *, tag: str) -> list[RegistryEntry[T]]:
        """Return entries whose identifier tree references a tagged entry's ``eval_hash``."""
        ...

    def list_metadata(
        self,
        *,
        include_filters: dict[str, object] | None = None,
        exclude_filters: dict[str, object] | None = None,
    ) -> list[ComponentIdentifier]:
        """List per-instance identifier metadata, optionally filtered."""
        ...

    def get_names(self) -> list[str]:
        """Return the sorted names of registered instances."""
        ...

    def __contains__(self, name: str) -> bool:
        """Check whether an instance name is registered."""
        ...

    def __len__(self) -> int:
        """Return the number of registered instances."""
        ...

    def __iter__(self) -> Iterator[str]:
        """Iterate over registered instance names."""
        ...


class SupportsInstances(Protocol[T]):
    """
    Structural marker for a registry that holds instances.

    Lets callers and type-checkers express "a registry that holds instances"
    without naming a concrete class, so a registry's capabilities are legible
    from its type.

    .. note::
        Introduced with the Phase 1 foundation but not yet consumed in the codebase.
        Its first real callers arrive when the target and scorer registries migrate
        onto ``.instances`` (Phase 4) and functions begin accepting
        "any registry that holds instances" structurally. It ships now so the typed
        capability is part of the foundation rather than a later additive change.

    Type Parameters:
        T: The type of instances held (must be ``Identifiable``).
    """

    instances: InstanceRegistry[T]


class DefaultInstanceRegistry(Generic[T]):
    """
    Concrete ``InstanceRegistry`` implementation assigned to ``.instances``.

    Holds named, pre-configured instances with tags and derived metadata. It owns
    no singleton lifecycle — the registry that exposes it via ``.instances`` owns
    that.

    Type Parameters:
        T: The type of instances held (must be ``Identifiable``).
    """

    def __init__(self, *, instance_type: type[T] | Callable[[], type[T]] | None = None) -> None:
        """
        Initialize an empty instance container.

        Args:
            instance_type (type[T] | Callable[[], type[T]] | None): Optional expected
                element type. When set, ``register`` raises ``TypeError`` for any
                instance that is not of this type, so a registry scoped to one
                component kind (e.g. a target registry) cannot silently hold a
                different kind (e.g. a scorer). May be the class itself or a
                zero-argument callable returning it; the callable form lets owners
                defer importing the type so a registry's lazy discovery is preserved.
                It is resolved once, on the first ``register`` call, and cached.
        """
        self._registry_items: dict[str, RegistryEntry[T]] = {}
        self._metadata_cache: list[ComponentIdentifier] | None = None
        self._instance_type: type[T] | Callable[[], type[T]] | None = instance_type

    def _resolve_instance_type(self) -> type | None:
        """
        Resolve and cache the configured expected element type, if any.

        Returns:
            type | None: The expected type, or None when no constraint is set.
        """
        if self._instance_type is None or isinstance(self._instance_type, type):
            return self._instance_type
        resolved = self._instance_type()
        self._instance_type = resolved
        return resolved

    @staticmethod
    def _normalize_tags(tags: dict[str, str] | list[str] | None = None) -> dict[str, str]:
        """
        Normalize tags into a ``dict[str, str]``.

        Args:
            tags (dict[str, str] | list[str] | None): Tags as a dict, a list of
                string keys (values default to ``""``), or None (empty dict).

        Returns:
            dict[str, str]: The normalized tags.
        """
        if tags is None:
            return {}
        if isinstance(tags, list):
            return dict.fromkeys(tags, "")
        return dict(tags)

    def register(
        self,
        instance: T,
        *,
        name: str | None = None,
        tags: dict[str, str] | list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Register a pre-configured instance.

        Args:
            instance (T): The instance to register.
            name (str | None): The registry name. Defaults to the instance's
                identifier ``unique_name``.
            tags (dict[str, str] | list[str] | None): Optional tags for
                categorization.
            metadata (dict[str, Any] | None): Optional per-entry metadata.

        Raises:
            TypeError: If this registry was created with an ``instance_type`` and
                ``instance`` is not of that type.
        """
        expected_type = self._resolve_instance_type()
        if expected_type is not None and not isinstance(instance, expected_type):
            raise TypeError(
                f"Cannot register a {type(instance).__name__!r} instance in a registry "
                f"of {expected_type.__name__!r} instances."
            )

        if name is None:
            name = instance.get_identifier().unique_name

        self._registry_items[name] = RegistryEntry(
            name=name,
            instance=instance,
            tags=self._normalize_tags(tags),
            metadata=metadata or {},
        )
        self._metadata_cache = None

    def get(self, name: str) -> T | None:
        """
        Get a registered instance by name.

        Args:
            name (str): The registry name of the instance.

        Returns:
            T | None: The instance, or None if not found.
        """
        entry = self._registry_items.get(name)
        return entry.instance if entry is not None else None

    def get_entry(self, name: str) -> RegistryEntry[T] | None:
        """
        Get the full entry (including tags) by name.

        Args:
            name (str): The registry name of the entry.

        Returns:
            RegistryEntry[T] | None: The entry, or None if not found.
        """
        return self._registry_items.get(name)

    def get_all_instances(self) -> list[RegistryEntry[T]]:
        """
        Get all registered entries sorted by name.

        Returns:
            list[RegistryEntry[T]]: The entries sorted by name.
        """
        return [self._registry_items[name] for name in sorted(self._registry_items.keys())]

    def get_names(self) -> list[str]:
        """
        Get a sorted list of all registered instance names.

        Returns:
            list[str]: The instance names sorted alphabetically.
        """
        return sorted(self._registry_items.keys())

    def get_by_tag(self, *, tag: str, value: str | None = None) -> list[RegistryEntry[T]]:
        """
        Get entries that carry a given tag, optionally matching a value.

        Args:
            tag (str): The tag key to match.
            value (str | None): If provided, only entries whose tag value equals
                this are returned. If None, any entry with the tag key matches.

        Returns:
            list[RegistryEntry[T]]: Matching entries sorted by name.
        """
        results: list[RegistryEntry[T]] = []
        for name in sorted(self._registry_items.keys()):
            entry = self._registry_items[name]
            if tag in entry.tags and (value is None or entry.tags[tag] == value):
                results.append(entry)
        return results

    def add_tags(self, *, name: str, tags: dict[str, str] | list[str]) -> None:
        """
        Add tags to an existing entry.

        Args:
            name (str): The registry name of the entry to tag.
            tags (dict[str, str] | list[str]): Tags to add.

        Raises:
            KeyError: If no entry with the given name exists.
        """
        entry = self._registry_items.get(name)
        if entry is None:
            raise KeyError(f"No instance named '{name}' in registry.")
        entry.tags.update(self._normalize_tags(tags))
        self._metadata_cache = None

    def find_dependents_of_tag(self, *, tag: str) -> list[RegistryEntry[T]]:
        """
        Find entries whose children depend on entries with the given tag.

        Scans each entry's ``ComponentIdentifier`` tree and checks whether any
        child's ``eval_hash`` matches the ``eval_hash`` of an entry that carries
        ``tag``. Entries that themselves carry ``tag`` are excluded.

        This enables automatic dependency detection: for example, tagging base
        refusal scorers with ``"refusal"`` lets you discover all wrapper scorers
        (inverters, composites) that embed a refusal scorer without any explicit
        ``depends_on`` declaration.

        Args:
            tag (str): The tag key that identifies the "base" entries.

        Returns:
            list[RegistryEntry[T]]: Entries that depend on tagged entries, sorted
            by name.
        """
        tagged_hashes: set[str] = set()
        tagged_names: set[str] = set()
        for entry in self.get_by_tag(tag=tag):
            tagged_names.add(entry.name)
            identifier = self._build_metadata(entry.instance)
            if identifier.eval_hash:
                tagged_hashes.add(identifier.eval_hash)

        if not tagged_hashes:
            return []

        dependents: list[RegistryEntry[T]] = []
        for name in sorted(self._registry_items.keys()):
            if name in tagged_names:
                continue
            entry = self._registry_items[name]
            identifier = self._build_metadata(entry.instance)
            child_hashes = identifier._collect_child_eval_hashes()
            if child_hashes & tagged_hashes:
                dependents.append(entry)
        return dependents

    def list_metadata(
        self,
        *,
        include_filters: dict[str, object] | None = None,
        exclude_filters: dict[str, object] | None = None,
    ) -> list[ComponentIdentifier]:
        """
        List metadata for all registered instances, optionally filtered.

        Args:
            include_filters (dict[str, object] | None): Filters items must match.
            exclude_filters (dict[str, object] | None): Filters items must not match.

        Returns:
            list[ComponentIdentifier]: The identifier metadata for each instance.
        """
        from pyrit.registry.base import _matches_filters

        if self._metadata_cache is None:
            self._metadata_cache = [
                self._build_metadata(self._registry_items[name].instance)
                for name in sorted(self._registry_items.keys())
            ]

        if not include_filters and not exclude_filters:
            return self._metadata_cache

        return [
            m
            for m in self._metadata_cache
            if _matches_filters(m, include_filters=include_filters, exclude_filters=exclude_filters)
        ]

    def _build_metadata(self, instance: T) -> ComponentIdentifier:
        """
        Build metadata for an item via its ``Identifiable`` interface.

        Args:
            instance (T): The item.

        Returns:
            ComponentIdentifier: The item's identifier.
        """
        return instance.get_identifier()

    def __contains__(self, name: str) -> bool:
        """
        Check if an instance name is registered.

        Returns:
            bool: True if the instance name is registered, False otherwise.
        """
        return name in self._registry_items

    def __len__(self) -> int:
        """
        Get the count of registered instances.

        Returns:
            int: The number of registered instances.
        """
        return len(self._registry_items)

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over registered instance names.

        Returns:
            Iterator[str]: An iterator over sorted instance names.
        """
        return iter(sorted(self._registry_items.keys()))
