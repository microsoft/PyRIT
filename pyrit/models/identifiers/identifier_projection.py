# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Marker-driven projections of component identifiers.

The strongly-typed identifier classes declare, on their own fields, which
parameters describe *behavior* and which are merely *operational* (endpoints,
deployment names, rate limits). This module reads those ``Evaluate.*`` markers
and provides:

* the marker-introspection helpers shared by the eval-hash engine, and
* ``project_behavioral_identity`` — the behavioral view of an identifier tree.

``project_behavioral_identity`` is deliberately **not** the eval-hash
projection. The eval hash may narrow a child further than its own type does
(``AttackIdentifier.objective_target`` restricts the target to ``temperature``
so that two attacks hash alike when only the target's model differs). That
narrowing is a hashing decision, not a statement that the rest of the child's
configuration is uninteresting. The behavioral projection therefore honors each
component's own markers and each parent's ``Exclude``, but ignores parent-side
``only_params`` narrowing, so every component reports the full behavior it
declares.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.evaluation_markers import EvalMarker, Exclude, Include, Unwrap


def resolve_child_type(annotation: Any) -> type[ComponentIdentifier]:
    """
    Resolve the ``ComponentIdentifier`` subclass a child field annotation denotes.

    Args:
        annotation (Any): A resolved child field annotation, e.g.
            ``TargetIdentifier | None`` or ``list[TargetIdentifier]``.

    Returns:
        type[ComponentIdentifier]: The referenced identifier subclass.

    Raises:
        TypeError: If no ``ComponentIdentifier`` subclass can be resolved.
    """
    if get_origin(annotation) is list:
        args = get_args(annotation)
        inner = args[0] if args else None
        if isinstance(inner, type) and issubclass(inner, ComponentIdentifier):
            return inner

    for candidate in get_args(annotation) or (annotation,):
        if isinstance(candidate, type) and issubclass(candidate, ComponentIdentifier):
            return candidate

    raise TypeError(f"Could not resolve a child identifier type from annotation {annotation!r}")


def field_marker(model_cls: type[ComponentIdentifier], field_name: str) -> EvalMarker | None:
    """Return the ``EvalMarker`` attached to a field, or ``None`` if unmarked."""
    for meta in model_cls.model_fields[field_name].metadata:
        if isinstance(meta, EvalMarker):
            return meta
    return None


def type_param_projection(
    model_cls: type[ComponentIdentifier],
) -> tuple[frozenset[str] | None, dict[str, str] | None]:
    """
    Project a type's own param-field markers into ``(included_params, fallbacks)``.

    An unmarked or ``Include`` param is kept; ``Exclude`` drops it. When the type
    has no excluded params, ``included_params`` is ``None`` (full include).

    Returns:
        tuple[frozenset[str] | None, dict[str, str] | None]: The included param
            names (``None`` for full include) and the per-param fallbacks (``None``
            when there are none).
    """
    included: list[str] = []
    fallbacks: dict[str, str] = {}
    has_exclude = False
    for name in model_cls._promoted_param_fields():
        marker = field_marker(model_cls, name)
        if isinstance(marker, Exclude):
            has_exclude = True
            continue
        included.append(name)
        if isinstance(marker, Include) and marker.fallback is not None:
            fallbacks[name] = marker.fallback
    included_params = frozenset(included) if has_exclude else None
    return included_params, (fallbacks or None)


def type_unwrap_field(model_cls: type[ComponentIdentifier]) -> str | None:
    """Return the name of the type's ``Evaluate.Unwrap()`` child field, if any."""
    for name in model_cls._promoted_child_fields():
        if isinstance(field_marker(model_cls, name), Unwrap):
            return name
    return None


def _child_type_for_slot(
    *,
    parent_type: type[ComponentIdentifier],
    child_name: str,
    child_identifier: ComponentIdentifier,
    declared: bool,
) -> type[ComponentIdentifier]:
    """
    Resolve the identifier type governing one child slot.

    The declared field annotation wins so that a slot declared as a
    ``TargetIdentifier`` is projected as a target even when the stored instance
    was rehydrated as a plain ``ComponentIdentifier``. Undeclared slots fall back
    to the runtime instance's own class.

    Returns:
        type[ComponentIdentifier]: The type whose markers govern the child.
    """
    if declared:
        return resolve_child_type(parent_type.model_fields[child_name].annotation)
    return type(child_identifier)


def project_behavioral_identity(
    identifier: ComponentIdentifier,
    *,
    identifier_type: type[ComponentIdentifier],
) -> ComponentIdentifier:
    """
    Return the behavioral view of an identifier tree.

    Each component is filtered by its own typed markers, so operational params
    (endpoints, deployment names, rate limits) are dropped and declared
    fallbacks are applied. A parent may drop a child outright with
    ``Evaluate.Exclude``; wrapper slots marked ``Evaluate.Unwrap`` are replaced
    by their first inner component.

    Args:
        identifier: The full component identifier to project.
        identifier_type: The typed identifier schema for the root component.

    Returns:
        ComponentIdentifier: A tree containing only behavior-defining details.
    """
    unwrap_field = type_unwrap_field(identifier_type)
    if unwrap_field:
        inner_identifiers = identifier.get_child_list(unwrap_field)
        if inner_identifiers:
            inner_type = resolve_child_type(identifier_type.model_fields[unwrap_field].annotation)
            return project_behavioral_identity(inner_identifiers[0], identifier_type=inner_type)

    included_params, param_fallbacks = type_param_projection(identifier_type)
    parameters = {
        name: value
        for name, value in identifier.params.items()
        if value is not None and (included_params is None or name in included_params)
    }
    for primary_name, fallback_name in (param_fallbacks or {}).items():
        primary_value = parameters.get(primary_name)
        if primary_value is None or primary_value == "":
            fallback_value = identifier.params.get(fallback_name)
            if fallback_value is not None and fallback_value != "":
                parameters[primary_name] = fallback_value

    declared_child_fields = identifier_type._promoted_child_fields()
    children: dict[str, ComponentIdentifier | list[ComponentIdentifier]] = {}
    for child_name, child_value in identifier.children.items():
        declared = child_name in declared_child_fields
        if declared and isinstance(field_marker(identifier_type, child_name), Exclude):
            continue

        child_identifiers = child_value if isinstance(child_value, list) else [child_value]
        projected_children = [
            project_behavioral_identity(
                child,
                identifier_type=_child_type_for_slot(
                    parent_type=identifier_type,
                    child_name=child_name,
                    child_identifier=child,
                    declared=declared,
                ),
            )
            for child in child_identifiers
        ]
        children[child_name] = projected_children if isinstance(child_value, list) else projected_children[0]

    return ComponentIdentifier(
        class_name=identifier.class_name,
        class_module=identifier.class_module,
        params=parameters,
        children=children,
    )
