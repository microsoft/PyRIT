# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Coordinate saved registry entries in the API, around the registry library that saves them.

Saving and deleting saved entries hold one lock, so a delete can't miss a save that names the entry it deletes.
"""

import asyncio
import weakref
from collections.abc import Callable, Mapping
from typing import Any

from pyrit.models.catalog import ComponentRecipe, RegistryRecipeKind, TechniqueRecipe
from pyrit.registry import (
    AttackTechniqueRegistry,
    ConverterRegistry,
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


async def save_then_register_locked_async(
    *,
    kind: RegistryRecipeKind,
    name: str,
    recipe: ComponentRecipe | TechniqueRecipe,
    register: Callable[[dict[str, Any]], object],
    source: str | None,
) -> None:
    """
    Save an entry through the registry library while holding the lock deleting takes.

    Args:
        kind (RegistryRecipeKind): The kind of entry.
        name (str): The entry's name.
        recipe (ComponentRecipe | TechniqueRecipe): How to rebuild the entry, naming who saved it.
        register (Callable[[dict[str, Any]], object]): Registers the built entry with the metadata it's given.
        source (str | None): Where saved entries are stored, or None for the default folder.

    Raises:
        SavedEntryConflictError: If a saved or registered entry of the kind already has the name, or an entry the
            recipe names was deleted.
        SavedEntryStorageError: If the recipe can't be written, or can't be deleted after registering failed.
    """
    async with saved_entry_lock():
        await save_then_register_async(kind=kind, name=name, recipe=recipe, register=register, source=source)
