# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
The constructor <-> ``Parameter`` contract bridge for PyRIT registries.

This module is the single place that translates between a component class's
``__init__`` and the declarative ``Parameter`` contract carried by its domain
identifier. It has three responsibilities:

- **Derive** (``derive_parameters``): read the constructor signature, enriched
  by the identifier's ``Param.*`` build markers, into a ``list[Parameter]``. A
  parameter the identifier promotes as a reference to another registry (an
  included field typed as a child identifier, e.g. ``TargetIdentifier``) becomes
  a registry **reference**; every other parameter becomes a plain value parameter
  whose ``param_type`` is the annotation with ``Optional[X]`` reduced to ``X``.
- **Resolve** (``resolve_constructor_args``): derive the contract for a class
  and turn a flat dict of raw arguments into constructor-ready keyword arguments —
  coercing simple string values via ``Parameter.coerce_value`` and resolving
  registry-reference parameters by name from the owning domain's registry.
- **Present** (``display_choices``): project a constrained-scalar ``param_type``
  into its allowed-value display tuple.

The identifier is the declarative blueprint; this module is where the registry
reads and applies it. It performs no eager heavy imports and never imports
``pyrit.backend``: registry lookups are done lazily so it can be reused anywhere.
"""

from __future__ import annotations

import inspect
import re
import types
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias, Union, get_args, get_origin

from pyrit.common.apply_defaults import REQUIRED_VALUE, _RequiredValueSentinel
from pyrit.models.parameter import ComponentType, Parameter, RegistryReference

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyrit.models.identifiers.component_identifier import ComponentIdentifier

# Constructor parameters that never describe a settable build input.
_SKIPPED_PARAM_NAMES: frozenset[str] = frozenset({"self", "args", "kwargs"})

#: A runtime type-annotation object as seen on a constructor parameter or a
#: ``Parameter.param_type``: a concrete ``type``, a typing special form
#: (``X | None`` / ``Optional`` / ``Union`` / ``Literal``), or
#: ``inspect.Parameter.empty`` for an unannotated parameter. Aliased to ``Any``
#: because no single static type captures all of these; the name documents intent.
TypeAnnotation: TypeAlias = Any


# ---------------------------------------------------------------------------
# Derive: component class -> list[Parameter]
# ---------------------------------------------------------------------------


def _unwrap_optional(annotation: TypeAnnotation) -> TypeAnnotation:
    """
    Reduce ``Optional[X]`` / ``X | None`` to ``X`` (only for single-member unions).

    Returns:
        TypeAnnotation: ``X`` when ``annotation`` is a single-member optional union,
            otherwise the annotation unchanged.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _parse_arg_descriptions(cls: type) -> dict[str, str]:
    """
    Parse parameter descriptions from a Google-style docstring ``Args`` section.

    Returns:
        dict[str, str]: Mapping of parameter names to their descriptions.
    """
    doc = (cls.__init__.__doc__ or cls.__doc__ or "").strip()
    match = re.search(r"Args:\s*\n(.*?)(?:\n\s*\n|\n\s*Returns:|\n\s*Raises:|\Z)", doc, re.DOTALL)
    if not match:
        return {}
    args_block = match.group(1)
    indent_match = re.match(r"^(\s+)", args_block)
    indent = indent_match.group(1) if indent_match else r"\s+"
    pattern = rf"^{indent}(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+?)(?=\n{indent}\w|\Z)"
    descriptions: dict[str, str] = {}
    for m in re.finditer(pattern, args_block, re.DOTALL | re.MULTILINE):
        descriptions[m.group(1)] = " ".join(m.group(2).split())
    return descriptions


def _default_for(param: inspect.Parameter) -> Any:
    """
    Return the ``Parameter.default`` for a constructor parameter.

    A parameter with no default or the ``REQUIRED_VALUE`` sentinel is required, and
    is represented with ``REQUIRED_VALUE`` so consumers can detect it uniformly.

    Returns:
        Any: The parameter's default value, or ``REQUIRED_VALUE`` when it is required.
    """
    if param.default is inspect.Parameter.empty or isinstance(param.default, _RequiredValueSentinel):
        return REQUIRED_VALUE
    return param.default


