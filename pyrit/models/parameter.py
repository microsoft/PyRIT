# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Declarative parameter model for registry and scenario construction."""

from __future__ import annotations

import copy
import types
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer, model_validator

from pyrit.common.apply_defaults import REQUIRED_VALUE

_SUPPORTED_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool)
_SCALAR_NAME_TO_TYPE: dict[str, type] = {"int": int, "float": float, "bool": bool, "str": str}


class ComponentType(str, Enum):
    """
    The component family a registry reference resolves to.

    Each member maps one-to-one to a registry singleton that resolves references
    of that family by name (``TARGET`` → ``TargetRegistry``, ``CONVERTER`` →
    ``ConverterRegistry``, ``SCORER`` → ``ScorerRegistry``, ``SCENARIO`` →
    ``ScenarioRegistry``).
    """

    TARGET = "target"
    CONVERTER = "converter"
    SCORER = "scorer"
    SCENARIO = "scenario"


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


class Parameter(BaseModel):
    """
    Describes a parameter that a PyRIT component accepts.

    This is the single JSON-serializable parameter descriptor reused across the
    registry, scenarios, the backend API, and the CLI. ``param_type`` carries the
    value's live Python type and its allowed set (a ``Literal[...]`` or ``Enum``
    *is* the allowed set) and drives ``coerce_value`` / ``validate``; it is **not**
    serialized. Serialization instead projects the type into the display fields
    ``type_name``, ``choices``, and ``is_list`` (plus ``required`` from the
    ``REQUIRED_VALUE`` sentinel), so a consumer can rebuild a usable contract from
    the registry without the live type travelling on the wire.

    ``reference``, when set, marks the parameter as a registry reference: its value
    is supplied *by name* and resolved to a registered instance by the registry
    layer (``Parameter`` itself never resolves references). It is also excluded
    from serialization.

    ``coerce_value`` and ``validate`` are the only public behaviors; all coercion
    branching lives behind them so callers never touch a free function.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str = Field(description="The parameter's name.")
    description: str = Field(description="Human-readable description of the parameter.")
    default: Any = Field(
        default=None,
        description=(
            "Default value, serialized as a display string for a scalar or a list of display "
            "strings for a list default (None when required or absent)."
        ),
    )
    param_type: Any = Field(
        default=None,
        exclude=True,
        description="Live Python type driving coercion; not serialized (see type_name/choices/is_list).",
    )
    reference: RegistryReference | None = Field(
        default=None,
        exclude=True,
        description="Set when the parameter references another registry component (resolved by name); not serialized.",
    )
    destination: ParameterDestination = Field(
        default=ParameterDestination.CONSTRUCTOR,
        exclude=True,
        description="Where the parameter is consumed at build time; not serialized.",
    )

    @model_validator(mode="before")
    @classmethod
    def _reconstruct_param_type_from_wire(cls, data: Any) -> Any:
        """
        Rebuild the live ``param_type`` when validating from a serialized payload.

        Serialization drops the live ``param_type`` and projects it onto the
        display fields ``type_name`` / ``choices`` / ``is_list``. A client that
        deserializes the wire form (e.g. the CLI consuming the REST catalog) has
        those fields but no live type; this reconstructs a coercion-capable
        ``param_type`` from them so the round-tripped ``Parameter`` can still
        coerce and validate values. In-process construction (which already
        supplies a live ``param_type``, or supplies neither) is left untouched.

        Returns:
            Any: The input unchanged, or a copy with ``param_type`` reconstructed
                from the serialized display fields.
        """
        if not isinstance(data, dict):
            return data
        if data.get("param_type") is not None or "type_name" not in data:
            return data
        data = dict(data)
        data["param_type"] = _param_type_from_display(
            type_name=data.get("type_name"),
            choices=data.get("choices"),
            is_list=bool(data.get("is_list")),
        )
        return data

    @computed_field
    @property
    def type_name(self) -> str:
        """Display name of the parameter's type (e.g. ``'int'``, ``'str'``, ``'list[str]'``, ``'any'``)."""
        return _render_type_name(self.param_type)

    @computed_field
    @property
    def required(self) -> bool:
        """Whether the parameter must be supplied (its default is the ``REQUIRED_VALUE`` sentinel)."""
        return self.default is REQUIRED_VALUE

    @computed_field
    @property
    def choices(self) -> list[str] | None:
        """Allowed values for a constrained scalar (``Literal`` / ``Enum``), or None when unconstrained."""
        members = display_choices(self.param_type)
        return [str(member) for member in members] if members is not None else None

    @computed_field
    @property
    def is_list(self) -> bool:
        """True when the parameter accepts a list of values (e.g. ``list[str]``)."""
        return get_origin(self.param_type) is list

    @field_serializer("default")
    def _serialize_default(self, value: Any) -> str | list[str] | None:
        """
        Serialize the default for display (None for a required or absent default).

        A scalar default renders as a single display string; a list default (e.g. for a
        ``list[str]`` parameter) renders as a list of display strings so a list-valued
        default round-trips as a list instead of being flattened to ``"['x']"``.

        Returns:
            str | list[str] | None: The default rendered as a display string (scalar), a
                list of display strings (list default), or None when the default is absent
                or the ``REQUIRED_VALUE`` sentinel.
        """
        if value is None or value is REQUIRED_VALUE:
            return None
        if isinstance(value, list):
            return [_render_default_value(item) for item in value]
        return _render_default_value(value)

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

    def validate(self) -> None:  # type: ignore[ty:invalid-method-override]
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


