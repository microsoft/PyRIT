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

Stored items must provide ``get_identifier()`` (``SupportsIdentifier``), which
``Identifiable`` components do and observation sources do structurally, so
per-instance metadata can be derived from it. This module imports no ``pyrit.backend`` code,
so it can be reused anywhere (forms, agents, attack strategies).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pyrit.models import ComponentIdentifier
    from pyrit.models.catalog import RestoreFailureReason
    from pyrit.registry.tag_query import TagQuery


class SupportsIdentifier(Protocol):
    """Anything that reports its behavioral identity, the one capability the instance container needs."""

    def get_identifier(self) -> ComponentIdentifier:
        """Return the component's identifier."""
        ...


T = TypeVar("T", bound=SupportsIdentifier)  # The type of items stored


class RegistryNameTakenError(ValueError):
    """A name is already registered, so another entry can't take it."""


@dataclass(frozen=True)
class UnavailableEntry:
    """
    A saved entry that couldn't be restored, so its name has no usable instance.

    Attributes:
        kind (str): What the entry is, such as "target".
        name (str | None): The entry's name, or None when the whole folder of saved entries of this kind
            couldn't be listed.
        reason (RestoreFailureReason): Why it couldn't be restored.
        message (str): The full explanation, for administrators.
    """

    kind: str
    name: str | None
    reason: RestoreFailureReason
    message: str


