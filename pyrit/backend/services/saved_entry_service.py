# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Look up, change, and delete saved registry entries through their stored files.

Who may change or delete an entry comes from its file, never from the running entry: the person who saved it or an
administrator, or anyone when sign-in is off. A file that doesn't say who saved it, such as one that can't be read,
needs an administrator. An entry that another saved entry names, or that a registered entry holds, isn't deleted. An
edit rebuilds the entry through the API that creates one, so it passes every check a new entry does.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from pyrit.backend.models import CreateConverterRequest, CreateScorerRequest, CreateTargetRequest
from pyrit.backend.models.saved_entries import (
    SavedEntryResponse,
    SavedEntryUpdateResponse,
    UpdateSavedEntryRequest,
)
from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.backend.services import get_converter_service, get_target_service
from pyrit.backend.services.component_lifecycle import release_component_async
from pyrit.backend.services.saved_entries import (
    REGISTRIES_BY_KIND,
    SavedEntryReplacement,
    SavedEntryStorageError,
    hold_saved_entry_lock_async,
)
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.backend.services.technique_service import get_technique_service
from pyrit.models import ComponentIdentifier, Identifiable
from pyrit.models.catalog import (
    ComponentRecipe,
    RegistryEntryOrigin,
    RegistryRecipeKind,
    RestoreFailure,
    SavedEntryOwner,
    SavedEntryRef,
    StoredRecipe,
    TechniqueRecipe,
)
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
    """The caller may not delete the saved entry."""


class SavedEntryVersionRequiredError(ValueError):
    """A delete didn't say which version of the saved file it read."""


class SavedEntryInUseError(ValueError):
    """The saved entry changed since it was read, or something still uses it."""


class SavedEntryRequestError(ValueError):
    """An edit asks for a change a saved entry can't make, such as a different class."""


@dataclass(frozen=True)
class SavedEntryCaller:
    """
    Who is asking, as the route works it out from the request.

    Attributes:
        oid (str | None): The signed-in user's object ID, or None when nobody is signed in.
        is_admin (bool): Whether the caller is an administrator.
        sign_in (bool): Whether sign-in is on.
    """

    oid: str | None
    is_admin: bool
    sign_in: bool


