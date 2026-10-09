# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for planning the restore order of saved instances."""

import hashlib

from pyrit.models import ComponentType, Parameter
from pyrit.models.catalog.instance_recipe import InstanceRecipe, StoredInstanceRecipe
from pyrit.models.parameter import RegistryReference
from pyrit.registry.instance_restore import InstanceRestorePlanner, get_recipe_references

TARGET = ComponentType.TARGET
CONVERTER = ComponentType.CONVERTER
SCORER = ComponentType.SCORER

_PARAMETERS = {
    "Chat": [Parameter(name="endpoint", description="", param_type=str)],
    "RoundRobin": [
        Parameter(
            name="targets", description="", reference=RegistryReference(component_type=TARGET, annotation=list[str])
        )
    ],
    "Translate": [
        Parameter(name="converter_target", description="", reference=RegistryReference(component_type=TARGET)),
        Parameter(name="sub_converter", description="", reference=RegistryReference(component_type=CONVERTER)),
    ],
    "Composite": [
        Parameter(name="chat_target", description="", reference=RegistryReference(component_type=TARGET)),
        Parameter(
            name="scorers", description="", reference=RegistryReference(component_type=SCORER, annotation=list[str])
        ),
    ],
}


def _saved(kind: ComponentType, name: str, type_name: str, **params: object) -> StoredInstanceRecipe:
    recipe = InstanceRecipe(kind=kind, name=name, type=type_name, params=params)
    return StoredInstanceRecipe(recipe=recipe, version=hashlib.sha256(name.encode()).hexdigest())


def _plan(recipes: list[StoredInstanceRecipe], *, registered: set[tuple[ComponentType, str]] | None = None):
    registered = registered or set()
    planner = InstanceRestorePlanner(
        recipes=recipes,
        is_registered=lambda kind, name: (kind, name) in registered,
        get_parameters=lambda recipe: _PARAMETERS.get(recipe.type),
    )
    return planner.plan()


def _order(plan) -> list[tuple[str, str]]:
    return [(planned.stored.recipe.kind.value, planned.stored.recipe.name) for planned in plan.ordered]


def _reasons(plan) -> dict[str, str]:
    return {entry.name: entry.reason for entry in plan.unrestorable}


def test_references_are_read_from_scalar_and_list_reference_parameters() -> None:
    recipe = InstanceRecipe(kind=SCORER, name="s", type="Composite", params={"chat_target": "t", "scorers": ["a", "b"]})

    references = get_recipe_references(recipe=recipe, parameters=_PARAMETERS["Composite"])

    assert references == {(TARGET, "t"), (SCORER, "a"), (SCORER, "b")}


def test_dependencies_are_rebuilt_first_across_and_within_kinds() -> None:
    plan = _plan(
        [
            _saved(SCORER, "judge", "Composite", chat_target="rr", scorers=["base"]),
            _saved(SCORER, "base", "Composite", chat_target="b"),
            _saved(CONVERTER, "outer", "Translate", converter_target="rr", sub_converter="inner"),
            _saved(CONVERTER, "inner", "Translate", converter_target="a"),
            _saved(TARGET, "rr", "RoundRobin", targets=["a", "b"]),
            _saved(TARGET, "b", "Chat"),
            _saved(TARGET, "a", "Chat"),
        ]
    )

    order = _order(plan)
    assert plan.unrestorable == []
    for dependent, dependency in [
        (("target", "rr"), ("target", "a")),
        (("target", "rr"), ("target", "b")),
        (("converter", "inner"), ("target", "a")),
        (("converter", "outer"), ("converter", "inner")),
        (("converter", "outer"), ("target", "rr")),
        (("scorer", "judge"), ("scorer", "base")),
    ]:
        assert order.index(dependency) < order.index(dependent)
    assert order[:3] == [("target", "a"), ("target", "b"), ("target", "rr")]


def test_order_is_deterministic_regardless_of_input_order() -> None:
    recipes = [_saved(TARGET, name, "Chat") for name in ("c", "a", "b")]

    assert (
        _order(_plan(recipes))
        == _order(_plan(list(reversed(recipes))))
        == [
            ("target", "a"),
            ("target", "b"),
            ("target", "c"),
        ]
    )


