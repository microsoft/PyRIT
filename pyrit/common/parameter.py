# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unified parameter declaration and coercion helpers shared by initializers, scenarios, and CLI parsers."""

import copy
from dataclasses import dataclass
from types import GenericAlias
from typing import Any, get_args, get_origin

_SUPPORTED_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool)


@dataclass(frozen=True)
class Parameter:
    """
    Describes a parameter that a PyRIT component (initializer or scenario) accepts.

    Args:
        name (str): Parameter name; becomes the key in ``params`` and the
            ``--kebab-case`` CLI flag.
        description (str): Human-readable description shown in ``--help`` and
            ``--list-*`` output.
        default (Any): Default value when not supplied. Defaults to None. Must not
            contain secrets; defaults are rendered verbatim by ``--list-scenarios``.
        param_type (type | GenericAlias | None): Type for scenario-side coercion.
            Supported: ``str``, ``int``, ``float``, ``bool``, ``list[str]``. None
            means no coercion (the initializer convention). Defaults to None.
        choices (tuple[Any, ...] | None): Optional allowed values. Coerced to
            ``param_type`` and tuple-normalized so argparse, YAML, and runtime
            membership checks see the same Python type. Defaults to None.
    """

    name: str
    description: str
    default: Any = None
    param_type: type | GenericAlias | None = None
    choices: tuple[Any, ...] | None = None

    def __post_init__(self) -> None:
        """Tuple-ify ``choices`` and coerce them to ``param_type`` for scalar types."""
        if self.choices is not None and not isinstance(self.choices, tuple):
            object.__setattr__(self, "choices", tuple(self.choices))
        # Lists with choices are rejected at declaration time, so list[T] is skipped here.
        if self.choices is not None and self.param_type in (bool, int, float, str):
            try:
                coerced = tuple(
                    _coerce_choice_value(name=self.name, param_type=self.param_type, raw_value=c) for c in self.choices
                )
            except ValueError:
                # Leave choices alone; _validate_declarations surfaces the error.
                return
            object.__setattr__(self, "choices", coerced)


def _coerce_choice_value(*, name: str, param_type: Any, raw_value: Any) -> Any:
    """
    Coerce one declared choice to ``param_type``.

    Helper for ``Parameter.__post_init__``. ``param_type`` is typed ``Any``
    because the dataclass field is ``type | GenericAlias | None``; the caller
    gates on scalar types before invoking this helper.

    Args:
        name (str): Parameter name (used only in error messages).
        param_type (Any): One of ``bool``, ``int``, ``float``, ``str``.
        raw_value (Any): The choice value as declared by the author.

    Returns:
        Any: The coerced choice value.
    """
    if param_type is bool:
        return coerce_bool(param_name=name, raw_value=raw_value)
    if param_type is int:
        return coerce_scalar(param_name=name, scalar_type=int, raw_value=raw_value)
    if param_type is float:
        return coerce_scalar(param_name=name, scalar_type=float, raw_value=raw_value)
    return str(raw_value)


def validate_param_type(*, param: Parameter) -> None:
    """
    Reject parameter declarations with an unsupported ``param_type``.

    Args:
        param (Parameter): The parameter declaration.

    Raises:
        ValueError: If ``param_type`` is not ``None``, ``str``, ``int``,
            ``float``, ``bool``, or ``list[str]``.
    """
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
    """
    Coerce a raw value to ``param.param_type`` and validate against ``param.choices``.

    Args:
        param (Parameter): The parameter declaration.
        raw_value (Any): Value as supplied by CLI, YAML, or declared default.

    Returns:
        Any: The coerced value.

    Raises:
        ValueError: If coercion fails or the result is not in ``choices``.
    """
    param_type = param.param_type
    if param_type is None:
        # Deep-copy so mutable raw values don't share identity with self.params.
        value: Any = copy.deepcopy(raw_value)
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
    """
    Coerce ``raw_value`` to ``int`` or ``float``, rejecting native ``bool`` inputs.

    Avoids ``int(True) == 1`` / ``float(False) == 0.0`` silent surprises.

    Args:
        param_name (str): Parameter name for error messages.
        scalar_type (type): ``int`` or ``float``.
        raw_value (Any): Value to coerce.

    Returns:
        Any: The coerced numeric value.

    Raises:
        ValueError: If ``raw_value`` is a ``bool`` or cannot be coerced.
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


def coerce_bool(*, param_name: str, raw_value: Any) -> bool:
    """
    Parse ``raw_value`` as a boolean, avoiding the ``bool("false") is True`` argparse footgun.

    Accepts native ``bool`` and case-insensitive ``true``/``1``/``yes`` /
    ``false``/``0``/``no`` strings.

    Args:
        param_name (str): Parameter name for error messages.
        raw_value (Any): Value to coerce.

    Returns:
        bool: The coerced boolean.

    Raises:
        ValueError: If ``raw_value`` is not a recognized boolean form.
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
        f"Parameter '{param_name}' expects bool but received {raw_value!r}. "
        f"Accepted values: true/false, 1/0, yes/no (case-insensitive), or a native bool."
    )


def coerce_list(*, param: Parameter, raw_value: Any) -> list[Any]:
    """
    Coerce a ``list[T]`` parameter (v1: only ``list[str]``).

    Args:
        param (Parameter): Declaration with ``param_type`` like ``list[str]``.
        raw_value (Any): Must be a list.

    Returns:
        list[Any]: The coerced list.

    Raises:
        ValueError: If ``raw_value`` is not a list or the element type isn't ``str``.
    """
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
