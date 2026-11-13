# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Save registry entries, so ``RegistryRecipeRestorer`` rebuilds them at every start.

Saving takes three steps: ``prepare_saved_params_async`` turns off the values the entry would take from this
server and refuses what a saved entry can't hold, the caller builds the entry from the parameters it returns, and
``save_then_register_async`` writes the entry's recipe and registers the entry as saved.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pyrit.models import ComponentType
from pyrit.models.catalog import ComponentRecipe, RegistryEntryOrigin, RegistryRecipeKind
from pyrit.registry.file_document_storage import DocumentConflictError, DocumentStorageUnavailableError
from pyrit.registry.instance_registry import SAVED_ENTRY_METADATA_KEY, RegistryNameTakenError, registry_entry_origin
from pyrit.registry.registry_recipe_rules import SavedEntryRefusedError, check_saved_entry_params
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pyrit.models import Parameter
    from pyrit.registry.registry import InstanceHoldingRegistry

logger = logging.getLogger(__name__)


class NotSaveableError(ValueError):
    """An entry can't be saved as asked, though it can still be created without saving it."""


class SavedEntryConflictError(ValueError):
    """An entry can't be saved under a name that a registered or saved entry of its kind already has."""


class SavedEntryStorageError(RuntimeError):
    """Saved entries can't be written right now."""


async def prepare_saved_params_async(
    *,
    registry: InstanceHoldingRegistry[Any, Any],
    type_name: str,
    cls: type,
    params: Mapping[str, Any],
    auth_mode: str | None = None,
) -> dict[str, Any]:
    """
    Turn off the server values an entry to save would read, then refuse what it still can't hold.

    The checks are the ones restore makes, so an entry this accepts is rebuilt at every start.

    Args:
        registry (InstanceHoldingRegistry[Any, Any]): The registry the entry's class is registered in.
        type_name (str): The name the class is registered under, which the recipe names.
        cls (type): The class the entry is built from.
        params (Mapping[str, Any]): The entry's constructor parameters.
        auth_mode (str | None): A target's auth mode, or None for an entry without one.

    Returns:
        dict[str, Any]: The parameters to build and save the entry with.

    Raises:
        NotSaveableError: If the entry would hold or use a credential, its class was added by a scan's launch
            script, or it uses a Temporary target.
    """
    saved_params = dict(params)
    fallbacks: Mapping[str, str | None] = getattr(cls, "server_fallback_parameters", {})
    for name, turned_off in fallbacks.items():
        if turned_off is not None and saved_params.get(name) in (None, ""):
            saved_params[name] = turned_off
    if registry.is_temporary_class(type_name):
        raise NotSaveableError(
            f"'{type_name}' was added by a scan's launch script, so a saved entry of it couldn't be restored after "
            "a restart. It can still be created without saving it."
        )
    metadata = await asyncio.to_thread(registry.get_registered_class_metadata, type_name)
    if metadata is None:
        # Restore falls back to the class itself the same way, so saving never checks less than restoring.
        metadata = await asyncio.to_thread(registry.get_class_metadata, cls)
    parameters = metadata.parameters
    try:
        check_saved_entry_params(cls=cls, parameters=parameters, params=saved_params, auth_mode=auth_mode)
    except SavedEntryRefusedError as error:
        raise NotSaveableError(f"{error} It can still be created without saving it.") from error
    _refuse_temporary_references(parameters=parameters, params=saved_params)
    return saved_params


def _refuse_temporary_references(*, parameters: Sequence[Parameter], params: Mapping[str, Any]) -> None:
    """
    Refuse an entry to save that uses a Temporary target, which won't exist after a restart.

    Raises:
        NotSaveableError: If a target parameter names a Temporary target.
    """
    from pyrit.registry.components.target_registry import TargetRegistry

    targets = TargetRegistry.get_registry_singleton().instances
    for parameter in parameters:
        if parameter.reference is None or parameter.reference.component_type is not ComponentType.TARGET:
            continue
        value = params.get(parameter.name)
        for name in value if isinstance(value, list) else [value]:
            entry = targets.get_entry(name) if isinstance(name, str) else None
            if entry is not None and registry_entry_origin(entry.metadata) is RegistryEntryOrigin.TEMPORARY:
                raise NotSaveableError(
                    f"Uses temporary target '{name}', which won't exist after a restart. "
                    "It can still be created without saving it."
                )


async def save_then_register_async(
    *,
    kind: RegistryRecipeKind,
    name: str,
    recipe: ComponentRecipe,
    register: Callable[[dict[str, Any]], object],
    source: str | None = None,
) -> None:
    """
    Save an entry's recipe, then register the entry as saved.

    The recipe is written only if no saved entry of the kind has the name, so of two saves under one name only one
    succeeds. If registering then fails, the recipe is deleted again.

    Args:
        kind (RegistryRecipeKind): The kind of entry.
        name (str): The entry's name.
        recipe (ComponentRecipe): How restore rebuilds the entry.
        register (Callable[[dict[str, Any]], object]): Registers the built entry with the metadata it's given.
        source (str | None): Local directory or Azure Blob URI holding the saved entries. Defaults to
            ``registry_recipes`` under the PyRIT configuration directory.

    Raises:
        SavedEntryConflictError: If a saved or registered entry of the kind already has the name.
        SavedEntryStorageError: If the recipe can't be written, or can't be deleted after registering failed.
    """
    singular = kind.value.removesuffix("s")
    storage = RegistryRecipeStorage(kind=kind, source=source)
    try:
        await asyncio.to_thread(storage.create_recipe, name=name, recipe=recipe)
    except DocumentConflictError as error:
        raise SavedEntryConflictError(f"A saved {singular} named '{name}' already exists.") from error
    except DocumentStorageUnavailableError as error:
        logger.error(
            "Saved %s '%s' couldn't be written to %s (%s). If the write reached storage anyway, the %s is restored "
            "at the next start.",
            singular,
            name,
            storage.display_source,
            error.reason,
            singular,
        )
        raise SavedEntryStorageError(f"Saved {kind.value} can't be written right now.") from error
    try:
        register({SAVED_ENTRY_METADATA_KEY: kind.value})
    except Exception as error:
        if not await _delete_recipe_async(storage=storage, singular=singular, name=name):
            raise SavedEntryStorageError(f"Saved {kind.value} can't be written right now.") from error
        if isinstance(error, RegistryNameTakenError):
            raise SavedEntryConflictError(str(error)) from error
        raise


async def _delete_recipe_async(*, storage: RegistryRecipeStorage, singular: str, name: str) -> bool:
    """
    Delete a recipe whose entry couldn't be registered.

    Returns:
        bool: False if the recipe couldn't be deleted, which is logged for an operator to fix.
    """
    try:
        await asyncio.to_thread(storage.delete_recipe, name)
    except Exception as error:
        logger.error(
            "Saved %s '%s' couldn't be registered, and its saved recipe in %s couldn't be deleted (%s). "
            "Delete it there, or the %s is restored at the next start.",
            singular,
            name,
            storage.display_source,
            error.reason if isinstance(error, DocumentStorageUnavailableError) else type(error).__name__,
            singular,
        )
        return False
    return True
