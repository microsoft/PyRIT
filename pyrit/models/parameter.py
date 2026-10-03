# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Declarative parameter model for registry and scenario construction."""

from __future__ import annotations

import copy
import types
from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Mapping, MutableMapping, MutableSequence, MutableSet, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ForwardRef, Literal, TypeAlias, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer, model_validator

from pyrit.common.apply_defaults import REQUIRED_VALUE

#: How registry callers supply a parameter; see ``Parameter.input_kind``.
ParameterInputKind: TypeAlias = Literal[
    "scalar", "collection", "reference", "structured", "in_process_only", "unsupported"
]
_JSON_INPUT_KINDS: frozenset[str] = frozenset({"scalar", "collection", "reference", "structured"})
_SEQUENCE_ORIGINS: tuple[Any, ...] = (
    list,
    tuple,
    set,
    frozenset,
    Collection,
    Sequence,
    MutableSequence,
    Iterable,
    AbstractSet,
    MutableSet,
)
_SET_ORIGINS: tuple[Any, ...] = (set, frozenset, AbstractSet, MutableSet)
_MAPPING_ORIGINS: tuple[Any, ...] = (dict, Mapping, MutableMapping)
_SUPPORTED_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, Path)
_SCALAR_NAME_TO_TYPE: dict[str, type | types.UnionType] = {
    "Path": Path,
    "Path | str": Path | str,
    "bool": bool,
    "float": float,
    "int": int,
    "str": str,
}


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


class StructuredParameterValue(ABC):
    """A parameter value with explicitly allowed structured variants."""

    @classmethod
    @abstractmethod
    def get_registry_input_variants(cls) -> dict[str, type[StructuredParameterValue]]:
        """
        Declare the implementations available for registry construction.

        Returns:
            dict[str, type[StructuredParameterValue]]: Input names mapped to subclasses of the declaring type.
        """
        ...