def test_reference_to_an_already_registered_instance_needs_no_saved_recipe() -> None:
    plan = _plan(
        [_saved(CONVERTER, "c", "Translate", converter_target="openai_chat")], registered={(TARGET, "openai_chat")}
    )

    assert _order(plan) == [("converter", "c")]
    assert plan.ordered[0].dependencies == frozenset()


def test_reference_to_a_saved_instance_whose_document_cannot_be_used_is_not_bound_to_another() -> None:
    planner = InstanceRestorePlanner(
        recipes=[_saved(CONVERTER, "c", "Translate", converter_target="alpha")],
        is_registered=lambda kind, name: (kind, name) == (TARGET, "alpha"),
        get_parameters=lambda recipe: _PARAMETERS.get(recipe.type),
        is_unavailable=lambda kind, name: (kind, name) == (TARGET, "alpha"),
    )

    plan = planner.plan()

    assert plan.ordered == []
    assert _reasons(plan) == {"c": "It depends on target 'alpha', which could not be restored."}
    assert plan.blocked == {(CONVERTER, "c"): frozenset({(TARGET, "alpha")})}


def test_reference_to_a_missing_instance_is_reported() -> None:
    plan = _plan([_saved(CONVERTER, "c", "Translate", converter_target="gone")])

    assert _reasons(plan) == {"c": "It references target 'gone', which is not registered."}


def test_saved_recipe_whose_name_is_taken_is_not_rebuilt_and_blocks_its_dependents() -> None:
    plan = _plan(
        [
            _saved(TARGET, "openai_chat", "Chat"),
            _saved(CONVERTER, "c", "Translate", converter_target="openai_chat"),
        ],
        registered={(TARGET, "openai_chat")},
    )

    reasons = _reasons(plan)
    assert plan.ordered == []
    assert "already registered" in reasons["openai_chat"]
    assert reasons["c"] == "It depends on target 'openai_chat', which could not be restored."


def test_unknown_type_fails_and_its_dependents_fail_transitively() -> None:
    plan = _plan(
        [
            _saved(TARGET, "a", "Removed"),
            _saved(TARGET, "rr", "RoundRobin", targets=["a"]),
            _saved(CONVERTER, "c", "Translate", converter_target="rr"),
            _saved(TARGET, "ok", "Chat"),
        ]
    )

    reasons = _reasons(plan)
    assert reasons["a"] == "Type 'Removed' is not registered."
    assert reasons["rr"] == "It depends on target 'a', which could not be restored."
    assert reasons["c"] == "It depends on target 'rr', which could not be restored."
    assert _order(plan) == [("target", "ok")]


def test_cycle_members_and_recipes_behind_a_cycle_are_reported_differently() -> None:
    plan = _plan(
        [
            _saved(TARGET, "a", "RoundRobin", targets=["b"]),
            _saved(TARGET, "b", "RoundRobin", targets=["a"]),
            _saved(TARGET, "c", "RoundRobin", targets=["a"]),
            _saved(TARGET, "self", "RoundRobin", targets=["self"]),
        ]
    )

    reasons = _reasons(plan)
    assert plan.ordered == []
    assert reasons["a"] == "It is part of a reference cycle: target 'a' -> target 'b' -> target 'a'."
    assert reasons["b"] == "It is part of a reference cycle: target 'b' -> target 'a' -> target 'b'."
    assert reasons["self"] == "It is part of a reference cycle: target 'self' -> target 'self'."
    assert reasons["c"] == "It depends on target 'a', which could not be restored."


def test_unrestorable_entries_keep_the_type_and_version() -> None:
    saved = _saved(TARGET, "a", "Removed")

    [entry] = _plan([saved]).unrestorable

    assert (entry.kind, entry.type, entry.version) == (TARGET, "Removed", saved.version)


def test_recipes_blocked_by_a_reference_are_listed_with_everything_they_reference() -> None:
    plan = _plan(
        [
            _saved(TARGET, "a", "Removed"),
            _saved(TARGET, "rr", "RoundRobin", targets=["a", "ok"]),
            _saved(CONVERTER, "c", "Translate", converter_target="gone"),
            _saved(TARGET, "ok", "Chat"),
        ]
    )

    assert plan.blocked == {
        (TARGET, "rr"): frozenset({(TARGET, "a"), (TARGET, "ok")}),
        (CONVERTER, "c"): frozenset({(TARGET, "gone")}),
    }
