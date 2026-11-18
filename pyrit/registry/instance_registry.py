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

An entry's metadata may name the caller principal that created it (``OWNER_METADATA_KEY``). An
entry with an owner may also be private (``PRIVATE_METADATA_KEY``): then only the caller in scope with
that principal (``pyrit.auth.get_caller_principal``) can see it. To everyone else, every lookup and
listing behaves as if it did not exist; only registering its name fails.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, runtime_checkable

from pyrit.auth import get_caller_principal
from pyrit.models.catalog import RegistryEntryOrigin

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator, Mapping

    from pyrit.models import ComponentIdentifier
    from pyrit.registry.tag_query import TagQuery


class SupportsIdentifier(Protocol):
    """Anything that reports its behavioral identity, the one capability the instance container needs."""

    def get_identifier(self) -> ComponentIdentifier:
        """Return the component's identifier."""
        ...


T = TypeVar("T", bound=SupportsIdentifier)  # The type of items stored

#: Entry metadata naming the kind of saved recipe an entry was rebuilt from, such as ``"targets"``.
SAVED_ENTRY_METADATA_KEY = "saved_recipe_kind"

#: Entry metadata marking an entry that the server's startup scripts registered, so its name is registered again
#: after a restart. Entries with neither this nor the saved marker are gone after a restart.
CONFIGURATION_ENTRY_METADATA_KEY = "configuration_entry"

#: Entry metadata naming the caller principal that created the entry. Entries from code or configuration have none.
OWNER_METADATA_KEY = "owner"

#: Entry metadata marking an entry with an owner as private: only its owner can see and use it.
PRIVATE_METADATA_KEY = "private"

#: Entry metadata marking a target whose credentials can be updated in place through the API: by its owner when
#: it is private, and by anyone signed in when it is shared. Entries without it can't be updated.
CREDENTIAL_UPDATES_METADATA_KEY = "credential_updates"

#: The refusal kind recorded when code tries to replace a name a private entry it can't see uses.
PRIVATE_ENTRY_REFUSAL_KIND = "private"

# The owners of private entries looked up in this context, while a build records them.
_private_references: ContextVar[dict[str, str] | None] = ContextVar("_private_references", default=None)

_saved_entry_refusals: ContextVar[list[tuple[str, str]] | None] = ContextVar("_saved_entry_refusals", default=None)
_registration_origin: ContextVar[RegistryEntryOrigin | None] = ContextVar("_registration_origin", default=None)
# The kind and name of the saved entry being rebuilt in this context, so registering that name marks the entry.
_saved_entry_registration: ContextVar[tuple[str, str] | None] = ContextVar("_saved_entry_registration", default=None)


class RegistryNameTakenError(ValueError):
    """A name is already registered, so another entry can't take it."""


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


def get_entry_owner(entry: RegistryEntry[Any]) -> str | None:
    """
    Return the principal that created an entry.

    Args:
        entry (RegistryEntry[Any]): The entry.

    Returns:
        str | None: The owner, or None for an entry registered by code or configuration.
    """
    owner = entry.metadata.get(OWNER_METADATA_KEY)
    return str(owner) if owner else None


def is_private_entry(entry: RegistryEntry[Any]) -> bool:
    """
    Check whether only an entry's owner can see and use it.

    Args:
        entry (RegistryEntry[Any]): The entry.

    Returns:
        bool: True if the entry has an owner and is marked private.
    """
    return bool(entry.metadata.get(PRIVATE_METADATA_KEY)) and get_entry_owner(entry) is not None


def allows_credential_updates(entry: RegistryEntry[Any]) -> bool:
    """
    Check whether an entry's credentials can be updated in place through the API.

    Args:
        entry (RegistryEntry[Any]): The entry.

    Returns:
        bool: True if whoever created the entry allowed credential updates.
    """
    return entry.metadata.get(CREDENTIAL_UPDATES_METADATA_KEY) is True


def credential_update_metadata(*, allowed: bool) -> dict[str, Any]:
    """
    Return the entry metadata that decides whether a target's credentials can be updated through the API.

    Pass it to ``register`` with the target, for example from an initializer that registers a shared target
    whose token expires: ``registry.instances.register(target, name=..., metadata=credential_update_metadata(
    allowed=True))``. Anyone signed in can then update the shared target's API key or request headers.

    Args:
        allowed (bool): Whether the target's credentials can be updated.

    Returns:
        dict[str, Any]: The metadata.
    """
    return {CREDENTIAL_UPDATES_METADATA_KEY: allowed}