class Parameter(BaseModel):
    """
    Describes a parameter that a PyRIT component accepts.

    This is the single JSON-serializable parameter descriptor reused across the
    registry, scenarios, the backend API, and the CLI. ``param_type`` carries the
    value's live Python type and its allowed set (a ``Literal[...]`` or ``Enum``
    *is* the allowed set) and drives ``coerce_value`` / ``validate``; it is **not**
    serialized. Serialization instead projects the type into the display fields
    ``type_name``, ``choices``, and ``is_list`` (plus ``required`` from the
    ``REQUIRED_VALUE`` sentinel and ``input_kind``, which says how registry callers
    supply the value), so a consumer can rebuild a usable contract from
    the registry without the live type travelling on the wire.

    ``reference``, when set, marks the parameter as a registry reference: its value
    is supplied *by name* and resolved to a registered instance by the registry
    layer (``Parameter`` itself never resolves references). The live reference is
    excluded from serialization; ``reference_type`` exposes its component family,
    while ``type_name`` and ``is_list`` expose whether clients supply one name or a
    list of names.

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
    variants: dict[str, list[Parameter]] | None = Field(
        default=None,
        description="Named structured-input variants and their constructor parameters, supplied by the registry.",
    )
    destination: ParameterDestination = Field(
        default=ParameterDestination.CONSTRUCTOR,
        exclude=True,
        description="Where the parameter is consumed at build time; not serialized.",
    )
    wire_input_kind: ParameterInputKind | None = Field(
        default=None,
        exclude=True,
        description=(
            "The ``input_kind`` read from a serialized payload. A ``param_type`` rebuilt from display "
            "fields cannot always express it, so the serialized value is kept. Not serialized."
        ),
    )
    opaque: bool = Field(
        default=False,
        exclude=True,
        description=(
            "When True, the value is a live object passed through by identity: it is neither "
            "coerced nor copied, and no ``param_type`` is required. Use for run-resolved inputs "
            "the scalar/list model can't represent (e.g. a live config object or a mapping of "
            "converter instances). Not serialized."
        ),
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
        needs_param_type = data.get("param_type") is None and "type_name" in data
        needs_reference = data.get("reference") is None and data.get("reference_type") is not None
        if not needs_param_type and not needs_reference:
            return data
        data = dict(data)
        if needs_param_type:
            data["param_type"] = _param_type_from_display(
                type_name=data.get("type_name"),
                choices=data.get("choices"),
                is_list=bool(data.get("is_list")),
            )
            data.setdefault("wire_input_kind", data.get("input_kind"))
        if needs_reference:
            data["reference"] = RegistryReference(
                component_type=ComponentType(data["reference_type"]),
                annotation=data.get("param_type"),
            )
        return data

    @property
    def _display_type(self) -> Any:
        """Wire type, where registry references are supplied as names."""
        if self.reference is None:
            return _unwrap_optional(self.param_type)
        annotation = _unwrap_optional(self.reference.annotation)
        return list[str] if get_origin(annotation) is list else str

    @computed_field
    @property
    def type_name(self) -> str:
        """Display name of the parameter's type (e.g. ``'int'``, ``'str'``, ``'list[str]'``, ``'any'``)."""
        return _render_type_name(self._display_type)

    @computed_field
    @property
    def required(self) -> bool:
        """Whether the parameter must be supplied (its default is the ``REQUIRED_VALUE`` sentinel)."""
        return self.default is REQUIRED_VALUE

    @computed_field
    @property
    def choices(self) -> list[str] | None:
        """Allowed values for a constrained scalar (``Literal`` / ``Enum``), or None when unconstrained."""
        members = display_choices(self._display_type)
        return [str(member) for member in members] if members is not None else None

    @computed_field
    @property
    def is_list(self) -> bool:
        """True when the parameter accepts a list of values (e.g. ``list[str]``)."""
        return get_origin(self._display_type) is list

    @property
    def is_path(self) -> bool:
        """Whether this is a local filesystem path parameter."""
        return self.reference is None and _unwrap_optional(self.param_type) is Path

    @property
    def is_path_or_str(self) -> bool:
        """Whether this parameter accepts paths or strings without URL normalization."""
        return self.reference is None and _is_path_or_str(self.param_type)

    @computed_field
    @property
    def reference_type(self) -> str | None:
        """Registry component family this parameter references, or None."""
        return self.reference.component_type.value if self.reference is not None else None

    @computed_field
    @property
    def input_kind(self) -> ParameterInputKind:
        """
        How registry callers supply this parameter.

        ``scalar`` and ``collection`` parameters take JSON values of their declared type,
        ``reference`` parameters take registry names, and ``structured`` parameters take one of
        their declared variants. ``in_process_only`` parameters take only live Python objects,
        and ``unsupported`` parameters have a type the registry cannot describe; neither takes
        JSON.
        """
        if self.wire_input_kind is not None:
            return self.wire_input_kind
        if self.reference is not None:
            return "reference"
        if self.variants is not None:
            return "structured"
        if self.opaque:
            return "in_process_only"
        return _annotation_input_kind(self.param_type)

    @property
    def is_json_configurable(self) -> bool:
        """Whether registry callers can supply this parameter as JSON."""
        return self.input_kind in _JSON_INPUT_KINDS

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
        ``bool`` / ``Path`` / ``Path | str``), ``Literal[...]``, or ``Enum`` parameter — exactly the forms a
        text field or CLI token can supply. References and structured types (lists
        and arbitrary objects) are False and are surfaced/handled elsewhere.

        Returns:
            bool: True when a string can be coerced to this parameter's value.
        """
        if self.reference is not None or self.opaque:
            return False
        return _is_scalar_param_type(_unwrap_optional(self.param_type))

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

        An opaque or reference parameter passes its value through unchanged (by
        identity — the registry layer resolves a reference by name; an opaque
        value is a live object owned by the caller). Otherwise it branches by
        shape: ``None`` passes through (deep-copied), a ``list`` coerces per
        element, and a scalar form (including ``Literal``/``Enum``) coerces and
        validates membership. Arbitrary defaulted types pass through unchanged.

        Args:
            raw_value (Any): The raw value to coerce.

        Returns:
            Any: The coerced value (the raw value unchanged for opaque/reference/
                arbitrary types, a deep copy for the ``None`` passthrough, a
                coerced list for list types, or a coerced scalar for scalar types).

        Raises:
            ValueError: If the value cannot be coerced to a constrained scalar or
                list element type.
        """
        if self.reference is not None or self.opaque:
            return raw_value
        param_type = self.param_type
        if raw_value is None and type(None) in get_args(param_type):
            return None
        param_type = _unwrap_optional(param_type)
        if param_type is None:
            return copy.deepcopy(raw_value)
        if get_origin(param_type) is list:
            return _coerce_list(param_name=self.name, param_type=param_type, raw_value=raw_value)
        if _is_scalar_param_type(param_type):
            return _coerce_simple_value(param_name=self.name, annotation=param_type, raw_value=raw_value)
        return raw_value

    def coerce_json_value(self, value: Any, *, owner: str) -> Any:
        """
        Convert a JSON value to this parameter's declared type under the registry contract.

        A ``scalar`` or ``collection`` parameter takes JSON of its declared type: arrays become
        the declared list, tuple, or set, and enum or literal choices become their members. A
        path is accepted only where the parameter is itself a ``Path`` or ``Path | str``. Other
        parameters take no JSON. ``None`` is accepted wherever the parameter allows it.

        Args:
            value (Any): JSON data: None, a string, number, boolean, list, or string-keyed dict.
            owner (str): The class name used in error messages.

        Returns:
            Any: The value converted to the declared type.

        Raises:
            ValueError: If the parameter cannot be supplied as JSON, or the value does not match
                its declared type.
        """
        if value is None and (self.default is None or type(None) in _union_members(self.param_type)):
            return None
        input_kind = self.input_kind
        if input_kind == "in_process_only":
            raise ValueError(
                f"Parameter '{self.name}' of '{owner}' accepts only a Python object ({self.type_name}), not JSON."
            )
        if input_kind not in ("scalar", "collection"):
            raise ValueError(
                f"Parameter '{self.name}' of '{owner}' has a type the registry does not support "
                f"({self.type_name}), so it cannot be set from JSON."
            )
        try:
            return _convert_json_value(self.param_type, value, allow_path=True)
        except _JsonMismatchError:
            raise ValueError(f"Parameter '{self.name}' of '{owner}' expects {self.type_name}.") from None

    def validate(self) -> None:  # type: ignore[ty:invalid-method-override]
        """
        Reject a declaration with an unsupported ``param_type``.

        Supported forms are a plain scalar, a constrained scalar
        (``Literal``/``Enum``), a ``list`` of any of those, a registry reference,
        an opaque passthrough, or ``None``. An otherwise-unsupported type is
        tolerated only when the parameter declares a default (the builder simply
        does not supply it, and the value passes through unchanged).

        Raises:
            ValueError: If ``param_type`` is unsupported and no default is declared.
        """
        if self.reference is not None or self.opaque or self.variants is not None:
            return
        param_type = _unwrap_optional(self.param_type)
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
            f"Supported types: str, int, float, bool, Path, Path | str, Literal[...], Enum, a list of those, "
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


