# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
The ``Parameter`` contract bridge for PyRIT registries.

This module is the single place that translates raw arguments into ready values
against the declarative ``Parameter`` contract, whether that contract is derived
from a class ``__init__`` or declared explicitly by a component. It has three
responsibilities:

- **Derive** (``derive_parameters``): read the constructor signature, plus
  explicitly forwarded parent signatures in MRO order, and enrich them with the
  identifier's ``Param.*`` build markers into a ``list[Parameter]``. A parameter
  the identifier promotes as a reference to another registry (an included field
  typed as a child identifier, e.g. ``TargetIdentifier``) becomes a registry
  **reference**; every other parameter becomes a plain value parameter whose
  ``param_type`` preserves the annotation, including nullability.
- **Resolve from a constructor** (``resolve_constructor_args``): derive the
  contract for a class and turn a flat dict of raw arguments into
  constructor-ready keyword arguments — coercing simple string values via
  ``Parameter.coerce_value`` and resolving registry-reference parameters by name
  from the owning domain's registry. Defaults are left to the constructor.
- **Resolve from a declared list** (``resolve_declared_params``): the sibling for
  a component that declares an explicit ``list[Parameter]`` (e.g. a scenario's
  ``supported_parameters()``). It has no references, coerces every supplied
  value, and materializes every declared default so the result is a complete
  param bag. Both resolve functions delegate the actual coercion/validation to
  the ``Parameter`` model — that is the one shared kernel; they differ only in
  where the contract comes from and how defaults are handled.

