# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Plan the order in which saved targets, converters, and scorers are rebuilt."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from pyrit.models.catalog.instance_recipe import InstanceRecipe, StoredInstanceRecipe, UnrestorableInstance
from pyrit.models.parameter import ComponentType

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from pyrit.models.parameter import Parameter

InstanceKey = tuple[ComponentType, str]


def get_recipe_references(*, recipe: InstanceRecipe, parameters: Sequence[Parameter]) -> set[InstanceKey]:
    """
    Get the instances a recipe names through its registry-reference parameters.

    Args:
        recipe (InstanceRecipe): The saved recipe.
        parameters (Sequence[Parameter]): The constructor parameters of the recipe's type.

    Returns:
        set[InstanceKey]: The kind and name of every referenced instance.
    """
    references: set[InstanceKey] = set()
    for parameter in parameters:
        if parameter.reference is None:
            continue
        value = recipe.params.get(parameter.name)
        names = value if isinstance(value, list) else [value]
        references.update((parameter.reference.component_type, name) for name in names if isinstance(name, str))
    return references


def describe_dependency_failure(dependency: InstanceKey) -> str:
    """
    Explain that an instance cannot be rebuilt because one it references was not.

    Returns:
        str: The reason.
    """
    return f"It depends on {dependency[0].value} '{dependency[1]}', which could not be restored."


def describe_missing_reference(reference: InstanceKey) -> str:
    """
    Explain that an instance cannot be rebuilt because one it references is not registered.

    Returns:
        str: The reason.
    """
    return f"It references {reference[0].value} '{reference[1]}', which is not registered."


@dataclass(frozen=True)
class PlannedInstance:
    """A saved recipe to rebuild, with the saved recipes that must be rebuilt first."""

    stored: StoredInstanceRecipe
    dependencies: frozenset[InstanceKey]


@dataclass(frozen=True)
class InstanceRestorePlan:
    """
    Saved recipes in a buildable order, and the ones that cannot be rebuilt.

    ``blocked`` maps each recipe that cannot be rebuilt only because of an instance it
    references to everything it references, so its reason can be revisited once those change.
    """

    ordered: list[PlannedInstance] = field(default_factory=list)
    unrestorable: list[UnrestorableInstance] = field(default_factory=list)
    blocked: dict[InstanceKey, frozenset[InstanceKey]] = field(default_factory=dict)