def _is_path_or_str(annotation: Any) -> bool:
    """Return whether the annotation is ``Path | str``, optionally including None."""
    return get_origin(annotation) in (Union, types.UnionType) and set(get_args(annotation)) in (
        {Path, str},
        {Path, str, type(None)},
    )


def _is_scalar_param_type(annotation: Any) -> bool:
    """
    Return True when ``annotation`` is a coercible scalar form.

    A scalar form is a plain scalar (``str`` / ``int`` / ``float`` / ``bool`` / ``Path``) or a
    constrained scalar (``Literal[...]`` or an ``Enum`` subclass) that carries its
    own allowed set.

    Returns:
        bool: True when the annotation is a single coercible scalar form.
    """
    if annotation in _SUPPORTED_SCALAR_TYPES or _is_path_or_str(annotation):
        return True
    if get_origin(annotation) is Literal:
        return True
    return _is_enum_type(annotation)


class _JsonMismatchError(Exception):
    """Raised when a JSON value does not match a declared type."""


def _union_members(annotation: Any) -> tuple[Any, ...]:
    """
    Return the members of a union annotation, or the annotation alone.

    Returns:
        tuple[Any, ...]: The union's members, including ``NoneType`` when present.
    """
    if get_origin(annotation) in (Union, types.UnionType):
        return get_args(annotation)
    return (annotation,)


def _is_unresolved(annotation: Any) -> bool:
    """
    Return whether an annotation is, or contains, a forward reference that was never resolved.

    Returns:
        bool: True when a string or ``ForwardRef`` stands in for a type.
    """
    if isinstance(annotation, (str, ForwardRef)):
        return True
    if get_origin(annotation) is Literal:
        return False
    return any(_is_unresolved(arg) for arg in get_args(annotation))