class SavedEntryService:
    """Look up and delete saved targets, converters, scorers, and techniques."""

    async def get_saved_entry_async(
        self, *, kind: RegistryRecipeKind, name: str, recipes_source: str | None, caller: SavedEntryCaller
    ) -> SavedEntryResponse:
        """
        Describe a saved entry's file: its version, who saved it, and whether the caller may change it.

        Args:
            kind (RegistryRecipeKind): The kind of entry.
            name (str): The entry's name.
            recipes_source (str | None): Where saved entries are stored, or None for the default folder.
            caller (SavedEntryCaller): Who is asking.

        Returns:
            SavedEntryResponse: The file's description.

        Raises:
            SavedEntryNotFoundError: If no saved entry of the kind has the name.
            SavedEntryStorageError: If storage can't be read.
        """
        stored = await _read_async(
            storage=RegistryRecipeStorage(kind=kind, source=recipes_source), kind=kind, name=name
        )
        return _describe(kind=kind, name=name, stored=stored, caller=caller)

    async def update_saved_entry_async(
        self,
        *,
        kind: RegistryRecipeKind,
        name: str,
        request: UpdateSavedEntryRequest,
        recipes_source: str | None,
        caller: SavedEntryCaller,
        runtime: RuntimeLifecycle | None,
    ) -> SavedEntryUpdateResponse:
        """
        Change a saved entry's settings, rebuilding it through the API that creates one.

        The running entry is removed while it's rebuilt and put back if the edit fails. Registered entries that hold
        it keep the old object until the next apply or restart. An entry that couldn't be restored is registered
        once its new settings work, which frees its name. The name, the class or attack type, and who saved it stay
        the same.

        Args:
            kind (RegistryRecipeKind): The kind of entry.
            name (str): The entry's name.
            request (UpdateSavedEntryRequest): The version the caller read and the changed recipe.
            recipes_source (str | None): Where saved entries are stored, or None for the default folder.
            caller (SavedEntryCaller): Who is asking.
            runtime (RuntimeLifecycle | None): The runtime, to keep the old entry's resources open while work could
                still be using it.

        Returns:
            SavedEntryUpdateResponse: The saved file after the edit, and what still holds the old entry.

        Raises:
            SavedEntryNotFoundError: If no saved entry of the kind has the name.
            SavedEntryForbiddenError: If the caller may not change it.
            SavedEntryInUseError: If the file changed since it was read, or can't be read or isn't a valid entry.
            SavedEntryRequestError: If the recipe changes the class or attack type, or what a saved entry can't hold.
            NotSaveableError: If the new settings can't be saved, as for a new entry.
            SavedEntryConflictError: If the new settings conflict with another entry, as for a new entry.
            SavedEntryStorageError: If storage can't be read or written.
            PermissionError: If the new settings need an administrator, as for a new entry.
            ValueError: If the new settings aren't valid, as for a new entry.
        """
        singular = _singular(kind)
        storage = RegistryRecipeStorage(kind=kind, source=recipes_source)
        async with hold_saved_entry_lock_async():
            stored = await _read_async(storage=storage, kind=kind, name=name)
            if not isinstance(stored, StoredRecipe):
                raise SavedEntryInUseError(
                    f"Saved {singular} '{name}' can't be read or isn't a valid entry, so it can't be changed. "
                    "Delete it, then save it again."
                )
            owner = stored.recipe.owner
            if not _may_change(owner=owner, caller=caller):
                who = owner.name if owner is not None and owner.name else "the person who saved it"
                raise SavedEntryForbiddenError(f"Only {who} or an administrator can change saved {singular} '{name}'.")
            if request.expected_version != stored.version:
                raise SavedEntryInUseError(
                    f"Saved {singular} '{name}' changed since it was read. Look at it again before changing it."
                )
            instances = REGISTRIES_BY_KIND[kind].get_registry_singleton().instances
            held = next(
                (unavailable for unavailable in instances.get_unavailable_entries() if unavailable.name == name), None
            )
            # A held name isn't listed, so this looks up only an entry that was registered.
            entry = instances.get_entry(name) if name in instances.get_names() else None
            if entry is not None and registry_entry_origin(entry.metadata) is not RegistryEntryOrigin.SAVED:
                entry = None
            if entry is not None:
                instances.unregister(name, expected_entry=entry)
            if held is not None:
                # The rebuilt entry takes the name that its failed restore held.
                instances.clear_unavailable(name)
            try:
                await _rebuild_async(
                    kind=kind,
                    name=name,
                    stored=stored.recipe,
                    changed=request.recipe,
                    caller=caller,
                    recipes_source=recipes_source,
                    replacing=SavedEntryReplacement(version=stored.version, previous=stored.recipe),
                )
            except BaseException:
                _put_back(kind=kind, entry=entry)
                if held is not None:
                    instances.mark_unavailable(held)
                raise
            if held is not None and runtime is not None:
                runtime.forget_restore_failure(SavedEntryRef(kind=kind, name=name))
            # Listed after the rebuild, since a request that finished during it may have registered an entry that
            # holds the old one.
            holders = _holders(kind=kind, entry=entry) if entry is not None else []
            if (
                entry is not None
                and not holders
                and not _registered_anywhere(entry.instance)
                and (runtime is None or not runtime.has_active_work())
            ):
                await release_component_async(entry.instance)
            updated = await _read_async(storage=storage, kind=kind, name=name)
        return SavedEntryUpdateResponse(
            **_describe(kind=kind, name=name, stored=updated, caller=caller).model_dump(),
            running=entry is not None,
            still_using_old=holders,
        )

    async def delete_saved_entry_async(
        self,
        *,
        kind: RegistryRecipeKind,
        name: str,
        expected_version: str | None,
        recipes_source: str | None,
        caller: SavedEntryCaller,
        runtime: RuntimeLifecycle | None,
    ) -> None:
        """
        Delete a saved entry's file and remove the entry it restored, so it doesn't come back.

        The running entry is removed before its file is deleted, so no request can pick it up in between, and is
        put back if the file can't be deleted. A startup entry that has the same name stays. Deleting an entry that
        couldn't be restored frees its name, which brings back a startup entry it hid.

        Args:
            kind (RegistryRecipeKind): The kind of entry.
            name (str): The entry's name.
            expected_version (str | None): The file version the caller read. Required unless the file can't be read.
            recipes_source (str | None): Where saved entries are stored, or None for the default folder.
            caller (SavedEntryCaller): Who is asking.
            runtime (RuntimeLifecycle | None): The runtime, to check that nothing is using the entry and to stop
                listing it among the entries that couldn't be restored.

        Raises:
            SavedEntryNotFoundError: If no saved entry of the kind has the name.
            SavedEntryForbiddenError: If the caller may not delete it.
            SavedEntryVersionRequiredError: If ``expected_version`` is missing for a file that can be read.
            SavedEntryInUseError: If the file changed since it was read, another saved entry names it, a registered
                entry holds it, or a request, run, or send is in progress.
            SavedEntryStorageError: If storage can't be read or written.
        """
        singular = _singular(kind)
        storage = RegistryRecipeStorage(kind=kind, source=recipes_source)
        async with hold_saved_entry_lock_async():
            stored = await _read_async(storage=storage, kind=kind, name=name)
            owner = _owner(stored)
            if not _may_change(owner=owner, caller=caller):
                who = owner.name if owner is not None and owner.name else None
                raise SavedEntryForbiddenError(
                    f"Only {who} or an administrator can delete saved {singular} '{name}'."
                    if who
                    else f"Only an administrator can delete saved {singular} '{name}', since its file doesn't say "
                    "who saved it."
                )
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
            held = any(unavailable.name == name for unavailable in instances.get_unavailable_entries())
            # A held name isn't listed, so this looks up only an entry that was registered.
            entry = instances.get_entry(name) if name in instances.get_names() else None
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
            if held:
                instances.clear_unavailable(name)
            if entry is not None and not _registered_anywhere(entry.instance):
                await release_component_async(entry.instance)
            if runtime is not None:
                runtime.forget_restore_failure(SavedEntryRef(kind=kind, name=name))