def owner_metadata(owner: str | None, *, private: bool) -> dict[str, Any]:
    """
    Return the entry metadata that records who created an entry and whether it is private.

    Args:
        owner (str | None): The creating caller principal, or None for an entry without one.
        private (bool): Whether only the owner can see and use the entry. Requires an owner.

    Returns:
        dict[str, Any]: The metadata, or an empty mapping when there is no owner.

    Raises:
        ValueError: If a private entry has no owner.
    """
    if owner is None:
        if private:
            raise ValueError("A private entry needs an owner.")
        return {}
    return {OWNER_METADATA_KEY: owner, PRIVATE_METADATA_KEY: private}


def is_visible_to_caller(entry: RegistryEntry[Any]) -> bool:
    """
    Apply the access rule: a private entry is visible only to its owner; every other entry to everyone.

    The caller is the one in scope (``pyrit.auth.get_caller_principal``). Work with no caller in
    scope sees no private entries.

    Args:
        entry (RegistryEntry[Any]): The entry.

    Returns:
        bool: True if the caller in scope may see and use the entry.
    """
    return not is_private_entry(entry) or get_entry_owner(entry) == get_caller_principal()


@contextmanager
def record_private_references() -> Generator[dict[str, str], None, None]:
    """
    Collect the private entries looked up by name or identifier in this context.

    A component built from references inside the scope uses a private entry when the mapping is
    nonempty, so it must be private too (see ``creator_metadata``). The access rule hides every
    other owner's private entries, so each one recorded is the caller's own.

    Yields:
        dict[str, str]: The kind of each private entry looked up so far, such as ``"target"``, by name.
    """
    references: dict[str, str] = {}
    token = _private_references.set(references)
    try:
        yield references
    finally:
        _private_references.reset(token)


def creator_metadata(
    *, name: str, requested_private: bool | None, private_references: Mapping[str, str]
) -> dict[str, Any]:
    """
    Decide whether a component created by the caller in scope is private, and record its creator.

    A component is shared unless the request makes it private. A component built on the caller's
    private entries is always private, so a shared component never uses one. With no caller in scope,
    such as server setup, a component is shared and has no owner.

    Args:
        name (str): The new component's name, for errors.
        requested_private (bool | None): The visibility the request asked for, or None for the default.
        private_references (Mapping[str, str]): The private entries the component was built on, from
            ``record_private_references``.

    Returns:
        dict[str, Any]: The entry metadata naming the creator and whether the entry is private.

    Raises:
        ValueError: If the request shares a component built on a private entry.
        PermissionError: If the request asks for a private component and no caller is in scope.
    """
    caller = get_caller_principal()
    if private_references and requested_private is False:
        inner, kind = sorted(private_references.items())[0]
        raise ValueError(f"'{name}' is built on your private {kind} '{inner}'; it must be private too.")
    if caller is None:
        if requested_private:
            raise PermissionError(f"'{name}' can be private only for a signed-in caller who owns it.")
        return {}
    return owner_metadata(caller, private=bool(requested_private) or bool(private_references))


class SavedEntryReplacementError(RuntimeError):
    """
    Raised when code registers an entry under the name of an entry rebuilt from a saved recipe.

    It isn't a ``ValueError``, so a startup script that skips the entries it can't build doesn't
    swallow it.
    """

    def __init__(self, *, kind: str, name: str) -> None:
        """
        Describe the saved entry that would have been replaced.

        Args:
            kind (str): The saved recipe kind, such as ``"targets"``.
            name (str): The entry's name.
        """
        super().__init__(f"Saved {kind.removesuffix('s')} '{name}' can't be replaced.")
        self.kind = kind
        self.name = name


class PrivateEntryReplacementError(RuntimeError):
    """
    Raised when code replaces an entry under a name that a private entry it can't see uses.

    It isn't a ``ValueError``, so a launch script that skips the entries it can't build doesn't swallow
    it. The message doesn't say whose entry it is.
    """

    def __init__(self, *, name: str) -> None:
        """
        Describe the name that couldn't be replaced.

        Args:
            name (str): The entry's name.
        """
        super().__init__(
            f"'{name}' can't be replaced: a user's private entry uses that name until the server restarts."
        )
        self.name = name