def _is_json_scalar_type(annotation: Any) -> bool:
    """
    Return whether JSON expresses the annotation as one string, number, or boolean.

    Returns:
        bool: True for ``str``, ``int``, ``float``, ``bool``, an ``Enum``, or a ``Literal`` of those values.
    """
    if annotation in (str, int, float, bool) or _is_enum_type(annotation):
        return True
    return get_origin(annotation) is Literal and all(
        arg is None or type(arg) in (str, int, float, bool) for arg in get_args(annotation)
    )


def _is_json_element_type(annotation: Any) -> bool:
    """
    Return whether a collection element type can be expressed in JSON.

    Paths are not, so a collection cannot carry server file paths.

    Returns:
        bool: True for JSON scalars, JSON collections, ``Any``, ``None``, and unions of those.
    """
    if annotation is Any or annotation is type(None):
        return True
    if get_origin(annotation) in (Union, types.UnionType):
        return all(_is_json_element_type(member) for member in get_args(annotation))
    return _is_json_scalar_type(annotation) or _is_json_collection_type(annotation)


def _is_json_collection_type(annotation: Any) -> bool:
    """
    Return whether the annotation is a list, tuple, set, or string-keyed mapping of JSON values.

    Returns:
        bool: True when a JSON array or object can express the annotation.
    """
    origin = get_origin(annotation) or annotation
    args = [arg for arg in get_args(annotation) if arg is not Ellipsis]
    if origin in _MAPPING_ORIGINS:
        key_type, value_type = args if len(args) == 2 else (str, Any)
        return key_type in (str, Any) and _is_json_element_type(value_type)
    if origin in _SEQUENCE_ORIGINS:
        return all(_is_json_element_type(arg) for arg in args)
    return False


def _member_input_kind(annotation: Any, *, in_union: bool) -> ParameterInputKind:
    """
    Classify one member of a value parameter's annotation.

    A ``Path`` is a scalar only when it is the whole type; inside a wider union it can only be
    passed as a live object.

    Returns:
        ParameterInputKind: ``scalar``, ``collection``, ``in_process_only``, or ``unsupported``.
    """
    if annotation is Any:
        return "unsupported"
    if annotation is Path:
        return "in_process_only" if in_union else "scalar"
    if _is_json_scalar_type(annotation):
        return "scalar"
    if _is_json_collection_type(annotation):
        return "collection"
    return "in_process_only"


def _annotation_input_kind(annotation: Any) -> ParameterInputKind:
    """
    Classify a value parameter's annotation under the registry input contract.

    A union takes JSON when any member does; its other members can still be passed as live
    objects. Unannotated parameters, ``Any``, and unresolved annotations are unsupported.

    Returns:
        ParameterInputKind: ``scalar``, ``collection``, ``in_process_only``, or ``unsupported``.
    """
    if annotation is None or _is_unresolved(annotation):
        return "unsupported"
    if _is_path_or_str(annotation):
        return "scalar"
    members = [member for member in _union_members(annotation) if member is not type(None)]
    kinds = {_member_input_kind(member, in_union=len(members) > 1) for member in members}
    precedence: tuple[ParameterInputKind, ...] = ("unsupported", "collection", "scalar", "in_process_only")
    return next((kind for kind in precedence if kind in kinds), "unsupported")


def _convert_json_value(annotation: Any, value: Any, *, allow_path: bool) -> Any:
    """
    Convert a JSON value to ``annotation``.

    A union takes the value as its first member that matches. ``allow_path`` is True only
    for the parameter's own type, so paths are never read from inside a union or collection,
    except for an explicit ``Path | str``.

    Returns:
        Any: The converted value.

    Raises:
        _JsonMismatchError: If the value does not match the annotation.
    """
    if annotation is Any:
        return value
    all_members = _union_members(annotation)
    members = [member for member in all_members if member is not type(None)]
    if value is None:
        if len(members) < len(all_members):
            return None
        raise _JsonMismatchError
    if allow_path and _is_path_or_str(annotation):
        if type(value) is str:
            return value
        raise _JsonMismatchError
    if len(members) == 1:
        return _convert_json_member(members[0], value, allow_path=allow_path)
    for member in members:
        try:
            return _convert_json_member(member, value, allow_path=False)
        except _JsonMismatchError:
            continue
    raise _JsonMismatchError


