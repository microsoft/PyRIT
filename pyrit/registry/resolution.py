# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Constructor-argument resolution for PyRIT registries.

This is the shared mechanism that lets any registry build an instance from a
type name plus a flat dict of arguments. Build inputs are exactly two kinds:

- **Simple values** — strings/ints/floats/bools (and ``Literal`` choices) that
  can be coerced to the constructor's annotated type.
- **Registry references** — a parameter whose annotation is a domain base type
  (``PromptTarget``, ``PromptConverter``, ``Scorer``) is supplied *by name* and
  resolved from that domain's registry. An already-constructed instance passes
  through unchanged.

Unknown parameters raise, so a caller (form, agent, attack strategy) gets a
clear error instead of having values silently dropped.

This module performs no eager heavy imports and never imports ``pyrit.backend``:
the resolvable-registry lookups are done lazily so it can be reused anywhere.
"""

from __future__ import annotations

import inspect
import types
from typing import TYPE_CHECKING, Any, Literal, Protocol, Union, get_args, get_origin

if TYPE_CHECKING:
    from collections.abc import Callable

# Scalar Python types whose string values can be coerced to the real type.
_SIMPLE_TYPES: set[type] = {str, int, float, bool}


class _NamedInstanceRegistry(Protocol):
    """Structural type for a registry that resolves stored instances by name."""

    def get(self, name: str) -> Any | None:
        """Return the instance registered under ``name``, or None."""
        ...

    def get_names(self) -> list[str]:
        """Return the sorted names of registered instances."""
        ...


def get_union_non_none_args(annotation: Any) -> list[Any] | None:
    """
    Return the non-``None`` members of a union annotation, or None if not a union.

    Handles both ``typing.Union[X, None]`` and PEP 604 ``X | None``. This is a
    general type-introspection utility (not presentation), reused by coercion,
    registry-reference detection, and callers that need to render a type.

    Args:
        annotation (Any): The type annotation to inspect.

    Returns:
        list[Any] | None: The non-None union members, or None when the annotation
        is not a union.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        return [a for a in get_args(annotation) if a is not type(None)]
    return None


def is_coercible_from_string(annotation: Any) -> bool:
    """
    Return True if a string value can be coerced to the annotated type.

    Covers the scalar types in ``_SIMPLE_TYPES`` (str/int/float/bool),
    ``Literal`` annotations, and an ``Optional`` wrapping one of those.

    Returns:
        bool: True if the annotation is coercible from a string, False otherwise.
    """
    if annotation in _SIMPLE_TYPES:
        return True
    if get_origin(annotation) is Literal:
        return True
    non_none = get_union_non_none_args(annotation)
    if non_none is not None:
        return len(non_none) == 1 and is_coercible_from_string(non_none[0])
    return False


def _resolvable_registries() -> list[tuple[type, Callable[[], _NamedInstanceRegistry]]]:
    """
    Return the (base type -> registry singleton getter) pairs that can be resolved by name.

    A constructor parameter whose annotation is (a subclass of) one of these base
    types is supplied by name and looked up in the paired registry. Imports are
    deferred so this core module stays import-light and free of cycles.

    Returns:
        list[tuple[type, Callable[[], _NamedInstanceRegistry]]]: The resolvable
        domain base types paired with a callable returning their registry singleton.
    """
    from pyrit.prompt_converter import PromptConverter
    from pyrit.prompt_target import PromptTarget
    from pyrit.registry.components import ConverterRegistry
    from pyrit.registry.object_registries import (
        ScorerRegistry,
        TargetRegistry,
    )
    from pyrit.score.scorer import Scorer

    return [
        (PromptTarget, TargetRegistry.get_registry_singleton),
        (PromptConverter, lambda: ConverterRegistry.get_registry_singleton().instances),
        (Scorer, ScorerRegistry.get_registry_singleton),
    ]


def get_resolvable_registry_getter(annotation: Any) -> Callable[[], _NamedInstanceRegistry] | None:
    """
    Return the registry-singleton getter for a registry-reference annotation.

    The annotation matches when it is (or unions, e.g. ``X | None``, to) a subclass
    of a resolvable domain base type. A parameter with such an annotation is
    supplied by name and resolved from the returned registry.

    Args:
        annotation (Any): The parameter's type annotation.

    Returns:
        Callable[[], _NamedInstanceRegistry] | None: A callable returning the
        registry singleton, or None when the annotation is not a registry reference.
    """
    if annotation is inspect.Parameter.empty:
        return None

    candidates = get_union_non_none_args(annotation)
    if candidates is None:
        candidates = [annotation]

    for base_type, getter in _resolvable_registries():
        for candidate in candidates:
            try:
                if isinstance(candidate, type) and issubclass(candidate, base_type):
                    return getter
            except TypeError:
                continue
    return None