The identifier is the declarative blueprint; this module is where the registry
reads and applies it. It performs no eager heavy imports and never imports
``pyrit.backend``: registry lookups are done lazily so it can be reused anywhere.
"""

from __future__ import annotations

import copy
import inspect
import json
import logging
import re
import types
from collections.abc import Collection, Sequence
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    Protocol,
    TypeAlias,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from pyrit.common.apply_defaults import REQUIRED_VALUE, _RequiredValueSentinel
from pyrit.common.brick_contract import init_parameters_are_forwarded
from pyrit.models import StructuredParameterValue
from pyrit.models.parameter import ComponentType, Parameter, RegistryReference

# Re-exported so ``from pyrit.registry.resolution import display_choices`` keeps working;
# the single implementation now lives in ``pyrit.models.parameter``.
from pyrit.models.parameter import display_choices as display_choices

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
logger = logging.getLogger(__name__)

# Only server-declared types can be constructed from JSON. Clients cannot name modules.
_REGISTERED_STRUCTURED_INPUTS: dict[type, dict[str, type]] = {}
_STRUCTURED_INPUT_REFERENCES: dict[type, dict[str, ComponentType]] = {}


def register_structured_input(
    *, base_type: type, variants: dict[str, type], references: dict[str, ComponentType] | None = None
) -> None:
    """
    Declare safe structured variants and references from trusted Python code.

    Raises:
        TypeError: If a variant is not a subclass of the base type.
    """
    if not variants or not all(isinstance(cls, type) and issubclass(cls, base_type) for cls in variants.values()):
        raise TypeError("Structured variants must be subclasses of their declared base type")
    _REGISTERED_STRUCTURED_INPUTS[base_type] = dict(variants)
    if references is not None:
        _STRUCTURED_INPUT_REFERENCES[base_type] = dict(references)


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


def _structured_variant_types(annotation: TypeAnnotation) -> dict[str, type] | None:
    """
    Return the named implementations declared by a structured-input annotation.

    Returns:
        dict[str, type] | None: The declared variants, or None when the annotation
            is not a structured input.

    Raises:
        TypeError: If the provider returns an invalid mapping or unrelated classes.
    """
    base_type = _unwrap_optional(annotation)
    origin = get_origin(base_type)
    if origin is Annotated:
        return _structured_variant_types(get_args(base_type)[0])
    if origin in (list, Collection, Sequence):
        return _structured_variant_types(get_args(base_type)[0]) if get_args(base_type) else None
    if origin is Union or origin is types.UnionType:
        combined: dict[str, type] = {}
        for member in get_args(base_type):
            combined.update(_structured_variant_types(member) or {})
        return combined or None
    if isinstance(base_type, type) and base_type in _REGISTERED_STRUCTURED_INPUTS:
        return _REGISTERED_STRUCTURED_INPUTS[base_type]
    if not isinstance(base_type, type) or not issubclass(base_type, StructuredParameterValue):
        return None
    variants = base_type.get_registry_input_variants()
    if not isinstance(variants, dict) or not all(
        isinstance(name, str) and isinstance(implementation, type) for name, implementation in variants.items()
    ):
        raise TypeError("get_registry_input_variants() must return dict[str, type].")
    if not all(issubclass(implementation, base_type) for implementation in variants.values()):
        raise TypeError(f"get_registry_input_variants() implementations must inherit from {base_type.__name__}.")
    result: dict[str, type] = {}
    result.update(variants)
    return result


def _json_input_type(annotation: TypeAnnotation) -> TypeAnnotation:
    """
    Project Python-only input annotations onto equivalent JSON-native types.

    Returns:
        TypeAnnotation: The JSON-native equivalent, or the original annotation.
    """
    allows_none = type(None) in get_args(annotation)
    unwrapped = _unwrap_optional(annotation)
    origin = get_origin(unwrapped)

    if origin in (Union, types.UnionType):
        members = get_args(unwrapped)
        if members and all(member is str or get_origin(member) is re.Pattern for member in members):
            result: TypeAnnotation = str
        else:
            return annotation
    elif origin is re.Pattern:
        result = str
    elif origin in (list, Collection, Sequence) and len(get_args(unwrapped)) == 1:
        element_type = get_args(unwrapped)[0]
        result = list[element_type]
    else:
        return annotation

    return result | None if allows_none else result


def _structured_variant_parameters(annotation: TypeAnnotation) -> dict[str, list[Parameter]] | None:
    """
    Derive JSON-native constructor parameters for a structured input's safe variants.

    Returns:
        dict[str, list[Parameter]] | None: Parameters keyed by variant, or None
            when the annotation is not a structured input.
    """
    variants = _structured_variant_types(annotation)
    if variants is None:
        return None
    return {name: json_input_parameters(implementation) for name, implementation in variants.items()}


def json_input_parameters(cls: type) -> list[Parameter]:
    """
    Derive constructor parameters with JSON-compatible input annotations.

    Returns:
        list[Parameter]: The constructor's input parameters.
    """
    if issubclass(cls, BaseModel):
        references = _structured_reference_types(cls)
        return [
            Parameter(
                name=name,
                description=info.description or "",
                param_type=None if name in references else info.annotation,
                default=REQUIRED_VALUE if info.is_required() else None if info.default_factory else info.default,
                reference=RegistryReference(component_type=references[name], annotation=info.annotation)
                if name in references
                else None,
                variants=None if name in references else _structured_variant_parameters(info.annotation),
            )
            for name, info in cls.model_fields.items()
        ]
    return [
        parameter.model_copy(update={"param_type": _json_input_type(parameter.param_type)})
        for parameter in derive_parameters(cls=cls)
    ]


def _structured_reference_types(cls: type) -> dict[str, ComponentType]:
    """
    Get inherited reference declarations for a configuration type.

    Returns:
        dict[str, ComponentType]: Registry references, with child overrides.
    """
    references: dict[str, ComponentType] = {}
    for owner in reversed(cls.__mro__):
        references.update(_STRUCTURED_INPUT_REFERENCES.get(owner, {}))
    return references


def _constructor_sources(cls: type) -> list[tuple[type, inspect.Signature]]:
    """
    Return the constructor signatures that form ``cls``'s build contract.

    The effective constructor is always first. When it explicitly declares
    ``**kwargs`` forwarding with ``forward_init_parameters``, the next constructor
    defined along the MRO is included. The same rule is applied recursively.

    Args:
        cls (type): The class whose constructor chain is inspected.

    Returns:
        list[tuple[type, inspect.Signature]]: Constructor owners and signatures
            in child-to-parent order.

    Raises:
        ValueError: If a constructor signature cannot be inspected.
    """
    owners = [owner for owner in cls.__mro__ if "__init__" in owner.__dict__]
    sources: list[tuple[type, inspect.Signature]] = []
    for index, owner in enumerate(owners):
        init = owner.__dict__["__init__"]
        try:
            signature = inspect.signature(init)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Failed to inspect __init__ signature for '{cls.__name__}': {exc}") from exc
        constructor = inspect.unwrap(init)
        namespace = {**vars(owner), owner.__name__: owner}
        parameters = []
        for param in signature.parameters.values():
            try:
                annotation = get_type_hints(
                    types.SimpleNamespace(__annotations__={param.name: param.annotation}),
                    globalns=getattr(constructor, "__globals__", {}),
                    localns=namespace,
                )[param.name]
            except (NameError, TypeError, AttributeError, SyntaxError) as exc:
                logger.debug("Unresolved annotation for %s.%s: %s", owner.__name__, param.name, exc)
                annotation = param.annotation
            parameters.append(param.replace(annotation=annotation))
        sources.append((owner, signature.replace(parameters=parameters)))

        if not init_parameters_are_forwarded(init) or index + 1 == len(owners):
            break
    return sources


def _parameters_from_signature(
    *,
    owner: type,
    signature: inspect.Signature,
    reference_overrides: dict[str, ComponentType],
) -> list[Parameter]:
    """
    Build parameters declared by one constructor signature.

    Args:
        owner (type): The class that defines the constructor.
        signature (inspect.Signature): The constructor signature.
        reference_overrides (dict[str, ComponentType]): Identifier-declared
            registry references keyed by constructor parameter name.

    Returns:
        list[Parameter]: Parameters declared by the constructor.
    """
    descriptions = _parse_arg_descriptions(owner)
    parameters: list[Parameter] = []
    for name, param in signature.parameters.items():
        if name in _SKIPPED_PARAM_NAMES or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        component_type = reference_overrides.get(name)
        if component_type is not None:
            parameters.append(
                Parameter(
                    name=name,
                    description=descriptions.get(name, ""),
                    default=_default_for(param),
                    reference=RegistryReference(component_type=component_type, annotation=param.annotation),
                )
            )
            continue

        param_type = None if param.annotation is inspect.Parameter.empty else param.annotation
        parameters.append(
            Parameter(
                name=name,
                description=descriptions.get(name, ""),
                default=_default_for(param),
                param_type=param_type,
                variants=_structured_variant_parameters(param_type),
            )
        )
    return parameters


def derive_parameters(*, cls: type, identifier_type: type[ComponentIdentifier] | None = None) -> list[Parameter]:
    """
    Derive the declarative ``Parameter`` list for ``cls`` from its constructor.

    Maps each settable constructor parameter to a ``Parameter``: parameters the
    identifier promotes as references carry a ``RegistryReference``; plain
    parameters carry the full constructor annotation as ``param_type``. When a constructor
    explicitly declares that its ``**kwargs`` are forwarded, the next constructor
    in MRO order is merged. Child declarations take precedence over same-named
    base declarations.

    Args:
        cls (type): The component class whose ``__init__`` drives derivation.
        identifier_type (type[ComponentIdentifier] | None): The domain identifier
            whose ``Param.*`` markers declare which parameters are registry
            references. When None, no parameter is treated as a reference.

    Returns:
        list[Parameter]: One ``Parameter`` per settable constructor parameter,
            ordered from the effective constructor through forwarded bases.

    Raises:
        ValueError: If the constructor signature cannot be inspected.
    """
    reference_overrides = identifier_type.get_reference_component_types() if identifier_type is not None else {}
    reference_overrides.update(_structured_reference_types(cls))
    parameters: list[Parameter] = []
    seen: set[str] = set()
    for owner, signature in _constructor_sources(cls):
        for parameter in _parameters_from_signature(
            owner=owner,
            signature=signature,
            reference_overrides=reference_overrides,
        ):
            if parameter.name in seen:
                continue
            parameters.append(parameter)
            seen.add(parameter.name)

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


def _registry_getter_for_component_type(
    component_type: ComponentType,
) -> Callable[[], _NamedInstanceRegistry] | None:
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
    from pyrit.registry.components import (
        ConverterRegistry,
        ScorerRegistry,
        TargetRegistry,
    )

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


def resolve_reference_value(
    *,
    component_type: ComponentType,
    value: Any,
    owner: str,
    name: str,
) -> Any:
    """
    Resolve a single registry-reference value (name -> instance) for ``component_type``.

    A string value is looked up by name in the component family's registry; an
    already-built instance passes through unchanged. Shares the same registry lookup
    and not-found errors used by the constructor-argument path, so a reference declared
    on a scenario (e.g. ``objective_target``) and one derived from a constructor
    signature resolve a name identically.

    Args:
        component_type (ComponentType): The registry family the reference resolves against.
        value (Any): The raw value (a registry name, or an instance to pass through).
        owner (str): The owning class name, for error messages.
        name (str): The parameter name, for error messages.

    Returns:
        Any: The resolved instance, or the value unchanged when already an instance.

    Raises:
        ValueError: If no registry is wired for ``component_type``, or the name is not registered.
    """
    getter = _registry_getter_for_component_type(component_type)
    if getter is None:
        raise ValueError(f"{owner}.{name}: no registry is wired for component type '{component_type}'.")
    return _resolve_registry_reference(value=value, getter=getter, owner=owner, name=name)


def resolve_constructor_args(
    *,
    cls: type,
    raw_args: dict[str, Any],
    identifier_type: type[ComponentIdentifier] | None = None,
) -> dict[str, Any]:
    """
    Resolve a flat argument dict into constructor-ready keyword arguments.

    Derives the ``Parameter`` contract for ``cls`` and applies it to
    ``raw_args``. For each raw argument: validate it is a declared parameter;
    resolve registry-reference parameters by name; coerce simple string values
    via ``Parameter.coerce_value``; pass everything else through unchanged.

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

        value_type = _unwrap_optional(param.param_type)
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
        elif param.variants is not None:
            resolved[name] = _resolve_structured_input(parameter=param, value=value)
        elif (isinstance(value, str) and param.is_string_coercible) or (
            isinstance(value_type, type) and issubclass(value_type, Enum)
        ):
            try:
                resolved[name] = param.coerce_value(value)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Parameter '{name}' of '{cls.__name__}': {e}") from e
        else:
            resolved[name] = value

    return resolved