class InstanceRestorePlanner:
    """
    Order saved recipes so every instance is rebuilt after the instances it names.

    References resolve by name at construction time, so a recipe must be rebuilt after
    any saved recipe it references. A reference to an instance that is already
    registered (for example by an initializer) needs no ordering. A saved recipe whose
    name is already registered is not rebuilt, and recipes that reference it are not
    silently bound to the other instance; neither are recipes that reference a saved
    instance whose document cannot be used. Ordering is deterministic: targets, then
    converters, then scorers, each by name.
    """

    _KIND_ORDER: ClassVar[dict[ComponentType, int]] = {
        ComponentType.TARGET: 0,
        ComponentType.CONVERTER: 1,
        ComponentType.SCORER: 2,
    }

    def __init__(
        self,
        *,
        recipes: Sequence[StoredInstanceRecipe],
        is_registered: Callable[[ComponentType, str], bool],
        get_parameters: Callable[[InstanceRecipe], Sequence[Parameter] | None],
        is_unavailable: Callable[[ComponentType, str], bool] | None = None,
    ) -> None:
        """
        Initialize the planner.

        Args:
            recipes (Sequence[StoredInstanceRecipe]): The saved recipes.
            is_registered (Callable[[ComponentType, str], bool]): Whether a name is already
                registered in the registry of the given kind.
            get_parameters (Callable[[InstanceRecipe], Sequence[Parameter] | None]): The
                constructor parameters of a recipe's type, or ``None`` if the type is unknown.
            is_unavailable (Callable[[ComponentType, str], bool] | None): Whether a name has a
                saved document that cannot be used as a recipe, such as one a newer PyRIT saved.
        """
        self._recipes = {(stored.recipe.kind, stored.recipe.name): stored for stored in recipes}
        self._is_registered = is_registered
        self._get_parameters = get_parameters
        self._is_unavailable = is_unavailable or (lambda kind, name: False)
        self._unavailable: set[InstanceKey] = set()
        self._failures: dict[InstanceKey, str] = {}
        self._dependencies: dict[InstanceKey, set[InstanceKey]] = {}
        self._references: dict[InstanceKey, frozenset[InstanceKey]] = {}
        self._blocked: set[InstanceKey] = set()

    def plan(self) -> InstanceRestorePlan:
        """
        Build the restore plan.

        Returns:
            InstanceRestorePlan: The buildable order and the recipes that cannot be rebuilt.
        """
        for key in self._sorted(self._recipes):
            self._check_recipe(key)
        self._propagate_failures()
        ordered = self._order()
        self._report_cycles(set(self._recipes) - set(self._failures) - set(ordered))
        return InstanceRestorePlan(
            ordered=[
                PlannedInstance(stored=self._recipes[key], dependencies=frozenset(self._dependencies[key]))
                for key in ordered
            ],
            unrestorable=[self._unrestorable(key) for key in self._sorted(self._failures)],
            blocked={key: self._references[key] for key in self._sorted(self._blocked)},
        )

    def _check_recipe(self, key: InstanceKey) -> None:
        """Record why a recipe cannot be rebuilt, or the saved recipes it depends on."""
        recipe = self._recipes[key].recipe
        if self._is_registered(*key):
            self._failures[key] = (
                f"The name is already registered (for example by an initializer), so the saved {key[0].value} "
                "was not restored."
            )
            return
        parameters = self._get_parameters(recipe)
        if parameters is None:
            self._failures[key] = f"Type '{recipe.type}' is not registered."
            return
        self._dependencies[key] = set()
        self._references[key] = frozenset(get_recipe_references(recipe=recipe, parameters=parameters))
        for reference in self._sorted(self._references[key]):
            if reference in self._recipes:
                self._dependencies[key].add(reference)
            elif self._is_unavailable(*reference):
                self._dependencies[key].add(reference)
                self._unavailable.add(reference)
            elif not self._is_registered(*reference):
                self._failures[key] = describe_missing_reference(reference)
                self._blocked.add(key)
                return

    def _propagate_failures(self) -> None:
        """Mark every recipe that depends on one that cannot be rebuilt."""
        changed = True
        while changed:
            changed = False
            for key in self._sorted(self._dependencies):
                if key in self._failures:
                    continue
                failed = next(
                    (
                        item
                        for item in self._sorted(self._dependencies[key])
                        if item in self._failures or item in self._unavailable
                    ),
                    None,
                )
                if failed is not None:
                    self._failures[key] = describe_dependency_failure(failed)
                    self._blocked.add(key)
                    changed = True

    def _order(self) -> list[InstanceKey]:
        """
        Order the buildable recipes so dependencies come first.

        Returns:
            list[InstanceKey]: The buildable recipes in order; recipes on or behind a cycle are left out.
        """
        pending = {key: set(self._dependencies[key]) for key in self._dependencies if key not in self._failures}
        ordered: list[InstanceKey] = []
        while ready := [key for key in pending if not pending[key]]:
            key = min(ready, key=self._sort_key)
            ordered.append(key)
            del pending[key]
            for remaining in pending.values():
                remaining.discard(key)
        return ordered

    def _report_cycles(self, leftover: set[InstanceKey]) -> None:
        """Explain each recipe left unordered: a member of a reference cycle, or behind one."""
        members = {key: path for key in leftover if (path := self._find_cycle(start=key, within=leftover))}
        for key in self._sorted(members):
            names = " -> ".join(f"{kind.value} '{name}'" for kind, name in members[key])
            self._failures[key] = f"It is part of a reference cycle: {names}."
        self._propagate_failures()

    def _find_cycle(self, *, start: InstanceKey, within: set[InstanceKey]) -> list[InstanceKey] | None:
        """
        Find a path of references that leads from a recipe back to itself.

        Returns:
            list[InstanceKey] | None: The cycle, starting and ending at ``start``, or ``None``.
        """
        stack: list[tuple[InstanceKey, list[InstanceKey]]] = [(start, [start])]
        visited: set[InstanceKey] = set()
        while stack:
            key, path = stack.pop()
            for dependency in self._sorted(self._dependencies.get(key, set()) & within):
                if dependency == start:
                    return [*path, start]
                if dependency not in visited:
                    visited.add(dependency)
                    stack.append((dependency, [*path, dependency]))
        return None

    def _unrestorable(self, key: InstanceKey) -> UnrestorableInstance:
        """
        Describe a recipe that cannot be rebuilt.

        Returns:
            UnrestorableInstance: The recipe and the reason.
        """
        stored = self._recipes[key]
        return UnrestorableInstance(
            kind=key[0], name=key[1], type=stored.recipe.type, reason=self._failures[key], version=stored.version
        )

    def _sort_key(self, key: InstanceKey) -> tuple[int, str]:
        """
        Order keys by kind, then name.

        Returns:
            tuple[int, str]: The sort key.
        """
        return self._KIND_ORDER[key[0]], key[1]

    def _sorted(self, keys: Iterable[InstanceKey]) -> list[InstanceKey]:
        """
        Sort keys deterministically.

        Returns:
            list[InstanceKey]: The keys ordered by kind, then name.
        """
        return sorted(keys, key=self._sort_key)