def _describe(
    *, kind: RegistryRecipeKind, name: str, stored: StoredRecipe | RestoreFailure, caller: SavedEntryCaller
) -> SavedEntryResponse:
    """
    Describe a saved entry's file for the caller.

    Returns:
        SavedEntryResponse: Its version and problem, who saved it, whether the caller may change it, and, for a caller
            who may, what it saves.
    """
    owner = _owner(stored)
    can_change = _may_change(owner=owner, caller=caller)
    recipe = (
        stored.recipe.model_dump(mode="json", exclude_none=True, exclude={"owner"})
        if can_change and isinstance(stored, StoredRecipe)
        else None
    )
    return SavedEntryResponse(
        kind=kind,
        name=name,
        version=stored.version,
        problem=stored.reason if isinstance(stored, RestoreFailure) else None,
        recipe=recipe,
        saved_by=(owner.name or None) if owner is not None else None,
        saved_by_me=owner is not None and caller.oid is not None and owner.oid == caller.oid,
        can_change=can_change,
    )


async def _rebuild_async(
    *,
    kind: RegistryRecipeKind,
    name: str,
    stored: ComponentRecipe | TechniqueRecipe,
    changed: dict[str, Any],
    caller: SavedEntryCaller,
    recipes_source: str | None,
    replacing: SavedEntryReplacement,
) -> None:
    """
    Build an edited entry through the API that creates one, overwriting its recipe and registering it.

    Raises:
        SavedEntryRequestError: If the recipe changes the name, class, attack type, or who saved the entry, or holds
            fields a saved entry of the kind doesn't have.
    """
    singular = _singular(kind)
    owner = stored.owner
    stored_owner = owner.model_dump(mode="json") if owner is not None else None
    if changed.get("owner", stored_owner) != stored_owner:
        raise SavedEntryRequestError(f"Who saved a saved {singular} can't change; leave out owner.")
    changes = {key: value for key, value in changed.items() if key not in ("schema_version", "owner")}
    try:
        if isinstance(stored, TechniqueRecipe):
            if changes.pop("type", stored.type) != stored.type:
                raise SavedEntryRequestError(
                    "A saved technique's attack type can't change. Delete it and save a new one instead."
                )
            if changes.pop("name", name) != name or changes.pop("persist", True) is not True:
                raise SavedEntryRequestError(
                    "A saved technique keeps its name and stays saved. Delete it and save a new one instead."
                )
            technique = CreateTechniqueRequest(name=name, type=stored.type, persist=True, **changes)
            await get_technique_service().create_async(
                request=technique, owner=owner, recipes_source=recipes_source, replacing=replacing
            )
            return
        for key in ("class_module", "class_name"):
            if changes.pop(key, getattr(stored, key)) != getattr(stored, key):
                raise SavedEntryRequestError(
                    f"A saved {singular}'s class can't change. Delete it and save a new one instead."
                )
        params = changes.pop("params", {})
        if changes:
            raise SavedEntryRequestError(
                f"Only the params of a saved {singular} can change; leave out {sorted(changes)}."
            )
        if not isinstance(params, dict):
            raise SavedEntryRequestError(f"A saved {singular}'s params must be an object.")
        if kind is RegistryRecipeKind.TARGETS:
            auth_mode = params.get("auth_mode")
            target = CreateTargetRequest(
                name=name,
                type=stored.class_name,
                params=params,
                persist=True,
                **({"auth_mode": auth_mode} if auth_mode is not None else {}),
            )
            await get_target_service().create_target_async(
                request=target,
                is_admin=caller.is_admin,
                owner=owner,
                recipes_source=recipes_source,
                replacing=replacing,
            )
        elif kind is RegistryRecipeKind.CONVERTERS:
            converter = CreateConverterRequest(name=name, type=stored.class_name, params=params, persist=True)
            await get_converter_service().create_converter_async(
                request=converter, owner=owner, recipes_source=recipes_source, replacing=replacing
            )
        else:
            scorer = CreateScorerRequest(name=name, type=stored.class_name, params=params, persist=True)
            await get_scorer_service().create_scorer_async(
                request=scorer, owner=owner, recipes_source=recipes_source, replacing=replacing
            )
    except ValidationError as error:
        raise SavedEntryRequestError(f"The changed recipe isn't valid for a saved {singular}: {error}") from error


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
        for other in registry.get_registry_singleton().instances.get_all_instances():
            if (other_kind, other.name) == (kind, entry.name):
                continue
            if held_hash in _held_hashes(other.instance):
                origin = _ORIGIN_WORDS[registry_entry_origin(other.metadata)]
                holders.append(f"{origin} {_singular(other_kind)} '{other.name}'")
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
        for other in registry.get_registry_singleton().instances.get_all_instances()
    )


def _owner(stored: StoredRecipe | RestoreFailure) -> SavedEntryOwner | None:
    """
    Return who saved an entry, as its file says.

    Returns:
        SavedEntryOwner | None: The owner, or None when the file doesn't say or can't be used.
    """
    return stored.recipe.owner if isinstance(stored, StoredRecipe) else None


def _may_change(*, owner: SavedEntryOwner | None, caller: SavedEntryCaller) -> bool:
    """
    Return whether the caller may change or delete an entry its file says the owner saved.

    Returns:
        bool: True when sign-in is off, for an administrator, and for the person who saved it.
    """
    if not caller.sign_in or caller.is_admin:
        return True
    return owner is not None and caller.oid is not None and owner.oid == caller.oid


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