def derive_parameters(*, cls: type, identifier_type: type[ComponentIdentifier] | None = None) -> list[Parameter]:
    """
    Derive the declarative ``Parameter`` list for ``cls`` from its constructor.

    Performs the single ``inspect.signature`` call of the build pipeline and maps
    each settable constructor parameter to a ``Parameter``: parameters the
    identifier promotes as references carry a ``RegistryReference``; plain
    parameters carry an ``Optional``-unwrapped ``param_type``. Parameter order
    follows the constructor signature.

    Args:
        cls (type): The component class whose ``__init__`` drives derivation.
        identifier_type (type[ComponentIdentifier] | None): The domain identifier
            whose ``Param.*`` markers declare which parameters are registry
            references. When None, no parameter is treated as a reference.

    Returns:
        list[Parameter]: One ``Parameter`` per settable constructor parameter.

    Raises:
        ValueError: If the constructor signature cannot be inspected.
    """
    try:
        sig = inspect.signature(cls.__init__)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Failed to inspect __init__ signature for '{cls.__name__}': {e}") from e

    reference_overrides = identifier_type.get_reference_component_types() if identifier_type is not None else {}
    descriptions = _parse_arg_descriptions(cls)

    parameters: list[Parameter] = []
    for name, param in sig.parameters.items():
        if name in _SKIPPED_PARAM_NAMES:
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        annotation = param.annotation
        component_type = reference_overrides.get(name)
        description = descriptions.get(name, "")
        default = _default_for(param)

        if component_type is not None:
            parameters.append(
                Parameter(
                    name=name,
                    description=description,
                    default=default,
                    reference=RegistryReference(component_type=component_type, annotation=annotation),
                )
            )
        else:
            param_type = None if annotation is inspect.Parameter.empty else _unwrap_optional(annotation)
            parameters.append(Parameter(name=name, description=description, default=default, param_type=param_type))

    return parameters


# ---------------------------------------------------------------------------
# Resolve: derived Parameters + raw args -> constructor keyword arguments
# ---------------------------------------------------------------------------


class _NamedInstanceRegistry(Protocol):
    """Structural type for a registry that resolves stored instances by name."""

    def get(self, name: str) -> Any | None:
        """Return the instance registered under ``name``, or None."""
        ...

    def get_names(self) -> list[str]:
        """Return the sorted names of registered instances."""
        ...


def _registry_getter_for_component_type(component_type: ComponentType) -> Callable[[], _NamedInstanceRegistry] | None:
    """
    Return the getter for the instance registry that resolves a component family.

    This is the one place that must import the concrete registries, so it stays in
    the resolve layer (the derive layer never imports them). It is the inverse of
    the identifier's self-reported ``component_type``: given that family, return the
    ``.instances`` container that resolves its references by name.

    The three component registries share a uniform surface — each is a ``Registry``
    whose pre-configured instances live under ``.instances`` — so the mapping is a
    flat ``ComponentType -> Registry class`` lookup.

    Returns:
        Callable[[], _NamedInstanceRegistry] | None: The registry getter, or None
        when no registry is wired for ``component_type``.
    """
    from pyrit.registry.components import ConverterRegistry, ScorerRegistry, TargetRegistry

    registry_classes = {
        ComponentType.TARGET: TargetRegistry,
        ComponentType.CONVERTER: ConverterRegistry,
        ComponentType.SCORER: ScorerRegistry,
    }
    registry_class = registry_classes.get(component_type)
    if registry_class is None:
        return None
    return lambda: registry_class.get_registry_singleton().instances