def is_registry_reference(annotation: Any) -> bool:
    """
    Return True if the annotation is a registry reference (resolved by name).

    Returns:
        bool: True if a value for this parameter is supplied by name and resolved
        from a registry, False otherwise.
    """
    return get_resolvable_registry_getter(annotation) is not None


def coerce_string_to_annotation(*, value: str, annotation: Any) -> Any:
    """
    Coerce a string value to the annotated scalar type (int/float/bool/Literal).

    ``Optional[X]`` / ``X | None`` is unwrapped to ``X`` first. A ``Literal`` value
    is validated against the allowed members and returned as the matching member
    (so an int literal comes back as an ``int``); other ``str`` values pass through
    unchanged.

    Args:
        value (str): The raw string value.
        annotation (Any): The parameter's type annotation.

    Returns:
        Any: The value coerced to the annotated type, or the original string when
        no numeric/boolean/Literal coercion applies.

    Raises:
        ValueError: If the value cannot be interpreted as the annotated type, or is
            not one of the allowed members of an annotated ``Literal``.
    """
    if annotation is inspect.Parameter.empty:
        return value

    non_none = get_union_non_none_args(annotation)
    if non_none is not None and len(non_none) == 1:
        annotation = non_none[0]

    if get_origin(annotation) is Literal:
        allowed = get_args(annotation)
        for member in allowed:
            if value == str(member):
                return member
        raise ValueError(f"expected one of {[str(a) for a in allowed]}, got {value!r}")

    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is bool:
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        raise ValueError(f"cannot interpret {value!r} as a boolean")
    return value


def _resolve_registry_reference(
    *, value: Any, getter: Callable[[], _NamedInstanceRegistry], owner: str, name: str
) -> Any:
    """
    Resolve a registry-reference parameter value to a stored instance.

    A string value is looked up by name in the paired registry. An already-built
    instance passes through unchanged.

    Args:
        value (Any): The raw value (a registry name, or an instance to pass through).
        getter (Callable[[], _NamedInstanceRegistry]): Returns the registry singleton.
        owner (str): The owning class name, for error messages.
        name (str): The parameter name, for error messages.

    Returns:
        Any: The resolved instance.

    Raises:
        ValueError: If the name is not registered.
    """
    if not isinstance(value, str):
        return value

    registry = getter()
    instance = registry.get(value)
    if instance is not None:
        return instance

    registry_label = type(registry).__name__
    available_names = registry.get_names()
    if not available_names:
        raise ValueError(
            f"{owner}.{name}: '{value}' not found. The {registry_label} is empty. "
            "Make sure to register instances (e.g. via an initializer) before building "
            "components that reference them by name."
        )
    raise ValueError(
        f"{owner}.{name}: '{value}' not found in {registry_label}. Available: {', '.join(available_names)}"
    )


def resolve_constructor_args(*, cls: type, raw_args: dict[str, Any]) -> dict[str, Any]:
    """
    Resolve a flat argument dict into constructor-ready keyword arguments.

    For each argument: validate it is a real constructor parameter (unless the
    constructor accepts ``**kwargs``); resolve registry-reference parameters by
    name; coerce simple string values to their annotated scalar type; pass
    everything else through unchanged.

    Args:
        cls (type): The class whose ``__init__`` signature drives resolution.
        raw_args (dict[str, Any]): The raw argument values (e.g. from a form or agent).

    Returns:
        dict[str, Any]: Arguments ready to pass to ``cls(**resolved)``.

    Raises:
        ValueError: If the signature cannot be inspected, an argument is not a
            valid constructor parameter, a registry reference cannot be resolved,
            or a simple value cannot be coerced.
    """
    try:
        sig = inspect.signature(cls.__init__)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Failed to inspect __init__ signature for '{cls.__name__}': {e}") from e

    accepts_var_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    valid_params = {
        param_name: p
        for param_name, p in sig.parameters.items()
        if param_name != "self" and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }

    resolved: dict[str, Any] = {}
    for name, value in raw_args.items():
        param = valid_params.get(name)
        if param is None and not accepts_var_kwargs:
            raise ValueError(
                f"Unknown parameter '{name}' for '{cls.__name__}'. Valid parameters: {sorted(valid_params.keys())}"
            )

        annotation = param.annotation if param is not None else inspect.Parameter.empty

        registry_getter = get_resolvable_registry_getter(annotation)
        if registry_getter is not None:
            resolved[name] = _resolve_registry_reference(
                value=value, getter=registry_getter, owner=cls.__name__, name=name
            )
        elif isinstance(value, str) and is_coercible_from_string(annotation):
            try:
                resolved[name] = coerce_string_to_annotation(value=value, annotation=annotation)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Parameter '{name}' of '{cls.__name__}': {e}") from e
        else:
            resolved[name] = value

    return resolved
