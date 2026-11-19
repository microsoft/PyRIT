# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Look up and delete saved registry entries through their stored files.

Saved entries are shared, so only an administrator may delete one, as for scenario presets and suites. The person
who saved an entry is kept in its file as attribution only. An entry that another saved entry names, or that a
registered entry holds, isn't deleted.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pyrit.backend.models.saved_entries import SavedEntryResponse
from pyrit.backend.services.component_lifecycle import release_component_async
from pyrit.backend.services.saved_entries import REGISTRIES_BY_KIND, saved_entry_lock
from pyrit.models import ComponentIdentifier, Identifiable
from pyrit.models.catalog import (
    RegistryEntryOrigin,
    RegistryRecipeKind,
    RestoreFailure,
    SavedEntryRef,
    StoredRecipe,
)
from pyrit.registry import SavedEntryStorageError, is_visible_to_caller
from pyrit.registry.file_document_storage import DocumentConflictError, DocumentStorageUnavailableError
from pyrit.registry.instance_registry import registry_entry_origin
from pyrit.registry.registry_recipe_restore import RegistryRecipeRestorer
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage

if TYPE_CHECKING:
    from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
    from pyrit.registry import RegistryEntry

logger = logging.getLogger(__name__)

# Deep enough for an attack's converter and scoring settings, which hold lists of configurations.
_MAX_SETTINGS_DEPTH = 8
# Requests such as a list refresh finish quickly, so a delete waits briefly for them before saying it's busy.
_IDLE_WAIT_SECONDS = 2.0
_IDLE_CHECK_SECONDS = 0.05
_ORIGIN_WORDS = {
    RegistryEntryOrigin.SAVED: "saved",
    RegistryEntryOrigin.CONFIGURATION: "startup",
    RegistryEntryOrigin.TEMPORARY: "temporary",
}


class SavedEntryNotFoundError(LookupError):
    """No saved entry of the kind has the name."""


class SavedEntryForbiddenError(PermissionError):
    """The caller isn't an administrator, so may not delete the saved entry."""


class SavedEntryVersionRequiredError(ValueError):
    """A delete didn't say which version of the saved file it read."""


class SavedEntryInUseError(ValueError):
    """The saved entry changed since it was read, or something still uses it."""


class SavedEntryService:
    """Look up and delete saved targets, converters, scorers, and techniques."""

    async def get_saved_entry_async(
        self, *, kind: RegistryRecipeKind, name: str, recipes_source: str | None, is_admin: bool
    ) -> SavedEntryResponse:
        """
        Describe a saved entry's file: its version, and whether the caller may change it.

        Args:
            kind (RegistryRecipeKind): The kind of entry.
            name (str): The entry's name.
            recipes_source (str | None): Where saved entries are stored, or None for the default folder.
            is_admin (bool): Whether the caller is an administrator.

        Returns:
            SavedEntryResponse: The file's description.

        Raises:
            SavedEntryNotFoundError: If no saved entry of the kind has the name.
            SavedEntryStorageError: If storage can't be read.
        """
        stored = await _read_async(
            storage=RegistryRecipeStorage(kind=kind, source=recipes_source), kind=kind, name=name
        )
        return SavedEntryResponse(
            kind=kind,
            name=name,
            version=stored.version,
            problem=stored.reason if isinstance(stored, RestoreFailure) else None,
            can_change=is_admin,
        )

    async def delete_saved_entry_async(
        self,
        *,
        kind: RegistryRecipeKind,
        name: str,
        expected_version: str | None,
        recipes_source: str | None,
        is_admin: bool,
        runtime: RuntimeLifecycle | None,
    ) -> None:
        """
        Delete a saved entry's file and remove the entry it restored, so it doesn't come back.

        The running entry is removed before its file is deleted, so no request can pick it up in between, and is
        put back if the file can't be deleted. A startup entry that has the same name stays. Deleting an entry that
        couldn't be restored takes it off the runtime's list of restore failures.

        Args:
            kind (RegistryRecipeKind): The kind of entry.
            name (str): The entry's name.
            expected_version (str | None): The file version the caller read. Required unless the file can't be read.
            recipes_source (str | None): Where saved entries are stored, or None for the default folder.
            is_admin (bool): Whether the caller is an administrator, which deleting takes.
            runtime (RuntimeLifecycle | None): The runtime, to check that nothing is using the entry and to stop
                listing it among the entries that couldn't be restored.

        Raises:
            SavedEntryNotFoundError: If no saved entry of the kind has the name.
            SavedEntryForbiddenError: If the caller isn't an administrator.
            SavedEntryVersionRequiredError: If ``expected_version`` is missing for a file that can be read.
            SavedEntryInUseError: If the file changed since it was read, another saved entry names it, a registered
                entry holds it, or a request, run, or send is in progress.
            SavedEntryStorageError: If storage can't be read or written.
        """
        singular = _singular(kind)
        storage = RegistryRecipeStorage(kind=kind, source=recipes_source)
        async with saved_entry_lock():
            stored = await _read_async(storage=storage, kind=kind, name=name)
            if not is_admin:
                raise SavedEntryForbiddenError(f"Only an administrator can delete saved {singular} '{name}'.")
            if stored.version is not None:
                if expected_version is None:
                    raise SavedEntryVersionRequiredError(
                        f"Send the version of saved {singular} '{name}' you read as expected_version."
                    )
                if expected_version != stored.version:
                    raise SavedEntryInUseError(_changed_message(singular=singular, name=name))
            users = await _saved_users_async(kind=kind, name=name, recipes_source=recipes_source)
            if users:
                raise SavedEntryInUseError(_in_use_message(singular=singular, name=name, users=users))
            instances = REGISTRIES_BY_KIND[kind].get_registry_singleton().instances
            entry = instances.get_entry(name)
            if entry is not None and registry_entry_origin(entry.metadata) is not RegistryEntryOrigin.SAVED:
                entry = None
            if entry is not None:
                _refuse_held(kind=kind, entry=entry)
                if runtime is not None and not await _wait_until_idle_async(runtime):
                    raise SavedEntryInUseError(
                        f"Saved {singular} '{name}' could be in use by a request, run, or send in progress. "
                        "Try again when it finishes."
                    )
                # A request that finished during the wait may have registered an entry that holds this one.
                _refuse_held(kind=kind, entry=entry)
                instances.unregister(name, expected_entry=entry)
            try:
                await asyncio.to_thread(storage.delete_recipe, name, expected_version=stored.version)
            except DocumentConflictError as error:
                _put_back(kind=kind, entry=entry)
                raise SavedEntryInUseError(_changed_message(singular=singular, name=name)) from error
            except DocumentStorageUnavailableError as error:
                _put_back(kind=kind, entry=entry)
                logger.error(
                    "Saved %s '%s' couldn't be deleted from %s (%s).",
                    singular,
                    name,
                    storage.display_source,
                    error.reason,
                )
                raise SavedEntryStorageError(f"Saved {kind.value} can't be deleted right now.") from error
            if entry is not None and not _registered_anywhere(entry.instance):
                await release_component_async(entry.instance)
            if runtime is not None:
                runtime.forget_restore_failure(SavedEntryRef(kind=kind, name=name))


