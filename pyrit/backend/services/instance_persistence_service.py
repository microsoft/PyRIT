# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Save the targets, converters, and scorers created through the API, and rebuild them after a restart.

A saved recipe is the commit point. A change builds and maps the instance off the registry,
writes its recipe, and only then registers it, so an interrupted change converges to the saved
recipes on the next start. A recipe names the environment variables that hold its credentials
instead of storing them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar, cast

from azure.core.exceptions import AzureError
from pydantic import BaseModel

from pyrit.backend.services.instance_credentials import InstanceCredentials
from pyrit.models import ComponentType
from pyrit.models.catalog.instance_recipe import InstanceRecipe, StoredInstanceRecipe, UnrestorableInstance
from pyrit.registry import ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.file_document_storage import DocumentConflictError
from pyrit.registry.instance_recipe_storage import InstanceRecipeStorage
from pyrit.registry.instance_restore import (
    InstanceRestorePlanner,
    describe_dependency_failure,
    describe_missing_reference,
    get_recipe_references,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence

    from pyrit.models import Parameter
    from pyrit.registry.instance_registry import RegistryEntry
    from pyrit.registry.instance_restore import InstanceKey
    from pyrit.registry.registry import InstanceHoldingRegistry

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")
ResponseT = TypeVar("ResponseT", bound=BaseModel)


class InstanceConflictError(ValueError):
    """The change conflicts with a saved or registered instance."""


class InstanceNotFoundError(LookupError):
    """No saved instance has the requested name."""


class PreconditionRequiredError(ValueError):
    """A change to a saved instance did not state the version it expects to replace."""


class AdministratorRequiredError(PermissionError):
    """Only an administrator can save, change, or delete credential references."""


class InstanceStoreUnavailableError(RuntimeError):
    """The saved instance store could not be read or written."""


@dataclass
class BuiltInstance:
    """A constructed instance, the registry metadata to store with it, and how to free what it owns."""

    instance: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    release: Callable[[], Awaitable[None]] | None = None


class InstanceKindHandler(ABC, Generic[ResponseT]):
    """
    The steps a target, converter, or scorer service supplies for its kind.

    A kind service saves, replaces, and deletes its instances through the helpers below,
    which run the shared persistence service with the service's own steps.
    """

    kind: ClassVar[ComponentType]

    async def create_saved_async(self, *, recipe: InstanceRecipe, is_admin: bool) -> ResponseT:
        """
        Build, save, and register a new instance of this kind.

        Returns:
            ResponseT: The API response, including the saved version.
        """
        # Type checkers do not infer ResponseT from Self, so pass self as its base type.
        handler = cast("InstanceKindHandler[ResponseT]", self)
        return await get_instance_persistence_service().create_async(handler=handler, recipe=recipe, is_admin=is_admin)

    async def update_saved_async(self, *, recipe: InstanceRecipe, expected_version: str, is_admin: bool) -> ResponseT:
        """
        Replace a saved instance of this kind with a new recipe.

        Returns:
            ResponseT: The API response, including the new saved version.
        """
        handler = cast("InstanceKindHandler[ResponseT]", self)
        return await get_instance_persistence_service().update_async(
            handler=handler, recipe=recipe, expected_version=expected_version, is_admin=is_admin
        )

    async def delete_saved_async(self, *, name: str, expected_version: str | None, is_admin: bool) -> bool:
        """
        Delete a saved instance of this kind and the instance built from it.

        Returns:
            bool: Whether anything was deleted.
        """
        return await get_instance_persistence_service().delete_async(
            handler=self, name=name, expected_version=expected_version, is_admin=is_admin
        )

    @abstractmethod
    def normalize_recipe(self, recipe: InstanceRecipe) -> InstanceRecipe:
        """Reject a recipe the kind cannot build, and return the recipe to save."""

    @abstractmethod
    async def build_async(self, *, recipe: InstanceRecipe, credentials: dict[str, object]) -> BuiltInstance:
        """Construct the instance without registering it."""

    @abstractmethod
    def to_response(self, *, name: str, instance: Any) -> ResponseT:
        """Map a constructed instance to its API response, which has a ``version`` field."""

    @abstractmethod
    async def release_entry_async(self, entry: RegistryEntry[Any]) -> None:
        """Free what a registry entry owns once it is replaced or removed."""

    @abstractmethod
    async def delete_unsaved_async(self, *, name: str) -> bool:
        """Remove an instance that has no saved recipe, returning whether one was removed."""


class InstancePersistenceService:
    """
    Write API-created instances through to saved recipes, and rebuild them in dependency order.

    Changes run one at a time in a retained task, so a cancelled request can neither leave a
    change half done nor let the next change start before it finishes. Conditional writes
    to the store stop another process from overwriting a change it did not read.
    """

    VERSION_METADATA_KEY: ClassVar[str] = "instance_recipe_version"
    REFERENCES_METADATA_KEY: ClassVar[str] = "instance_recipe_references"
    # Fields of a saved document that identify it, rather than name what it references.
    _DOCUMENT_IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset({"schema_version", "kind", "name", "type"})
    _REGISTRY_CLASSES: ClassVar[dict[ComponentType, type[InstanceHoldingRegistry[Any, Any]]]] = {
        ComponentType.TARGET: TargetRegistry,
        ComponentType.CONVERTER: ConverterRegistry,
        ComponentType.SCORER: ScorerRegistry,
    }

    def __init__(self) -> None:
        """Initialize the service with the default store."""
        self._storage = InstanceRecipeStorage()
        self._lock = asyncio.Lock()
        self._operations: set[asyncio.Future[Any]] = set()
        self._unrestorable: list[UnrestorableInstance] = []
        self._blocked: dict[InstanceKey, frozenset[InstanceKey]] = {}
        self._restore_error: str | None = None

    @property
    def restore_error(self) -> str | None:
        """Why the last restore failed as a whole, such as an unreadable store, or ``None`` if it ran."""
        return self._restore_error

    def configure_source(self, source: str | None) -> None:
        """Use a local directory or Azure Blob container, or the default directory for ``None``."""
        self._storage = InstanceRecipeStorage(source=source)

    def get_version(self, entry: RegistryEntry[Any] | None) -> str | None:
        """
        Get the version of the saved recipe a registry entry was built from.

        Returns:
            str | None: The version, or ``None`` for an instance that is not saved.
        """
        version = entry.metadata.get(self.VERSION_METADATA_KEY) if entry is not None else None
        return version if isinstance(version, str) else None

    def get_unrestorable(self, kind: ComponentType) -> list[UnrestorableInstance]:
        """
        Get the saved instances of one kind that the last restore could not rebuild.

        Returns:
            list[UnrestorableInstance]: The instances and reasons, sorted by name.
        """
        return sorted((item for item in self._unrestorable if item.kind is kind), key=lambda item: item.name)

    def describe_missing(self, *, kind: ComponentType, name: str) -> str:
        """
        Explain why no instance is registered under a name.

        Returns:
            str: The restore failure for a saved instance, or that the name was not found.
        """
        label = f"{kind.value.capitalize()} '{name}'"
        item = next((item for item in self._unrestorable if (item.kind, item.name) == (kind, name)), None)
        return f"{label} not found" if item is None else f"{label} is saved but was not restored: {item.reason}"

    async def create_async(
        self, *, handler: InstanceKindHandler[ResponseT], recipe: InstanceRecipe, is_admin: bool
    ) -> ResponseT:
        """
        Build, save, and register a new instance.

        Returns:
            ResponseT: The kind's API response, including the saved version.
        """
        return await self._run_exclusively_async(
            lambda: self._create_locked_async(handler=handler, recipe=recipe, is_admin=is_admin)
        )

    async def update_async(
        self, *, handler: InstanceKindHandler[ResponseT], recipe: InstanceRecipe, expected_version: str, is_admin: bool
    ) -> ResponseT:
        """
        Replace a saved instance with a new recipe.

        Returns:
            ResponseT: The kind's API response, including the new saved version.
        """
        return await self._run_exclusively_async(
            lambda: self._update_locked_async(
                handler=handler, recipe=recipe, expected_version=expected_version, is_admin=is_admin
            )
        )

    async def delete_async(
        self, *, handler: InstanceKindHandler[Any], name: str, expected_version: str | None, is_admin: bool
    ) -> bool:
        """
        Delete a saved instance and the instance built from it.

        Returns:
            bool: Whether anything was deleted.
        """
        return await self._run_exclusively_async(
            lambda: self._delete_locked_async(
                handler=handler, name=name, expected_version=expected_version, is_admin=is_admin
            )
        )

    async def restore_async(self, *, handlers: Sequence[InstanceKindHandler[Any]]) -> None:
        """Rebuild every saved instance after the initializers ran, recording why any could not be rebuilt."""
        await self._run_exclusively_async(lambda: self._restore_locked_async(handlers=handlers))

    async def record_restore_failure_async(self, *, error: Exception) -> None:
        """Record why no saved instance could be restored, so the API reports it instead of listing none."""
        await self._run_exclusively_async(lambda: self._record_restore_failure_locked_async(error=error))

    async def close_async(self) -> None:
        """Wait for changes that are still running."""
        if self._operations:
            await asyncio.gather(*self._operations, return_exceptions=True)

    async def _run_exclusively_async(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        """
        Run one change in a retained task that holds the lock until the change finishes.

        Returns:
            ResultT: The change's result.
        """
        task = asyncio.ensure_future(self._run_locked_async(operation))
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)
        return await asyncio.shield(task)

    async def _run_locked_async(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        """
        Run one change while holding the lock.

        Returns:
            ResultT: The change's result.
        """
        async with self._lock:
            return await operation()

    async def _create_locked_async(
        self, *, handler: InstanceKindHandler[ResponseT], recipe: InstanceRecipe, is_admin: bool
    ) -> ResponseT:
        """
        Create an instance while holding the lock.

        Returns:
            ResponseT: The API response with the saved version.

        Raises:
            ValueError: If the name is shaped like a saved document name, which is reserved.
            InstanceConflictError: If the name is registered or already saved.
        """
        if self._storage.is_document_name(kind=recipe.kind, name=recipe.name):
            raise ValueError(
                f"{self._label(kind=recipe.kind, name=recipe.name)} cannot be created: names shaped like a saved "
                f"document name ('{recipe.kind.value}_<name>_<12 hex digits>') are reserved. Choose another name."
            )
        self._authorize(is_admin=is_admin, recipes=[recipe])
        registry = self._get_registry(recipe.kind)
        if recipe.name in registry.instances:
            raise InstanceConflictError(f"{self._label(kind=recipe.kind, name=recipe.name)} already exists.")
        registry.instances.validate_name_available(recipe.name)
        if await self._load_async(kind=recipe.kind, name=recipe.name) is not None:
            raise InstanceConflictError(
                self._describe_saved_conflict(kind=recipe.kind, name=recipe.name, expected_version=None)
            )
        await self._ensure_restorable_async(kind=recipe.kind, name=recipe.name)
        recipe, parameters = await asyncio.to_thread(self._prepare, handler=handler, recipe=recipe)
        self._ensure_references_survive_restart(recipe=recipe, parameters=parameters)
        built = await self._build_async(handler=handler, recipe=recipe, parameters=parameters)
        try:
            response = handler.to_response(name=recipe.name, instance=built.instance)
            stored = await self._save_async(recipe=recipe, expected_version=None)
        except BaseException:
            await self._release_after_failure_async(built)
            raise
        try:
            self._register(registry=registry, built=built, stored=stored, replace=False)
        except BaseException:
            await self._discard_saved_async(stored=stored)
            await self._release_after_failure_async(built)
            raise
        self._note_change(kind=recipe.kind, name=recipe.name)
        return response.model_copy(update={"version": stored.version})

    async def _update_locked_async(
        self, *, handler: InstanceKindHandler[ResponseT], recipe: InstanceRecipe, expected_version: str, is_admin: bool
    ) -> ResponseT:
        """
        Replace a saved instance while holding the lock.

        Returns:
            ResponseT: The API response with the new saved version.

        Raises:
            InstanceNotFoundError: If nothing is saved under the name.
        """
        current = await self._load_async(kind=recipe.kind, name=recipe.name)
        if current is None:
            raise InstanceNotFoundError(f"No saved {recipe.kind.value} is named '{recipe.name}'.")
        if isinstance(current, UnrestorableInstance):
            raise InstanceConflictError(
                f"{self._label(kind=recipe.kind, name=recipe.name)} cannot be replaced: {current.reason} "
                "Delete it to save a new one."
            )
        self._authorize_change(current=current, recipe=recipe, is_admin=is_admin)
        self._check_version(current=current, kind=recipe.kind, name=recipe.name, expected_version=expected_version)
        await self._ensure_restorable_async(kind=recipe.kind, name=recipe.name)
        registry = self._get_registry(recipe.kind)
        entry = await self._get_owned_entry_async(
            registry=registry, kind=recipe.kind, name=recipe.name, action="changed"
        )
        recipe, parameters = await asyncio.to_thread(self._prepare, handler=handler, recipe=recipe)
        self._ensure_references_survive_restart(recipe=recipe, parameters=parameters)
        built = await self._build_async(handler=handler, recipe=recipe, parameters=parameters)
        try:
            response = handler.to_response(name=recipe.name, instance=built.instance)
            stored = await self._save_async(recipe=recipe, expected_version=expected_version)
        except BaseException:
            await self._release_after_failure_async(built)
            raise
        try:
            self._register(registry=registry, built=built, stored=stored, replace=True)
        except BaseException:
            await self._restore_previous_async(current=current, stored=stored)
            await self._release_after_failure_async(built)
            raise
        if entry is not None:
            await self._release_replaced_entry_async(handler=handler, entry=entry)
        self._note_change(kind=recipe.kind, name=recipe.name)
        return response.model_copy(update={"version": stored.version})

    async def _delete_locked_async(
        self, *, handler: InstanceKindHandler[Any], name: str, expected_version: str | None, is_admin: bool
    ) -> bool:
        """
        Delete a saved instance while holding the lock.

        Returns:
            bool: Whether anything was deleted.

        Raises:
            PreconditionRequiredError: If a saved instance is deleted without ``expected_version``.
            InstanceConflictError: If the instance was built from a saved recipe whose document no longer exists.
        """
        current = await self._load_async(kind=handler.kind, name=name)
        if current is None:
            if self.get_version(self._get_registry(handler.kind).instances.get_entry(name)) is not None:
                raise InstanceConflictError(
                    f"{self._label(kind=handler.kind, name=name)} was built from a saved recipe whose document no "
                    "longer exists. Restart or reinitialize the backend to remove it."
                )
            return await handler.delete_unsaved_async(name=name)
        self._authorize_change(current=current, recipe=None, is_admin=is_admin)
        if expected_version is None:
            raise PreconditionRequiredError(
                f"Deleting saved {handler.kind.value} '{name}' requires the version returned when it was read."
            )
        self._check_version(current=current, kind=handler.kind, name=name, expected_version=expected_version)
        registry = self._get_registry(handler.kind)
        live_name = self._live_name(kind=handler.kind, name=name)
        entry = await self._get_owned_entry_async(
            registry=registry,
            kind=handler.kind,
            name=live_name,
            action="deleted",
            usable=isinstance(current, StoredInstanceRecipe),
        )
        try:
            await self._call_storage_async(
                self._storage.delete_recipe, kind=handler.kind, name=name, expected_version=expected_version
            )
        except DocumentConflictError as error:
            raise InstanceConflictError(
                self._describe_saved_conflict(kind=handler.kind, name=name, expected_version=expected_version)
            ) from error
        if entry is not None:
            registry.instances.unregister(live_name, expected_entry=entry)
            await self._release_replaced_entry_async(handler=handler, entry=entry)
        reported = current.name if isinstance(current, UnrestorableInstance) else current.recipe.name
        document = self._storage.get_addressed_document_name(kind=handler.kind, name=name)
        listed = {
            item.name
            for item in self._unrestorable
            if item.kind is handler.kind and self._document_of(item) == document
        }
        for changed in sorted({name, live_name, reported} | listed):
            self._note_change(kind=handler.kind, name=changed)
        return True

    def _live_name(self, *, kind: ComponentType, name: str) -> str:
        """
        Get the name of the live instance built from the saved document a name addresses.

        A saved document this PyRIT cannot use is also addressed by its own document name; that
        name is mapped to the live instance built from the document, matched by the document name
        its own name maps to, since an unreadable document may not say which instance it is.

        Returns:
            str: The name of that live instance, or ``name`` if no other live instance was built from it.
        """
        instances = self._get_registry(kind).instances
        if not self._storage.is_document_name(kind=kind, name=name):
            return name
        for live_name in instances.get_names():
            if (
                self.get_version(instances.get_entry(live_name)) is not None
                and self._storage.get_document_name(kind=kind, name=live_name) == name
            ):
                return live_name
        return name

    async def _restore_locked_async(self, *, handlers: Sequence[InstanceKindHandler[Any]]) -> None:
        """Rebuild every saved instance while holding the lock, recording a failure instead of raising it."""
        self._unrestorable, self._blocked, self._restore_error = [], {}, None
        try:
            self._unrestorable, self._blocked = await self._restore_saved_async(handlers=handlers)
        except InstanceStoreUnavailableError as error:
            logger.error(f"Saved instances were not restored: {error}")
            self._restore_error = str(error)
        except Exception as error:
            logger.exception("Saved instances were not restored.")
            self._restore_error = f"Saved instances were not restored: {error}"

    async def _record_restore_failure_locked_async(self, *, error: Exception) -> None:
        """Clear the last restore's results and keep why this one could not run, while holding the lock."""
        self._unrestorable, self._blocked = [], {}
        self._restore_error = f"Saved instances were not restored: {error}"

    async def _restore_saved_async(
        self, *, handlers: Sequence[InstanceKindHandler[Any]]
    ) -> tuple[list[UnrestorableInstance], dict[InstanceKey, frozenset[InstanceKey]]]:
        """
        Rebuild every saved instance in dependency order.

        Returns:
            tuple[list[UnrestorableInstance], dict[InstanceKey, frozenset[InstanceKey]]]: The saved
            instances that could not be rebuilt, and why; and, for each one that could not be rebuilt
            only because of an instance it references, everything it references.
        """
        recipes, unreadable = await self._call_storage_async(self._storage.list_recipes)
        plan = InstanceRestorePlanner(
            recipes=recipes,
            is_registered=lambda kind, name: name in self._get_registry(kind).instances,
            get_parameters=lambda recipe: self._get_parameters(kind=recipe.kind, type_name=recipe.type),
            is_unavailable=lambda kind, name: (
                self._find_unrestored(reference=(kind, name), unrestored=unreadable) is not None
            ),
        ).plan()
        handler_by_kind = {handler.kind: handler for handler in handlers}
        issues, blocked = [*unreadable, *plan.unrestorable], dict(plan.blocked)
        failed: set[InstanceKey] = {(issue.kind, issue.name) for issue in issues}
        restored = 0
        for planned in plan.ordered:
            key = (planned.stored.recipe.kind, planned.stored.recipe.name)
            failed_dependency = next((item for item in sorted(planned.dependencies) if item in failed), None)
            if failed_dependency is None:
                issue = await self._restore_one_async(handler=handler_by_kind[key[0]], stored=planned.stored)
            else:
                reason = describe_dependency_failure(failed_dependency)
                issue = self._unrestorable_from(stored=planned.stored, reason=reason)
                blocked[key] = planned.dependencies
            if issue is None:
                restored += 1
                continue
            issues.append(issue)
            failed.add(key)
        logger.info(f"Restored {restored} saved instances from {self._storage.display_source}.")
        for issue in issues:
            logger.warning(f"Saved {issue.kind.value} '{issue.name}' was not restored: {issue.reason}")
        return issues, blocked

    async def _restore_one_async(
        self, *, handler: InstanceKindHandler[Any], stored: StoredInstanceRecipe
    ) -> UnrestorableInstance | None:
        """
        Rebuild and register one saved instance.

        Returns:
            UnrestorableInstance | None: Why it could not be rebuilt, or ``None`` once it is registered.
        """
        try:
            recipe, parameters = await asyncio.to_thread(self._prepare, handler=handler, recipe=stored.recipe)
            built = await self._build_async(handler=handler, recipe=recipe, parameters=parameters)
            try:
                self._register(registry=self._get_registry(recipe.kind), built=built, stored=stored, replace=False)
            except BaseException:
                await self._release_after_failure_async(built)
                raise
        except Exception as error:
            return self._unrestorable_from(stored=stored, reason=str(error))
        return None

    def _prepare(
        self, *, handler: InstanceKindHandler[Any], recipe: InstanceRecipe
    ) -> tuple[InstanceRecipe, Sequence[Parameter]]:
        """
        Validate a recipe and drop credential parameters left empty.

        Returns:
            tuple[InstanceRecipe, Sequence[Parameter]]: The recipe to save and its type's parameters.

        Raises:
            ValueError: If the recipe would save a credential, references a credential for a
                parameter that is not one, or references itself.
        """
        recipe = handler.normalize_recipe(recipe)
        parameters: Sequence[Parameter] = self._get_parameters(kind=recipe.kind, type_name=recipe.type) or ()
        recipe = InstanceCredentials.check_recipe(recipe=recipe, parameters=parameters)
        if (recipe.kind, recipe.name) in get_recipe_references(recipe=recipe, parameters=parameters):
            raise ValueError(f"{self._label(kind=recipe.kind, name=recipe.name)} cannot reference itself.")
        return recipe, parameters

    async def _ensure_restorable_async(self, *, kind: ComponentType, name: str) -> None:
        """
        Refuse to save while the last restore failed and the store still cannot be listed.

        A restore finds saved instances by listing the store, so one saved while the store cannot
        be listed would not be rebuilt after a restart.

        Raises:
            InstanceStoreUnavailableError: If the last restore failed and the store still cannot be listed.
        """
        if self._restore_error is None:
            return
        try:
            await self._call_storage_async(self._storage.list_recipes)
        except InstanceStoreUnavailableError as error:
            raise InstanceStoreUnavailableError(
                f"{self._label(kind=kind, name=name)} cannot be saved while the saved instances cannot be listed, "
                f"because a restart could not rebuild it. {error}"
            ) from error

    def _ensure_references_survive_restart(self, *, recipe: InstanceRecipe, parameters: Sequence[Parameter]) -> None:
        """
        Refuse a reference that the next restore would not bind.

        A restore never binds a reference to another instance that took the name of a saved
        instance it could not rebuild, so saving such a reference would work only until a restart.
        While the last restore failed as a whole, which saved instances it could not rebuild is
        unknown, so no reference is accepted until a restart or reinitialization restores them.

        Raises:
            InstanceConflictError: If the recipe references a registered name whose saved instance was not
                restored, or references anything while the last restore failed as a whole.
        """
        references = sorted(get_recipe_references(recipe=recipe, parameters=parameters))
        if references and self._restore_error is not None:
            raise InstanceConflictError(
                f"{self._label(kind=recipe.kind, name=recipe.name)} cannot reference other instances until the "
                f"saved instances are restored ({self._restore_error.rstrip('.')}). Restart or reinitialize the "
                "backend first."
            )
        for reference in references:
            unrestored = self._find_unrestored(reference=reference, unrestored=self._unrestorable)
            if unrestored is not None and reference[1] in self._get_registry(reference[0]).instances:
                raise InstanceConflictError(
                    f"{self._label(kind=recipe.kind, name=recipe.name)} cannot reference "
                    f"{self._label(kind=reference[0], name=reference[1])} because the saved one was not restored: "
                    f"{unrestored.reason} A restart would not rebuild this reference, so delete that saved one first."
                )

    async def _build_async(
        self, *, handler: InstanceKindHandler[Any], recipe: InstanceRecipe, parameters: Sequence[Parameter]
    ) -> BuiltInstance:
        """
        Read the referenced credentials and construct the instance.

        Returns:
            BuiltInstance: The constructed instance.

        Raises:
            ValueError: If construction fails; credential values are removed from the message.
        """
        credentials = InstanceCredentials.resolve(recipe=recipe, parameters=parameters)
        try:
            # A constructor may change the arguments it is given, such as merging headers into a
            # nested map, so it gets a copy: the recipe saved afterwards must stay as validated.
            return await handler.build_async(recipe=recipe.model_copy(deep=True), credentials=credentials)
        except Exception as error:
            if not credentials:
                raise
            raise ValueError(InstanceCredentials.redact(message=str(error), credentials=credentials)) from None

    def _register(
        self,
        *,
        registry: InstanceHoldingRegistry[Any, Any],
        built: BuiltInstance,
        stored: StoredInstanceRecipe,
        replace: bool,
    ) -> None:
        """Register a built instance with the version and the references of the recipe it was built from."""
        recipe = stored.recipe
        parameters = self._get_parameters(kind=recipe.kind, type_name=recipe.type) or ()
        registry.instances.register(
            built.instance,
            name=recipe.name,
            metadata={
                **built.metadata,
                self.VERSION_METADATA_KEY: stored.version,
                self.REFERENCES_METADATA_KEY: frozenset(get_recipe_references(recipe=recipe, parameters=parameters)),
            },
            replace=replace,
        )

    async def _get_owned_entry_async(
        self,
        *,
        registry: InstanceHoldingRegistry[Any, Any],
        kind: ComponentType,
        name: str,
        action: str,
        usable: bool = True,
    ) -> RegistryEntry[Any] | None:
        """
        Get the registry entry built from a saved recipe, refusing a change that would break it.

        A saved recipe whose name an initializer or another component registered first is not
        live: the registered entry was not built from a saved recipe. Changing the recipe could
        never take effect, so it is refused; deleting it removes only the saved recipe, and the
        instances that reference the name keep the registered one. An entry built from an earlier
        version of the document, which changed outside the API, is still the instance the recipe
        saved, so it is checked, replaced, and removed like any live one. Any other recipe that
        saved instances reference cannot be deleted, live or not, because they could not be
        rebuilt without it; it cannot be changed while live, because they hold the instance.
        A saved document that is not ``usable`` as a recipe and is not live can always be deleted:
        it cannot be rebuilt, so the instances that reference it are not rebuilt either, and
        deleting it is how the instance is created again.

        Returns:
            RegistryEntry[Any] | None: The entry built from the recipe, or ``None`` if the recipe is not live.

        Raises:
            InstanceConflictError: If another component holds the name and the recipe is being
                changed, or saved instances reference the instance.
        """
        entry = registry.instances.get_entry(name)
        if entry is not None and self.get_version(entry) is None:
            if action == "changed":
                raise InstanceConflictError(
                    f"{self._label(kind=kind, name=name)} is registered by an initializer or another component, "
                    "so the saved one cannot be applied. Delete the saved one or save it under another name."
                )
            return None
        if entry is not None or (action == "deleted" and usable):
            await self._ensure_no_dependents_async(
                kind=kind, name=name, action=action, counts_unusable=action == "deleted"
            )
        return entry

    async def _ensure_no_dependents_async(
        self, *, kind: ComponentType, name: str, action: str, counts_unusable: bool
    ) -> None:
        """
        Refuse a change to a live instance, or a delete, that other saved instances reference.

        With ``counts_unusable``, a saved document this PyRIT cannot use as a recipe, such as one a
        newer PyRIT saved, counts too: its reference by name breaks when a recipe is deleted. A live
        saved instance counts by the references it was built with, whatever its saved document holds
        now. A saved document the store could not read may reference the instance, so while one
        cannot be read, the change is refused rather than allowed.

        Raises:
            InstanceConflictError: If any saved recipe references the instance.
            InstanceStoreUnavailableError: If a saved document could not be read, so whether it
                references the instance is unknown.
        """
        recipes, unusable = await self._call_storage_async(self._storage.list_recipes)
        unread = sorted(document.name for document in unusable if document.version is None)
        if unread:
            raise InstanceStoreUnavailableError(
                f"{self._label(kind=kind, name=name)} cannot be {action} while saved documents cannot be read, "
                f"because they may reference it: {', '.join(unread)}. Make them readable, then try again."
            )
        dependents = {
            (stored.recipe.kind, stored.recipe.name)
            for stored in recipes
            if self._may_reference(recipe=stored.recipe, key=(kind, name))
        }
        if counts_unusable:
            dependents.update(
                (document.kind, document.name)
                for document in unusable
                if self._document_may_reference(document=document, name=name)
            )
        live = self._live_dependents(key=(kind, name))
        saved_versions = {(stored.recipe.kind, stored.recipe.name): stored.version for stored in recipes}
        changed = {dependent for dependent, version in live.items() if saved_versions.get(dependent) != version}
        dependents.update(live)
        dependents.discard((kind, name))
        changed.discard((kind, name))
        if dependents:
            message = (
                f"{self._label(kind=kind, name=name)} cannot be {action} because saved instances reference it: "
                f"{self._labels(dependents)}. Change or delete them first."
            )
            if changed:
                message += (
                    " Restored instances whose saved documents changed since keep what they were built with until a "
                    f"restart or reinitialization: {self._labels(changed)}."
                )
            raise InstanceConflictError(message)

    def _live_dependents(self, *, key: InstanceKey) -> dict[InstanceKey, str | None]:
        """
        Find the live saved instances that were built with a reference to an instance.

        Returns:
            dict[InstanceKey, str | None]: The version of the saved recipe each was built from,
            keyed by its kind and name.
        """
        dependents: dict[InstanceKey, str | None] = {}
        for dependent_kind in self._REGISTRY_CLASSES:
            instances = self._get_registry(dependent_kind).instances
            for dependent_name in instances.get_names():
                entry = instances.get_entry(dependent_name)
                if entry is not None and key in entry.metadata.get(self.REFERENCES_METADATA_KEY, ()):
                    dependents[(dependent_kind, dependent_name)] = self.get_version(entry)
        return dependents

    def _may_reference(self, *, recipe: InstanceRecipe, key: InstanceKey) -> bool:
        """
        Return whether a saved recipe references an instance.

        Which parameters hold references depends on the recipe's type, so while its type is not
        registered, any text in its parameter values that names the instance counts: the instance
        is kept until the recipe that may need it is changed or deleted. Parameter names do not count.

        Returns:
            bool: Whether the recipe references the instance, or may when its type is unknown.
        """
        parameters = self._get_parameters(kind=recipe.kind, type_name=recipe.type)
        if parameters is None:
            return key[1] in self._texts(list(recipe.params.values()))
        return key in get_recipe_references(recipe=recipe, parameters=parameters)

    @classmethod
    def _document_may_reference(cls, *, document: UnrestorableInstance, name: str) -> bool:
        """
        Return whether a saved document that is not a usable recipe may reference an instance.

        What such a document references is unknown, so, as for a recipe whose type is not
        registered, any text in it that names the instance counts, apart from the fields that
        identify the document itself.

        Returns:
            bool: Whether the document is a JSON object whose other fields name the instance.
        """
        try:
            payload = json.loads(document.content) if document.content is not None else None
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        return name in cls._texts(
            [value for field_name, value in payload.items() if field_name not in cls._DOCUMENT_IDENTITY_FIELDS]
        )

    @classmethod
    def _texts(cls, value: object) -> Iterator[str]:
        """
        Yield every string in a JSON value, including the keys of its maps.

        Yields:
            str: Each string, at any depth.
        """
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from cls._texts(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from cls._texts(key)
                yield from cls._texts(item)

    async def _load_async(
        self, *, kind: ComponentType, name: str
    ) -> StoredInstanceRecipe | UnrestorableInstance | None:
        """
        Read the saved recipe of one instance.

        Returns:
            StoredInstanceRecipe | UnrestorableInstance | None: The recipe, an unreadable document, or ``None``.
        """
        return await self._call_storage_async(self._storage.load_recipe, kind=kind, name=name)

    async def _save_async(self, *, recipe: InstanceRecipe, expected_version: str | None) -> StoredInstanceRecipe:
        """
        Save a recipe if the store still holds the expected version.

        Returns:
            StoredInstanceRecipe: The saved recipe and its version.

        Raises:
            InstanceConflictError: If the store holds another version, or any version on create.
        """
        try:
            return await self._call_storage_async(
                self._storage.save_recipe, recipe=recipe, expected_version=expected_version
            )
        except DocumentConflictError as error:
            raise InstanceConflictError(
                self._describe_saved_conflict(kind=recipe.kind, name=recipe.name, expected_version=expected_version)
            ) from error

    async def _discard_saved_async(self, *, stored: StoredInstanceRecipe) -> None:
        """Delete a recipe whose instance could not be registered; the next restore rebuilds it if this fails."""
        try:
            await self._call_storage_async(
                self._storage.delete_recipe,
                kind=stored.recipe.kind,
                name=stored.recipe.name,
                expected_version=stored.version,
            )
        except Exception:
            logger.exception(f"Could not remove the saved recipe of '{stored.recipe.name}' after registration failed.")

    async def _restore_previous_async(self, *, current: StoredInstanceRecipe, stored: StoredInstanceRecipe) -> None:
        """Put the replaced recipe back, byte for byte, after its replacement could not be registered."""
        try:
            await self._call_storage_async(
                self._storage.restore_recipe, previous=current, expected_version=stored.version
            )
        except Exception:
            logger.exception(f"Could not put back the replaced recipe of '{stored.recipe.name}'.")

    async def _call_storage_async(self, function: Callable[..., ResultT], **kwargs: Any) -> ResultT:
        """
        Run a blocking store call off the event loop.

        Returns:
            ResultT: The call's result.

        Raises:
            InstanceStoreUnavailableError: If the store cannot be read or written.
        """
        try:
            return await asyncio.to_thread(function, **kwargs)
        except (OSError, AzureError) as error:
            raise InstanceStoreUnavailableError(
                f"The saved instance store {self._storage.display_source} is unavailable: "
                f"{self._storage.redact(str(error))}"
            ) from error

    @staticmethod
    async def _release_replaced_entry_async(*, handler: InstanceKindHandler[Any], entry: RegistryEntry[Any]) -> None:
        """Free what a replaced or removed entry owns; the change has committed, so a failure is only logged."""
        try:
            await handler.release_entry_async(entry)
        except Exception:
            logger.exception(f"Could not free what '{entry.name}' owned after it was replaced or removed.")

    async def _release_after_failure_async(self, built: BuiltInstance) -> None:
        """Free what an instance built for a failed change owns, logging a failure rather than hiding the first one."""
        try:
            await self._release_async(built)
        except Exception:
            logger.exception("Could not free what an instance built for a failed change owns.")

    @staticmethod
    async def _release_async(built: BuiltInstance) -> None:
        """Free what a built instance owns."""
        if built.release is not None:
            await built.release()

    def _authorize_change(
        self, *, current: StoredInstanceRecipe | UnrestorableInstance, recipe: InstanceRecipe | None, is_admin: bool
    ) -> None:
        """
        Require an administrator when the saved or replacement recipe references credentials.

        A saved document that cannot be read might reference credentials, so changing it also
        requires an administrator.

        Raises:
            AdministratorRequiredError: If credentials are involved and the caller is not an administrator.
        """
        if isinstance(current, UnrestorableInstance) and not is_admin:
            raise AdministratorRequiredError(
                "Only an administrator can change or delete a saved document that cannot be read."
            )
        saved = current.recipe if isinstance(current, StoredInstanceRecipe) else None
        self._authorize(is_admin=is_admin, recipes=[recipe, saved])

    @staticmethod
    def _authorize(*, is_admin: bool, recipes: Sequence[InstanceRecipe | None]) -> None:
        """
        Require an administrator when any of the recipes references credentials.

        Raises:
            AdministratorRequiredError: If credentials are involved and the caller is not an administrator.
        """
        if not is_admin and any(recipe is not None and recipe.credentials for recipe in recipes):
            raise AdministratorRequiredError(
                "Only an administrator can save, change, or delete an instance that reads credentials from "
                "server environment variables."
            )

    @classmethod
    def _check_version(
        cls,
        *,
        current: StoredInstanceRecipe | UnrestorableInstance,
        kind: ComponentType,
        name: str,
        expected_version: str,
    ) -> None:
        """
        Refuse a change based on a stale read.

        Raises:
            InstanceConflictError: If the saved version differs from ``expected_version``.
        """
        if current.version != expected_version:
            raise InstanceConflictError(
                cls._describe_saved_conflict(kind=kind, name=name, expected_version=expected_version)
            )

    @staticmethod
    def _describe_saved_conflict(*, kind: ComponentType, name: str, expected_version: str | None) -> str:
        """
        Explain a conflict with the saved recipe of an instance.

        Returns:
            str: That a recipe is already saved under the name, or that it changed after it was read.
        """
        if expected_version is None:
            return f"A saved {kind.value} is already named '{name}'."
        return f"Saved {kind.value} '{name}' was changed after it was read. Reload it and retry."

    def _note_change(self, *, kind: ComponentType, name: str) -> None:
        """
        Drop the restore failure of an instance that was just saved or deleted, and keep the
        reasons of the instances blocked by a reference true now that the registry changed.
        """
        self._unrestorable = [item for item in self._unrestorable if (item.kind, item.name) != (kind, name)]
        self._blocked.pop((kind, name), None)
        self._unrestorable = [self._revisit_blocked(item) for item in self._unrestorable]

    @classmethod
    def _find_unrestored(
        cls, *, reference: InstanceKey, unrestored: Iterable[UnrestorableInstance]
    ) -> UnrestorableInstance | None:
        """
        Find the saved instance a reference names among those a restore could not rebuild.

        A reference names the saved instance stored in the document its name maps to. A document
        listed under its own document name is found that way too, but never by a reference to an
        instance that merely has that name, such as one an initializer registered.

        Returns:
            UnrestorableInstance | None: The saved instance, or ``None`` if the reference names none of them.
        """
        kind, name = reference
        document = InstanceRecipeStorage.get_document_name(kind=kind, name=name)
        return next(
            (item for item in unrestored if item.kind is kind and cls._document_of(item) == document),
            None,
        )

    @staticmethod
    def _document_of(item: UnrestorableInstance) -> str:
        """
        Get the name of the saved document an instance the restore could not rebuild was read from.

        Returns:
            str: Its own name when it is listed under its document name, or the document name its name maps to.
        """
        return InstanceRecipeStorage.get_addressed_document_name(kind=item.kind, name=item.name)

    def _revisit_blocked(self, item: UnrestorableInstance) -> UnrestorableInstance:
        """
        Restate why an instance blocked by a reference was not restored, given what is registered now.

        A reference is satisfied once its name is registered and no saved instance of that name
        failed to restore, because a restore never binds a reference to another instance that took
        the saved one's name.

        Returns:
            UnrestorableInstance: The instance with the first reference still unsatisfied, or with a
            note that a restart or reinitialization can rebuild it; any other instance unchanged.
        """
        references = self._blocked.get((item.kind, item.name))
        if references is None:
            return item
        for reference in sorted(references):
            if self._find_unrestored(reference=reference, unrestored=self._unrestorable) is not None:
                return item.model_copy(update={"reason": describe_dependency_failure(reference)})
            if reference[1] not in self._get_registry(reference[0]).instances:
                return item.model_copy(update={"reason": describe_missing_reference(reference)})
        reason = "Everything it references is registered now. Restart or reinitialize the backend to rebuild it."
        return item.model_copy(update={"reason": reason})

    @staticmethod
    def _unrestorable_from(*, stored: StoredInstanceRecipe, reason: str) -> UnrestorableInstance:
        """
        Describe a saved instance that could not be rebuilt.

        Returns:
            UnrestorableInstance: The instance, its saved version, and the reason.
        """
        return UnrestorableInstance(
            kind=stored.recipe.kind,
            name=stored.recipe.name,
            type=stored.recipe.type,
            reason=reason,
            version=stored.version,
        )

    def _get_parameters(self, *, kind: ComponentType, type_name: str) -> Sequence[Parameter] | None:
        """
        Get the constructor parameters of a registered type.

        Returns:
            Sequence[Parameter] | None: The parameters, or ``None`` if the type is not registered.
        """
        metadata = self._get_registry(kind).get_registered_class_metadata(type_name)
        if metadata is None:
            return None
        parameters: Sequence[Parameter] = metadata.parameters
        return parameters

    def _get_registry(self, kind: ComponentType) -> InstanceHoldingRegistry[Any, Any]:
        """
        Get the current registry of a kind; live apply replaces the registries.

        Returns:
            InstanceHoldingRegistry[Any, Any]: The registry.
        """
        return self._REGISTRY_CLASSES[kind].get_registry_singleton()

    @staticmethod
    def _label(*, kind: ComponentType, name: str) -> str:
        """
        Name an instance in messages.

        Returns:
            str: For example ``Target 'team-chat'``.
        """
        return f"{kind.value.capitalize()} '{name}'"

    @classmethod
    def _labels(cls, keys: Iterable[InstanceKey]) -> str:
        """
        Name several instances in messages, in a stable order.

        Returns:
            str: For example ``Converter 'tone', Target 'pool'``.
        """
        return ", ".join(sorted(cls._label(kind=kind, name=name) for kind, name in keys))


@lru_cache(maxsize=1)
def get_instance_persistence_service() -> InstancePersistenceService:
    """
    Get the shared persistence service.

    Returns:
        InstancePersistenceService: The service.
    """
    return InstancePersistenceService()


async def restore_saved_instances_async() -> None:
    """Rebuild every saved target, converter, and scorer. Failures are recorded and logged, never raised."""
    # Deferred: the kind services import this module.
    from pyrit.backend.services.converter_service import get_converter_service
    from pyrit.backend.services.scorer_service import get_scorer_service
    from pyrit.backend.services.target_service import get_target_service

    try:
        persistence = get_instance_persistence_service()
        try:
            handlers = [get_target_service(), get_converter_service(), get_scorer_service()]
        except Exception as error:
            await persistence.record_restore_failure_async(error=error)
            raise
        await persistence.restore_async(handlers=handlers)
    except Exception:
        logger.exception("Restoring saved instances failed; continuing with the instances registered at startup.")