def _convert_json_member(annotation: Any, value: Any, *, allow_path: bool) -> Any:
    """
    Convert a non-null JSON value to one non-union type.

    Choices match the way ``coerce_value`` matches them, so their display strings are accepted.

    Returns:
        Any: The converted value.

    Raises:
        _JsonMismatchError: If the value does not match the type.
    """
    if annotation is Any:
        return value
    if annotation in (str, bool):
        if type(value) is annotation:
            return value
    elif annotation is int:
        if type(value) is int:
            return value
    elif annotation is float:
        if type(value) in (int, float):
            try:
                return float(value)
            except OverflowError:
                pass
    elif annotation is Path:
        if allow_path and type(value) is str:
            return Path(value)
    elif get_origin(annotation) is Literal or _is_enum_type(annotation):
        if type(value) in (str, int, float, bool):
            try:
                return _coerce_simple_value(param_name="", annotation=annotation, raw_value=value)
            except ValueError:
                pass
    else:
        return _convert_json_collection(annotation, value)
    raise _JsonMismatchError


def _convert_json_collection(annotation: Any, value: Any) -> Any:
    """
    Convert a JSON array or object to a declared collection type.

    Returns:
        Any: A list, tuple, set, frozenset, or dict holding the converted items.

    Raises:
        _JsonMismatchError: If the value does not match the collection type.
    """
    origin = get_origin(annotation) or annotation
    args = get_args(annotation)
    if origin in _MAPPING_ORIGINS:
        key_type, value_type = args if len(args) == 2 else (str, Any)
        if type(value) is not dict or key_type not in (str, Any):
            raise _JsonMismatchError
        return {key: _convert_json_value(value_type, item, allow_path=False) for key, item in value.items()}
    if origin not in _SEQUENCE_ORIGINS or type(value) is not list:
        raise _JsonMismatchError
    if origin is tuple and args and not (len(args) == 2 and args[1] is Ellipsis):
        if len(args) != len(value):
            raise _JsonMismatchError
        return tuple(_convert_json_value(arg, item, allow_path=False) for arg, item in zip(args, value, strict=True))
    element_type = args[0] if args else Any
    items = [_convert_json_value(element_type, item, allow_path=False) for item in value]
    if origin is tuple:
        return tuple(items)
    if origin in _SET_ORIGINS:
        try:
            return frozenset(items) if origin is frozenset else set(items)
        except TypeError as exc:
            raise _JsonMismatchError from exc
    return items


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
    if _is_path_or_str(annotation):
        if isinstance(raw_value, (Path, str)):
            return raw_value
        raise ValueError(f"Parameter '{param_name}' expects a Path or str, got {type(raw_value).__name__}.")
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
    if annotation is Path:
        return Path(raw_value)
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
    base_type = _SCALAR_NAME_TO_TYPE.get(base_name, str)
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
    if _is_path_or_str(param_type):
        return "Path | str"
    if get_origin(param_type) is Literal:
        args = get_args(param_type)
        literal_type_name: str = type(args[0]).__name__ if args else "str"
        return literal_type_name
    if get_origin(param_type) is list:
        type_args = get_args(param_type)
        element_type = _unwrap_optional(type_args[0]) if type_args else str
        if get_origin(element_type) is Literal:
            element_args = get_args(element_type)
            element_name = type(element_args[0]).__name__ if element_args else "str"
            return f"list[{element_name}]"
        if isinstance(element_type, type) and issubclass(element_type, Enum):
            member = next(iter(element_type), None)
            return f"list[{type(member.value).__name__ if member is not None else 'str'}]"
        if _is_scalar_param_type(element_type):
            return f"list[{_render_type_name(element_type)}]"
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

    A ``list[...]`` parameter is unwrapped to its element type first, so a
    constrained list (``list[Literal[...]]`` / ``list[Enum]``) surfaces its
    element's allowed set — the ``is_list`` + ``choices`` projection a multi-select
    consumer needs.

    Returns:
        tuple[Any, ...] | None: The allowed members for a constrained scalar
        (``Literal`` args or ``Enum`` member values), or None when unconstrained.
    """
    unwrapped = _unwrap_optional(param_type)
    if get_origin(unwrapped) is list:
        type_args = get_args(unwrapped)
        unwrapped = _unwrap_optional(type_args[0]) if type_args else str
    if get_origin(unwrapped) is Literal:
        return get_args(unwrapped)
    if isinstance(unwrapped, type) and issubclass(unwrapped, Enum):
        return tuple(member.value for member in unwrapped)
    return None
