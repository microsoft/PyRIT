# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Save registry entries, so ``RegistryRecipeRestorer`` rebuilds them at every start.

Saving takes three steps: ``prepare_saved_params_async`` turns off the values the entry would take from this
server and refuses what a saved entry can't hold, the caller builds the entry from the parameters it returns, and
``save_then_register_async`` writes the entry's recipe and registers the entry as saved. Targets, converters, and
scorers are saved the same way.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyrit.common.apply_defaults import resolve_constructor_defaults
from pyrit.common.azure_storage import redact_urls
from pyrit.models import ComponentType
from pyrit.models.catalog import (
    ComponentRecipe,
    RegistryEntryOrigin,
    RegistryRecipeKind,
    RestoreFailureReason,
    TechniqueRecipe,
)
from pyrit.registry.file_document_storage import DocumentConflictError, DocumentStorageUnavailableError
from pyrit.registry.instance_registry import SAVED_ENTRY_METADATA_KEY, RegistryNameTakenError, registry_entry_origin
from pyrit.registry.registry_recipe_restore import RegistryRecipeRestorer
from pyrit.registry.registry_recipe_rules import SavedEntryRefusedError, check_saveable_class, check_saved_entry_params
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage
from pyrit.registry.resolution import reject_non_external_params

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pyrit.models import Parameter
    from pyrit.registry.instance_registry import RegistryEntry
    from pyrit.registry.registry import InstanceHoldingRegistry, Registry

logger = logging.getLogger(__name__)


class NotSaveableError(ValueError):
    """An entry can't be saved as asked, though it can still be created without saving it."""

    def __init__(self, cause: str) -> None:
        """
        Initialize the error with why the entry can't be saved.

        Args:
            cause (str): Why the entry can't be saved, in words safe to show users. The error's message adds that
                the entry can still be created without saving it.
        """
        super().__init__(f"{cause} It can still be created without saving it.")
        self.cause = cause


class SavedEntryConflictError(ValueError):
    """An entry can't be saved under a name that a registered or saved entry of its kind already has."""


class SavedEntryStorageError(RuntimeError):
    """Saved entries can't be written right now."""


@dataclass(frozen=True)
class SavedEntryReplacement:
    """
    A saved entry an edit rebuilds, so its recipe is overwritten instead of created.

    Before the entry is rebuilt, the edit has removed the running entry.

    Attributes:
        version (str): The version of the recipe the edit read.
        previous (ComponentRecipe | TechniqueRecipe): That recipe, written back if the new entry can't be registered.
        running (bool): Whether the entry was running, so the rebuilt one is registered in its place. One that
            wasn't, such as an entry that failed to restore, is only saved, and the next apply or start restores it.
    """

    version: str
    previous: ComponentRecipe | TechniqueRecipe
    running: bool


def get_class_saveability(*, registry: Registry[Any, Any], type_name: str) -> tuple[tuple[str, ...], str | None]:
    """
    Return the auth modes a saved entry of a registered class can use, or why none can.

    This is the class-level answer, before any of the entry's values are known; the values are checked when it's
    saved.

    Args:
        registry (Registry[Any, Any]): The registry the class is registered in.
        type_name (str): The name the class is registered under.

    Returns:
        tuple[tuple[str, ...], str | None]: The saveable auth modes and None, or no modes and why the class can't
            be saved.
    """
    if registry.is_temporary_class(type_name):
        return (), _launch_script_class_reason(type_name)
    try:
        return check_saveable_class(registry.get_class(type_name)), None
    except SavedEntryRefusedError as error:
        return (), str(error)


