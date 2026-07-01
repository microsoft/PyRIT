# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Declarative parameter model for registry and scenario construction."""

from __future__ import annotations

import copy
import types
from dataclasses import dataclass
from enum import Enum
from types import GenericAlias
from typing import Any, Literal, Union, get_args, get_origin

_SUPPORTED_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool)


class ComponentType(str, Enum):
    """
    The component family a registry reference resolves to.

    Each member maps one-to-one to a registry singleton that resolves references
    of that family by name (``TARGET`` → ``TargetRegistry``, ``CONVERTER`` →
    ``ConverterRegistry``, ``SCORER`` → ``ScorerRegistry``).
    """

    TARGET = "target"
    CONVERTER = "converter"
    SCORER = "scorer"


class ParameterDestination(str, Enum):
    """Where a declarative parameter is consumed at build time."""

    CONSTRUCTOR = "constructor"
    REGISTERED = "registered"


@dataclass(frozen=True)
class RegistryReference:
    """Self-describing reference to another registry-backed component."""

    component_type: ComponentType
    name: str | None = None
    annotation: Any | None = None


@dataclass(frozen=True)
class Parameter:
    """
    Describes a parameter that a PyRIT component accepts.

    ``param_type`` carries the value's type and its allowed set (a ``Literal[...]``
    or ``Enum`` *is* the allowed set). ``reference``, when set, marks the parameter
    as a registry reference: its value is supplied *by name* and resolved to a
    registered instance by the registry layer (``Parameter`` itself never resolves
    references).

    ``coerce_value`` and ``validate`` are the only public behaviors; all coercion
    branching lives behind them so callers never touch a free function.
    """

    name: str
    description: str
    default: Any = None
    param_type: type | GenericAlias | None = None
    reference: RegistryReference | None = None
    destination: ParameterDestination = ParameterDestination.CONSTRUCTOR

    @property
    def is_string_coercible(self) -> bool:
        """
        Whether a single string token can be coerced to this parameter's value.

        True for a non-reference plain scalar (``str`` / ``int`` / ``float`` /
        ``bool``) or ``Literal[...]`` parameter — exactly the forms a text field or
        CLI token can supply. References and structured types (lists, enums,
        arbitrary objects) are False and are surfaced/handled elsewhere.

        Returns:
            bool: True when a string can be coerced to this parameter's value.
        """
        if self.reference is not None:
            return False
        if self.param_type in _SUPPORTED_SCALAR_TYPES:
            return True
        return get_origin(self.param_type) is Literal

    def is_reference_to(self, component_type: ComponentType) -> bool:
        """
        Whether this parameter is a registry reference to the given component family.

        A reference parameter is supplied by name and resolved to a registered
        instance by the registry layer. This is the single source of truth for
        "does this parameter point at a ``TARGET`` / ``CONVERTER`` / ``SCORER``",
        so callers never re-derive it from ``reference`` internals.

        Args:
            component_type (ComponentType): The component family to test against.

        Returns:
            bool: True when this parameter is a reference to ``component_type``.
        """
        return self.reference is not None and self.reference.component_type is component_type

    def coerce_value(self, raw_value: Any) -> Any:
        """
        Coerce ``raw_value`` to this parameter's declared type.

        A reference parameter passes its value through unchanged (the registry
        layer resolves it by name). Otherwise it branches by shape: ``None``
        passes through (deep-copied), a ``list`` coerces per element, and a scalar
        form (including ``Literal``/``Enum``) coerces and validates membership.
        Arbitrary defaulted types pass through unchanged.

        Args:
            raw_value (Any): The raw value to coerce.

        Returns:
            Any: The coerced value (a deep copy for the ``None`` passthrough, a
                coerced list for list types, a coerced scalar for scalar types, or
                the raw value unchanged for reference/arbitrary types).

        Raises:
            ValueError: If the value cannot be coerced to a constrained scalar or
                list element type.
        """
        if self.reference is not None:
            return raw_value
        param_type = self.param_type
        if param_type is None:
            return copy.deepcopy(raw_value)
        if get_origin(param_type) is list:
            return _coerce_list(param_name=self.name, param_type=param_type, raw_value=raw_value)
        if _is_scalar_param_type(param_type):
            return _coerce_simple_value(param_name=self.name, annotation=param_type, raw_value=raw_value)
        return raw_value

    def validate(self) -> None:
        """
        Reject a declaration with an unsupported ``param_type``.

        Supported forms are a plain scalar, a constrained scalar
        (``Literal``/``Enum``), a ``list`` of any of those, a registry reference,
        or ``None``. An otherwise-unsupported type is tolerated only when the
        parameter declares a default (the builder simply does not supply it, and
        the value passes through unchanged).

        Raises:
            ValueError: If ``param_type`` is unsupported and no default is declared.
        """
        if self.reference is not None:
            return
        param_type = self.param_type
        if param_type is None or _is_scalar_param_type(param_type):
            return
        if get_origin(param_type) is list:
            type_args = get_args(param_type)
            element_type = type_args[0] if type_args else str
            if _is_scalar_param_type(element_type):
                return
        if self.default is not None:
            return

        raise ValueError(
            f"Parameter '{self.name}' has unsupported param_type {param_type!r}. "
            f"Supported types: str, int, float, bool, Literal[...], Enum, a list of those, "
            f"or None (or provide a default)."
        )