def _resolve_structured_input(*, parameter: Parameter, value: Any) -> Any:
    """
    Build a declared structured-input variant from its JSON representation.

    Returns:
        Any: An existing structured input, None, or the constructed variant.

    Raises:
        ValueError: If the input shape, variant, or nested parameters are invalid.
    """
    annotation = _unwrap_optional(parameter.param_type)
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if value is None and type(None) in get_args(parameter.param_type):
        return None
    if get_origin(annotation) in (list, Collection, Sequence):
        if not isinstance(value, list):
            raise ValueError(f"Parameter '{parameter.name}': expected a list")
        element = parameter.model_copy(update={"param_type": get_args(annotation)[0]})
        return [_resolve_structured_input(parameter=element, value=item) for item in value]
    if isinstance(annotation, type) and isinstance(value, annotation):
        return value

    try:
        if not isinstance(value, dict) or set(value) - {"type", "parameters"}:
            raise ValueError("expected an object with 'type' and optional 'parameters'")
        variants = _structured_variant_types(annotation)
        if variants is None:
            raise ValueError("annotation does not declare structured input variants")
        name = value.get("type")
        if not isinstance(name, str) or name not in variants:
            raise ValueError(f"type must be one of {list(variants)}")
        supplied = value.get("parameters", {})
        if not isinstance(supplied, dict):
            raise ValueError("parameters must be an object")

        implementation = variants[name]
        declared = {nested.name: nested for nested in json_input_parameters(implementation)}
        unknown = supplied.keys() - declared.keys()
        if unknown:
            raise ValueError(f"unknown parameters for '{name}': {sorted(unknown)}")
        missing = [nested.name for nested in declared.values() if nested.required and nested.name not in supplied]
        if missing:
            raise ValueError(f"missing parameters for '{name}': {missing}")
        args = {key: resolve_json_parameter(parameter=declared[key], value=raw) for key, raw in supplied.items()}
        return implementation(**args)
    except (ValueError, TypeError, re.error) as exc:
        raise ValueError(f"Parameter '{parameter.name}': {exc}") from exc


