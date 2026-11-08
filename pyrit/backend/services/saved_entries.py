# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Save registry entries created through the API, so they come back at every start.

Targets, converters, and scorers are saved the same way: the values an entry to save would take from this server
are turned off or refused, its recipe is written only if no saved entry of its kind has the name, and then the
entry is registered as saved. If registering fails, the recipe is deleted again.
"""

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from azure.core.exceptions import AzureError

from pyrit.common.apply_defaults import resolve_constructor_defaults
from pyrit.models import ComponentType, Parameter
from pyrit.models.catalog import ComponentRecipe, RegistryEntryOrigin, RegistryRecipeKind, RestoreFailureReason
from pyrit.registry import ConverterRegistry, InstanceHoldingRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.file_document_storage import DocumentConflictError
from pyrit.registry.instance_registry import (
    SAVED_ENTRY_METADATA_KEY,
    SAVED_ENTRY_OWNER_METADATA_KEY,
    registry_entry_origin,
)
from pyrit.registry.registry_recipe_rules import SavedEntryRefusedError, check_saved_entry_params
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage

logger = logging.getLogger(__name__)

# The registries whose entries a saved entry may name; it may use only the ones that come back after a restart.
_REFERENCED_REGISTRIES: Mapping[ComponentType, Any] = {
    ComponentType.TARGET: TargetRegistry,
    ComponentType.CONVERTER: ConverterRegistry,
    ComponentType.SCORER: ScorerRegistry,
}


class NotSaveableError(ValueError):
    """An entry can't be saved as asked, though it can still be created without saving it."""


class SavedEntryConflictError(ValueError):
    """An entry can't be saved under a name that a registered or saved entry of its kind already has."""


class SavedEntryStorageError(RuntimeError):
    """Saved entries can't be written right now."""


class SavedEntryRemovalError(RuntimeError):
    """A saved entry can't be removed through the API yet, since it comes back from its saved file at every start."""