def _resolve_single_reference(
    *, value: Any, getter: Callable[[], _NamedInstanceRegistry], owner: str, name: str
) -> Any:
    """
    Resolve a single registry-reference value to a stored instance.

    A string value is looked up by name in the paired registry. An already-built
    instance passes through unchanged.

    Args:
        value (Any): The raw value (a registry name, or an instance to pass through).
        getter (Callable[[], _NamedInstanceRegistry]): Returns the instance registry.
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


def _resolve_registry_reference(
    *,
    value: Any,
    getter: Callable[[], _NamedInstanceRegistry],
    owner: str,
    name: str,
    annotation: TypeAnnotation = None,
) -> Any:
    """
    Resolve a registry-reference parameter value to stored instance(s).

    A scalar reference resolves a single name (or instance). A reference whose
    constructor annotation is a ``list[...]`` resolves a list of names element by
    element, so a multi-target (``RoundRobinTarget``) or a composite scorer can be
    built from a list of registry names. Each element is resolved by
    ``_resolve_single_reference`` (string → lookup, instance → passthrough).

    The value's shape must match the reference's arity: a ``list[...]`` reference
    requires a list and a scalar reference rejects one, so a shape mismatch fails
    here with a clear message instead of constructing the component with the wrong
    argument shape and erroring obscurely downstream.

    Args:
        value (Any): The raw value (a name, an instance, or a list of either).
        getter (Callable[[], _NamedInstanceRegistry]): Returns the instance registry.
        owner (str): The owning class name, for error messages.
        name (str): The parameter name, for error messages.
        annotation (TypeAnnotation): The constructor parameter's type annotation,
            used to detect a ``list[...]`` reference.

    Returns:
        Any: The resolved instance, or a list of resolved instances.

    Raises:
        ValueError: If a name is not registered, or the value's shape (list vs.
            scalar) does not match the reference's arity.
    """
    if get_origin(annotation) is list:
        if not isinstance(value, list):
            raise ValueError(
                f"{owner}.{name}: expected a list of registry names or instances for this "
                f'reference, but got {type(value).__name__}. Pass a list, e.g. {name}=["a", "b"].'
            )
        return [_resolve_single_reference(value=item, getter=getter, owner=owner, name=name) for item in value]
    if isinstance(value, list):
        raise ValueError(
            f"{owner}.{name}: expected a single registry name or instance for this reference, "
            f'but got a list. Pass a single value, e.g. {name}="a".'
        )
    return _resolve_single_reference(value=value, getter=getter, owner=owner, name=name)


def resolve_constructor_args(
    *, cls: type, raw_args: dict[str, Any], identifier_type: type[ComponentIdentifier] | None = None
) -> dict[str, Any]:
    """
    Resolve a flat argument dict into constructor-ready keyword arguments.

    Derives the ``Parameter`` contract for ``cls`` (the single
    ``inspect.signature`` call) and applies it to ``raw_args``. For each raw
    argument: validate it is a declared parameter; resolve registry-reference
    parameters by name; coerce simple string values via
    ``Parameter.coerce_value``; pass everything else through unchanged.

    Args:
        cls (type): The class being built.
        raw_args (dict[str, Any]): The raw argument values (e.g. from a form or agent).
        identifier_type (type[ComponentIdentifier] | None): The domain identifier
            whose ``Param.*`` markers declare which parameters are registry
            references. When None, no parameter is treated as a reference.

    Returns:
        dict[str, Any]: Arguments ready to pass to ``cls(**resolved)``.

    Raises:
        ValueError: If an argument is not a declared parameter, a registry
            reference cannot be resolved, or a simple value cannot be coerced.
    """
    by_name = {param.name: param for param in derive_parameters(cls=cls, identifier_type=identifier_type)}

    resolved: dict[str, Any] = {}
    for name, value in raw_args.items():
        param = by_name.get(name)
        if param is None:
            raise ValueError(
                f"Unknown parameter '{name}' for '{cls.__name__}'. Valid parameters: {sorted(by_name.keys())}"
            )

        if param.reference is not None:
            getter = _registry_getter_for_component_type(param.reference.component_type)
            if getter is None:
                raise ValueError(
                    f"{cls.__name__}.{name}: no registry is wired for component type "
                    f"'{param.reference.component_type}'."
                )
            resolved[name] = _resolve_registry_reference(
                value=value,
                getter=getter,
                owner=cls.__name__,
                name=name,
                annotation=param.reference.annotation,
            )
        elif isinstance(value, str) and param.is_string_coercible:
            try:
                resolved[name] = param.coerce_value(value)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Parameter '{name}' of '{cls.__name__}': {e}") from e
        else:
            resolved[name] = value

    return resolved


# ---------------------------------------------------------------------------
# Present: param_type -> allowed-value display tuple
# ---------------------------------------------------------------------------


def display_choices(param_type: TypeAnnotation) -> tuple[Any, ...] | None:
    """
    Derive the allowed-value display list from a constrained-scalar ``param_type``.

    This is the presentation projection of an allowed set: a ``Parameter`` stores
    the constraint as a ``Literal[...]`` / ``Enum`` type, and serializers render the
    members on demand instead of reading a separate field. ``Optional[X]`` /
    ``X | None`` is unwrapped first.

    Args:
        param_type (TypeAnnotation): The parameter's type annotation.

    Returns:
        tuple[Any, ...] | None: The allowed members for a constrained scalar
        (``Literal`` args or ``Enum`` member values), or None when unconstrained.
    """
    unwrapped = _unwrap_optional(param_type)
    if get_origin(unwrapped) is Literal:
        return get_args(unwrapped)
    if isinstance(unwrapped, type) and issubclass(unwrapped, Enum):
        return tuple(member.value for member in unwrapped)
    return None