def _unwrap_optional(annotation: Any) -> Any:
    """
    Reduce ``Optional[X]`` / ``X | None`` to ``X`` (only for single-member unions).

    Returns:
        Any: ``X`` when ``annotation`` is a single-member optional union, otherwise the
            annotation unchanged.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _is_enum_type(annotation: Any) -> bool:
    """Return True when ``annotation`` is an ``Enum`` subclass."""
    return isinstance(annotation, type) and issubclass(annotation, Enum)


def _is_scalar_param_type(annotation: Any) -> bool:
    """
    Return True when ``annotation`` is a coercible scalar form.

    A scalar form is a plain scalar (``str`` / ``int`` / ``float`` / ``bool``) or a
    constrained scalar (``Literal[...]`` or an ``Enum`` subclass) that carries its
    own allowed set.

    Returns:
        bool: True when the annotation is a single coercible scalar form.
    """
    if annotation in _SUPPORTED_SCALAR_TYPES:
        return True
    if get_origin(annotation) is Literal:
        return True
    return _is_enum_type(annotation)


def _coerce_simple_value(*, param_name: str, annotation: Any, raw_value: Any) -> Any:
    """
    Coerce ``raw_value`` to a scalar ``annotation`` — the shared coercion core.

    Handles ``Optional[X]`` unwrap, ``Literal``/``Enum`` membership, and
    int/float/bool/str. Anything else passes through unchanged. Both the
    ``Parameter`` path (``coerce_value``) and the resolver's annotation path route
    through this function so they cannot diverge on coerced values.

    Returns:
        Any: The coerced value (a ``Literal``/``Enum`` member, an int/float/bool/str, or
            the raw value unchanged for unsupported annotations).

    Raises:
        ValueError: If the value is not a valid member of a ``Literal``/``Enum`` or
            cannot be coerced to the annotated scalar type.
    """
    annotation = _unwrap_optional(annotation)
    if get_origin(annotation) is Literal:
        return _coerce_literal(param_name=param_name, annotation=annotation, raw_value=raw_value)
    if _is_enum_type(annotation):
        return _coerce_enum(param_name=param_name, enum_type=annotation, raw_value=raw_value)
    if annotation is bool:
        return _coerce_bool(param_name=param_name, raw_value=raw_value)
    if annotation is int:
        return _coerce_scalar(param_name=param_name, scalar_type=int, raw_value=raw_value)
    if annotation is float:
        return _coerce_scalar(param_name=param_name, scalar_type=float, raw_value=raw_value)
    if annotation is str:
        return str(raw_value)
    return raw_value


def _coerce_literal(*, param_name: str, annotation: Any, raw_value: Any) -> Any:
    """
    Validate ``raw_value`` against a ``Literal`` and return the matching member.

    Returns:
        Any: The matching ``Literal`` member.

    Raises:
        ValueError: If ``raw_value`` does not match any allowed member.
    """
    allowed = get_args(annotation)
    for member in allowed:
        if str(raw_value) == str(member):
            return member
    raise ValueError(f"Parameter '{param_name}' expected one of {[str(a) for a in allowed]}, got {raw_value!r}.")


def _coerce_enum(*, param_name: str, enum_type: type[Enum], raw_value: Any) -> Any:
    """
    Validate ``raw_value`` against an ``Enum`` and return the matching member.

    Returns:
        Any: The matching ``Enum`` member.

    Raises:
        ValueError: If ``raw_value`` does not match any enum member by identity, value, or name.
    """
    for member in enum_type:
        if raw_value is member or str(raw_value) == str(member.value) or str(raw_value) == member.name:
            return member
    raise ValueError(
        f"Parameter '{param_name}' expected one of {[member.name for member in enum_type]}, got {raw_value!r}."
    )


def _coerce_scalar(*, param_name: str, scalar_type: type, raw_value: Any) -> Any:
    """
    Coerce ``raw_value`` to ``int`` or ``float`` while rejecting native ``bool`` inputs.

    Returns:
        Any: The value coerced to ``scalar_type``.

    Raises:
        ValueError: If ``raw_value`` is a native ``bool`` or cannot be coerced to ``scalar_type``.
    """
    if isinstance(raw_value, bool):
        raise ValueError(
            f"Parameter '{param_name}' expects {scalar_type.__name__} but received a bool ({raw_value!r})."
        )
    try:
        return scalar_type(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Parameter '{param_name}' could not be coerced to {scalar_type.__name__}: {raw_value!r} ({exc})."
        ) from exc


def _coerce_bool(*, param_name: str, raw_value: Any) -> bool:
    """
    Parse ``raw_value`` as a boolean, accepting the usual textual forms.

    Returns:
        bool: The parsed boolean value.

    Raises:
        ValueError: If ``raw_value`` cannot be interpreted as a boolean.
    """
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in ("true", "1", "yes"):
            return True
        if normalized in ("false", "0", "no"):
            return False
    raise ValueError(
        f"Parameter '{param_name}' expects bool but received {raw_value!r}; could not interpret as a boolean. "
        f"Accepted values: true/false, 1/0, yes/no (case-insensitive), or a native bool."
    )


def _coerce_list(*, param_name: str, param_type: Any, raw_value: Any) -> list[Any]:
    """
    Coerce a ``list[T]`` parameter by coercing each element to ``T``.

    Returns:
        list[Any]: The list with each element coerced to the declared element type.

    Raises:
        ValueError: If ``raw_value`` is not a list or the element type is unsupported.
    """
    if not isinstance(raw_value, list):
        raise ValueError(
            f"Parameter '{param_name}' expects a list but received {type(raw_value).__name__} ({raw_value!r})."
        )

    type_args = get_args(param_type)
    element_type = type_args[0] if type_args else str

    if _is_scalar_param_type(element_type):
        return [
            _coerce_simple_value(param_name=param_name, annotation=element_type, raw_value=item) for item in raw_value
        ]
    raise ValueError(
        f"Parameter '{param_name}' has unsupported list element type {element_type!r}. "
        f"Supported list element types: str, int, float, bool, or Literal[...]."
    )