def _coerce_structured_input(*, parameter: Parameter, value: Any) -> Any:
    """
    Validate a JSON-native nested value before applying shared coercion.

    Returns:
        Any: The validated and coerced nested value.

    Raises:
        ValueError: If the value does not match the declared JSON type.
    """
    try:
        annotation = parameter.param_type
        if get_origin(annotation) in (Union, types.UnionType) and parameter.variants is not None:
            scalar_members = [
                member
                for member in get_args(annotation)
                if member is not type(None) and _json_annotation_supported(member)
            ]
            if not scalar_members:
                raise ValueError(f"'{parameter.name}' expects a structured input")
            annotation = scalar_members[0]
            for member in scalar_members[1:]:
                annotation = annotation | member
        if not _json_annotation_supported(annotation):
            raise ValueError(f"'{parameter.name}' requires a declared structured variant or a Python value")
        validated = TypeAdapter(annotation).validate_json(json.dumps(value, allow_nan=False), strict=True)
    except ValidationError as exc:
        raise ValueError(f"'{parameter.name}' expects {parameter.type_name}: {exc}") from exc
    return parameter.coerce_value(validated)


def _json_annotation_supported(annotation: TypeAnnotation) -> bool:
    """
    Allow JSON scalars and containers, not implicit model or object construction.

    Returns:
        bool: Whether a type can be safely validated without constructing a component.
    """
    if annotation in (Any, JsonValue, str, int, float, bool, type(None), Path, date, datetime, AwareDatetime, UUID):
        return True
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return True
    origin = get_origin(annotation)
    if origin is Annotated:
        return _json_annotation_supported(get_args(annotation)[0])
    if origin is Literal:
        return all(isinstance(value, (str, int, float, bool)) or value is None for value in get_args(annotation))
    if origin in (list, Collection, Sequence, dict, Union, types.UnionType):
        return all(_json_annotation_supported(member) for member in get_args(annotation))
    return False


