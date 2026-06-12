# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Container registry base for PyRIT.

``ContainerRegistry`` extends ``BuildableRegistry`` with an instance container:
in addition to discovering classes and building instances (the buildable layer),
it holds named, pre-configured instances that callers register and retrieve.
This is the base for domains that are both buildable *and* hold instances
(converters, targets, scorers).

The container is the registry's primary identity: the protocol surface
(``get_names``, ``__contains__``, ``__len__``, ``__iter__``, ``list_metadata``)
refers to **instances**. The class catalog is reached through the explicitly
named buildable methods (``get_class``, ``get_class_names``,
``list_class_metadata``, ``create_instance``). This keeps name-based resolution
consistent across every container registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pyrit.models import ComponentIdentifier, Identifiable
from pyrit.registry.buildable_registry import BuildableRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyrit.registry.object_registries.base_instance_registry import RegistryEntry

T = TypeVar("T", bound=Identifiable)
MetadataT = TypeVar("MetadataT")


class ContainerRegistry(BuildableRegistry[T, MetadataT], Generic[T, MetadataT]):
    """
    Registry base that is buildable *and* holds named instances.

    Adds an instance container on top of ``BuildableRegistry``: register
    pre-configured instances, retrieve them by name, list and tag them. Stored
    instances must implement ``Identifiable`` so instance metadata can be derived
    from ``get_identifier()``.

    The container is primary: ``get_names``/``__contains__``/``__len__``/
    ``__iter__``/``list_metadata`` operate on instances. The class catalog is
    accessed via the buildable methods inherited from ``BuildableRegistry``.

    Type Parameters:
        T: The type of instances held (must be ``Identifiable``).
        MetadataT: The class-catalog metadata type.
    """

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry.

        Args:
            lazy_discovery (bool): If True, class discovery is deferred until first
                access. If False, discovery runs immediately.
        """
        super().__init__(lazy_discovery=lazy_discovery)
        self._instance_entries: dict[str, RegistryEntry[T]] = {}
        self._instance_metadata_cache: list[ComponentIdentifier] | None = None

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

    def register_instance(
        self,
        instance: T,
        *,
        name: str | None = None,
        tags: dict[str, str] | list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Register a pre-configured instance in the container.

        Args:
            instance (T): The instance to register.
            name (str | None): The registry name. Defaults to the instance's
                identifier ``unique_name``.
            tags (dict[str, str] | list[str] | None): Optional tags for
                categorization.
            metadata (dict[str, Any] | None): Optional per-entry metadata.
        """
        if name is None:
            name = instance.get_identifier().unique_name

        from pyrit.registry.object_registries.base_instance_registry import RegistryEntry

        self._instance_entries[name] = RegistryEntry(
            name=name,
            instance=instance,
            tags=self._normalize_tags(tags),
            metadata=metadata or {},
        )
        self._instance_metadata_cache = None

    def get_instance_by_name(self, name: str) -> T | None:
        """
        Get a registered instance by name.

        Args:
            name (str): The registry name of the instance.

        Returns:
            T | None: The instance, or None if not found.
        """
        entry = self._instance_entries.get(name)
        return entry.instance if entry is not None else None

    def get_instance_entry(self, name: str) -> RegistryEntry[T] | None:
        """
        Get the full instance entry (including tags) by name.

        Args:
            name (str): The registry name of the entry.

        Returns:
            RegistryEntry[T] | None: The entry, or None if not found.
        """
        return self._instance_entries.get(name)

    def get_all_instances(self) -> list[RegistryEntry[T]]:
        """
        Get all registered instance entries sorted by name.

        Returns:
            list[RegistryEntry[T]]: The instance entries sorted by name.
        """
        return [self._instance_entries[name] for name in sorted(self._instance_entries.keys())]

    def get_by_tag(self, *, tag: str, value: str | None = None) -> list[RegistryEntry[T]]:
        """
        Get instance entries that carry a given tag, optionally matching a value.

        Args:
            tag (str): The tag key to match.
            value (str | None): If provided, only entries whose tag value equals
                this are returned. If None, any entry with the tag key matches.

        Returns:
            list[RegistryEntry[T]]: Matching entries sorted by name.
        """
        results: list[RegistryEntry[T]] = []
        for name in sorted(self._instance_entries.keys()):
            entry = self._instance_entries[name]
            if tag in entry.tags and (value is None or entry.tags[tag] == value):
                results.append(entry)
        return results

    def add_tags(self, *, name: str, tags: dict[str, str] | list[str]) -> None:
        """
        Add tags to an existing instance entry.

        Args:
            name (str): The registry name of the entry to tag.
            tags (dict[str, str] | list[str]): Tags to add.

        Raises:
            KeyError: If no entry with the given name exists.
        """
        entry = self._instance_entries.get(name)
        if entry is None:
            raise KeyError(f"No instance named '{name}' in registry.")
        entry.tags.update(self._normalize_tags(tags))
        self._instance_metadata_cache = None

    def list_instance_metadata(
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

        if self._instance_metadata_cache is None:
            self._instance_metadata_cache = [
                self._instance_entries[name].instance.get_identifier() for name in sorted(self._instance_entries.keys())
            ]

        if not include_filters and not exclude_filters:
            return self._instance_metadata_cache

        return [
            m
            for m in self._instance_metadata_cache
            if _matches_filters(m, include_filters=include_filters, exclude_filters=exclude_filters)
        ]

    # ------------------------------------------------------------------
    # Protocol surface — operates on the instance container (primary identity)
    # ------------------------------------------------------------------

    def get_names(self) -> list[str]:
        """
        Get a sorted list of all registered instance names.

        Returns:
            list[str]: The instance names sorted alphabetically.
        """
        return sorted(self._instance_entries.keys())

    def list_metadata(  # type: ignore[ty:invalid-method-override]
        self,
        *,
        include_filters: dict[str, object] | None = None,
        exclude_filters: dict[str, object] | None = None,
    ) -> list[ComponentIdentifier]:
        """
        List instance metadata (the container is the primary identity).

        Intentionally narrows the return type to instance ``ComponentIdentifier``
        metadata: on a container registry the protocol surface refers to
        instances. Class-catalog metadata is available via ``list_class_metadata``.

        Args:
            include_filters (dict[str, object] | None): Filters items must match.
            exclude_filters (dict[str, object] | None): Filters items must not match.

        Returns:
            list[ComponentIdentifier]: The identifier metadata for each instance.
        """
        return self.list_instance_metadata(include_filters=include_filters, exclude_filters=exclude_filters)

    def __contains__(self, name: str) -> bool:
        """
        Check if an instance name is registered.

        Returns:
            bool: True if the instance name is registered, False otherwise.
        """
        return name in self._instance_entries

    def __len__(self) -> int:
        """
        Get the count of registered instances.

        Returns:
            int: The number of registered instances.
        """
        return len(self._instance_entries)

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over registered instance names.

        Returns:
            Iterator[str]: An iterator over sorted instance names.
        """
        return iter(sorted(self._instance_entries.keys()))
