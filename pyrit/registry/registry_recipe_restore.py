# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Rebuild saved registry entries in dependency order and report the ones that can't come back."""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common.azure_storage import redact_urls
from pyrit.models import ComponentType
from pyrit.models.catalog import (
    ComponentRecipe,
    RegistryRecipeKind,
    RegistryRestoreReport,
    RestoreFailure,
    RestoreFailureReason,
    SavedEntryRef,
    StoredRecipe,
    TechniqueRecipe,
)
from pyrit.registry.file_document_storage import DocumentStorageUnavailableError
from pyrit.registry.instance_registry import RegistryNameTakenError, saved_entry_registration
from pyrit.registry.registry_recipe_rules import (
    SavedEntryRefusedError,
    check_saved_copy_params,
    check_saved_entry_params,
)
from pyrit.registry.registry_recipe_storage import RecipeListing, RegistryRecipeStorage, safe_failure_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.models import Parameter
    from pyrit.registry.registry import InstanceHoldingRegistry, Registry
    from pyrit.registry.registry_metadata import RegistryMetadata

logger = logging.getLogger(__name__)

# Identifies a failure for the log: one saved entry, or a whole folder when the name is None.
_FailureKey = tuple[RegistryRecipeKind, str | None]

# Builds one saved entry and registers it under its name.
RecipeBuilder = Callable[[StoredRecipe], Awaitable[None]]


@dataclass(frozen=True)
class SavedRecipes:
    """What reading the saved recipes found: each folder that was read, and a failure for each one that wasn't."""

    listings: tuple[RecipeListing, ...]
    failures: tuple[RestoreFailure, ...]
    # The storage error behind each folder that couldn't be listed, for the log only.
    causes: Mapping[RegistryRecipeKind, BaseException] = field(
        default_factory=lambda: MappingProxyType({}), compare=False, repr=False
    )


@dataclass(frozen=True)
class _PlannedEntry:
    """A saved entry whose class and references checked out, waiting to be built."""

    stored: StoredRecipe
    dependencies: tuple[SavedEntryRef, ...]


