# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Rebuild saved registry entries in dependency order and report the ones that can't come back."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.models.catalog.registry_recipe import (
    ComponentRecipe,
    RegistryRecipeKind,
    RegistryRestoreReport,
    RestoreFailure,
    RestoreFailureReason,
    SavedEntryRef,
    StoredRecipe,
    TechniqueRecipe,
)
from pyrit.models.parameter import ComponentType
from pyrit.registry.registry_recipe_storage import RecipeListing, RegistryRecipeStorage

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.models.parameter import Parameter
    from pyrit.registry.registry import InstanceHoldingRegistry


@dataclass(frozen=True)
class _PlannedEntry:
    """A saved entry whose class and references checked out, waiting to be built."""

    stored: StoredRecipe
    dependencies: tuple[SavedEntryRef, ...]


class RegistryRecipeRestorer:
    """
    Rebuild saved targets, converters, scorers, and techniques into their registries.

    Each entry is built after the saved entries it names, with the same calls the API uses to
    create one, so a restored entry passes the same checks. A file never chooses what gets
    imported: classes and attack types are looked up among the ones already registered. Server
    paths are refused as they are for API input, and a recipe never holds a value for a parameter
    its class declares as a credential. Entries that can't come back are reported with the reason
    instead of being skipped.

    Stored files are otherwise trusted the way configuration is. Rules for credentials a class
    reads from server settings, and for fields that can carry a credential without being declared
    one, need each class to declare them, so they arrive with saving and apply to restore too.
    """

    _RESERVED_TECHNIQUE_NAMES: ClassVar[frozenset[str]] = frozenset({"all", "default", "types"})
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
        self._storages = {kind: RegistryRecipeStorage(kind=kind, source=source) for kind in RegistryRecipeKind}

    def restore(self) -> RegistryRestoreReport:
        """
        Rebuild every saved entry that can be rebuilt and register it.

        Call it when the registries hold only startup entries, as they do at startup and after an
        apply resets them; an entry restored earlier would otherwise clash with itself by name. If
        any registry's folder can't be read, nothing is built, since entries in the other folders
        may name entries in the unread one.

        Returns:
            RegistryRestoreReport: What was restored, what failed and why, and which files were ignored.
        """
        listings, failures = self._read_all()
        ignored = [file for listing in listings for file in listing.ignored]
        failures.extend(failure for listing in listings for failure in listing.failures)
        if len(listings) < len(self._storages):
            return RegistryRestoreReport(failures=self._sorted(failures), ignored=ignored)
        stored = {recipe.ref: recipe for listing in listings for recipe in listing.recipes.values()}
        planned = self._plan_all(stored=stored, failures=failures)
        restored = self._build_in_order(planned=planned, failures=failures)
        return RegistryRestoreReport(restored=restored, failures=self._sorted(failures), ignored=ignored)

    def _read_all(self) -> tuple[list[RecipeListing], list[RestoreFailure]]:
        """
        Read every registry's folder, turning a folder that can't be listed into a failure.

        Returns:
            tuple[list[RecipeListing], list[RestoreFailure]]: The folders that were read, and one
                failure per folder that wasn't.
        """
        listings: list[RecipeListing] = []
        failures: list[RestoreFailure] = []
        for kind, storage in self._storages.items():
            try:
                listings.append(storage.list_recipes())
            except Exception as error:  # Any listing error, not only storage ones, must become a failure.
                message = f"The saved {kind.value} couldn't be listed: {storage._describe_read_error(error)}"
                failures.append(
                    RestoreFailure(kind=kind, reason=RestoreFailureReason.UNREADABLE_STORAGE, message=message)
                )
        return listings, failures

    def _plan_all(
        self, *, stored: Mapping[SavedEntryRef, StoredRecipe], failures: list[RestoreFailure]
    ) -> dict[SavedEntryRef, _PlannedEntry]:
        """
        Check every readable recipe's class, credentials, name, and references.

        A reference resolves to a saved entry first, even one that failed to load, so an entry is
        never silently attached to a startup entry that only shares its name.

        Returns:
            dict[SavedEntryRef, _PlannedEntry]: The entries ready to build. Failures are appended.
        """
        saved_refs = set(stored) | {
            SavedEntryRef(kind=failure.kind, name=failure.name) for failure in failures if failure.name is not None
        }
        planned: dict[SavedEntryRef, _PlannedEntry] = {}
        for ref in sorted(stored, key=self._sort_key):
            outcome = self._plan(stored=stored[ref], saved_refs=saved_refs)
            if isinstance(outcome, RestoreFailure):
                failures.append(outcome)
            else:
                planned[ref] = outcome
        return planned

    def _plan(self, *, stored: StoredRecipe, saved_refs: set[SavedEntryRef]) -> _PlannedEntry | RestoreFailure:
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
        except _RecipeRejectedError as rejection:
            return self._failure(stored=stored, reason=rejection.reason, message=str(rejection))
        except Exception as error:  # Planning one entry must never stop the others from being checked.
            return self._failure(
                stored=stored, reason=RestoreFailureReason.INVALID_RECIPE, message=self._safe_message(error)
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
            _RecipeRejectedError: If the class is unknown, moved, or needs a server-chosen upload
                folder, the recipe holds a credential, or the name is already taken.
        """
        registry = self._get_registry(ref.kind)
        try:
            cls = registry.get_class(recipe.class_name)
        except KeyError as error:
            message = f"'{recipe.class_name}' is not a registered {self._singular(ref.kind)} class."
            raise _RecipeRejectedError(reason=RestoreFailureReason.UNKNOWN_CLASS, message=message) from error
        if cls.__module__ != recipe.class_module:
            message = (
                f"'{recipe.class_name}' is registered from '{cls.__module__}', "
                f"but the saved entry was made with the one in '{recipe.class_module}'."
            )
            raise _RecipeRejectedError(reason=RestoreFailureReason.MODULE_MISMATCH, message=message)
        if getattr(cls, "upload_directory_parameter", None) is not None:
            message = (
                f"'{recipe.class_name}' uploads files from a folder the server chooses, which restore can't set yet."
            )
            raise _RecipeRejectedError(reason=RestoreFailureReason.UNSUPPORTED_CLASS, message=message)
        parameters = registry.get_class_metadata(cls).parameters
        self._check_stored_credentials(parameters=parameters, params=recipe.params)
        try:
            registry.instances.validate_name_available(ref.name)
        except ValueError as error:
            raise _RecipeRejectedError(reason=RestoreFailureReason.NAME_CLASH, message=str(error)) from error
        return self._parameter_references(parameters=parameters, params=recipe.params)

    def _technique_references(self, *, ref: SavedEntryRef, recipe: TechniqueRecipe) -> list[SavedEntryRef]:
        """
        Check a technique recipe's name and attack type, and list the entries it names.

        Returns:
            list[SavedEntryRef]: The adversarial target, converters, and attack parameter references.

        Raises:
            _RecipeRejectedError: If the name is reserved, the attack type isn't registered, or the
                recipe holds a credential.
        """
        from pyrit.registry.components.attack_registry import AttackRegistry

        if ref.name.casefold() in self._RESERVED_TECHNIQUE_NAMES:
            message = f"'{ref.name}' is a reserved technique name."
            raise _RecipeRejectedError(reason=RestoreFailureReason.INVALID_RECIPE, message=message)
        attack_registry = AttackRegistry.get_registry_singleton()
        try:
            attack_class = attack_registry.get_class(recipe.type)
        except KeyError as error:
            message = f"'{recipe.type}' is not a registered attack type."
            raise _RecipeRejectedError(reason=RestoreFailureReason.UNKNOWN_CLASS, message=message) from error
        converters = [*(recipe.request_converters or []), *(recipe.response_converters or [])]
        references = [SavedEntryRef(kind=RegistryRecipeKind.CONVERTERS, name=name) for name in converters]
        if recipe.adversarial_chat is not None:
            references.append(SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name=recipe.adversarial_chat))
        parameters = attack_registry.get_class_metadata(attack_class).parameters
        self._check_stored_credentials(parameters=parameters, params=recipe.params)
        return references + self._parameter_references(parameters=parameters, params=recipe.params)

    @staticmethod
    def _check_stored_credentials(*, parameters: Sequence[Parameter], params: Mapping[str, Any]) -> None:
        """
        Refuse a recipe that stores a value for a parameter its class declares as a credential.

        Raises:
            _RecipeRejectedError: If a declared credential parameter has a value.
        """
        stored = sorted(
            parameter.name
            for parameter in parameters
            if parameter.sensitive and params.get(parameter.name) not in (None, "")
        )
        if stored:
            raise _RecipeRejectedError(
                reason=RestoreFailureReason.UNSUPPORTED_CREDENTIAL,
                message=f"Saved entries can't hold credentials ('{stored[0]}').",
            )

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

    def _build_in_order(
        self, *, planned: dict[SavedEntryRef, _PlannedEntry], failures: list[RestoreFailure]
    ) -> list[SavedEntryRef]:
        """
        Build entries once everything they depend on has been settled.

        Returns:
            list[SavedEntryRef]: The entries that were rebuilt and registered. Failures are appended.
        """
        failed = {SavedEntryRef(kind=failure.kind, name=failure.name) for failure in failures if failure.name}
        restored: list[SavedEntryRef] = []
        pending = dict(planned)
        ready = self._ready(pending=pending, settled={*failed})
        while ready:
            for ref in ready:
                entry = pending.pop(ref)
                failure = self._failed_dependency(entry=entry, failed=failed) or self._build(stored=entry.stored)
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

    def _build(self, *, stored: StoredRecipe) -> RestoreFailure | None:
        """
        Build one entry and register it.

        Returns:
            RestoreFailure | None: Why the entry couldn't be built or registered, or None on success.
        """
        recipe = stored.recipe
        try:
            if isinstance(recipe, TechniqueRecipe):
                self._build_technique(name=stored.ref.name, recipe=recipe)
            else:
                self._get_registry(stored.ref.kind).create_named_instance(
                    name=stored.ref.name, type_name=recipe.class_name, params=recipe.params, external_input=True
                )
        except _RecipeRejectedError as rejection:
            return self._failure(stored=stored, reason=rejection.reason, message=str(rejection))
        except Exception as error:  # A constructor can raise anything; one bad entry must not stop the rest.
            return self._failure(
                stored=stored, reason=RestoreFailureReason.BUILD_ERROR, message=self._safe_message(error)
            )
        return None

    @staticmethod
    def _build_technique(*, name: str, recipe: TechniqueRecipe) -> None:
        """
        Build a technique with the call the technique API uses, then register it.

        Raises:
            _RecipeRejectedError: If the name or a tag conflicts with a registered technique.
        """
        from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry

        registry = AttackTechniqueRegistry.get_registry_singleton()
        args = recipe.model_dump(exclude_unset=True, exclude={"schema_version", "type", "tags"})
        factory = registry.create_factory(
            name=name, attack_type=recipe.type, technique_tags=recipe.tags, strict_json=True, **args
        )
        try:
            registry.instances.register_runtime(factory)
        except ValueError as error:
            raise _RecipeRejectedError(reason=RestoreFailureReason.NAME_CLASH, message=str(error)) from error

    def _get_registry(self, kind: RegistryRecipeKind) -> InstanceHoldingRegistry[Any, Any]:
        """
        Get the registry singleton that holds saved targets, converters, or scorers.

        Returns:
            InstanceHoldingRegistry[Any, Any]: The registry.
        """
        from pyrit.registry.components.converter_registry import ConverterRegistry
        from pyrit.registry.components.scorer_registry import ScorerRegistry
        from pyrit.registry.components.target_registry import TargetRegistry

        registries = {
            RegistryRecipeKind.TARGETS: TargetRegistry,
            RegistryRecipeKind.CONVERTERS: ConverterRegistry,
            RegistryRecipeKind.SCORERS: ScorerRegistry,
        }
        return registries[kind].get_registry_singleton()

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

        Returns:
            RestoreFailure: The failure, carrying the stored document's version.
        """
        return RestoreFailure(
            kind=stored.ref.kind,
            name=stored.ref.name,
            reason=reason,
            message=message,
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
        Describe an error for the failure report without any credential it may quote.

        Constructor errors can quote a server setting, such as an endpoint with a SAS token. The text
        is cut at the first ``?`` or ``#``, where a URL's query or fragment starts. A user name and
        password can't be told apart reliably, so text with an ``@`` isn't shown at all.

        Returns:
            str: The error's type and its text before any ``?`` or ``#``, or the type alone.
        """
        name = type(error).__name__
        try:
            text = str(error)
        except Exception:
            return name
        if "@" in text:
            return f"{name} (message hidden because it may include a credential)"
        cut = text.split("?", 1)[0].split("#", 1)[0]
        return f"{name}: {cut}" if cut else name


class _RecipeRejectedError(Exception):
    """A saved recipe failed a check before or while it was built."""

    def __init__(self, *, reason: RestoreFailureReason, message: str) -> None:
        """Initialize the error with the reason to report."""
        super().__init__(message)
        self.reason = reason