async def _wait_until_idle_async(runtime: RuntimeLifecycle) -> bool:
    """
    Wait briefly for requests in flight to finish.

    Nothing is awaited after the last check, so the caller can act on the answer before any other request starts.

    Returns:
        bool: Whether nothing could be using a registered entry. Runs, sends, and estimates usually outlast the wait.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _IDLE_WAIT_SECONDS
    while runtime.has_active_work():
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(_IDLE_CHECK_SECONDS)
    return True


async def _read_async(
    *, storage: RegistryRecipeStorage, kind: RegistryRecipeKind, name: str
) -> StoredRecipe | RestoreFailure:
    """
    Read one saved entry's file off the event loop.

    Returns:
        StoredRecipe | RestoreFailure: The recipe, or why its file can't be used.

    Raises:
        SavedEntryNotFoundError: If no saved entry of the kind has the name.
        SavedEntryStorageError: If storage can't be read.
    """
    try:
        stored = await asyncio.to_thread(storage.read_recipe, name)
    except DocumentStorageUnavailableError as error:
        logger.error(
            "Saved %s '%s' couldn't be read from %s (%s).",
            _singular(kind),
            name,
            storage.display_source,
            error.reason,
        )
        raise SavedEntryStorageError(f"Saved {kind.value} can't be read right now.") from error
    if stored is None:
        raise SavedEntryNotFoundError(f"No saved {_singular(kind)} is named '{name}'.")
    return stored


async def _saved_users_async(*, kind: RegistryRecipeKind, name: str, recipes_source: str | None) -> list[str]:
    """
    List the other saved entries whose files name this one, as restoring them would read them.

    Returns:
        list[str]: Each one described as, for example, "saved technique 'formal_crescendo'".

    Raises:
        SavedEntryStorageError: If storage can't be read.
    """
    ref = SavedEntryRef(kind=kind, name=name)
    restorer = RegistryRecipeRestorer(source=recipes_source)

    def scan() -> list[str]:
        users = []
        for other_kind in RegistryRecipeKind:
            listing = RegistryRecipeStorage(kind=other_kind, source=recipes_source).list_recipes()
            for other_name, other in sorted(listing.recipes.items()):
                if (other_kind, other_name) == (kind, name):
                    continue
                if ref in restorer.recipe_references(kind=other_kind, recipe=other.recipe):
                    users.append(f"saved {_singular(other_kind)} '{other_name}'")
        return users

    try:
        return await asyncio.to_thread(scan)
    except DocumentStorageUnavailableError as error:
        logger.error("Saved entries couldn't be read to check what uses '%s' (%s).", name, error.reason)
        raise SavedEntryStorageError("Saved entries can't be read right now.") from error


def _holders(*, kind: RegistryRecipeKind, entry: RegistryEntry[Any]) -> list[str]:
    """
    List the registered entries that hold this one, whatever their origin.

    An entry holds another when that one appears in its configuration, such as a converter's target or a
    technique's adversarial target. An entry with the same configuration counts too, since the two can't be told
    apart.

    Returns:
        list[str]: Each holder described as, for example, "temporary converter 'tone'".
    """
    if kind is RegistryRecipeKind.TECHNIQUES:
        return []
    held_hash = entry.instance.get_identifier().hash
    holders = []
    for other_kind, registry in REGISTRIES_BY_KIND.items():
        # Another user's private entry holds it too, but its name isn't shown.
        for other in registry.get_registry_singleton().instances.get_all_instances_of_every_owner():
            if (other_kind, other.name) == (kind, entry.name):
                continue
            if held_hash in _held_hashes(other.instance):
                if is_visible_to_caller(other):
                    origin = _ORIGIN_WORDS[registry_entry_origin(other.metadata)]
                    holders.append(f"{origin} {_singular(other_kind)} '{other.name}'")
                else:
                    holders.append(f"another user's private {_singular(other_kind)}")
    return holders


def _refuse_held(*, kind: RegistryRecipeKind, entry: RegistryEntry[Any]) -> None:
    """
    Refuse to remove an entry that a registered entry holds.

    Raises:
        SavedEntryInUseError: If a registered entry holds it.
    """
    holders = _holders(kind=kind, entry=entry)
    if holders:
        raise SavedEntryInUseError(_in_use_message(singular=_singular(kind), name=entry.name, users=holders))


def _held_hashes(instance: object) -> set[str]:
    """
    Collect the identifier hashes of the components a registered entry holds.

    Returns:
        set[str]: The hashes of its children for a target, converter, or scorer, and of the components in its
            settings for a technique.
    """
    # A technique's identifier doesn't list the components it holds, so its settings are walked instead.
    get_settings = getattr(instance, "get_effective_settings", None)
    if callable(get_settings):
        return _hashes_in(get_settings(), depth=0)
    if isinstance(instance, Identifiable):
        return _child_hashes(instance.get_identifier())
    return set()


def _hashes_in(value: object, *, depth: int) -> set[str]:
    """
    Collect the identifier hashes of the components in a technique's settings.

    Returns:
        set[str]: The hashes of each component found and of its children.
    """
    if isinstance(value, Identifiable):
        identifier = value.get_identifier()
        return {identifier.hash, *_child_hashes(identifier)}
    if depth >= _MAX_SETTINGS_DEPTH:
        return set()
    if isinstance(value, Mapping):
        items: list[object] = list(value.values())
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        items = [getattr(value, field.name) for field in dataclasses.fields(value)]
    else:
        return set()
    hashes: set[str] = set()
    for item in items:
        hashes |= _hashes_in(item, depth=depth + 1)
    return hashes


def _child_hashes(identifier: ComponentIdentifier) -> set[str]:
    """
    Collect the hashes of every component below an identifier.

    Returns:
        set[str]: The descendants' hashes.
    """
    hashes: set[str] = set()
    for child in identifier.children.values():
        for item in child if isinstance(child, list) else [child]:
            hashes.add(item.hash)
            hashes |= _child_hashes(item)
    return hashes


def _put_back(*, kind: RegistryRecipeKind, entry: RegistryEntry[Any] | None) -> None:
    """Register an entry again after its file couldn't be deleted."""
    if entry is not None:
        REGISTRIES_BY_KIND[kind].get_registry_singleton().instances.register(
            entry.instance, name=entry.name, tags=entry.tags, metadata=entry.metadata
        )


def _registered_anywhere(instance: object) -> bool:
    """
    Return whether an object is still registered under some name, so its resources must stay open.

    Returns:
        bool: Whether any registry holds the same object.
    """
    return any(
        other.instance is instance
        for registry in REGISTRIES_BY_KIND.values()
        for other in registry.get_registry_singleton().instances.get_all_instances_of_every_owner()
    )


def _singular(kind: RegistryRecipeKind) -> str:
    return kind.value.removesuffix("s")


def _changed_message(*, singular: str, name: str) -> str:
    return f"Saved {singular} '{name}' changed since it was read. Look at it again before deleting it."


def _in_use_message(*, singular: str, name: str, users: list[str]) -> str:
    them = "it" if len(users) == 1 else "them"
    message = f"Saved {singular} '{name}' is used by {', '.join(users)}. Delete or change {them} first."
    if any(user.startswith("temporary ") for user in users):
        message += " A temporary entry is gone after the backend restarts or is reinitialized."
    return message