def resolve_json_parameter(*, parameter: Parameter, value: Any) -> Any:
    """
    Resolve one declared JSON input, rejecting unsupported live-object inputs.

    Returns:
        Any: The validated value or resolved declared component.

    Raises:
        ValueError: If a type, reference, or supported input does not match.
    """
    if parameter.reference is not None:
        reference = parameter.reference
        getter = _registry_getter_for_component_type(reference.component_type)
        if getter is None:
            raise ValueError(f"No registry for {reference.component_type}")
        if value is None and type(None) in get_args(reference.annotation):
            return None
        resolved = _resolve_registry_reference(
            value=value,
            getter=getter,
            owner="Technique",
            name=parameter.name,
            annotation=_unwrap_optional(reference.annotation),
        )
        try:
            TypeAdapter(reference.annotation, config=ConfigDict(arbitrary_types_allowed=True)).validate_python(
                resolved, strict=True
            )
        except ValidationError as exc:
            raise ValueError(f"Reference '{parameter.name}' has the wrong component type") from exc
        return resolved
    if value is None:
        if parameter.param_type in (Any, JsonValue, type(None)) or type(None) in get_args(parameter.param_type):
            return None
        raise ValueError(f"Parameter '{parameter.name}' does not accept null")
    if parameter.variants is not None:
        # A union can also accept a scalar, for example str | SeedPrompt.
        if not isinstance(value, (dict, list)):
            return _coerce_structured_input(parameter=parameter, value=value)
        return _resolve_structured_input(parameter=parameter, value=value)
    annotation = _unwrap_optional(parameter.param_type)
    origin = get_origin(annotation)
    if origin in (list, Collection, Sequence):
        if not isinstance(value, list):
            raise ValueError(f"Parameter '{parameter.name}' expects a list")
        child = parameter.model_copy(update={"param_type": get_args(annotation)[0] if get_args(annotation) else Any})
        return [resolve_json_parameter(parameter=child, value=item) for item in value]
    if parameter.opaque or not _json_annotation_supported(parameter.param_type):
        raise ValueError(f"Parameter '{parameter.name}' requires a Python value; it is not supported through REST")
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Parameter '{parameter.name}' has an invalid choice") from exc
    return _coerce_structured_input(parameter=parameter, value=value)