async def prepare_saved_params_async(
    *,
    registry: InstanceHoldingRegistry[Any, Any],
    type_name: str,
    cls: type,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Turn off the server values an entry to save would read, then refuse what it still can't hold.

    Args:
        registry (InstanceHoldingRegistry[Any, Any]): The registry the entry's class is registered in.
        type_name (str): The name the class is registered under.
        cls (type): The class the entry is built from.
        params (Mapping[str, Any]): The entry's constructor parameters.

    Returns:
        dict[str, Any]: The parameters to build and save the entry with.

    Raises:
        NotSaveableError: If the entry would hold or use a credential, or names a Temporary entry.
        ValueError: If an address the entry would use isn't a full http(s) or ws(s) address.
    """
    saved_params = dict(params)
    fallbacks: Mapping[str, str | None] = getattr(cls, "server_fallback_parameters", {})
    for name, turned_off in fallbacks.items():
        if turned_off is not None and saved_params.get(name) in (None, ""):
            saved_params[name] = turned_off
    metadata = await asyncio.to_thread(registry.get_registered_class_metadata, type_name)
    parameters = metadata.parameters if metadata is not None else ()
    try:
        check_saved_entry_params(cls=cls, parameters=parameters, params=saved_params)
    except SavedEntryRefusedError as error:
        # The address needs fixing; creating the entry without saving it wouldn't make it usable.
        if error.reason is RestoreFailureReason.INVALID_RECIPE:
            raise ValueError(str(error)) from error
        raise NotSaveableError(f"{error} It can still be created without saving it.") from error
    _name_default_references(cls=cls, parameters=parameters, params=saved_params)
    refuse_temporary_references(parameters=parameters, params=saved_params)
    return saved_params


def _name_default_references(*, cls: type, parameters: Sequence[Parameter], params: dict[str, Any]) -> None:
    """
    Name the registered entry that a reference left unset takes from the configured defaults.

    A constructor that applies defaults fills an unset reference from them, and those can be set by code that
    doesn't run again at startup, so the recipe names the entry instead of relying on the default. As when the
    entry is built, a reference sent as null is null, while one left out or cleared starts from the constructor's
    own default.

    Raises:
        NotSaveableError: If a reference left unset takes a default that isn't a registered entry.
    """
    unset = [
        parameter
        for parameter in parameters
        if parameter.reference is not None and params.get(parameter.name) in (None, "")
    ]
    if not unset:
        return
    unset_names = {parameter.name for parameter in unset}
    signature = inspect.signature(cls.__init__)
    sent_as_null = {name: None for name in unset_names if name in params and params[name] is None}
    try:
        effective = resolve_constructor_defaults(
            class_type=cls,
            arguments=sent_as_null,
            excluded_parameters=set(signature.parameters) - unset_names,
        )
    except ValueError:
        # A required reference without a default; building the entry reports it.
        return
    for parameter in unset:
        value = effective.get(parameter.name)
        if value is None or parameter.reference is None:
            continue
        constructor_parameter = signature.parameters.get(parameter.name)
        if (
            parameter.name not in sent_as_null
            and constructor_parameter is not None
            and value is constructor_parameter.default
        ):
            # The constructor's own default comes back the same way at every start.
            continue
        component_type = parameter.reference.component_type
        registry = _REFERENCED_REGISTRIES.get(component_type)
        entries = (
            [item for item in registry.get_registry_singleton().instances.get_all_instances() if item.instance is value]
            if registry is not None
            else []
        )
        if not entries:
            raise NotSaveableError(
                f"'{parameter.name}' is left to a default {component_type.value} that isn't registered, so it won't "
                f"exist after a restart. Name a {component_type.value} to save it, or create it without saving it."
            )
        # Of several names for the default, one that comes back at every start can be saved.
        entry = next(
            (item for item in entries if registry_entry_origin(item.metadata) is not RegistryEntryOrigin.TEMPORARY),
            entries[0],
        )
        params[parameter.name] = entry.name


def refuse_temporary_references(*, parameters: Sequence[Parameter], params: Mapping[str, Any]) -> None:
    """
    Refuse an entry to save that uses a Temporary entry, which won't exist after a restart.

    Args:
        parameters (Sequence[Parameter]): The class's constructor parameters.
        params (Mapping[str, Any]): The entry's constructor parameters.

    Raises:
        NotSaveableError: If a parameter names a Temporary entry, or an entry of a kind saved entries can't use.
    """
    for parameter in parameters:
        if parameter.reference is None:
            continue
        component_type = parameter.reference.component_type
        value = params.get(parameter.name)
        for name in value if isinstance(value, list) else [value]:
            if not isinstance(name, str) or not name:
                continue
            registry = _REFERENCED_REGISTRIES.get(component_type)
            if registry is None:
                raise NotSaveableError(
                    f"Uses {component_type.value} '{name}', which saved entries can't use yet. "
                    "It can still be created without saving it."
                )
            entry = registry.get_registry_singleton().instances.get_entry(name)
            if entry is not None and registry_entry_origin(entry.metadata) is RegistryEntryOrigin.TEMPORARY:
                raise NotSaveableError(
                    f"Uses temporary {component_type.value} '{name}', which won't exist after a restart. "
                    "It can still be created without saving it."
                )


async def save_then_register_async(
    *,
    kind: RegistryRecipeKind,
    name: str,
    recipe: ComponentRecipe,
    recipes_source: str | None,
    register: Callable[[dict[str, Any]], object],
) -> None:
    """
    Save an entry's recipe, then register the entry as saved.

    The recipe is written only if no saved entry of the kind has the name, so of two saves under one name only one
    succeeds. If registering then fails, the recipe is deleted again.

    Args:
        kind (RegistryRecipeKind): The kind of entry.
        name (str): The entry's name.
        recipe (ComponentRecipe): How to rebuild the entry, naming who saved it.
        recipes_source (str | None): Where saved entries are stored, or None for the default folder.
        register (Callable[[dict[str, Any]], object]): Registers the built entry with the metadata it's given.

    Raises:
        SavedEntryConflictError: If a saved or registered entry of the kind already has the name.
        SavedEntryStorageError: If the recipe can't be written, or can't be deleted after registering failed.
    """
    singular = kind.value.removesuffix("s")
    storage = RegistryRecipeStorage(kind=kind, source=recipes_source)
    try:
        await asyncio.to_thread(storage.create_recipe, name=name, recipe=recipe)
    except DocumentConflictError as error:
        raise SavedEntryConflictError(f"A saved {singular} named '{name}' already exists.") from error
    except (OSError, AzureError) as error:
        logger.error(
            "Saved %s '%s' couldn't be written to %s (%s). If the write reached storage anyway, the %s is restored "
            "at the next start.",
            singular,
            name,
            storage.display_source,
            type(error).__name__,
            singular,
        )
        raise SavedEntryStorageError(f"Saved {kind.value} can't be written right now.") from error
    try:
        register({SAVED_ENTRY_METADATA_KEY: kind.value, SAVED_ENTRY_OWNER_METADATA_KEY: recipe.owner})
    except Exception as error:
        if not await _delete_recipe_async(storage=storage, singular=singular, name=name):
            raise SavedEntryStorageError(f"Saved {kind.value} can't be written right now.") from error
        if isinstance(error, ValueError):
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
            type(error).__name__,
            singular,
        )
        return False
    return True
