# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Declarative parameter model for registry and scenario construction."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from types import GenericAlias
from typing import Any, get_args, get_origin

_SUPPORTED_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool)


class ComponentRegistryKind(str, Enum):
    """Registry family associated with a parameter reference."""

    COMPONENT = "component"
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

    kind: ComponentRegistryKind
    name: str | None = None
    annotation: Any | None = None


@dataclass(frozen=True)
class Parameter:
    """
    Describes a parameter that a PyRIT component accepts.

    The model-side definition is the canonical contract for declarative
    construction and coercion. ``pyrit.common.parameter`` re-exports it for
    existing callers.
    """

    name: str
    description: str
    default: Any = None
    param_type: type | GenericAlias | None = None
    choices: tuple[Any, ...] | None = None
    registry_kind: ComponentRegistryKind | None = None
    destination: ParameterDestination = ParameterDestination.CONSTRUCTOR

    def __post_init__(self) -> None:
        """Tuple-ify ``choices`` and coerce them to ``param_type`` for scalar types."""
        if self.choices is not None and not isinstance(self.choices, tuple):
            object.__setattr__(self, "choices", tuple(self.choices))
        if self.choices is not None and self.param_type in (bool, int, float, str):
            try:
                coerced = tuple(
                    _coerce_choice_value(name=self.name, param_type=self.param_type, raw_value=c) for c in self.choices
                )
            except ValueError:
                return
            object.__setattr__(self, "choices", coerced)


def _coerce_choice_value(*, name: str, param_type: Any, raw_value: Any) -> Any:
    """Coerce one declared choice to ``param_type``."""
    if param_type is bool:
        return coerce_bool(param_name=name, raw_value=raw_value)
    if param_type is int:
        return coerce_scalar(param_name=name, scalar_type=int, raw_value=raw_value)
    if param_type is float:
        return coerce_scalar(param_name=name, scalar_type=float, raw_value=raw_value)
    return str(raw_value)


def validate_param_type(*, param: Parameter) -> None:
    """Reject parameter declarations with an unsupported ``param_type``."""
    param_type = param.param_type
    if param_type is None or param_type in _SUPPORTED_SCALAR_TYPES:
        return
    if get_origin(param_type) is list:
        type_args = get_args(param_type)
        element_type = type_args[0] if type_args else str
        if element_type is str:
            return

    raise ValueError(
        f"Parameter '{param.name}' has unsupported param_type {param_type!r}. "
        f"Supported types: str, int, float, bool, list[str], or None."
    )


def coerce_value(*, param: Parameter, raw_value: Any) -> Any:
    """Coerce a raw value to ``param.param_type`` and validate against ``param.choices``."""
    param_type = param.param_type
    if param_type is None:
        value = copy.deepcopy(raw_value)
    elif param_type is bool:
        value = coerce_bool(param_name=param.name, raw_value=raw_value)
    elif param_type is int:
        value = coerce_scalar(param_name=param.name, scalar_type=int, raw_value=raw_value)
    elif param_type is float:
        value = coerce_scalar(param_name=param.name, scalar_type=float, raw_value=raw_value)
    elif param_type is str:
        value = str(raw_value)
    elif get_origin(param_type) is list:
        value = coerce_list(param=param, raw_value=raw_value)
    else:
        raise ValueError(
            f"Parameter '{param.name}' has unsupported param_type {param_type!r}. "
            f"Supported types: str, int, float, bool, list[str]."
        )

    if param.choices is not None and value not in param.choices:
        raise ValueError(f"Parameter '{param.name}' value {value!r} is not in declared choices {param.choices!r}.")

    return value


def coerce_scalar(*, param_name: str, scalar_type: type, raw_value: Any) -> Any:
    """Coerce ``raw_value`` to ``int`` or ``float`` while rejecting native ``bool`` inputs."""
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


def coerce_bool(*, param_name: str, raw_value: Any) -> bool:
    """Parse ``raw_value`` as a boolean, accepting the usual textual forms."""
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in ("true", "1", "yes"):
            return True
        if normalized in ("false", "0", "no"):
            return False
    raise ValueError(
        f"Parameter '{param_name}' expects bool but received {raw_value!r}. "
        f"Accepted values: true/false, 1/0, yes/no (case-insensitive), or a native bool."
    )


def coerce_list(*, param: Parameter, raw_value: Any) -> list[Any]:
    """Coerce a ``list[T]`` parameter (currently only ``list[str]``)."""
    if not isinstance(raw_value, list):
        raise ValueError(
            f"Parameter '{param.name}' expects a list but received {type(raw_value).__name__} ({raw_value!r})."
        )

    type_args = get_args(param.param_type)
    element_type = type_args[0] if type_args else str

    if element_type is str:
        return [str(item) for item in raw_value]
    raise ValueError(
        f"Parameter '{param.name}' has unsupported list element type {element_type!r}. Supported list types: list[str]."
    )