def resolve_json_constructor_args(
    *,
    cls: type,
    raw_args: dict[str, Any],
    identifier_type: type[ComponentIdentifier] | None = None,
    deferred: frozenset[str] = frozenset(),
    forbidden: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """
    Resolve JSON constructor settings without constructing the component.

    Returns:
        dict[str, Any]: Validated constructor settings.

    Raises:
        ValueError: If supplied or required settings are invalid.
    """
    declared = {param.name: param for param in derive_parameters(cls=cls, identifier_type=identifier_type)}
    unknown = raw_args.keys() - declared.keys()
    if unknown:
        raise ValueError(f"Unknown parameters for '{cls.__name__}': {sorted(unknown)}")
    if supplied_forbidden := raw_args.keys() & forbidden:
        raise ValueError(f"These parameters are supplied at execution: {sorted(supplied_forbidden)}")
    missing = [
        name for name, param in declared.items() if param.required and name not in raw_args and name not in deferred
    ]
    if missing:
        raise ValueError(f"Missing required parameters for '{cls.__name__}': {missing}")
    return {name: resolve_json_parameter(parameter=declared[name], value=value) for name, value in raw_args.items()}


# ---------------------------------------------------------------------------
# Resolve (declared list): raw args -> fully-materialized declared-parameter dict
# ---------------------------------------------------------------------------


def resolve_declared_params(
    *,
    declared: list[Parameter],
    raw_args: dict[str, Any],
    owner: str,
) -> dict[str, Any]:
    """
    Resolve ``raw_args`` against an explicit declared-parameter contract.

    The declared-list sibling of ``resolve_constructor_args``. Both translate a
    flat dict of raw arguments into ready values against the ``Parameter``
    contract, delegating the actual coercion/validation to the ``Parameter``
    model; they differ only in where the contract comes from and how it is
    consumed:

    - ``resolve_constructor_args`` derives the contract from a class ``__init__``,
      resolves registry references, coerces string values, and returns the kwargs
      subset for ``cls(**resolved)`` (the constructor supplies defaults).
    - ``resolve_declared_params`` takes an explicit ``list[Parameter]`` (e.g. a
      scenario's ``supported_parameters()``), has no references, coerces every
      supplied value, and **materializes every declared default** so the returned
      dict is a complete param bag. Params declared without a default land as
      ``None`` so callers can rely on ``params[name]`` never raising ``KeyError``.

    Args:
        declared (list[Parameter]): The declaration snapshot to validate against.
        raw_args (dict[str, Any]): Map of parameter name to raw value. Keys with
            ``None`` values are treated as absent (YAML ``null``).
        owner (str): Human-readable owner label used to prefix error messages,
            e.g. ``"Scenario 'FoundryScenario'"``.

    Returns:
        dict[str, Any]: Fully-materialized parameter dict.

    Raises:
        ValueError: Invalid declaration, unknown parameter, coercion failure, or
            value not in ``choices``.
    """
    _validate_declarations(declared=declared, owner=owner)

    declared_by_name = {param.name: param for param in declared}

    # None values are treated as absent so YAML `key: null` falls through to defaults.
    supplied = {name: value for name, value in raw_args.items() if value is not None}

    coerced: dict[str, Any] = {}
    for name, raw_value in supplied.items():
        param = declared_by_name.get(name)
        if param is None:
            # Stash unknowns so _reject_undeclared_params can list them all at once.
            coerced[name] = raw_value
            continue
        coerced[name] = param.coerce_value(raw_value)

    _reject_undeclared_params(params=coerced, declared=declared, owner=owner)

    for param in declared:
        if param.name in coerced:
            continue
        # Materialize every declared param so callers can rely on
        # ``params[name]`` never raising ``KeyError``. Params declared without an
        # explicit default land as None, and the owner raises a domain-specific
        # error at run time if it cannot proceed.
        coerced[param.name] = copy.deepcopy(param.coerce_value(param.default)) if param.default is not None else None

    return coerced


def _validate_declarations(*, declared: list[Parameter], owner: str) -> None:
    """
    Validate a declared-parameter snapshot for author mistakes.

    Args:
        declared (list[Parameter]): The declaration snapshot.
        owner (str): Owner label used to prefix error messages.

    Raises:
        ValueError: If declarations contain duplicate names, an unsupported
            ``param_type``, or a default that fails coercion (including
            membership for a constrained scalar).
    """
    seen: set[str] = set()
    for param in declared:
        if param.name in seen:
            raise ValueError(f"{owner} declares duplicate parameter name '{param.name}'.")
        seen.add(param.name)

        try:
            param.validate()
        except ValueError as exc:
            raise ValueError(f"{owner} {exc}") from exc

        if param.default is not None:
            try:
                param.coerce_value(param.default)
            except ValueError as exc:
                raise ValueError(f"{owner} parameter '{param.name}' has an invalid default: {exc}") from exc


def _reject_undeclared_params(*, params: dict[str, Any], declared: list[Parameter], owner: str) -> None:
    """
    Raise if ``params`` contains any key not in the ``declared`` snapshot.

    Specific to the declared-parameter path (``resolve_declared_params``): it
    reports every undeclared key at once. The constructor path
    (``resolve_constructor_args``) rejects unknown arguments inline instead.

    Args:
        params (dict[str, Any]): Coerced (declared names) or raw (unknown) values.
        declared (list[Parameter]): Declaration snapshot from the caller.
        owner (str): Owner label used to prefix error messages.

    Raises:
        ValueError: If any keys in ``params`` are not declared.
    """
    declared_names = {param.name for param in declared}
    unknown = sorted(set(params.keys()) - declared_names)
    if unknown:
        raise ValueError(
            f"{owner} received unknown parameter(s): {', '.join(unknown)}. "
            f"Supported parameters: "
            f"{', '.join(sorted(declared_names)) if declared_names else 'none'}."
        )


# ---------------------------------------------------------------------------