def _render_default_value(value: Any) -> str:
    """
    Render a single default value as a display string.

    Returns:
        str: ``value`` rendered as a string (an ``Enum`` renders as its member value).
    """
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _param_type_from_display(*, type_name: str | None, choices: list[str] | None, is_list: bool) -> Any:
    """
    Reconstruct a coercion-capable ``param_type`` from serialized display fields.

    Inverse of the ``type_name`` / ``choices`` / ``is_list`` projection: maps the
    display base scalar name back to a concrete scalar type, rebuilds a
    constrained set as ``Literal[...]`` from ``choices`` (typed by the base
    scalar), and wraps the element type in ``list[...]`` for a list parameter.
    The unconstrained ``"any"`` (or an absent name) maps back to ``None``.

    Args:
        type_name (str | None): Display type name (e.g. ``"int"``, ``"list[str]"``, ``"any"``).
        choices (list[str] | None): Allowed values for a constrained scalar, or None.
        is_list (bool): True when the parameter accepts a list of values.

    Returns:
        Any: The reconstructed ``param_type`` (a scalar type, a ``Literal[...]``, a
            ``list[...]`` of either, or None for the unconstrained case).
    """
    if not type_name or type_name == "any":
        return None
    base_name = type_name.removeprefix("list[").rstrip("]") if is_list else type_name
    base_type: type = _SCALAR_NAME_TO_TYPE.get(base_name, str)
    if choices:
        coerced = tuple(_coerce_simple_value(param_name="", annotation=base_type, raw_value=c) for c in choices)
        element_type: Any = Literal[coerced]  # ty: ignore[invalid-type-form]
    else:
        element_type = base_type
    return list[element_type] if is_list else element_type  # ty: ignore[invalid-type-form]


def _render_type_name(param_type: Any) -> str:
    """
    Render a ``Parameter.param_type`` value as a short user-facing string.

    A constrained scalar (``Literal[...]``) renders as its base scalar name so the
    display + round-trip works; the allowed members travel via ``choices``. A
    ``list[...]`` renders as ``list[<element>]`` and ``None`` renders as ``"any"``.
    ``Optional[X]`` / ``X | None`` is unwrapped to ``X`` first, matching ``choices``
    and coercion, so the base scalar name surfaces (e.g. ``Optional[int]`` → ``"int"``).

    Args:
        param_type (Any): The parameter type (None, builtin, ``Literal``, or a
            parameterized generic such as ``list[str]``).

    Returns:
        str: Display string (e.g. ``"int"``, ``"list[str]"``, ``"any"``).
    """
    if param_type is None:
        return "any"
    param_type = _unwrap_optional(param_type)
    if get_origin(param_type) is Literal:
        args = get_args(param_type)
        return type(args[0]).__name__ if args else "str"
    if get_origin(param_type) is list:
        type_args = get_args(param_type)
        element_type = type_args[0] if type_args else str
        if get_origin(element_type) is Literal:
            element_args = get_args(element_type)
            element_name = type(element_args[0]).__name__ if element_args else "str"
            return f"list[{element_name}]"
    # Detect parameterized generics (list[str], dict[str, int], ...) reliably across Python
    # versions: get_origin returns the unparameterized type for GenericAlias, None otherwise.
    if get_origin(param_type) is not None:
        return str(param_type)
    if isinstance(param_type, type):
        return param_type.__name__
    return str(param_type)


def display_choices(param_type: Any) -> tuple[Any, ...] | None:
    """
    Derive the allowed-value display list from a constrained-scalar ``param_type``.

    This is the presentation projection of an allowed set: a ``Parameter`` stores
    the constraint as a ``Literal[...]`` / ``Enum`` type, and serializers render the
    members on demand instead of reading a separate field. ``Optional[X]`` /
    ``X | None`` is unwrapped first.

    Args:
        param_type (Any): The parameter's type annotation.

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