def refuse_saved_entry(entry: RegistryEntry[Any] | None) -> None:
    """
    Refuse to replace, or stand in for, an entry rebuilt from a saved recipe.

    Args:
        entry (RegistryEntry[Any] | None): The entry registered under the name, if any.

    Raises:
        SavedEntryReplacementError: If the entry was rebuilt from a saved recipe.
    """
    kind = _record_saved_entry_refusal(entry)
    if entry is not None and kind is not None:
        raise SavedEntryReplacementError(kind=kind, name=entry.name)


def _record_saved_entry_refusal(entry: RegistryEntry[Any] | None) -> str | None:
    """
    Record a refused use of an entry rebuilt from a saved recipe, when refusals are being recorded.

    Args:
        entry (RegistryEntry[Any] | None): The entry registered under the name, if any.

    Returns:
        str | None: The entry's saved recipe kind, or None when it wasn't rebuilt from a saved recipe.
    """
    if entry is None or SAVED_ENTRY_METADATA_KEY not in entry.metadata:
        return None
    kind = str(entry.metadata[SAVED_ENTRY_METADATA_KEY])
    refusals = _saved_entry_refusals.get()
    if refusals is not None:
        refusals.append((kind, entry.name))
    return kind


def _record_private_entry_refusal(name: str) -> None:
    refusals = _saved_entry_refusals.get()
    if refusals is not None:
        refusals.append((PRIVATE_ENTRY_REFUSAL_KIND, name))


def registry_entry_origin(metadata: Mapping[str, Any]) -> RegistryEntryOrigin:
    """
    Tell where a registered entry comes from, from its metadata.

    Args:
        metadata (Mapping[str, Any]): The entry's registry metadata.

    Returns:
        RegistryEntryOrigin: Saved when rebuilt from a saved recipe, configuration when the server's startup scripts
            registered it, and temporary otherwise, such as an entry created through the API without saving it or
            one a scan's launch scripts registered.
    """
    if SAVED_ENTRY_METADATA_KEY in metadata:
        return RegistryEntryOrigin.SAVED
    if metadata.get(CONFIGURATION_ENTRY_METADATA_KEY):
        return RegistryEntryOrigin.CONFIGURATION
    return RegistryEntryOrigin.TEMPORARY


@contextmanager
def registration_origin(origin: RegistryEntryOrigin) -> Generator[None, None, None]:
    """
    Mark what code run in this context registers as coming from the server's startup scripts or a scan.

    Entries registered while the startup scripts run are marked as configuration. Classes that first
    appear while a scan's launch scripts run are temporary, since those scripts don't run at startup.

    Args:
        origin (RegistryEntryOrigin): ``CONFIGURATION`` for startup scripts, ``TEMPORARY`` for launch scripts.

    Yields:
        None: Control while the scripts run.
    """
    token = _registration_origin.set(origin)
    try:
        yield
    finally:
        _registration_origin.reset(token)


def current_registration_origin() -> RegistryEntryOrigin | None:
    """
    Return the origin set by the innermost ``registration_origin`` around the caller, if any.

    Returns:
        RegistryEntryOrigin | None: The origin, or None outside any.
    """
    return _registration_origin.get()


@contextmanager
def record_saved_entry_refusals() -> Generator[list[tuple[str, str]], None, None]:
    """
    Collect the saved entries that code run in this context tried to replace.

    The list holds every refusal, including ones the code caught itself.

    Yields:
        list[tuple[str, str]]: The kind and name of each saved entry that was refused.
    """
    refusals: list[tuple[str, str]] = []
    token = _saved_entry_refusals.set(refusals)
    try:
        yield refusals
    finally:
        _saved_entry_refusals.reset(token)


