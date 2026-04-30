# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unified parameter declaration for PyRIT components.

Both initializers and scenarios use ``Parameter`` to declare the custom
parameters they accept. The framework inspects these declarations for CLI
argument building, config-file validation, ``--list-scenarios`` / ``--list-initializers``
display, and runtime type coercion.

The module-level coercion helpers (``coerce_value``, ``coerce_bool``,
``coerce_scalar``, ``coerce_list``) and ``validate_param_type`` are reused
across the scenario layer and both CLI parsers (``pyrit_scan`` argparse and
``pyrit_shell`` shlex), so the bool/scalar/list handling stays consistent
no matter where a value enters the framework.
"""

from dataclasses import dataclass
from types import GenericAlias
from typing import Any, get_args, get_origin

_SUPPORTED_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool)


@dataclass(frozen=True)
class Parameter:
    """
    Describes a parameter that a PyRIT component accepts.

    Used by both ``PyRITInitializer`` subclasses and ``Scenario`` subclasses
    to declare their configurable surface area.

    Args:
        name (str): The parameter name. Becomes the key in the component's
            ``params`` dict and is converted to ``--kebab-case`` for the CLI.
        description (str): Human-readable description shown in ``--help`` and
            in ``--list-scenarios`` / ``--list-initializers``.
        required (bool): Whether the parameter must be provided. Defaults to False.
        default (Any): Default value applied when the parameter is not supplied.
            Defaults to None. Note: initializer authors should pass values matching
            the ``dict[str, list[str]]`` storage convention enforced by
            ``PyRITInitializer.set_params_from_args`` (typically ``list[str]``)
            until typed coercion is added on the initializer path.
        param_type (type | GenericAlias | None): Type used to coerce raw input
            for scenario parameters. Supported types are ``str``, ``int``,
            ``float``, ``bool``, and ``list[str]``. When None, no framework-side
            coercion is performed (the initializer convention). Defaults to None.
        choices (tuple[Any, ...] | None): Optional set of allowed values.
            Validated after coercion. Lists are accepted at construction time
            and normalized to tuples to preserve frozen-dataclass hashability.
            Defaults to None.
    """

    name: str
    description: str
    required: bool = False
    default: Any = None
    param_type: type | GenericAlias | None = None
    choices: tuple[Any, ...] | None = None

    def __post_init__(self) -> None:
        """Normalize ``choices`` from list to tuple to keep the dataclass hashable."""
        if self.choices is not None and not isinstance(self.choices, tuple):
            object.__setattr__(self, "choices", tuple(self.choices))


def validate_param_type(*, param: Parameter) -> None:
    """
    Reject parameter declarations with unsupported ``param_type``.

    Supported types: ``None`` (raw passthrough), ``str``, ``int``, ``float``,
    ``bool``, and ``list[str]``.

    Args:
        param (Parameter): The parameter declaration.

    Raises:
        ValueError: If ``param_type`` is not in the supported set. The error
            message references only the parameter; callers add component
            context (scenario name, initializer name, etc.).
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
    Coerce a raw input value into the declared ``param_type`` and validate ``choices``.

    Used both by ``Scenario.set_params_from_args`` (after CLI/config merge)
    and by the CLI parser layer (as an argparse ``type=`` callable or shell
    ``_ArgSpec.parser``).

    Args:
        param (Parameter): The parameter declaration.
        raw_value (Any): The value as supplied (string from CLI, native Python
            value from YAML, declared default during declaration validation).

    Returns:
        Any: The coerced value, ready to store on a component's ``params``.

    Raises:
        ValueError: If coercion fails or the coerced value is not in ``choices``.
    """
    param_type = param.param_type
    if param_type is None:
        value: Any = raw_value
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
    Coerce a raw value into ``int`` or ``float``, rejecting native ``bool`` inputs.

    ``int(True) == 1`` and ``float(False) == 0.0`` are silent surprises when
    a YAML typo or stray flag lands a ``bool`` where a number was expected.
    We reject those explicitly.

    Args:
        param_name (str): Parameter name (used in error messages).
        scalar_type (type): Either ``int`` or ``float``.
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
    Parse a raw value as a boolean.

    Accepts native ``bool``, plus case-insensitive strings ``true``/``1``/``yes``
    for True and ``false``/``0``/``no`` for False. Avoids the well-known
    ``bool("false") is True`` argparse footgun.

    Args:
        param_name (str): Parameter name (used in error messages).
        raw_value (Any): Value to coerce.

    Returns:
        bool: Coerced boolean.

    Raises:
        ValueError: If the value is not a recognized boolean form.
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
    Coerce a list-typed parameter, validating arity and per-element type.

    Args:
        param (Parameter): The parameter declaration. ``param.param_type``
            must be a parameterized list generic such as ``list[str]``.
        raw_value (Any): The raw value (must be a list).

    Returns:
        list[Any]: The coerced list.

    Raises:
        ValueError: If ``raw_value`` is not a list, or any element fails
            coercion to the declared element type.
    """
    if not isinstance(raw_value, list):
        raise ValueError(
            f"Parameter '{param.name}' expects a list but received {type(raw_value).__name__} ({raw_value!r})."
        )

    type_args = get_args(param.param_type)
    element_type = type_args[0] if type_args else str

    if element_type is str:
        return [str(item) for item in raw_value]
    # Defensive: v1 only ships list[str], but the coercion logic is written
    # so future expansion to list[int] / list[float] / list[bool] would only
    # need this branch widened.
    raise ValueError(
        f"Parameter '{param.name}' has unsupported list element type {element_type!r}. Supported list types: list[str]."
    )
