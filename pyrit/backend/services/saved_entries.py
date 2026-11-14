# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Coordinate saved registry entries in the API, around the registry library that saves them.

Saving, changing, and deleting saved entries hold one lock, so a delete can't miss a save that names the entry it
deletes, and an edit can't race another change.
"""

import asyncio
import weakref
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from pyrit.models.catalog import ComponentRecipe, RegistryRecipeKind, TechniqueRecipe
from pyrit.registry import (
    AttackTechniqueRegistry,
    ConverterRegistry,
    SavedEntryReplacement,
    ScorerRegistry,
    TargetRegistry,
    save_then_register_async,
)

_SAVED_ENTRY_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()

REGISTRIES_BY_KIND: Mapping[RegistryRecipeKind, Any] = {
    RegistryRecipeKind.TARGETS: TargetRegistry,
    RegistryRecipeKind.CONVERTERS: ConverterRegistry,
    RegistryRecipeKind.SCORERS: ScorerRegistry,
    RegistryRecipeKind.TECHNIQUES: AttackTechniqueRegistry,
}

# Whether the running task holds the lock, so it can't wait on it a second time.
_HOLDING_SAVED_ENTRY_LOCK: ContextVar[bool] = ContextVar("_holding_saved_entry_lock", default=False)


class SavedEntryRemovalError(RuntimeError):
    """A saved entry can't be removed through the API yet, since it comes back from its saved file at every start."""


def saved_entry_lock() -> asyncio.Lock:
    """
    Return the lock that saving and deleting saved entries hold, so a delete can't miss a save that names the entry.

    Returns:
        asyncio.Lock: The lock for the running event loop.
    """
    loop = asyncio.get_running_loop()
    lock = _SAVED_ENTRY_LOCKS.get(loop)
    if lock is None:
        lock = _SAVED_ENTRY_LOCKS[loop] = asyncio.Lock()
    return lock


@asynccontextmanager
async def hold_saved_entry_lock_async() -> AsyncGenerator[None, None]:
    """
    Hold the lock that saving, changing, and deleting saved entries take.

    Yields:
        None: While the lock is held.

    Raises:
        RuntimeError: If the running task already holds it, which would otherwise wait forever.
    """
    if _HOLDING_SAVED_ENTRY_LOCK.get():
        raise RuntimeError("This task already holds the saved entry lock, so it can't take it again.")
    async with saved_entry_lock():
        token = _HOLDING_SAVED_ENTRY_LOCK.set(True)
        try:
            yield
        finally:
            _HOLDING_SAVED_ENTRY_LOCK.reset(token)


async def save_then_register_locked_async(
    *,
    kind: RegistryRecipeKind,
    name: str,
    recipe: ComponentRecipe | TechniqueRecipe,
    register: Callable[[dict[str, Any]], object],
    source: str | None,
    replacing: SavedEntryReplacement | None = None,
) -> None:
    """
    Save an entry through the registry library while holding the lock deleting takes.

    An edit already holds the lock through ``hold_saved_entry_lock_async()``, so a replacement is saved under it.

    Args:
        kind (RegistryRecipeKind): The kind of entry.
        name (str): The entry's name.
        recipe (ComponentRecipe | TechniqueRecipe): How to rebuild the entry, naming who saved it.
        register (Callable[[dict[str, Any]], object]): Registers the built entry with the metadata it's given.
        source (str | None): Where saved entries are stored, or None for the default folder.
        replacing (SavedEntryReplacement | None): The saved entry an edit rebuilds, whose recipe is overwritten
            instead, or None to save a new entry. Defaults to None.

    Raises:
        RuntimeError: If an edit replaces a saved entry without holding the lock.
        SavedEntryConflictError: If a saved or registered entry of the kind already has the name, an entry the
            recipe names was deleted, or the recipe an edit replaces changed since it was read.
        SavedEntryStorageError: If the recipe can't be written, or can't be deleted or put back after registering
            failed.
    """
    if replacing is not None:
        if not _HOLDING_SAVED_ENTRY_LOCK.get():
            raise RuntimeError("An edit must hold the saved entry lock while it replaces a saved entry.")
        await save_then_register_async(
            kind=kind, name=name, recipe=recipe, register=register, source=source, replacing=replacing
        )
        return
    async with hold_saved_entry_lock_async():
        await save_then_register_async(kind=kind, name=name, recipe=recipe, register=register, source=source)