@contextmanager
def saved_entry_registration(*, kind: str, name: str) -> Generator[None, None, None]:
    """
    Mark the entry registered under ``name`` in this context as rebuilt from a saved recipe.

    Args:
        kind (str): The saved recipe kind, such as ``"targets"``.
        name (str): The saved entry's name.

    Yields:
        None: Control returns to the caller while the mark applies.
    """
    token = _saved_entry_registration.set((kind, name))
    try:
        yield
    finally:
        _saved_entry_registration.reset(token)


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
        """Return the instance registered under ``name``, or None."""
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
        """Return all entries the caller in scope may see, sorted by name."""
        ...

    def get_all_instances_of_every_owner(self) -> list[RegistryEntry[T]]:
        """Return every entry, including other callers' private ones, for internal bookkeeping only."""
        ...

    @property
    def visibility_key(self) -> str | None:
        """The owner whose private entries the caller in scope sees, or None when it sees only shared ones."""
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
        entry_kind: str = "entry",
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
            entry_kind (str): What an entry is, such as ``"target"``, for messages. Defaults to ``"entry"``.
        """
        self._registry_items: dict[str, RegistryEntry[T]] = {}
        self._lock = threading.RLock()
        self._metadata_cache: tuple[list[RegistryEntry[T]], list[ComponentIdentifier]] | None = None
        # How many private entries each owner has, so the visibility key needs no scan.
        self._private_entry_counts: dict[str, int] = {}
        self._instance_version = 0
        self._instance_type: type[T] | Callable[[], type[T]] | None = instance_type
        self._reserved_names = frozenset(reserved_names or ())
        self._entry_kind = entry_kind

    @property
    def revision(self) -> int:
        """The mutation revision, including direct registration and removal."""
        with self._lock:
            return self._instance_version

    @property
    def visibility_key(self) -> str | None:
        """
        The owner whose private entries the caller in scope sees, or None when it sees only shared ones.

        Caches of results derived from the visible entries include it, so callers never share a result
        derived from one caller's private entries.
        """
        caller = get_caller_principal()
        with self._lock:
            return caller if caller is not None and caller in self._private_entry_counts else None

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

        An entry registered while the server's startup scripts run is marked as configuration
        (see ``registration_origin``).

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
            PrivateEntryReplacementError: If ``replace`` would replace a private entry the caller in scope
                can't see.
            SavedEntryReplacementError: If ``replace`` would replace an entry rebuilt
                from a saved recipe.
        """
        expected_type = self._resolve_instance_type()
        if expected_type is not None and not isinstance(instance, expected_type):
            raise TypeError(
                f"Cannot register a {type(instance).__name__!r} instance in a registry "
                f"of {expected_type.__name__!r} instances."
            )

        if name is None:
            name = instance.get_identifier().unique_name
        entry_metadata = dict(metadata or {})
        if _registration_origin.get() is RegistryEntryOrigin.CONFIGURATION:
            entry_metadata[CONFIGURATION_ENTRY_METADATA_KEY] = True
        restoring = _saved_entry_registration.get()
        if restoring is not None and restoring[1] == name:
            entry_metadata[SAVED_ENTRY_METADATA_KEY] = restoring[0]
        entry = RegistryEntry(
            name=name,
            instance=instance,
            tags=self._normalize_tags(tags),
            metadata=entry_metadata,
        )
        with self._lock:
            if name in self._reserved_names:
                raise ValueError(f"Instance name '{name}' is reserved")
            previous = self._registry_items.get(name)
            if replace and previous is not None and not is_visible_to_caller(previous):
                _record_private_entry_refusal(name)
                raise PrivateEntryReplacementError(name=name)
            if not replace:
                self.validate_name_available(name)
            else:
                refuse_saved_entry(previous)
            if previous is not None:
                self._forget_owner(previous)
            self._registry_items[name] = entry
            owner = get_entry_owner(entry)
            if owner is not None and is_private_entry(entry):
                self._private_entry_counts[owner] = self._private_entry_counts.get(owner, 0) + 1
            self._metadata_cache = None
            self._instance_version += 1

    def _forget_owner(self, entry: RegistryEntry[T]) -> None:
        owner = get_entry_owner(entry)
        if owner is None or not is_private_entry(entry):
            return
        remaining = self._private_entry_counts.get(owner, 0) - 1
        if remaining > 0:
            self._private_entry_counts[owner] = remaining
        else:
            self._private_entry_counts.pop(owner, None)

    def get(self, name: str) -> T | None:
        """
        Get a registered instance by name.

        Args:
            name (str): The registry name of the instance.

        Returns:
            T | None: The instance, or None if not found.
        """
        entry = self.get_entry(name)
        return entry.instance if entry is not None else None

    def validate_name_available(self, name: str) -> None:
        """
        Validate that a registry name can be used.

        Args:
            name (str): The proposed registry name.

        Names are global, so a name another caller's private entry holds is taken too. The error
        doesn't say whose entry holds it.

        Raises:
            ValueError: If the name is reserved.
            RegistryNameTakenError: If the name is already registered.
        """
        with self._lock:
            if name in self._reserved_names:
                raise ValueError(f"Instance name '{name}' is reserved")
            if (previous := self._registry_items.get(name)) is not None:
                # Record the clash even if a launch script catches the generic name-taken error.
                if not is_visible_to_caller(previous):
                    _record_private_entry_refusal(name)
                else:
                    _record_saved_entry_refusal(previous)
                raise RegistryNameTakenError(f"Instance '{name}' already exists")

    def unregister(self, name: str, *, expected_entry: RegistryEntry[T] | None = None) -> T | None:
        """
        Remove a registered instance by name.

        Args:
            name (str): The registry name of the instance.
            expected_entry (RegistryEntry[T] | None): When supplied, remove the
                name only if it still refers to this exact registry entry. Holding the
                entry, such as one from ``get_all_instances_of_every_owner``, allows
                removing it whoever owns it.

        Returns:
            T | None: The removed instance, or None if the name is missing, now
                refers to a different entry, or, without ``expected_entry``, is private
                to another caller.
        """
        with self._lock:
            entry = self._registry_items.get(name)
            if entry is None or (expected_entry is not None and entry is not expected_entry):
                return None
            if expected_entry is None and not is_visible_to_caller(entry):
                return None
            del self._registry_items[name]
            self._forget_owner(entry)
            self._metadata_cache = None
            self._instance_version += 1
            return entry.instance

    def get_entry(self, name: str) -> RegistryEntry[T] | None:
        """
        Get the full entry (including tags) by name.

        Every lookup by name goes through here, and a private entry is returned only to its owner.

        Args:
            name (str): The registry name of the entry.

        Returns:
            RegistryEntry[T] | None: The entry, or None if not found or private to another caller.
        """
        with self._lock:
            entry = self._registry_items.get(name)
        if entry is None or not is_visible_to_caller(entry):
            return None
        references = _private_references.get()
        if references is not None and is_private_entry(entry):
            references[entry.name] = self._entry_kind
        return entry

    def get_all_instances(self) -> list[RegistryEntry[T]]:
        """
        Get the entries the caller in scope may see, sorted by name.

        Returns:
            list[RegistryEntry[T]]: The entries sorted by name.
        """
        return [entry for entry in self.get_all_instances_of_every_owner() if is_visible_to_caller(entry)]

    def get_all_instances_of_every_owner(self) -> list[RegistryEntry[T]]:
        """
        Get every entry, including other callers' private ones, sorted by name.

        Only for internal bookkeeping that must see every entry, such as releasing files or
        checking names for conflicts. Never use it to find a component for a caller.

        Returns:
            list[RegistryEntry[T]]: The entries sorted by name.
        """
        with self._lock:
            return [self._registry_items[name] for name in sorted(self._registry_items)]

    def get_names(self) -> list[str]:
        """
        Get a sorted list of the instance names the caller in scope may see.

        Returns:
            list[str]: The instance names sorted alphabetically.
        """
        return [entry.name for entry in self.get_all_instances()]

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
            if entry is None or not is_visible_to_caller(entry):
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
        List metadata for the instances the caller in scope can see, optionally filtered.

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
                    cached = self._metadata_cache
                    break
                version = self._instance_version
                entries = self.get_all_instances_of_every_owner()
            built = (entries, [self._build_metadata(entry.instance) for entry in entries])
            with self._lock:
                if version != self._instance_version:
                    continue
                self._metadata_cache = built
                cached = self._metadata_cache
                break
        cached_entries, cached_metadata = cached
        visible = [is_visible_to_caller(entry) for entry in cached_entries]
        metadata = (
            cached_metadata
            if all(visible)
            else [identifier for identifier, shown in zip(cached_metadata, visible, strict=True) if shown]
        )

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
        Check if an instance name is registered.

        Returns:
            bool: True if the caller in scope can see an instance with this name, False otherwise.
        """
        with self._lock:
            entry = self._registry_items.get(name)
        return entry is not None and is_visible_to_caller(entry)

    def __len__(self) -> int:
        """
        Get the count of instances the caller in scope can see.

        Returns:
            int: The number of visible instances.
        """
        return len(self.get_all_instances())

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over registered instance names.

        Returns:
            Iterator[str]: An iterator over sorted instance names.
        """
        return iter(self.get_names())