class SavedEntryUnavailableError(Exception):
    """
    A name belongs to a saved entry that couldn't be restored, so it can't be used or taken.

    Looking the name up raises this instead of finding nothing, so nothing else stands in for the entry.
    """

    def __init__(self, *, entry: UnavailableEntry, name: str) -> None:
        """
        Initialize the error for one name.

        Args:
            entry (UnavailableEntry): The saved entry, or the folder, that couldn't be restored.
            name (str): The name that was looked up or registered.
        """
        self.entry = entry
        self.name = name
        super().__init__(self.describe(with_details=True))

    def describe(self, *, with_details: bool) -> str:
        """
        Describe the error, with the full message or with a fixed explanation of the reason.

        Args:
            with_details (bool): Whether to include the full message, which can quote a server setting.

        Returns:
            str: The description.
        """
        if self.entry.name is None:
            subject = f"'{self.name}' may be a saved {self.entry.kind} that couldn't be restored"
        else:
            subject = f"The saved {self.entry.kind} '{self.name}' couldn't be restored"
        if with_details:
            return f"{subject}: {self.entry.message}"
        return f"{subject}. {self.entry.reason.explanation}"


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
    name, list, tag, and filter. Stored items must provide ``get_identifier()``.
    ``DefaultInstanceRegistry`` is the concrete default implementation; expressing
    the surface as a protocol lets callers depend on the capability rather than a
    concrete class.

    Type Parameters:
        T: The type of instances held (must provide ``get_identifier()``).
    """

    @property
    def revision(self) -> int:
        """The mutation revision of this container."""
        ...

    def register(
        self,
        instance: T,
        *,
        name: str | None = None,
        tags: dict[str, str] | list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        replace: bool = False,
    ) -> None:
        """Register a pre-configured instance, defaulting its name to the identifier's ``unique_name``."""
        ...

    def get(self, name: str) -> T | None:
        """Return the instance registered under ``name``, or None; raise if a saved entry holds the name."""
        ...

    def validate_name_available(self, name: str) -> None:
        """Raise if ``name`` is reserved or already registered."""
        ...

    def unregister(self, name: str, *, expected_entry: RegistryEntry[T] | None = None) -> T | None:
        """Remove and return ``name`` when it still refers to ``expected_entry``, if supplied."""
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

    def query_by_tags(self, *, query: TagQuery) -> list[RegistryEntry[T]]:
        """Return entries whose tag keys satisfy the composable ``TagQuery``, sorted by name."""
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
        """Return the sorted names of registered instances, without names held for saved entries."""
        ...

    def mark_unavailable(self, entry: UnavailableEntry) -> None:
        """Hold a name, or every unregistered name, for a saved entry that couldn't be restored."""
        ...

    def clear_unavailable(self, name: str) -> None:
        """Release the hold on a saved entry's name."""
        ...

    def get_unavailable_entries(self) -> list[UnavailableEntry]:
        """Return the held saved entries, sorted by name."""
        ...

    def __contains__(self, name: str) -> bool:
        """Check whether an instance name is registered or held for a saved entry."""
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
        T: The type of instances held (must provide ``get_identifier()``).
    """

    instances: InstanceRegistry[T]


class DefaultInstanceRegistry(Generic[T]):
    """
    Concrete ``InstanceRegistry`` implementation assigned to ``.instances``.

    Holds named, pre-configured instances with tags and derived metadata. It owns
    no singleton lifecycle — the registry that exposes it via ``.instances`` owns
    that.

    Type Parameters:
        T: The type of instances held (must provide ``get_identifier()``).
    """

    def __init__(
        self,
        *,
        instance_type: type[T] | Callable[[], type[T]] | None = None,
        reserved_names: set[str] | frozenset[str] | None = None,
    ) -> None:
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
            reserved_names (set[str] | frozenset[str] | None): Names that cannot be
                registered in this container.
        """
        self._registry_items: dict[str, RegistryEntry[T]] = {}
        self._lock = threading.RLock()
        self._metadata_cache: list[ComponentIdentifier] | None = None
        self._instance_version = 0
        self._instance_type: type[T] | Callable[[], type[T]] | None = instance_type
        self._reserved_names = frozenset(reserved_names or ())
        self._unavailable: dict[str, UnavailableEntry] = {}
        self._unavailable_unregistered: UnavailableEntry | None = None

    @property
    def revision(self) -> int:
        """The mutation revision, including direct registration and removal."""
        with self._lock:
            return self._instance_version

    def _resolve_instance_type(self) -> type | None:
        """
        Resolve and cache the configured expected element type, if any.

        Returns:
            type | None: The expected type, or None when no constraint is set.
        """
        with self._lock:
            if self._instance_type is None or isinstance(self._instance_type, type):
                return self._instance_type
            resolved = self._instance_type()
            self._instance_type = resolved
            return resolved

    @staticmethod
    def _normalize_tags(
        tags: dict[str, str] | list[str] | None = None,
    ) -> dict[str, str]:
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
        replace: bool = False,
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
            replace (bool): Whether to replace an existing entry with the same name.

        Raises:
            TypeError: If this registry was created with an ``instance_type`` and
                ``instance`` is not of that type.
            ValueError: If the name is reserved.
            RegistryNameTakenError: If the name is already registered and ``replace`` is False.
        """
        expected_type = self._resolve_instance_type()
        if expected_type is not None and not isinstance(instance, expected_type):
            raise TypeError(
                f"Cannot register a {type(instance).__name__!r} instance in a registry "
                f"of {expected_type.__name__!r} instances."
            )

        if name is None:
            name = instance.get_identifier().unique_name
        entry = RegistryEntry(
            name=name,
            instance=instance,
            tags=self._normalize_tags(tags),
            metadata=metadata or {},
        )
        with self._lock:
            if name in self._reserved_names:
                raise ValueError(f"Instance name '{name}' is reserved")
            self._raise_if_unavailable(name)
            if not replace:
                self.validate_name_available(name)
            self._registry_items[name] = entry
            self._metadata_cache = None
            self._instance_version += 1

    def get(self, name: str) -> T | None:
        """
        Get a registered instance by name.

        Args:
            name (str): The registry name of the instance.

        Returns:
            T | None: The instance, or None if not found.

        Raises:
            SavedEntryUnavailableError: If the name belongs to a saved entry that couldn't be restored.
        """
        entry = self.get_entry(name)
        return entry.instance if entry is not None else None

    def validate_name_available(self, name: str) -> None:
        """
        Validate that a registry name can be used.

        Args:
            name (str): The proposed registry name.

        Raises:
            ValueError: If the name is reserved.
            SavedEntryUnavailableError: If the name belongs to a saved entry that couldn't be restored.
            RegistryNameTakenError: If the name is already registered.
        """
        with self._lock:
            if name in self._reserved_names:
                raise ValueError(f"Instance name '{name}' is reserved")
            self._raise_if_unavailable(name)
            if name in self._registry_items:
                raise RegistryNameTakenError(f"Instance '{name}' already exists")

    def unregister(self, name: str, *, expected_entry: RegistryEntry[T] | None = None) -> T | None:
        """
        Remove a registered instance by name.

        Args:
            name (str): The registry name of the instance.
            expected_entry (RegistryEntry[T] | None): When supplied, remove the
                name only if it still refers to this exact registry entry.

        Returns:
            T | None: The removed instance, or None if the name is missing or now
                refers to a different entry.
        """
        with self._lock:
            entry = self._registry_items.get(name)
            if entry is None or (expected_entry is not None and entry is not expected_entry):
                return None
            del self._registry_items[name]
            self._metadata_cache = None
            self._instance_version += 1
            return entry.instance

    def get_entry(self, name: str) -> RegistryEntry[T] | None:
        """
        Get the full entry (including tags) by name.

        Args:
            name (str): The registry name of the entry.

        Returns:
            RegistryEntry[T] | None: The entry, or None if not found.

        Raises:
            SavedEntryUnavailableError: If the name belongs to a saved entry that couldn't be restored.
        """
        with self._lock:
            self._raise_if_unavailable(name)
            return self._registry_items.get(name)

    def get_all_instances(self) -> list[RegistryEntry[T]]:
        """
        Get all registered entries sorted by name, leaving out names of saved entries that couldn't be restored.

        Returns:
            list[RegistryEntry[T]]: The entries sorted by name.
        """
        with self._lock:
            return [
                self._registry_items[name] for name in sorted(self._registry_items) if name not in self._unavailable
            ]

    def get_names(self) -> list[str]:
        """
        Get a sorted list of the registered instance names, leaving out names held for saved entries.

        Returns:
            list[str]: The instance names sorted alphabetically.
        """
        with self._lock:
            return [name for name in sorted(self._registry_items) if name not in self._unavailable]

    def mark_unavailable(self, entry: UnavailableEntry) -> None:
        """
        Hold a name for a saved entry that couldn't be restored, so using or taking it fails with the reason.

        An instance registered under the same name, such as one from a startup script, is hidden until the
        hold is cleared. An entry without a name holds every name that isn't registered, because the
        saved entries of this kind couldn't be listed.

        Args:
            entry (UnavailableEntry): The saved entry, or the folder, that couldn't be restored.
        """
        with self._lock:
            if entry.name is None:
                self._unavailable_unregistered = entry
            else:
                self._unavailable[entry.name] = entry
            self._metadata_cache = None
            self._instance_version += 1

    def clear_unavailable(self, name: str) -> None:
        """
        Release the hold on a name, once its saved entry was fixed or deleted.

        Args:
            name (str): The entry's name.
        """
        with self._lock:
            if self._unavailable.pop(name, None) is not None:
                self._metadata_cache = None
                self._instance_version += 1

    def get_unavailable_entries(self) -> list[UnavailableEntry]:
        """
        List the held names of saved entries that couldn't be restored, and a held folder last.

        Returns:
            list[UnavailableEntry]: The held entries, sorted by name.
        """
        with self._lock:
            entries = [self._unavailable[name] for name in sorted(self._unavailable)]
            if self._unavailable_unregistered is not None:
                entries.append(self._unavailable_unregistered)
            return entries

    def _raise_if_unavailable(self, name: str) -> None:
        """
        Refuse a name held for a saved entry that couldn't be restored.

        Raises:
            SavedEntryUnavailableError: If the name is held.
        """
        entry = self._unavailable.get(name)
        if entry is None and name not in self._registry_items:
            entry = self._unavailable_unregistered
        if entry is not None:
            raise SavedEntryUnavailableError(entry=entry, name=name)

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
        with self._lock:
            return [
                entry
                for entry in self.get_all_instances()
                if tag in entry.tags and (value is None or entry.tags[tag] == value)
            ]

    def query_by_tags(self, *, query: TagQuery) -> list[RegistryEntry[T]]:
        """
        Get entries whose tag keys satisfy a composable ``TagQuery``.

        Where ``get_by_tag`` matches a single key (optionally a value), this
        evaluates an arbitrary AND / OR / exclude predicate built with ``TagQuery``
        (e.g. ``TagQuery.all("core") & TagQuery.any_of("fast", "cheap")``). Matching
        is on the tag *keys* only; tag values are not considered.

        Args:
            query (TagQuery): The predicate to evaluate against each entry's tag keys.

        Returns:
            list[RegistryEntry[T]]: Matching entries sorted by name.
        """
        with self._lock:
            return [entry for entry in self.get_all_instances() if query.matches(set(entry.tags))]

    def add_tags(self, *, name: str, tags: dict[str, str] | list[str]) -> None:
        """
        Add tags to an existing entry.

        Args:
            name (str): The registry name of the entry to tag.
            tags (dict[str, str] | list[str]): Tags to add.

        Raises:
            KeyError: If no entry with the given name exists.
        """
        normalized_tags = self._normalize_tags(tags)
        with self._lock:
            entry = self._registry_items.get(name)
            if entry is None:
                raise KeyError(f"No instance named '{name}' in registry.")
            entry.tags.update(normalized_tags)
            self._metadata_cache = None
            self._instance_version += 1

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
        for entry in self.get_all_instances():
            if entry.name in tagged_names:
                continue
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
        from pyrit.registry.registry import _matches_filters

        while True:
            with self._lock:
                if self._metadata_cache is not None:
                    metadata = self._metadata_cache
                    break
                version = self._instance_version
                entries = self.get_all_instances()
            built = [self._build_metadata(entry.instance) for entry in entries]
            with self._lock:
                if version != self._instance_version:
                    continue
                self._metadata_cache = built
                metadata = self._metadata_cache
                break

        if not include_filters and not exclude_filters:
            return metadata

        return [
            m for m in metadata if _matches_filters(m, include_filters=include_filters, exclude_filters=exclude_filters)
        ]

    def _build_metadata(self, instance: T) -> ComponentIdentifier:
        """
        Build metadata for an item via its ``get_identifier()``.

        Args:
            instance (T): The item.

        Returns:
            ComponentIdentifier: The item's identifier.
        """
        return instance.get_identifier()

    def __contains__(self, name: str) -> bool:
        """
        Check if an instance name is registered or held for a saved entry that couldn't be restored.

        Returns:
            bool: True if the instance name is registered or held, False otherwise.
        """
        with self._lock:
            return name in self._registry_items or name in self._unavailable

    def __len__(self) -> int:
        """
        Get the count of registered instances that can be used.

        Returns:
            int: The number of registered instances.
        """
        return len(self.get_names())

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over registered instance names.

        Returns:
            Iterator[str]: An iterator over sorted instance names.
        """
        return iter(self.get_names())