def _launch_script_class_reason(type_name: str) -> str:
    """
    Explain why an entry of a class that a scan's launch script added can't be saved.

    Args:
        type_name (str): The name the class is registered under.

    Returns:
        str: The reason.
    """
    return (
        f"'{type_name}' was added by a scan's launch script, so a saved entry of it couldn't be restored after a "
        "restart."
    )


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

    Args:
        registry (InstanceHoldingRegistry[Any, Any]): The registry the entry's class is registered in.
        type_name (str): The name the class is registered under.
        cls (type): The class the entry is built from.
        params (Mapping[str, Any]): The entry's constructor parameters.
        auth_mode (str | None): A target's auth mode from its create request, or None for converters and scorers.

    Returns:
        dict[str, Any]: The parameters to build and save the entry with.

    Raises:
        NotSaveableError: If the entry's class can't be saved or was added by a scan's launch script, or the entry
            would hold or use a credential, leaves a setting to this server's environment, or names a Temporary entry.
        ValueError: If an address the entry would use isn't a full http(s) or ws(s) address, or a parameter is one
            the API can't set, which restore can't pass either.
    """
    # Finding the class can run the registry's first, slow class discovery, so it stays off the event loop.
    _, class_reason = await asyncio.to_thread(get_class_saveability, registry=registry, type_name=type_name)
    if class_reason is not None:
        raise NotSaveableError(class_reason)
    saved_params = dict(params)
    fallbacks: Mapping[str, str | None] = getattr(cls, "server_fallback_parameters", {})
    for name, turned_off in fallbacks.items():
        if turned_off is not None and saved_params.get(name) in (None, ""):
            saved_params[name] = turned_off
    metadata = await asyncio.to_thread(registry.get_registered_class_metadata, type_name)
    if metadata is None:
        # Restore falls back to the class itself the same way, so saving never checks less than restoring.
        metadata = await asyncio.to_thread(registry.get_class_metadata, cls)
    parameters = metadata.parameters
    try:
        check_saved_entry_params(cls=cls, parameters=parameters, params=saved_params, auth_mode=auth_mode)
    except SavedEntryRefusedError as error:
        # The address needs fixing; creating the entry without saving it wouldn't make it usable.
        if error.reason is RestoreFailureReason.INVALID_RECIPE:
            raise ValueError(str(error)) from error
        raise NotSaveableError(str(error)) from error
    _name_default_references(cls=cls, parameters=parameters, params=saved_params)
    _refuse_values_restore_cant_pass(cls=cls, parameters=parameters, params=saved_params)
    _refuse_environment_values(cls=cls, params=saved_params)
    refuse_temporary_references(parameters=parameters, params=saved_params)
    return saved_params


def _refuse_values_restore_cant_pass(*, cls: type, parameters: Sequence[Parameter], params: Mapping[str, Any]) -> None:
    """
    Refuse a parameter restore can't pass, since it builds a saved entry from its recipe as the API builds one.

    Raises:
        ValueError: If a parameter isn't declared, takes a Python object, names a server file or folder, or names an
            entry by anything but its name.
    """
    try:
        reject_non_external_params(
            params=params,
            declared=parameters,
            owner=cls.__name__,
            allowed_path_params=getattr(cls, "caller_path_parameters", frozenset()),
        )
    except ValueError as error:
        raise ValueError(f"Saved entries are rebuilt from what the API accepts. {error}") from error


def _refuse_environment_values(*, cls: type, params: Mapping[str, Any]) -> None:
    """
    Refuse an entry to save that leaves a required setting to this server's environment.

    Raises:
        NotSaveableError: If a setting the class reads from the environment when it's empty is empty.
    """
    for name in sorted(getattr(cls, "environment_backed_parameters", frozenset())):
        value = params.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise NotSaveableError(
                f"'{name}' is empty, so '{cls.__name__}' would read it from this server's environment. A saved "
                "entry sets it."
            )


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
        names = [
            _registered_name(parameter_name=parameter.name, component_type=component_type, instance=item)
            for item in (value if isinstance(value, (list, tuple)) else [value])
        ]
        params[parameter.name] = names if isinstance(value, (list, tuple)) else names[0]


def _registered_name(*, parameter_name: str, component_type: ComponentType, instance: Any) -> str:
    """
    Name the registered entry that holds a default reference, preferring a name that comes back at every start.

    Returns:
        str: The entry's registry name.

    Raises:
        NotSaveableError: If no registered entry holds the instance.
    """
    registry = _referenced_registry(component_type)
    entries: list[RegistryEntry[Any]] = (
        [item for item in registry.instances.get_all_instances() if item.instance is instance]
        if registry is not None
        else []
    )
    if not entries:
        raise NotSaveableError(
            f"'{parameter_name}' is left to a default {component_type.value} that isn't registered, so it won't "
            "exist after a restart."
        )
    entry = next(
        (item for item in entries if registry_entry_origin(item.metadata) is not RegistryEntryOrigin.TEMPORARY),
        entries[0],
    )
    return entry.name


def _referenced_registry(component_type: ComponentType) -> InstanceHoldingRegistry[Any, Any] | None:
    """
    Return the registry whose entries a saved entry may name, or None for a kind saved entries can't use.

    Saved entries may use only the kinds that restore rebuilds.

    Returns:
        InstanceHoldingRegistry[Any, Any] | None: The registry, or None.
    """
    from pyrit.registry.components.converter_registry import ConverterRegistry
    from pyrit.registry.components.scorer_registry import ScorerRegistry
    from pyrit.registry.components.target_registry import TargetRegistry

    registries: dict[ComponentType, Any] = {
        ComponentType.TARGET: TargetRegistry,
        ComponentType.CONVERTER: ConverterRegistry,
        ComponentType.SCORER: ScorerRegistry,
    }
    registry = registries.get(component_type)
    return registry.get_registry_singleton() if registry is not None else None


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
        value = params.get(parameter.name)
        refuse_temporary_entries(
            component_type=parameter.reference.component_type,
            names=value if isinstance(value, list) else [value],
        )


def refuse_temporary_entries(*, component_type: ComponentType, names: Sequence[Any]) -> None:
    """
    Refuse an entry to save that names a Temporary entry, which won't exist after a restart.

    Args:
        component_type (ComponentType): The kind of entry the names refer to.
        names (Sequence[Any]): The names the entry uses; values that aren't names are skipped.

    Raises:
        NotSaveableError: If a name is a Temporary entry, or an entry of a kind saved entries can't use.
    """
    for name in names:
        if not isinstance(name, str) or not name:
            continue
        registry = _referenced_registry(component_type)
        if registry is None:
            raise NotSaveableError(f"Uses {component_type.value} '{name}', which saved entries can't use yet.")
        entry = registry.instances.get_entry(name)
        if entry is not None and registry_entry_origin(entry.metadata) is RegistryEntryOrigin.TEMPORARY:
            raise NotSaveableError(
                f"Uses temporary {component_type.value} '{name}', which won't exist after a restart."
            )


async def save_then_register_async(
    *,
    kind: RegistryRecipeKind,
    name: str,
    recipe: ComponentRecipe | TechniqueRecipe,
    register: Callable[[dict[str, Any]], object],
    source: str | None = None,
    replacing: SavedEntryReplacement | None = None,
) -> None:
    """
    Save an entry's recipe, then register the entry as saved.

    The recipe is written only if no saved entry of the kind has the name, so of two saves under one name only one
    succeeds. If registering then fails, the recipe this save wrote is deleted again, unless another writer has
    replaced it since. Saving first checks that every entry the recipe names still comes back after a restart, since
    one may have been deleted after the entry was checked; code that also deletes saved entries holds one lock around
    both. A save that's cancelled finishes, or is undone, before the cancellation is passed on, so the saved recipes
    and the registry agree.

    Args:
        kind (RegistryRecipeKind): The kind of entry.
        name (str): The entry's name.
        recipe (ComponentRecipe | TechniqueRecipe): How restore rebuilds the entry.
        register (Callable[[dict[str, Any]], object]): Registers the built entry with the metadata it's given.
        source (str | None): Local directory or Azure Blob URI holding the saved entries. Defaults to
            ``registry_recipes`` under the PyRIT configuration directory.
        replacing (SavedEntryReplacement | None): The saved entry an edit rebuilds, whose recipe is overwritten
            instead, or None to save a new entry. Defaults to None.

    Raises:
        SavedEntryConflictError: If a saved or registered entry of the kind already has the name, an entry the
            recipe names was deleted, or the recipe an edit replaces changed since it was read.
        SavedEntryStorageError: If the recipe can't be written, or can't be deleted or put back after registering
            failed.
    """
    # Looking up the recipe's classes can wait on discovery, so it stays off the event loop.
    await asyncio.to_thread(_refuse_missing_references, kind=kind, recipe=recipe, source=source)
    transaction = (
        _replace_then_register_async(
            kind=kind, name=name, recipe=recipe, register=register, source=source, replacing=replacing
        )
        if replacing is not None
        else _save_then_register_async(kind=kind, name=name, recipe=recipe, register=register, source=source)
    )
    await _settle_async(asyncio.create_task(transaction))


def _refuse_missing_references(
    *, kind: RegistryRecipeKind, recipe: ComponentRecipe | TechniqueRecipe, source: str | None
) -> None:
    """
    Refuse a recipe that names an entry which no longer comes back after a restart.

    Raises:
        SavedEntryConflictError: If a named entry isn't registered, or is Temporary.
    """
    for ref in RegistryRecipeRestorer(source=source).recipe_references(kind=kind, recipe=recipe):
        entry = _registry_for_kind(ref.kind).instances.get_entry(ref.name)
        if entry is None or registry_entry_origin(entry.metadata) is RegistryEntryOrigin.TEMPORARY:
            raise SavedEntryConflictError(
                f"{ref.kind.value.removesuffix('s').capitalize()} '{ref.name}' was deleted while this was being "
                "saved, so it can't be saved now."
            )


def _registry_for_kind(kind: RegistryRecipeKind) -> InstanceHoldingRegistry[Any, Any]:
    """
    Return the registry that holds saved entries of a kind.

    Returns:
        InstanceHoldingRegistry[Any, Any]: The registry.
    """
    from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
    from pyrit.registry.components.converter_registry import ConverterRegistry
    from pyrit.registry.components.scorer_registry import ScorerRegistry
    from pyrit.registry.components.target_registry import TargetRegistry

    registries: dict[RegistryRecipeKind, Any] = {
        RegistryRecipeKind.TARGETS: TargetRegistry,
        RegistryRecipeKind.CONVERTERS: ConverterRegistry,
        RegistryRecipeKind.SCORERS: ScorerRegistry,
        RegistryRecipeKind.TECHNIQUES: AttackTechniqueRegistry,
    }
    return registries[kind].get_registry_singleton()


async def _settle_async(transaction: asyncio.Task[None]) -> None:
    """
    Wait for a save to finish, even if the caller is cancelled meanwhile, then pass the cancellation on.

    A cancelled wait doesn't stop a worker thread that's writing, so the save runs on, undoing itself if it
    fails, and the caller sees its cancellation only once the save is settled. ``asyncio.wait`` never cancels
    what it waits for, which shields the save.

    Raises:
        asyncio.CancelledError: If the caller was cancelled while the save ran, raised from the save's own error if
            it failed.
    """
    cancellation: asyncio.CancelledError | None = None
    while not transaction.done():
        try:
            await asyncio.wait([transaction])
        except asyncio.CancelledError as error:
            cancellation = error
    if cancellation is None:
        transaction.result()
        return
    raise cancellation from (None if transaction.cancelled() else transaction.exception())


async def _save_then_register_async(
    *,
    kind: RegistryRecipeKind,
    name: str,
    recipe: ComponentRecipe | TechniqueRecipe,
    register: Callable[[dict[str, Any]], object],
    source: str | None,
) -> None:
    """
    Write a recipe under a free name, then register its entry, deleting the recipe again if registering fails.

    Raises:
        SavedEntryConflictError: If a saved or registered entry of the kind already has the name.
        SavedEntryStorageError: If the recipe can't be written, or can't be deleted after registering failed.
    """
    singular = kind.value.removesuffix("s")
    storage = RegistryRecipeStorage(kind=kind, source=source)
    try:
        version = await asyncio.to_thread(storage.create_recipe, name=name, recipe=recipe)
    except DocumentConflictError as error:
        raise SavedEntryConflictError(f"A saved {singular} named '{name}' already exists.") from error
    except DocumentStorageUnavailableError as error:
        _log_failure(
            summary=f"Saved {singular} '{name}' couldn't be written to {storage.display_source} ({error.reason}). "
            f"If the write reached storage anyway, the {singular} is restored at the next start.",
            cause=error,
        )
        raise SavedEntryStorageError(f"Saved {kind.value} can't be written right now.") from error
    try:
        register({SAVED_ENTRY_METADATA_KEY: kind.value})
    except Exception as error:
        if not await _delete_recipe_async(storage=storage, singular=singular, name=name, version=version):
            raise SavedEntryStorageError(f"Saved {kind.value} can't be written right now.") from error
        if isinstance(error, RegistryNameTakenError):
            raise SavedEntryConflictError(str(error)) from error
        raise


async def _replace_then_register_async(
    *,
    kind: RegistryRecipeKind,
    name: str,
    recipe: ComponentRecipe | TechniqueRecipe,
    register: Callable[[dict[str, Any]], object],
    source: str | None,
    replacing: SavedEntryReplacement,
) -> None:
    """
    Overwrite the recipe an edit read, then register the rebuilt entry if the old one was running.

    The old recipe is put back if registering fails.

    Raises:
        SavedEntryConflictError: If the recipe changed since it was read, or registering refused the entry.
        SavedEntryStorageError: If the recipe can't be written, or can't be put back after registering failed.
    """
    singular = kind.value.removesuffix("s")
    storage = RegistryRecipeStorage(kind=kind, source=source)
    try:
        version = await asyncio.to_thread(
            storage.replace_recipe, name=name, recipe=recipe, expected_version=replacing.version
        )
    except DocumentConflictError as error:
        raise SavedEntryConflictError(
            f"Saved {singular} '{name}' changed since it was read. Look at it again before changing it."
        ) from error
    except DocumentStorageUnavailableError as error:
        _log_failure(
            summary=f"Saved {singular} '{name}' couldn't be written to {storage.display_source} ({error.reason}).",
            cause=error,
        )
        raise SavedEntryStorageError(f"Saved {kind.value} can't be written right now.") from error
    if not replacing.running:
        return
    try:
        register({SAVED_ENTRY_METADATA_KEY: kind.value})
    except Exception as error:
        try:
            await asyncio.to_thread(
                storage.replace_recipe, name=name, recipe=replacing.previous, expected_version=version
            )
        except (DocumentConflictError, DocumentStorageUnavailableError) as put_back_error:
            _log_failure(
                summary=f"Saved {singular} '{name}' in {storage.display_source} holds the new settings, but the "
                "edited entry couldn't be registered. Its old settings come back only if the file is fixed.",
                cause=put_back_error,
            )
            raise SavedEntryStorageError(f"Saved {kind.value} can't be written right now.") from error
        if isinstance(error, RegistryNameTakenError):
            raise SavedEntryConflictError(str(error)) from error
        raise


async def _delete_recipe_async(*, storage: RegistryRecipeStorage, singular: str, name: str, version: str) -> bool:
    """
    Delete a recipe whose entry couldn't be registered, unless another writer has replaced it since.

    Returns:
        bool: False if the recipe couldn't be deleted, which is logged for an operator to fix.
    """
    try:
        await asyncio.to_thread(storage.delete_recipe, name, expected_version=version)
    except DocumentConflictError:
        # The file holds what another writer saved since, so it isn't this save's recipe to delete.
        return True
    except Exception as error:
        reason = error.reason if isinstance(error, DocumentStorageUnavailableError) else type(error).__name__
        _log_failure(
            summary=f"Saved {singular} '{name}' couldn't be registered, and its saved recipe in "
            f"{storage.display_source} couldn't be deleted ({reason}). Delete it there, or the {singular} is "
            "restored at the next start.",
            cause=error,
        )
        return False
    return True


def _log_failure(*, summary: str, cause: BaseException) -> None:
    """Log a save failure as an error with the error behind it, URL credentials removed, as restore does."""
    logger.error(redact_urls(f"{summary}\n{''.join(traceback.format_exception(cause)).rstrip()}"))