class RegistryRecipeRestorer:
    """
    Rebuild saved targets, converters, scorers, and techniques into their registries.

    This class reads the saved files, checks them, and works out the order to build them in: each entry
    is built after the saved entries it names. The caller supplies the builder, so an entry is built the
    way the caller's create builds one; the backend's builder calls the API's create for each kind. A
    file never chooses what gets imported: classes and attack types are looked up among the ones already
    registered. Server paths are refused as they are for API input, and ``check_saved_entry_params``
    refuses a recipe that stores a credential, that would let its entry use one from this server, or, for
    a target, that would send requests where this server's settings say. Entries that can't come back are
    reported with the reason instead of being skipped.

    Stored files are otherwise trusted the way configuration is.
    """

    _KIND_BY_COMPONENT_TYPE: ClassVar[dict[ComponentType, RegistryRecipeKind]] = {
        ComponentType.TARGET: RegistryRecipeKind.TARGETS,
        ComponentType.CONVERTER: RegistryRecipeKind.CONVERTERS,
        ComponentType.SCORER: RegistryRecipeKind.SCORERS,
    }
    _KIND_ORDER: ClassVar[tuple[RegistryRecipeKind, ...]] = tuple(RegistryRecipeKind)

    def __init__(self, *, source: str | None = None) -> None:
        """
        Initialize the restorer.

        Args:
            source (str | None): Local directory or Azure Blob URI holding the per-registry folders.
                Defaults to ``registry_recipes`` under the PyRIT configuration directory.
        """
        from pyrit.registry.components.converter_registry import ConverterRegistry
        from pyrit.registry.components.scorer_registry import ScorerRegistry
        from pyrit.registry.components.target_registry import TargetRegistry

        self._storages = {kind: RegistryRecipeStorage(kind=kind, source=source) for kind in RegistryRecipeKind}
        self._registry_types: dict[RegistryRecipeKind, Any] = {
            RegistryRecipeKind.TARGETS: TargetRegistry,
            RegistryRecipeKind.CONVERTERS: ConverterRegistry,
            RegistryRecipeKind.SCORERS: ScorerRegistry,
        }

    def read(self) -> SavedRecipes:
        """
        Read every registry's folder without building anything.

        Reading can wait on storage, so a caller on an event loop can run this in a worker thread and
        pass the result to ``restore_async`` on the loop. A folder that can't be listed becomes a failure; any
        other error is a bug rather than a problem with the files, so it isn't caught.

        Returns:
            SavedRecipes: The folders that were read, and a failure for each one that wasn't.
        """
        listings: list[RecipeListing] = []
        failures: list[RestoreFailure] = []
        causes: dict[RegistryRecipeKind, BaseException] = {}
        for kind, storage in self._storages.items():
            try:
                listings.append(storage.list_recipes())
            except DocumentStorageUnavailableError as error:
                message = safe_failure_text(f"The saved {kind.value} couldn't be listed: {error.reason}")
                failures.append(
                    RestoreFailure(kind=kind, reason=RestoreFailureReason.UNREADABLE_STORAGE, message=message)
                )
                causes[kind] = error
        return SavedRecipes(listings=tuple(listings), failures=tuple(failures), causes=MappingProxyType(causes))

    async def restore_async(
        self, *, build_async: RecipeBuilder, saved: SavedRecipes | None = None
    ) -> RegistryRestoreReport:
        """
        Rebuild every saved entry that can be rebuilt and register it.

        Entries are built one at a time, in dependency order, on the calling event loop, because a
        constructor can need the running loop. Call it when the registries hold only startup entries, as
        they do at startup and after an apply resets them; an entry restored earlier would otherwise clash
        with itself by name. If any registry's folder can't be read, nothing is built, since entries
        in the other folders may name entries in the unread one.

        Each failure is logged once, with the error behind it, if any, and URL credentials removed.

        Args:
            build_async (RecipeBuilder): Builds one checked entry and registers it under its name. It raises
                ``RecipeRejectedError`` to report a reason; any other error is reported as a build error.
            saved (SavedRecipes | None): What ``read`` returned. Defaults to reading now, in a worker thread.

        Returns:
            RegistryRestoreReport: What was restored, what failed and why, and which files were ignored.
        """
        saved = saved if saved is not None else await asyncio.to_thread(self.read)
        listings, failures = list(saved.listings), list(saved.failures)
        causes: dict[_FailureKey, BaseException] = {(kind, None): error for kind, error in saved.causes.items()}
        for listing in listings:
            failures.extend(listing.failures)
            for failure in listing.failures:
                cause = listing.causes.get(failure.name) if failure.name is not None else None
                if cause is not None:
                    causes[(failure.kind, failure.name)] = cause
        ignored = [file for listing in listings for file in listing.ignored]
        if saved.failures:
            report = RegistryRestoreReport(failures=self._sorted(failures), ignored=ignored)
        else:
            stored = {recipe.ref: recipe for listing in listings for recipe in listing.recipes.values()}
            planned = self._plan_all(stored=stored, failures=failures, causes=causes)
            restored = await self._build_in_order_async(
                planned=planned, build_async=build_async, failures=failures, causes=causes
            )
            report = RegistryRestoreReport(restored=restored, failures=self._sorted(failures), ignored=ignored)
        self._log_failures(failures=report.failures, causes=causes)
        return report

    def _log_failures(self, *, failures: Sequence[RestoreFailure], causes: Mapping[_FailureKey, BaseException]) -> None:
        """Log each failure once, with the error behind it, if any, and URL credentials removed."""
        for failure in failures:
            subject = (
                f"the saved {failure.kind.value}"
                if failure.name is None
                else self._describe_ref(SavedEntryRef(kind=failure.kind, name=failure.name))
            )
            summary = f"Couldn't restore {subject} ({failure.reason.value}): {failure.message}"
            cause = causes.get((failure.kind, failure.name))
            if cause is None:
                logger.warning(summary)
            else:
                logger.warning(f"{summary}\n{redact_urls(''.join(traceback.format_exception(cause))).rstrip()}")

    def _plan_all(
        self,
        *,
        stored: Mapping[SavedEntryRef, StoredRecipe],
        failures: list[RestoreFailure],
        causes: dict[_FailureKey, BaseException],
    ) -> dict[SavedEntryRef, _PlannedEntry]:
        """
        Check every readable recipe's class, credentials, name, and references.

        A reference resolves to a saved entry first, even one that failed to load, so an entry is
        never silently attached to a startup entry that only shares its name.

        Returns:
            dict[SavedEntryRef, _PlannedEntry]: The entries ready to build. Failures and their errors are added.
        """
        saved_refs = set(stored) | {
            SavedEntryRef(kind=failure.kind, name=failure.name) for failure in failures if failure.name is not None
        }
        planned: dict[SavedEntryRef, _PlannedEntry] = {}
        for ref in sorted(stored, key=self._sort_key):
            outcome = self._plan(stored=stored[ref], saved_refs=saved_refs, causes=causes)
            if isinstance(outcome, RestoreFailure):
                failures.append(outcome)
            else:
                planned[ref] = outcome
        return planned

    def _plan(
        self,
        *,
        stored: StoredRecipe,
        saved_refs: set[SavedEntryRef],
        causes: dict[_FailureKey, BaseException],
    ) -> _PlannedEntry | RestoreFailure:
        """
        Check one recipe and collect the saved entries it depends on.

        Returns:
            _PlannedEntry | RestoreFailure: The entry ready to build, or why it can't be.
        """
        recipe = stored.recipe
        try:
            references = (
                self._technique_references(ref=stored.ref, recipe=recipe)
                if isinstance(recipe, TechniqueRecipe)
                else self._component_references(ref=stored.ref, recipe=recipe)
            )
        except (RecipeRejectedError, SavedEntryRefusedError) as rejection:
            return self._failure(stored=stored, reason=rejection.reason, message=str(rejection))
        except Exception as error:  # A bug here must not stop the others from being checked, or blame the file.
            causes[(stored.ref.kind, stored.ref.name)] = error
            return self._failure(
                stored=stored, reason=RestoreFailureReason.BUILD_ERROR, message=self._safe_message(error)
            )
        dependencies: list[SavedEntryRef] = []
        for reference in references:
            if reference in saved_refs:
                dependencies.append(reference)
            elif reference.name not in self._get_registry(reference.kind).instances:
                message = f"Uses {self._describe_ref(reference)}, which doesn't exist."
                return self._failure(
                    stored=stored,
                    reason=RestoreFailureReason.MISSING_DEPENDENCY,
                    message=message,
                    dependency=reference,
                )
        return _PlannedEntry(stored=stored, dependencies=tuple(dict.fromkeys(dependencies)))

    def _component_references(self, *, ref: SavedEntryRef, recipe: ComponentRecipe) -> list[SavedEntryRef]:
        """
        Check a target, converter, or scorer recipe and list the entries its parameters name.

        Returns:
            list[SavedEntryRef]: The referenced entries, in parameter order.

        Raises:
            RecipeRejectedError: If the class is unknown or is now one from outside PyRIT, the recipe sets a
                server file or folder, or the name is taken or can't be used.
            SavedEntryRefusedError: If the class can't be saved, or the recipe holds or would use a credential.
        """
        registry = self._get_registry(ref.kind)
        try:
            cls = registry.get_class(recipe.class_name)
        except KeyError as error:
            message = f"'{recipe.class_name}' is not a registered {self._singular(ref.kind)} class."
            raise RecipeRejectedError(reason=RestoreFailureReason.UNKNOWN_CLASS, message=message) from error
        # Classes are found by registered name, so one that moved within PyRIT is still the same class.
        moved_within_pyrit = _is_pyrit_module(cls.__module__) and _is_pyrit_module(recipe.class_module)
        if cls.__module__ != recipe.class_module and not moved_within_pyrit:
            message = (
                f"'{recipe.class_name}' is registered from '{cls.__module__}', "
                f"but the saved entry was made with the one in '{recipe.class_module}'."
            )
            raise RecipeRejectedError(reason=RestoreFailureReason.MODULE_MISMATCH, message=message)
        parameters = self._class_parameters(registry=registry, name=recipe.class_name, cls=cls)
        if recipe.source is None:
            check_saved_entry_params(cls=cls, parameters=parameters, params=recipe.params, auth_mode=recipe.auth_mode)
        else:
            # A copy takes everything else, including where it sends requests and how it signs in, from its source.
            check_saved_copy_params(parameters=parameters, params=recipe.params)
        # As in the target API, a target may let callers set some path parameters, such as a working folder.
        allowed_paths = (
            getattr(cls, "caller_path_parameters", frozenset())
            if ref.kind is RegistryRecipeKind.TARGETS
            else frozenset()
        )
        server_paths = sorted(
            parameter.name
            for parameter in parameters
            if parameter.mentions_path
            and parameter.name not in allowed_paths
            and recipe.params.get(parameter.name) not in (None, "")
        )
        if server_paths:
            message = (
                f"'{recipe.class_name}' reads a server file or folder from '{server_paths[0]}', "
                "which a saved entry can't set."
            )
            raise RecipeRejectedError(reason=RestoreFailureReason.UNSUPPORTED_CLASS, message=message)
        try:
            registry.instances.validate_name_available(ref.name)
        except RegistryNameTakenError as error:
            raise RecipeRejectedError(reason=RestoreFailureReason.NAME_CLASH, message=str(error)) from error
        except ValueError as error:  # A reserved name, which no entry of this kind can have.
            raise RecipeRejectedError(reason=RestoreFailureReason.INVALID_RECIPE, message=str(error)) from error
        references = self._parameter_references(parameters=parameters, params=recipe.params)
        if recipe.source is not None:
            references.insert(0, SavedEntryRef(kind=ref.kind, name=recipe.source))
        return references

    def _technique_references(self, *, ref: SavedEntryRef, recipe: TechniqueRecipe) -> list[SavedEntryRef]:
        """
        Check a technique recipe's attack type, and list the entries it names.

        The builder checks the name, as the technique API does.

        Returns:
            list[SavedEntryRef]: The adversarial target, converters, and attack parameter references.

        Raises:
            RecipeRejectedError: If the attack type isn't registered.
            SavedEntryRefusedError: If the recipe holds a credential.
        """
        from pyrit.registry.components.attack_registry import AttackRegistry

        attack_registry = AttackRegistry.get_registry_singleton()
        try:
            attack_class = attack_registry.get_class(recipe.type)
        except KeyError as error:
            message = f"'{recipe.type}' is not a registered attack type."
            raise RecipeRejectedError(reason=RestoreFailureReason.UNKNOWN_CLASS, message=message) from error
        converters = [*(recipe.request_converters or []), *(recipe.response_converters or [])]
        references = [SavedEntryRef(kind=RegistryRecipeKind.CONVERTERS, name=name) for name in converters]
        if recipe.adversarial_chat is not None:
            references.append(SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name=recipe.adversarial_chat))
        parameters = self._class_parameters(registry=attack_registry, name=recipe.type, cls=attack_class)
        check_saved_entry_params(cls=attack_class, parameters=parameters, params=recipe.params)
        return references + self._parameter_references(parameters=parameters, params=recipe.params)

    @staticmethod
    def _class_parameters(*, registry: Registry[Any, Any], name: str, cls: type) -> tuple[Parameter, ...]:
        """
        Get a registered class's constructor parameters from the registry's cached metadata.

        Returns:
            tuple[Parameter, ...]: The parameters the class declares.
        """
        metadata: RegistryMetadata = registry.get_registered_class_metadata(name) or registry.get_class_metadata(cls)
        return metadata.parameters

    def _parameter_references(
        self, *, parameters: Sequence[Parameter], params: Mapping[str, Any]
    ) -> list[SavedEntryRef]:
        """
        List the entries that supplied reference parameters name.

        An empty name leaves the reference unset, as it does when the API builds an entry.

        Returns:
            list[SavedEntryRef]: One reference per named entry of a kind that can be saved.
        """
        references: list[SavedEntryRef] = []
        for parameter in parameters:
            if parameter.reference is None or params.get(parameter.name) in (None, ""):
                continue
            kind = self._KIND_BY_COMPONENT_TYPE.get(parameter.reference.component_type)
            value = params[parameter.name]
            names = value if isinstance(value, list) else [value]
            if kind is not None:
                references.extend(SavedEntryRef(kind=kind, name=name) for name in names if isinstance(name, str))
        return references

    async def _build_in_order_async(
        self,
        *,
        planned: dict[SavedEntryRef, _PlannedEntry],
        build_async: RecipeBuilder,
        failures: list[RestoreFailure],
        causes: dict[_FailureKey, BaseException],
    ) -> list[SavedEntryRef]:
        """
        Build entries once everything they depend on has been settled.

        Returns:
            list[SavedEntryRef]: The entries that were rebuilt and registered. Failures and their errors are added.
        """
        failed = {SavedEntryRef(kind=failure.kind, name=failure.name) for failure in failures if failure.name}
        restored: list[SavedEntryRef] = []
        pending = dict(planned)
        ready = self._ready(pending=pending, settled={*failed})
        while ready:
            for ref in ready:
                entry = pending.pop(ref)
                failure = self._failed_dependency(entry=entry, failed=failed) or await self._build_async(
                    stored=entry.stored, build_async=build_async, causes=causes
                )
                if failure is None:
                    restored.append(ref)
                else:
                    failures.append(failure)
                    failed.add(ref)
            ready = self._ready(pending=pending, settled={*failed, *restored})
        failures.extend(self._unresolved(pending=pending))
        return restored

    def _ready(
        self, *, pending: Mapping[SavedEntryRef, _PlannedEntry], settled: set[SavedEntryRef]
    ) -> list[SavedEntryRef]:
        """
        List the pending entries whose dependencies have all been built or have failed.

        Returns:
            list[SavedEntryRef]: The entries to handle next, in a stable order.
        """
        ready = [ref for ref, entry in pending.items() if settled.issuperset(entry.dependencies)]
        return sorted(ready, key=self._sort_key)

    def _failed_dependency(self, *, entry: _PlannedEntry, failed: set[SavedEntryRef]) -> RestoreFailure | None:
        """
        Fail an entry if a saved entry it depends on couldn't be restored.

        Returns:
            RestoreFailure | None: The failure, or None if every dependency was restored.
        """
        dependency = next((ref for ref in entry.dependencies if ref in failed), None)
        if dependency is None:
            return None
        return self._failure(
            stored=entry.stored,
            reason=RestoreFailureReason.FAILED_DEPENDENCY,
            message=f"Uses {self._describe_ref(dependency)}, which couldn't be restored.",
            dependency=dependency,
        )

    def _unresolved(self, *, pending: Mapping[SavedEntryRef, _PlannedEntry]) -> list[RestoreFailure]:
        """
        Fail the entries left waiting: those on a dependency loop, and those waiting on one.

        Returns:
            list[RestoreFailure]: One failure per entry that was never built.
        """
        failures: list[RestoreFailure] = []
        for ref in sorted(pending, key=self._sort_key):
            entry = pending[ref]
            waiting_on = [dependency for dependency in entry.dependencies if dependency in pending]
            on_loop = [
                dependency
                for dependency in waiting_on
                if dependency == ref or self._reaches(start=dependency, target=ref, pending=pending)
            ]
            blocker = (on_loop or waiting_on)[0]
            if on_loop:
                reason = RestoreFailureReason.DEPENDENCY_LOOP
                message = f"Saved entries use each other in a loop, through {self._describe_ref(blocker)}."
            else:
                reason = RestoreFailureReason.FAILED_DEPENDENCY
                message = f"Uses {self._describe_ref(blocker)}, which is waiting on a loop of saved entries."
            failures.append(self._failure(stored=entry.stored, reason=reason, message=message, dependency=blocker))
        return failures

    @staticmethod
    def _reaches(
        *, start: SavedEntryRef, target: SavedEntryRef, pending: Mapping[SavedEntryRef, _PlannedEntry]
    ) -> bool:
        """
        Return whether following pending dependencies from ``start`` leads to ``target``.

        Returns:
            bool: True if ``target`` can be reached.
        """
        stack = [dependency for dependency in pending[start].dependencies if dependency in pending]
        seen: set[SavedEntryRef] = set()
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(dependency for dependency in pending[current].dependencies if dependency in pending)
        return False

    async def _build_async(
        self, *, stored: StoredRecipe, build_async: RecipeBuilder, causes: dict[_FailureKey, BaseException]
    ) -> RestoreFailure | None:
        """
        Build one entry with the caller's builder.

        Returns:
            RestoreFailure | None: Why the entry couldn't be built or registered, or None on success.
        """
        from pyrit.registry.components.attack_technique_registry import TechniqueAdmissionError

        try:
            owner = stored.recipe.owner if isinstance(stored.recipe, ComponentRecipe) else None
            with saved_entry_registration(kind=stored.ref.kind.value, name=stored.ref.name, owner=owner):
                await build_async(stored)
        except (RecipeRejectedError, SavedEntryRefusedError) as rejection:
            return self._failure(stored=stored, reason=rejection.reason, message=str(rejection))
        except RegistryNameTakenError as error:
            return self._failure(stored=stored, reason=RestoreFailureReason.NAME_CLASH, message=str(error))
        except TechniqueAdmissionError as error:
            return self._failure(stored=stored, reason=RestoreFailureReason.INVALID_RECIPE, message=str(error))
        except Exception as error:  # A constructor can raise anything; one bad entry must not stop the rest.
            causes[(stored.ref.kind, stored.ref.name)] = error
            return self._failure(
                stored=stored, reason=RestoreFailureReason.BUILD_ERROR, message=self._safe_message(error)
            )
        return None

    def _get_registry(self, kind: RegistryRecipeKind) -> InstanceHoldingRegistry[Any, Any]:
        """
        Get the registry singleton that holds saved targets, converters, or scorers.

        Returns:
            InstanceHoldingRegistry[Any, Any]: The registry.
        """
        return self._registry_types[kind].get_registry_singleton()

    def _failure(
        self,
        *,
        stored: StoredRecipe,
        reason: RestoreFailureReason,
        message: str,
        dependency: SavedEntryRef | None = None,
    ) -> RestoreFailure:
        """
        Build a failure for one saved entry.

        The message can repeat stored content, such as a class name or a parameter key, so it's escaped and
        shortened.

        Returns:
            RestoreFailure: The failure, carrying the stored document's version.
        """
        return RestoreFailure(
            kind=stored.ref.kind,
            name=stored.ref.name,
            reason=reason,
            message=safe_failure_text(message),
            version=stored.version,
            dependency=dependency,
        )

    def _sort_key(self, ref: SavedEntryRef) -> tuple[int, str]:
        """
        Order entries by registry, then by name, so restores are repeatable.

        Returns:
            tuple[int, str]: The sort key.
        """
        return self._KIND_ORDER.index(ref.kind), ref.name

    def _sorted(self, failures: list[RestoreFailure]) -> list[RestoreFailure]:
        """
        Order failures by registry, then by name, with whole-folder failures first.

        Returns:
            list[RestoreFailure]: The sorted failures.
        """
        return sorted(failures, key=lambda failure: (self._KIND_ORDER.index(failure.kind), failure.name or ""))

    def _describe_ref(self, ref: SavedEntryRef) -> str:
        """
        Describe an entry for a message, such as "target 'contoso_gpt4o'".

        Returns:
            str: The description.
        """
        return f"{self._singular(ref.kind)} '{ref.name}'"

    @staticmethod
    def _singular(kind: RegistryRecipeKind) -> str:
        """
        Name one entry of a kind, such as "target".

        Returns:
            str: The singular name.
        """
        return kind.value.removesuffix("s")

    @staticmethod
    def _safe_message(error: Exception) -> str:
        """
        Describe an error for the failure report: its type and text, without URL credentials.

        Stored files are trusted the way configuration is, so the text is shown as it is, except that each
        URL in it loses its user name and password, query, and fragment, which can hold a credential such
        as a SAS token. ``_failure`` escapes and shortens it; the log has the whole error.

        Returns:
            str: The error's type and text.
        """
        name = type(error).__name__
        try:
            text = str(error)
        except Exception:
            return name
        return f"{name}: {redact_urls(text)}" if text else name


def _is_pyrit_module(module: str) -> bool:
    """
    Return whether a module is part of PyRIT.

    Returns:
        bool: True for ``pyrit`` and its submodules.
    """
    return module == "pyrit" or module.startswith("pyrit.")


class RecipeRejectedError(Exception):
    """A saved recipe failed a check before or while it was built, for the reason it carries."""

    def __init__(self, *, reason: RestoreFailureReason, message: str) -> None:
        """Initialize the error with the reason to report."""
        super().__init__(message)
        self.reason = reason
