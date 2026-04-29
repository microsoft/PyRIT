# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unified parameter declaration for PyRIT components.

Both initializers and scenarios use ``Parameter`` to declare the custom
parameters they accept. The framework inspects these declarations for CLI
argument building, config-file validation, ``--list-scenarios`` / ``--list-initializers``
display, and runtime type coercion.
"""

from dataclasses import dataclass
from types import GenericAlias
from typing import Any


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
